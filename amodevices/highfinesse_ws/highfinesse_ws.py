# -*- coding: utf-8 -*-
"""
Device driver for HighFinesse WS series wavemeters, interfaced through the
Windows DLL API (`wlmData.dll`) of the HighFinesse wavemeter software, which
must be running on the same PC.
Tested with model WS/7.
All calls address wavemeter channel 1 only.

Originally written by Fabian Schmid for the He+ project at MPQ, where it was
used with a WS Ultimate 2 MC.
Adapted by Lothar Maisenbacher/UC Berkeley.
"""

import logging
import ctypes
import math

from .. import dev_generic
from ..dev_exceptions import DeviceError
from ..status import STATUS_OK, STATUS_UNKNOWN, status_for

logger = logging.getLogger(__name__)

# Constants for the wavelength meter library
# pylint: disable=invalid-name
cInstResetCalc = 0
cCtrlWLMShow = 1
# `GetAmplitudeNum` index: the interference pattern's minimum, maximum
# and average on CCD array 1 and 2 (counts of the array's ADC; the
# automatic exposure aims at a maximum of about 1000-3000 counts)
cMin1 = 0
cMin2 = 1
cMax1 = 2
cMax2 = 3
cAvg1 = 4
cAvg2 = 5
cmiDeviationUnit = 1041
cReturnWavelengthVac = 0
cReturnFrequency = 2
cReturnWavenumber = 3
# pylint: enable=invalid-name

SET_ERRORS = {
    0: "ResERR_NoErr",
    -1: "ResERR_WlmMissing",
    -2: "ResERR_CouldNotSet",
    -3: "ResERR_ParmOutOfRange",
    -4: "ResERR_WlmOutOfResources",
    -5: "ResERR_WlmInternalError",
    -6: "ResERR_NotAvailable",
    -7: "ResERR_WlmBusy",
    -8: "ResERR_NotInMeasurementMode",
    -9: "ResERR_OnlyInMeasurementMode",
    -10: "ResERR_ChannelNotAvailable",
    -11: "ResERR_ChannelTemporarilyNotAvailable",
    -12: "ResERR_CalOptionNotAvailable",
    -13: "ResERR_CalWavelengthOutOfRange",
    -14: "ResERR_BadCalibrationSignal",
    -15: "ResERR_UnitNotAvailable",
    -16: "ResERR_FileNotFound",
    -17: "ResERR_FileCreation",
    -18: "ResERR_TriggerPending",
    -19: "ResERR_TriggerWaiting",
    -20: "ResERR_NoLegitimation"
}

# Return values of GetFrequency/GetWavelength (and GetWLMVersion,
# GetOptionInfo): > 0 is a result, <= 0 one of these codes. From
# `Data.h` of the wavemeter software "Setup 7 834 USB [7.834.6533.007]"
# (WS/7 manual section 4.1.2.2 misnumbers -5 to -8). 0 = ErrNoValue is
# also the normal "nothing new since the last read" answer in
# 'ReadOnce' mode.
GET_ERRORS = {
    0: "ErrNoValue",
    -1: "ErrNoSignal",
    -2: "ErrBadSignal",
    -3: "ErrLowSignal",
    -4: "ErrBigSignal",
    -5: "ErrWlmMissing",
    -6: "ErrNotAvailable",
    -7: "InfNothingChanged",
    -8: "ErrNoPulse",
    -10: "ErrChannelNotAvailable",
    -13: "ErrDiv0",
    -14: "ErrOutOfRange",
    -15: "ErrUnitNotAvailable",
    -26: "ErrTCPErr",
    -28: "ErrParameterOutOfRange",
    -29: "ErrStringTooLong",
    -30: "ErrInterruptedByUser",
    -31: "ErrInfoAlreadyFetched",
}

# Plain-word status texts for the codes above — the vocabulary written
# to the database and shown to people (the identifiers in GET_ERRORS
# are for logs and this file), following the fleet convention in
# `amodevices.status`: 'ok' (STATUS_OK) marks a valid result, an
# unmapped code becomes 'unknown_error' (STATUS_UNKNOWN) in the data
# with the raw code going to the log only.
STATUS_TEXT = {
    0: "no_value",
    -1: "no_signal",
    -2: "bad_signal",
    -3: "underexposed",
    -4: "overexposed",
    -5: "wavemeter_missing",
    -6: "not_available",
    -7: "nothing_changed",
    -8: "no_pulse",
    -10: "channel_not_available",
    -13: "division_by_zero",
    -14: "out_of_range",
    -15: "unit_not_available",
    -26: "tcp_error",
    -28: "parameter_out_of_range",
    -29: "string_too_long",
    -30: "interrupted_by_user",
    -31: "info_already_fetched",
}


