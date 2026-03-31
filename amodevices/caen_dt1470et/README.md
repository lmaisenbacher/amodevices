# CAEN R/DT14xxET and R/DT1570ET HV power supplies

Driver for the CAEN R14xxET/DT14xxET/R1570ET/DT1570ET family of high-voltage
power supplies. The ASCII command protocol is documented in CAEN user manual
UM3372 rev 20 (April 2024).

## Communication

The driver supports two connection modes:

- **Ethernet (TCP/IP):** default port 1470. The module must be configured with
  a static IP or DHCP. To enable DHCP, connect via USB serial, enter the CAEN
  terminal (type `CAEN`), select `E` for Ethernet settings, enable DHCP, save
  with `S`, and power-cycle the unit.
- **USB serial:** 9600 baud, 8N1, Xon/Xoff flow control. On Windows the device
  appears as a COM port; on Linux as `/dev/ttyACMx`.

## Device configuration dict

```python
device = {
    'Device': 'CAEN DT1470ET',         # human-readable name (str)
    'Address': '192.168.1.100',         # IP address (Ethernet) or COM port (USB)
    'Port': 1470,                       # optional, TCP port (default 1470)
    'Timeout': 5.,                      # optional, timeout in seconds (default 5)
    'ConnectionType': 'ethernet',       # optional, 'ethernet' or 'usb' (default 'ethernet')
    'BoardAddress': 0,                  # optional, module address 0-31 (default 0)
    'MaxVoltage': 30.,                  # optional, software-enforced voltage cap in V
}
```

'MaxVoltage' is a software safety limit enforced by `set_vset()`. If set, any
request exceeding this value raises `DeviceError` before a command is sent to
the hardware.

## API

### Connection

```python
dev = CAENDT1470ET(device)
dev.connect()    # open Ethernet or USB connection

# ... use device ...

dev.close()
```

### Channel monitoring

All channel methods take a zero-based `channel` integer.

```python
dev.get_vmon(channel)        # monitored output voltage (V)
dev.get_imon(channel)        # monitored output current (uA)
dev.get_vset(channel)        # voltage set point (V)
dev.get_iset(channel)        # current limit set point (uA)
dev.get_max_voltage(channel) # MAXV hardware protection limit (V)
dev.get_ramp_up(channel)     # ramp-up rate (V/s)
dev.get_ramp_down(channel)   # ramp-down rate (V/s)
dev.get_trip(channel)        # trip time in seconds (1000 = infinite)
dev.get_status(channel)      # raw status bit field (int)
dev.get_status_str(channel)  # list of active status flag names
dev.get_polarity(channel)    # '+' or '-'
dev.get_power_down(channel)  # 'RAMP' or 'KILL'
dev.get_imon_range(channel)  # 'HIGH' or 'LOW'
```

### Channel setting

```python
dev.set_vset(channel, voltage)        # set output voltage (V)
dev.set_iset(channel, current)        # set current limit (uA)
dev.set_on(channel)                   # turn channel on
dev.set_off(channel)                  # turn channel off
dev.set_ramp_up(channel, rate)        # set ramp-up rate (V/s)
dev.set_ramp_down(channel, rate)      # set ramp-down rate (V/s)
dev.set_trip(channel, time)           # set trip time (s); 1000 = infinite
dev.set_power_down(channel, mode)     # 'RAMP' or 'KILL'
dev.set_imon_range(channel, range)    # 'HIGH' or 'LOW'
dev.set_max_voltage(channel, voltage) # set MAXV hardware protection (V)
```

### Board monitoring

```python
dev.get_board_name()        # module name string
dev.get_num_channels()      # number of channels
dev.get_firmware_release()  # firmware release string
dev.get_serial_number()     # serial number (int)
dev.get_interlock_status()  # 'YES' or 'NO'
dev.get_interlock_mode()    # 'OPEN' or 'CLOSED'
dev.get_control_mode()      # 'LOCAL' or 'REMOTE'
dev.get_board_alarm()       # raw alarm bit field (int)
dev.get_board_alarm_str()   # list of active alarm flag names
```

### Board setting

```python
dev.set_interlock_mode(mode)  # 'OPEN' or 'CLOSED'
dev.clear_alarm()             # clear the board alarm signal
```

### Status flags

Channel status bits (from p. 30 of UM3372):

| Bit | Name   | Meaning                    |
|-----|--------|----------------------------|
| 0   | ON     | Channel is on              |
| 1   | RUP    | Channel is ramping up      |
| 2   | RDW    | Channel is ramping down    |
| 3   | OVC    | Over-current               |
| 4   | OVV    | Over-voltage               |
| 5   | UNV    | Under-voltage              |
| 6   | MAXV   | MAXV protection active     |
| 7   | TRIP   | Channel has tripped        |
| 8   | OVP    | Over-power protection      |
| 9   | OVT    | Over-temperature           |
| 10  | DIS    | Channel disabled           |
| 11  | KILL   | Channel killed             |
| 12  | ILK    | Interlock open             |
| 13  | NOCAL  | Not calibrated             |

Board alarm bits (from p. 31 of UM3372):

| Bit | Name     | Meaning                  |
|-----|----------|--------------------------|
| 0   | CH0      | Channel 0 in alarm       |
| 1   | CH1      | Channel 1 in alarm       |
| 2   | CH2      | Channel 2 in alarm       |
| 3   | CH3      | Channel 3 in alarm       |
| 4   | PWFAIL   | Power fail               |
| 5   | OVP      | Over-power protection    |
| 6   | HVCKFAIL | HV clock fail            |

## Dependencies

- `pyserial` (USB connection only)
- Python standard library (`socket`) for Ethernet
