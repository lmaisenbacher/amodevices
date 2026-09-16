# -*- coding: utf-8 -*-
"""
Driver for the NKT Photonics Koheras ADJUSTIK (K822) single-frequency
fiber laser system: the K822 mainboard (module type 0x34, address 128)
and the Koheras BASIK K1x2 fiber laser module it holds (module type
0x33, address 1), spoken to with the NKT Interbus register protocol
(`interbus.py`) over the front USB port — a Silicon Labs CP210x virtual
COM port at 115200 8N1 — or the Ethernet port (TCP, port 10001).

Register map: "NKT Photonics SDK Instruction manual" (SDK 2.1.16),
sections 6.7 (Koheras BASIK Module K1x2) and 6.4 (Koheras ADJUSTIK /
ACOUSTIK System K822 / K852); the addresses, types, and scalings are
quoted beside each register below. The lab's earlier Majel C++ server
drove this laser with the same addresses (BASIK 0x01, mainboard 0x80)
and registers.

The laser's wavelength is tuned two ways: thermally through the BASIK
wavelength offset setpoint (register 0x2A, 0.1 pm resolution, a range
of a few hundred pm — the fiber laser's substrate temperature), and
fast through the piezo driven by the mainboard's Wavelength+/- analog
input, whose setup (external/internal source, wide/narrow range, DC/AC
coupling) is configured through the BASIK setup bits.

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging
import struct
import time
from enum import IntEnum

from .. import dev_generic
from ..dev_exceptions import DeviceError
from ..status import STATUS_OK
from .interbus import (
    InterbusTransport, InterbusError, InterbusNack, InterbusProtocolError,
    SerialChannel, TCPChannel, decode_module_type, TCP_PORT_DEFAULT,
    REG_MODULE_TYPE)

logger = logging.getLogger(__name__)

#: Module type numbers (manual 6.7 and 6.4)
MODULE_TYPE_BASIK = 0x33
MODULE_TYPE_ADJUSTIK = 0x34
#: Resolution of the wavelength offset registers (pm)
OFFSET_RESOLUTION_PM = 0.1
#: Wavelength offset setpoint limits (pm) used when neither the config
#: nor the module's parameter set provides them: the limits the lab's
#: Majel server hard-coded for this laser
WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM = (-289., 349.)
#: A parameter-set limit beyond this (raw 0.1 pm units, 2 nm) is not
#: believed
PARAMSET_LIMIT_MAX_RAW = 20000
#: Layout of a settings register's parameter set (little-endian): unit
#: code, error handler, start value, factory value, upper limit, lower
#: limit, numerator, denominator, offset. Not in the manual — from the
#: SDK's `tParamSetStruct` as used by third-party code; the parameter
#: set of a register lives at register + 0x30 (0x2A -> 0x5A, which the
#: NKT CONTROL application reads for the offset slider's range).
PARAMSET_FORMAT = '<BBhhhhhhh'
#: Serial port settings of every Interbus device (manual 2.1); `rtscts`
#: off because a port whose CTS the device never drives would stall
#: every write with it on, while pyserial asserts RTS on open either way
DEFAULT_SERIAL_PARAMS = {
    'baudrate': 115200, 'bytesize': 8, 'parity': 'N', 'stopbits': 1,
    'rtscts': False, 'dsrdtr': False, 'write_timeout': 1.0}
#: Config defaults
DEFAULT_CONFIG = {
    'Interface': 'serial', 'Port': TCP_PORT_DEFAULT, 'Timeout': 0.5,
    'Retries': 3, 'BasikAddress': 1, 'MainboardAddress': 128}
#: USB vendor id of the Silicon Labs CP210x bridge in the ADJUSTIK
USB_VID_SILABS = 0x10C4


class BasikReg(IntEnum):
    """Registers of the Koheras BASIK K1x2 module (manual 6.7)."""
    #: Emission on/off, U8: 0 off, 1 on (if the interlock is closed)
    EMISSION = 0x30
    #: Setup bits, U16 (`BASIK_SETUP_BITS`); Write SET/CLR/TGL usable
    SETUP = 0x31
    #: Output power setpoint, U16, 0.01 mW
    POWER_SETPOINT_MW = 0x22
    #: Output power setpoint, I16, 0.01 dBm
    POWER_SETPOINT_DBM = 0xA0
    #: Wavelength offset setpoint, I16, 0.1 pm: the resulting wavelength
    #: is the standard wavelength plus this offset
    WAVELENGTH_OFFSET_SETPOINT = 0x2A
    #: Parameter set of the wavelength offset setpoint (its limits)
    WAVELENGTH_OFFSET_PARAMSET = 0x5A
    #: User area, 240 bytes of non-volatile memory
    USER_AREA = 0x8D
    #: Status bits, U16 (`BASIK_STATUS_BITS`)
    STATUS = 0x66
    #: Error code, U8
    ERROR_CODE = 0x67
    #: Output power readout, U16, 0.01 mW
    OUTPUT_POWER_MW = 0x17
    #: Output power readout, I16, 0.01 dBm
    OUTPUT_POWER_DBM = 0x90
    #: Standard wavelength (the wavelength at offset 0), U32, 0.1 pm
    STANDARD_WAVELENGTH = 0x32
    #: Measured/calculated wavelength offset readout, I32, 0.1 pm
    WAVELENGTH_OFFSET_READOUT = 0x72
    #: Module temperature, I16, 0.1 C
    MODULE_TEMPERATURE = 0x1C
    #: Module supply voltage, U16, mV
    SUPPLY_VOLTAGE = 0x1E
    #: Module type number (general register)
    MODULE_TYPE = 0x61
    #: Firmware version code (general register)
    FIRMWARE = 0x64
    #: Serial number, 8-character string (general register)
    SERIAL_NUMBER = 0x65
    #: Internal wavelength modulation frequency, two F32 (Hz)
    WL_MOD_FREQUENCY = 0xB8
    #: Wavelength modulation level, U16, permille
    WL_MOD_LEVEL = 0x2B
    #: Wavelength modulation offset, I16, permille
    WL_MOD_OFFSET = 0x2F
    #: Internal amplitude modulation frequency, two F32 (Hz)
    AMP_MOD_FREQUENCY = 0xBA
    #: Amplitude modulation depth, U16, permille
    AMP_MOD_DEPTH = 0x2C
    #: Modulation setup, U16 (waveforms and frequency selectors)
    MODULATION_SETUP = 0xB7


class MainboardReg(IntEnum):
    """Registers of the K822 ADJUSTIK mainboard (manual 6.4)."""
    #: Emission broadcast to all laser modules, U8
    EMISSION = 0x30
    #: Setup bits broadcast to all laser modules, U16
    SETUP = 0x31
    #: Interlock reset: a value > 0 resets the interlock circuit
    INTERLOCK_RESET = 0x32
    #: Watchdog, U8 seconds without communication before emission is
    #: switched off; 0 = disabled
    WATCHDOG = 0x34
    #: Wavelength offset broadcast to all laser modules, I16, 0.1 pm
    WAVELENGTH_OFFSET_BROADCAST = 0x2D
    #: Power setpoint broadcasts (dBm, mW)
    POWER_DBM_BROADCAST = 0x2E
    POWER_MW_BROADCAST = 0x2F
    #: Status bits, U16 (`MAINBOARD_STATUS_BITS`)
    STATUS = 0x66
    #: Internal supply voltage, U16, mV
    SUPPLY_VOLTAGE = 0x11
    #: Modulation setup, U16 (`MAINBOARD_MODULATION_SETUP_BITS`)
    MODULATION_SETUP = 0x3B
    #: Wavelength modulation on/off broadcast, U8
    WL_MODULATION_ON = 0x3E
    #: Amplitude modulation on/off broadcast, U8
    AMP_MODULATION_ON = 0x3F
    #: Multichannel simulation, U8 (0 for normal operation)
    MULTICHANNEL_SIMULATION = 0x36
    #: Module type, firmware, serial number (general registers)
    MODULE_TYPE = 0x61
    FIRMWARE = 0x64
    SERIAL_NUMBER = 0x65
    #: IP address, four U8
    IP_ADDRESS = 0xB0
    #: Modulation gain of the analog wavelength modulation input (not in
    #: the manual; two U16 in tenths of a percent per the Majel server)
    MODULATION_GAIN = 0x38


#: BASIK setup bits (register 0x31), bit -> name
BASIK_SETUP_BITS = {
    1: 'narrow_wavelength_modulation_range',
    2: 'external_wavelength_modulation',
    3: 'wavelength_modulation_dc_coupled',
    4: 'internal_wavelength_modulation',
    5: 'modulation_output',
    8: 'pump_constant_current',
    9: 'external_amplitude_modulation_source',
}
#: BASIK status bits (register 0x66), bit -> name
BASIK_STATUS_BITS = {
    0: 'emission',
    1: 'interlock_off',
    4: 'module_disabled',
    5: 'supply_voltage_low',
    6: 'module_temperature_out_of_range',
    11: 'waiting_for_temperature_to_drop',
    13: 'fiber_laser_temperature_settling',
    14: 'wavelength_stabilized',
    15: 'error_code_present',
}
#: Mainboard status bits (register 0x66), bit -> name
MAINBOARD_STATUS_BITS = {
    0: 'emission',
    1: 'interlock_relays_off',
    2: 'interlock_supply_low',
    3: 'interlock_loop_open',
    4: 'module_address_problem',
    5: 'sd_card_problem',
    6: 'module_communication_problem',
    7: 'no_backplane',
    8: 'illegal_mac_address',
    9: 'power_supply_low',
    10: 'temperature_out_of_range',
    15: 'system_error_code_present',
}
#: Mainboard modulation setup bits (register 0x3B), bit -> name; the
#: waveform fields (bits 4-6 and 12-14) are left out
MAINBOARD_MODULATION_SETUP_BITS = {
    0: 'amplitude_modulation_frequency_selector',
    2: 'amplitude_modulation_from_master',
    3: 'amplitude_modulation_internal_source',
    8: 'wavelength_modulation_frequency_selector',
    10: 'wavelength_modulation_internal_source',
    11: 'wavelength_modulation_to_modules',
}
#: Fleet status vocabulary (`amodevices.status`) for the BASIK status
#: bits that mean the module is not delivering: bit -> word, lowest bit
#: first when several are set
STATUS_TEXT = {
    1: 'interlock_off',
    4: 'module_disabled',
    5: 'supply_voltage_low',
    6: 'temperature_out_of_range',
    15: 'error_code_present',
}
STATUS_EMISSION_OFF = 'emission_off'


def decode_bits(value, table):
    """{name: bool} for every bit of `table` in `value`."""
    return {name: bool(value & (1 << bit)) for bit, name in table.items()}


def status_word(status_bits):
    """The fleet status word of a BASIK status register value: the
    problem of the lowest set problem bit, else 'emission_off' while
    emission is off, else 'ok'."""
    for bit in sorted(STATUS_TEXT):
        if status_bits & (1 << bit):
            return STATUS_TEXT[bit]
    if not status_bits & 1:
        return STATUS_EMISSION_OFF
    return STATUS_OK


def _printable(data):
    """The leading NUL-terminated ASCII text of `data`, or '' when it is
    not printable."""
    text = data.split(b'\x00', 1)[0]
    if text and all(0x20 <= byte < 0x7F for byte in text):
        return text.decode('ascii').strip()
    return ''


def decode_firmware(data):
    """The firmware register (0x64) as text. Its format is not in the
    manual; a K1x2 module answers a 16-bit version code followed by a
    build string (`75 00 31 2e 31 37 ...` = 117, "1.17-2345 Dec 21
    2021 ..."), rendered as "1.17 (1.17-2345 Dec 21 2021 ...)". Plain
    text is passed through, anything else shown as hex."""
    if len(data) >= 3 and data[1] == 0 and _printable(data[2:]):
        version = int.from_bytes(data[:2], 'little')
        return f'{version // 100}.{version % 100:02d} ({_printable(data[2:])})'
    text = _printable(data)
    return text if text else data.hex(' ')


class NKTKoherasAdjustik(dev_generic.Device):
    """Koheras ADJUSTIK K822 system: BASIK K1x2 laser module plus the
    ADJUSTIK mainboard on one Interbus port.

    Configuration dict `device`:

    - 'Device': name (log messages, database 'device' tag).
    - 'Address': serial port ('COM7', '/dev/ttyUSB0'), or the host name
      or IP address with 'Interface': 'tcp'.
    - 'Interface': 'serial' (default) or 'tcp' (the Ethernet port).
    - 'Port': TCP port (default 10001).
    - 'Timeout': Interbus reply timeout per transaction (s, default 0.5).
    - 'Retries': repeats after a timeout, Busy, or CRC error (default 3).
    - 'SerialConnectionParams': pyserial keyword arguments merged over
      `DEFAULT_SERIAL_PARAMS` (e.g. {'rtscts': True}).
    - 'BasikAddress' (default 1), 'MainboardAddress' (default 128).
    - 'HostAddress': fixed host address (default None = cycled).
    - 'WavelengthOffsetLimits_pm': [min, max] overriding the limits read
      from the module's parameter set.
    """

    def __init__(self, device):
        """Initialize the driver for the device configured by the dict
        `device` (no I/O; `connect()` opens the port)."""
        device = {**DEFAULT_CONFIG, **device}
        device['SerialConnectionParams'] = {
            **DEFAULT_SERIAL_PARAMS,
            **device.get('SerialConnectionParams', {})}
        super().__init__(device)
        self.basik_address = int(device['BasikAddress'])
        self.mainboard_address = int(device['MainboardAddress'])
        self.transport = None
        self._channel = None
        self.mainboard_present = False
        self.serial_number = ''
        self.firmware_version = ''
        self._standard_wavelength_pm = float('nan')
        self.wavelength_offset_limits_pm = WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM
        #: Where the offset limits came from: 'config', 'paramset', or
        #: 'fallback'
        self.wavelength_offset_limits_source = 'fallback'

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, verify=True):
        """Open the port, verify the module types, and cache the constant
        module data (serial number, firmware, standard wavelength, offset
        limits). Raises `DeviceError` when the port cannot be opened or
        no BASIK module answers at its address. With `verify` False only
        the port is opened (for an address scan of an unknown bus)."""
        device = self.device
        self.close()
        interface = str(device.get('Interface', 'serial')).lower()
        if interface == 'tcp':
            self._channel = TCPChannel(
                device['Address'], int(device.get('Port', TCP_PORT_DEFAULT)))
            logger.info('%s: Opened TCP connection to %s:%s',
                        device['Device'], device['Address'],
                        device.get('Port', TCP_PORT_DEFAULT))
        elif interface == 'serial':
            self.serial_connect()
            self._channel = SerialChannel(self.ser)
        else:
            raise DeviceError(
                f'{device["Device"]}: Unknown \'Interface\' {interface!r} '
                '(expected \'serial\' or \'tcp\')')
        self.transport = InterbusTransport(
            self._channel, timeout_s=float(device['Timeout']),
            retries=int(device['Retries']),
            host_address=device.get('HostAddress'), name=device['Device'])
        if verify:
            try:
                self._identify()
            except Exception:
                self.close()
                raise
        self.device_present = True
        self.device_connected = True

    def _identify(self):
        """Verify the BASIK module (its type at its address), look for the
        mainboard, and cache the constant module data."""
        device = self.device
        transport = self.transport
        try:
            module_type = decode_module_type(
                transport.read_register(self.basik_address, REG_MODULE_TYPE))
        except InterbusError as exc:
            raise DeviceError(
                f'{device["Device"]}: No module answers at BASIK address '
                f'{self.basik_address}: {exc}') from exc
        if module_type != MODULE_TYPE_BASIK:
            raise DeviceError(
                f'{device["Device"]}: Module at address {self.basik_address} '
                f'has type 0x{module_type:02X}, expected the Koheras BASIK '
                f'K1x2 type 0x{MODULE_TYPE_BASIK:02X}')
        self.serial_number = transport.read_str(
            self.basik_address, BasikReg.SERIAL_NUMBER)
        self.firmware_version = decode_firmware(
            transport.read_register(self.basik_address, BasikReg.FIRMWARE))
        self._standard_wavelength_pm = (
            transport.read_u32(self.basik_address,
                               BasikReg.STANDARD_WAVELENGTH)
            * OFFSET_RESOLUTION_PM)
        logger.info(
            '%s: BASIK module serial number %s, firmware %s, standard '
            'wavelength %.4f nm', device['Device'], self.serial_number,
            self.firmware_version, self._standard_wavelength_pm / 1e3)
        try:
            mainboard_type = decode_module_type(transport.read_register(
                self.mainboard_address, REG_MODULE_TYPE))
        except InterbusError as exc:
            logger.warning(
                '%s: No mainboard answers at address %d (%s); mainboard '
                'functions are unavailable', device['Device'],
                self.mainboard_address, exc)
            self.mainboard_present = False
        else:
            self.mainboard_present = mainboard_type == MODULE_TYPE_ADJUSTIK
            if not self.mainboard_present:
                logger.warning(
                    '%s: Module at address %d has type 0x%02X, expected the '
                    'ADJUSTIK mainboard type 0x%02X; mainboard functions '
                    'are unavailable', device['Device'],
                    self.mainboard_address, mainboard_type,
                    MODULE_TYPE_ADJUSTIK)
        if self.mainboard_present:
            try:
                watchdog_s = self.get_watchdog_s()
            except InterbusError as exc:
                logger.warning('%s: Watchdog register not readable: %s',
                               device['Device'], exc)
            else:
                if watchdog_s:
                    logger.warning(
                        '%s: The mainboard watchdog is set to %d s: a pause '
                        'in the communication with the host switches '
                        'emission OFF', device['Device'], watchdog_s)
        self._resolve_offset_limits()

    def _resolve_offset_limits(self):
        """The wavelength offset setpoint limits: the config's, else the
        module's parameter set, else the fallback."""
        device = self.device
        configured = device.get('WavelengthOffsetLimits_pm')
        if configured is not None:
            low, high = (float(configured[0]), float(configured[1]))
            if not low < high:
                raise DeviceError(
                    f'{device["Device"]}: \'WavelengthOffsetLimits_pm\' '
                    f'{configured!r} is not an increasing pair')
            self.wavelength_offset_limits_pm = (low, high)
            self.wavelength_offset_limits_source = 'config'
            return
        try:
            limits = self._read_offset_limits_from_paramset()
        except InterbusError as exc:
            logger.info(
                '%s: Wavelength offset limits not available from the '
                'module (%s); using the fallback %s pm', device['Device'],
                exc, WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM)
            self.wavelength_offset_limits_pm = (
                WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM)
            self.wavelength_offset_limits_source = 'fallback'
            return
        self.wavelength_offset_limits_pm = limits
        self.wavelength_offset_limits_source = 'paramset'
        logger.info('%s: Wavelength offset limits %.1f to %.1f pm (module '
                    'parameter set)', device['Device'], *limits)

    def _read_offset_limits_from_paramset(self):
        """(min, max) offset limits (pm) from the parameter set of register
        0x2A, parsed defensively (its layout is undocumented): raises
        `InterbusProtocolError` on a short or implausible record."""
        data = self.transport.read_register(
            self.basik_address, BasikReg.WAVELENGTH_OFFSET_PARAMSET)
        logger.info('%s: Wavelength offset parameter set: %s',
                    self.device['Device'], data.hex(' '))
        size = struct.calcsize(PARAMSET_FORMAT)
        if len(data) < size:
            raise InterbusProtocolError(
                f'parameter set of {len(data)} bytes, expected {size}')
        (_unit, _error_handler, _start, _factory, upper, lower,
         _numerator, _denominator, _offset) = struct.unpack(
            PARAMSET_FORMAT, data[:size])
        if not (lower < 0 < upper
                and max(abs(lower), abs(upper)) <= PARAMSET_LIMIT_MAX_RAW):
            raise InterbusProtocolError(
                f'implausible parameter set limits {lower}..{upper}')
        return (lower * OFFSET_RESOLUTION_PM, upper * OFFSET_RESOLUTION_PM)

    def close(self):
        """Close the connection."""
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception as exc:
                logger.debug('%s: closing the channel: %s',
                             self.device['Device'], exc)
        self._channel = None
        self.transport = None
        if self.ser is not None:
            self.serial_close()
            self.ser = None
        self.device_connected = False

    def _require_connection(self):
        if self.transport is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')
        return self.transport

    def _require_mainboard(self):
        transport = self._require_connection()
        if not self.mainboard_present:
            raise DeviceError(
                f'{self.device["Device"]}: No ADJUSTIK mainboard at address '
                f'{self.mainboard_address}')
        return transport

    # ------------------------------------------------------------------
    # General registers
    # ------------------------------------------------------------------

    def get_module_type(self, address=None):
        """Module type number of the module at `address` (default: the
        BASIK module)."""
        transport = self._require_connection()
        address = self.basik_address if address is None else address
        return decode_module_type(
            transport.read_register(address, REG_MODULE_TYPE))

    def get_serial_number(self, address=None):
        """Serial number string of the module at `address` (default: the
        BASIK module)."""
        transport = self._require_connection()
        address = self.basik_address if address is None else address
        return transport.read_str(address, BasikReg.SERIAL_NUMBER)

    def get_firmware_version(self, address=None):
        """Firmware version of the module at `address` (default: the BASIK
        module), as text when printable, else as hex bytes."""
        transport = self._require_connection()
        address = self.basik_address if address is None else address
        return decode_firmware(
            transport.read_register(address, BasikReg.FIRMWARE))

    # ------------------------------------------------------------------
    # BASIK module: emission and setup
    # ------------------------------------------------------------------

    def get_emission(self):
        """Whether laser emission is switched on (register 0x30)."""
        transport = self._require_connection()
        return bool(transport.read_u8(self.basik_address, BasikReg.EMISSION))

    def set_emission(self, on):
        """Switch laser emission on (True; needs the interlock closed) or
        off (False)."""
        transport = self._require_connection()
        transport.write_u8(self.basik_address, BasikReg.EMISSION, 1 if on else 0)

    def get_setup_bits(self):
        """The setup register value (0x31)."""
        transport = self._require_connection()
        return transport.read_u16(self.basik_address, BasikReg.SETUP)

    def get_setup(self):
        """The setup bits decoded, {name: bool} per `BASIK_SETUP_BITS`."""
        return decode_bits(self.get_setup_bits(), BASIK_SETUP_BITS)

    def set_setup_bit(self, bit, on):
        """Set (True) or clear (False) one setup bit (Write SET1 / CLR1,
        which leave the other bits alone)."""
        transport = self._require_connection()
        mask = 1 << int(bit)
        if on:
            transport.write_set_bits(self.basik_address, BasikReg.SETUP, mask)
        else:
            transport.write_clear_bits(self.basik_address, BasikReg.SETUP, mask)

    def set_wide_modulation_range(self, on):
        """Wide (True) or narrow (False) wavelength modulation range
        (setup bit 1 = narrow)."""
        self.set_setup_bit(1, not on)

    def set_external_modulation(self, on):
        """Enable the external wavelength modulation input (setup bit 2)."""
        self.set_setup_bit(2, on)

    def set_dc_coupled_modulation(self, on):
        """DC (True) or AC (False) coupling of the wavelength modulation
        (setup bit 3)."""
        self.set_setup_bit(3, on)

    def set_internal_modulation(self, on):
        """Enable the internal wavelength modulation generator (setup bit
        4)."""
        self.set_setup_bit(4, on)

    def set_modulation_output(self, on):
        """Enable the modulation signal output (setup bit 5)."""
        self.set_setup_bit(5, on)

    def set_pump_constant_current(self, on):
        """Pump operation at constant current (True) or constant power
        (False) (setup bit 8)."""
        self.set_setup_bit(8, on)

    # ------------------------------------------------------------------
    # BASIK module: power and wavelength
    # ------------------------------------------------------------------

    def get_power_setpoint_mw(self):
        """Output power setpoint (mW)."""
        transport = self._require_connection()
        return transport.read_u16(
            self.basik_address, BasikReg.POWER_SETPOINT_MW) * 0.01

    def set_power_setpoint_mw(self, power_mw):
        """Set the output power setpoint (mW, 0.01 mW resolution)."""
        transport = self._require_connection()
        transport.write_u16(self.basik_address, BasikReg.POWER_SETPOINT_MW,
                            int(round(power_mw / 0.01)))

    def get_power_setpoint_dbm(self):
        """Output power setpoint (dBm)."""
        transport = self._require_connection()
        return transport.read_i16(
            self.basik_address, BasikReg.POWER_SETPOINT_DBM) * 0.01

    def get_wavelength_offset_setpoint_pm(self):
        """Wavelength offset setpoint (pm; register 0x2A, the thermal
        tuning)."""
        transport = self._require_connection()
        return transport.read_i16(
            self.basik_address,
            BasikReg.WAVELENGTH_OFFSET_SETPOINT) * OFFSET_RESOLUTION_PM

    def set_wavelength_offset_setpoint_pm(self, offset_pm):
        """Set the wavelength offset setpoint (pm, rounded to the 0.1 pm
        resolution). A value outside `wavelength_offset_limits_pm` is
        refused (never clamped) with a `DeviceError`. Returns the value
        written."""
        transport = self._require_connection()
        raw = int(round(float(offset_pm) / OFFSET_RESOLUTION_PM))
        value = raw * OFFSET_RESOLUTION_PM
        low, high = self.wavelength_offset_limits_pm
        if not low - 1e-9 <= value <= high + 1e-9:
            raise DeviceError(
                f'{self.device["Device"]}: Wavelength offset {offset_pm:.1f} '
                f'pm is outside the limits {low:.1f} to {high:.1f} pm')
        transport.write_i16(
            self.basik_address, BasikReg.WAVELENGTH_OFFSET_SETPOINT, raw)
        return value

    def get_wavelength_offset_readout_pm(self):
        """Measured/calculated wavelength offset (pm; register 0x72). Per
        the lab's Majel documentation it includes the piezo contribution."""
        transport = self._require_connection()
        return transport.read_i32(
            self.basik_address,
            BasikReg.WAVELENGTH_OFFSET_READOUT) * OFFSET_RESOLUTION_PM

    def get_standard_wavelength_nm(self):
        """The wavelength at offset 0 (nm; register 0x32, cached at
        `connect()`)."""
        self._require_connection()
        return self._standard_wavelength_pm / 1e3

    def get_wavelength_nm(self):
        """Absolute wavelength (nm): standard wavelength plus the offset
        readout."""
        return (self._standard_wavelength_pm
                + self.get_wavelength_offset_readout_pm()) / 1e3

    def get_wavelength_offset_limits_pm(self):
        """(min, max) of the wavelength offset setpoint (pm); the source
        is in `wavelength_offset_limits_source`."""
        return self.wavelength_offset_limits_pm

    # ------------------------------------------------------------------
    # BASIK module: readouts
    # ------------------------------------------------------------------

    def get_status_bits(self):
        """The status register value (0x66)."""
        transport = self._require_connection()
        return transport.read_u16(self.basik_address, BasikReg.STATUS)

    def get_status(self):
        """The status bits decoded, {name: bool} per `BASIK_STATUS_BITS`."""
        return decode_bits(self.get_status_bits(), BASIK_STATUS_BITS)

    def get_error_code(self):
        """The error code (register 0x67; 0 = none)."""
        transport = self._require_connection()
        return transport.read_u8(self.basik_address, BasikReg.ERROR_CODE)

    def get_output_power_mw(self):
        """Output power readout (mW)."""
        transport = self._require_connection()
        return transport.read_u16(
            self.basik_address, BasikReg.OUTPUT_POWER_MW) * 0.01

    def get_output_power_dbm(self):
        """Output power readout (dBm)."""
        transport = self._require_connection()
        return transport.read_i16(
            self.basik_address, BasikReg.OUTPUT_POWER_DBM) * 0.01

    def get_module_temperature_c(self):
        """Module temperature (C)."""
        transport = self._require_connection()
        return transport.read_i16(
            self.basik_address, BasikReg.MODULE_TEMPERATURE) * 0.1

    def get_supply_voltage_v(self):
        """Module supply voltage (V)."""
        transport = self._require_connection()
        return transport.read_u16(
            self.basik_address, BasikReg.SUPPLY_VOLTAGE) * 1e-3

    def get_wavelength_modulation_frequency_hz(self):
        """The two internal wavelength modulation frequencies (Hz)."""
        transport = self._require_connection()
        data = transport.read_register(self.basik_address,
                                       BasikReg.WL_MOD_FREQUENCY)
        if len(data) < 8:
            raise InterbusProtocolError(
                f'{self.device["Device"]}: Modulation frequency register '
                f'returned {len(data)} bytes, expected 8')
        return struct.unpack('<ff', data[:8])

    def get_wavelength_modulation_level_permille(self):
        """Internal wavelength modulation level (permille)."""
        transport = self._require_connection()
        return transport.read_u16(self.basik_address, BasikReg.WL_MOD_LEVEL)

    def get_wavelength_modulation_offset_permille(self):
        """Internal wavelength modulation offset (permille)."""
        transport = self._require_connection()
        return transport.read_i16(self.basik_address, BasikReg.WL_MOD_OFFSET)

    def get_modulation_setup_bits(self):
        """The BASIK modulation setup register value (0xB7)."""
        transport = self._require_connection()
        return transport.read_u16(self.basik_address,
                                  BasikReg.MODULATION_SETUP)

    def read_readings(self, include_slow=True):
        """One poll of the module: the fast set (wavelength offset
        setpoint and readout, status, setup: four transactions) plus,
        with `include_slow`, output power, temperature, supply voltage,
        error code, and the mainboard status.

        Returns a dict with 'offset_setpoint_pm', 'offset_readout_pm',
        'wavelength_nm', 'status_bits', 'status' (dict), 'status_word',
        'emission', 'setup_bits', 'setup' (dict), 'external_modulation',
        'wide_modulation_range', 'dc_coupled_modulation', 'time' (epoch
        s), and with the slow set 'power_mw', 'temperature_c',
        'supply_voltage_v', 'error_code', 'system_status_bits',
        'system_status' (dict; the last two None without a mainboard).
        """
        self._require_connection()
        offset_setpoint_pm = self.get_wavelength_offset_setpoint_pm()
        offset_readout_pm = self.get_wavelength_offset_readout_pm()
        status_bits = self.get_status_bits()
        setup_bits = self.get_setup_bits()
        setup = decode_bits(setup_bits, BASIK_SETUP_BITS)
        readings = {
            'offset_setpoint_pm': offset_setpoint_pm,
            'offset_readout_pm': offset_readout_pm,
            'wavelength_nm': (
                self._standard_wavelength_pm + offset_readout_pm) / 1e3,
            'status_bits': status_bits,
            'status': decode_bits(status_bits, BASIK_STATUS_BITS),
            'status_word': status_word(status_bits),
            'emission': bool(status_bits & 1),
            'setup_bits': setup_bits,
            'setup': setup,
            'external_modulation': setup['external_wavelength_modulation'],
            'wide_modulation_range': (
                not setup['narrow_wavelength_modulation_range']),
            'dc_coupled_modulation': setup['wavelength_modulation_dc_coupled'],
        }
        if include_slow:
            readings['power_mw'] = self.get_output_power_mw()
            readings['temperature_c'] = self.get_module_temperature_c()
            readings['supply_voltage_v'] = self.get_supply_voltage_v()
            readings['error_code'] = self.get_error_code()
            if self.mainboard_present:
                system_status_bits = self.get_system_status_bits()
                readings['system_status_bits'] = system_status_bits
                readings['system_status'] = decode_bits(
                    system_status_bits, MAINBOARD_STATUS_BITS)
            else:
                readings['system_status_bits'] = None
                readings['system_status'] = None
        readings['time'] = time.time()
        return readings

    # ------------------------------------------------------------------
    # ADJUSTIK mainboard
    # ------------------------------------------------------------------

    def get_system_status_bits(self):
        """The mainboard status register value (0x66)."""
        transport = self._require_mainboard()
        return transport.read_u16(self.mainboard_address, MainboardReg.STATUS)

    def get_system_status(self):
        """The mainboard status bits decoded, {name: bool} per
        `MAINBOARD_STATUS_BITS`."""
        return decode_bits(self.get_system_status_bits(),
                           MAINBOARD_STATUS_BITS)

    def get_mainboard_supply_voltage_v(self):
        """The mainboard's internal supply voltage (V)."""
        transport = self._require_mainboard()
        return transport.read_u16(
            self.mainboard_address, MainboardReg.SUPPLY_VOLTAGE) * 1e-3

    def reset_interlock(self):
        """Reset the interlock circuit (register 0x32): works only with
        the door interlock closed, the key switch on, and the external
        bus interlock loop closed."""
        transport = self._require_mainboard()
        transport.write_u8(self.mainboard_address,
                           MainboardReg.INTERLOCK_RESET, 1)

    def get_watchdog_s(self):
        """The communication watchdog (s; 0 = disabled)."""
        transport = self._require_mainboard()
        return transport.read_u8(self.mainboard_address, MainboardReg.WATCHDOG)

    def set_watchdog_s(self, seconds):
        """Set the communication watchdog (0..255 s; 0 = disabled): with
        it on, emission is switched off after that long without
        communication from the host."""
        transport = self._require_mainboard()
        seconds = int(seconds)
        if not 0 <= seconds <= 255:
            raise DeviceError(
                f'{self.device["Device"]}: Watchdog {seconds} s is outside '
                '0..255 s')
        transport.write_u8(self.mainboard_address, MainboardReg.WATCHDOG,
                           seconds)

    def set_emission_broadcast(self, on):
        """Switch emission on/off in all laser modules (mainboard 0x30)."""
        transport = self._require_mainboard()
        transport.write_u8(self.mainboard_address, MainboardReg.EMISSION,
                           1 if on else 0)

    def get_mainboard_modulation_setup_bits(self):
        """The mainboard modulation setup register value (0x3B)."""
        transport = self._require_mainboard()
        return transport.read_u16(self.mainboard_address,
                                  MainboardReg.MODULATION_SETUP)

    def get_mainboard_modulation_setup(self):
        """The mainboard modulation setup bits decoded, {name: bool} per
        `MAINBOARD_MODULATION_SETUP_BITS`."""
        return decode_bits(self.get_mainboard_modulation_setup_bits(),
                           MAINBOARD_MODULATION_SETUP_BITS)

    def set_wavelength_modulation_on(self, on):
        """Switch the wavelength modulation on/off in all laser modules
        (mainboard 0x3E; this also enables/disables the external
        modulation source in the modules)."""
        transport = self._require_mainboard()
        transport.write_u8(self.mainboard_address,
                           MainboardReg.WL_MODULATION_ON, 1 if on else 0)

    def set_amplitude_modulation_on(self, on):
        """Switch the amplitude modulation on/off in all laser modules
        (mainboard 0x3F)."""
        transport = self._require_mainboard()
        transport.write_u8(self.mainboard_address,
                           MainboardReg.AMP_MODULATION_ON, 1 if on else 0)

    def set_wavelength_offset_broadcast_pm(self, offset_pm):
        """Broadcast a wavelength offset setpoint (pm) to all laser
        modules (mainboard 0x2D); same limits as the module register."""
        transport = self._require_mainboard()
        raw = int(round(float(offset_pm) / OFFSET_RESOLUTION_PM))
        value = raw * OFFSET_RESOLUTION_PM
        low, high = self.wavelength_offset_limits_pm
        if not low - 1e-9 <= value <= high + 1e-9:
            raise DeviceError(
                f'{self.device["Device"]}: Wavelength offset {offset_pm:.1f} '
                f'pm is outside the limits {low:.1f} to {high:.1f} pm')
        transport.write_i16(self.mainboard_address,
                            MainboardReg.WAVELENGTH_OFFSET_BROADCAST, raw)

    # ------------------------------------------------------------------
    # Lab helpers
    # ------------------------------------------------------------------

    def scan_addresses(self, addresses=range(1, 256), timeout_s=0.1):
        """{address: module type} of every module answering on the bus."""
        transport = self._require_connection()
        return transport.scan(addresses, timeout_s=timeout_s)

    def dump_registers(self, address, registers):
        """{register: bytes | None} — the raw content of each register at
        `address`, None where the module answers with a Nack."""
        transport = self._require_connection()
        dump = {}
        for register in registers:
            try:
                dump[int(register)] = transport.read_register(
                    address, int(register))
            except InterbusNack:
                dump[int(register)] = None
        return dump

    @staticmethod
    def find_ports():
        """Serial port names whose USB bridge is a Silicon Labs CP210x —
        the ADJUSTIK's front USB port (and any other CP210x device)."""
        from serial.tools import list_ports
        return [port.device for port in list_ports.comports()
                if port.vid == USB_VID_SILABS]
