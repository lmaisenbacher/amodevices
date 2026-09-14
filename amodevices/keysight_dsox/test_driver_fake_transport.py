# -*- coding: utf-8 -*-
"""Tests of the Keysight DSOX driver against a fake VISA transport: the
constructor's configuration, keyword validation, trigger settings snapshot,
waveform record assembly, and the single-acquisition wait. No oscilloscope
needed. Runs under pytest or directly as a script.
"""

import datetime

import numpy as np
import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.keysight_dsox.keysight_dsox import KeysightDSOX

# Query responses of a stopped MSO-X 2024A in edge-then-edge trigger mode
# with a 4-point normal acquisition of channel 1
RESPONSES = {
    '*IDN?': 'AGILENT TECHNOLOGIES,MSO-X 2024A,MY61410204,02.65.2021102830',
    ':RSTate?': 'STOP',
    ':WAVeform:POINts:MODE?': 'RAW',
    ':WAVeform:POINts?': '4',
    ':ACQuire:POINts?': '4',
    ':WAVeform:PREamble?': (
        '+1,+0,+4,+1,+1.00000000E-009,-2.00000000E-009,+0,'
        '+2.00000000E-003,+1.00000000E-001,+0'),
    ':WAVeform:TYPE?': 'NORM',
    ':CHANnel1:DISPlay?': '1',
    ':CHANnel1:SCALe?': '+5.0E-02',
    ':CHANnel1:OFFSet?': '+1.0E-01',
    ':CHANnel1:COUPling?': 'DC',
    ':CHANnel2:DISPlay?': '0',
    ':TIMebase:SCALe?': '+1.0E-06',
    ':TIMebase:POSition?': '+0.0E+00',
    ':TRIGger:MODE?': 'DEL',
    ':TRIGger:SWEep?': 'NORM',
    ':TRIGger:HOLDoff?': '+4.0E-08',
    ':TRIGger:DELay:ARM:SOURce?': 'CHAN2',
    ':TRIGger:DELay:ARM:SLOPe?': 'POS',
    ':TRIGger:DELay:TRIGger:SOURce?': 'CHAN1',
    ':TRIGger:DELay:TRIGger:SLOPe?': 'POS',
    ':TRIGger:DELay:TRIGger:COUNt?': '1',
    ':TRIGger:DELay:TDELay:TIME?': '+4.0E-09',
    ':TRIGger:EDGE:LEVel? CHAN2': '+1.5E+00',
    ':TRIGger:EDGE:LEVel? CHAN1': '+2.5E-01',
    ':SYSTem:ERRor?': '+0,"No error"',
    }


class FakeDSOX(KeysightDSOX):
    """The driver on a scripted transport: `writes` logs every command,
    `responses` answers queries, `raw` is the waveform data, and the run
    bit stays set for `running_polls` polls after each ':SINGle'."""

    POLL_INTERVAL = 0.

    def __init__(self, responses=RESPONSES, raw=(0, 1, -1, 2), running_polls=0):
        self.writes = []
        self.responses = dict(responses)
        self.raw = raw
        self.running_polls = running_polls
        self._polls_left = 0
        super().__init__({'Device': 'Fake scope', 'Address': 'FAKE::INSTR'})

    def init_visa(self):
        self.device_present = True
        self.device_connected = True

    def visa_write(self, cmd):
        self.writes.append(cmd)
        if cmd == ':SINGle':
            self._polls_left = self.running_polls

    def visa_query(self, query, return_ascii=False):
        if query == ':OPERegister:CONDition?':
            if self._polls_left > 0:
                self._polls_left -= 1
                return '8'
            return '0'
        return self.responses[query]

    def visa_query_binary(self, query, datatype='h', is_big_endian=False, chunk_size=2**20):
        assert query == ':WAVeform:DATA?' and datatype == 'h' and not is_big_endian
        return np.array(self.raw, dtype=np.int16)


def test_init_reads_identity_and_configures_transfer():
    scope = FakeDSOX()
    assert (scope.manufacturer, scope.model, scope.serial_number, scope.firmware) == (
        'AGILENT TECHNOLOGIES', 'MSO-X 2024A', 'MY61410204', '02.65.2021102830')
    assert scope.writes == [
        ':WAVeform:UNSigned OFF',
        ':WAVeform:BYTeorder LSBFirst',
        ':WAVeform:FORMat WORD',
        ':WAVeform:POINts:MODE RAW',
        ':WAVeform:POINts MAXimum',
        ]
    assert scope.system_error == (0, 'No error')
    scope.check_errors()


