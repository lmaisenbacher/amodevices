# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Smoke test of the Keysight DSOX driver against the CRD lab's MSO-X 2024A:
identity, error queue, trigger settings, one acquisition of channel 1, and
a save/load round trip of the record.
"""

import logging
import tempfile
from pathlib import Path

import numpy as np

from amodevices import KeysightDSOX
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Keysight MSO-X 2024A',
    'Address': 'TCPIP0::192.168.50.29::inst0::INSTR',
    'Timeout': 4.,
    }

# Arm a single acquisition and wait for its trigger (True), or read the
# record the oscilloscope currently holds (False)
arm_single = True
# Time to wait for the single acquisition to trigger (s)
acquisition_timeout = 30.

try:
    scope = KeysightDSOX(device)
    print(
        f'Connected to {scope.manufacturer} {scope.model}, serial number'
        f' {scope.serial_number}, firmware {scope.firmware}')
    scope.check_errors()
    print(f'Run state: {scope.state}, waveform points mode: {scope.waveform_points_mode}')
    print(f'Acquisition type: {scope.acquire.type}, timebase scale: {scope.timebase.scale} s/div')
    print('Trigger settings:')
    for key, value in scope.trigger.settings.items():
        print(f'  {key}: {value}')
    for channel_number in range(1, 5):
        channel = scope.channel(channel_number)
        if channel.display:
            print(
                f'Channel {channel_number}: {channel.scale} V/div, offset {channel.offset} V,'
                f' {channel.coupling} coupling, label \'{channel.label}\'')

    if arm_single:
        record = scope.acquire_single([1], timeout=acquisition_timeout, names=['Test'])
    else:
        record = scope.read_waveforms([1], names=['Test'])
    scope.check_errors()
    time = record['time']
    voltage = record['data'][0]
    print(
        f'Read {len(voltage)} values from channel 1: {time[0]*1e6:.3f} us to'
        f' {time[-1]*1e6:.3f} us, {voltage.min():.4f} V to {voltage.max():.4f} V')
    print('Metadata:')
    for key, value in record['metadata'].items():
        print(f'  {key}: {value}')

    with tempfile.TemporaryDirectory() as directory:
        path = KeysightDSOX.save_waveforms(Path(directory, 'test_trace.npz'), record)
        loaded = KeysightDSOX.load_waveforms(path)
        assert np.array_equal(loaded['time'], record['time'])
        assert np.array_equal(loaded['data'], record['data'])
        assert loaded['metadata']['NPoints'] == record['metadata']['NPoints']
        print(f'Save/load round trip through \'{path}\' OK')

    scope.close()
except DeviceError as e:
    print(e)
