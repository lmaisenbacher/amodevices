# -*- coding: utf-8 -*-
"""Tests of the EA-1 driver against a fake socket that answers from a
script: the connect sequence (banner, leftover stream, echo, identity),
the line handling quirks seen on the live adapter (prompt, stray LF,
echo), the reply parsers, the settings API with its validation, and the
per-pulse stream with counter wraps, over-range and unforeseen lines.
No adapter needed. Runs under pytest or directly as a script.
"""

import socket
import sys
from collections import deque

import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.status import check_status_word
from amodevices.ophir_ea1 import ophir_ea1 as ea1
from amodevices.ophir_ea1.ophir_ea1 import OphirEA1, Pulse

BANNER = b'\xff\xfd\x24\xff\xfb\x01'

#: What the live adapter answered on 2026-09-17 (firmware EA1.17), with
#: the prompt and the stray LF exactly as received
LIVE_REPLIES = {
    '$CS 1': b'*STOPPED\r\n>',
    '$EE 0': b'*0 (ECHO OFF)\r\n>',
    '$VE': b'*EA1.17\r\n>',
    '$II': b'* ETHA 3061169 ETHERNET-ADAPTER\r\n>',
    '$HI': b'* PY 3064525 PE50BF-DFH-C 00608003 \r\n>',
    '$MM': b'*3\r\n>',
    '$AR': b'*3 10.0J 2.00J 200mJ 20.0mJ 2.00mJ \n\r\n>',
    '$RN': b'*3\n\r\n>',
    '$AW': b'*CONTINUOUS 190 3000 3 532 566 283 1064 2100 2940 \n\r\n>',
    '$PL': b'*1 1.0ms 2.0ms 5.0ms 10ms 20ms \n\r\n>',
    '$DQ': b'* 1 N/A\n\r\n>',
    '$UT': b'*105 105 2500\n\r\n>',
    '$EF': b'*1\r\n>',
    '$SE': b'* 0.030E-2\r\n>',
    '$ES': b'? Not Supported Command\r\n>',
    '$CS 3': b'*STARTED\r\n>',
    '$FE': b'*\r\n>',
    '$WN': b'*\r\n>',
    '$WL': b'*\r\n>',
    '$WI': b'*\r\n>',
    '$HC': b'*\r\n>',
}


class FakeSocket:
    """A socket whose `recv` serves queued chunks and whose `sendall`
    queues the scripted reply of the command just sent (looked up by the
    full command, then by its two-letter code). An empty queue raises
    `socket.timeout` at once, so nothing in these tests waits."""

    def __init__(self, replies=None, banner=BANNER):
        self.sent = []
        self.replies = dict(LIVE_REPLIES)
        self.replies.update(replies or {})
        self.incoming = deque()
        if banner:
            self.incoming.append(banner)
        self.closed = False
        self.peer_closed = False
        self.timeout = None

    def push(self, *chunks):
        """Queue bytes the adapter sends on its own (streamed lines)."""
        self.incoming.extend(chunks)

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        command = data.decode('ascii').rstrip('\r\n')
        self.sent.append(command)
        reply = self.replies.get(command, self.replies.get(command.split()[0]))
        if callable(reply):
            reply = reply(command)
        if reply is None:
            return
        if isinstance(reply, (bytes, bytearray)):
            reply = [bytes(reply)]
        self.incoming.extend(reply)

    def recv(self, n):
        if self.peer_closed:
            return b''
        if self.incoming:
            return self.incoming.popleft()
        raise socket.timeout()

    def close(self):
        self.closed = True


class FakeOphirEA1(OphirEA1):
    def __init__(self, fake, **device):
        super().__init__({'Device': 'EA-1 under test', 'Address': 'fake',
                          'Timeout': 0.2, **device})
        self.fake = fake

    def _open_socket(self):
        return self.fake


def connected(replies=None, banner=BANNER):
    fake = FakeSocket(replies, banner)
    dev = FakeOphirEA1(fake)
    dev.connect()
    return dev, fake


# -- connect -------------------------------------------------------------


