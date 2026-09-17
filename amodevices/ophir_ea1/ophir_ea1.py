# -*- coding: utf-8 -*-
"""
Driver for the Ophir EA-1 Ethernet adapter and the Ophir smart sensor head
plugged into it (developed with a PE50BF-DFH-C pyroelectric energy head).

Communication (EA-1 user manual, chapter 6 "User Commands"; every claim
below was checked against a live adapter, firmware EA1.17):

- TCP to port 23. The adapter speaks Telnet: on connect it sends the
  negotiation bytes ``\\xff\\xfd\\x24\\xff\\xfb\\x01`` (IAC sequences of
  three bytes), which the driver strips wherever they occur.
- Commands are ASCII, ``$`` plus a two-letter code and optional
  parameters, terminated by CR LF. A reply starts with ``*`` (success)
  or ``?`` (error), ends with CR LF, and is followed by a ``>`` prompt
  that is NOT a terminator (it precedes the next reply on the same line
  and can occur inside a body). Some replies carry a stray LF before the
  CR LF (``*3\\n\\r\\n>``). The driver turns echo off (``$EE 0``).
- Per-pulse readout is the streaming mode ``$CS 3``: the adapter answers
  ``*STARTED`` and then PUSHES one line per measured pulse,
  ``*<pulse index> <timestamp us> <energy J>``. The index counts every
  pulse the adapter measured (a gap = pulses that never reached the
  host), the timestamp is the adapter's own clock at 1 us resolution;
  both are 32-bit counters that wrap (the timestamp every 71.6 min) and
  are UNWRAPPED here (the timestamp with the host clock as the guide, so
  a pause longer than half the wrap period cannot mislead it); only a
  counter reset on the adapter still shows as a jump. ``$CS 1`` stops the stream
  (``*STOPPED``, possibly interleaved with pulse lines), and so does ANY
  other command: settings are read or changed between streams only.
- The polled readout (``$EF`` new-value flag, cleared by ``$SE``) is the
  same read-and-clear flag a Thorlabs PM100 has and is rated at about
  10 Hz; it is offered for diagnostics only.

The manual does not say how an over-range pulse looks in mode 3 (``OVER``
exists in the polled and mode-2 replies); a third token ``OVER`` is
taken as one, and any line the driver does not understand is returned
as status 'unparseable' with the raw text rather than dropped, so an
unforeseen format shows up in a log instead of as a missing pulse.

The adapter accepts one client: StarLab (or any other reader) must be
closed while this driver holds the connection.

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging
import re
import socket
import threading
import time
from collections import namedtuple
from types import SimpleNamespace

from .. import dev_generic
from ..dev_exceptions import DeviceError
from ..status import STATUS_OK

logger = logging.getLogger(__name__)


class CommandRejected(DeviceError):
    """The adapter answered a command with a ``?`` line: the command
    channel is intact, the command itself was refused (unknown to the
    firmware, or its argument out of range). Every other `DeviceError`
    means the channel can no longer be trusted."""

#: Telnet command port of the adapter
DEFAULT_PORT = 23
#: Reply timeout per command (s)
DEFAULT_TIMEOUT_S = 2.0
#: TCP connect timeout (s)
DEFAULT_CONNECT_TIMEOUT_S = 5.0
#: How long `connect` collects the Telnet banner (s)
BANNER_WAIT_S = 0.5
#: Silence after `*STOPPED` before the command channel is considered clean (s)
STOP_DRAIN_S = 0.05
#: The streaming mode with index and timestamp per pulse (`$CS 3`)
STREAM_MODE_PER_PULSE = 3
#: `$MM` reply while the head measures energy (verified on the PE50BF-DFH-C)
MEASUREMENT_MODE_ENERGY = 3
#: Status words of a streamed pulse (fleet vocabulary, `amodevices.status`)
STATUS_OVER = 'overexposed'
STATUS_UNPARSEABLE = 'unparseable'
STATUS_WORDS = (STATUS_OK, STATUS_OVER, STATUS_UNPARSEABLE)
#: Telnet "interpret as command" byte; an IAC is followed by two bytes
IAC = 0xFF
_IAC_LEN = 3
_WRAP = 2 ** 32
_HALF_WRAP = 2 ** 31
_SI_PREFIXES = {'': 1.0, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6, '\u00b5': 1e-6,
                'm': 1e-3, 'k': 1e3, 'M': 1e6}
_SI_VALUE_RE = re.compile(
    r'^([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*([pnu\u00b5mkM]?)([A-Za-z]+)$')

#: One streamed pulse. `index` and `timestamp_us` are the adapter's counters
#: UNWRAPPED (monotone within a stream session as long as the adapter's
#: counters run on; None for an unparseable line), `energy_j` is None
#: for an over-range or unparseable line,
#: `status` is one of `STATUS_WORDS`, `t_recv` is `time.time()` right after
#: the `recv` that completed the line, `raw` the line as received.
Pulse = namedtuple('Pulse', 'index timestamp_us energy_j status t_recv raw')


#: TCP keepalive: probe after this much idle time (s), then at this
#: interval (s). A streaming session is idle whenever the laser is off, and
#: an adapter that was power-cycled or unplugged then never errors an idle
#: `recv` on its own: the stream would look like "no pulses" forever.
#: With the probes a dead peer fails the read within about a minute
KEEPALIVE_IDLE_S = 10
KEEPALIVE_INTERVAL_S = 5


def _enable_keepalive(sock):
    """Turn on TCP keepalive probes on `sock`, with the timing above where
    the platform lets it be set (Windows: `SIO_KEEPALIVE_VALS`; Linux:
    the `TCP_KEEP*` options). Best effort: a platform without the knobs
    keeps its defaults."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
            sock.ioctl(socket.SIO_KEEPALIVE_VALS,
                       (1, KEEPALIVE_IDLE_S * 1000, KEEPALIVE_INTERVAL_S * 1000))
        else:
            for name, value in (('TCP_KEEPIDLE', KEEPALIVE_IDLE_S),
                                ('TCP_KEEPINTVL', KEEPALIVE_INTERVAL_S),
                                ('TCP_KEEPCNT', 5)):
                if hasattr(socket, name):
                    sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name),
                                    value)
    except OSError as exc:
        logger.debug('TCP keepalive not set: %s', exc)


