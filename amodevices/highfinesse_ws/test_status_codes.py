# -*- coding: utf-8 -*-
"""Tests for the fleet status vocabulary (`amodevices.status`) and the
HighFinesse GetFrequency return-code maps built on it. Pure Python — no
wavemeter DLL needed. Runs under pytest or directly as a script.
"""

import pytest

from amodevices.highfinesse_ws.highfinesse_ws import (
    GET_ERRORS,
    STATUS_TEXT,
    HighFinesseWS,
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


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