def test_connect_identifies_and_prepares_the_channel():
    dev, fake = connected()
    assert dev.device_connected
    assert dev.firmware_version == 'EA1.17'
    assert (dev.adapter_type, dev.adapter_serial, dev.adapter_name) == (
        'ETHA', '3061169', 'ETHERNET-ADAPTER')
    assert (dev.head_type, dev.head_serial, dev.head_name, dev.head_extra) == (
        'PY', '3064525', 'PE50BF-DFH-C', '00608003')
    # A stream a crashed client left running is stopped first, then echo off
    assert fake.sent[:2] == ['$CS 1', '$EE 0']
    assert not dev.streaming


def test_banner_is_stripped_even_when_split_across_chunks():
    fake = FakeSocket(banner=None)
    fake.push(b'\xff\xfd', b'\x24\xff\xfb\x01')
    dev = FakeOphirEA1(fake)
    dev.connect()
    assert dev.firmware_version == 'EA1.17'


def test_connect_tolerates_a_rejected_stop_and_leftover_pulses():
    dev, fake = connected({'$CS 1': b'*12 34 1.0E-3\r\n?NOT STREAMING\r\n>'})
    assert dev.firmware_version == 'EA1.17'


def test_connect_failure_closes_the_socket():
    fake = FakeSocket({'$VE': b'?BAD COMMAND\r\n>'})
    dev = FakeOphirEA1(fake)
    with pytest.raises(DeviceError, match='rejected'):
        dev.connect()
    assert fake.closed
    assert not dev.device_connected


def test_close_is_idempotent_and_stops_a_stream():
    dev, fake = connected()
    dev.start_stream()
    dev.close()
    dev.close()
    assert '$CS 1' in fake.sent[-2:]
    assert fake.closed and not dev.streaming


# -- line handling -----------------------------------------------------------


def test_stray_lf_and_prompt_are_stripped():
    dev, _ = connected()
    assert dev.get_range_index() == 3
    assert dev.get_ranges() == (3, [10.0, 2.0, 0.2, 0.02, 0.002])


def test_reply_after_a_prompt_on_the_same_line():
    dev, fake = connected({'$VE': b'>*EA1.17\r\n'})
    assert dev._query('$VE') == 'EA1.17'


def test_command_echo_is_skipped():
    dev, _ = connected({'$VE': b'$VE\r\n*EA1.17\r\n>'})
    assert dev._query('$VE') == 'EA1.17'


def test_error_reply_raises_with_the_text():
    dev, _ = connected()
    with pytest.raises(DeviceError, match='Not Supported Command'):
        dev._query('$ES')


def test_missing_reply_times_out():
    dev, fake = connected()
    fake.replies['$VE'] = None
    with pytest.raises(DeviceError, match='No reply'):
        dev._query('$VE')


def test_threshold_reply_is_taken_verbatim_despite_its_pulse_shape():
    dev, _ = connected()
    assert dev.get_threshold() == (0.0105, 0.0105, 0.25)


def test_query_is_refused_while_streaming():
    dev, _ = connected()
    dev.start_stream()
    with pytest.raises(DeviceError, match='stop it first'):
        dev.get_range_index()


# -- parsers -------------------------------------------------------------


@pytest.mark.parametrize('token, unit, value', [
    ('200mJ', 'J', 0.2), ('10.0J', 'J', 10.0), ('2.00mJ', 'J', 0.002),
    ('20.0uJ', 'J', 20e-6), ('1.0ms', 's', 0.001), ('10ms', 's', 0.01),
    ('30.0mW', 'W', 0.03), ('1.5kW', 'W', 1500.0),
    ])
def test_parse_si_value(token, unit, value):
    assert ea1.parse_si_value(token, unit) == pytest.approx(value)


@pytest.mark.parametrize('token', ['200mJ', 'abc', '10', '1.0xs'])
def test_parse_si_value_rejects_the_wrong_unit(token):
    with pytest.raises(ValueError):
        ea1.parse_si_value(token, 's')


def test_parse_ranges_skips_auto():
    assert ea1.parse_ranges('2 AUTO 10.0W 3.00W 300mW 30.0mW', unit='W') == (
        2, [10.0, 3.0, 0.3, 0.03])


def test_parse_pulse_lengths():
    assert ea1.parse_pulse_lengths('1 1.0ms 2.0ms 5.0ms 10ms 20ms') == (
        1, [0.001, 0.002, 0.005, 0.01, 0.02])


