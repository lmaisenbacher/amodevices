# Keysight InfiniiVision X-Series oscilloscopes (DSO-X, MSO-X)

Driver for Keysight (Agilent) InfiniiVision 2000/3000 X-Series oscilloscopes.
The SCPI command set is documented in the Keysight InfiniiVision 2000 X-Series
Oscilloscopes Programmer's Guide, publication 9018-06893 (2024-02-01); page
references below are to that guide. Tested with an MSO-X 2024A. Moved from
the MPQ package `pyhs` (`pyhs.devices.DSOX`), whose `.npz` files
`load_waveforms()` still reads.

## Communication

VISA. Over LAN the resource name is `TCPIP0::<host>::inst0::INSTR` (VXI-11);
USB (USBTMC) works with its own resource name. A LAN address the VISA library
does not enumerate (only addresses registered in NI MAX are) is opened
directly.

## Device configuration dict

```python
device = {
    'Device': 'Keysight MSO-X 2024A',                 # human-readable name (str)
    'Address': 'TCPIP0::192.168.50.29::inst0::INSTR', # VISA resource name
    'Timeout': 4.,                                    # optional, VISA timeout in seconds
}
```

The connection is opened by the constructor, which also fixes the waveform
transfer encoding (signed 16-bit little-endian WORD data) and selects the raw
acquisition record with all its points (`waveform_points_mode = 'RAW'`,
`waveform_points = 'MAXimum'`).

## API

### Connection and identity

```python
scope = KeysightDSOX(device)
scope.manufacturer, scope.model, scope.serial_number, scope.firmware  # from '*IDN?'
scope.system_error      # next entry of the error queue: (code, message); code 0 = empty
scope.check_errors()    # drain the error queue, DeviceError listing any errors
scope.visa_write(cmd); scope.visa_query(query)   # escape hatch for any other SCPI
scope.close()
```

### Waveform records

```python
record = scope.read_waveforms([1, 2], names=['Cav. trans. PD', 'Cav. piezo'])
record['time']       # (N,) float64, seconds
record['data']       # (n_channels, N) float64, volts
record['channels']   # [1, 2]
record['metadata']   # dict (JSON-serializable, PascalCase keys with unit suffixes)

t, v = scope.channel(1).read_waveform()   # one channel, no metadata
scope.channel(1).preamble                 # the scaling preamble (dict) plus 'type'
```

The metadata carries `Manufacturer`, `Model`, `SerialNumber`, `Firmware`,
`Timestamp` (ISO 8601 with UTC offset, PC clock at the read), `RunState`,
`AcquisitionType` (`NORM`, `PEAK`, `AVER`, `HRES`), `AverageCount`,
`PointsMode`, `NPoints`, `NValues`, `XIncrement_s`, `XOrigin_s`,
`TimebaseScale_s`, `TimebasePosition_s`, and per channel `Channels`,
`ChannelNames`, `ChannelUnits`, `ChannelScales_V`, `ChannelOffsets_V`,
`ChannelCouplings`, `YIncrements_V`, `YOrigins_V`, `YReferences`. Add your
own keys (a comment, `scope.trigger.settings`) before saving.

Voltages follow the guide's scaling (p. 731):
`voltage = (raw - yreference) * yincrement + yorigin`,
`time = (index - xreference) * xincrement + xorigin`. In peak-detect
acquisitions the record holds two values (min, max) per point (p. 731), so
`NValues` is twice `NPoints` and each time value appears twice.

### Acquisition control

```python
scope.run(); scope.stop(); scope.single()
scope.state             # 'RUN', 'STOP', or 'SING' (single armed, waiting for the trigger)
scope.running           # True until an armed acquisition has completed
scope.armed, scope.triggered   # one-shot event registers (clear on read)
scope.wait_for_stop(timeout)   # poll until stopped, DeviceError on timeout
record = scope.acquire_single([1], timeout=30., names=['Cav. trans. PD'])
scope.digitize(1, 2)    # ':DIGitize': blocks the interface until done, see below
scope.acquire.type      # 'NORMal', 'AVERage', 'HRESolution', 'PEAK'
scope.acquire.count     # averages
scope.acquire.points    # points the hardware acquires (read-only)
scope.acquire.mode      # 'RTIMe' or 'SEGMented'
scope.waveform_points_mode   # 'NORMal', 'MAXimum', 'RAW'
scope.waveform_points        # 100, 250, 500, 1000, ... or 'MAXimum'
```

