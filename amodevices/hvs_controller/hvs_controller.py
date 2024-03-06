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

        self._monitors = PyDAQmx.Task("Voltage Monitors")
        self._voltage_controls = PyDAQmx.Task("Voltage Controls")

        self.timeout = device["Timeout"]

        self._cache_interval = 0.5  # 100 ms
        self._last_reading_time = None
        self._voltages = np.full(shape=(4,), fill_value=np.nan)
        self._currents = np.full(shape=(2,), fill_value=np.nan)
        self._write_voltages = np.zeros(4)

        self._connected = False

    def connect(self, adc_slot='cDAQ1Mod4', dac_slot='cDAQ1Mod2'):
        try:
            for monitor in self.device['Monitors']:
                adc_port = str.encode(f"/{adc_slot}/AI{monitor['Input']}")
                if monitor["Measurement"] == "Voltage":
                    # Double check that this works? How does computer assign port?
                    self._monitors.CreateAIVoltageChan(adc_port, b'', PyDAQmx.DAQmx_Val_RSE,
                                                            PyDAQmx.float64(0), PyDAQmx.float64(monitor['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
                elif monitor["Measurement"] == "Current":
                    self._monitors.CreateAIVoltageChan(adc_port, b'', PyDAQmx.DAQmx_Val_RSE,
                                                            PyDAQmx.float64(0), PyDAQmx.float64(monitor['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
                else:
                    raise DeviceError(
                        f"{monitor['Measurement']} is not a known measurement type!")
            for control in self.device['Controls']:
                dac_port = str.encode(f"/{dac_slot}/AO{control['Output']}")
                self._voltage_controls.CreateAOVoltageChan(dac_port, b'', PyDAQmx.float64(
                    0), PyDAQmx.float64(control['Limit']), PyDAQmx.DAQmx_Val_Volts, None)
            self._connected = True
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
    
    def connected(self):
        return self._connected
    
    def get_name(self, channel):
        # If you tell me a current monitor how should I know which name to return?
        return self.device["Monitors"][channel]["Name"]
    
    def set_name(self, channel, name):
        self.device["Monitors"][channel]["Name"] = name
        self.device["Controls"][channel]["Name"] = name
        if channel == 0:
            self.device["Monitors"][4]["Name"] = name
        elif channel == 3:
            self.device["Monitors"][5]["Name"] = name

    def get_device(self):
        return self.device

    def read_voltage(self, channel):
        # Do a read every cache interval and cache the readings. If reading is less than last cached data then just use the previous data
        if (self._last_reading_time is None) or (time.time()-self._last_reading_time > self._cache_interval):
            self.update_readings()
        return self._voltages[channel]

    def read_current(self, channel):
        # Do a read every cache interval and cache the readings. If reading is less than last cached data then just use the previous data
        if self._last_reading_time is None or (time.time()-self._last_reading_time > self._cache_interval):
            self.update_readings()
        chan = 0 if (channel == 0) else 1
        return self._currents[chan]
    
    def update_readings(self, n_samples = 64):
        try:
            # Configure memory for read
            samples = np.empty((6, n_samples))
            samps_read = PyDAQmx.int32()
            # Perform read
            self._monitors.StartTask()
            self._last_reading_time = time.time()
            self._monitors.ReadAnalogF64(PyDAQmx.int32(n_samples), PyDAQmx.float64(
                    self.timeout), PyDAQmx.DAQmx_Val_GroupByChannel, samples, PyDAQmx.uInt32(samples.size), samps_read, None)
            self._monitors.StopTask()
            if int(samps_read.value) != n_samples:
                raise DeviceError("Requested and read samples mismatch!")
            # Store readings
            values = np.mean(samples, axis=1)
            # Problem: values are stored according to channel; must either sort labels according to channels or values according to labels
            for i, val in enumerate(values):
                if i < 4:
                    self._voltages[i] = val * \
                        self.device["Monitors"][i]["Scaling"]
                else:
                    self._currents[i % 4] = val * self.device["Monitors"][i]["Scaling"]
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
        except PyDAQmx.DAQError as e:
            raise DeviceError(f"Received NI Card Error; {e}")


    def set_voltage(self, channel, value):
        try:
            self._write_voltages[channel] = value * self.device["Controls"][channel]["Scaling"]
            samps_written = PyDAQmx.int32()
            self._voltage_controls.StartTask()
            self._voltage_controls.WriteAnalogF64(PyDAQmx.int32(1), PyDAQmx.bool32(True), PyDAQmx.float64(self.timeout), PyDAQmx.bool32(
                PyDAQmx.DAQmx_Val_GroupByChannel), self._write_voltages, PyDAQmx.byref(samps_written), None)
            self._voltage_controls.StopTask()
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
        except PyDAQmx.DAQError as e:
            raise DeviceError(f"Received NI Card Error; {e}")
        if not samps_written:
            raise DeviceError("Requested and written samples mismatch!")
        return


# ADD functionality to set all voltage to zero when we closer the server or delete the controller?.