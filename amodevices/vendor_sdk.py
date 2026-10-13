# -*- coding: utf-8 -*-
"""Where drivers find the vendor drivers and SDKs they load: DLLs and SDK
folders that the device vendors supply, which are neither part of this
package (license, size) nor on PyPI.

A driver looks for them in a fixed order and takes the first folder that
holds every file it needs (`find_vendor_sdk`):

1. an explicit folder in the device dict (each driver names its key);
2. the per-PC vendor SDK folder, `vendor_sdk_dir()`: the environment
   variable AMODEVICES_VENDOR_SDK_DIR, else
   %PROGRAMDATA%\\amodevices\\vendor_sdk on Windows, which services
   running as LocalSystem see as well, else
   ~/.local/share/amodevices/vendor_sdk;
3. the folder the vendor's own installer uses;
4. the driver's package folder, where editable installs kept the files
   before 0.1.39.

Otherwise it raises `DeviceError` naming every folder tried and what to
do. Where a lab keeps its own copy of the files, the device dict's
'VendorSDKSource' (a folder or a URL, set in the application's config, so
that this package names no lab) makes that message an instruction to copy
them from there into the vendor SDK folder (`copy_instruction`): a new
PC's first start then says what to copy from where to where. Nothing is
copied automatically. Transport-free on purpose: importable without any
device library.
"""

import os
from pathlib import Path

from .dev_exceptions import DeviceError

ENV_VAR = 'AMODEVICES_VENDOR_SDK_DIR'
SOURCE_KEY = 'VendorSDKSource'


def vendor_sdk_dir():
    """The per-PC folder of vendor drivers and SDKs (see the module
    docstring)."""
    configured = os.environ.get(ENV_VAR)
    if configured:
        return Path(configured)
    if os.name == 'nt':
        program_data = os.environ.get('PROGRAMDATA', r'C:\ProgramData')
        return Path(program_data, 'amodevices', 'vendor_sdk')
    return Path.home() / '.local' / 'share' / 'amodevices' / 'vendor_sdk'


def copy_instruction(device, item, target, otherwise):
    """What to do when vendor files are missing: copy `item` from the
    device dict's 'VendorSDKSource' to `target` where the dict has one,
    `otherwise` (the vendor's own way to get them) where not."""
    source = device.get(SOURCE_KEY)
    if source:
        return f'Copy {item} from {source} to {target}.'
    return otherwise


def find_vendor_sdk(candidates, required, what, how_to_get):
    """Return the first folder among `candidates` (paths or None, None
    skipped) that holds every name in `required`; raise `DeviceError`
    naming `what`, the folders tried and `how_to_get` otherwise."""
    tried = []
    for candidate in candidates:
        if candidate is None or candidate == '':
            continue
        folder = Path(candidate)
        tried.append(str(folder))
        if all((folder / name).exists() for name in required):
            return folder
    raise DeviceError(
        f'{what} not found: no folder holds {", ".join(required)} '
        f'(tried {"; ".join(tried)}). {how_to_get}')
