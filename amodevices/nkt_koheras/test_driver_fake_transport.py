# -*- coding: utf-8 -*-
"""Tests of the Koheras ADJUSTIK driver against a fake Interbus channel
that serves a register store: the connect sequence (module types, cached
constants, watchdog warning, offset limits), the unit conversions of the
readouts and setpoints, the setup-bit writes, and the status vocabulary.
No laser needed. Runs under pytest or directly as a script.
"""

import logging
import struct
import sys

import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.status import check_status_table
from amodevices.nkt_koheras import nkt_koheras_adjustik as nkt
from amodevices.nkt_koheras.interbus import (
    InterbusTransport, build_telegram, parse_telegram, MSG_READ, MSG_WRITE,
    MSG_WRITE_SET1, MSG_WRITE_CLR1, MSG_ACK, MSG_DATAGRAM, MSG_NACK)
from amodevices.nkt_koheras.nkt_koheras_adjustik import (
    NKTKoherasAdjustik, BasikReg, MainboardReg, STATUS_TEXT, status_word,
    WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM)

BASIK = 1
MAINBOARD = 128


def paramset(lower, upper):
    """A parameter-set record with the given raw limits (0.1 pm)."""
    return struct.pack(nkt.PARAMSET_FORMAT, 14, 0, 0, 0, upper, lower, 1, 1, 0)


def healthy_registers():
    """The register store of a healthy laser: emission on, external DC
    coupled wavelength modulation, offset setpoint 12.3 pm."""
    return {
        (BASIK, BasikReg.MODULE_TYPE): b'\x33',
        (BASIK, BasikReg.SERIAL_NUMBER): b'17360328',
        (BASIK, BasikReg.FIRMWARE): b'1.06',
        (BASIK, BasikReg.STANDARD_WAVELENGTH):
            (10640000).to_bytes(4, 'little'),
        (BASIK, BasikReg.WAVELENGTH_OFFSET_SETPOINT):
            (123).to_bytes(2, 'little', signed=True),
        (BASIK, BasikReg.WAVELENGTH_OFFSET_READOUT):
            (-10).to_bytes(4, 'little', signed=True),
        (BASIK, BasikReg.STATUS): (0x0001).to_bytes(2, 'little'),
        (BASIK, BasikReg.SETUP): (0x000C).to_bytes(2, 'little'),
        (BASIK, BasikReg.OUTPUT_POWER_MW): (4000).to_bytes(2, 'little'),
        (BASIK, BasikReg.MODULE_TEMPERATURE):
            (351).to_bytes(2, 'little', signed=True),
        (BASIK, BasikReg.SUPPLY_VOLTAGE): (12000).to_bytes(2, 'little'),
        (BASIK, BasikReg.ERROR_CODE): b'\x00',
        (BASIK, BasikReg.EMISSION): b'\x01',
        (BASIK, BasikReg.WAVELENGTH_OFFSET_PARAMSET): paramset(-2890, 3490),
        (MAINBOARD, MainboardReg.MODULE_TYPE): b'\x34',
        (MAINBOARD, MainboardReg.STATUS): (0x0001).to_bytes(2, 'little'),
        (MAINBOARD, MainboardReg.WATCHDOG): b'\x00',
        (MAINBOARD, MainboardReg.SUPPLY_VOLTAGE): (12100).to_bytes(2, 'little'),
    }


class FakeModuleBus:
    """An Interbus channel serving a register store: reads answer with
    the stored bytes (a Nack for an absent register), writes update the
    store (Write SET1/CLR1 modify it) and are acknowledged. `writes`
    records (address, message type, register, data) of every write."""

    def __init__(self, registers):
        self.registers = registers
        self.writes = []
        self._pending = b''

    def write(self, data):
        request = parse_telegram(data)
        key = (request.dest, request.register)
        if request.msg_type == MSG_READ:
            if key not in self.registers:
                reply = build_telegram(request.src, request.dest, MSG_NACK,
                                       request.register)
            else:
                reply = build_telegram(request.src, request.dest, MSG_DATAGRAM,
                                       request.register, self.registers[key])
        else:
            self.writes.append((request.dest, request.msg_type,
                                request.register, request.data))
            if request.msg_type == MSG_WRITE:
                self.registers[key] = request.data
            elif request.msg_type in (MSG_WRITE_SET1, MSG_WRITE_CLR1):
                current = int.from_bytes(self.registers.get(key, b'\x00\x00'),
                                         'little')
                mask = int.from_bytes(request.data, 'little')
                current = (current | mask if request.msg_type == MSG_WRITE_SET1
                           else current & ~mask)
                self.registers[key] = current.to_bytes(len(request.data),
                                                       'little')
            reply = build_telegram(request.src, request.dest, MSG_ACK,
                                   request.register)
        self._pending = reply

    def read_until(self, terminator, deadline):
        out, self._pending = self._pending, b''
        return out

    def reset_input_buffer(self):
        self._pending = b''

    def close(self):
        pass


