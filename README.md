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

Additionally, to run the high voltage controller server you also need to download National Instruments (NI) NI-DAQmx drivers. Further instructions can be found [here](https://www.ni.com/en/support/downloads/drivers/download.ni-daq-mx.html).

## Supported devices

| Class | Device | Communication |
|-------|--------|---------------|
| `FLIRBoson` | FLIR Boson thermal camera | FLIR Boson SDK |
| `Keysight53220A` | Keysight 53220A universal counter | VISA |
| [`LioptecLiopStar`](amodevices/lioptec_liopstar/README.md) | LIOP-TEC LiopStar-E dye laser | TCP/IP |
| `NIDAQ` | NI DAQ analog input/output | NI-DAQmx |
| `RigolDG900Pro` | Rigol DG800 Pro / DG900 Pro function generator | VISA |
| `RigolRSA3000` | Rigol RSA3000 spectrum analyzer | VISA |
| `RPLockbox` | Red Pitaya lockbox | TCP/IP (SCPI) |
| `SiglentSSA3000XPlus` | Siglent SSA3000X Plus spectrum analyzer | VISA |
| `SRSCTC100` | SRS CTC100 cryogenic temperature controller | Serial |
| `SRSSIM922` | SRS SIM922 diode temperature monitor | Serial |
| `ThorlabsBC` | Thorlabs BC207 and BC210 beam profilers | Thorlabs Beam (DLL) |
| `ThorlabsK10CR1` | Thorlabs K10CR1 motorized rotation mount | Thorlabs Kinesis (DLL) |
| [`ThorlabsKPA101`](amodevices/thorlabs_kpa101/README.md) | Thorlabs KPA101 beam position aligner | USB serial (pyserial, APT protocol) |
| `ThorlabsMDT693B` | Thorlabs MDT693B 3-axis piezo controller | Serial |
| `ThorlabsPM100` | Thorlabs PM100 power meter | VISA |
