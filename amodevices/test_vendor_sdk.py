# -*- coding: utf-8 -*-
"""Tests of the vendor SDK lookup (`amodevices.vendor_sdk`) and of the two
drivers that use it, with temporary folders: the search order, the error
naming every folder tried, the copy instruction from 'VendorSDKSource',
the FLIR Boson SDK imported from any folder, and a Kinesis DLL that does
not load becoming `DeviceError`. No vendor files or devices needed. Runs
under pytest.
"""

import os
import sys

import pytest

from amodevices import vendor_sdk
from amodevices.dev_exceptions import DeviceError
from amodevices.vendor_sdk import copy_instruction, find_vendor_sdk, vendor_sdk_dir


def test_vendor_sdk_dir_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(vendor_sdk.ENV_VAR, str(tmp_path))
    assert vendor_sdk_dir() == tmp_path


@pytest.mark.skipif(os.name != 'nt', reason='Windows default')
def test_vendor_sdk_dir_default_under_programdata(monkeypatch, tmp_path):
    monkeypatch.delenv(vendor_sdk.ENV_VAR, raising=False)
    monkeypatch.setenv('PROGRAMDATA', str(tmp_path))
    assert vendor_sdk_dir() == tmp_path / 'amodevices' / 'vendor_sdk'


def test_find_takes_first_folder_with_every_file(tmp_path):
    partial, full, later = tmp_path / 'partial', tmp_path / 'full', tmp_path / 'later'
    for folder in (partial, full, later):
        folder.mkdir()
    (partial / 'a.dll').touch()
    for folder in (full, later):
        (folder / 'a.dll').touch()
        (folder / 'b.dll').touch()
    found = find_vendor_sdk([None, '', partial, full, later], ['a.dll', 'b.dll'], 'X', 'hint')
    assert found == full


def test_find_error_names_every_folder_and_the_hint(tmp_path):
    with pytest.raises(DeviceError) as error:
        find_vendor_sdk([None, tmp_path / 'one', tmp_path / 'two'], ['a.dll'],
                        'Vendor SDK', 'Install it.')
    text = str(error.value)
    assert text.startswith('Vendor SDK not found')
    assert str(tmp_path / 'one') in text and str(tmp_path / 'two') in text
    assert text.endswith('Install it.')


def test_copy_instruction_with_and_without_source(tmp_path):
    device = {'VendorSDKSource': 'D:/vendor files'}
    assert (copy_instruction(device, 'the folder X', tmp_path, 'Install it.')
            == f'Copy the folder X from D:/vendor files to {tmp_path}.')
    assert copy_instruction({}, 'the folder X', tmp_path, 'Install it.') == 'Install it.'


# FLIR Boson SDK: a fake SDK folder with a relative import, like the real one

@pytest.fixture
def flir_boson(monkeypatch, tmp_path):
    from amodevices.flir_boson import flir_boson
    monkeypatch.setenv(vendor_sdk.ENV_VAR, str(tmp_path / 'vendor_sdk'))
    monkeypatch.setattr(flir_boson, 'LEGACY_DIR', tmp_path / 'legacy')
    for name in [m for m in sys.modules if m.startswith(flir_boson.SDK_MODULE)]:
        monkeypatch.delitem(sys.modules, name)
    yield flir_boson
    for name in [m for m in sys.modules if m.startswith(flir_boson.SDK_MODULE)]:
        del sys.modules[name]


def make_fake_sdk(folder):
    (folder / 'ClientFiles_Python').mkdir(parents=True)
    (folder / 'ClientFiles_Python' / '__init__.py').write_text('')
    (folder / 'ClientFiles_Python' / 'Client_API.py').write_text('NAME = "fake client"\n')
    (folder / '__init__.py').write_text('from .ClientFiles_Python import Client_API as CamAPI\n')


def test_boson_sdk_from_vendor_sdk_folder(flir_boson, tmp_path):
    make_fake_sdk(tmp_path / 'vendor_sdk' / 'flir_boson' / 'SDK_USER_PERMISSIONS')
    sdk = flir_boson.load_sdk({})
    assert sdk.CamAPI.NAME == 'fake client'


def test_boson_sdk_path_in_device_dict_wins(flir_boson, tmp_path):
    make_fake_sdk(tmp_path / 'vendor_sdk' / 'flir_boson' / 'SDK_USER_PERMISSIONS')
    make_fake_sdk(tmp_path / 'explicit')
    (tmp_path / 'explicit' / 'ClientFiles_Python' / 'Client_API.py').write_text(
        'NAME = "explicit client"\n')
    sdk = flir_boson.load_sdk({'SDKPath': str(tmp_path / 'explicit')})
    assert sdk.CamAPI.NAME == 'explicit client'


def test_boson_missing_sdk_tells_where_to_copy_from(flir_boson, tmp_path):
    with pytest.raises(DeviceError) as error:
        flir_boson.load_sdk({'VendorSDKSource': 'https://example.org/sdk.zip'})
    text = str(error.value)
    assert 'Copy the folder \'SDK_USER_PERMISSIONS\' from https://example.org/sdk.zip' in text
    assert str(tmp_path / 'vendor_sdk' / 'flir_boson') in text


def test_boson_broken_sdk_is_device_error(flir_boson, tmp_path):
    folder = tmp_path / 'vendor_sdk' / 'flir_boson' / 'SDK_USER_PERMISSIONS'
    folder.mkdir(parents=True)
    (folder / '__init__.py').write_text('from .missing import nothing\n')
    with pytest.raises(DeviceError, match='failed to import'):
        flir_boson.load_sdk({})
    assert flir_boson.SDK_MODULE not in sys.modules


# Thorlabs Kinesis: a file that is not a DLL fails to load

@pytest.mark.skipif(os.name != 'nt', reason='Windows DLL')
def test_kinesis_dll_that_does_not_load_is_device_error(monkeypatch, tmp_path):
    from amodevices.thorlabs_k10cr1 import thorlabs_k10cr1
    for name in thorlabs_k10cr1.KINESIS_DLLS:
        (tmp_path / name).write_bytes(b'not a DLL')
    with pytest.raises(DeviceError, match='cannot load'):
        thorlabs_k10cr1.ThorlabsK10CR1({'Device': 'test', 'KinesisDir': str(tmp_path),
                                        'SerialNumber': 1})


def test_kinesis_missing_names_installer_and_source(monkeypatch, tmp_path):
    from amodevices.thorlabs_k10cr1 import thorlabs_k10cr1
    monkeypatch.setenv(vendor_sdk.ENV_VAR, str(tmp_path / 'vendor_sdk'))
    monkeypatch.setattr(thorlabs_k10cr1, 'KINESIS_INSTALL_DIR', tmp_path / 'kinesis')
    monkeypatch.setattr(thorlabs_k10cr1, 'LEGACY_DIR', tmp_path / 'legacy')
    with pytest.raises(DeviceError, match='Install Thorlabs Kinesis'):
        thorlabs_k10cr1.ThorlabsK10CR1({'Device': 'test', 'SerialNumber': 1})
    with pytest.raises(DeviceError, match='from D:/vendor'):
        thorlabs_k10cr1.ThorlabsK10CR1({'Device': 'test', 'SerialNumber': 1,
                                        'VendorSDKSource': 'D:/vendor'})
