# -*- coding: utf-8 -*-
"""
NKT Photonics Interbus protocol — the register protocol every NKT
Photonics laser system speaks over its RS-232, USB (virtual COM port),
RS-485, or Ethernet port. Transport-agnostic: the framing, CRC, and
special-character conversion live in pure functions, `InterbusTransport`
runs request/reply transactions over a `SerialChannel` or `TCPChannel`.

Specification: "NKT Photonics SDK Instruction manual", chapter 2
(Interbus Protocol Description; SDK 2.1.16). Facts implemented here:

- Serial settings 115200 bit/s, 8 data bits, 1 stop bit, no parity;
  Ethernet = the same telegrams over TCP, port 10001 by default.
- Telegram = [SOT 0x0D][message][EOT 0x0A]. The message is
  destination address, source address, message type, register number,
  0..240 data bytes, CRC MSB, CRC LSB. Multi-byte register values are
  little-endian; the CRC alone is big-endian.
- CRC-16 CCITT (X^16 + X^12 + X^5 + 1, polynomial 0x1021) with the
  initial value 0 — "CRC-CCITT (XModem)" — over the whole message
  BEFORE the special-character conversion; a receiver running all
  bytes incl. the CRC through the same function ends at 0.
- Special characters: after the CRC is appended, every 0x0A, 0x0D, and
  0x5E in the message (CRC bytes included) becomes 0x5E followed by the
  byte plus 0x40. The receiver drops the 0x5E and subtracts 0x40 from
  the byte after it. The CRC is not recalculated after the conversion.
- Message types: 0 Nack, 1 CRC error, 2 Busy, 3 Ack, 4 Read, 5 Write,
  6 Write SET1, 7 Write CLR1, 8 Datagram, 9 Write TGL1. A read is
  answered with a datagram carrying [register, data...], a write with
  an ack carrying [register] ("in special cases, and in older modules,
  this byte may be 0"). A non-existing or restricted register gets a
  Nack.
- Module addresses run from 1 to 160; host addresses must be above 160
  (the manual's examples use 0xA2). Cycling the source address per
  telegram (161..255) pairs every reply with its request — a module
  answers to the source address it was asked from.
- Address scan: read register 0x61 (module type) from every address
  with a 50-100 ms timeout; a module present answers with its type.

The manual's worked examples are the test vectors of `test_interbus.py`.

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging
import socket
import struct
import threading
import time
from collections import namedtuple

from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

#: Start of telegram, end of telegram, start of a substitution word, and
#: the value added to a substituted byte
SOT = 0x0D
EOT = 0x0A
ESC = 0x5E
ESC_OFFSET = 0x40
SPECIAL_BYTES = frozenset((SOT, EOT, ESC))

#: Message types (manual section 2.2)
MSG_NACK = 0
MSG_CRC_ERROR = 1
MSG_BUSY = 2
MSG_ACK = 3
MSG_READ = 4
MSG_WRITE = 5
MSG_WRITE_SET1 = 6
MSG_WRITE_CLR1 = 7
MSG_DATAGRAM = 8
MSG_WRITE_TGL1 = 9
MSG_NAMES = {
    MSG_NACK: 'nack', MSG_CRC_ERROR: 'crc_error', MSG_BUSY: 'busy',
    MSG_ACK: 'ack', MSG_READ: 'read', MSG_WRITE: 'write',
    MSG_WRITE_SET1: 'write_set1', MSG_WRITE_CLR1: 'write_clr1',
    MSG_DATAGRAM: 'datagram', MSG_WRITE_TGL1: 'write_tgl1'}
#: The write types a module answers with an ack
WRITE_TYPES = frozenset(
    (MSG_WRITE, MSG_WRITE_SET1, MSG_WRITE_CLR1, MSG_WRITE_TGL1))

#: Host (source) address of the manual's examples, and the range a host
#: may cycle through to pair replies with requests
HOST_ADDRESS_DEFAULT = 0xA2
HOST_ADDRESS_RANGE = range(0xA1, 0x100)
#: Longest data field of a message
MAX_DATA_BYTES = 240
#: The register every module answers with its module type number
REG_MODULE_TYPE = 0x61
#: Pause before repeating a request the module answered with Busy or
#: CRC error
RETRY_DELAY_S = 0.02
#: Default TCP port of the Ethernet interface
TCP_PORT_DEFAULT = 10001


class InterbusError(DeviceError):
    """Any failure of an Interbus transaction."""


class InterbusTimeout(InterbusError):
    """No usable reply within the timeout (all attempts)."""


class InterbusNack(InterbusError):
    """The module refused the request (register unknown, restricted, or
    the value not allowed)."""


class InterbusFrameError(InterbusError):
    """A received telegram is malformed (bad escape sequence, too short,
    CRC mismatch)."""


class InterbusProtocolError(InterbusError):
    """A well-formed reply of the wrong kind (a datagram for a write, a
    datagram for another register, a payload too short for its type)."""


#: A parsed message. `register` is None only for an ack without the
#: register byte (older modules); `data` excludes the register byte.
Message = namedtuple('Message', 'dest src msg_type register data')


# ---------------------------------------------------------------------------
# Pure protocol functions
# ---------------------------------------------------------------------------

def crc16_xmodem(data):
    """CRC-16 CCITT with polynomial 0x1021 and initial value 0 (XModem),
    no reflection, no final xor — the manual's `CRC_add1` in Python.
    Running a message plus its own CRC bytes through it yields 0."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def escape(message):
    """Special-character conversion of a message (CRC already appended):
    0x0A, 0x0D, 0x5E become 0x5E followed by the byte plus 0x40."""
    out = bytearray()
    for byte in message:
        if byte in SPECIAL_BYTES:
            out.append(ESC)
            out.append(byte + ESC_OFFSET)
        else:
            out.append(byte)
    return bytes(out)


