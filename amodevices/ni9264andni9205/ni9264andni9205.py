#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug  4 11:50:18 2025

@author: Isaac Pope/UC Berkeley
"""

from PyDAQmx import Task
from PyDAQmx.DAQmxConstants import *
from PyDAQmx.DAQmxFunctions import *
import numpy as np
import logging
import threading

# Thread lock to avoid writing/reading of serial ports from different threads
# at the same time
# All readers have to lock this
read_lock = threading.Lock()

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class NI9264andNI9205(dev_generic.Device):
    """
    NI 9264 (AO) and NI 9205 (AI) for voltage output and input, respectively.
    Maps:
        - AO channels: 'x' → ao8, 'y' → ao9, 'z' → ao10, 'g' → a011
        - AI channels: 'x' → ai18, 'y' → ai19, 'z' → ai20
    """

    def __init__(self, device):
        super(NI9264andNI9205, self).__init__(device)

        self.ao_device = device['AODevice']  # e.g. 'cDAQ1Mod1'
        self.ai_device = device['AIDevice']  # e.g. 'cDAQ1Mod2'

        self.ao_channels = {
            'x': f"{self.ao_device}/ao8",
            'y': f"{self.ao_device}/ao9",
            'z': f"{self.ao_device}/ao10",
            'g': f"{self.ao_device}/ao11",
        }

        self.ai_channels = {
            'x': f"{self.ai_device}/ai18",
            'y': f"{self.ai_device}/ai19",
            'z': f"{self.ai_device}/ai20",
        }

        self.ao_tasks = {axis: Task() for axis in self.ao_channels}
        self.ai_tasks = {axis: Task() for axis in self.ai_channels}

        self._voltage_cache = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        self.initialized = False

    def connect(self):
        for axis, chan in self.ao_channels.items():
            self.ao_tasks[axis].CreateAOVoltageChan(
                chan, "", 0.0, 5.0, DAQmx_Val_Volts, None
            )

        for axis, chan in self.ai_channels.items():
            self.ai_tasks[axis].CreateAIVoltageChan(
                chan, "", DAQmx_Val_Cfg_Default, 0.0, 5.0, DAQmx_Val_Volts, None
            )

        self.initialized = True

    def close(self):
        for task in self.ao_tasks.values():
            task.StopTask()
            task.ClearTask()

        for task in self.ai_tasks.values():
            task.StopTask()
            task.ClearTask()

        self.initialized = False

    def set_voltage(self, axis, voltage):
        if axis not in self.ao_channels:
            raise ValueError(f"Unknown AO axis: {axis}")

        data = np.array([voltage], dtype=np.float64)
        self.ao_tasks[axis].WriteAnalogF64(
            1,  # numSampsPerChan
            1,  # autoStart
            10.0,  # timeout
            DAQmx_Val_GroupByChannel,
            data,
            None,
            None
        )
        self._voltage_cache[axis] = voltage

    def read_voltage(self, axis):
        if axis not in self.ai_channels:
            raise ValueError(f"Unknown AI axis: {axis}")

        data = np.zeros((1,), dtype=np.float64)
        read = int32()
        self.ai_tasks[axis].ReadAnalogF64(
            1,  # numSampsPerChan
            10.0,  # timeout
            DAQmx_Val_GroupByChannel,
            data,
            1,  # arraySizeInSamps
            read,
            None
        )
        return data[0]