def test_parse_wavelength_info_continuous_and_discrete():
    info = ea1.parse_wavelength_info(
        'CONTINUOUS 200 3000 2 2490 971 532 NONE NONE NONE')
    assert info == {'mode': 'CONTINUOUS', 'min_nm': 200.0, 'max_nm': 3000.0,
                    'active': 2,
                    'favorites_nm': [2490.0, 971.0, 532.0, None, None, None]}
    assert ea1.parse_wavelength_info('DISCRETE 2 CO2 YAG') == {
        'mode': 'DISCRETE', 'active': 2, 'names': ['CO2', 'YAG']}


def test_parse_pulse_line():
    assert ea1.parse_pulse_line('2222 33333 1.234E-1') == (
        2222, 33333, pytest.approx(0.1234), 'ok')
    assert ea1.parse_pulse_line('2 1500 OVER') == (2, 1500, None, 'overexposed')
    for body in ('STOPPED', '1 2', '1 2 3 4', 'a b 1.0', '-1 2 1.0'):
        with pytest.raises(ValueError):
            ea1.parse_pulse_line(body)


def test_unwrap32():
    assert ea1.unwrap32(0, 4294967295, 0) == (4294967296, 1)
    assert ea1.unwrap32(500, 4294967000, 1) == (500 + 2 * 2 ** 32, 2)
    # A small step backwards is a counter reset, not a wrap
    assert ea1.unwrap32(3, 10, 0) == (3, 0)
    assert ea1.unwrap32(7, None, 0) == (7, 0)


def test_strip_iac():
    assert ea1.strip_iac(b'\xff\xfd\x24\xff\xfb\x01*E\r\n') == (b'*E\r\n', b'')
    assert ea1.strip_iac(b'abc\xff\xfd') == (b'abc', b'\xff\xfd')
    assert ea1.strip_iac(b'\xff') == (b'', b'\xff')


def test_status_words_follow_the_fleet_vocabulary():
    for word in ea1.STATUS_WORDS:
        check_status_word(word)
    assert ea1.STATUS_OVER == 'overexposed'


# -- settings ------------------------------------------------------------


def test_settings_readbacks():
    dev, _ = connected()
    assert dev.get_measurement_mode() == ea1.MEASUREMENT_MODE_ENERGY
    assert dev.get_wavelength_nm() == 283.0
    assert dev.get_pulse_lengths() == (1, [0.001, 0.002, 0.005, 0.01, 0.02])
    assert dev.get_diffuser() == '1 N/A'
    assert dev.read_energy_polled() == (True, pytest.approx(3e-4), 'ok')


def test_range_index_for_energy_is_the_smallest_fitting_range():
    dev, _ = connected()
    assert dev.range_index_for_energy(1.5e-3) == 4
    assert dev.range_index_for_energy(2.5e-3) == 3
    assert dev.range_index_for_energy(5.0) == 0
    with pytest.raises(DeviceError, match='exceeds'):
        dev.range_index_for_energy(20.0)


def test_setters_send_the_commands():
    dev, fake = connected()
    dev.set_range_index(4)
    dev.set_wavelength_nm(300)
    dev.set_pulse_length_index(5)
    dev.set_threshold(0.02)
    dev.select_wavelength_favorite(3)
    dev.force_energy_mode()
    dev.save_settings()
    sent = [c for c in fake.sent if c.split()[0] in
            ('$WN', '$WL', '$PL', '$UT', '$WI', '$FE', '$HC')]
    assert sent == ['$WN 4', '$WL 300', '$PL', '$PL 5', '$UT', '$UT 200',
                    '$WI 3', '$FE', '$HC S']


def test_setters_refuse_values_outside_the_head_limits():
    dev, fake = connected()
    with pytest.raises(DeviceError, match='outside'):
        dev.set_wavelength_nm(100)
    with pytest.raises(DeviceError, match='outside'):
        dev.set_threshold(0.5)
    with pytest.raises(DeviceError, match='outside'):
        dev.set_range_index(5)
    with pytest.raises(DeviceError, match='outside'):
        dev.set_pulse_length_index(0)
    with pytest.raises(DeviceError, match='outside'):
        dev.set_pulse_length_index(6)
    with pytest.raises(DeviceError, match='outside'):
        dev.select_wavelength_favorite(7)
    assert not any(c.startswith(('$WL', '$WN', '$WI')) or c == '$UT 5000'
                   for c in fake.sent)


