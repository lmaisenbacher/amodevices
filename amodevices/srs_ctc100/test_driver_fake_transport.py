# -*- coding: utf-8 -*-
"""Tests of the CTC100 driver against a fake serial port that answers from
a script: the one-query read of every channel (`read_outputs`) with the
replies of a CTC100 with firmware 4.65 whose inputs are named after their
sensors, its checks, and the discarding of a late reply before a query.
No instrument needed. Runs under pytest or directly as a script.
"""

import math
import sys

import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.srs_ctc100.srs_ctc100 import SRSCTC100

NAMES = (b'X207812, X207813, X233659, X233660, HEATER1, 50KHeat, AIO 1, '
         b'AIO 2, AIO 3, AIO 4, DIO, Relays, V1, V2, V3\r\n')
OUTPUT = (b'7.892, 7.832, 7.857, 7.765, 0.000, 0.000, 0.628, 0.628, 0.628, '
          b'1.442, 0, 0, NaN, NaN, NaN\r\n')


class FakeSerial:
    """A serial port whose `write` queues the scripted reply of the line
    just sent and whose `readline` returns it (b'' when nothing is
    queued, as a timeout would)."""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.sent = []
        self.incoming = b''

    def write(self, data):
        command = data.decode('ascii').rstrip('\n')
        self.sent.append(command)
        reply = self.replies.get(command, b'')
        self.incoming += reply() if callable(reply) else reply
        return len(data)

    def readline(self):
        line, sep, rest = self.incoming.partition(b'\n')
        self.incoming = rest
        return line + sep

    def reset_input_buffer(self):
        self.incoming = b''

    def close(self):
        pass


def make_device(replies):
    """A driver connected to a `FakeSerial` with `replies`."""
    fake = FakeSerial(replies)
    dev = SRSCTC100({'Device': 'Fake CTC100', 'Address': 'COM99'})
    dev.serial_connect = lambda: setattr(dev, 'ser', fake)
    dev.serial_close = lambda: None
    dev.connect()
    return dev, fake


def test_read_outputs_maps_every_channel():
    dev, fake = make_device({'getOutputNames?': NAMES, 'getOutput?': OUTPUT})
    outputs = dev.read_outputs()
    assert list(outputs)[:6] == ['X207812', 'X207813', 'X233659', 'X233660',
                                 'HEATER1', '50KHeat']
    assert outputs['X233660'] == 7.765
    assert outputs['AIO 4'] == 1.442
    assert math.isnan(outputs['V1'])
    assert fake.sent == ['getOutputNames?', 'getOutput?']


def test_names_read_once_per_connection():
    dev, fake = make_device({'getOutputNames?': NAMES, 'getOutput?': OUTPUT})
    for _ in range(3):
        dev.read_outputs()
    assert fake.sent.count('getOutputNames?') == 1
    assert fake.sent.count('getOutput?') == 3
    dev.connect()
    dev.read_outputs()
    assert fake.sent.count('getOutputNames?') == 2


def test_count_mismatch_refused_and_names_read_again():
    dev, fake = make_device(
        {'getOutputNames?': NAMES, 'getOutput?': b'7.892, 7.832\r\n'})
    with pytest.raises(DeviceError, match='2 values for 15 channels'):
        dev.read_outputs()
    fake.replies['getOutput?'] = OUTPUT
    assert dev.read_outputs()['X207812'] == 7.892
    assert fake.sent.count('getOutputNames?') == 2


def test_non_numeric_value_refused():
    dev, _ = make_device({'getOutputNames?': b'In 1, In 2\r\n',
                          'getOutput?': b'7.892, Error\r\n'})
    with pytest.raises(DeviceError, match='Non-numeric'):
        dev.read_outputs()


def test_no_reply_refused():
    dev, _ = make_device({'getOutputNames?': NAMES})
    with pytest.raises(DeviceError, match='No response'):
        dev.read_outputs()


def test_late_reply_discarded_before_a_query():
    # The setpoint's reply came after its query had timed out: the next
    # query must not take it for its own
    dev, fake = make_device({'HEATER1.PID.Setpoint?': b'',
                             'X233660?': b'7.765\r\n'})
    with pytest.raises(DeviceError, match='No response'):
        dev.read_pid_setpoint('HEATER1')
    fake.incoming += b'284.000\r\n'
    assert dev.read_temperature('X233660') == 7.765


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
