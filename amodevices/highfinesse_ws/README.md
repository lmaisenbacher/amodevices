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

- `get_frequency()`: current frequency of channel 1 in THz; DLL return codes (<= 0) pass through unmapped. In 'ReadOnce' mode 0 (ErrNoValue) means "nothing new since the last read"; negative values are errors, e.g. -4 ErrBigSignal (overexposed), -1 ErrNoSignal.
- `GET_ERRORS`: the return codes of `GetFrequency`/`GetWavelength` mapped to their header identifiers (from `Data.h` of the wavemeter software 7.834.6533.007; the WS/7 manual section 4.1.2.2 misnumbers -5 to -8). `status_name(code)` looks one up ('Err<code>' when unknown).
- `STATUS_TEXT` / `status_text(code)`: the same codes as plain-word status strings for logging and display, following the fleet-wide vocabulary convention in `amodevices.status` (snake_case, `[a-z0-9_]`, at most 32 characters; `STATUS_OK` = 'ok' for a valid result, `STATUS_UNKNOWN` = 'unknown_error' for an unmapped code — the raw code belongs in a log line, never in the data). Examples: -4 → 'overexposed', -3 → 'underexposed', -1 → 'no_signal', -8 → 'no_pulse'.
- `classify_result(raw)`: a `get_frequency()`/`get_power()` return sorted into `(value, status)` — `(float, 'ok')` for a result, `(nan, None)` for 0 (nothing new) or None (not present), `(nan, status_text(code))` for an error code.
- `get_pulse_mode()` / `check_pulse_mode()`: measurement mode readout and configured-mode check.
- `get_exposures()` / `set_exposure_1()` / `set_exposure_2()` / `get_automatic_exposure()` / `set_automatic_exposure()`: exposure control of the two sensors.
- `get_amplitude(index)`: one amplitude of the last measurement's interference pattern in counts of the CCD array's ADC — the minimum, maximum or average height of the fringes on array 1 or 2 (`cMin1`, `cMin2`, `cMax1`, `cMax2`, `cAvg1`, `cAvg2`; the automatic exposure aims at a maximum of about 1000–3000 counts). A state readout: unaffected by 'ReadOnce', and it describes the pattern of the last result whether that was valid or an error. Returns a `SET_ERRORS` code (<= 0) on failure; `amplitude_value(raw)` gives counts as a float or NaN, `amplitude_error_name(code)` the header identifier. `get_levels()` is the pair of maxima.
- `get_power()`: the power (µW, CW) or pulse energy (µJ, pulsed modes) the wavemeter derived from the last measurement — relative, never calibrated, and measured behind the coupling fiber. Same return contract as `get_frequency()` (`classify_result`), including ErrNoValue (0) in 'ReadOnce' mode once the latest result has been requested; whether that flag is shared with the frequency read is not stated by the manual (the live `test.py` probes it: read the frequency first).
- `get_temperature()` / `get_pressure()`: the optical unit's temperature (°C) and air pressure (mbar, of the active pressure mode — a built-in sensor is not present on every unit). Error codes are ErrTemperature (−1000) plus a `GET_ERRORS` code (`ENVIRONMENT_ERRORS`: −1000 not measured yet, −1005 server gone, −1006 no sensor); `environment_value(raw)` gives the value or NaN, `environment_error_name(code)` the identifier.
- `get_pid_setpoint()` / `set_pid_setpoint()` / `get_pid_enabled()` / `set_pid_status()` / `get_pid_output_voltage()`: PID laser control (requires the wavemeter PID option).

`test_status_codes.py` (pure, runs under pytest) covers the code tables and the classifiers; `test.py` is the manual hardware test with the 'ReadOnce' probe.