def test_keyword_setters_validate_and_send_long_forms():
    scope = FakeDSOX()
    scope.writes.clear()
    scope.trigger.mode = 'delay'
    scope.trigger.delay.arm_source = 2
    scope.trigger.delay.arm_slope = 'pos'
    scope.trigger.delay.trigger_source = 'CHAN1'
    scope.trigger.sweep = 'NORMal'
    scope.trigger.set_level(0.25, 1)
    scope.waveform_points = 1000
    scope.waveform_points = 'max'
    scope.channel(1).coupling = 'dc'
    scope.channel(1).display = True
    scope.channel(1).label = 'Trans. PD'
    scope.acquire.type = 'hres'
    scope.timebase.mode = 'main'
    scope.digitize(1, 2)
    scope.digitize()
    assert scope.writes == [
        ':TRIGger:MODE DELay',
        ':TRIGger:DELay:ARM:SOURce CHANnel2',
        ':TRIGger:DELay:ARM:SLOPe POSitive',
        ':TRIGger:DELay:TRIGger:SOURce CHAN1',
        ':TRIGger:SWEep NORMal',
        ':TRIGger:EDGE:LEVel 0.25,CHANnel1',
        ':WAVeform:POINts 1000',
        ':WAVeform:POINts MAXimum',
        ':CHANnel1:COUPling DC',
        ':CHANnel1:DISPlay 1',
        ':CHANnel1:LABel "Trans. PD"',
        ':ACQuire:TYPE HRESolution',
        ':TIMebase:MODE MAIN',
        ':DIGitize CHANnel1,CHANnel2',
        ':DIGitize',
        ]
    with pytest.raises(DeviceError, match='Fake scope: Trigger mode must be one of'):
        scope.trigger.mode = 'DELAYED'
    with pytest.raises(DeviceError, match='Arming edge slope'):
        scope.trigger.delay.arm_slope = 'RISING'
    with pytest.raises(DeviceError, match='Channel label'):
        scope.channel(1).label = 'more than ten characters'


def test_trigger_settings_snapshot():
    scope = FakeDSOX()
    assert scope.trigger.settings == {
        'TriggerMode': 'DEL',
        'TriggerSweep': 'NORM',
        'TriggerHoldoff_s': 4e-8,
        'TriggerArmSource': 'CHAN2',
        'TriggerArmSlope': 'POS',
        'TriggerArmLevel_V': 1.5,
        'TriggerSource': 'CHAN1',
        'TriggerSlope': 'POS',
        'TriggerLevel_V': 0.25,
        'TriggerCount': 1,
        'TriggerDelayTime_s': 4e-9,
        }
    # Edge mode, digital source: no level
    scope.responses.update({
        ':TRIGger:MODE?': 'EDGE',
        ':TRIGger:EDGE:SOURce?': 'DIG3',
        ':TRIGger:EDGE:SLOPe?': 'NEG',
        ':TRIGger:EDGE:COUPling?': 'DC',
        })
    settings = scope.trigger.settings
    assert settings['TriggerSource'] == 'DIG3' and settings['TriggerSlope'] == 'NEG'
    assert np.isnan(settings['TriggerLevel_V'])
    assert 'TriggerArmSource' not in settings


def test_read_waveforms_record():
    scope = FakeDSOX()
    scope.writes.clear()
    record = scope.read_waveforms([1], names=['Cav. trans. PD'])
    assert scope.writes == [':WAVeform:SOURce CHANnel1']
    np.testing.assert_allclose(record['time'], [-2e-9, -1e-9, 0., 1e-9])
    np.testing.assert_allclose(record['data'], [[0.1, 0.102, 0.098, 0.104]])
    assert record['channels'] == [1]
    metadata = record['metadata']
    assert metadata['Model'] == 'MSO-X 2024A'
    assert metadata['RunState'] == 'STOP'
    assert metadata['AcquisitionType'] == 'NORM'
    assert metadata['PointsMode'] == 'RAW'
    assert metadata['NPoints'] == 4 and metadata['NValues'] == 4
    assert metadata['PointsAcquired'] == 4
    assert metadata['XIncrement_s'] == 1e-9 and metadata['XOrigin_s'] == -2e-9
    assert metadata['TimebaseScale_s'] == 1e-6
    assert metadata['ChannelNames'] == ['Cav. trans. PD']
    assert metadata['ChannelUnits'] == ['V']
    assert metadata['ChannelScales_V'] == [0.05]
    assert metadata['ChannelOffsets_V'] == [0.1]
    assert metadata['ChannelCouplings'] == ['DC']
    assert metadata['YIncrements_V'] == [2e-3]
    assert metadata['YReferences'] == [0]
    # ISO 8601 with the local UTC offset
    assert datetime.datetime.fromisoformat(metadata['Timestamp']).tzinfo is not None
    # Default channel names, and a name count mismatch
    assert scope.read_waveforms([1])['metadata']['ChannelNames'] == ['Ch1']
    with pytest.raises(DeviceError, match='2 channel names given for 1 channels'):
        scope.read_waveforms([1], names=['a', 'b'])
    # Waveforms can only be read from displayed channels
    with pytest.raises(DeviceError, match='Channel 2 is not displayed'):
        scope.read_waveforms([2])