# -- pure helpers (no I/O) ---------------------------------------------------

def strip_iac(data):
    """Remove every Telnet IAC sequence (`IAC` plus two bytes) from `data`.

    Returns ``(clean, tail)``: `tail` is an incomplete trailing sequence
    (0 to 2 bytes) to prepend to the next chunk. The payload is ASCII, so
    0xFF never occurs in data.
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == IAC:
            if n - i < _IAC_LEN:
                return bytes(out), bytes(data[i:])
            i += _IAC_LEN
        else:
            out.append(data[i])
            i += 1
    return bytes(out), b''


def parse_pulse_line(body):
    """Parse the body (after ``*``) of a streamed pulse line.

    Returns ``(raw_index, raw_timestamp_us, energy_j, status)`` with
    `energy_j` None and `status` `STATUS_OVER` for a third token ``OVER``;
    raises `ValueError` for anything that is not three tokens of that
    shape (which is how ``*STOPPED`` and unforeseen lines are told apart).
    """
    tokens = body.split()
    if len(tokens) != 3:
        raise ValueError(f'expected three tokens, got {len(tokens)}')
    index = int(tokens[0])
    timestamp_us = int(tokens[1])
    if index < 0 or timestamp_us < 0:
        raise ValueError('negative counter')
    if tokens[2].upper() == 'OVER':
        return index, timestamp_us, None, STATUS_OVER
    return index, timestamp_us, float(tokens[2]), STATUS_OK


def unwrap32(raw, last_raw, wraps):
    """Unwrap a 32-bit counter reading that advances by small steps (the
    pulse index).

    A step backwards by more than half the range is a wrap and increments
    `wraps`; a smaller step backwards is a counter reset on the device and
    is passed through unchanged (the caller decides what to make of it).
    Returns ``(unwrapped, wraps)``.
    """
    if last_raw is not None and raw < last_raw and last_raw - raw > _HALF_WRAP:
        wraps += 1
    return raw + wraps * _WRAP, wraps


def unwrap32_timed(raw, last_unwrapped, last_t_recv, t_recv):
    """Unwrap a 32-bit timestamp reading (us) with the host clock as the
    guide.

    The half-range rule of `unwrap32` cannot serve a free-running clock:
    the counter wraps every 71.6 min, so a laser pause longer than 35.8
    min that happens to straddle a wrap reads as a small step backwards
    and the "monotone" timestamp would jump back by up to 36 min. The
    host clock breaks the tie: of the candidates ``raw + k * 2**32`` the
    one nearest ``last_unwrapped + (t_recv - last_t_recv)`` is right as
    long as the two clocks disagree by less than 35.8 min over the gap
    (days at 100 ppm drift). A counter reset on the device still shows
    as a jump, forward or backward, for the caller to treat as a new
    epoch. Returns `raw` for the first reading.
    """
    if last_unwrapped is None:
        return raw
    expected = last_unwrapped + (t_recv - last_t_recv) * 1e6
    return raw + round((expected - raw) / _WRAP) * _WRAP


def parse_si_value(token, unit):
    """``'200mJ'`` -> 0.2 for `unit` ``'J'``; ``'10ms'`` -> 0.01 for ``'s'``.

    Raises `ValueError` when the token does not end in `unit` or carries
    an unknown prefix.
    """
    match = _SI_VALUE_RE.match(token.strip())
    if match is None:
        raise ValueError(f'not a value with unit: {token!r}')
    number, prefix, suffix = match.groups()
    if suffix != unit:
        # A prefix letter can be swallowed into the suffix ('mJ' vs 'J')
        if suffix.endswith(unit) and len(suffix) == len(unit) + 1 and not prefix:
            prefix, suffix = suffix[0], suffix[1:]
        else:
            raise ValueError(f'expected unit {unit!r} in {token!r}')
    if prefix not in _SI_PREFIXES:
        raise ValueError(f'unknown SI prefix in {token!r}')
    return float(number) * _SI_PREFIXES[prefix]


def _parse_indexed_list(body, unit):
    """``'3 10.0J 2.00J 200mJ'`` -> ``(3, [10.0, 2.0, 0.2])``; an ``AUTO``
    entry (autoranging power heads) is left out of the list."""
    tokens = body.split()
    if not tokens:
        raise ValueError('empty reply')
    current = int(tokens[0])
    values = [parse_si_value(token, unit) for token in tokens[1:]
              if token.upper() != 'AUTO']
    return current, values


def parse_ranges(body, unit='J'):
    """The `$AR` reply: ``(current index, ranges in J)``, index 0 being the
    highest range (`unit` ``'W'`` for a head in power mode)."""
    return _parse_indexed_list(body, unit)


def parse_pulse_lengths(body):
    """The `$PL` reply: ``(current index, pulse lengths in s)``. Unlike
    the range index, this index is 1-based (``1`` selects the first
    length): ``'1 1.0ms 2.0ms 5.0ms 10ms 20ms'`` -> ``(1, [0.001, ...])``
    with 1.0 ms selected, as the live head showed against StarLab."""
    return _parse_indexed_list(body, 's')


def parse_wavelength_info(body):
    """The `$AW` reply.

    Continuous-curve heads: ``'CONTINUOUS 190 3000 3 532 566 283 1064 2100
    2940'`` -> ``{'mode': 'CONTINUOUS', 'min_nm': 190.0, 'max_nm': 3000.0,
    'active': 3, 'favorites_nm': [532.0, ...]}`` (`active` is a 1-based
    favorite index; an empty favorite slot reads ``NONE`` and becomes None).
    Discrete heads: ``'DISCRETE 2 CO2 YAG'`` -> ``{'mode': 'DISCRETE',
    'active': 2, 'names': ['CO2', 'YAG']}``.
    """
    tokens = body.split()
    if not tokens:
        raise ValueError('empty reply')
    mode = tokens[0].upper()
    if mode == 'CONTINUOUS':
        if len(tokens) < 4:
            raise ValueError(f'short CONTINUOUS reply: {body!r}')
        favorites = [None if token.upper() == 'NONE' else float(token)
                     for token in tokens[4:]]
        return {'mode': mode, 'min_nm': float(tokens[1]),
                'max_nm': float(tokens[2]), 'active': int(tokens[3]),
                'favorites_nm': favorites}
    if mode == 'DISCRETE':
        if len(tokens) < 2:
            raise ValueError(f'short DISCRETE reply: {body!r}')
        return {'mode': mode, 'active': int(tokens[1]), 'names': tokens[2:]}
    raise ValueError(f'unknown wavelength mode in {body!r}')


def parse_threshold(body):
    """The `$UT` reply ``'105 105 2500'`` -> ``(current, minimum, maximum)``
    as FRACTIONS of the full-scale energy (the device counts in
    1/10000)."""
    tokens = body.split()
    if len(tokens) != 3:
        raise ValueError(f'expected three integers in {body!r}')
    return tuple(int(token) / 10000.0 for token in tokens)


# -- the driver ------------------------------------------------------------

class OphirEA1(dev_generic.Device):
    """Ophir EA-1 Ethernet adapter with a smart sensor head.

    Device configuration dict keys: 'Device' (name for messages),
    'Address' (IP or host name), 'Port' (default `DEFAULT_PORT`),
    'Timeout' (reply timeout in s, default `DEFAULT_TIMEOUT_S`),
    'ConnectTimeout' (s, default `DEFAULT_CONNECT_TIMEOUT_S`).

    The constructor does no I/O; call `connect()`. Every send and receive
    holds one re-entrant lock, so a settings query and the streaming
    reader can live on different threads. While a stream runs, commands
    are refused (`DeviceError`): the caller stops the stream, applies its
    commands, and starts it again; the adapter would have stopped it
    anyway on the first command, silently.

    Values are SI throughout: energies in J, pulse lengths in s,
    wavelengths in nm, thresholds as fractions of the full-scale energy.
    """

    def __init__(self, device):
        super().__init__(device)
        self._timeout_s = float(self.device.get('Timeout', DEFAULT_TIMEOUT_S))
        self._connect_timeout_s = float(
            self.device.get('ConnectTimeout', DEFAULT_CONNECT_TIMEOUT_S))
        self._lock = threading.RLock()
        self._sock = None
        self._buffer = b''
        self._iac_tail = b''
        self._t_last_recv = 0.0
        self._streaming = False
        self._unwrap = self._fresh_unwrap_state()
        self.firmware_version = ''
        self.adapter_type = ''
        self.adapter_serial = ''
        self.adapter_name = ''
        self.head_type = ''
        self.head_serial = ''
        self.head_name = ''
        self.head_extra = ''

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def _fresh_unwrap_state():
        return SimpleNamespace(last_index=None, index_wraps=0,
                               last_ts=None, last_t_recv=None)

    # -- connection --------------------------------------------------------

    def _open_socket(self):
        """Open the TCP connection; the one seam a fake transport replaces."""
        host = self.device['Address']
        port = int(self.device.get('Port', DEFAULT_PORT))
        try:
            sock = socket.create_connection(
                (host, port), timeout=self._connect_timeout_s)
        except OSError as exc:
            raise DeviceError(
                f'{self.device["Device"]}: Failed to connect to'
                f' {host}:{port}: {exc}') from exc
        _enable_keepalive(sock)
        return sock

    def connect(self):
        """Open the connection, stop a stream a previous client may have
        left running, turn echo off, and read the adapter and head
        identity. Raises `DeviceError`."""
        with self._lock:
            self.close()
            self._sock = self._open_socket()
            self._buffer = b''
            self._iac_tail = b''
            self._streaming = False
            try:
                # The Telnet banner; a stream a crashed client left
                # running keeps pushing lines through the drain's cap,
                # and the `$CS 1` ends it
                self._drain(BANNER_WAIT_S)
                self._send('$CS 1')
                deadline = time.monotonic() + self._timeout_s
                while True:
                    line = self._read_line(deadline)
                    if line is None or line.startswith('?') or (
                            line.upper().startswith('*STOPPED')):
                        break
                    logger.debug('%s: Discarding %r left over from an'
                                 ' earlier session', self.device['Device'],
                                 line)
                self._drain(STOP_DRAIN_S)
                try:
                    self._query('$EE 0')
                except CommandRejected as exc:
                    # A firmware without the command; a MISSING reply is
                    # not tolerated: it would arrive later and shift
                    # every following reply by one
                    logger.debug('%s: Could not turn echo off: %s',
                                 self.device['Device'], exc)
                self.firmware_version = self._query('$VE')
                self._parse_identity(self._query('$II'), self._query('$HI'))
            except DeviceError:
                self._close_socket()
                raise
            self.device_present = True
            self.device_connected = True
        logger.info(
            '%s: Connected to %s:%s, adapter %s (serial %s) firmware %s,'
            ' head %s (serial %s)', self.device['Device'],
            self.device['Address'], self.device.get('Port', DEFAULT_PORT),
            self.adapter_name, self.adapter_serial, self.firmware_version,
            self.head_name, self.head_serial)

    def _parse_identity(self, adapter_body, head_body):
        adapter = adapter_body.split()
        self.adapter_type = adapter[0] if len(adapter) > 0 else ''
        self.adapter_serial = adapter[1] if len(adapter) > 1 else ''
        self.adapter_name = ' '.join(adapter[2:])
        head = head_body.split()
        self.head_type = head[0] if len(head) > 0 else ''
        self.head_serial = head[1] if len(head) > 1 else ''
        self.head_name = head[2] if len(head) > 2 else ''
        self.head_extra = ' '.join(head[3:])

    def close(self):
        """Stop a running stream (best effort) and close the connection.
        Idempotent."""
        with self._lock:
            if self._sock is None:
                return
            try:
                if self._streaming:
                    try:
                        self.stop_stream()
                    except DeviceError as exc:
                        logger.debug('%s: Stream not stopped cleanly on'
                                     ' close: %s', self.device['Device'], exc)
            finally:
                self._close_socket()

    def _close_socket(self):
        sock, self._sock = self._sock, None
        self._streaming = False
        self.device_connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _require_connection(self):
        if self._sock is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')

    # -- bytes and lines ---------------------------------------------------

    def _send(self, command):
        self._require_connection()
        logger.debug('%s TX: %s', self.device['Device'], command)
        try:
            self._sock.sendall((command + '\r\n').encode('ascii'))
        except OSError as exc:
            raise DeviceError(
                f'{self.device["Device"]}: Failed to send {command!r}:'
                f' {exc}') from exc

    def _recv_into_buffer(self, deadline):
        """One `recv` bounded by `deadline` (monotonic); False on timeout.
        Raises `DeviceError` when the adapter closed the connection."""
        self._require_connection()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self._sock.settimeout(remaining)
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return False
        except OSError as exc:
            raise DeviceError(
                f'{self.device["Device"]}: Receive failed: {exc}') from exc
        if not chunk:
            raise DeviceError(
                f'{self.device["Device"]}: Connection closed by the adapter')
        self._t_last_recv = time.time()
        clean, self._iac_tail = strip_iac(self._iac_tail + chunk)
        self._buffer += clean
        return True

    def _pop_line(self):
        """The next complete line of the buffer, or None.

        Terminators are LF (a stray LF before the CR LF then yields one
        line plus an empty one); a leading run of ``>`` prompts and the
        surrounding whitespace are stripped; empty lines and command
        echoes (``$...``) are skipped.
        """
        while True:
            index = self._buffer.find(b'\n')
            if index < 0:
                return None
            raw, self._buffer = self._buffer[:index], self._buffer[index + 1:]
            # latin-1 never fails and keeps a 0xB5 micro sign (a
            # 'uJ'-style token spelled with the micro sign) as the character
            # the SI parser knows
            line = raw.decode('latin-1').strip().lstrip('>').strip()
            if line and not line.startswith('$'):
                return line

    def _read_line(self, deadline):
        """The next line, receiving as needed until `deadline`; None on
        timeout."""
        while True:
            line = self._pop_line()
            if line is not None:
                return line
            if not self._recv_into_buffer(deadline):
                return None

    def _drain(self, seconds, total_s=None):
        """Discard the buffer and everything that arrives until the line
        has been silent for `seconds`, or for `total_s` in all (default
        four times `seconds`): a stream a crashed client left running
        never falls silent, and the caller's `$CS 1` is what ends it."""
        end = time.monotonic() + (4 * seconds if total_s is None else total_s)
        while self._recv_into_buffer(min(time.monotonic() + seconds, end)):
            pass
        if self._buffer:
            logger.debug('%s: Discarding %r', self.device['Device'],
                         self._buffer)
        self._buffer = b''

    def _query(self, command, timeout_s=None):
        """Send `command` and return the body of its ``*`` reply (the text
        after the star, stripped). A ``?`` reply raises `DeviceError` with
        the adapter's text, and so does a missing reply or a running
        stream. No heuristic on the reply line: `$UT` answers with three
        integers, indistinguishable from a pulse line."""
        with self._lock:
            if self._streaming:
                raise DeviceError(
                    f'{self.device["Device"]}: {command!r} refused while the'
                    ' pulse stream runs; stop it first')
            self._send(command)
            deadline = time.monotonic() + (
                self._timeout_s if timeout_s is None else timeout_s)
            while True:
                line = self._read_line(deadline)
                if line is None:
                    raise DeviceError(
                        f'{self.device["Device"]}: No reply to {command!r}')
                if line.startswith('*'):
                    body = line[1:].strip()
                    logger.debug('%s RX: %s', self.device['Device'], body)
                    return body
                if line.startswith('?'):
                    raise CommandRejected(
                        f'{self.device["Device"]}: Command {command!r}'
                        f' rejected: {line[1:].strip()}')
                logger.debug('%s: Unexpected line %r before the reply to %s',
                             self.device['Device'], line, command)

    def _query_parsed(self, command, parse):
        """`_query` plus `parse` of the body, a parse failure raised as
        `DeviceError`: an empty or foreign reply (a desynchronized
        channel, a leftover line) must fail the caller the way a lost
        connection does, not escape as `ValueError`."""
        body = self._query(command)
        try:
            return parse(body)
        except (ValueError, IndexError, TypeError) as exc:
            raise DeviceError(
                f'{self.device["Device"]}: Unexpected reply to {command!r}:'
                f' {body!r} ({exc})') from exc

    # -- identity and settings ---------------------------------------------

    def get_measurement_mode(self):
        """The `$MM` mode number; `MEASUREMENT_MODE_ENERGY` while the head
        measures energy."""
        return self._query_parsed('$MM', int)

    def force_energy_mode(self):
        """Put the head into energy mode (`$FE`)."""
        self._query('$FE')

    def force_power_mode(self):
        """Put the head into power mode (`$FP`); ends energy measurement."""
        self._query('$FP')

    def get_ranges(self):
        """``(current index, ranges in J)``, index 0 the highest range."""
        current, ranges = self._query_parsed('$AR', parse_ranges)
        if not ranges:
            raise DeviceError(
                f'{self.device["Device"]}: $AR listed no ranges')
        return current, ranges

    def get_range_index(self):
        """The current range index (`$RN`)."""
        return self._query_parsed('$RN', lambda body: int(body.split()[0]))

    def set_range_index(self, index):
        """Select a range by index (`$WN`). The head settles for a few
        seconds afterwards."""
        _, ranges = self.get_ranges()
        if not 0 <= int(index) < len(ranges):
            raise DeviceError(
                f'{self.device["Device"]}: Range index {index} outside'
                f' 0..{len(ranges) - 1}')
        self._query(f'$WN {int(index)}')

    def range_index_for_energy(self, energy_j):
        """The index of the smallest range that still holds `energy_j`."""
        _, ranges = self.get_ranges()
        fitting = [i for i, r in enumerate(ranges) if r >= energy_j]
        if not fitting:
            raise DeviceError(
                f'{self.device["Device"]}: {energy_j} J exceeds the highest'
                f' range ({max(ranges)} J)')
        return max(fitting)

    def get_wavelength_info(self):
        """The `$AW` reply as a dict, see `parse_wavelength_info`."""
        return self._query_parsed('$AW', parse_wavelength_info)

    def get_wavelength_nm(self):
        """The active wavelength (nm) of a continuous-curve head."""
        info = self.get_wavelength_info()
        if info['mode'] != 'CONTINUOUS':
            raise DeviceError(
                f'{self.device["Device"]}: Head has discrete wavelengths'
                f' ({info})')
        favorites = info['favorites_nm']
        if not 1 <= info['active'] <= len(favorites):
            raise DeviceError(
                f'{self.device["Device"]}: Active wavelength slot'
                f' {info["active"]} outside 1..{len(favorites)}')
        value = favorites[info['active'] - 1]
        if value is None:
            raise DeviceError(
                f'{self.device["Device"]}: Active wavelength slot is empty')
        return value

    def set_wavelength_nm(self, wavelength_nm):
        """Set the active favorite slot to `wavelength_nm` (`$WL`),
        checked against the head's calibrated span."""
        info = self.get_wavelength_info()
        if info['mode'] != 'CONTINUOUS':
            raise DeviceError(
                f'{self.device["Device"]}: Head has discrete wavelengths')
        if not info['min_nm'] <= wavelength_nm <= info['max_nm']:
            raise DeviceError(
                f'{self.device["Device"]}: {wavelength_nm} nm outside the'
                f' head\'s {info["min_nm"]:g}-{info["max_nm"]:g} nm')
        self._query(f'$WL {wavelength_nm:g}')

    def select_wavelength_favorite(self, slot):
        """Make favorite `slot` (1-6) the active wavelength (`$WI`)."""
        if not 1 <= int(slot) <= 6:
            raise DeviceError(
                f'{self.device["Device"]}: Favorite slot {slot} outside 1..6')
        self._query(f'$WI {int(slot)}')

    def get_pulse_lengths(self):
        """``(current index, pulse lengths in s)`` (`$PL`); the index is
        1-based, so the selected length is ``lengths[index - 1]``."""
        return self._query_parsed('$PL', parse_pulse_lengths)

    def set_pulse_length_index(self, index):
        """Select a pulse length by its 1-based index (`$PL <i>`)."""
        _, lengths = self.get_pulse_lengths()
        if not 1 <= int(index) <= len(lengths):
            raise DeviceError(
                f'{self.device["Device"]}: Pulse length index {index}'
                f' outside 1..{len(lengths)}')
        self._query(f'$PL {int(index)}')

    def get_threshold(self):
        """``(current, minimum, maximum)`` trigger threshold as fractions
        of the full-scale energy (`$UT`)."""
        return self._query_parsed('$UT', parse_threshold)

    def set_threshold(self, fraction):
        """Set the trigger threshold (fraction of full scale, `$UT`)."""
        _, low, high = self.get_threshold()
        if not low <= fraction <= high:
            raise DeviceError(
                f'{self.device["Device"]}: Threshold {fraction:.4f} outside'
                f' {low:.4f}..{high:.4f} of full scale')
        self._query(f'$UT {round(fraction * 10000)}')

    def get_diffuser(self):
        """The `$DQ` reply text (e.g. ``'1 N/A'`` for a head without one)."""
        return self._query('$DQ')

    def save_settings(self):
        """Store the present settings as the head's power-up defaults
        (`$HC S`)."""
        self._query('$HC S')

    def read_energy_polled(self):
        """The polled readout, for diagnostics: ``(new_value, energy_j,
        status)`` from `$EF`/`$SE`. `$SE` clears the new-value flag and
        repeats the last value until the next pulse; rated ~10 Hz."""
        new_value = self._query('$EF').strip() == '1'
        energy_j = self._query_parsed(
            '$SE', lambda body: None if body.upper().startswith('OVER')
            else float(body))
        if energy_j is None:
            return new_value, None, STATUS_OVER
        return new_value, energy_j, STATUS_OK

    # -- streaming -----------------------------------------------------------

    @property
    def streaming(self):
        """Whether the per-pulse stream is running."""
        return self._streaming

    def start_stream(self):
        """Start the per-pulse stream (`$CS 3`); the adapter then pushes
        one line per pulse until `stop_stream`. Resets the counter
        unwrapping: a session's indices and timestamps start afresh."""
        with self._lock:
            if self._streaming:
                return
            body = self._query(f'$CS {STREAM_MODE_PER_PULSE}')
            if body.upper() != 'STARTED':
                raise DeviceError(
                    f'{self.device["Device"]}: Unexpected reply to $CS'
                    f' {STREAM_MODE_PER_PULSE}: {body!r}')
            self._unwrap = self._fresh_unwrap_state()
            self._streaming = True

    def read_pulses(self, timeout_s):
        """Every pulse line already buffered or received within
        `timeout_s`, as `Pulse` records; ``[]`` on a quiet stream and at
        once when no stream runs. Never drops a line short of empty ones
        and command echoes: what is not a pulse comes back as status
        'unparseable'. Raises `DeviceError` when the connection fails."""
        with self._lock:
            if not self._streaming:
                return []
            deadline = time.monotonic() + timeout_s
            pulses = []
            while True:
                line = self._pop_line()
                while line is not None:
                    pulses.append(self._parse_stream_line(line))
                    line = self._pop_line()
                if pulses or not self._recv_into_buffer(deadline):
                    return pulses

    def _parse_stream_line(self, line):
        t_recv = self._t_last_recv
        if line.startswith('*'):
            try:
                raw_index, raw_ts, energy_j, status = parse_pulse_line(line[1:])
            except ValueError:
                return Pulse(None, None, None, STATUS_UNPARSEABLE, t_recv, line)
            state = self._unwrap
            index, state.index_wraps = unwrap32(
                raw_index, state.last_index, state.index_wraps)
            state.last_index = raw_index
            timestamp_us = unwrap32_timed(
                raw_ts, state.last_ts, state.last_t_recv, t_recv)
            state.last_ts = timestamp_us
            state.last_t_recv = t_recv
            return Pulse(index, timestamp_us, energy_j, status, t_recv, line)
        return Pulse(None, None, None, STATUS_UNPARSEABLE, t_recv, line)

    def stop_stream(self):
        """Stop the stream (`$CS 1`) and return the pulses that arrived
        before the adapter's ``*STOPPED``; ``[]`` when no stream runs.
        Leaves the command channel clean. Raises `DeviceError` when the
        adapter never confirms (the stream is considered stopped
        regardless, so a reconnect can follow)."""
        with self._lock:
            if not self._streaming:
                return []
            pulses = []
            stopped = False
            try:
                self._send('$CS 1')
                deadline = time.monotonic() + self._timeout_s
                while True:
                    line = self._read_line(deadline)
                    if line is None:
                        break
                    if line.upper().startswith('*STOPPED') or line.startswith('?'):
                        stopped = True
                        break
                    pulses.append(self._parse_stream_line(line))
                self._drain(STOP_DRAIN_S)
            finally:
                self._streaming = False
            if not stopped:
                raise DeviceError(
                    f'{self.device["Device"]}: No *STOPPED after $CS 1')
            return pulses
