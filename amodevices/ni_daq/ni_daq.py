#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:25:00 2025

@author: Isaac Pope and Lothar Maisenbacher/UC Berkeley

Device driver for NI DAQ devices using DAQmx interface.

Analog output runs in one of two modes, ``AOTiming`` in the config:

- ``'on-demand'`` (default): one software-timed task per AO channel,
  :meth:`set_voltage` writes a sample and the DAC jumps to it.
- ``'hardware'``: one hardware-timed task over all AO channels, and a
  move is a finite waveform the card clocks out by itself
  (:meth:`start_ao_generation`), all channels in lockstep — the Newport
  FSM-300 server ramps its mirror this way so a scan step does not kick
  the optical table. The output holds the last sample after each
  generation. :meth:`ao_generation_done`/:meth:`finish_ao_generation`
  poll and close a generation without ever blocking on it, and
  :meth:`current_ao_voltages` gives the samples on the outputs at this
  instant, from the card's own generated-sample count. In this mode
  :meth:`set_voltage` is a two-sample generation (a step).
"""

import logging
import warnings

import nidaqmx
from nidaqmx.constants import AcquisitionType
from nidaqmx.errors import DaqError, DaqWarning, DaqWriteError

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

#: The two analog-output timing modes (config ``AOTiming``)
AO_TIMING_ON_DEMAND = 'on-demand'
AO_TIMING_HARDWARE = 'hardware'
#: A hardware-timed finite generation needs at least this many samples
AO_GENERATION_MIN_SAMPLES = 2


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
        - ``AOTiming``: ``'on-demand'`` (default) or ``'hardware'``, see
          the module docstring.
        - ``AIChannelDefault``, ``AIChannels``: analogous for analog inputs.

        No hardware I/O is performed here — call :meth:`connect` to
        actually reserve the channels and start the tasks.
        """
        self.ao_channel_default = config.get('AOChannelDefault', {})
        self.ao_channels = {
            axis: {**self.ao_channel_default, **chan}
            for axis, chan in config.get('AOChannels', {}).items()}
        self.ao_timing = config.get('AOTiming', AO_TIMING_ON_DEMAND)
        if self.ao_timing not in (AO_TIMING_ON_DEMAND, AO_TIMING_HARDWARE):
            raise DeviceError(
                f'AOTiming must be \'{AO_TIMING_ON_DEMAND}\' or'
                f' \'{AO_TIMING_HARDWARE}\', got \'{self.ao_timing}\'')
        # On-demand mode: one task per axis. Hardware mode: one task over
        # every axis (`self.ao_task`, built in `connect`), the axes in the
        # order of the config
        self.ao_tasks = ({axis: nidaqmx.Task() for axis in self.ao_channels}
                         if self.ao_timing == AO_TIMING_ON_DEMAND else {})
        self.ao_task: nidaqmx.Task | None = None
        self._ao_axis_order: list[str] = list(self.ao_channels)
        # The generation in progress (hardware mode): its samples per axis
        # and their count, None between generations; the (rate, samples)
        # the task's timing is configured for, retimed only on a change
        self._ao_generation = None
        self._ao_timing_configured = None
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
        if self.ao_timing == AO_TIMING_HARDWARE:
            # The outputs keep their voltages across tasks and processes,
            # so a move must start from what they carry now, not from
            # zero: seed the cache from the card's own AO readback
            # channels (X Series: Dev1/_ao0_vs_aognd) before any
            # generation. A device without them leaves the cache at None
            # (a first move then starts from 0 V, with a warning).
            self._read_ao_outputs()
            # One task over every axis; timing is configured per
            # generation (`start_ao_generation`), which also starts it
            self.ao_task = nidaqmx.Task()
            for axis, chan in self.ao_channels.items():
                self.ao_task.ao_channels.add_ao_voltage_chan(
                    physical_channel=chan['ChannelName'],
                    name_to_assign_to_channel=axis,
                    min_val=chan.get('MinVal', -10.),
                    max_val=chan.get('MaxVal', 10.)
                )
        else:
            # Per-axis AO tasks, explicitly started so that each `.write()`
            # just updates the DAC without paying verify/commit/start/stop
            # overhead.
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
        if self.ao_task is not None:
            try:
                self._settle_generation()
            finally:
                self.ao_task.close()
            self.ao_task = None
            self._ao_timing_configured = None
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

        In hardware-timed mode (see the module docstring) the write is a
        two-sample generation of `voltage` on `axis`, the other axes held
        at their current values: the DAC steps just the same.

        Raises `DeviceError` if `axis` is not a configured AO channel or
        if the underlying DAQmx write fails.
        """
        if axis not in self.ao_channels:
            raise DeviceError(f'Unknown AO axis: \'{axis}\'')
        if self.ao_timing == AO_TIMING_HARDWARE:
            current = self.current_ao_voltages()
            samples = {a: [current[a] if current[a] is not None else 0.] * 2
                       for a in self._ao_axis_order}
            samples[axis] = [voltage, voltage]
            self.start_ao_generation(samples, rate_hz=1000.)
            return
        try:
            self.ao_tasks[axis].write(voltage)
        except DaqWriteError as e:
            # Convention: pass a stringified message into `DeviceError`,
            # chain the original exception via `from` so its traceback
            # survives on `__cause__` for local debugging.
            raise DeviceError(str(e)) from e
        self.ao_voltages[axis] = voltage

    # ------------------------------------------------------------------
    # Hardware-timed generation (AOTiming 'hardware')
    # ------------------------------------------------------------------

    def _read_ao_outputs(self):
        """Seed ``ao_voltages`` from the card's internal AO readback
        channels, one short on-demand read; a device without them (or a
        refused read) logs a warning and leaves the cache as it is."""
        try:
            with nidaqmx.Task() as task:
                for axis, chan in self.ao_channels.items():
                    device, name = chan['ChannelName'].split('/', 1)
                    task.ai_channels.add_ai_voltage_chan(
                        f'{device}/_{name}_vs_aognd',
                        name_to_assign_to_channel=axis,
                        min_val=-10., max_val=10.)
                result = task.read()
        except DaqError as e:
            logger.warning(
                'Could not read the AO outputs back (%s); the first move of'
                ' each axis starts from 0 V', str(e).splitlines()[0])
            return
        values = [float(result)] if len(self.ao_channels) == 1 else [
            float(v) for v in result]
        for axis, value in zip(self.ao_channels, values):
            self.ao_voltages[axis] = value
        logger.info('AO outputs read back: %s', ', '.join(
            f'{axis} {value:+.4f} V' for axis, value in zip(self.ao_channels, values)))

    def _generation_index(self, gen):
        """The index of the sample the card has reached in `gen`."""
        count = int(self.ao_task.out_stream.total_samp_per_chan_generated)
        return min(max(count - 1, 0), gen['n'] - 1)

    def _settle_generation(self):
        """Stop the generation in progress, wherever it got to, and make
        the samples the card had reached the cached ``ao_voltages``: what
        the outputs carry from here on. Nothing to do without one."""
        gen = self._ao_generation
        if gen is None:
            return
        try:
            reached = self._generation_index(gen)
            with warnings.catch_warnings():
                # Stopping a finite task before its last sample is a
                # DAQmx warning (200010); here it is the intent
                warnings.simplefilter('ignore', DaqWarning)
                self.ao_task.stop()
        finally:
            self._ao_generation = None
        for axis, row in zip(self._ao_axis_order, gen['samples']):
            self.ao_voltages[axis] = row[reached]

    def start_ao_generation(self, samples, rate_hz):
        """Start clocking `samples` out of every AO channel at `rate_hz`.

        `samples` maps every configured axis to a sequence of voltages
        (V), all of the same length, at least `AO_GENERATION_MIN_SAMPLES`:
        a moving axis gets its ramp, a resting one its current voltage
        repeated. The card generates them at `rate_hz` samples per second
        and holds the last ones. A generation still running is stopped
        first, wherever it got to, and that is where the outputs stay if
        the new one is refused (see :meth:`current_ao_voltages`). The
        task is retimed only when the sample count or the rate changes,
        so a run of equal moves stays in the committed state. Returns at
        once; poll :meth:`ao_generation_done` and then call
        :meth:`finish_ao_generation`.

        Raises `DeviceError` outside hardware-timed mode, for a missing
        or ragged axis, a rate that is not positive, or when DAQmx
        refuses.
        """
        if self.ao_timing != AO_TIMING_HARDWARE or self.ao_task is None:
            raise DeviceError(
                'Hardware-timed generation needs AOTiming \'hardware\' and'
                ' a connected device')
        missing = [a for a in self._ao_axis_order if a not in samples]
        if missing:
            raise DeviceError(f'No samples for AO axis {missing}')
        rows = [[float(v) for v in samples[a]] for a in self._ao_axis_order]
        n = len(rows[0])
        if n < AO_GENERATION_MIN_SAMPLES or any(len(r) != n for r in rows):
            raise DeviceError(
                f'Every axis needs the same number of samples, at least'
                f' {AO_GENERATION_MIN_SAMPLES}')
        rate_hz = float(rate_hz)
        if not rate_hz > 0.:
            raise DeviceError(f'The sample rate must be positive, got {rate_hz}')
        try:
            self._settle_generation()
            if self._ao_timing_configured != (rate_hz, n):
                self.ao_task.timing.cfg_samp_clk_timing(
                    rate=rate_hz, sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=n)
                self._ao_timing_configured = (rate_hz, n)
            # A one-channel task takes a flat list, several channels a
            # list per channel
            self.ao_task.write(rows if len(rows) > 1 else rows[0],
                               auto_start=False)
            self.ao_task.start()
        except (DaqError, DaqWriteError) as e:
            raise DeviceError(str(e)) from e
        self._ao_generation = {'samples': rows, 'n': n, 'rate_hz': rate_hz}

    def ao_generation_done(self):
        """Whether the generation in progress has clocked out its last
        sample (True as well when none is in progress). Never blocks."""
        if self._ao_generation is None:
            return True
        try:
            return bool(self.ao_task.is_task_done())
        except DaqError as e:
            raise DeviceError(str(e)) from e

    def finish_ao_generation(self):
        """Stop the generation in progress — completed, or abandoned
        wherever it got to — the samples the card reached becoming the
        cached ``ao_voltages``."""
        try:
            self._settle_generation()
        except DaqError as e:
            raise DeviceError(str(e)) from e

    def current_ao_voltages(self):
        """The voltages on the AO channels at this instant: during a
        generation the samples the card has reached (its own count of
        generated samples), otherwise the last written values (None for
        an axis never written)."""
        gen = self._ao_generation
        if gen is None:
            return dict(self.ao_voltages)
        try:
            index = self._generation_index(gen)
        except DaqError as e:
            raise DeviceError(str(e)) from e
        return {axis: row[index]
                for axis, row in zip(self._ao_axis_order, gen['samples'])}
