# -*- coding: utf-8 -*-
"""Tests for the hardware-free parts of the Keysight DSOX driver: SCPI
keyword matching, source names, preamble parsing, waveform scaling, and the
waveform record files (new layout and the legacy pyhs layout). No
oscilloscope needed. Runs under pytest or directly as a script.
"""

import json

import numpy as np
import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.keysight_dsox.keysight_dsox import (
    KeysightDSOX,
    channel_number,
    channel_source,
    is_level_source,
    load_waveforms,
    parse_preamble,
    save_waveforms,
    scale_waveform,
    scpi_keyword,
    time_axis,
)

# A preamble as an MSO-X 2024A sends it: WORD format, normal acquisition,
# 18800 points at 1 ns
PREAMBLE_TEXT = (
    '+1,+0,+18800,+1,+1.00000000E-009,-4.40625000E-006,+0,'
    '+8.00000000E-005,+2.00000000E-001,+0')


def test_scpi_keyword_accepts_short_and_long_forms():
    keywords = ('NORMal', 'MAXimum', 'RAW')
    assert scpi_keyword('raw', keywords, 'x') == 'RAW'
    assert scpi_keyword('max', keywords, 'x') == 'MAXimum'
    assert scpi_keyword('MAXIMUM', keywords, 'x') == 'MAXimum'
    assert scpi_keyword(' Norm ', keywords, 'x') == 'NORMal'
    assert scpi_keyword('sbus1', ('EDGE', 'SBUS1'), 'x') == 'SBUS1'
    assert scpi_keyword('lfr', ('AC', 'DC', 'LFReject'), 'x') == 'LFReject'
    with pytest.raises(DeviceError, match='Scope: Points mode must be one of'):
        scpi_keyword('MAXI', keywords, 'Points mode', device='Scope')


def test_sources():
    assert channel_source(2) == 'CHANnel2'
    assert channel_source(np.int64(3)) == 'CHANnel3'
    assert channel_source(' CHAN2 ') == 'CHAN2'
    assert channel_source('EXTernal') == 'EXTernal'
    assert channel_number('CHAN2') == 2
    assert channel_number('CHANnel3') == 3
    assert channel_number(4) == 4
    assert channel_number('DIG0') is None
    assert channel_number('EXT') is None
    assert is_level_source(1)
    assert is_level_source('CHAN2')
    assert is_level_source('EXT')
    assert not is_level_source('DIG3')
    assert not is_level_source('LINE')


def test_parse_preamble():
    preamble = parse_preamble(PREAMBLE_TEXT + '\n')
    assert preamble == {
        'format': 1, 'type_code': 0, 'points': 18800, 'count': 1,
        'xincrement': 1e-9, 'xorigin': -4.40625e-6, 'xreference': 0,
        'yincrement': 8e-5, 'yorigin': 0.2, 'yreference': 0}
    assert all(isinstance(preamble[key], int) for key in (
        'format', 'type_code', 'points', 'count', 'xreference', 'yreference'))
    # Integer fields in NR3 format
    assert parse_preamble('1,0,1.88E+4,1,1e-9,0,0,1e-3,0,0')['points'] == 18800
    with pytest.raises(DeviceError, match='holds 3 fields'):
        parse_preamble('1,0,100')
    with pytest.raises(DeviceError, match='not a number'):
        parse_preamble('1,0,x,1,1e-9,0,0,1e-3,0,0')


def test_scale_waveform():
    preamble = {'yincrement': 2e-3, 'yorigin': 0.1, 'yreference': 0}
    volts = scale_waveform(np.array([0, 1, -1], dtype=np.int16), preamble)
    assert volts.dtype == np.float64
    np.testing.assert_allclose(volts, [0.1, 0.102, 0.098])
    # yreference is subtracted (unsigned data would carry 32768 here)
    preamble['yreference'] = 128
    np.testing.assert_allclose(
        scale_waveform([128, 129], preamble), [0.1, 0.102])


def test_time_axis():
    preamble = {'points': 4, 'xincrement': 1e-9, 'xorigin': -2e-9, 'xreference': 0}
    np.testing.assert_allclose(time_axis(preamble, 4), [-2e-9, -1e-9, 0., 1e-9])
    preamble['xreference'] = 1
    np.testing.assert_allclose(time_axis(preamble, 4), [-3e-9, -2e-9, -1e-9, 0.])
    # Peak detect: two values (min, max) per time bucket
    np.testing.assert_allclose(
        time_axis(preamble, 8), [-3e-9, -3e-9, -2e-9, -2e-9, -1e-9, -1e-9, 0., 0.])
    with pytest.raises(DeviceError, match='holds 5 values'):
        time_axis(preamble, 5)