def status_text(code):
    """Status vocabulary word for a GetFrequency return `code` (<= 0);
    'unknown_error' for a code not in `STATUS_TEXT`."""
    return status_for(code, STATUS_TEXT)


def status_name(code):
    """Header identifier of a GetFrequency return `code` (<= 0), e.g.
    'ErrBigSignal'; 'Err<code>' for a code not in `GET_ERRORS`."""
    return GET_ERRORS.get(int(code), f"Err{int(code)}")


# `GetTemperature`/`GetPressure` return the value, or ErrTemperature
# (-1000) plus a GET_ERRORS code (WS/7 manual section 4.1.2.2 and the
# constants list): every code is <= ENVIRONMENT_ERROR_MAX
ENVIRONMENT_ERROR_MAX = -1000
ENVIRONMENT_ERRORS = {
    -1000: "ErrTempNotMeasured",       # ErrTemperature + ErrNoValue
    -1005: "ErrTempWlmMissing",        # ErrTemperature + ErrWlmMissing
    -1006: "ErrTempNotAvailable",      # ErrTemperature + ErrNotAvailable
}


def classify_result(raw):
    """Sort a `GetFrequencyNum`/`GetPowerNum` return into
    ``(value, status)``: a result > 0 gives ``(float, 'ok')``; 0
    (ErrNoValue — also "nothing new since the last read" in 'ReadOnce'
    mode) and None (device not present) give ``(nan, None)``; an error
    code < 0 gives ``(nan, status_text(code))``."""
    if raw is None or raw == 0:
        return math.nan, None
    if raw > 0:
        return float(raw), STATUS_OK
    return math.nan, status_text(raw)


def amplitude_value(raw):
    """The counts of a `GetAmplitudeNum` return as a float, NaN for a
    code <= 0 (a `SET_ERRORS` code, or 0 — indistinguishable from
    ResERR_NoErr) or None (device not present)."""
    if raw is None or raw <= 0:
        return math.nan
    return float(raw)


def amplitude_error_name(code):
    """Header identifier of a `GetAmplitudeNum` error `code` (< 0),
    e.g. 'ResERR_NotAvailable'; 'ResERR<code>' for a code not in
    `SET_ERRORS`."""
    return SET_ERRORS.get(int(code), f"ResERR{int(code)}")


def environment_value(raw):
    """The value of a `GetTemperature`/`GetPressure` return as a float,
    NaN for a code (<= `ENVIRONMENT_ERROR_MAX`) or None (device not
    present)."""
    if raw is None or raw <= ENVIRONMENT_ERROR_MAX:
        return math.nan
    return float(raw)


def environment_error_name(code):
    """Header identifier of a `GetTemperature`/`GetPressure` error
    `code` (<= `ENVIRONMENT_ERROR_MAX`), e.g. 'ErrTempNotAvailable';
    'ErrTemp<code>' for a code not in `ENVIRONMENT_ERRORS`."""
    return ENVIRONMENT_ERRORS.get(int(code), f"ErrTemp{int(code)}")


