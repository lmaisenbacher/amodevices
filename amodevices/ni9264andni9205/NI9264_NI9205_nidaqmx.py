#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:25:00 2025

@author: isaac/uc berkeley
"""

import nidaqmx
from nidaqmx.constants import AcquisitionType
import time


class NI9264andNI9205:
    """
    NI 9264 (AO) and NI 9205 (AI) for voltage output and input, respectively.
    Maps:
        - AO channels: 'x' → ao0, 'y' → ao1, 'z' → ao2
        - AI channels: 'x' → ai0, 'y' → ai1, 'z' → ai2
    """

    def __init__(self, config):
        self.ao_device = config['AODevice']  # e.g. 'cDAQ1Mod1'
        self.ai_device = config['AIDevice']  # e.g. 'cDAQ1Mod2'

        self.ao_channels = {
            'x': f"{self.ao_device}/ao0",
            'y': f"{self.ao_device}/ao1",
            'z': f"{self.ao_device}/ao2",
        }

        self.ai_channels = {
            'x': f"{self.ai_device}/ai0",
            'y': f"{self.ai_device}/ai1",
            'z': f"{self.ai_device}/ai2",
        }

        self.ao_tasks = {axis: nidaqmx.Task() for axis in self.ao_channels}
        self.ai_tasks = {axis: nidaqmx.Task() for axis in self.ai_channels}

        self._voltage_cache = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self.initialized = False

    def connect(self):
        for axis, chan in self.ao_channels.items():
            self.ao_tasks[axis].ao_channels.add_ao_voltage_chan(
                physical_channel=chan,
                min_val=0.0,
                max_val=10.0
            )

        for axis, chan in self.ai_channels.items():
            self.ai_tasks[axis].ai_channels.add_ai_voltage_chan(
                physical_channel=chan,
                min_val=0.0,
                max_val=10.0
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

    def set_voltage(self, axis, voltage):
        if axis not in self.ao_channels:
            raise ValueError(f"Unknown AO axis: {axis}")
        self.ao_tasks[axis].write(voltage)
        self._voltage_cache[axis] = voltage

    def read_voltage(self, axis):
        if axis not in self.ai_channels:
            raise ValueError(f"Unknown AI axis: {axis}")
        return self.ai_tasks[axis].read()
