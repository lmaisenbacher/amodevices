#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:25:00 2025

@author: Isaac Pope and Lothar Maisenbacher/UC Berkeley

Device driver for NI DAQ devices using DAQmx interface.
"""

import logging
import nidaqmx
from nidaqmx.errors import DaqWriteError

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class NIDAQ(dev_generic.Device):
    """
    Device driver for NI DAQ devices using DAQmx interface.
    """

    def __init__(self, config):
        self.ao_channel_default = config.get('AOChannelDefault', {})
        self.ao_channels = {
            axis: {**self.ao_channel_default, **chan}
            for axis, chan in config.get('AOChannels', {}).items()}
        self.ao_tasks = {axis: nidaqmx.Task() for axis in self.ao_channels}
        self.ao_voltages = {axis: None for axis in self.ao_channels}

        self.ai_channel_default = config.get('AIChannelDefault', {})
        self.ai_channels = {
            axis: {**self.ai_channel_default, **chan}
            for axis, chan in config.get('AIChannels', {}).items()}
        self.ai_tasks = {axis: nidaqmx.Task() for axis in self.ai_channels}
        self.ai_voltages = {axis: None for axis in self.ai_channels}

        self.initialized = False

    def connect(self):
        for axis, chan in self.ao_channels.items():
            self.ao_tasks[axis].ao_channels.add_ao_voltage_chan(
                physical_channel=chan['ChannelName'],
                min_val=chan.get('MinVal', -10.),
                max_val=chan.get('MaxVal', 10.)
            )
        for axis, chan in self.ai_channels.items():
            self.ai_tasks[axis].ai_channels.add_ai_voltage_chan(
                physical_channel=chan['ChannelName'],
                min_val=chan.get('MinVal', -10.),
                max_val=chan.get('MaxVal', 10.)
            )
        self.initialized = True

    def close(self):
        for task in self.ao_tasks.values():
            task.stop()
            task.close()
        for task in self.ai_tasks.values():
            task.stop()
            task.close()
        self.initialized = False

    def read_voltage(self, axis):
        if axis not in self.ai_channels:
            raise DeviceError(f'Unknown AI axis: \'{axis}\'')
        voltage = self.ai_tasks[axis].read()
        self.ai_voltages[axis] = voltage
        return voltage

    def set_voltage(self, axis, voltage):
        if axis not in self.ao_channels:
            raise DeviceError(f'Unknown AO axis: \'{axis}\'')
        try:
            self.ao_tasks[axis].write(voltage)
        except DaqWriteError as e:
            raise DeviceError(e)
        self.ao_voltages[axis] = voltage
