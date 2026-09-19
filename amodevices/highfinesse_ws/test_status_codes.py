# -*- coding: utf-8 -*-
"""Tests for the fleet status vocabulary (`amodevices.status`) and the
HighFinesse GetFrequency return-code maps built on it. Pure Python — no
wavemeter DLL needed. Runs under pytest or directly as a script.
"""

import pytest

import math

from amodevices.highfinesse_ws.highfinesse_ws import (
    ENVIRONMENT_ERROR_MAX,
    ENVIRONMENT_ERRORS,
    GET_ERRORS,
    STATUS_TEXT,
    HighFinesseWS,
    amplitude_error_name,
    amplitude_value,
    cAvg1,
    cAvg2,
    classify_result,
    cMax1,
    cMax2,
    cMin1,
    cMin2,
    environment_error_name,
    environment_value,
    status_name,
    status_text,
)
from amodevices.status import (
    STATUS_OK,
    STATUS_PATTERN,
    STATUS_UNKNOWN,
    check_status_table,
    check_status_word,
    status_for,
)


def test_vocabulary_convention():
    assert STATUS_OK == 'ok' and STATUS_UNKNOWN == 'unknown_error'
    assert check_status_word('overexposed') == 'overexposed'
    for bad in ('Overexposed', 'over exposed', 'over-exposed', '', 'a' * 33, None):
        with pytest.raises(ValueError):
            check_status_word(bad)
    assert status_for(-4.0, {-4: 'overexposed'}) == 'overexposed'
    assert status_for(-999, {-4: 'overexposed'}) == STATUS_UNKNOWN
    with pytest.raises(ValueError, match='reserved'):
        check_status_table({0: 'ok'})
    with pytest.raises(ValueError, match='same word'):
        check_status_table({-1: 'x', -2: 'x'})


def test_codes_are_non_positive_and_named_once():
    assert all(code <= 0 for code in GET_ERRORS)
    assert len(set(GET_ERRORS.values())) == len(GET_ERRORS)
    # Spot checks against the header (Data.h, software 7.834.6533.007)
    assert GET_ERRORS[0] == 'ErrNoValue'
    assert GET_ERRORS[-4] == 'ErrBigSignal'
    assert GET_ERRORS[-5] == 'ErrWlmMissing'
    assert GET_ERRORS[-8] == 'ErrNoPulse'


def test_every_code_has_a_status_text_in_the_vocabulary():
    assert set(STATUS_TEXT) == set(GET_ERRORS)
    check_status_table(STATUS_TEXT)
    assert all(STATUS_PATTERN.match(word) for word in STATUS_TEXT.values())


def test_lookups():
    assert status_text(-4) == 'overexposed'
    assert status_text(-3) == 'underexposed'
    assert status_text(-1) == 'no_signal'
    assert status_text(-999) == STATUS_UNKNOWN
    assert status_text(-4.0) == 'overexposed'      # a float from the DLL
    assert status_name(-4) == 'ErrBigSignal'
    assert status_name(-999) == 'Err-999'
    # Re-exported on the class for consumers that only import the driver
    assert HighFinesseWS.STATUS_OK == STATUS_OK
    assert HighFinesseWS.status_text(-8) == 'no_pulse'


def test_amplitude_indices_follow_the_header():
    assert (cMin1, cMin2, cMax1, cMax2, cAvg1, cAvg2) == (0, 1, 2, 3, 4, 5)


def test_classify_result_sorts_frequency_and_power_returns():
    assert classify_result(387.0) == (387.0, STATUS_OK)
    assert classify_result(12.5) == (12.5, STATUS_OK)
    for nothing in (0, 0.0, None):          # nothing new / not present
        value, status = classify_result(nothing)
        assert math.isnan(value) and status is None
    value, status = classify_result(-4.0)
    assert math.isnan(value) and status == 'overexposed'
    value, status = classify_result(-999)
    assert math.isnan(value) and status == STATUS_UNKNOWN
    assert HighFinesseWS.classify_result(-8) == classify_result(-8)


def test_amplitude_value_is_counts_or_nan():
    assert amplitude_value(2500) == 2500.0
    assert isinstance(amplitude_value(2500), float)   # never an int field
    for nothing in (0, -6, None):
        assert math.isnan(amplitude_value(nothing))
    assert amplitude_error_name(-6) == 'ResERR_NotAvailable'
    assert amplitude_error_name(-1) == 'ResERR_WlmMissing'
    assert amplitude_error_name(-99) == 'ResERR-99'


def test_environment_value_drops_the_temperature_codes():
    assert environment_value(23.4) == 23.4
    assert environment_value(1013.2) == 1013.2
    assert environment_value(-5.0) == -5.0          # a cold lab is a value
    for code in (ENVIRONMENT_ERROR_MAX, -1005.0, -1006, None):
        assert math.isnan(environment_value(code))
    # ErrTemperature (-1000) + the GET_ERRORS code, per the header
    assert ENVIRONMENT_ERRORS == {-1000: 'ErrTempNotMeasured',
                                  -1005: 'ErrTempWlmMissing',
                                  -1006: 'ErrTempNotAvailable'}
    assert environment_error_name(-1006.0) == 'ErrTempNotAvailable'
    assert environment_error_name(-1001) == 'ErrTemp-1001'
    assert HighFinesseWS.environment_value(-1000) is not None


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
