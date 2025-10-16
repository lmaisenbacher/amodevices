#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:25:00 2025

@author: Isaac Pope and Lothar Maisenbacher/UC Berkeley

Device driver for NI DAQ devices using DAQmx interface.
"""

import logging
import nidaqmx

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class NIDAQ(dev_generic.Device):
    """
    Device driver for NI DAQ devices using DAQmx interface.
    """

    def __init__(self, config):
        self.ao_channels = config['AOChannels']
        self.ao_tasks = {axis: nidaqmx.Task() for axis in self.ao_channels}
        self.voltages = {axis: 0. for axis in self.ao_channels}
        self.initialized = False

    def connect(self):
        for axis, chan in self.ao_channels.items():
            self.ao_tasks[axis].ao_channels.add_ao_voltage_chan(
                physical_channel=chan,
                min_val=0.0,
                max_val=10.0
            )
        self.initialized = True

    def close(self):
        for task in self.ao_tasks.values():
            task.stop()
            task.close()
        self.initialized = False

    def set_voltage(self, axis, voltage):
        if axis not in self.ao_channels:
            raise DeviceError(f'Unknown AO axis: \'{axis}\'')
        self.ao_tasks[axis].write(voltage)
        self.voltages[axis] = voltage