def _record():
    time = np.linspace(-1e-6, 1e-6, 50)
    data = np.vstack([np.sin(1e7*time), np.cos(1e7*time)])
    return {
        'time': time,
        'data': data,
        'channels': [1, 3],
        'metadata': {
            'Model': 'MSO-X 2024A',
            'NPoints': np.int64(50),
            'XIncrement_s': np.float64(1e-9),
            'Channels': [1, 3],
            'ChannelNames': ['Cav. trans. PD', 'Cav. piezo'],
            'TriggerLevel_V': float('nan'),
            'Comment': 'unicode µ ok',
            },
        }


def test_save_load_round_trip(tmp_path):
    record = _record()
    path = save_waveforms(tmp_path / 'trace.npz', record)
    assert path == tmp_path / 'trace.npz'
    loaded = load_waveforms(path)
    np.testing.assert_array_equal(loaded['time'], record['time'])
    np.testing.assert_array_equal(loaded['data'], record['data'])
    assert loaded['channels'] == [1, 3]
    metadata = loaded['metadata']
    assert metadata['Model'] == 'MSO-X 2024A'
    assert metadata['NPoints'] == 50 and isinstance(metadata['NPoints'], int)
    assert metadata['XIncrement_s'] == 1e-9
    assert metadata['ChannelNames'] == ['Cav. trans. PD', 'Cav. piezo']
    assert np.isnan(metadata['TriggerLevel_V'])
    assert metadata['Comment'] == 'unicode µ ok'
    # The metadata is stored as JSON text, so no pickling is involved
    with np.load(path, allow_pickle=False) as archive:
        assert json.loads(str(archive['Metadata']))['Model'] == 'MSO-X 2024A'
    # Re-exported on the class for consumers that only import the driver
    assert KeysightDSOX.save_waveforms is save_waveforms
    assert KeysightDSOX.load_waveforms is load_waveforms


def test_load_legacy_layout(tmp_path):
    # The layout of the pyhs driver's `writeFile()`, as in the archived
    # CRD traces: time in column 0, one channel per further column, and a
    # pickled params dict
    time = np.arange(5)*1e-9
    voltage = np.array([0.2093, 0.2093, 0.2001, 0.19, 0.18])
    params = {
        'Manufacturer': 'AGILENT TECHNOLOGIES',
        'Model': 'MSO-X 2024A',
        'Serial number': 'MY61410204',
        'Mode': 'Normal',
        'Samples': 5,
        'Time': '2026-09-11 18-48-04',
        'NChannels': 1,
        'Columns': ['Time', 'Ch1'],
        'ColumnUnits': ['s', 'V'],
        'ColumnNames': ['Time', 'Cav. trans. PD'],
        'Comment': '243 nm enhancement cavity finesse measurement',
        }
    path = tmp_path / 'legacy.npz'
    np.savez_compressed(path, rawData=np.column_stack([time, voltage]), params=params)
    loaded = load_waveforms(path)
    np.testing.assert_array_equal(loaded['time'], time)
    assert loaded['data'].shape == (1, 5)
    np.testing.assert_array_equal(loaded['data'][0], voltage)
    assert loaded['channels'] == [1]
    metadata = loaded['metadata']
    assert metadata['Serial number'] == 'MY61410204'
    assert metadata['Comment'] == params['Comment']
    assert metadata['Channels'] == [1]
    assert metadata['ChannelNames'] == ['Cav. trans. PD']
    assert metadata['ChannelUnits'] == ['V']
    # Channel numbers come from the 'Ch<n>' column labels
    np.savez_compressed(
        path, rawData=np.column_stack([time, voltage, 2*voltage]),
        params={'Columns': ['Time', 'Ch1', 'Ch3']})
    loaded = load_waveforms(path)
    assert loaded['channels'] == [1, 3]
    assert loaded['metadata']['ChannelNames'] == ['Ch1', 'Ch3']
    assert loaded['data'].shape == (2, 5)


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
