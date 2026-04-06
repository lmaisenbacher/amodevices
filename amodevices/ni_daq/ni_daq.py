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
        """Build the driver state from a config dict.

        Expects the following top-level keys (all optional):

        - ``AOChannelDefault``: default keyword arguments merged into every
          entry of ``AOChannels``.
        - ``AOChannels``: mapping of axis name to a per-channel config dict.
          Each entry must define ``ChannelName`` (DAQmx physical channel
          name, e.g. ``Dev1/ao0``) and may override ``MinVal``/``MaxVal``.
        - ``AIChannelDefault``, ``AIChannels``: analogous for analog inputs.

        No hardware I/O is performed here — call :meth:`connect` to
        actually reserve the channels and start the tasks.
        """
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
        # All AI channels share a single long-lived `nidaqmx.Task`, so one
        # `.read()` returns values for every configured channel at once.
        self.ai_task: nidaqmx.Task | None = None
        self._ai_axis_order: list[str] = []
        self.ai_voltages = {axis: None for axis in self.ai_channels}

        self.initialized = False

    def connect(self):
        """Reserve all configured channels and start the DAQmx tasks.

        Each AO channel gets its own `nidaqmx.Task`, while all AI channels
        share a single task so one `.read()` returns every AI value at
        once. Both AO and AI tasks are explicitly started here and left in
        the RUNNING state for the lifetime of the driver, so each
        subsequent `.read()`/`.write()` only transfers data and does not
        pay the DAQmx verify/commit/start/stop state-change overhead (~20
        ms per transition on Windows).

        If AI setup fails partway through, the half-built AI task is
        closed before the exception propagates, leaving the driver in a
        clean "not initialized" state.
        """
        # Per-axis AO tasks, explicitly started so that each `.write()` just
        # updates the DAC without paying verify/commit/start/stop overhead.
        for axis, chan in self.ao_channels.items():
            self.ao_tasks[axis].ao_channels.add_ao_voltage_chan(
                physical_channel=chan['ChannelName'],
                min_val=chan.get('MinVal', -10.),
                max_val=chan.get('MaxVal', 10.)
            )
            self.ao_tasks[axis].start()
        # Build a single AI task with all channels, then explicitly start it
        # so subsequent `.read()` calls don't pay per-call commit/start/stop
        # state-change overhead.
        self.ai_task = nidaqmx.Task()
        self._ai_axis_order = []
        try:
            for axis, chan in self.ai_channels.items():
                self.ai_task.ai_channels.add_ai_voltage_chan(
                    physical_channel=chan['ChannelName'],
                    name_to_assign_to_channel=axis,
                    min_val=chan.get('MinVal', -10.),
                    max_val=chan.get('MaxVal', 10.)
                )
                self._ai_axis_order.append(axis)
            if self._ai_axis_order:
                self.ai_task.start()
        except Exception:
            try:
                self.ai_task.close()
            finally:
                self.ai_task = None
                self._ai_axis_order = []
            raise
        self.initialized = True

    def close(self):
        """Stop and release all DAQmx tasks.

        Must be called on shutdown to free the reserved channels, otherwise
        a subsequent :meth:`connect` (in this or any other process) will
        fail with ``DAQmxErrorResourceReserved``.
        """
        for task in self.ao_tasks.values():
            task.stop()
            task.close()
        if self.ai_task is not None:
            try:
                self.ai_task.stop()
            finally:
                self.ai_task.close()
            self.ai_task = None
            self._ai_axis_order = []
        self.initialized = False

    def read_all_ai_voltages(self) -> dict[str, float]:
        """Read all configured AI channels in a single DAQmx call.

        Returns a dict mapping axis name to voltage (V). The dict preserves
        the order in which channels were added in :meth:`connect`, which
        matches the iteration order of the ``AIChannels`` config entry.
        Also updates the cached ``self.ai_voltages`` dict as a side effect.

        Raises `DeviceError` if the AI task is not initialized (i.e.
        :meth:`connect` has not been called, or no AI channels are
        configured) or if the underlying `nidaqmx.Task.read` call fails.
        """
        if self.ai_task is None or not self._ai_axis_order:
            raise DeviceError('AI task not initialized')
        try:
            result = self.ai_task.read()
        except Exception as e:
            raise DeviceError(str(e)) from e
        # A 1-channel task returns a scalar; N>=2 returns a list of floats.
        if len(self._ai_axis_order) == 1:
            values = [float(result)]
        else:
            values = [float(v) for v in result]
        voltages = dict(zip(self._ai_axis_order, values))
        self.ai_voltages.update(voltages)
        return voltages

    def read_voltage(self, axis):
        """Read the voltage (V) on a single AI channel.

        Thin shim over :meth:`read_all_ai_voltages` that returns only the
        requested axis. Since the underlying call already reads every
        configured AI channel in one DAQmx round-trip, calling this method
        once per axis is wasteful when you need multiple values —
        :meth:`read_all_ai_voltages` directly is preferred in that case.

        Raises `DeviceError` if `axis` is not a configured AI channel or
        if the underlying read fails.
        """
        if axis not in self.ai_channels:
            raise DeviceError(f'Unknown AI axis: \'{axis}\'')
        voltages = self.read_all_ai_voltages()
        return voltages[axis]

    def set_voltage(self, axis, voltage):
        """Write a voltage (V) to a single AO channel.

        The target axis is looked up in the per-axis AO task dict and
        written via ``nidaqmx.Task.write``. Because the AO task is already
        running (see :meth:`connect`), this call only transfers the sample
        to the DAC and returns — no task state changes occur. Also updates
        the cached ``self.ao_voltages`` dict as a side effect.

        Raises `DeviceError` if `axis` is not a configured AO channel or
        if the underlying DAQmx write fails.
        """
        if axis not in self.ao_channels:
            raise DeviceError(f'Unknown AO axis: \'{axis}\'')
        try:
            self.ao_tasks[axis].write(voltage)
        except DaqWriteError as e:
            raise DeviceError(e)
        self.ao_voltages[axis] = voltage
