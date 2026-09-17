# Ophir EA-1 Ethernet adapter

Driver for the [Ophir EA-1](https://www.ophiropt.com/en/f/ethernet-virtual-power-meter)
Ethernet adapter and the Ophir smart sensor head plugged into it. Developed
and verified with a PE50BF-DFH-C pyroelectric energy head; the command set
is the adapter's, so other heads work for what they support (a thermal head
has no pulse stream and rejects the pyro-only commands).

## Communication

The adapter speaks the "User Commands" of the EA-1 user manual (chapter 6)
over TCP port 23 (Telnet), and the same commands over UDP port 11000 and
HTTP, which this driver does not use.

- On connect the adapter sends Telnet negotiation bytes (`\xff\xfd\x24\xff\xfb\x01`,
  IAC sequences of three bytes); the driver strips them wherever they occur.
- Commands are ASCII, `$` plus a two-letter code and optional parameters,
  terminated by CR LF. A reply starts with `*` (success) or `?` (error) and
  ends with CR LF, followed by a `>` prompt. The prompt is not a terminator:
  it precedes the next reply on the same line, and can occur inside a reply
  body. Some replies carry a stray LF before the CR LF (`*3\n\r\n>`). The
  driver turns the command echo off (`$EE 0`) at connect.
- Per-pulse readout is the streaming mode `$CS 3`: the adapter answers
  `*STARTED` and then pushes one line per measured pulse,
  `*<pulse index> <timestamp us> <energy J>`. The index counts every pulse
  the adapter measured (a gap means pulses that never reached the host), the
  timestamp is the adapter's own clock at 1 us resolution; both are 32-bit
  counters that wrap (the timestamp every 71.6 min) and are unwrapped by the
  driver within a stream session; the timestamp with the host clock as the
  guide, because a pause longer than half the wrap period that straddles a
  wrap would otherwise read as a step back of up to 36 min (only a counter
  reset on the adapter still shows as a jump). `$CS 1` stops the
  stream (`*STOPPED`, possibly after further pulse lines), and so does any
  other command: settings are read or changed between streams only, and
  `_query()` refuses to run while a stream is up.
- The polled readout (`$EF` new-value flag, cleared by `$SE`) is the same
  read-and-clear flag a Thorlabs PM100 has and is rated at about 10 Hz;
  `read_energy_polled()` offers it for diagnostics.
- `$UT` (threshold) answers with three integers, indistinguishable from a
  pulse line, which is why the driver never skips "pulse-shaped" lines while
  waiting for a reply; the command channel is kept clean by stopping and
  draining the stream before any command.

StarLab must be closed while this driver holds the connection: the adapter
accepts one client.

## Device configuration dict

| Key | Meaning | Default |
| --- | --- | --- |
| `'Device'` | Name used in log and error messages | |
| `'Address'` | IP address or host name of the adapter | |
| `'Port'` | TCP port | 23 |
| `'Timeout'` | Reply timeout per command (s) | 2.0 |
| `'ConnectTimeout'` | TCP connect timeout (s) | 5.0 |

The constructor does no I/O; call `connect()`. Every send and receive holds
one re-entrant lock, so a settings query and the streaming reader can live on
different threads.

## API

Identity after `connect()`: `firmware_version` (`'EA1.17'`), `adapter_type`,
`adapter_serial`, `adapter_name`, `head_type` (`'PY'` for a pyroelectric
head), `head_serial`, `head_name` (`'PE50BF-DFH-C'`), `head_extra`.

Settings (SI throughout: energies in J, pulse lengths in s, wavelengths in
nm, thresholds as fractions of the full-scale energy):

| Method | Command | Notes |
| --- | --- | --- |
| `get_measurement_mode()` | `$MM` | `MEASUREMENT_MODE_ENERGY` (3) while measuring energy |
| `force_energy_mode()` / `force_power_mode()` | `$FE` / `$FP` | |
| `get_ranges()` | `$AR` | `(current index, ranges in J)`, index 0 the highest |
| `get_range_index()` / `set_range_index(i)` | `$RN` / `$WN` | the head settles for seconds after a change |
| `range_index_for_energy(j)` | | the smallest range that holds `j` |
| `get_wavelength_info()` | `$AW` | dict with `mode`, `min_nm`, `max_nm`, `active`, `favorites_nm` |
| `get_wavelength_nm()` / `set_wavelength_nm(nm)` | `$AW` / `$WL` | the active favorite slot; checked against the calibrated span |
| `select_wavelength_favorite(slot)` | `$WI` | slots 1-6 |
| `get_pulse_lengths()` / `set_pulse_length_index(i)` | `$PL` | `(current index, lengths in s)`; the index is 1-based, unlike the range index |
| `get_threshold()` / `set_threshold(fraction)` | `$UT` | `(current, minimum, maximum)`; the device counts in 1/10000 |
| `get_diffuser()` | `$DQ` | text; `'1 N/A'` without a diffuser |
| `save_settings()` | `$HC S` | present settings become the power-up defaults |
| `read_energy_polled()` | `$EF`, `$SE` | `(new_value, energy_j, status)`; diagnostics only |

Streaming:

| Method | Notes |
| --- | --- |
| `start_stream()` | `$CS 3`; resets the counter unwrapping |
| `read_pulses(timeout_s)` | every complete line now or within `timeout_s`, as `Pulse` records; `[]` on a quiet stream, and at once when no stream runs |
| `stop_stream()` | `$CS 1`; returns the pulses that arrived before `*STOPPED`; drains the channel |
| `streaming` | property |

```python
Pulse = namedtuple('Pulse', 'index timestamp_us energy_j status t_recv raw')
```

`index` and `timestamp_us` are unwrapped (None for an unparseable line),
`energy_j` is None for an over-range or unparseable line, `status` is
`'ok'`, `'overexposed'` or `'unparseable'` (the fleet vocabulary of
`amodevices.status`), `t_recv` is `time.time()` right after the `recv` that
completed the line, `raw` the line as received. A line the driver does not
understand is returned as `'unparseable'` with its text rather than dropped,
so an unforeseen format shows up in a log instead of as a missing pulse.

## Verified and unverified

Verified on the live adapter (firmware EA1.17, laser off, 2026-09-17): the
banner, every reply quoted in `test_driver_fake_transport.py`, the polled
`$EF`/`$SE` pair, `$CS 3` -> `*STARTED`, silence without pulses, `$CS 1` ->
`*STOPPED`, and the command channel afterwards. A client that closes
cleanly (`close()`, which stops the stream) can reconnect at once. A client
killed while streaming leaves the adapter holding the dead TCP session: it
REFUSES new connections on port 23 for about ten minutes (the UDP command
port is silent too; only the HTTP page answers), then accepts again. A
server with automatic reconnection recovers on its own after that; a
`$RE` reset through the HTTP interface (`http://<ip>/?COMMAND=%24re`) is
the untested way to shorten it.

Not yet seen, because only a firing laser can show them (run `test.py` per
its docstring the first day it does): the per-pulse line itself, how an
over-range pulse appears in mode 3 (a third token `OVER` is assumed), whether
the index and the timestamp continue across a stream restart, whether pulses
fired while the stream was stopped are reported on restart, and whether an
adapter that lost its client mid-stream keeps pushing pulses into the next
connection (`connect` bounds its banner wait and sends `$CS 1` regardless).

`test_driver_fake_transport.py` drives the driver against a fake socket
scripted with the live replies; `test.py` is the manual lab script.
