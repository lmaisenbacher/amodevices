# amodevices

Drivers for AMO (atomic, molecular, and optical physics) laboratory devices.

## Installation

To install this package directly from this repository, use (with HTTPS)

```
pip install git+https://github.com/lmaisenbacher/amodevices.git
```
or (with SSH)
```
pip install git+ssh://git@github.com:lmaisenbacher/amodevices.git
```

Alternatively, the package can be install as a local copy, useful when developing. For this, clone this repository and run `pip install -e .` in the root directory (containing `setup.py`). The `-e` flag ensures that the files in the local copy of the repository are used when importing the package elsewhere and changes to these files will be directly visible, as opposed to a normal installation, where the package files are imported from a dedicated directory holding all installed packages (see [`pip install` documentation](https://pip.pypa.io/en/stable/cli/pip_install/)).

Additionally, to run the `NIDAQ` NI DAQ analog input/output you need to install National Instruments (NI) NI-DAQmx drivers. Further instructions can be found [here](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html).

## Supported devices

| Class | Device | Communication |
|-------|--------|---------------|
| [`CAENDT1470ET`](amodevices/caen_dt1470et/README.md) | CAEN R/DT14xxET, R/DT1570ET HV power supply | TCP/IP, USB serial |
| `FLIRBoson` | FLIR Boson thermal camera | FLIR Boson SDK |
| [`HighFinesseWS`](amodevices/highfinesse_ws/README.md) | HighFinesse WS series wavemeter | wlmData.dll (Windows DLL API) |
| `Keysight53220A` | Keysight 53220A universal counter | VISA |
| [`KeysightDSOX`](amodevices/keysight_dsox/README.md) | Keysight/Agilent InfiniiVision 2000/3000 X-Series oscilloscopes (DSO-X, MSO-X) | VISA |
| `KJLC354` | Kurt J. Lesker KJLC 354/352 and InstruTech IGM401/402 ion pressure gauges, KJLC 300 series Pirani pressure gauge | Serial (RS-485) |
| `KJLCACG` | Kurt J. Lesker KJLC ACG series capacitance manometer | Serial (RS-232) |
| `KJLCXCG` | Kurt J. Lesker KJLC Carbon XCG series pressure gauge (custom Arduino readout) | USB serial |
| `LeyboldMagDrive` | Leybold MAG.DRIVE S/iS frequency converter of the TURBOVAC MAG W P / MAG W iP turbo pumps (converter type 201): status, process values and parameters, the control word in every telegram, and a simulated drive (`sim_magdrive`) | RS-232 (pyserial, Leybold USS telegram) |
| [`LioptecLiopStar`](amodevices/lioptec_liopstar/README.md) | LIOP-TEC LiopStar-E dye laser | TCP/IP |
| `NIDAQ` | NI DAQ analog input/output | NI-DAQmx |
| [`NKTKoherasAdjustik`](amodevices/nkt_koheras/README.md) | NKT Photonics Koheras ADJUSTIK (K822) laser system with Koheras BASIK (K1x2) module | USB serial (pyserial, NKT Interbus), TCP/IP |
| [`OphirEA1`](amodevices/ophir_ea1/README.md) | Ophir EA-1 Ethernet adapter with an Ophir smart sensor head (PE50BF-DFH-C pyroelectric energy head): settings and per-pulse energy stream | TCP/IP (Telnet) |
| `RigolDG900Pro` | Rigol DG800 Pro / DG900 Pro function generator: the frequency of each channel | VISA |
| `RigolRSA3000` | Rigol RSA3000 spectrum analyzer | VISA |
| `RPLockbox` | Red Pitaya lockbox | TCP/IP (SCPI) |
| `SiglentSSA3000XPlus` | Siglent SSA3000X Plus spectrum analyzer | VISA |
| `SRSCTC100` | SRS CTC100 cryogenic temperature controller | Serial |
| `SRSSIM922` | SRS SIM922 diode temperature monitor | Serial |
| `ThorlabsBC` | Thorlabs BC207 and BC210 beam profilers | Thorlabs Beam (DLL) |
| `ThorlabsK10CR1` | Thorlabs K10CR1 motorized rotation mount | Thorlabs Kinesis (DLL) |
| [`ThorlabsKPA101`](amodevices/thorlabs_kpa101/README.md) | Thorlabs KPA101 beam position aligner | USB serial (pyserial, APT protocol) |
| `ThorlabsMDT693B` | Thorlabs MDT693B 3-axis piezo controller | USB serial |
| `ThorlabsPM100` | Thorlabs PM100 power meter | VISA |

## Reading status vocabulary

A driver that can tell a valid reading from an invalid one reports a plain-word status string beside each reading, written to the database as a companion field and shown to people verbatim. The convention is defined once in `amodevices.status` (transport-free, importable without any device library): snake_case, `[a-z0-9_]`, at most 32 characters; `STATUS_OK` = `ok` for a valid reading; otherwise a short plain-English reason, the same word across devices where the meaning matches (`overexposed`, `no_signal`, ...); `STATUS_UNKNOWN` = `unknown_error` for a device code without a mapping (the raw code belongs in a log line). Each driver keeps its own code → word table next to its codes and maps through `status_for(code, table)`; `check_status_table(table)` validates a table in the driver's tests. First user: [`HighFinesseWS`](amodevices/highfinesse_ws/README.md) (`STATUS_TEXT`, `status_text(code)`).