def test_read_waveforms_peak_detect_doubles_values():
    scope = FakeDSOX(raw=(0, 1, 0, 2, 0, 3, 0, 4))
    scope.responses[':WAVeform:TYPE?'] = 'PEAK'
    record = scope.read_waveforms([1])
    assert record['metadata']['AcquisitionType'] == 'PEAK'
    assert record['metadata']['NPoints'] == 4 and record['metadata']['NValues'] == 8
    np.testing.assert_allclose(
        record['time'], [-2e-9, -2e-9, -1e-9, -1e-9, 0., 0., 1e-9, 1e-9])
    # A length that fits neither one nor two values per point is refused
    scope = FakeDSOX(raw=(0, 1, 2))
    with pytest.raises(DeviceError, match='holds 3 values'):
        scope.read_waveforms([1])


def test_acquire_single_waits_for_stop():
    scope = FakeDSOX(running_polls=3)
    scope.writes.clear()
    record = scope.acquire_single([1], timeout=1.)
    assert scope.writes[:2] == [':SINGle', ':WAVeform:SOURce CHANnel1']
    assert record['data'].shape == (1, 4)
    # Timeout: the acquisition is stopped and the error names the run state
    scope = FakeDSOX(running_polls=10**6)
    scope.responses[':RSTate?'] = 'SING'
    scope.writes.clear()
    with pytest.raises(DeviceError, match='did not complete within 0.0 s .run state \'SING\''):
        scope.acquire_single([1], timeout=0.)
    assert scope.writes == [':SINGle', ':STOP']


def test_acquire_single_warns_on_auto_sweep(caplog):
    scope = FakeDSOX()
    scope.responses[':TRIGger:SWEep?'] = 'AUTO'
    with caplog.at_level('WARNING'):
        scope.acquire_single([1], timeout=1.)
    assert 'Trigger sweep mode is AUTO' in caplog.text


def test_read_waveforms_warns_when_running(caplog):
    # Running scope: the instrument reports the requested 'RAW' mode as
    # 'MAX' and serves the shorter measurement record
    scope = FakeDSOX()
    scope.responses.update({
        ':RSTate?': 'RUN',
        ':WAVeform:POINts:MODE?': 'MAX',
        ':ACQuire:POINts?': '500000',
        })
    with caplog.at_level('WARNING'):
        record = scope.read_waveforms([1])
    assert 'Read while the oscilloscope is running' in caplog.text
    assert '(4 points transferred, 500000 acquired)' in caplog.text
    assert record['metadata']['RunState'] == 'RUN'
    assert record['metadata']['PointsMode'] == 'MAX'
    assert record['metadata']['PointsAcquired'] == 500000
    # The measurement record is what 'NORM' mode asks for: no warning
    caplog.clear()
    scope.responses[':WAVeform:POINts:MODE?'] = 'NORM'
    with caplog.at_level('WARNING'):
        scope.read_waveforms([1])
    assert 'running' not in caplog.text
    # Stopped: the raw record is served even though the acquired count
    # includes memory beyond the displayed window
    caplog.clear()
    scope.responses.update({':RSTate?': 'STOP', ':WAVeform:POINts:MODE?': 'MAX'})
    with caplog.at_level('WARNING'):
        scope.read_waveforms([1])
    assert 'running' not in caplog.text


def test_check_errors_drains_queue():
    scope = FakeDSOX()
    errors = iter(['-113,"Undefined header"', '-222,"Data out of range"', '+0,"No error"'])
    scope.visa_query = lambda query, return_ascii=False: next(errors)
    with pytest.raises(DeviceError, match='-113: Undefined header; -222: Data out of range'):
        scope.check_errors()


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
