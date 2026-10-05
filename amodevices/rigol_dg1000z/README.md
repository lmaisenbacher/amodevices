# Rigol DG1000Z series function generators

Driver for the Rigol DG1000Z series of two-channel function and arbitrary
waveform generators (DG1022Z, DG1032Z, DG1062Z). The SCPI commands are those
of the DG1000Z Programming Guide. The driver covers pulse waveforms played as
externally triggered bursts (one pulse per trigger edge at a channel's
rear-panel input), their levels, edges and widths, the burst idle level and
the outputs. Tested with a DG1062Z, firmware 03.01.12.

## Communication

VISA. Over USB the resource name is `USB0::0x1AB1::0x0642::<serial>::INSTR`;
with the pyvisa-py backend (`'VISABackend': '@py'`) the generator must be
bound to Windows' WinUSB driver (e.g. with Zadig) instead of a VISA vendor's
USBTMC driver. Over LAN use `TCPIP0::<host>::INSTR` (VXI-11).

## Device configuration dict

```python
device = {
    'Device': 'Rigol DG1062Z',                                  # name (str)
    'Address': 'USB0::0x1AB1::0x0642::DG1ZA000000000::INSTR',   # VISA resource name
    'VISABackend': '@py',                                       # optional
    'Timeout': 5.,                                              # optional, s
}
```

## API

```python
gen = RigolDG1000Z(device)
gen.identity, gen.model, gen.serial_number, gen.firmware   # from '*IDN?'

# One 10 ms pulse from 0 V to 3 V per rising trigger edge on CH1, the
# fastest edges, high-impedance load, resting low between pulses
gen.configure_triggered_pulse(1, width_s=10e-3, period_s=10.5e-3,
                              low_v=0., high_v=3.)
gen.set_pulse_width(1, 8e-3, 8.5e-3)   # width and period, in a safe order
gen.set_levels(1, 0., 3.3)             # low and high, in a safe order
gen.set_burst_idle(1, 'TOP')           # rest at the high level instead
gen.set_output(1, False)
gen.check_errors()                     # DeviceError listing any queued errors
gen.close()
```

`set(command)` sends any setting and queries it back.

## Behavior

Observed with a DG1062Z, firmware 03.01.12:

- Two commands in one message (joined by `;`) hang the generator; the driver
  sends one per message and queries each setting back before the next.
- `*OPC?` does not answer while a triggered burst is armed.
- A pulse's period and width are coupled through `PULS:HOLD`: a channel holds
  whichever of width and duty cycle it held last. Holding the duty cycle, a
  period change also changes the width. `configure_triggered_pulse` sets
  `PULS:HOLD WIDT`.
- A pulse is drawn from 8192 points per period: a set edge time comes out as
  steps of a period / 8192 (1.28 µs at a 10.5 ms period), and the edge is
  limited to 0.625 × (period − width).
- A burst whose trigger arrives while the previous burst is still playing does
  not start; the output holds the idle level until a further trigger arrives.
  A trigger during the burst delay (`BURS:TDEL`) is ignored.
- Each channel follows its own trigger input: two channels on one trigger
  signal stay in step only when both are armed before it arrives.
- Switching an output relay takes about 0.1 s, a change of the burst trigger
  source about 80 ms.
