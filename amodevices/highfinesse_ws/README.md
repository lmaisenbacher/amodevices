# HighFinesse WS series wavemeter

Device driver for HighFinesse WS series wavemeters. Tested with model WS/7.

## Communication

The driver communicates through the Windows DLL API (`wlmData.dll`) of the HighFinesse wavemeter software, which must be installed and running on the same PC — the DLL relays all calls to the running server application. The driver is therefore Windows-only at runtime; the module itself imports cleanly on other platforms and raises `DeviceError` at instantiation.

All calls address wavemeter channel 1 only; multichannel switch ("MC") units are not yet parameterized.

## Device configuration dict

```python
device = {
    # Human-readable device name (str), used in error messages
    'Device': 'HighFinesse WS/7 wavemeter',
    # Path to the wavemeter library (str, optional).
    # Default: 'C:\\Windows\\System32\\wlmData.dll'
    'Address': 'C:\\Windows\\System32\\wlmData.dll',
    # Report each measurement result only once (bool, optional, default False).
    # If True, until the wavemeter completes a new measurement, further calls
    # to `get_frequency` return ErrNoValue (0) instead of repeating the last
    # result. Use this to read each shot of a pulsed laser exactly once by
    # polling faster than the pulse repetition rate.
    'ReadOnce': False,
    # Expected measurement mode of the wavemeter software (int, optional):
    # 0 = continuous wave (CW), nonzero = a pulsed mode (numbering depends on
    # the device version, see WS/7 manual section 4.1.2.4). The mode is checked
    # (`check_pulse_mode` raises `DeviceError` on mismatch), never set: the
    # driver must not override a mode an operator chose in the wavemeter GUI.
    'PulseMode': 0,
    }
```

## API

Construction loads the DLL, registers with the wavemeter software (`Instantiate`), and runs an initial `check_pulse_mode`. The driver then exposes:

- `get_frequency()`: current frequency of channel 1 in THz; DLL error codes (<= 0, e.g. ErrNoSignal) pass through unmapped (see WS/7 manual section 4.1.2.2).
- `get_pulse_mode()` / `check_pulse_mode()`: measurement mode readout and configured-mode check.
- `get_exposures()` / `set_exposure_1()` / `set_exposure_2()` / `get_automatic_exposure()` / `set_automatic_exposure()`: exposure control of the two sensors.
- `get_levels()`: maximum amplitudes of the two sensors' interference patterns.
- `get_pid_setpoint()` / `set_pid_setpoint()` / `get_pid_enabled()` / `set_pid_status()` / `get_pid_output_voltage()`: PID laser control (requires the wavemeter PID option).
