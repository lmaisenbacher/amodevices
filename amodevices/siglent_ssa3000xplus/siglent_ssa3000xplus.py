# -*- coding: utf-8 -*-
"""
Created on Mon Feb 10 15:40:58 2025

@author: Lothar Maisenbacher/UC Berkeley

Device driver for Siglent SSA3000X Plus series spectrum analyzer, controlled through VISA.
"""

import numpy as np
import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class SiglentSSA3000XPlus(dev_generic.Device):
    """Device driver for Siglent SSA3000X Plus series spectrum analyzer."""

    class _freq():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def center(self):
            """Get center frequency (Hz, float)."""
            return float(self.outer_instance.visa_query(':FREQuency:CENTer?'))

        @center.setter
        def center(self, freq):
            """Set center frequency to `freq` (Hz, float)."""
            return self.outer_instance.visa_write(f':FREQuency:CENTer {freq}')

        @property
        def span(self):
            """Get frequency span (Hz, float)."""
            return float(self.outer_instance.visa_query(':FREQuency:SPAN?'))

        @span.setter
        def span(self, freq):
            """Set frequency span to `freq` (Hz, float)."""
            return self.outer_instance.visa_write(f':FREQuency:SPAN {freq}')

        @property
        def start(self):
            """Get start frequency (Hz, float)."""
            return float(self.outer_instance.visa_query(':FREQuency:STARt?'))

        @start.setter
        def start(self, freq):
            """Set start frequency to `freq` (Hz, float)."""
            return self.outer_instance.visa_write(f':FREQuency:STARt {freq}')

        @property
        def stop(self):
            """Get stop frequency (Hz, float)."""
            return float(self.outer_instance.visa_query(':FREQuency:STOP?'))

        @stop.setter
        def stop(self, freq):
            """Set stop frequency to `freq` (Hz, float)."""
            return self.outer_instance.visa_write(f':FREQuency:STOP {freq}')

        @property
        def values(self):
            """Get frequency values as 1-D array."""
            return np.linspace(self.start, self.stop, self.outer_instance.sweep_points)

    class _trace():

        def __init__(self, outer_instance, trace_id):
            self.outer_instance = outer_instance
            self.trace_id = trace_id

        @property
        def detector(self):
            """
            Get the detector type for trace number `trace_id` (int).
            Returns either 'NORMal', 'POSitive', 'SAMPle', 'AVERage', 'NORMAL', 'QUASi'.
            """
            return self.outer_instance.visa_query(f':DETector:TRACe{self.trace_id:d}?')

        @detector.setter
        def detector(self, detector):
            """
            Set the detector type to `detector` for trace number `trace_id` (int).
            `detector` is either 'NORMal', 'POSitive', 'SAMPle', 'AVERage', 'NORMAL', 'QUASi'.
            """
            return self.outer_instance.visa_write(f':DETector:TRACe{self.trace_id:d} {detector}')

        @property
        def data(self):
            """Read the y-axis data. The unit will the unit the device is currently set to."""
            trace = self.outer_instance.visa_query(f':TRAC:DATA? TRACE{self.trace_id:d}')
            return np.array(trace.split(',')).astype(float)

        @data.setter
        def data(self, data):
            """
            Set the y-axis data to `data` (1-D array, float or int).
            The unit will the unit the device is currently set to.
            """
            data_str = ','.join(data.astype(str))
            return self.outer_instance.visa_write(
                f':TRAC:DATA TRACE{self.trace_id:d}, {data_str}')

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        self.init_visa()
        self.freq = self._freq(self)

        # Set trace data format to ASCII
        self.trace_data_format = 'ASCii'

    def close(self):
        """Close connection to device."""
        self.visa_resource.close()

    def trace(self, trace_id):
        """Return instance of trace class (class `_trace`) for trace number `trace_id`."""
        return self._trace(self, trace_id)

    @property
    def yunit(self):
        """Get the y-axis unit (str): either 'DBM', 'DBMV', 'DBUV', 'DBUA', 'V', or 'W'."""
        return self.visa_query(':UNIT:POWer?')

    @yunit.setter
    def yunit(self, unit):
        """
        Set the y-axis unit to `unit` (str): either 'DBM', 'DBMV', 'DBUV', 'DBUA', 'V', or 'W'.
        """
        if unit not in ['DBM', 'DBMV', 'DBUV', 'DBUA', 'V', 'W']:
            raise DeviceError(
                f'{self.device["Device"]}: '
                +'Power unit must be \'DBM\', \'DBMV\', \'DBUV\', \'DBUA\', \'V\', or \'W\'')
        return self.visa_write(f':UNIT:POWer {unit}')

    @property
    def rbw(self):
        """Get resolution bandwidth (RBW) (Hz, float)."""
        return float(self.visa_query(':BANDwidth:RESolution?'))

    @rbw.setter
    def rbw(self, rbw):
        """Set resolution bandwidth (RBW) to `rbw` (Hz, float)."""
        return self.visa_write(f':BANDwidth:RESolution {rbw}')

    @property
    def continuous_measurement(self):
        """Get current measurement mode: True if continuous, False if single."""
        return bool(int(self.visa_query(':INITiate:CONTinuous?')))

    @continuous_measurement.setter
    def continuous_measurement(self, mode):
        """Set current measurement mode to `mode` (bool): True for continuous, False for single."""
        return self.visa_write(f':INITiate:CONTinuous {int(mode)}')

    @property
    def acquisition_time(self):
        """Get acquistion time (s, float). Only available in RTSA mode."""
        return float(self.visa_query(':ACQuisition:TIME?'))

    @acquisition_time.setter
    def acquisition_time(self, time):
        """Set acquistion time to `time` (s, float). Only available in RTSA mode."""
        return self.visa_write(f':ACQuisition:TIME {time}')

    @property
    def sweep_points(self):
        """Get number of sweep points (int)."""
        return int(self.visa_query(':SWEEp:POINts?'))

    @sweep_points.setter
    def sweep_points(self, num_points):
        """Set number of sweep points to `num_points` (int)."""
        return self.visa_write(f':SWEEp:POINts {num_points:d}')

    @property
    def sweep_time(self):
        """Get sweep time (s, float)."""
        return float(self.visa_query(':SWEEp:TIME?'))

    @sweep_time.setter
    def sweep_time(self, time):
        """Set sweep time to `time` (s, float)."""
        return self.visa_write(f':SWEEp:TIME {time}')

    @property
    def sweep_time_auto(self):
        """Get the status of the auto sweep time (bool)."""
        return bool(int(self.visa_query(':SWEEp:TIME:AUTO?')))

    @sweep_time_auto.setter
    def sweep_time_auto(self, auto):
        """Set auto sweep time to status `auto` (bool)."""
        return self.visa_write(f':SWEEp:TIME:AUTO {int(auto)}')

    @property
    def tg_output(self):
        """Get the on/off status of the tracking generator (bool)."""
        return bool(int(self.visa_query(':OUTPut:STATe?')))

    @tg_output.setter
    def tg_output(self, status):
        """Get the on/off status of the tracking generator to `status` (bool)."""
        return self.visa_write(f':OUTPut:STATe {status:d}')

    @property
    def tg_output_amplitude(self):
        """Get the output amplitude of the tracking generator (dBm, float)."""
        return float(self.visa_query(':SOURce:POWer:LEVel:IMMediate:AMPLitude?'))

    @tg_output_amplitude.setter
    def tg_output_amplitude(self, amplitude):
        """Set the output amplitude of the tracking generator to `amplitude` (dBm, float)."""
        return self.visa_write(f':SOURce:POWer:LEVel:IMMediate:AMPLitude {amplitude}')

    @property
    def trace_data_format(self):
        """
        Get input/output format of the trace data (s, float):
        either 'ASCii', 'REAL,32', or 'REAL,64'.
        """
        return self.visa_query(':FORMat:TRACe:DATA?')

    @trace_data_format.setter
    def trace_data_format(self, data_format):
        """
        Set input/output format of the trace data to `format` (s, str):
        either 'ASCii', 'REAL,32', 'REAL,64'.
        """
        return self.visa_write(f':FORMat:TRACe:DATA {data_format}')

    def sweep(self):
        """
        Initialize a sweep (in non-measurement state) or trigger a measurement
        (in measurement state).
        """
        return self.visa_write(':INITiate:IMMediate')
