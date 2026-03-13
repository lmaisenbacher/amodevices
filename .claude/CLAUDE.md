# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`amodevices` is a Python package of laboratory hardware drivers for AMO (Atomic, Molecular, and Optical) physics experiments. It supports ~18 device types across multiple communication protocols.

**Install (development mode):**
```bash
pip install -e .
```

There are no automated tests, linting configs, or CI/CD pipelines. Testing is done manually by connecting to hardware.

## Architecture

### Base Class (`amodevices/dev_generic.py`)

All drivers inherit from `dev_generic.Device`, which provides:
- `device` (dict) — configuration dictionary passed at construction
- `device_present`, `device_connected` (bool) — connection state
- `ser` — serial connection object
- `visa_resource` — VISA resource object
- `serial_connect()`, `serial_close()`, `serial_write(command, encoding, eol)`
- `init_visa()`, `visa_write(cmd)`, `visa_query(query, return_ascii=False)`
- `to_float(value)`, `to_int(value)` — type conversion helpers
- Module-level `write_lock` (threading.Lock) for thread-safe serial writes

Custom exception: `DeviceError` in `amodevices/dev_exceptions.py`.

### Device Configuration Dict

All drivers take a `device` dict as first constructor argument:

```python
device = {
    'Device': 'Human-readable name',     # str
    'Address': 'VISA::...::INSTR',       # connection address
    'Timeout': 5.0,                       # optional, seconds
    'DeviceSpecificParams': {...},         # optional
    'VISAIDN': 'expected IDN string',     # optional, for VISA validation
}
```

### Communication Patterns

| Pattern | Example drivers | Key library |
|---------|----------------|-------------|
| VISA | `thorlabs_pm100`, `keysight_53220a`, `rigol_dg900pro` | `pyvisa` |
| Serial | `srs_sim922`, `srs_ctc100`, `kjlc_xcg` | `pyserial` |
| NI DAQmx | `ni_daq`, `hvs_controller`, `ni9264andni9205` | `nidaqmx` |
| Native SDK/DLL | `thorlabs_k10cr1`, `thorlabs_kpa101` | `ctypes` |
| USB + SDK | `flir_boson` | FLIR SDK + `opencv-python` |
| pylablib | `thorlabs_k10cr1` (alternate) | `pylablib` |

### Adding a New Driver

1. Create `amodevices/<manufacturer>_<model>/` with `__init__.py` and `<manufacturer>_<model>.py`
2. Inherit from `dev_generic.Device`
3. Implement `connect()` and `close()`, delegating to base class helpers where possible
4. Add the module to `amodevices/__init__.py` imports
5. Add any new dependencies to `setup.py` `install_requires`

### VISA-based Pattern

```python
class MyDevice(dev_generic.Device):
    def __init__(self, device, update_callback_func=None):
        super().__init__(device)
        self.init_visa()   # opens VISA connection, checks IDN

    def close(self):
        self.visa_resource.close()

    def get_value(self):
        return self.to_float(self.visa_query(':MEAS:VAL?'))
```

### Serial-based Pattern

```python
class MyDevice(dev_generic.Device):
    def connect(self):
        self.serial_connect()

    def close(self):
        self.serial_close()

    def query(self, command):
        self.serial_write(command, encoding='ASCII', eol='\n')
        return self.ser.readline().decode('ASCII').strip()
```

### NI DAQmx Pattern

```python
class MyDevice(dev_generic.Device):
    def __init__(self, config):
        self.ao_channels = config.get('AOChannels', {})
        self.ao_tasks = {ch: nidaqmx.Task() for ch in self.ao_channels}

    def connect(self):
        for ch, cfg in self.ao_channels.items():
            self.ao_tasks[ch].ao_channels.add_ao_voltage_chan(cfg['Channel'], ...)

    def set_voltage(self, channel, voltage):
        self.ao_tasks[channel].write(voltage)
```

## Documentation Style

In docstrings and comments, use backticks for code identifiers that have no
other syntactic marker — function names, class names, variable names, method
names:

> Raises `DeviceError` if the connection fails.
> Call `load_grating_params_from_xml()` to populate it.

String literals already carry their own marker (the quotes), so do **not**
add backticks around them:

> Update 'Address' to the IP address ...   ✓
> Update `'Address'` to the IP address ... ✗

In RST-formatted docstrings (Sphinx), use `:meth:`, `:class:`, `:func:`
roles for cross-references, and double backticks (`` ``value`` ``) for
inline code literals.  Plain `single backticks` are for parameter names.

## Key Files

- [amodevices/dev_generic.py](amodevices/dev_generic.py) — base `Device` class
- [amodevices/dev_exceptions.py](amodevices/dev_exceptions.py) — `DeviceError`
- [amodevices/__init__.py](amodevices/__init__.py) — package exports
- [setup.py](setup.py) — package metadata and dependencies
