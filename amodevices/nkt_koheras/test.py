# -*- coding: utf-8 -*-
"""
Manual lab checks of the NKT Koheras ADJUSTIK driver (not a pytest
file): the first contact with a laser over its USB port, before the
pydase server is deployed. Close NKT Photonics CONTROL first — a COM
port has one client.

    python test.py --list-ports            # CP210x serial ports on this PC
    python test.py -p COM7 --scan          # module types per bus address
    python test.py -p COM7 --dump          # read-only register dump, decoded
    python test.py -p COM7 --step 1.0      # offset +1.0 pm for 60 s, then back

`--scan` expects {1: 0x33, 128: 0x34}. No replies at all: retry with
`--rtscts` (the SDK recommends the handshake for a complete system).
`--dump` is the register set to compare with NKT CONTROL's readouts
(offset setpoint and readout, wavelength, power, temperature, setup
bits) and to record the parameter-set bytes of register 0x5A and the
watchdog value (must be 0). `--step` needs the cavity lock running and
the lockbox dashboard open: note the direction and size of the OUT1
excursion (the sign and magnitude of the plant gain) and that the lock
holds.

@author: Lothar Maisenbacher/UC Berkeley
"""

import argparse
import logging
import struct
import time

from amodevices.dev_exceptions import DeviceError
from amodevices.nkt_koheras.nkt_koheras_adjustik import (
    NKTKoherasAdjustik, BasikReg, MainboardReg, BASIK_SETUP_BITS,
    BASIK_STATUS_BITS, MAINBOARD_STATUS_BITS, MAINBOARD_MODULATION_SETUP_BITS,
    decode_bits, status_word)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

#: The read-only dump: (address name, register, description, decoder)
DUMP = [
    ('BASIK', BasikReg.MODULE_TYPE, 'module type', 'u8'),
    ('BASIK', BasikReg.FIRMWARE, 'firmware', 'text'),
    ('BASIK', BasikReg.SERIAL_NUMBER, 'serial number', 'text'),
    ('BASIK', BasikReg.EMISSION, 'emission', 'u8'),
    ('BASIK', BasikReg.SETUP, 'setup bits', 'setup'),
    ('BASIK', BasikReg.WAVELENGTH_OFFSET_SETPOINT,
     'wavelength offset setpoint (0.1 pm)', 'i16'),
    ('BASIK', BasikReg.WAVELENGTH_OFFSET_READOUT,
     'wavelength offset readout (0.1 pm)', 'i32'),
    ('BASIK', BasikReg.STANDARD_WAVELENGTH, 'standard wavelength (0.1 pm)',
     'u32'),
    ('BASIK', BasikReg.STATUS, 'status bits', 'status'),
    ('BASIK', BasikReg.ERROR_CODE, 'error code', 'u8'),
    ('BASIK', BasikReg.OUTPUT_POWER_MW, 'output power (0.01 mW)', 'u16'),
    ('BASIK', BasikReg.MODULE_TEMPERATURE, 'module temperature (0.1 C)',
     'i16'),
    ('BASIK', BasikReg.SUPPLY_VOLTAGE, 'supply voltage (mV)', 'u16'),
    ('BASIK', BasikReg.WAVELENGTH_OFFSET_PARAMSET,
     'wavelength offset parameter set', 'paramset'),
    ('mainboard', MainboardReg.MODULE_TYPE, 'module type', 'u8'),
    ('mainboard', MainboardReg.STATUS, 'system status bits', 'system_status'),
    ('mainboard', MainboardReg.SUPPLY_VOLTAGE, 'supply voltage (mV)', 'u16'),
    ('mainboard', MainboardReg.WATCHDOG, 'watchdog (s)', 'u8'),
    ('mainboard', MainboardReg.MODULATION_SETUP, 'modulation setup bits',
     'modulation_setup'),
]

INT_FORMATS = {'u8': '<B', 'u16': '<H', 'i16': '<h', 'u32': '<I', 'i32': '<i'}


