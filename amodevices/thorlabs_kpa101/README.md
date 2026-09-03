# Thorlabs KPA101 beam position aligner

Driver for the [Thorlabs KPA101](https://www.thorlabs.com/thorproduct.cfm?partnumber=KPA101)
beam position aligner controller.

## Communication

The KPA101 communicates over a USB virtual serial port (FTDI FT232R chip) using
the Thorlabs APT binary protocol at 115200 baud with RTS/CTS flow control.
The driver uses **pyserial** directly — no pylablib or Kinesis DLL required.

The serial port is chosen one of two ways:

- **Discovery by serial number** (`'SerialNumber'` only): the port whose USB
  serial string matches is opened, on Windows and Linux alike. The string the
  OS reports differs by platform and both forms are accepted: the Windows
  FTDI driver appends a channel-letter suffix to the serial number stored in
  the chip EEPROM — always `'A'` for the single-port KPA101 (serial number
  `69252254` → `'69252254A'`) — while Linux's `ftdi_sio` reports the bare
  EEPROM string (`'69252254'`).
- **Direct port** (`'Address'`, e.g. `'COM20'` or `'/dev/ttyUSB0'`): the named
  port is opened and discovery is skipped. On Linux prefer the
  `/dev/serial/by-id/usb-...-if00-port0` symlink — it encodes the USB serial
  and is stable across enumeration order, unlike `/dev/ttyUSBn`. When
  `'SerialNumber'` is given as well, the driver reads the device's serial
  number after opening (APT `HW_REQ_INFO`) and refuses a mismatch, so a
  renumbered port that now belongs to another Thorlabs cube fails loudly
  instead of being read.

### Linux setup

- The FTDI latency timer defaults to 16 ms and caps the read rate at ~50 Hz;
  1 ms gives ~300 Hz reads (measured on Windows at the same setting). Set it
  with a udev rule, e.g. `/etc/udev/rules.d/99-thorlabs-apt.rules`:
  `ACTION=="add", SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTRS{idProduct}=="faf0", ATTR{latency_timer}="1"`
  (or transiently `echo 1 > /sys/bus/usb-serial/devices/ttyUSBx/latency_timer`).
- The service user needs access to `/dev/ttyUSB*` (typically the `dialout`
  group).

## Device configuration dict

```python
device = {
    'Device': 'Thorlabs KPA101',
    'SerialNumber': 69252254,   # Thorlabs 8-digit serial number (int); discovers the port, or verifies the device when 'Address' is given too
    'Address': 'COM20',         # optional, serial port to open directly (skips discovery); at least one of 'SerialNumber'/'Address' is required
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

`read_readings()` reads all detector signals in ONE request, bypassing the
cache, and returns them as a plain dict (keys `xdiff`, `ydiff`, `sum`,
`xpos`, `ypos`, `xpos_pdp90a`, `ypos_pdp90a`, and `time` — the epoch-second
read time). The PDP90A positions are NaN when the summed signal is zero
(beam blocked or sensor dark) — they never raise. This is the method for a
polling loop: N property reads cost up to N wire round trips, one
`read_readings()` always costs exactly one.

The properties below are cached; a new request is sent only when the cache
has expired (interval set by 'CacheInterval', default 0.1 s; set to 0 to
disable caching).

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