class HighFinesseWS(dev_generic.Device):
    """
    Device driver for HighFinesse WS series wavemeters, interfaced through the
    Windows DLL API of the HighFinesse wavemeter software running on the same PC.
    """

    DEFAULT_LIBRARY_PATH = r'C:\Windows\System32\wlmData.dll'

    # GetFrequency return codes and their status vocabulary — exposed on
    # the class so consumers need only `from amodevices import HighFinesseWS`
    GET_ERRORS = GET_ERRORS
    STATUS_TEXT = STATUS_TEXT
    STATUS_OK = STATUS_OK
    STATUS_UNKNOWN = STATUS_UNKNOWN
    status_text = staticmethod(status_text)
    status_name = staticmethod(status_name)
    classify_result = staticmethod(classify_result)
    amplitude_value = staticmethod(amplitude_value)
    amplitude_error_name = staticmethod(amplitude_error_name)
    environment_value = staticmethod(environment_value)
    environment_error_name = staticmethod(environment_error_name)

    # DLL entry points with a non-int return: (restype, argtypes). Bound
    # at construction; a missing export is reported by name instead of
    # surfacing as an AttributeError
    _SIGNATURES = {
        'GetFrequencyNum': (ctypes.c_double, None),
        'GetWavelengthNum': (ctypes.c_double, None),
        'GetDeviationSignal': (ctypes.c_double, None),
        'GetDeviationReference': (ctypes.c_double, None),
        'GetPulseMode': (ctypes.c_ushort, None),
        'GetAmplitudeNum': (ctypes.c_long, [ctypes.c_long, ctypes.c_long, ctypes.c_long]),
        'GetPowerNum': (ctypes.c_double, [ctypes.c_long, ctypes.c_double]),
        'GetTemperature': (ctypes.c_double, [ctypes.c_double]),
        'GetPressure': (ctypes.c_double, [ctypes.c_double]),
    }

    def __init__(self, device):
        """Initialize class for device with settings `device` (dict).

        Keys consumed beyond those of `dev_generic.Device`:
        'Address' (optional): path to the wavemeter library
            (default 'C:\\Windows\\System32\\wlmData.dll').
        'ReadOnce' (optional, default False): if True, each measurement result
            can be read only once: until the wavemeter completes a NEW
            measurement, further calls to `get_frequency` return ErrNoValue (0)
            instead of repeating the last result. Use this to read each shot of
            a pulsed laser exactly once by polling faster than the pulse
            repetition rate.
        'PulseMode' (optional): expected measurement mode of the wavemeter
            software (0 = CW, nonzero = a pulsed mode, numbering per WS/7
            manual section 4.1.2.4). Checked, never set: the driver must not override
            a mode an operator chose in the wavemeter GUI, only refuse to
            operate in the wrong one (see `check_pulse_mode`).
        """
        super().__init__(device)

        library_path = device.get('Address', self.DEFAULT_LIBRARY_PATH)
        read_once = device.get('ReadOnce', False)
        self.expected_pulse_mode = device.get('PulseMode')

        try:
            self.wlm_lib = ctypes.WinDLL(library_path)
        except (OSError, AttributeError) as err:
            # AttributeError: `ctypes.WinDLL` does not exist on non-Windows
            # platforms
            raise DeviceError(
                f'{self.device["Device"]}: Could not open HighFinesse wavemeter'
                f' library \'{library_path}\': {err}') from err
        for name, (restype, argtypes) in self._SIGNATURES.items():
            try:
                entry = getattr(self.wlm_lib, name)
            except AttributeError as err:
                raise DeviceError(
                    f'{self.device["Device"]}: HighFinesse wavemeter library'
                    f' \'{library_path}\' has no entry point \'{name}\'') from err
            entry.restype = restype
            if argtypes is not None:
                entry.argtypes = argtypes

        retval = self.wlm_lib.Instantiate(cInstResetCalc, 1 if read_once else 0, 0, 0)
        if retval == 0:
            raise DeviceError(
                f'{self.device["Device"]}: Could not instantiate wavemeter.'
                ' Is the wavemeter software running?')

        self.device_present = True
        self.device_connected = True

        # Set the wavemeter to report frequencies
        self.wlm_lib.SetPIDSetting(
            cmiDeviationUnit, 1, cReturnFrequency, ctypes.c_double(0))

        logger.info(
            'Wavemeter software measurement mode: %s (0 = CW, nonzero = pulsed)',
            self.get_pulse_mode())
        self.check_pulse_mode()

    def check_pulse_mode(self):
        """Raise `DeviceError` if the wavemeter software is not in the
        measurement mode expected by the device configuration ('PulseMode')."""
        if self.expected_pulse_mode is None:
            return
        actual_mode = self.get_pulse_mode()
        if actual_mode != self.expected_pulse_mode:
            raise DeviceError(
                f'{self.device["Device"]}: Wavemeter software is in measurement'
                f' mode {actual_mode}, but mode {self.expected_pulse_mode} is'
                ' expected (\'PulseMode\' in device configuration). Select the'
                ' correct mode in the \'Pulse\' group of the wavemeter software.')

    def set_pid_setpoint(self, setpoint):
        """Set the PID setpoint.

        :setpoint: the setpoint in THz
        """
        if not self.device_present:
            logger.warning("set_pid_setpoint(%f) called for non-present HighFinesse wavemeter.",
                           setpoint)
            return
        retval = self.wlm_lib.SetPIDCourseNum(1, str(setpoint).encode())
        if retval != 0:
            logger.error("Could not set PID setpoint. Error: %s",
                         SET_ERRORS.get(retval, str(retval)))

    def get_pid_setpoint(self):
        """Return the current PID setpoint.

        :returns: the current setpoint in THz or -1 of the device is not present
        """
        if not self.device_present:
            logger.warning("get_pid_setpoint() called for non-present HighFinesse wavemeter.")
            return -1
        strbuf = ctypes.create_string_buffer(1024)
        self.wlm_lib.GetPIDCourseNum(1, ctypes.byref(strbuf))
        return float(strbuf.value.lstrip(b"= ").replace(b",", b"."))

    def set_pid_status(self, status):
        """Enable or disable the PID.

        :status: True to enable the PID, False to disable it
        """
        if not self.device_present:
            logger.warning("set_pid_status(%s) called for non-present HighFinesse wavemeter.",
                           status)
            return

        retval = self.wlm_lib.SetDeviationMode(ctypes.c_bool(status))
        if retval != 0:
            logger.error("Could not set PID status. Error: %s",
                         SET_ERRORS.get(retval, str(retval)))

    def set_automatic_exposure(self, status):
        """Enable or disable automatic exposure.

        :status: True to enable automatic exposure, False to disable it
        """
        if not self.device_present:
            logger.warning("set_automatic_exposure(%s) called for non-present"
                           " HighFinesse wavemeter.", status)
            return
        retval = self.wlm_lib.SetExposureMode(status)
        if retval != 0:
            logger.error("Could not set automatic exposure. Error: %s",
                         SET_ERRORS.get(retval, str(retval)))

    def set_exposure_1(self, exposure):
        """Set the exposure of the first sensor.

        :exposure: the exposure to set in ms
        """
        if not self.device_present:
            logger.warning("set_exposure_1(%d) called for non-present HighFinesse wavemeter.",
                           exposure)
            return
        retval = self.wlm_lib.SetExposureNum(1, 1, exposure)
        if retval != 0:
            logger.error("Could not set first sensor exposure. Error: %s",
                         SET_ERRORS.get(retval, str(retval)))

    def set_exposure_2(self, exposure):
        """Set the exposure of the second sensor.

        :exposure: the exposure to set in ms
        """
        if not self.device_present:
            logger.warning("set_exposure_2(%d) called for non-present HighFinesse wavemeter.",
                           exposure)
            return
        retval = self.wlm_lib.SetExposureNum(1, 2, exposure)
        if retval != 0:
            logger.error("Could not set second sensor exposure. Error: %s",
                         SET_ERRORS.get(retval, str(retval)))

    def get_pid_output_voltage(self):
        """Return the current PID output voltage.

        :returns: the PID output voltage in mV or -1.0 if the device is not present.
        """
        if not self.device_present:
            logger.warning("get_pid_output_voltage() called for non-present"
                           " HighFinesse wavemeter.")
            return -1.0
        return self.wlm_lib.GetDeviationSignal(ctypes.c_double(0))

    def get_exposures(self):
        """Return the current exposure times.

        :returns: a tuple containing the exposure times of the two sensors in ms or (-1, -1) if the
                  device is not present.
        """
        if not self.device_present:
            logger.warning("get_exposures() called for non-present HighFinesse wavemeter.")
            return (-1, -1)
        return self.wlm_lib.GetExposureNum(1, 1, 0), self.wlm_lib.GetExposureNum(1, 2, 0)

    def get_amplitude(self, index):
        """Return one amplitude of the last measurement's interference
        pattern.

        :index: which one — `cMin1`, `cMin2`, `cMax1`, `cMax2`, `cAvg1`
                or `cAvg2` (minimum, maximum, average of CCD array 1
                and 2)
        :returns: the amplitude in counts (> 0), an error code (<= 0,
                  `SET_ERRORS`, see `amplitude_error_name`), or None
                  if the device is not present. A state readout, not a
                  result: unaffected by 'ReadOnce', and it describes
                  the pattern of the last result whether that one was
                  valid or an error (an overexposed shot's maximum is
                  the interesting number). `amplitude_value` turns the
                  return into counts or NaN.
        """
        if not self.device_present:
            logger.warning("get_amplitude(%d) called for non-present HighFinesse wavemeter.",
                           index)
            return None
        return self.wlm_lib.GetAmplitudeNum(1, index, 0)

    def get_levels(self):
        """Return the current sensor levels.

        :returns: a tuple containing the maximum amplitudes of the two
                  sensors (see `get_amplitude`) or (-1.0, -1.0) if the
                  device is not present"""
        if not self.device_present:
            logger.warning("get_levels() called for non-present HighFinesse wavemeter.")
            return (-1.0, -1.0)
        return self.get_amplitude(cMax1), self.get_amplitude(cMax2)

    def get_power(self):
        """Return the optical power (or, in a pulsed mode, the pulse
        energy) the wavemeter derived from the last measurement.

        :returns: the value (> 0; relative, not calibrated — µW in CW
                  mode, µJ per pulse in a pulsed mode), an error code
                  (<= 0, the `GET_ERRORS` codes; 0 = ErrNoValue when
                  there is none), or None if the device is not
                  present. `classify_result` sorts the return like a
                  frequency. In 'ReadOnce' mode this too returns
                  ErrNoValue once the latest result has been
                  requested (manual section 4.1.2.2); whether the
                  frequency and the power share that flag is not
                  stated — read the frequency first and expect 0 here
                  if they do.
        """
        if not self.device_present:
            logger.warning("get_power() called for non-present HighFinesse wavemeter.")
            return None
        return self.wlm_lib.GetPowerNum(1, ctypes.c_double(0))

    def get_temperature(self):
        """Return the temperature of the wavemeter's optical unit.

        :returns: the temperature in °C (three digits), an error code
                  (<= `ENVIRONMENT_ERROR_MAX`, see
                  `environment_error_name`: not measured yet, no
                  sensor, server gone), or None if the device is not
                  present.
                  `environment_value` turns the return into a value or
                  NaN.
        """
        if not self.device_present:
            logger.warning("get_temperature() called for non-present HighFinesse wavemeter.")
            return None
        return self.wlm_lib.GetTemperature(ctypes.c_double(0))

    def get_pressure(self):
        """Return the air pressure inside the wavemeter's optical unit.

        :returns: the pressure in mbar of the active pressure mode
                  (the built-in sensor, or an external one set through
                  the DLL), an error code (<= `ENVIRONMENT_ERROR_MAX`,
                  see `environment_error_name`: not measured yet, no
                  sensor and no external mode, server gone), or None
                  if the device is not present.
                  `environment_value` turns the return into a value or
                  NaN.
        """
        if not self.device_present:
            logger.warning("get_pressure() called for non-present HighFinesse wavemeter.")
            return None
        return self.wlm_lib.GetPressure(ctypes.c_double(0))

    def get_frequency(self):
        """Return the current laser frequency.

        :returns: the current frequency in THz, or None if the device is not present,
                  or an error code (<= 0, passed through unmapped) — see
                  `GET_ERRORS` for the identifiers and `status_text()` for the
                  plain-word status vocabulary; 0 (ErrNoValue) is also the
                  "nothing new since the last read" answer in 'ReadOnce' mode
        """
        if not self.device_present:
            logger.warning("get_frequency() called for non-present HighFinesse wavemeter.")
            return None
        return self.wlm_lib.GetFrequencyNum(1, ctypes.c_double(0))

    def get_pulse_mode(self):
        """Return the current measurement mode of the wavemeter software.

        :returns: the mode as reported by `GetPulseMode`: 0 for continuous
                  wave (CW), nonzero for one of the pulsed modes (numbering
                  depends on the device version, see WS/7 manual section
                  4.1.2.4), or None if the device is not present
        """
        if not self.device_present:
            logger.warning("get_pulse_mode() called for non-present HighFinesse wavemeter.")
            return None
        return self.wlm_lib.GetPulseMode(ctypes.c_ushort(0))

    def get_automatic_exposure(self):
        """Return if automatic exposure is enabled.

        :returns: True if automatic exposure is enabled, False if it is disabled or the device is
                  not present
        """
        if not self.device_present:
            logger.warning("get_automatic_exposure() called for non-present"
                           " HighFinesse wavemeter.")
            return False
        return self.wlm_lib.GetExposureMode(ctypes.c_bool(False))

    def get_pid_enabled(self):
        """Return if the PID controller is enabled.

        :returns: True if the PID is enabled, False if it is disabled or the device is not present
        """
        if not self.device_present:
            logger.warning("get_pid_enabled() called for non-present HighFinesse wavemeter.")
            return False
        return self.wlm_lib.GetDeviationMode(ctypes.c_bool(False))
