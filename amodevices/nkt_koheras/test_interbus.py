# -*- coding: utf-8 -*-
"""Tests of the NKT Interbus protocol module against the worked examples
of the NKT Photonics SDK Instruction manual (chapter 2) and a scripted
fake channel: CRC, special-character conversion, telegram building and
parsing, and the transport's retry, validation, and address-pairing
rules. No hardware needed. Runs under pytest or directly as a script.
"""

import sys

import pytest

from amodevices.nkt_koheras import interbus as ib
from amodevices.nkt_koheras.interbus import (
    build_telegram, parse_telegram, find_telegram, crc16_xmodem, escape,
    unescape, decode_module_type, InterbusTransport, InterbusTimeout,
    InterbusNack, InterbusFrameError, InterbusProtocolError, Message,
    MSG_READ, MSG_WRITE, MSG_ACK, MSG_DATAGRAM, MSG_NACK, MSG_BUSY,
    MSG_CRC_ERROR, MSG_WRITE_SET1, HOST_ADDRESS_RANGE)


def h(text):
    return bytes.fromhex(text)


# ---------------------------------------------------------------------------
# The manual's examples (section 2.3-2.4; all bytes in hex)
# ---------------------------------------------------------------------------

def test_crc_of_the_manual_examples():
    assert crc16_xmodem(h('0FA2053003')) == 0xBCE1
    assert crc16_xmodem(h('0AA205238813')) == 0x3B55
    assert crc16_xmodem(h('0AA20411')) == 0x7583
    # A message plus its own CRC bytes runs to 0 (the receiver's check)
    assert crc16_xmodem(h('0FA2053003BCE1')) == 0
    assert crc16_xmodem(h('A20A0811 5E91 637E'.replace(' ', ''))) == 0


def test_build_telegram_matches_the_manual():
    # Example 1: emission on in a SuperK Extreme at address 0x0F
    assert (build_telegram(0x0F, 0xA2, MSG_WRITE, 0x30, b'\x03')
            == h('0D0FA2053003BCE10A'))
    # Example 2: 50 mW = 5000 x 10 uW to a K80-1 BasiK at address 0x0A;
    # the destination byte is a special character
    assert (build_telegram(0x0A, 0xA2, MSG_WRITE, 0x23,
                           (5000).to_bytes(2, 'little'))
            == h('0D5E4AA2052388133B550A'))
    # Example 3: read the temperature register 0x11
    assert build_telegram(0x0A, 0xA2, MSG_READ, 0x11) == h('0D5E4AA2041175830A')
    # Section 2.3: data byte 0x0D and destination 0x0A converted, the CRC
    # calculated before the conversion
    assert (build_telegram(0x0A, 0x42, MSG_WRITE, 0x32, b'\x0D')
            == h('0D5E4A4205325E4D9CF00A'))


def test_parse_telegram_matches_the_manual():
    assert (parse_telegram(h('0DA20F0330482F0A'))
            == Message(0xA2, 0x0F, MSG_ACK, 0x30, b''))
    assert (parse_telegram(h('0DA25E4A0323818D0A'))
            == Message(0xA2, 0x0A, MSG_ACK, 0x23, b''))
    reply = parse_telegram(h('0DA25E4A08115E9E91637E0A'))
    assert reply == Message(0xA2, 0x0A, MSG_DATAGRAM, 0x11, h('5E91'))
    assert int.from_bytes(reply.data, 'little') == 37214


def test_escape_and_unescape_round_trip():
    message = bytes(range(256))
    assert unescape(escape(message)) == message
    assert escape(b'\x0A\x0D\x5E') == h('5E4A5E4D5E9E')
    with pytest.raises(InterbusFrameError):
        unescape(b'\x01\x5E')                    # dangling escape
    with pytest.raises(InterbusFrameError):
        unescape(b'\x5E\x01')                    # not a substitution word


def test_parse_telegram_rejects_bad_frames():
    with pytest.raises(InterbusFrameError):
        parse_telegram(h('0DA20F0330482E0A'))    # CRC off by one
    with pytest.raises(InterbusFrameError):
        parse_telegram(h('0DA20F030A'))          # too short
    with pytest.raises(InterbusFrameError):
        parse_telegram(h('A20F0330482F0A'))      # no SOT
    # An ack without the register byte (older modules) parses with None
    body = h('A20F03')
    body += crc16_xmodem(body).to_bytes(2, 'big')
    assert parse_telegram(b'\x0D' + escape(body) + b'\x0A').register is None