# -- streaming -----------------------------------------------------------


def drain_stream(dev, calls=10):
    pulses = []
    for _ in range(calls):
        batch = dev.read_pulses(0.01)
        if not batch:
            break
        pulses.extend(batch)
    return pulses


def test_stream_lines_become_pulses_with_unwrapped_counters():
    dev, fake = connected()
    dev.start_stream()
    assert dev.streaming and fake.sent[-1] == '$CS 3'
    fake.push(b'*4294967295 4294967000 1.0E-3\r\n*0 500 1.1E-3\r\n',
              b'*2 1500 OVER\r\n', b'garbage line\r\n?ERR\r\n',
              b'*5 2500 1.2E-3\r\n')
    pulses = drain_stream(dev)
    assert [p.index for p in pulses] == [
        4294967295, 4294967296, 4294967298, None, None, 4294967301]
    assert [p.timestamp_us for p in pulses] == [
        4294967000, 4294967796, 4294968796, None, None, 4294969796]
    assert [p.energy_j for p in pulses] == pytest.approx(
        [1.0e-3, 1.1e-3, None, None, None, 1.2e-3])
    assert [p.status for p in pulses] == [
        'ok', 'ok', 'overexposed', 'unparseable', 'unparseable', 'ok']
    assert [p.raw for p in pulses[3:5]] == ['garbage line', '?ERR']
    assert all(isinstance(p, Pulse) and p.t_recv > 0 for p in pulses)


def test_read_pulses_without_a_stream_returns_nothing_at_once():
    dev, _ = connected()
    assert dev.read_pulses(0.01) == []


def test_quiet_stream_returns_nothing():
    dev, _ = connected()
    dev.start_stream()
    assert dev.read_pulses(0.01) == []


def test_a_small_backwards_index_is_a_reset_not_a_wrap():
    dev, fake = connected()
    dev.start_stream()
    fake.push(b'*10 1000 1.0E-3\r\n*3 2000 1.0E-3\r\n')
    assert [p.index for p in drain_stream(dev)] == [10, 3]


def test_start_stream_resets_the_unwrap_state():
    dev, fake = connected()
    dev.start_stream()
    fake.push(b'*4294967295 10 1.0E-3\r\n*0 20 1.0E-3\r\n')
    assert [p.index for p in drain_stream(dev)] == [4294967295, 4294967296]
    dev.stop_stream()
    dev.start_stream()
    fake.push(b'*0 30 1.0E-3\r\n')
    assert [p.index for p in drain_stream(dev)] == [0]


def test_stop_stream_returns_the_pulses_before_stopped():
    dev, fake = connected({'$CS 1': b'*7 3500 1.3E-3\r\n*STOPPED\r\n>'})
    dev.start_stream()
    leftovers = dev.stop_stream()
    assert [p.index for p in leftovers] == [7]
    assert not dev.streaming
    assert dev.get_range_index() == 3


def test_stop_stream_without_confirmation_raises_but_ends_the_stream():
    dev, fake = connected()
    dev.start_stream()
    fake.replies['$CS 1'] = None
    with pytest.raises(DeviceError, match='STOPPED'):
        dev.stop_stream()
    assert not dev.streaming
    assert dev.stop_stream() == []


def test_unexpected_start_reply_raises():
    dev, _ = connected({'$CS 3': b'*NOPE\r\n>'})
    with pytest.raises(DeviceError, match='Unexpected reply'):
        dev.start_stream()
    assert not dev.streaming


def test_connection_closed_by_the_adapter_raises():
    dev, fake = connected()
    dev.start_stream()
    fake.peer_closed = True
    with pytest.raises(DeviceError, match='closed'):
        dev.read_pulses(0.01)


# -- review fixes (2026-09-17) -------------------------------------------


