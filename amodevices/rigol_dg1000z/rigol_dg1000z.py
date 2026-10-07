# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Device driver for the Rigol DG1000Z series of two-channel function and
arbitrary waveform generators (DG1022Z, DG1032Z, DG1062Z), controlled
through VISA: pulse waveforms played as externally triggered bursts (one
pulse per trigger edge), their levels, edges and widths, the burst idle
level and the outputs.

The SCPI commands are those of the DG1000Z Programming Guide. The
behavior noted in the README (one command per message, '*OPC?' and
armed bursts, the pulse width hold, bursts and triggers) was observed
with a DG1062Z, firmware 03.01.12.
"""

import logging
import time

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

#: The burst idle levels: the waveform's first point, its maximum, its
#: center, its minimum
IDLE_LEVELS = ('FPT', 'TOP', 'CENTER', 'BOTTOM')
#: After a setting the generator takes a while to put it on the output,
#: and a further message in that time can drop it there while it still
#: reads back (see the README): the wait (s) after a setting before the
#: next message
SETTLE_S = 0.1


class RigolDG1000Z(dev_generic.Device):
    """Device driver for a Rigol DG1000Z series generator.

    Every setting is sent as a message of its own and confirmed by
    querying it back (`set`): two commands in one message hung the
    generator, and '*OPC?' does not answer while a triggered burst is
    armed. The next message waits until `SETTLE_S` after a setting
    (`set`, `query`), so the setting reaches the output. `check_errors`
    drains the error queue after a group of settings. Messages sent
    with `visa_write` or `visa_query` directly do not wait.
    """

    CHANNELS = (1, 2)
    #: The time of the last setting (`set`)
    _last_setting_t = None
    #: The clock and the sleep of the settle wait (seams for tests)
    _clock = staticmethod(time.monotonic)
    _sleep = staticmethod(time.sleep)

    def __init__(self, device):
        """Open the connection to the generator described by `device`
        (dict: 'Device' a name, 'Address' the VISA resource name,
        optional 'VISABackend' and 'Timeout' (s), see `dev_generic`)."""
        super().__init__(device)
        self.init_visa()
        self.identity = self.visa_query('*IDN?')
        parts = [p.strip() for p in self.identity.split(',')] + [''] * 4
        (self.manufacturer, self.model, self.serial_number,
         self.firmware) = parts[:4]

    def close(self):
        """Close the connection to the generator."""
        if self.visa_resource is not None:
            self.visa_resource.close()
            self.visa_resource = None
        self.device_connected = False

    # ── messages ─────────────────────────────────────────────────────

    def _settle(self):
        """Wait until `SETTLE_S` after the last setting."""
        if self._last_setting_t is not None:
            wait = self._last_setting_t + SETTLE_S - self._clock()
            if wait > 0:
                self._sleep(wait)

    def _write_setting(self, command, readback):
        """Send the setting `command` after the settle wait, then the
        query `readback`; returns its reply."""
        self._settle()
        self.visa_write(command)
        reply = self.visa_query(readback)
        self._last_setting_t = self._clock()
        return reply

    def set(self, command):
        """Send the setting `command` (str, e.g. ':SOUR1:BURS ON') and
        query it back (its header with '?'), so the next message is sent
        only after the generator took this one; and not before `SETTLE_S`
        after the setting before."""
        return self._write_setting(command, command.split(' ')[0] + '?')

    def query(self, command):
        """Send the query `command` (str, e.g. ':SOUR2:FUNC?') and return
        the reply, not before `SETTLE_S` after the last setting."""
        self._settle()
        return self.visa_query(command)

    @property
    def system_error(self):
        """The next entry of the error queue as (code, message); code 0
        when the queue is empty."""
        reply = self.query(':SYST:ERR?')
        code, _, message = reply.partition(',')
        return self.to_int(code), message.strip().strip('"')

    def errors(self):
        """Drain the error queue: the entries as (code, message)."""
        out = []
        for _ in range(32):
            code, message = self.system_error
            if code == 0:
                break
            out.append((code, message))
        return out

    def check_errors(self):
        """Drain the error queue, raising `DeviceError` listing any
        errors."""
        errors = self.errors()
        if errors:
            raise DeviceError(
                f'{self.device["Device"]}: '
                + '; '.join(f'{code}, {message}' for code, message in errors))

    @staticmethod
    def _channel(channel):
        if channel not in RigolDG1000Z.CHANNELS:
            raise DeviceError(f'Channel must be 1 or 2, got {channel!r}')
        return channel

    # ── pulses as triggered bursts ───────────────────────────────────

    def configure_triggered_pulse(
            self, channel, width_s, period_s, low_v, high_v, delay_s=0.,
            slope='POS', idle='BOTTOM', load='INF', leading_s='MIN',
            trailing_s='MIN', output=True):
        """Make `channel` (1 or 2) play one pulse per edge at its
        rear-panel external trigger input: a pulse waveform of
        `period_s` (s) whose pulse lasts `width_s` (s), from `low_v` to
        `high_v` (V), as a burst of one cycle started `delay_s` (s)
        after each `slope` ('POS' or 'NEG') edge, resting at the `idle`
        level (`IDLE_LEVELS`) between bursts; output load `load` ('INF'
        = high impedance, or ohms), edge times `leading_s`/`trailing_s`
        (s, or 'MIN'). The output is switched off while it is set up
        and on at the end with `output`. The pulse WIDTH is held when
        the period changes (`PULS:HOLD WIDT`; a channel otherwise keeps
        holding whichever it held last, and one holding the duty cycle
        changes its width with the period)."""
        n = self._channel(channel)
        if idle not in IDLE_LEVELS:
            raise DeviceError(f'Idle level must be one of {IDLE_LEVELS}')
        self.set(f':OUTP{n} OFF')
        self.set(f':SOUR{n}:BURS OFF')
        self.set(f':OUTP{n}:IMP {load}')
        # Function, frequency, amplitude and offset in one command
        self._write_setting(
            f':SOUR{n}:APPL:PULS {1. / period_s:.9g},'
            f'{high_v - low_v:.6g},{(high_v + low_v) / 2:.6g},0',
            f':SOUR{n}:FUNC?')
        for command in (
                f':SOUR{n}:PULS:HOLD WIDT',
                f':SOUR{n}:FUNC:PULS:WIDT {width_s:.9g}',
                f':SOUR{n}:PULS:TRAN:LEAD {self._edge(leading_s)}',
                f':SOUR{n}:PULS:TRAN:TRA {self._edge(trailing_s)}',
                f':SOUR{n}:BURS:MODE TRIG',
                f':SOUR{n}:BURS:NCYC 1',
                f':SOUR{n}:BURS:TRIG:SOUR EXT',
                f':SOUR{n}:BURS:TRIG:SLOP {slope}',
                f':SOUR{n}:BURS:TDEL {delay_s:.9g}',
                f':SOUR{n}:BURS:IDLE {idle}',
                f':SOUR{n}:BURS ON'):
            self.set(command)
        if output:
            self.set(f':OUTP{n} ON')

    @staticmethod
    def _edge(value):
        return value if isinstance(value, str) else f'{value:.9g}'

    def pulse_width(self, channel):
        """The pulse width of `channel` (s)."""
        n = self._channel(channel)
        return self.to_float(self.query(f':SOUR{n}:FUNC:PULS:WIDT?'))

    def set_pulse_width(self, channel, width_s, period_s):
        """Set the pulse width (s) and the waveform period (s) of
        `channel`, in the order that keeps the pulse inside the period
        throughout: the period first when the pulse grows, the width
        first when it shrinks."""
        n = self._channel(channel)
        steps = ((f':SOUR{n}:PER {period_s:.9g}',
                  f':SOUR{n}:FUNC:PULS:WIDT {width_s:.9g}')
                 if width_s > self.pulse_width(n) else
                 (f':SOUR{n}:FUNC:PULS:WIDT {width_s:.9g}',
                  f':SOUR{n}:PER {period_s:.9g}'))
        for command in steps:
            self.set(command)

    def levels(self, channel):
        """The (low, high) levels of `channel` (V)."""
        n = self._channel(channel)
        return (self.to_float(self.query(f':SOUR{n}:VOLT:LOW?')),
                self.to_float(self.query(f':SOUR{n}:VOLT:HIGH?')))

    def function(self, channel):
        """The waveform `channel` plays (str, e.g. 'PULSE', 'SIN')."""
        n = self._channel(channel)
        return self.query(f':SOUR{n}:FUNC?').strip().upper()

    def set_levels(self, channel, low_v, high_v):
        """Set the low and high levels of `channel` (V), in the order
        that never puts the low level above the high one: the high level
        first when the levels rise above the present high level."""
        n = self._channel(channel)
        if not low_v < high_v:
            raise DeviceError('The low level must lie below the high level')
        _low, high = self.levels(n)
        steps = ((f':SOUR{n}:VOLT:HIGH {high_v:.6g}',
                  f':SOUR{n}:VOLT:LOW {low_v:.6g}')
                 if low_v >= high else
                 (f':SOUR{n}:VOLT:LOW {low_v:.6g}',
                  f':SOUR{n}:VOLT:HIGH {high_v:.6g}'))
        for command in steps:
            self.set(command)

    def set_burst_idle(self, channel, idle):
        """Set the level `channel` rests at between bursts
        (`IDLE_LEVELS`; 'TOP' holds the pulse's high level, 'BOTTOM' its
        low level)."""
        n = self._channel(channel)
        if idle not in IDLE_LEVELS:
            raise DeviceError(f'Idle level must be one of {IDLE_LEVELS}')
        self.set(f':SOUR{n}:BURS:IDLE {idle}')

    def set_output(self, channel, on):
        """Switch the output of `channel` on or off."""
        n = self._channel(channel)
        self.set(f':OUTP{n} {"ON" if on else "OFF"}')
