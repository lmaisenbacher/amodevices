# -*- coding: utf-8 -*-
"""
@author: Jack Mango/Berkeley

Device driver for high voltage supply controller.
"""

import numpy as np
import PyDAQmx
import time

import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)


class hvsController(dev_generic.Device):

    def __init__(self, device):
        super().__init__(device)

        self._voltage_monitors = PyDAQmx.Task()
        self._current_monitors = PyDAQmx.Task()
        self._voltage_controls = PyDAQmx.Task()

        self.timeout = device["Timeout"]

        self._cache_interval = 0.1 # 100 ms
        self._last_reading_time = None
        self._voltages = np.full(shape=(4,), fill_value=np.nan)
        self._currents = np.full(shape=(4,), fill_value=np.nan)
        self._write_voltages = np.full(shape=(4,), fill_value=np.nan)

    def connect(self, adc_slot='cDAQ1Mod2', dac_slot='cDAQ1Mod4'):
        for monitor in self.device['Monitors']:
            adc_port = str.encode(f"/{adc_slot}/{monitor['Input']}")
            if monitor["Measurement"] == "Voltage":
            # Double check that this works? How does computer assign port?
                self._voltage_monitors.CreateAIVoltageChan(adc_port, b'', PyDAQmx.DAQmx_Val_RSE,
                                                        PyDAQmx.float64(0), PyDAQmx.float64(monitor['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
            elif monitor["Measurement"] == "Current":
                self._current_monitors.CreateAIVoltageChan(adc_port, b'', PyDAQmx.DAQmx_Val_RSE,
                                                        PyDAQmx.float64(0), PyDAQmx.float64(monitor['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
            else:
                raise DeviceError(f"{monitor["Measurement"]} is not a known measurement type!")
        for control in self.device['Controls']:
            dac_port = str.encode(f"/{dac_slot}/{control['Input']}")
            self._voltage_controls.CreateAOVoltageChan(dac_port, b'', PyDAQmx.float64(
                0), PyDAQmx.float64(control['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
        return

    def read_voltage(self, channel, n_samples=64):
        # Do a read every cache interval and cache the readings. If reading is less than last cached data then just use the previous data
        if np.isnan(self._voltages) or self._last_reading_time is None or (time.time()-self._last_reading_time > self._cache_interval):
            self._voltages = np.full(shape=(4,), fill_value=np.nan)
            try:
                # Configure memory for read
                samples = np.empty((len(self.voltage_monitors_info), n_samples))
                samps_read = PyDAQmx.int32()
                # Perform read
                self._voltage_monitors.StartTask()
                self._last_reading_time = time.time()
                self._voltage_monitors.ReadAnalogF64(PyDAQmx.int32(n_samples), PyDAQmx.float64(
                    self.timeout), PyDAQmx.DAQmx_Val_GroupByChannel, samples, PyDAQmx.uInt32(samples.size), samps_read, None)
                self._voltage_monitors.StopTask()
                if samps_read != n_samples:
                    raise DeviceError("Requested and read samples mismatch!")
                # Store readings
                values = np.mean(samples, axis=1)
                # Problem: values are stored according to channel; must either sort labels according to channels or values according to labels
                for i, val in enumerate(values):
                    self._voltages[i] = val * self.device["Monitors"][i]["Scaling"]
            except PyDAQmx.DAQmxError as e:
                raise DeviceError(f"Received NI Card Error; {e}")
        return self._voltages[channel]

    def read_current(self, channel, n_samples=64):
        # Do a read every cache interval and cache the readings. If reading is less than last cached data then just use the previous data
        if np.isnan(self._currents) or self._last_reading_time is None or (time.time()-self._last_reading_time > self._cache_interval):
            self._currents = np.full(shape=(4,), fill_value=np.nan)
            try:
                # Configure memory for read
                samples = np.empty((len(self.voltage_monitors_info), n_samples))
                samps_read = PyDAQmx.int32()
                # Perform read
                self._voltage_monitors.StartTask()
                self._last_reading_time = time.time()
                self._voltage_monitors.ReadAnalogF64(PyDAQmx.int32(n_samples), PyDAQmx.float64(
                    self.timeout), PyDAQmx.DAQmx_Val_GroupByChannel, samples, PyDAQmx.uInt32(samples.size), samps_read, None)
                self._voltage_monitors.StopTask()
                if samps_read != n_samples:
                    raise DeviceError("Requested and read samples mismatch!")
                # Store readings
                values = np.mean(samples, axis=1 )
                # Problem: values are stored according to channel; must either sort labels according to channels or values according to labels
                for i, val in enumerate(values):
                    self._currents[i] = val * self.device["Monitors"][i]["Scaling"]
            except PyDAQmx.DAQmxError as e:
                raise DeviceError(f"Received NI Card Error; {e}")
        return self._currents[channel]

    def set_voltage(self, channel, value):
        try:
            self._write_voltages[channel] = value * self.device["Controls"][channel]["Scaling"]
            samps_written = PyDAQmx.int32()
            self._voltage_controls.StartTask()
            self.voltage_controls.WriteAnalogF64(PyDAQmx.int32(1), PyDAQmx.bool32(True), PyDAQmx.float64(self.timeout), PyDAQmx.bool32(
                PyDAQmx.DAQmx_Val_GroupByChannel), np.zeros(self._write_voltages.size), PyDAQmx.byref(samps_written), None)
            self.voltage_controls.StopTask()
        except PyDAQmx.DAQmxError as e:
            raise DeviceError(f"Received NI Card Error; {e}")
        if samps_written != 4:
            raise DeviceError("Requested and written samples mismatch!")  
        return