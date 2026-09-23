# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Driver for the Leybold MAG.DRIVE S/iS frequency converter of the TURBOVAC
MAG W P and MAG W iP turbomolecular pumps (converter type 201), through its
RS-232 service interface.

The interface speaks Leybold's USS telegram ('Serial Interfaces for
MAG.DRIVE S/iS', 17200308_002_C0, sections 3 and 4): 24 bytes at 19200
baud, 8 data bits, even parity, one stop bit, all multi-byte fields
big-endian:

    byte  0      STX, 0x02
    byte  1      LGE, 22
    byte  2      ADR, the drive's address (0 on RS-232)
    bytes 3-4    PKE: the access code in bits 15-12, the parameter number
                 in the bits below
    byte  5      reserved, 0
    byte  6      IND, the index of an array parameter
    bytes 7-10   PWE, the parameter value
    bytes 11-12  PZD1: the control word in a request, the status word in
                 a reply
    bytes 13-22  PZD2-PZD6: in a request the speed setpoint and zeros; in
                 a reply the stator frequency (Hz), the converter
                 temperature (degC), the motor current (0.1 A), the pump
                 temperature (degC) and the intermediate-circuit voltage
                 (0.1 V)
    byte  23     BCC, the XOR of bytes 0-22

Every reply carries the status word and the five process values, whatever
the parameter access, so a telegram without any parameter access
(`poll`) is the cheapest status read.

The control word travels in EVERY telegram, parameter reads included.
With bit 10 clear the drive follows its digital inputs (the X1 connector)
and ignores every other control bit; with bit 10 set this interface
controls the drive, the inputs are ignored, and bit 0 is the run command -
a level, not an edge, so it has to be sent in every telegram. The driver
therefore holds ONE control word (`control_word`) and sends it in every
exchange, so no read can carry a different one. Set it before `connect`,
whose identification telegrams already carry it.

