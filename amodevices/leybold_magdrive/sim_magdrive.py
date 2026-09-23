# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

A simulated MAG.DRIVE S/iS behind a fake serial port: it answers the USS
telegrams of `leybold_magdrive` the way the manual (17200308_002_C0)
describes, with a rotor that runs up and down at a finite rate. For tests
and for running a server without the pump:

    sim = SimulatedMagDrive()
    drive = LeyboldMagDrive(device_config, serial_factory=lambda d: sim)

One simulator object keeps the pump's state across reconnects, as the
hardware does. It records the control word of every request it answers
(`control_words`), so a test can check what the drive was told.
"""

import time

import serial

from .leybold_magdrive import (
    ACCESS_NONE, ACCESS_READ, ACCESS_READ_ARRAY, CONTROL_REMOTE,
    CONTROL_RESET, CONTROL_STANDBY, CONTROL_START, CONVERTER_TYPE, LGE,
    OPERATING_NORMAL, OPERATING_READY, OPERATING_SPEED_DROPPING,
    OPERATING_SPEED_INCREASING, OPERATING_STANDBY, PARAMETERS,
    REPLY_ERROR, REPLY_NONE, REPLY_VALUE_16, REPLY_VALUE_32,
    STATUS_ACCELERATING, STATUS_DECELERATING, STATUS_FAILURE,
    STATUS_NORMAL_OPERATION, STATUS_OPERATION_ENABLED,
    STATUS_PARAMETER_CHANNEL, STATUS_READY, STATUS_REMOTE, STATUS_ROTATING,
    STATUS_SWITCH_ON_LOCK, STX, TELEGRAM_LENGTH, checksum)

_KINDS = {p.number: p.kind for p in PARAMETERS.values()}


class SimulatedMagDrive:
    """A MAG.DRIVE S/iS with a TURBOVAC MAG W 600 iP (800 Hz rated), on a
    fake serial port.

    `switch_on` is the start switch on the X1 connector, which the drive
    follows while no telegram carries control bit 10. `ramp_hz_per_s`
    is the run-up and run-down rate (the real pump takes about 6 min to
    800 Hz). `watchdog_s` is what parameter 182 reports.
    """

    def __init__(self, switch_on=True, nominal_hz=800., standby_hz=560.,
                 ramp_hz_per_s=800. / 360., watchdog_s=0.,
                 converter_type=CONVERTER_TYPE, frequency_hz=None,
                 address=0, clock=time.monotonic):
        self.switch_on = switch_on
        self.nominal_hz = nominal_hz
        self.standby_hz = standby_hz
        self.ramp_hz_per_s = ramp_hz_per_s
        self.watchdog_s = watchdog_s
        self.converter_type = converter_type
        self.address = address
        self.clock = clock
        # Running from the start when the switch says so (a pump found
        # at speed), unless told otherwise
        if frequency_hz is None:
            frequency_hz = nominal_hz if switch_on else 0.
        self.frequency_hz = float(frequency_hz)
        self.failure = 0
        self.start_cycles = 42
        self.remote = False
        self.run = switch_on
        self.standby = False
        self._reset_prev = False
        self._last_t = clock()
        #: False: no reply at all (a cable pulled, the drive off)
        self.responding = True
        #: True: every port operation raises (a USB adapter unplugged)
        self.port_broken = False
        #: The control word of every request answered, in order
        self.control_words = []
        self._out = b''
        self.closed = False

    # -- the serial port -----------------------------------------------

    def reset_input_buffer(self):
        self._check_port()
        self._out = b''

    def write(self, data):
        self._check_port()
        self._out = self._respond(bytes(data))
        return len(data)

    def read(self, size=1):
        self._check_port()
        out, self._out = self._out[:size], self._out[size:]
        return out

    def close(self):
        self.closed = True

    def _check_port(self):
        if self.port_broken:
            raise serial.SerialException('simulated port failure')

    # -- test handles --------------------------------------------------

    def inject_failure(self, code):
        """A failure: the drive stops the pump until it is reset."""
        self.failure = int(code)

    # -- the drive -----------------------------------------------------

    def _running(self):
        return self.run and not self.failure

    def _target_hz(self):
        if not self._running():
            return 0.
        return self.standby_hz if self.standby else self.nominal_hz

    def _advance(self):
        now = self.clock()
        dt, self._last_t = now - self._last_t, now
        target = self._target_hz()
        step = self.ramp_hz_per_s * dt
        if self.frequency_hz < target:
            self.frequency_hz = min(target, self.frequency_hz + step)
        else:
            self.frequency_hz = max(target, self.frequency_hz - step)

    def _apply_control(self, control):
        was_running = self._running()
        self.remote = bool(control & CONTROL_REMOTE)
        if self.remote:
            self.run = bool(control & CONTROL_START)
            self.standby = bool(control & CONTROL_STANDBY)
            reset = bool(control & CONTROL_RESET)
            # Only the 0 -> 1 transition resets, and not while the run
            # command is on (section 4.3)
            if reset and not self._reset_prev and not self.run:
                self.failure = 0
            self._reset_prev = reset
        else:
            self.run = self.switch_on
            self.standby = False
            self._reset_prev = False
        if self._running() and not was_running:
            self.start_cycles += 1

    def _status_word(self):
        enabled = self._running()
        target = self._target_hz()
        f = self.frequency_hz
        word = STATUS_PARAMETER_CHANNEL
        if not self.failure:
            word |= STATUS_READY
        else:
            word |= STATUS_FAILURE
        if enabled:
            word |= STATUS_OPERATION_ENABLED
            if f < target - 1:
                word |= STATUS_ACCELERATING
            if f >= 0.9 * target:
                word |= STATUS_NORMAL_OPERATION
        else:
            word |= STATUS_SWITCH_ON_LOCK
        if f > target + 1:
            word |= STATUS_DECELERATING
        if f > 3:
            word |= STATUS_ROTATING
        if self.remote:
            word |= STATUS_REMOTE
        return word

    def _operating_status(self):
        status = self._status_word()
        bits = 0
        if status & STATUS_NORMAL_OPERATION:
            bits |= OPERATING_NORMAL
        if not self._running() and not self.failure:
            bits |= OPERATING_READY
        if status & STATUS_ACCELERATING:
            bits |= OPERATING_SPEED_INCREASING
        if status & STATUS_DECELERATING:
            bits |= OPERATING_SPEED_DROPPING
        if self.standby and self._running():
            bits |= OPERATING_STANDBY
        return bits

    def _motor_current_raw(self):
        """0.1 A: run-up draws more than holding speed."""
        status = self._status_word()
        if status & STATUS_ACCELERATING:
            return 20
        if self._running():
            return 5
        return 0

    def _parameter(self, number):
        current = self._motor_current_raw()
        values = {
            1: self.converter_type,
            2: 8015500,
            3: int(round(self.frequency_hz)),
            4: 480,
            5: current,
            6: current * 48,
            7: 40,
            11: 35,
            38: self.start_cycles,
            40: 3,
            44: 1234567,
            105: 7,
            106: 1234,
            125: 38,
            182: int(round(self.watchdog_s * 10)),
            184: 12345,
            227: 0,
            228: 0,
            230: 0,
            232: 0,
            303: self._operating_status(),
            390: 28,
            860: 0,
            947: self.failure,
        }
        return values.get(number)

    def _respond(self, request):
        if (len(request) != TELEGRAM_LENGTH or request[0] != STX
                or request[1] != LGE or request[2] != self.address
                or checksum(request[:23]) != request[23]):
            return b''          # the drive discards what it cannot parse
        if not self.responding:
            return b''
        pke = int.from_bytes(request[3:5], 'big')
        access, number = pke >> 12, pke & 0x7FF
        control = int.from_bytes(request[11:13], 'big')
        self.control_words.append(control)
        self._advance()
        self._apply_control(control)
        reply_code, value = REPLY_NONE, 0
        if access in (ACCESS_READ, ACCESS_READ_ARRAY):
            value = self._parameter(number)
            if value is None:
                reply_code, value = REPLY_ERROR, 0
            elif _KINDS.get(number, 'u16') in ('i32', 'u32'):
                reply_code = REPLY_VALUE_32
            else:
                reply_code = REPLY_VALUE_16
        elif access != ACCESS_NONE:
            reply_code, value = REPLY_ERROR, 101        # order unknown
            number = 0
        return self._reply(reply_code, number, request[6], value)

    def _reply(self, reply_code, number, index, value):
        data = bytearray(TELEGRAM_LENGTH)
        data[0] = STX
        data[1] = LGE
        data[2] = self.address
        data[3:5] = ((reply_code << 12) | number).to_bytes(2, 'big')
        data[6] = index
        data[7:11] = (value & 0xFFFFFFFF).to_bytes(4, 'big')
        words = (self._status_word(), int(round(self.frequency_hz)), 35,
                 self._motor_current_raw(), 30, 480)
        for i, word in enumerate(words):
            data[11 + 2 * i:13 + 2 * i] = word.to_bytes(2, 'big')
        data[23] = checksum(data[:23])
        return bytes(data)
