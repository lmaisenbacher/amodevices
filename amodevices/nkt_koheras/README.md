# NKT Photonics Koheras ADJUSTIK (K822)

Driver for the [NKT Photonics Koheras ADJUSTIK](https://www.nktphotonics.com/products/single-frequency-fiber-lasers/koheras-adjustik/)
single-frequency fiber laser system: the K822 mainboard and the Koheras BASIK
K1x2 fiber laser module inside it. Pure Python — no NKT DLL, no pylablib.

## Communication

NKT Photonics devices speak the **Interbus** register protocol (`interbus.py`),
documented in the "NKT Photonics SDK Instruction manual" (SDK 2.1.16, chapter 2):

- Serial 115200 bit/s, 8 data bits, 1 stop bit, no parity, over the ADJUSTIK's
  front USB port — a Silicon Labs CP210x virtual COM port (driver from
  silabs.com; `NKTKoherasAdjustik.find_ports()` lists the CP210x ports). The
  Ethernet port on the back carries the same telegrams over TCP (port 10001,
  `'Interface': 'tcp'`).
- A telegram is `[0x0D][message][0x0A]`; the message is destination address,
  source address, message type, register number, 0..240 data bytes (multi-byte
  values little-endian), and a big-endian CRC-16 CCITT (polynomial 0x1021,
  initial value 0, "XModem"). After the CRC is appended, the bytes 0x0A, 0x0D,
  and 0x5E anywhere in the message are replaced by 0x5E followed by the byte
  plus 0x40.
- Message types 4 (read) and 5 (write) from the host; a module answers a read
  with a datagram (type 8, `[register, data...]`), a write with an ack (type
  3), and an unknown register with a nack (type 0). Types 6/7/9 set, clear,
  and toggle bits of a register.
- Module addresses are 1..160; the host uses an address above 160 and, by
  default, cycles it per request so a late reply is recognized and dropped.

The system holds two modules on one port: the **BASIK K1x2** laser module
(type 0x33) at address 1 and the **ADJUSTIK mainboard** (type 0x34) at address
128. `connect()` verifies both types; a missing mainboard is a warning (its
functions then raise), a wrong module at the BASIK address a `DeviceError`.

NKT Photonics CONTROL must be closed while this driver holds the COM port.

The manual's worked telegram examples are the test vectors of `test_interbus.py`;
`test_driver_fake_transport.py` drives the driver on a fake register bus.

## Device configuration dict

```python
device = {
    'Device': 'NKT Koheras ADJUSTIK',
    'Address': 'COM7',                  # serial port, or the host with 'Interface': 'tcp'
    'Interface': 'serial',              # optional, 'serial' (default) or 'tcp'
    'Port': 10001,                      # optional, TCP port (default 10001)
    'Timeout': 0.5,                     # optional, reply timeout per transaction in s (default 0.5)
    'Retries': 3,                       # optional, repeats after a timeout, Busy, or CRC error (default 3)
    'SerialConnectionParams': {'rtscts': False},   # optional, pyserial arguments merged over the defaults
    'BasikAddress': 1,                  # optional (default 1)
    'MainboardAddress': 128,            # optional (default 128)
    'WavelengthOffsetLimits_pm': [-289, 349],      # optional, overrides the module's parameter set
}
```

The serial defaults are 115200 8N1 with RTS/CTS off (pyserial asserts RTS on
open, so the device is free to answer; with the handshake on, a port whose CTS
the device never drives stalls every write). The SDK recommends RTS/CTS for a
complete system — set `{'rtscts': True}` if a bare port shows no replies.

## API

### Connection

```python
dev = NKTKoherasAdjustik(device)
dev.connect()         # opens the port, verifies the modules, caches the constants
dev.close()
```

After `connect()`: `dev.serial_number`, `dev.firmware_version`,
`dev.mainboard_present`, `dev.wavelength_offset_limits_pm` (min, max) and
`dev.wavelength_offset_limits_source` (`'config'`, `'paramset'`, or
`'fallback'`). `connect()` warns when the mainboard watchdog is set (with it
on, a pause in the communication switches emission off).

### BASIK module

| Method | Register | Description |
|--------|----------|-------------|
| `get_emission()` / `set_emission(on)` | 0x30 | Laser emission on/off |
| `get_setup_bits()` / `get_setup()` | 0x31 | Setup bits, raw / decoded (`BASIK_SETUP_BITS`) |
| `set_external_modulation(on)`, `set_dc_coupled_modulation(on)`, `set_wide_modulation_range(on)`, `set_internal_modulation(on)`, `set_modulation_output(on)`, `set_pump_constant_current(on)` | 0x31 | One setup bit each (Write SET1 / CLR1) |
| `get_wavelength_offset_setpoint_pm()` / `set_wavelength_offset_setpoint_pm(pm)` | 0x2A | Thermal wavelength offset setpoint (pm, 0.1 pm resolution); a value outside the limits is refused, never clamped; returns the value written |
| `get_wavelength_offset_readout_pm()` | 0x72 | Measured/calculated wavelength offset (pm) |
| `get_standard_wavelength_nm()` | 0x32 | Wavelength at offset 0 (nm) |
| `get_wavelength_nm()` | 0x32 + 0x72 | Absolute wavelength (nm) |
| `get_status_bits()` / `get_status()` | 0x66 | Status bits, raw / decoded (`BASIK_STATUS_BITS`) |
| `get_error_code()` | 0x67 | Error code (0 = none) |
| `get_output_power_mw()` / `get_output_power_dbm()` | 0x17 / 0x90 | Output power readout |
| `get_power_setpoint_mw()` / `set_power_setpoint_mw(mw)` | 0x22 | Output power setpoint |
| `get_module_temperature_c()` | 0x1C | Module temperature (°C) |
| `get_supply_voltage_v()` | 0x1E | Module supply voltage (V) |
| `get_wavelength_modulation_frequency_hz()`, `..._level_permille()`, `..._offset_permille()`, `get_modulation_setup_bits()` | 0xB8, 0x2B, 0x2F, 0xB7 | Internal modulation generator readouts |
| `read_readings(include_slow=True)` | | One poll as a dict (see the docstring); the fast set is four transactions |

`status_word(status_bits)` maps the status register to the fleet's status
vocabulary (`amodevices.status`): `'ok'`, `'emission_off'`, or the lowest set
problem bit's word (`STATUS_TEXT`).

### ADJUSTIK mainboard

| Method | Register | Description |
|--------|----------|-------------|
| `get_system_status_bits()` / `get_system_status()` | 0x66 | System status, raw / decoded (`MAINBOARD_STATUS_BITS`) |
| `get_mainboard_supply_voltage_v()` | 0x11 | Internal supply voltage (V) |
| `reset_interlock()` | 0x32 | Reset the interlock circuit (door interlock closed, key on, bus loop closed) |
| `get_watchdog_s()` / `set_watchdog_s(s)` | 0x34 | Communication watchdog (0 = off) |
| `set_emission_broadcast(on)` | 0x30 | Emission on/off in all laser modules |
| `get_mainboard_modulation_setup_bits()` / `..._setup()` | 0x3B | Modulation setup, raw / decoded |
| `set_wavelength_modulation_on(on)`, `set_amplitude_modulation_on(on)` | 0x3E, 0x3F | Modulation on/off in all laser modules |
| `set_wavelength_offset_broadcast_pm(pm)` | 0x2D | Wavelength offset setpoint to all laser modules |

### Lab helpers

`scan_addresses()` (module type per answering address; `connect(verify=False)`
opens the port without the module check first), `dump_registers(address,
registers)` (raw bytes, `None` for a nack), `find_ports()` (CP210x serial
ports). `test.py` wraps them for the first contact with a laser: `--list-ports`,
`--scan`, `--dump`, `--step <pm>`.

### Wavelength offset limits

The setpoint range comes from the config (`'WavelengthOffsetLimits_pm'`), else
from the module's parameter-set record of register 0x2A (register 0x5A, the
record NKT CONTROL reads for its slider; its layout is not in the manual and is
parsed defensively), else from the fallback (−289, +349) pm — the limits the
lab's earlier Majel server used for this laser.

## Dependencies

- `pyserial` (already a package dependency)