def unescape(body):
    """Reverse the special-character conversion of a telegram body (the
    bytes between SOT and EOT). Raises `InterbusFrameError` on a dangling
    or invalid escape sequence."""
    out = bytearray()
    escaped = False
    for byte in body:
        if escaped:
            value = byte - ESC_OFFSET
            if value not in SPECIAL_BYTES:
                raise InterbusFrameError(
                    f'Invalid escape sequence 0x5E 0x{byte:02X}')
            out.append(value)
            escaped = False
        elif byte == ESC:
            escaped = True
        else:
            out.append(byte)
    if escaped:
        raise InterbusFrameError(
            'Telegram ends with an unfinished escape sequence')
    return bytes(out)


def build_telegram(dest, src, msg_type, register, data=b''):
    """The bytes to transmit for one message: SOT, the escaped message
    (dest, src, type, register, data, CRC MSB, CRC LSB), EOT."""
    data = bytes(data)
    if len(data) > MAX_DATA_BYTES:
        raise InterbusError(
            f'Data field of {len(data)} bytes exceeds the {MAX_DATA_BYTES} '
            'byte maximum')
    for name, value in (('dest', dest), ('src', src), ('type', msg_type),
                        ('register', register)):
        if not 0 <= value <= 0xFF:
            raise InterbusError(f'{name} 0x{value:X} is not a byte')
    message = bytes((dest, src, msg_type, register)) + data
    message += crc16_xmodem(message).to_bytes(2, 'big')
    return bytes((SOT,)) + escape(message) + bytes((EOT,))


def parse_telegram(telegram):
    """Parse one framed telegram (SOT ... EOT) into a `Message`,
    verifying the framing and the CRC. Raises `InterbusFrameError`."""
    telegram = bytes(telegram)
    if len(telegram) < 2 or telegram[0] != SOT or telegram[-1] != EOT:
        raise InterbusFrameError(
            f'Telegram is not framed by SOT/EOT: {telegram.hex(" ")}')
    body = unescape(telegram[1:-1])
    # dest, src, type, CRC MSB, CRC LSB at the least: an ack of an old
    # module may omit the register byte
    if len(body) < 5:
        raise InterbusFrameError(
            f'Telegram body of {len(body)} bytes is too short: '
            f'{body.hex(" ")}')
    if crc16_xmodem(body) != 0:
        raise InterbusFrameError(
            f'CRC error in received telegram {body.hex(" ")}')
    register = body[3] if len(body) >= 6 else None
    return Message(body[0], body[1], body[2], register, body[4:-2])


