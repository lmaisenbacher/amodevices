# -*- coding: utf-8 -*-
"""Tests of the Rigol DG1000Z driver against a fake VISA transport: the
identity, the triggered-pulse configuration, the ordering of width and
level changes, and the error queue. No generator needed. Runs under
pytest.
"""

import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.rigol_dg1000z.rigol_dg1000z import SETTLE_S, RigolDG1000Z


class FakeDG1000Z(RigolDG1000Z):
    """The driver on a scripted transport: `writes` logs every command,
    a query answers the last value written for its header (`values`),
    the error queue pops `errors`."""

    def __init__(self, errors=()):
        self.writes = []
        self.queries = []
        self.values = {':SOUR1:FUNC:PULS:WIDT': '1.000000E-02',
                       ':SOUR2:FUNC:PULS:WIDT': '1.000000E-02',
                       ':SOUR2:VOLT:LOW': '2.000000E+00',
                       ':SOUR2:VOLT:HIGH': '5.000000E+00'}
        self._errors = list(errors)
        # A clock that only the channel wait's sleeps (and the test) move
        self.now, self.slept = 0., []
        self._clock = lambda: self.now
        self._sleep = self._fake_sleep
        super().__init__({'Device': 'Fake DG1062Z', 'Address': 'FAKE::INSTR'})

    def _fake_sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def init_visa(self):
        self.device_present = True
        self.device_connected = True

    def visa_write(self, cmd):
        self.writes.append(cmd)
        head, _, value = cmd.partition(' ')
        self.values[head] = value

    def visa_query(self, query, return_ascii=False):
        self.queries.append(query)
        if query == '*IDN?':
            return 'Rigol Technologies,DG1062Z,DG1ZA000000000,03.01.12'
        if query == ':SYST:ERR?':
            return self._errors.pop(0) if self._errors else '0,"No error"'
        return self.values.get(query[:-1], '1')


def test_identity():
    gen = FakeDG1000Z()
    assert (gen.manufacturer, gen.model, gen.serial_number, gen.firmware) == (
        'Rigol Technologies', 'DG1062Z', 'DG1ZA000000000', '03.01.12')


def test_triggered_pulse():
    gen = FakeDG1000Z()
    gen.configure_triggered_pulse(2, 10e-3, 10.5e-3, 2., 5.)
    w = gen.writes
    # One command per message, each queried back; never '*OPC?'
    assert all(';' not in c for c in w)
    assert '*OPC?' not in gen.queries
    assert w[0] == ':OUTP2 OFF' and w[-1] == ':OUTP2 ON'
    assert f':SOUR2:APPL:PULS {1 / 10.5e-3:.9g},3,3.5,0' in w
    # The width held when the period changes, set before the width
    assert w.index(':SOUR2:PULS:HOLD WIDT') < w.index(':SOUR2:FUNC:PULS:WIDT 0.01')
    for cmd in (':OUTP2:IMP INF', ':SOUR2:PULS:TRAN:LEAD MIN',
                ':SOUR2:PULS:TRAN:TRA MIN', ':SOUR2:BURS:MODE TRIG',
                ':SOUR2:BURS:NCYC 1', ':SOUR2:BURS:TRIG:SOUR EXT',
                ':SOUR2:BURS:TRIG:SLOP POS', ':SOUR2:BURS:TDEL 0',
                ':SOUR2:BURS:IDLE BOTTOM'):
        assert cmd in w, cmd
    # The burst switched on once it is set up
    assert w.index(':SOUR2:BURS ON') > w.index(':SOUR2:BURS:TRIG:SOUR EXT')
    gen.writes.clear()
    gen.configure_triggered_pulse(1, 1e-3, 2e-3, 0., 3., delay_s=1e-6,
                                  idle='TOP', leading_s=1e-6, output=False)
    assert ':SOUR1:BURS:IDLE TOP' in gen.writes
    assert ':SOUR1:BURS:TDEL 1e-06' in gen.writes
    assert ':SOUR1:PULS:TRAN:LEAD 1e-06' in gen.writes
    assert ':OUTP1 ON' not in gen.writes
    with pytest.raises(DeviceError):
        gen.configure_triggered_pulse(3, 1e-3, 2e-3, 0., 3.)
    with pytest.raises(DeviceError):
        gen.configure_triggered_pulse(1, 1e-3, 2e-3, 0., 3., idle='HIGH')


def test_pulse_width_stays_inside_the_period():
    gen = FakeDG1000Z()
    gen.set_pulse_width(1, 20e-3, 20.5e-3)        # grows: the period first
    assert gen.writes == [f':SOUR1:PER {20.5e-3:.9g}', ':SOUR1:FUNC:PULS:WIDT 0.02']
    gen.writes.clear()
    gen.set_pulse_width(1, 5e-3, 5.5e-3)          # shrinks: the width first
    assert gen.writes == [':SOUR1:FUNC:PULS:WIDT 0.005', f':SOUR1:PER {5.5e-3:.9g}']


def test_levels_never_cross():
    gen = FakeDG1000Z()                           # CH2 at 2 V / 5 V
    gen.set_levels(2, 6., 8.)                     # above the high: high first
    assert gen.writes == [':SOUR2:VOLT:HIGH 8', ':SOUR2:VOLT:LOW 6']
    gen.writes.clear()
    gen.set_levels(2, -1., 0.5)                   # below: low first
    assert gen.writes == [':SOUR2:VOLT:LOW -1', ':SOUR2:VOLT:HIGH 0.5']
    with pytest.raises(DeviceError):
        gen.set_levels(2, 1., 1.)


def test_idle_and_output():
    gen = FakeDG1000Z()
    gen.set_burst_idle(1, 'TOP')
    gen.set_output(2, False)
    assert gen.writes == [':SOUR1:BURS:IDLE TOP', ':OUTP2 OFF']
    assert gen.queries[-2:] == [':SOUR1:BURS:IDLE?', ':OUTP2?']


def test_the_next_message_waits_after_a_setting():
    # A setting reaches the output only a while after its read-back, and
    # a further message in that time can drop it there
    gen = FakeDG1000Z()
    gen.check_errors()                            # nothing set yet
    gen.set(':SOUR1:BURS:IDLE TOP')
    assert gen.slept == []
    gen.now += 0.03
    gen.set(':SOUR1:BURS ON')                     # waits out the rest
    assert gen.slept == [pytest.approx(SETTLE_S - 0.03)]
    gen.levels(2)                                 # a query waits too
    assert gen.slept[-1] == pytest.approx(SETTLE_S)
    n = len(gen.slept)
    gen.levels(2)                                 # no setting since
    gen.now += 1.
    gen.set(':OUTP2 ON')                          # long after: no wait
    assert len(gen.slept) == n
    # The read-back itself follows its command at once
    assert gen.queries[-1] == ':OUTP2?'
    gen.now += 1.
    n = len(gen.slept)
    gen.configure_triggered_pulse(1, 10e-3, 10.5e-3, 0., 3.)
    # Each of its 16 settings but the first waited the full time
    assert gen.slept[n:] == [pytest.approx(SETTLE_S)] * 15
    assert gen.function(2) == '1'


def test_error_queue():
    gen = FakeDG1000Z(errors=['-222,"Data out of range"',
                              '-221,"Settings conflict"'])
    with pytest.raises(DeviceError, match='Data out of range.*Settings conflict'):
        gen.check_errors()
    gen.check_errors()                            # drained
    assert gen.system_error == (0, 'No error')
