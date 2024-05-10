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

Additionally, to run the high voltage controller server you also need to download National Instruments MAX drivers. Further instructions can be found [here](https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000000YGQwCAO&l=en-US).