def find_telegram(raw):
    """The last complete telegram in a chunk read up to an EOT: the slice
    from the last SOT to the trailing EOT, or None when there is no SOT
    or no trailing EOT. SOT and EOT never occur inside a converted
    message, so everything before the last SOT is garbage (a partial
    telegram from before a buffer reset, line noise)."""
    raw = bytes(raw)
    if not raw or raw[-1] != EOT:
        return None
    start = raw.rfind(bytes((SOT,)))
    if start < 0:
        return None
    return raw[start:]


def decode_module_type(data):
    """The module type number from a register 0x61 payload: one byte in
    legacy modules, two (little-endian) in later ones."""
    if len(data) >= 2:
        return int.from_bytes(data[:2], 'little')
    return data[0]


# ---------------------------------------------------------------------------
# Channels — the byte transports the transport class drives
# ---------------------------------------------------------------------------

class SerialChannel:
    """A pyserial port as an Interbus channel."""

    def __init__(self, ser):
        self.ser = ser

    def write(self, data):
        try:
            n_written = self.ser.write(data)
        except Exception as exc:
            raise InterbusError(f'Serial write failed: {exc}') from exc
        if n_written != len(data):
            raise InterbusError(
                f'Serial write incomplete: {n_written} of {len(data)} bytes')

    def read_until(self, terminator, deadline):
        """Read up to and including `terminator`, giving up at the
        monotonic time `deadline`; a partial buffer comes back then."""
        try:
            self.ser.timeout = max(0., deadline - time.monotonic())
            return self.ser.read_until(terminator)
        except Exception as exc:
            raise InterbusError(f'Serial read failed: {exc}') from exc

    def reset_input_buffer(self):
        try:
            self.ser.reset_input_buffer()
        except Exception as exc:
            raise InterbusError(
                f'Serial input buffer reset failed: {exc}') from exc

    def close(self):
        self.ser.close()


class TCPChannel:
    """The Ethernet interface (raw TCP, the same telegrams) as an Interbus
    channel. Bytes beyond a terminator stay buffered for the next read."""

    def __init__(self, host, port=TCP_PORT_DEFAULT, connect_timeout_s=5.):
        try:
            self._sock = socket.create_connection(
                (host, port), timeout=connect_timeout_s)
        except OSError as exc:
            raise InterbusError(
                f'TCP connection to {host}:{port} failed: {exc}') from exc
        self._buffer = b''

    def write(self, data):
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise InterbusError(f'TCP write failed: {exc}') from exc

    def read_until(self, terminator, deadline):
        while terminator not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break
            except OSError as exc:
                raise InterbusError(f'TCP read failed: {exc}') from exc
            if not chunk:
                raise InterbusError('TCP connection closed by the device')
            self._buffer += chunk
        index = self._buffer.find(terminator)
        if index < 0:
            out, self._buffer = self._buffer, b''
        else:
            out, self._buffer = (self._buffer[:index + 1],
                                 self._buffer[index + 1:])
        return out

    def reset_input_buffer(self):
        self._buffer = b''
        self._sock.settimeout(0.)
        try:
            while self._sock.recv(4096):
                pass
        except (BlockingIOError, socket.timeout, OSError):
            pass

    def close(self):
        self._sock.close()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