def test_find_telegram_skips_garbage_before_the_last_sot():
    telegram = h('0DA20F0330482F0A')
    assert find_telegram(b'\x00garbage' + telegram) == telegram
    assert find_telegram(h('0D0102') + telegram) == telegram
    assert find_telegram(telegram[:-1]) is None      # no trailing EOT
    assert find_telegram(b'no start\x0A') is None
    assert find_telegram(b'') is None


def test_build_telegram_refuses_bad_arguments():
    with pytest.raises(ib.InterbusError):
        build_telegram(0x100, 0xA2, MSG_READ, 0x11)
    with pytest.raises(ib.InterbusError):
        build_telegram(0x0A, 0xA2, MSG_WRITE, 0x8D, bytes(241))


def test_decode_module_type():
    assert decode_module_type(b'\x33') == 0x33
    assert decode_module_type(b'\x34\x00') == 0x34
    assert decode_module_type(b'\x01\x02') == 0x0201


# ---------------------------------------------------------------------------
# Transport on a scripted channel
# ---------------------------------------------------------------------------

def datagram(data):
    """A reply builder: the datagram answering a read with `data`."""
    return lambda request: build_telegram(
        request.src, request.dest, MSG_DATAGRAM, request.register, data)


def ack(request):
    return build_telegram(request.src, request.dest, MSG_ACK, request.register)


def ack_without_register(request):
    body = bytes((request.src, request.dest, MSG_ACK))
    body += crc16_xmodem(body).to_bytes(2, 'big')
    return b'\x0D' + escape(body) + b'\x0A'


def nack(request):
    return build_telegram(request.src, request.dest, MSG_NACK, request.register)


def busy(request):
    return build_telegram(request.src, request.dest, MSG_BUSY, request.register)


def crc_error(request):
    return build_telegram(request.src, request.dest, MSG_CRC_ERROR,
                          request.register)


def stale(request):
    """A reply addressed to the host address BEFORE the request's (a late
    answer to an earlier, timed-out request)."""
    previous = HOST_ADDRESS_RANGE[
        (HOST_ADDRESS_RANGE.index(request.src) - 1) % len(HOST_ADDRESS_RANGE)]
    return build_telegram(previous, request.dest, MSG_DATAGRAM,
                          request.register, b'\x00\x00')


SILENCE = None


class FakeChannel:
    """An Interbus channel answering from a script: `replies` maps
    (dest, msg_type, register) to a list of reply builders (or None =
    silence) consumed in order; `prefix` bytes precede every reply (line
    noise); `writes` records the telegrams written; `resets` counts the
    input buffer resets."""

    def __init__(self, replies=None, prefix=b''):
        self.replies = {key: list(value)
                        for key, value in (replies or {}).items()}
        self.prefix = prefix
        self.writes = []
        self.resets = 0
        self._pending = b''

    def write(self, data):
        self.writes.append(data)
        request = parse_telegram(data)
        queue = self.replies.get(
            (request.dest, request.msg_type, request.register), [])
        builders = queue.pop(0) if queue else SILENCE
        self._pending = b''
        if builders is None:
            return
        if not isinstance(builders, (list, tuple)):
            builders = [builders]
        self._pending = self.prefix + b''.join(
            builder(request) for builder in builders)

    def read_until(self, terminator, deadline):
        index = self._pending.find(terminator)
        if index < 0:
            out, self._pending = self._pending, b''
        else:
            out, self._pending = (self._pending[:index + 1],
                                  self._pending[index + 1:])
        return out

    def reset_input_buffer(self):
        self.resets += 1
        self._pending = b''

    def close(self):
        pass


def transport(replies=None, prefix=b'', **kwargs):
    kwargs.setdefault('timeout_s', 0.01)
    kwargs.setdefault('retries', 3)
    return InterbusTransport(FakeChannel(replies, prefix), **kwargs)


def requests(tr):
    return [parse_telegram(w) for w in tr.channel.writes]


def test_read_u16_decodes_the_manual_reply():
    tr = transport({(0x0A, MSG_READ, 0x11): [datagram(h('5E91'))]},
                   host_address=0xA2)
    assert tr.read_u16(0x0A, 0x11) == 37214
    assert tr.channel.writes == [h('0D5E4AA2041175830A')]
    assert tr.channel.resets == 1