class FakeNKT(NKTKoherasAdjustik):
    """The driver on a `FakeModuleBus` instead of a serial port."""

    def __init__(self, registers=None, **config):
        self.registers = (healthy_registers() if registers is None
                          else registers)
        super().__init__({'Device': 'Fake ADJUSTIK', 'Address': 'FAKE',
                          **config})

    def connect(self):
        self.close()
        self._channel = FakeModuleBus(self.registers)
        self.transport = InterbusTransport(
            self._channel, timeout_s=0.01, retries=0, host_address=0xA2,
            name=self.device['Device'])
        try:
            self._identify()
        except Exception:
            self.close()
            raise
        self.device_present = True
        self.device_connected = True

    @property
    def writes(self):
        return self._channel.writes


def connected(**kwargs):
    dev = FakeNKT(**kwargs)
    dev.connect()
    return dev


def test_connect_verifies_the_modules_and_caches_the_constants(caplog):
    with caplog.at_level(logging.WARNING):
        dev = connected()
    assert dev.device_connected and dev.mainboard_present
    assert dev.serial_number == '17360328'
    assert dev.firmware_version == '1.06'
    assert dev.get_standard_wavelength_nm() == pytest.approx(1064.0)
    assert dev.wavelength_offset_limits_pm == pytest.approx((-289., 349.))
    assert dev.wavelength_offset_limits_source == 'paramset'
    assert not dev.writes                           # connecting writes nothing
    assert not caplog.records


def test_connect_refuses_a_wrong_module_type():
    registers = healthy_registers()
    registers[(BASIK, BasikReg.MODULE_TYPE)] = b'\x21'
    dev = FakeNKT(registers)
    with pytest.raises(DeviceError, match='type 0x21'):
        dev.connect()
    assert not dev.device_connected and dev.transport is None


def test_connect_without_a_mainboard_warns_and_continues(caplog):
    registers = healthy_registers()
    del registers[(MAINBOARD, MainboardReg.MODULE_TYPE)]
    with caplog.at_level(logging.WARNING):
        dev = FakeNKT(registers)
        dev.connect()
    assert dev.device_connected and not dev.mainboard_present
    assert any('No mainboard' in r.message for r in caplog.records)
    with pytest.raises(DeviceError):
        dev.get_system_status_bits()
    readings = dev.read_readings()
    assert readings['system_status_bits'] is None


def test_a_set_watchdog_is_warned_about(caplog):
    registers = healthy_registers()
    registers[(MAINBOARD, MainboardReg.WATCHDOG)] = b'\x1E'
    with caplog.at_level(logging.WARNING):
        dev = FakeNKT(registers)
        dev.connect()
    assert any('watchdog is set to 30 s' in r.message for r in caplog.records)
    assert dev.get_watchdog_s() == 30


def test_offset_setpoint_write_rounds_refuses_and_returns_the_value():
    dev = connected()
    assert dev.set_wavelength_offset_setpoint_pm(12.34) == pytest.approx(12.3)
    assert dev.writes[-1] == (BASIK, MSG_WRITE,
                              BasikReg.WAVELENGTH_OFFSET_SETPOINT, b'\x7B\x00')
    assert dev.set_wavelength_offset_setpoint_pm(-5.0) == pytest.approx(-5.0)
    assert dev.writes[-1][3] == b'\xCE\xFF'
    assert dev.get_wavelength_offset_setpoint_pm() == pytest.approx(-5.0)
    n_writes = len(dev.writes)
    with pytest.raises(DeviceError, match='outside the limits'):
        dev.set_wavelength_offset_setpoint_pm(400.)
    with pytest.raises(DeviceError, match='outside the limits'):
        dev.set_wavelength_offset_setpoint_pm(-289.06)
    assert len(dev.writes) == n_writes                # refused, not clamped
    # The limits themselves are writable
    assert dev.set_wavelength_offset_setpoint_pm(349.0) == pytest.approx(349.0)


def test_readouts_convert_units():
    dev = connected()
    assert dev.get_wavelength_offset_readout_pm() == pytest.approx(-1.0)
    assert dev.get_wavelength_nm() == pytest.approx(1063.999)
    assert dev.get_output_power_mw() == pytest.approx(40.0)
    assert dev.get_module_temperature_c() == pytest.approx(35.1)
    assert dev.get_supply_voltage_v() == pytest.approx(12.0)
    assert dev.get_mainboard_supply_voltage_v() == pytest.approx(12.1)
    assert dev.get_error_code() == 0
    assert dev.get_emission() is True


