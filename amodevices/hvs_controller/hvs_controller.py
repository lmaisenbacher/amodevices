# -*- coding: utf-8 -*-
"""
@author: Jack Mango/Berkeley

This is a device driver designed for the Matsusada J4-5P and KA-10P
high voltage supplies. These supplies allow for the adjustment of output
voltages through analog control voltage inputs, and their outputs can be
monitored via analog monitor voltages. Moreover, the KA-10P high voltage supply
permits the measurement of current using a monitor voltage. The analog voltages
are interfaced with a National Instruments 9205 ADC for measurement and a
National Instruments 9264 DAC for voltage adjustment.

The controller implements a caching scheme for current and voltage measurement. If
it's been over a `cache_interval` since the last measurement the controller will perform
another one for all channels.

Measurement of monitor signals and setting of control signals is implemented using two
separate PyDAQmx Tasks. There is an assumed ordering to the high voltage supplies set 
in the config file: The first four entries in the 'Monitors' dictionary should be for
the voltage monitors, channels one through four. The last two should be the current
monitors for the first, followed by the fourth high voltage supply. The 'Controls'
should also be ordered the same as the first four monitors, in increasing order.
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

        self._monitors = None
        self._voltage_controls = None
        self.timeout = device["timeout"]
        self._cache_interval = 0.5
        self._last_reading_time = None
        self._names = []
        self._voltages = {}
        self._currents = {}
        self._write_voltages = {}
        self._connected = False

    def connect(self):
        """Setup the necessary 'connections' for the high voltage controller."""
        try:
            self._monitors = PyDAQmx.Task("Voltage Monitors")
            self._voltage_controls = PyDAQmx.Task("Voltage Controls")
            for chan in self.device['channels']:
                if chan['model'] == "Matsusada KA-10P":
                    self.config_ka10p(chan)
                elif chan['model'] in ("Matsusada J4-5P", "Matsusada J4-5N"):
                    self.config_j45(chan)
                else:
                    raise DeviceError(f"Model {chan['model']} isn't recognized!")
            self._monitors.StartTask()
            self._voltage_controls.StartTask()
            self._connected = True
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
        
    def config_ka10p(self, channel):
        """
        Configure the analog readout and controls for a Matsusada KA-10P high voltage supply.
        `channel` (dict) : connections for this high voltage supply to the ADC and DAC.
                            should include `name`, `model`, `voltage_monitor` and `current_monitor`
        """
        voltage_monitor_port = str.encode(
            f"/{self.device['cDAQs']['adc']}/AI{channel['voltage_monitor']}")
        current_monitor_port = str.encode(
            f"/{self.device['cDAQs']['adc']}/AI{channel['current_monitor']}")
        voltage_control_port = str.encode(
            f"/{self.device['cDAQs']['dac']}/AO{channel['voltage_control']}")
        self._monitors.CreateAIVoltageChan(voltage_monitor_port,
                                                    b'',
                                                    PyDAQmx.DAQmx_Val_RSE,
                                                    PyDAQmx.float64(0),
                                                    PyDAQmx.float64(10),
                                                    PyDAQmx.DAQmx_Val_Volts,
                                                    None)
        self._monitors.CreateAIVoltageChan(current_monitor_port,
                                                    b'',
                                                    PyDAQmx.DAQmx_Val_RSE,
                                                    PyDAQmx.float64(0),
                                                    PyDAQmx.float64(10),
                                                    PyDAQmx.DAQmx_Val_Volts,
                                                    None)
        self._voltage_controls.CreateAOVoltageChan(voltage_control_port,
                                                       b'',
                                                       PyDAQmx.float64(0),
                                                       PyDAQmx.float64(10),
                                                       PyDAQmx.DAQmx_Val_Volts,
                                                       None)
        self._voltages[channel['name']] = np.nan
        self._currents[channel['name']] = np.nan
        self._write_voltages[channel['name']] = 0
        self._names.append(channel['name'])
        return  
    
    def config_j45(self, channel):
            """
        Configure the analog readout and controls for a Matsusada J4-5X high voltage supply.
        `channel` (dict) : connections for this high voltage supply to the ADC and DAC.
                            should include `name`, `model`, and `voltage_monitor`
        """
            voltage_monitor_port = str.encode(f"/{self.device['cDAQs']['adc']}/AI{channel['voltage_monitor']}")
            voltage_control_port = str.encode(f"/{self.device['cDAQs']['dac']}/AO{channel['voltage_control']}")
            self._monitors.CreateAIVoltageChan(voltage_monitor_port,
                                                        b'',
                                                        PyDAQmx.DAQmx_Val_RSE,
                                                        PyDAQmx.float64(0),
                                                        PyDAQmx.float64(5),
                                                        PyDAQmx.DAQmx_Val_Volts,
                                                        None)
            self._voltage_controls.CreateAOVoltageChan(voltage_control_port,
                                                           b'',
                                                           PyDAQmx.float64(0),
                                                           PyDAQmx.float64(9),
                                                           PyDAQmx.DAQmx_Val_Volts,
                                                           None)
            self._voltages[channel['name']] = np.nan
            self._write_voltages[channel['name']] = 0
            self._names.append(channel['name'])
            return
            
        
    def connected(self):
        """
        Whether there was an issue connecting to the NI cards. False indicates there was
        and error.
        """
        return self._connected
        
    def set_name(self, channel_name, new_name):
        """Change the high voltage supply with name `channel_name` to `new_name`."""
        if new_name in self._names:
            raise DeviceError(f"{new_name} is an invalid name, cannot duplicate names!")
        for chan in self.device['channels']:
            if chan['name'] == channel_name: chan['name'] = new_name
        self._names[self._names.index(channel_name)] = new_name
        self._voltages = self._change_key_in_place(channel_name, new_name, self._voltages)
        self._write_voltages = self._change_key_in_place(channel_name, new_name, self._write_voltages)
        if channel_name in self._currents:
            self._currents = self._change_key_in_place(channel_name, new_name, self._currents)

    def _change_key_in_place(self, key, new_key, dic):
        """
        Change a dictionary (`dic`) key (`key`) in place to `new_key`,
        keeping the ordering of keys and values the same.
        """
        new_dict = {}
        for k, v in dic.items():
            if k == key: new_dict[new_key] = v
            else: new_dict[k] = v
        return new_dict

    def get_device(self):
        "Getter method for the device dictionary."
        return self.device

    def read_voltage(self, channel_name):
        """
        Return the most recent voltage reading from a single high voltage supply,
        denoted by `channel_name`.
        """
        # Update all supply outputs if last cache was more than cache_interval ago.
        if (self._last_reading_time is None) or \
            (time.time()-self._last_reading_time > self._cache_interval):
            self.update_readings()
        return self._voltages[channel_name]

    def read_current(self, channel_name):
        """
        Return the most recent current reading from a single high voltage supply,
        denoted by `channel_name`.
        """
        # Update all supply outputs if last cache was more than cache_interval ago.
        if self._last_reading_time is None or \
            (time.time()-self._last_reading_time > self._cache_interval):
            self.update_readings()
        return self._currents[channel_name]
    
    def update_readings(self):
        """
        Measure and cache the voltage and current for all high voltage supplies. 
        """
        values = self._measure_voltage()
        i = 0
        for chan in self.device['channels']:
            if chan['model'] == "Matsusada KA-10P":
                voltage_scaling = 1000
                current_scaling = 0.1
                self._voltages[chan['name']] = values[i] * voltage_scaling
                self._currents[chan['name']] = values[i + 1] * current_scaling
                i += 2
            elif chan['model'] in ("Matsusada J4-5P", "Matsusada J4-5N"):
                voltage_scaling = 1000
                self._voltages[chan['name']] = values[i] * voltage_scaling
                i += 1
        
        
    def _measure_voltage(self, n_samples=64):
        """
        Perform unscaled voltage measurement from analog to digital converters.
        `n_samples` are collected on each channel for averaging.
        """
        try:
            # Configure memory for read
            buf_size = len(self._currents) + len(self._voltages)
            samples = np.empty((buf_size, n_samples))
            samps_read = PyDAQmx.int32()
            # Perform read
            self._last_reading_time = time.time()
            self._monitors.ReadAnalogF64(PyDAQmx.int32(n_samples),
                                         PyDAQmx.float64(self.timeout),
                                         PyDAQmx.DAQmx_Val_GroupByChannel,
                                         samples,
                                         PyDAQmx.uInt32(samples.size),
                                         samps_read,
                                         None)
            if int(samps_read.value) != n_samples:
                raise DeviceError("Requested and read samples mismatch!")
            # Store readings
            values = np.mean(samples, axis=1)
            return values
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
        except PyDAQmx.DAQError as e:
            raise DeviceError(f"Received NI Card Error; {e}") 
        
    # New problem: if we change the name, the order of the _write_voltages changes relative to what they were stored as 
    # in the task...

    def set_voltage(self, channel_name, value):
        """
        Set the voltage of high voltage supply with name `channel_name` to `value`.
        """
        scaling = 0
        for chan in self.device['channels']:
            if chan['name'] == channel_name and chan['model'] == "Matsusada KA-10P":
                scaling =  0.001
            elif chan['name'] == channel_name and chan['model'] in ("Matsusada J4-5P", "Matsusada J4-5N"):
                scaling = 0.0018
        self._write_voltages[channel_name] = value * scaling
        self._update_voltages()
        return
    
    def _update_voltages(self):
        """
        Write the voltages in `self._write_voltages` to the digital to analog converter.
        """
        try:
            samps_written = PyDAQmx.int32()
            values = np.array(list(self._write_voltages.values()))
            self._voltage_controls.WriteAnalogF64(PyDAQmx.int32(1),
                                                  PyDAQmx.bool32(True),
                                                  PyDAQmx.float64(self.timeout),
                                                  PyDAQmx.bool32(PyDAQmx.DAQmx_Val_GroupByChannel),
                                                  values,
                                                  PyDAQmx.byref(samps_written),
                                                  None)
        except PyDAQmx.DAQmxFunctions.DevCannotBeAccessedError as e:
            self._connected = False
            raise DeviceError(f"Connection Error: {e}")
        except PyDAQmx.DAQError as e:
            raise DeviceError(f"Received NI Card Error; {e}")
        if not samps_written:
            raise DeviceError("Requested and written samples mismatch!")
        
    def close(self):
        """
        End the tasks used to monitor and control the high voltage supplies.
        Call this method on program exit to ensure memory used by the program
        is correctly freed up.
        """
        self._monitors.ClearTask()
        self._voltage_controls.ClearTask()
