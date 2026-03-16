# Thorlabs KPA101 beam position aligner

Driver for the [Thorlabs KPA101](https://www.thorlabs.com/thorproduct.cfm?partnumber=KPA101)
beam position aligner controller.

## Communication

The KPA101 communicates over a USB virtual serial port (FTDI FT232R chip) using
the Thorlabs APT binary protocol at 115200 baud with RTS/CTS flow control.
The driver uses **pyserial** directly — no pylablib or Kinesis DLL required.

The COM port is discovered automatically from the device serial number.
The Windows FTDI driver appends a channel-letter suffix to the serial number
stored in the chip EEPROM; for the single-port KPA101 this is always `'A'`
(e.g., serial number `69252254` → USB serial string `'69252254A'`).

## Device configuration dict

```python
device = {
    'Device': 'Thorlabs KPA101',
    'SerialNumber': 69252254,   # Thorlabs 8-digit serial number (int)
    'Timeout': 5.,              # optional, serial read timeout in seconds (default 5)
    'CacheInterval': 0.1,       # optional, minimum interval between device reads in seconds (default 0.1); set to 0 to disable caching
}
```

## API

### Connection

```python
dev = ThorlabsKPA101(device)
dev.connect()   # opens COM port, runs APT init sequence
dev.close()
```

### Detector readings

Readings are cached; a new request is sent only when the cache has expired
(interval set by 'CacheInterval', default 0.1 s; set to 0 to disable caching).

| Property | Description |
|----------|-------------|
| `xdiff` | X-axis difference signal (V) |
| `ydiff` | Y-axis difference signal (V) |
| `sum` | Sum signal (V) |
| `xpos` | X position (mm) — hardware-computed, valid for some sensor types |
| `ypos` | Y position (mm) — hardware-computed, valid for some sensor types |
| `xpos_pdp90a` | X position (mm) computed as `5 * xdiff / sum` — only valid for Thorlabs PDP90A |
| `ypos_pdp90a` | Y position (mm) computed as `5 * ydiff / sum` — only valid for Thorlabs PDP90A |

Voltage scaling: ±10 V maps to ±32767 (signed 16-bit integer from the device).

### Operation mode

```python
dev.operation_mode          # returns one of: 'monitor', 'open_loop',
                            #   'closed_loop', 'auto_loop'
dev.operation_mode = 'open_loop'
```

### Device information

```python
info = dev.get_device_info()
# DeviceInfo(serial_number=..., model='...', fw_version='...', hw_version=...,
#            num_channels=...)
```

## Dependencies

- `pyserial` (already a package dependency)