The parameter numbers, types and scales (`PARAMETERS`) and the failure
and warning texts are those of converter type 201 in Leybold's parameter
database, cross-checked against section 5 of the manual.
"""

import logging
import threading
from collections import namedtuple
from dataclasses import dataclass

import serial

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

STX = 0x02
LGE = 22
TELEGRAM_LENGTH = 24
#: The converter type (parameter 1) of the MAG.DRIVE S/iS
CONVERTER_TYPE = 201

# Query designators, PKE bits 15-12 (section 4.1)
ACCESS_NONE = 0
ACCESS_READ = 1
ACCESS_READ_ARRAY = 6

# Reply designators
REPLY_NONE = 0
REPLY_VALUE_16 = 1
REPLY_VALUE_32 = 2
REPLY_ARRAY_16 = 4
REPLY_ARRAY_32 = 5
REPLY_ERROR = 7
REPLY_NO_PERMISSION = 8
REPLY_VALUES = (REPLY_VALUE_16, REPLY_VALUE_32, REPLY_ARRAY_16, REPLY_ARRAY_32)

#: What a reply with designator 7 carries in PWE (section 4.1)
REPLY_ERRORS = {
    0: 'impermissible parameter number',
    1: 'parameter cannot be changed',
    2: 'min./max. restriction',
    3: 'wrong index value',
    4: 'no array',
    5: 'wrong data type',
    6: 'setting is not allowed',
    7: 'element was not changed',
    101: 'order unknown',
    104: 'password level too low',
}

# Control word, PZD1 of a request (section 4.3)
CONTROL_START = 1 << 0
CONTROL_RESET = 1 << 7
CONTROL_STANDBY = 1 << 8
CONTROL_REMOTE = 1 << 10
#: The bits a caller may set. Left out: the speed setpoint (bit 6), purge
#: gas and venting (bits 11 and 12, "for future applications") and venting
#: by operating status (bit 15, which needs parameter 134 = 21)
CONTROL_BITS = CONTROL_START | CONTROL_RESET | CONTROL_STANDBY | CONTROL_REMOTE

# Status word, PZD1 of a reply (section 4.4)
STATUS_READY = 1 << 0
STATUS_OPERATION_ENABLED = 1 << 2
STATUS_FAILURE = 1 << 3
STATUS_ACCELERATING = 1 << 4
STATUS_DECELERATING = 1 << 5
STATUS_SWITCH_ON_LOCK = 1 << 6
STATUS_TEMPERATURE_WARNING = 1 << 7
STATUS_PARAMETER_CHANNEL = 1 << 9
STATUS_NORMAL_OPERATION = 1 << 10
STATUS_ROTATING = 1 << 11
STATUS_FAILURE_COUNTER = 1 << 12
STATUS_OVERLOAD_WARNING = 1 << 13
STATUS_REMOTE = 1 << 15

# Actual operating status, parameter 303
OPERATING_NORMAL = 1 << 0
OPERATING_READY = 1 << 1
OPERATING_SPEED_INCREASING = 1 << 2
OPERATING_SPEED_DROPPING = 1 << 3
OPERATING_GENERATOR = 1 << 4
OPERATING_STANDBY = 1 << 5

#: A parameter the driver reads: number, word type ('u16', 'i16', 'u32',
#: 'i32') and the scale from the raw value to the unit in the name
Parameter = namedtuple('Parameter', 'number kind scale')

PARAMETERS = {
    'converter_type': Parameter(1, 'u16', 1),
    'software_version': Parameter(2, 'i32', 1),
    'frequency_hz': Parameter(3, 'u16', 1),
    'dc_link_voltage_v': Parameter(4, 'u16', 0.1),
    'motor_current_a': Parameter(5, 'u16', 0.1),
    'motor_power_w': Parameter(6, 'u16', 0.1),
    'motor_temperature_c': Parameter(7, 'u16', 1),
    'converter_temperature_c': Parameter(11, 'u16', 1),
    'start_cycles': Parameter(38, 'u16', 1),
    'error_count': Parameter(40, 'u16', 1),
    'pump_operating_hours_h': Parameter(44, 'i32', 0.01),
    'touchdowns': Parameter(105, 'u16', 1),
    'touchdown_time_s': Parameter(106, 'i32', 0.01),
    'bearing_temperature_c': Parameter(125, 'u16', 1),
    'uss_watchdog_s': Parameter(182, 'u16', 0.1),
    'converter_operating_hours_h': Parameter(184, 'u16', 1),
    'warning_bits_1': Parameter(227, 'u16', 1),
    'warning_bits_2': Parameter(228, 'u16', 1),
    'warning_bits_3': Parameter(230, 'u16', 1),
    'warning_bits_4': Parameter(232, 'u16', 1),
    'operating_status': Parameter(303, 'i32', 1),
    'cooler_temperature_c': Parameter(390, 'u16', 1),
    'control_selector': Parameter(860, 'u16', 1),
    'actual_failure': Parameter(947, 'u16', 1),
}

#: Parameter 947, the failure pending (0 = none)
FAILURES = {
    0: 'no error pending',
    2: 'pump motor temperature too high',
    3: 'supply voltage failure',
    4: 'converter temperature failure',
    6: 'overload failure',
    7: 'acceleration time',
    9: 'bearing temperature too high',
    12: 'radial bearing unbalance at the upper magnetic bearing',
    13: 'radial bearing unbalance at the lower magnetic bearing',
    14: 'axial bearing unbalance',
    16: 'overload duration',
    17: 'pump motor current failure',
    19: 'starting time exceeded',
    26: 'bearing temperature sensor defective',
    28: 'motor temperature sensor defective',
    31: 'high-load duration',
    39: 'magnetic bearing start-up failure',
    43: 'overspeed',
    63: 'internal parameter failure',
    65: 'cyclic pump communication failed',
    66: 'current load of the magnetic bearings too high',
    67: 'internal overload',
    71: 'first-time initialization failure',
    73: 'operating cycles',
    74: 'operating hours',
    75: 'pump initialization failure',
    77: 'number of bearing touchdowns',
    78: 'bearing touchdown time',
    79: 'internal communication failure',
    80: 'invalid interface module combination',
    81: 'RS232/RS485 communication interruption (USS watchdog)',
    82: 'fieldbus communication interruption',
    90: 'pump speed adjustment failure',
    91: 'pump cable length failure',
    92: 'external pump controller with a cable length of 0 m',
    93: 'cable parameter faulty',
    201: 'unidentifiable failure in the control board',
    203: 'failure during self-test',
    204: 'RAM insufficient for the scope function',
    206: 'pump parameter failure',
    209: 'pump initialization failure',
    213: 'supply voltage too high',
}

#: The named bits of warning words 1-4 (parameters 227, 228, 230, 232);
#: warning word 5 (parameter 233) has none
WARNING_BITS = {
    'warning_bits_1': {
        0: 'motor temperature too high',
        1: 'converter housing temperature too high',
        2: 'bearing temperature too high',
        6: 'overspeed',
        10: 'unbalance at the upper bearing',
        11: 'unbalance at the lower bearing',
        12: 'oscillation at the axial bearing',
    },
    'warning_bits_2': {
        11: 'magnetic bearing has not lifted',
        12: 'magnetic bearing overload (level 1)',
        13: 'converter power stage temperature too high',
    },
    'warning_bits_3': {
        4: 'magnetic bearing overload (level 2)',
        5: 'maximum number of run-up cycles reached',
        6: 'maximum number of operating hours reached',
        8: 'high load',
        9: 'magnetic bearing overload, Z axis',
        11: 'overload',
        12: 'radial bearing displacement',
        14: 'supply voltage too high or too low',
        15: 'motor start locked',
    },
    'warning_bits_4': {
        0: 'magnetic bearing overload 0',
        1: 'magnetic bearing overload 1',
        2: 'magnetic bearing overload 2',
        3: 'magnetic bearing overload 3',
        4: 'magnetic bearing overload 4, Z axis',
        5: 'magnetic bearing overload 5',
        6: 'magnetic bearing overload 6',
        8: 'upper radial bearing displacement X1',
        9: 'upper radial bearing displacement Y1',
        10: 'lower radial bearing displacement X2',
        11: 'lower radial bearing displacement Y2',
        12: 'axial bearing displacement Z',
        13: 'high number of auxiliary bearing impacts',
        14: 'high accumulated touchdown time',
        15: 'high number of touchdown run-downs',
    },
}

#: Parameter 860, the interface that controls the drive
CONTROL_SELECTORS = {
    0: 'AUTO',
    1: 'X1 module (control slot)',
    2: 'USS, control interface',
    3: 'USS, service interface',
    4: 'fieldbus, control interface',
    5: 'fieldbus, service interface',
    6: 'CAN bus option plug',
    7: 'parameter interface',
}


def failure_text(code):
    """'<code>: <text>' for a failure code of parameter 947."""
    return f'{code}: {FAILURES.get(code, "unknown failure")}'


def warning_texts(name, bits):
    """The named warnings set in warning word `name` (a key of
    `WARNING_BITS`), and 'bit <n>' for a set bit without a name."""
    table = WARNING_BITS[name]
    return [table.get(bit, f'bit {bit}')
            for bit in range(16) if bits & (1 << bit)]


def checksum(data):
    """BCC: the XOR of the bytes of `data`."""
    bcc = 0
    for byte in data:
        bcc ^= byte
    return bcc


def decode_value(raw, kind):
    """The parameter value PWE (`raw`, 32 bits) as the word type `kind`."""
    if kind == 'u16':
        return raw & 0xFFFF
    if kind == 'i16':
        value = raw & 0xFFFF
        return value - 0x10000 if value & 0x8000 else value
    if kind == 'u32':
        return raw & 0xFFFFFFFF
    if kind == 'i32':
        value = raw & 0xFFFFFFFF
        return value - (1 << 32) if value & 0x80000000 else value
    raise ValueError(f'Unknown word type {kind!r}')


def _signed16(value):
    return value - 0x10000 if value & 0x8000 else value


def build_telegram(access, parameter=0, index=0, value=0, control=0,
                   setpoint=0, address=0):
    """A 24-byte request telegram."""
    if not 0 <= parameter < 2048:
        raise ValueError(f'Parameter number {parameter} out of range')
    data = bytearray(TELEGRAM_LENGTH)
    data[0] = STX
    data[1] = LGE
    data[2] = address
    data[3:5] = ((access << 12) | parameter).to_bytes(2, 'big')
    data[6] = index
    data[7:11] = (value & 0xFFFFFFFF).to_bytes(4, 'big')
    data[11:13] = control.to_bytes(2, 'big')
    data[13:15] = setpoint.to_bytes(2, 'big')
    data[23] = checksum(data[:23])
    return bytes(data)


@dataclass(frozen=True)
class Reply:
    """A reply telegram, decoded."""
    reply_code: int
    parameter: int
    index: int
    #: PWE as an unsigned 32-bit integer
    value: int
    status_word: int
    frequency_hz: int
    converter_temperature_c: int
    motor_current_a: float
    pump_temperature_c: int
    dc_link_voltage_v: float


def parse_reply(data):
    """Decode a 24-byte reply telegram; `DeviceError` when it is not one."""
    if len(data) != TELEGRAM_LENGTH:
        raise DeviceError(f'Reply of {len(data)} bytes, not {TELEGRAM_LENGTH}')
    if data[0] != STX or data[1] != LGE:
        raise DeviceError(
            f'Reply starts with 0x{data[0]:02X} 0x{data[1]:02X}, not STX and LGE')
    if checksum(data[:23]) != data[23]:
        raise DeviceError('Reply checksum mismatch')
    pke = int.from_bytes(data[3:5], 'big')
    words = [int.from_bytes(data[i:i + 2], 'big') for i in range(11, 23, 2)]
    return Reply(
        reply_code=pke >> 12,
        parameter=pke & 0x7FF,
        index=data[6],
        value=int.from_bytes(data[7:11], 'big'),
        status_word=words[0],
        frequency_hz=words[1],
        converter_temperature_c=_signed16(words[2]),
        motor_current_a=words[3] * 0.1,
        pump_temperature_c=_signed16(words[4]),
        dc_link_voltage_v=words[5] * 0.1,
        )


class LeyboldMagDrive(dev_generic.Device):
    """Leybold MAG.DRIVE S/iS on its RS-232 service interface.

    Configuration keys beyond 'Device' and 'Address' (the serial port):
    'Timeout' (s, per reply, default 0.5), 'USSAddress' (default 0),
    'Retries' (repeats of a telegram whose reply is missing or garbled,
    default 2), 'ExpectedConverterType' (default 201; `connect` refuses
    another drive; None skips the check) and 'SerialConnectionParams'
    (merged over 19200 baud, 8E1).

    `serial_factory(device)`, when given, supplies the port object instead
    of pyserial (a simulated drive, see `sim_magdrive`).
    """

    def __init__(self, device, serial_factory=None):
        device = {
            'Timeout': 0.5,
            'USSAddress': 0,
            'Retries': 2,
            'ExpectedConverterType': CONVERTER_TYPE,
            **device,
            }
        device['SerialConnectionParams'] = {
            'baudrate': 19200,
            'bytesize': serial.EIGHTBITS,
            'parity': serial.PARITY_EVEN,
            'stopbits': serial.STOPBITS_ONE,
            **device.get('SerialConnectionParams', {}),
            }
        super().__init__(device)
        self._serial_factory = serial_factory
        self._control_word = 0
        # One exchange at a time: a request and its reply belong together
        self._io_lock = threading.Lock()
        self.converter_type = None
        self.software_version = None

    @property
    def name(self):
        return self.device['Device']

    @property
    def control_word(self):
        """The control word every telegram carries (see the module
        docstring)."""
        return self._control_word

    @control_word.setter
    def control_word(self, value):
        value = int(value)
        if value & ~CONTROL_BITS:
            raise DeviceError(
                f'{self.name}: control word 0x{value:04X} sets bits this'
                ' driver does not send')
        if value and not value & CONTROL_REMOTE:
            raise DeviceError(
                f'{self.name}: control word 0x{value:04X} lacks bit 10,'
                ' so the drive would ignore it')
        self._control_word = value

    def connect(self):
        """Open the port and identify the drive (parameters 1 and 2). The
        identification telegrams carry `control_word`."""
        if self._serial_factory is not None:
            self.serial_close()
            self.ser = self._serial_factory(self.device)
            self.device_present = True
            self.device_connected = True
        else:
            self.serial_connect()
        try:
            self.converter_type = self.read('converter_type')
            expected = self.device.get('ExpectedConverterType')
            if expected is not None and self.converter_type != expected:
                raise DeviceError(
                    f'{self.name}: converter type {self.converter_type}, not'
                    f' {expected} (MAG.DRIVE S/iS) whose parameters this'
                    ' driver knows')
            self.software_version = self.read('software_version')
        except DeviceError:
            self.close()
            raise
        logger.info('%s: MAG.DRIVE S/iS, converter type %d, software %d',
                    self.name, self.converter_type, self.software_version)

    def close(self):
        """Close the port. Sends nothing: the drive keeps the last control
        word it received."""
        self.serial_close()
        self.ser = None

    def exchange(self, access=ACCESS_NONE, parameter=0, index=0):
        """One request, carrying `control_word`, and its reply. A missing,
        garbled or mismatched reply is retried 'Retries' times, then
        raises `DeviceError`."""
        if self.ser is None:
            raise DeviceError(f'{self.name}: Not connected')
        request = build_telegram(
            access, parameter, index, control=self._control_word,
            address=self.device['USSAddress'])
        problem = None
        with self._io_lock:
            for _ in range(1 + int(self.device['Retries'])):
                try:
                    self.ser.reset_input_buffer()
                    self.ser.write(request)
                    data = self.ser.read(TELEGRAM_LENGTH)
                except serial.SerialException as e:
                    self.device_connected = False
                    raise DeviceError(
                        f'{self.name}: serial I/O failed: {e}') from e
                if len(data) < TELEGRAM_LENGTH:
                    problem = (f'no complete reply within'
                               f' {self.device["Timeout"]} s'
                               f' ({len(data)} bytes)')
                    continue
                try:
                    reply = parse_reply(bytes(data))
                except DeviceError as e:
                    problem = str(e)
                    continue
                if (access != ACCESS_NONE
                        and reply.reply_code in REPLY_VALUES
                        and reply.parameter != parameter):
                    problem = (f'reply for parameter {reply.parameter},'
                               f' not {parameter}')
                    continue
                return reply
        raise DeviceError(f'{self.name}: {problem}')

    def poll(self):
        """The status word and the five process values: a telegram
        without parameter access."""
        return self.exchange(ACCESS_NONE)

    def read_parameter(self, number, index=None, kind='u16'):
        """Parameter `number` (element `index` of an array parameter) as
        the word type `kind`. Raises `DeviceError` with the drive's
        reason when it refuses."""
        access = ACCESS_READ if index is None else ACCESS_READ_ARRAY
        reply = self.exchange(access, number, 0 if index is None else index)
        if reply.reply_code == REPLY_ERROR:
            code = reply.value & 0xFFFF
            raise DeviceError(
                f'{self.name}: parameter {number} not read:'
                f' {REPLY_ERRORS.get(code, f"error {code}")}')
        if reply.reply_code == REPLY_NO_PERMISSION:
            raise DeviceError(
                f'{self.name}: parameter {number} not read: no permission')
        if reply.reply_code not in REPLY_VALUES:
            raise DeviceError(
                f'{self.name}: parameter {number} not read: reply'
                f' designator {reply.reply_code}')
        return decode_value(reply.value, kind)

    def read(self, name):
        """Parameter `name` (a key of `PARAMETERS`) in its unit: an int
        for a scale of 1, a float otherwise."""
        parameter = PARAMETERS[name]
        value = self.read_parameter(parameter.number, kind=parameter.kind)
        return value if parameter.scale == 1 else value * parameter.scale