def decode(kind, data):
    """A register's bytes as text for the dump."""
    if data is None:
        return 'NACK'
    if kind in INT_FORMATS:
        fmt = INT_FORMATS[kind]
        size = struct.calcsize(fmt)
        if len(data) < size:
            return f'{data.hex(" ")} (short)'
        return str(struct.unpack(fmt, data[:size])[0])
    if kind == 'text':
        return data.split(b'\x00', 1)[0].decode('ascii', 'replace')
    if kind in ('setup', 'status', 'system_status', 'modulation_setup'):
        if len(data) < 2:
            return f'{data.hex(" ")} (short)'
        value = int.from_bytes(data[:2], 'little')
        table = {'setup': BASIK_SETUP_BITS, 'status': BASIK_STATUS_BITS,
                 'system_status': MAINBOARD_STATUS_BITS,
                 'modulation_setup': MAINBOARD_MODULATION_SETUP_BITS}[kind]
        names = [name for name, on in decode_bits(value, table).items() if on]
        text = f'0x{value:04X} {names}'
        if kind == 'status':
            text += f' -> {status_word(value)}'
        return text
    if kind == 'paramset':
        return f'{len(data)} bytes: {data.hex(" ")}'
    return data.hex(' ')


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[1])
    p.add_argument('-p', '--port', default=None,
                   help='serial port (default: the first CP210x port found)')
    p.add_argument('--tcp', default=None,
                   help='use the Ethernet port at this host instead of USB')
    p.add_argument('--rtscts', action='store_true',
                   help='enable RTS/CTS handshake on the serial port')
    p.add_argument('--list-ports', action='store_true',
                   help='list the CP210x serial ports and exit')
    p.add_argument('--scan', action='store_true',
                   help='scan the bus addresses 1..255 for modules')
    p.add_argument('--dump', action='store_true',
                   help='dump and decode the read-only register set')
    p.add_argument('--step', type=float, default=None,
                   help='step the wavelength offset by this (pm) and back')
    p.add_argument('--hold', type=float, default=60.,
                   help='seconds to hold the step (default 60)')
    return p.parse_args()


def main():
    a = parse_args()
    if a.list_ports:
        ports = NKTKoherasAdjustik.find_ports()
        print('CP210x serial ports:', ports or 'none')
        return
    device = {'Device': 'NKT Koheras ADJUSTIK', 'Timeout': 0.5, 'Retries': 3}
    if a.tcp:
        device.update({'Interface': 'tcp', 'Address': a.tcp})
    else:
        port = a.port
        if port is None:
            ports = NKTKoherasAdjustik.find_ports()
            if not ports:
                raise SystemExit('No CP210x serial port found; pass --port')
            port = ports[0]
        device.update({'Address': port,
                       'SerialConnectionParams': {'rtscts': a.rtscts}})
    dev = NKTKoherasAdjustik(device)
    if a.scan:
        # A scan needs the port open, not a verified module
        try:
            dev.connect(verify=False)
        except DeviceError as e:
            raise SystemExit(f'connect failed: {e}')
        print('scanning addresses 1..255 (100 ms each, about 25 s)...')
        found = dev.scan_addresses()
        print('modules found:',
              {address: f'0x{module_type:02X}'
               for address, module_type in found.items()} or 'none')
        dev.close()
        return
    try:
        dev.connect()
    except DeviceError as e:
        raise SystemExit(f'connect failed: {e}')
    try:
        print(f'connected: serial number {dev.serial_number}, firmware '
              f'{dev.firmware_version}, standard wavelength '
              f'{dev.get_standard_wavelength_nm():.4f} nm, offset limits '
              f'{dev.wavelength_offset_limits_pm} pm '
              f'({dev.wavelength_offset_limits_source}), mainboard '
              f'{"present" if dev.mainboard_present else "absent"}')
        if a.dump:
            for module, register, description, kind in DUMP:
                address = (dev.basik_address if module == 'BASIK'
                           else dev.mainboard_address)
                if module == 'mainboard' and not dev.mainboard_present:
                    continue
                data = dev.dump_registers(address, [register])[int(register)]
                raw = 'NACK' if data is None else data.hex(' ')
                print(f'{module:9s} 0x{int(register):02X} {description:38s} '
                      f'{decode(kind, data):45s} [{raw}]')
        if a.step is not None:
            before = dev.get_wavelength_offset_setpoint_pm()
            target = before + a.step
            print(f'offset setpoint {before:.1f} pm -> {target:.1f} pm for '
                  f'{a.hold:.0f} s; watch OUT1 on the lockbox dashboard')
            try:
                written = dev.set_wavelength_offset_setpoint_pm(target)
                print(f'written {written:.1f} pm')
                t_end = time.monotonic() + a.hold
                while time.monotonic() < t_end:
                    time.sleep(5.)
                    r = dev.read_readings(include_slow=False)
                    print(f'  setpoint {r["offset_setpoint_pm"]:.1f} pm, '
                          f'readout {r["offset_readout_pm"]:.1f} pm, '
                          f'status {r["status_word"]}')
            finally:
                dev.set_wavelength_offset_setpoint_pm(before)
                print(f'offset setpoint restored to {before:.1f} pm')
    finally:
        dev.close()


if __name__ == '__main__':
    main()
