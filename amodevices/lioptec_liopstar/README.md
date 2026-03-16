# LIOP-TEC LiopStar-E dye laser

Driver for the [LIOP-TEC LiopStar-E](https://www.liop-tec.com/) dye laser.

## Communication

The driver communicates with the **LiopStar Control** software (tested with v4)
running on the laser PC via TCP/IP. Commands and responses are ASCII strings
terminated by CR/LF. The default port is 65510. The protocol is specified in
the LIOP-TEC document "TCP-IP remote communication protocol" (2012).

`Get*` commands (status and position queries) do not require a remote
connection. All other commands (wavelength moves, scans, home) require calling
`remote_connect()` first.

## Device configuration dict

```python
device = {
    'Device': 'LIOP-TEC LiopStar-E',         # human-readable name (str)
    'Address': '192.168.1.100',               # hostname or IP of the LiopStar Control PC
    'Port': 65510,                            # optional, TCP port (default 65510)
    'Timeout': 5.,                            # optional, socket timeout in seconds (default 5)
    'GratingParamsXML': 'path/to/file.xml',  # optional, path to LiopStar calibration XML file
}
```

'GratingParamsXML' is optional but required for wavelength read-back
(`get_wavelength()`) and calibration-based move completion detection. The XML
file is the LabVIEW configuration file shipped with each dye/grating
combination or saved by LiopStar Control. Alternatively, load it manually with
`load_grating_params_from_xml()` and pass the result as `'GratingParams'`
in the device dict.

## API

### Connection

```python
dev = LioptecLiopStar(device)
dev.connect()           # open TCP socket
dev.remote_connect()    # acquire remote control (required for move commands)

# ... use device ...

dev.remote_disconnect()
dev.close()
```

### Wavelength tuning

```python
# Non-blocking: returns immediately
dev.set_wavelength(563.5)
dev.wait_for_move_complete(timeout=10.)

# Blocking convenience method (equivalent)
dev.set_wavelength_and_wait(563.5, timeout=10.)
```

### Status and position

```python
dev.get_status()            # 'OK', 'MOVING', 'SCAN', 'HOME', 'CALIB', or 'ERROR'
dev.get_actual_position()   # {'Resonator': <steps>, 'FCU1': <steps>, 'FCU2': <steps>}
dev.get_wavelength()        # wavelength in nm (requires 'GratingParams')
dev.get_error()             # error string from control software
dev.acknowledge_error()
```

### Scans

```python
# Table-based scan
dev.set_scan_table(n_shots=10, wavelengths=[560.0, 561.0, 562.0])
dev.start_scan()

# Parametric scan (upload + start in one command)
dev.start_scan_param(n_shots=10, start_nm=560., stop_nm=570., increment_nm=0.5)

dev.stop_scan()
```

### Other

```python
dev.move_home()                 # move all drives to home position
dev.stop_drives()               # immediately halt all drives
dev.wait_for_ready(timeout=30.) # block until status is 'OK'
```

### Move completion detection

`wait_for_move_complete()` has two modes:

- **With calibration** ('GratingParams' set and `target_wavelength_nm`
  passed): polls position and status; returns when the resonator has reached
  the target step count and status is 'OK'. Handles all move sizes including
  tiny moves where status never transitions to 'MOVING'.
- **Without calibration**: two-phase status polling. Phase 1 waits up to
  `MOVE_START_TIMEOUT` (200 ms) for status to leave 'OK'; returns immediately
  if it never does (already at target). Phase 2 waits for status to return
  to 'OK'.

`set_wavelength_and_wait()` automatically uses the calibration-based path when
'GratingParams' is available.

## Dependencies

- Python standard library only (`socket`, `xml.etree.ElementTree`, `math`, `re`)