def test_status_and_setup_decode():
    dev = connected()
    status = dev.get_status()
    assert status['emission'] and not status['interlock_off']
    setup = dev.get_setup()
    assert setup['external_wavelength_modulation']
    assert setup['wavelength_modulation_dc_coupled']
    assert not setup['narrow_wavelength_modulation_range']
    system = dev.get_system_status()
    assert system['emission'] and not system['interlock_loop_open']


def test_status_word_follows_the_fleet_vocabulary():
    check_status_table(STATUS_TEXT)
    assert status_word(0x0001) == 'ok'
    assert status_word(0x0000) == 'emission_off'
    assert status_word(0x0002) == 'interlock_off'
    assert status_word(0x0003) == 'interlock_off'   # a problem beats emission
    assert status_word(0x8010) == 'module_disabled'  # lowest problem bit first
    assert status_word(0x8001) == 'error_code_present'


def test_setup_bit_writes_use_set_and_clear():
    dev = connected()
    dev.set_external_modulation(True)
    assert dev.writes[-1] == (BASIK, MSG_WRITE_SET1, BasikReg.SETUP,
                              b'\x04\x00')
    dev.set_dc_coupled_modulation(False)
    assert dev.writes[-1] == (BASIK, MSG_WRITE_CLR1, BasikReg.SETUP,
                              b'\x08\x00')
    dev.set_wide_modulation_range(False)             # narrow = bit 1 set
    assert dev.writes[-1] == (BASIK, MSG_WRITE_SET1, BasikReg.SETUP,
                              b'\x02\x00')
    setup = dev.get_setup()
    assert setup['narrow_wavelength_modulation_range']
    assert not setup['wavelength_modulation_dc_coupled']
    dev.set_emission(False)
    assert dev.writes[-1] == (BASIK, MSG_WRITE, BasikReg.EMISSION, b'\x00')


def test_mainboard_writes():
    dev = connected()
    dev.reset_interlock()
    assert dev.writes[-1] == (MAINBOARD, MSG_WRITE,
                              MainboardReg.INTERLOCK_RESET, b'\x01')
    dev.set_wavelength_modulation_on(True)
    assert dev.writes[-1] == (MAINBOARD, MSG_WRITE,
                              MainboardReg.WL_MODULATION_ON, b'\x01')
    dev.set_watchdog_s(0)
    assert dev.writes[-1] == (MAINBOARD, MSG_WRITE, MainboardReg.WATCHDOG,
                              b'\x00')
    with pytest.raises(DeviceError):
        dev.set_watchdog_s(300)


def test_offset_limits_precedence():
    # Config beats the parameter set
    dev = connected(WavelengthOffsetLimits_pm=[-100, 100])
    assert dev.wavelength_offset_limits_pm == (-100., 100.)
    assert dev.wavelength_offset_limits_source == 'config'
    with pytest.raises(DeviceError):
        dev.set_wavelength_offset_setpoint_pm(150.)
    # No parameter set (Nack) -> fallback
    registers = healthy_registers()
    del registers[(BASIK, BasikReg.WAVELENGTH_OFFSET_PARAMSET)]
    dev = FakeNKT(registers)
    dev.connect()
    assert dev.wavelength_offset_limits_pm == WAVELENGTH_OFFSET_LIMITS_FALLBACK_PM
    assert dev.wavelength_offset_limits_source == 'fallback'
    # An implausible parameter set -> fallback
    registers = healthy_registers()
    registers[(BASIK, BasikReg.WAVELENGTH_OFFSET_PARAMSET)] = paramset(0, 0)
    dev = FakeNKT(registers)
    dev.connect()
    assert dev.wavelength_offset_limits_source == 'fallback'
    # A malformed config pair is refused at connect
    with pytest.raises(DeviceError):
        connected(WavelengthOffsetLimits_pm=[100, -100])


def test_read_readings_key_sets():
    dev = connected()
    fast = dev.read_readings(include_slow=False)
    assert set(fast) == {
        'offset_setpoint_pm', 'offset_readout_pm', 'wavelength_nm',
        'status_bits', 'status', 'status_word', 'emission', 'setup_bits',
        'setup', 'external_modulation', 'wide_modulation_range',
        'dc_coupled_modulation', 'time'}
    assert fast['offset_setpoint_pm'] == pytest.approx(12.3)
    assert fast['status_word'] == 'ok' and fast['emission']
    assert fast['external_modulation'] and fast['dc_coupled_modulation']
    assert fast['wide_modulation_range']
    full = dev.read_readings()
    assert set(full) - set(fast) == {
        'power_mw', 'temperature_c', 'supply_voltage_v', 'error_code',
        'system_status_bits', 'system_status'}
    assert full['system_status']['emission']
    assert not dev.writes


def test_not_connected_raises():
    dev = FakeNKT()
    with pytest.raises(DeviceError, match='Not connected'):
        dev.get_emission()
    dev.connect()
    dev.close()
    assert not dev.device_connected
    with pytest.raises(DeviceError, match='Not connected'):
        dev.read_readings()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
