# -*- coding: utf-8 -*-
"""Tests of the PM100 driver's sensor identification parse: the 6
fields of SYST:SENS:IDN? (quoted or not), and a clear `DeviceError`
for a reply of another form, such as the meter's own 4-field *IDN?
reply read one behind its query. No VISA library needed."""

import pytest

from amodevices.dev_exceptions import DeviceError
from amodevices.thorlabs_pm100.thorlabs_pm100 import ThorlabsPM100


class FakeMeter:
    device = {'Device': 'meter under test'}

    def __init__(self, reply):
        self.reply = reply

    def visa_query(self, command):
        assert command == 'SYSTem:SENSor:IDN?'
        return self.reply


@pytest.mark.parametrize('reply', [
    'ES111C,M01265679,01-Sep-2025,3,2,34',
    '"S121C","230919232","19-Sep-2023",1,18,289'])
def test_the_six_fields_are_parsed(reply):
    sensor = ThorlabsPM100._sensor(FakeMeter(reply))
    assert sensor.idn == reply
    assert sensor._name in ('ES111C', 'S121C') and isinstance(sensor._flags, int)


def test_a_reply_of_another_form_raises_a_device_error():
    sensor = ThorlabsPM100._sensor(FakeMeter('Thorlabs,PM100D,P0045954,2.8.1'))
    with pytest.raises(DeviceError, match='has 4 fields, not 6'):
        _ = sensor.idn