class EndlessStreamSocket(FakeSocket):
    """An adapter still streaming from a crashed client's session: every
    `recv` with an empty queue serves another pulse line until `$CS 1`
    arrives, after which it answers `*STOPPED` and falls silent."""

    def __init__(self):
        super().__init__()
        self.stopped = False
        self.served = 0

    def sendall(self, data):
        if data.startswith(b'$CS 1'):
            self.stopped = True
        super().sendall(data)

    def recv(self, n):
        if self.incoming or self.stopped:
            return super().recv(n)
        self.served += 1
        return b'*%d %d 1.0E-3\r\n' % (self.served, 100000 * self.served)


def test_connect_ends_its_banner_wait_under_a_running_stream(monkeypatch):
    monkeypatch.setattr(ea1, 'BANNER_WAIT_S', 0.02)
    fake = EndlessStreamSocket()
    dev = FakeOphirEA1(fake)
    dev.connect()
    assert fake.stopped and dev.firmware_version == 'EA1.17'
    assert fake.served > 0


def test_timestamp_unwrap_follows_the_host_clock_across_a_long_gap():
    # A plain wrap 100 ms after the last pulse
    assert ea1.unwrap32_timed(500, 4294967000, 1000.0, 1000.1) == 4294967796
    # A 45 min pause straddling the wrap: the raw value steps back by
    # less than half the range, which is no reset of the counter
    assert ea1.unwrap32_timed(2405032704, 4000000000, 1000.0, 3700.0) == (
        2405032704 + 2 ** 32)
    # A genuine reset after a minute shows as a backwards jump
    assert ea1.unwrap32_timed(5, 1000000000, 1000.0, 1060.0) == 5
    assert ea1.unwrap32_timed(7, None, None, 1000.0) == 7


def test_stream_timestamps_use_the_receive_time_as_the_guide():
    dev, fake = connected()
    dev.start_stream()
    dev._t_last_recv = 1000.0
    first = dev._parse_stream_line('*1 4000000000 1.0E-3')
    dev._t_last_recv = 3700.0
    second = dev._parse_stream_line('*2 2405032704 1.0E-3')
    assert (first.timestamp_us, second.timestamp_us) == (
        4000000000, 2405032704 + 2 ** 32)


@pytest.mark.parametrize('command, call', [
    ('$MM', lambda dev: dev.get_measurement_mode()),
    ('$AR', lambda dev: dev.get_ranges()),
    ('$RN', lambda dev: dev.get_range_index()),
    ('$AW', lambda dev: dev.get_wavelength_info()),
    ('$PL', lambda dev: dev.get_pulse_lengths()),
    ('$UT', lambda dev: dev.get_threshold()),
    ('$SE', lambda dev: dev.read_energy_polled()),
])
def test_an_empty_or_foreign_reply_raises_device_error(command, call):
    dev, _ = connected({command: b'*\r\n>'})
    with pytest.raises(DeviceError, match='Unexpected reply'):
        call(dev)
    dev, _ = connected({command: b'* PY 3064525 PE50BF-DFH-C 00608003\r\n>'})
    with pytest.raises(DeviceError, match='Unexpected reply'):
        call(dev)


def test_a_range_list_without_ranges_raises_device_error():
    dev, _ = connected({'$AR': b'*0\r\n>'})
    with pytest.raises(DeviceError, match='no ranges'):
        dev.range_index_for_energy(1e-3)


def test_connect_tolerates_a_rejected_echo_command_but_not_a_missing_reply():
    dev, _ = connected({'$EE 0': b'?BAD COMMAND\r\n>'})
    assert dev.firmware_version == 'EA1.17'
    fake = FakeSocket({'$EE 0': None})
    dev = FakeOphirEA1(fake)
    with pytest.raises(DeviceError, match='No reply'):
        dev.connect()
    assert fake.closed


def test_micro_sign_range_tokens_from_the_wire_parse():
    dev, _ = connected({'$AR': b'*1 10.0J 200\xb5J\r\n>'})
    assert dev.get_ranges() == (1, [10.0, pytest.approx(200e-6)])


def test_active_wavelength_slot_outside_the_favorites_raises():
    dev, _ = connected(
        {'$AW': b'*CONTINUOUS 190 3000 0 532 566 283 1064 2100 2940\r\n>'})
    with pytest.raises(DeviceError, match='slot 0'):
        dev.get_wavelength_nm()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