def test_typed_reads_and_writes():
    tr = transport({
        (1, MSG_READ, 0x2A): [datagram(h('CEFF'))],
        (1, MSG_READ, 0x72): [datagram(h('F6FFFFFF'))],
        (1, MSG_READ, 0x32): [datagram((10640000).to_bytes(4, 'little'))],
        (1, MSG_READ, 0x65): [datagram(b'17360328\x00\x00')],
        (1, MSG_READ, 0xB8): [datagram(h('0000803F00000040'))],
        (1, MSG_WRITE, 0x2A): [ack],
        (1, MSG_WRITE, 0x30): [ack_without_register],
        (1, MSG_WRITE_SET1, 0x31): [ack],
    })
    assert tr.read_i16(1, 0x2A) == -50
    assert tr.read_i32(1, 0x72) == -10
    assert tr.read_u32(1, 0x32) == 10640000
    assert tr.read_str(1, 0x65) == '17360328'
    assert tr.read_f32(1, 0xB8) == 1.0
    tr.write_i16(1, 0x2A, 123)
    tr.write_u8(1, 0x30, 1)                      # ack without register
    tr.write_set_bits(1, 0x31, 0x0004)
    sent = requests(tr)[-3:]
    assert sent[0].data == h('7B00')
    assert sent[1].data == b'\x01'
    assert (sent[2].msg_type, sent[2].data) == (MSG_WRITE_SET1, h('0400'))


def test_source_address_cycles_and_replies_are_paired():
    tr = transport({(1, MSG_READ, 0x61): [datagram(b'\x33')] * 3})
    for _ in range(3):
        tr.read_u8(1, 0x61)
    sources = [r.src for r in requests(tr)]
    assert sources == list(HOST_ADDRESS_RANGE[:3])
    assert all(src > 160 for src in sources)


def test_stale_reply_to_an_earlier_request_is_ignored():
    # The first read times out; its late reply then arrives together
    # with the answer to the second read and must be skipped
    tr = transport({(1, MSG_READ, 0x2A): [
        SILENCE, [stale, datagram(h('7B00'))]]}, retries=0)
    with pytest.raises(InterbusTimeout):
        tr.read_i16(1, 0x2A)
    assert tr.read_i16(1, 0x2A) == 123


def test_garbage_before_the_reply_is_skipped():
    tr = transport({(1, MSG_READ, 0x2A): [datagram(h('7B00'))]},
                   prefix=b'\x00\xFF\x0D\x01')
    assert tr.read_i16(1, 0x2A) == 123


def test_busy_and_crc_error_replies_are_retried():
    tr = transport({(1, MSG_WRITE, 0x2A): [busy, crc_error, ack]})
    tr.write_i16(1, 0x2A, 5)
    assert len(tr.channel.writes) == 3


def test_timeouts_exhaust_the_retries():
    tr = transport({}, retries=2)
    with pytest.raises(InterbusTimeout) as excinfo:
        tr.read_u8(1, 0x30)
    assert len(tr.channel.writes) == 3
    assert '3 attempt(s)' in str(excinfo.value)


def test_nack_is_final():
    tr = transport({(1, MSG_READ, 0x99): [nack]})
    with pytest.raises(InterbusNack):
        tr.read_u8(1, 0x99)
    assert len(tr.channel.writes) == 1


def test_wrong_reply_kinds_raise_protocol_errors():
    tr = transport({
        (1, MSG_READ, 0x2A): [ack],
        (1, MSG_WRITE, 0x2A): [datagram(b'\x00')],
        (1, MSG_READ, 0x30): [lambda r: build_telegram(
            r.src, r.dest, MSG_DATAGRAM, 0x31, b'\x00')],
        (1, MSG_READ, 0x66): [datagram(b'\x01')],
    })
    with pytest.raises(InterbusProtocolError):
        tr.read_i16(1, 0x2A)
    with pytest.raises(InterbusProtocolError):
        tr.write_i16(1, 0x2A, 0)
    with pytest.raises(InterbusProtocolError):
        tr.read_u8(1, 0x30)                      # datagram of another register
    with pytest.raises(InterbusProtocolError):
        tr.read_u16(1, 0x66)                     # one byte for a U16


def test_toggle_write_is_never_retried():
    tr = transport({}, retries=3)
    with pytest.raises(InterbusTimeout):
        tr.write_toggle_bits(1, 0x31, 0x0002)
    assert len(tr.channel.writes) == 1


def test_scan_reports_the_answering_addresses():
    tr = transport({
        (1, MSG_READ, 0x61): [datagram(b'\x33')],
        (128, MSG_READ, 0x61): [datagram(b'\x34\x00')],
        (5, MSG_READ, 0x61): [nack],
    })
    assert tr.scan(addresses=(1, 2, 5, 128), timeout_s=0.001) == {
        1: 0x33, 128: 0x34}


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