`acquire_single()` arms a single acquisition, polls the run state until the
oscilloscope stops (the guide's polling wait, p. 886), and reads the record;
on a timeout it stops the acquisition and raises `DeviceError`. Set
`scope.trigger.sweep = 'NORMal'` first: in `AUTO` sweep mode the oscilloscope
triggers itself when no trigger arrives (a warning is logged).

### Trigger

```python
scope.trigger.mode       # 'EDGE', 'DELay' (edge then edge), ...; query returns 'DEL'
scope.trigger.sweep      # 'AUTO' or 'NORMal'
scope.trigger.holdoff    # s
scope.trigger.force()
scope.trigger.level(source)             # V; ':TRIGger:EDGE:LEVel? <source>'
scope.trigger.set_level(level, source)  # source: channel number or 'CHAN2', 'EXTernal'
scope.trigger.edge.source, .slope, .coupling
scope.trigger.delay.arm_source, .arm_slope        # edge-then-edge trigger
scope.trigger.delay.trigger_source, .trigger_slope, .trigger_count, .delay_time
scope.trigger.settings   # flat dict snapshot for records
```

The edge-then-edge trigger's arming and trigger edge levels are the sources'
edge trigger levels, set with `:TRIGger:EDGE:LEVel` (p. 665);
`:TRIGger:LEVel:HIGH`/`LOW` belong to the runt and transition triggers only.
`trigger.settings` returns `TriggerMode`, `TriggerSweep`, `TriggerHoldoff_s`
and, in mode `DEL`, `TriggerArmSource`, `TriggerArmSlope`, `TriggerArmLevel_V`,
`TriggerSource`, `TriggerSlope`, `TriggerLevel_V`, `TriggerCount`,
`TriggerDelayTime_s` (in mode `EDGE`: `TriggerSource`, `TriggerSlope`,
`TriggerCoupling`, `TriggerLevel_V`). Levels are NaN for digital sources.

### Channels and timebase

```python
channel = scope.channel(1)
channel.scale, channel.offset      # V/div, V
channel.coupling                   # 'AC' or 'DC'
channel.display                    # bool; waveforms can only be read from displayed channels
channel.label, channel.probe
scope.timebase.scale, .position, .range, .mode
```

### Files

```python
KeysightDSOX.save_waveforms(path, record)   # .npz: Time, Data, Channels, Metadata (JSON text)
record = KeysightDSOX.load_waveforms(path)  # new layout or the legacy pyhs layout
```

`save_waveforms()` writes a compressed NumPy archive with the arrays `Time`
(s), `Data` (V, one row per channel), `Channels`, and `Metadata`, the
metadata dict as a JSON string in a 0-d array, so loading needs no pickling.
`load_waveforms()` returns the record dict for such files and for the legacy
pyhs layout (`rawData` with the time axis in column 0 and a pickled `params`
dict): the legacy metadata is the `params` dict verbatim plus `Channels`,
`ChannelNames`, and `ChannelUnits` derived from its `Columns`.

## Examples and tests

- `example_read.py`: read the current trace, save it, and plot it.
- `example_plot.py`: plot a saved trace file (`python example_plot.py <file.npz>`).
- `test.py`: smoke test against the lab's scope (identity, error queue,
  trigger settings, one acquisition, save/load round trip).
- `test_waveform_decoding.py`, `test_driver_fake_transport.py`: pytest,
  no hardware needed.

## Notes

- The raw acquisition record (points mode `RAW`) can only be transferred
  while the oscilloscope is stopped; while running the measurement record is
  returned instead (p. 742). `read_waveforms()` logs a warning in that case
  and records the run state in the metadata. Bench-verified on an MSO-X 2024A
  (firmware 02.65): the same acquisition gave 60000 points while running and
  480000 once stopped; a requested `RAW` mode always reads back as `MAX`; and
  `PointsAcquired` (500000 there) also counts acquisition memory beyond the
  displayed window, which is never transferred (p. 740).
- `:DIGitize` blocks the instrument's interface until the acquisition
  completes, so any query sent meanwhile hits the VISA timeout (p. 888).
  `acquire_single()` (`:SINGle` plus polling) never blocks.
- The preamble's acquisition-type code is documented inconsistently (p. 728
  vs p. 744); the driver takes the type text from `:WAVeform:TYPE?` instead.
- In WORD format the values 0x0000, 0x0100, and 0xFF00 mark holes and
  clipped points (p. 737); the driver does not interpret them.