class InterbusTransport:
    """Request/reply transactions over a channel, one at a time.

    `timeout_s` bounds the wait for a reply per attempt, `retries` is
    the number of repeats after a timeout, a Busy, or a CRC-error reply
    (a Nack is final). With `host_address` None the source address
    cycles through 161..255 per transaction, so a late reply to an
    earlier request is recognized by its destination byte and dropped;
    a fixed address (the manual's examples use 0xA2) is for tests and
    for legacy modules that accept one host address only. A write of
    type Write TGL1 is never repeated: a repeat would toggle the bits
    back."""

    def __init__(self, channel, timeout_s=0.5, retries=3, host_address=None,
                 name='Interbus'):
        self.channel = channel
        self.timeout_s = float(timeout_s)
        self.retries = int(retries)
        self.host_address = host_address
        self.name = name
        self._lock = threading.RLock()
        self._source_index = 0

    def _next_source(self):
        if self.host_address is not None:
            return self.host_address
        address = HOST_ADDRESS_RANGE[self._source_index]
        self._source_index = (self._source_index + 1) % len(HOST_ADDRESS_RANGE)
        return address

    def _await_reply(self, dest, src, deadline):
        """The first well-formed telegram from `dest` to `src` before the
        deadline, or None. Garbage, malformed frames, and telegrams of
        other conversations (a stale reply, another module) are dropped."""
        terminator = bytes((EOT,))
        while True:
            raw = self.channel.read_until(terminator, deadline)
            if not raw or raw[-1] != EOT:
                return None
            telegram = find_telegram(raw)
            if telegram is None:
                if time.monotonic() >= deadline:
                    return None
                continue
            try:
                message = parse_telegram(telegram)
            except InterbusFrameError as exc:
                logger.debug('%s: dropped frame: %s', self.name, exc)
                if time.monotonic() >= deadline:
                    return None
                continue
            if message.dest != src or message.src != dest:
                logger.debug(
                    '%s: dropped telegram for another conversation '
                    '(dest 0x%02X, src 0x%02X): %s', self.name, message.dest,
                    message.src, telegram.hex(' '))
                if time.monotonic() >= deadline:
                    return None
                continue
            return message

    def transaction(self, dest, msg_type, register, data=b'', timeout_s=None,
                    retries=None):
        """Send one message and return the module's reply as a `Message`:
        the datagram of a read (its `register` verified, `data` = the
        register content), the ack of a write. Raises `InterbusNack`,
        `InterbusProtocolError`, `InterbusTimeout`, or the channel's
        `InterbusError`."""
        timeout_s = self.timeout_s if timeout_s is None else float(timeout_s)
        retries = self.retries if retries is None else int(retries)
        attempts = 1 if msg_type == MSG_WRITE_TGL1 else 1 + max(0, retries)
        what = (f'{MSG_NAMES.get(msg_type, msg_type)} of register '
                f'0x{register:02X} at address 0x{dest:02X}')
        with self._lock:
            last_problem = 'no reply'
            for _ in range(attempts):
                src = self._next_source()
                telegram = build_telegram(dest, src, msg_type, register, data)
                self.channel.reset_input_buffer()
                self.channel.write(telegram)
                reply = self._await_reply(
                    dest, src, time.monotonic() + timeout_s)
                if reply is None:
                    last_problem = 'no reply'
                    continue
                if reply.msg_type in (MSG_BUSY, MSG_CRC_ERROR):
                    last_problem = f'module answered {MSG_NAMES[reply.msg_type]}'
                    time.sleep(RETRY_DELAY_S)
                    continue
                if reply.msg_type == MSG_NACK:
                    raise InterbusNack(
                        f'{self.name}: {what} not acknowledged (register '
                        'unknown, restricted, or value not allowed)')
                if msg_type == MSG_READ:
                    if reply.msg_type != MSG_DATAGRAM:
                        raise InterbusProtocolError(
                            f'{self.name}: {what} answered with '
                            f'{MSG_NAMES.get(reply.msg_type, reply.msg_type)} '
                            'instead of a datagram')
                    if reply.register != register:
                        raise InterbusProtocolError(
                            f'{self.name}: {what} answered with a datagram '
                            f'of register 0x{reply.register:02X}')
                    return reply
                if reply.msg_type != MSG_ACK:
                    raise InterbusProtocolError(
                        f'{self.name}: {what} answered with '
                        f'{MSG_NAMES.get(reply.msg_type, reply.msg_type)} '
                        'instead of an ack')
                if reply.register not in (register, 0, None):
                    raise InterbusProtocolError(
                        f'{self.name}: {what} acknowledged for register '
                        f'0x{reply.register:02X}')
                return reply
            raise InterbusTimeout(
                f'{self.name}: {what} failed after {attempts} attempt(s): '
                f'{last_problem}')

    # -- register access --------------------------------------------------

    def read_register(self, dest, register, **kwargs):
        """The raw content (bytes) of a register."""
        return self.transaction(dest, MSG_READ, register, **kwargs).data

    def write_register(self, dest, register, data, msg_type=MSG_WRITE,
                       **kwargs):
        """Write raw `data` (bytes) to a register (type Write, or one of
        the bit-manipulating write types)."""
        self.transaction(dest, msg_type, register, bytes(data), **kwargs)

    def _read_struct(self, dest, register, fmt):
        data = self.read_register(dest, register)
        size = struct.calcsize(fmt)
        if len(data) < size:
            raise InterbusProtocolError(
                f'{self.name}: register 0x{register:02X} at address '
                f'0x{dest:02X} returned {len(data)} byte(s), expected {size}')
        return struct.unpack(fmt, data[:size])[0]

    def read_u8(self, dest, register):
        return self._read_struct(dest, register, '<B')

    def read_i8(self, dest, register):
        return self._read_struct(dest, register, '<b')

    def read_u16(self, dest, register):
        return self._read_struct(dest, register, '<H')

    def read_i16(self, dest, register):
        return self._read_struct(dest, register, '<h')

    def read_u32(self, dest, register):
        return self._read_struct(dest, register, '<I')

    def read_i32(self, dest, register):
        return self._read_struct(dest, register, '<i')

    def read_f32(self, dest, register):
        return self._read_struct(dest, register, '<f')

    def read_str(self, dest, register):
        """A string register (ASCII; NUL padding and whitespace stripped)."""
        data = self.read_register(dest, register)
        return data.split(b'\x00', 1)[0].decode('ascii', 'replace').strip()

    def _write_struct(self, dest, register, fmt, value, **kwargs):
        try:
            data = struct.pack(fmt, int(value))
        except struct.error as exc:
            raise InterbusError(
                f'{self.name}: value {value!r} does not fit register '
                f'0x{register:02X} ({fmt})') from exc
        self.write_register(dest, register, data, **kwargs)

    def write_u8(self, dest, register, value, **kwargs):
        self._write_struct(dest, register, '<B', value, **kwargs)

    def write_u16(self, dest, register, value, **kwargs):
        self._write_struct(dest, register, '<H', value, **kwargs)

    def write_i16(self, dest, register, value, **kwargs):
        self._write_struct(dest, register, '<h', value, **kwargs)

    def write_u32(self, dest, register, value, **kwargs):
        self._write_struct(dest, register, '<I', value, **kwargs)

    def write_i32(self, dest, register, value, **kwargs):
        self._write_struct(dest, register, '<i', value, **kwargs)

    def write_set_bits(self, dest, register, mask, width=2):
        """Set the bits of `mask` in a register (Write SET1)."""
        self.write_register(dest, register, int(mask).to_bytes(width, 'little'),
                            msg_type=MSG_WRITE_SET1)

    def write_clear_bits(self, dest, register, mask, width=2):
        """Clear the bits of `mask` in a register (Write CLR1)."""
        self.write_register(dest, register, int(mask).to_bytes(width, 'little'),
                            msg_type=MSG_WRITE_CLR1)

    def write_toggle_bits(self, dest, register, mask, width=2):
        """Toggle the bits of `mask` in a register (Write TGL1; never
        retried)."""
        self.write_register(dest, register, int(mask).to_bytes(width, 'little'),
                            msg_type=MSG_WRITE_TGL1)

    def scan(self, addresses=range(1, 256), timeout_s=0.1):
        """Address scan: {address: module type} of every address that
        answers a read of register 0x61 within `timeout_s` (one attempt
        each; the manual suggests 50-100 ms)."""
        found = {}
        for address in addresses:
            try:
                reply = self.transaction(
                    address, MSG_READ, REG_MODULE_TYPE, timeout_s=timeout_s,
                    retries=0)
            except (InterbusTimeout, InterbusNack, InterbusProtocolError):
                continue
            if reply.data:
                found[address] = decode_module_type(reply.data)
        return found
