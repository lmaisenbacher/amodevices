# -*- coding: utf-8 -*-
"""Tests for the NIDAQ driver's hardware-timed AO mode against a fake
`nidaqmx.Task`: the outputs read back at connect, a generation's
bookkeeping (write layout, retiming only on a change, the reached
sample on a restart or a refusal), and that the default on-demand mode
is untouched. No hardware; run with pytest from the amodevices root or
`uv run --project <unitrap-pydase-apps> pytest amodevices/ni_daq`.
"""

import types

import pytest

from amodevices.ni_daq import ni_daq
from amodevices.dev_exceptions import DeviceError


class FakeTask:
    """The slice of `nidaqmx.Task` the driver touches. `generated` is
    the sample count the fake card has reached; the test sets it."""

    instances = []

    def __init__(self):
        self.ai = []
        self.ao = []
        self.timing = types.SimpleNamespace(calls=[], cfg_samp_clk_timing=self._cfg)
        self.ai_channels = types.SimpleNamespace(add_ai_voltage_chan=self._add_ai)
        self.ao_channels = types.SimpleNamespace(add_ao_voltage_chan=self._add_ao)
        self.out_stream = types.SimpleNamespace(total_samp_per_chan_generated=0)
        self.written = []
        self.running = False
        self.closed = False
        self.readback = {}          # internal AO readback per channel name
        self.refuse_start = False
        self.control_calls = []     # TaskMode values passed to control()
        FakeTask.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _add_ai(self, physical_channel, name_to_assign_to_channel=None, **kw):
        self.ai.append(physical_channel)

    def _add_ao(self, physical_channel, name_to_assign_to_channel=None, **kw):
        self.ao.append(physical_channel)

    def _cfg(self, rate, sample_mode, samps_per_chan):
        self.timing.calls.append((rate, samps_per_chan))

    def control(self, mode):
        self.control_calls.append(mode)

    def read(self, number_of_samples_per_channel=None):
        values = [self.readback.get(c, 0.) for c in self.ai]
        return values[0] if len(values) == 1 else values

    def write(self, data, auto_start=True):
        self.written.append(data)
        self.auto_start = auto_start

    def start(self):
        if self.refuse_start:
            raise ni_daq.DaqError('refused', 0)
        self.running = True
        self.out_stream.total_samp_per_chan_generated = 0

    def stop(self):
        self.running = False

    def is_task_done(self):
        raise AssertionError('is_task_done is 24 ms a call on the USB-6343; '
                             'the driver judges from the sample count')

    def close(self):
        self.closed = True


CONFIG = {'AOTiming': 'hardware',
          'AOChannels': {'x': {'ChannelName': 'Dev1/ao0'},
                         'y': {'ChannelName': 'Dev1/ao1'}},
          'AIChannels': {'x_err': {'ChannelName': 'Dev1/ai0'}}}


@pytest.fixture
def dev(monkeypatch):
    FakeTask.instances.clear()
    monkeypatch.setattr(ni_daq.nidaqmx, 'Task', FakeTask)
    return ni_daq.NIDAQ(CONFIG)


def test_outputs_are_read_back_at_connect(dev, monkeypatch):
    readback = {'Dev1/_ao0_vs_aognd': 0.76, 'Dev1/_ao1_vs_aognd': -0.2}
    original = FakeTask.__init__

    def seeded(self):
        original(self)
        self.readback = readback
    monkeypatch.setattr(FakeTask, '__init__', seeded)
    dev.connect()
    assert dev.ao_voltages == {'x': 0.76, 'y': -0.2}
    assert FakeTask.instances[0].ai == ['Dev1/_ao0_vs_aognd', 'Dev1/_ao1_vs_aognd']
    assert FakeTask.instances[0].closed                    # the readback task is transient
    assert dev.ao_task.ao == ['Dev1/ao0', 'Dev1/ao1']
    assert dev.current_ao_voltages() == {'x': 0.76, 'y': -0.2}


def test_generation_layout_retiming_and_completion(dev):
    dev.connect()
    task = dev.ao_task
    dev.start_ao_generation({'x': [0., 0.5, 1.], 'y': [0., 0., 0.]}, rate_hz=1000.)
    assert task.written[-1] == [[0., 0.5, 1.], [0., 0., 0.]]   # a list per channel
    assert task.timing.calls == [(1000., 3)]
    # Committed once with the timing (the buffer sized first), so later
    # starts skip the programming
    assert task.control_calls == [ni_daq.TaskMode.TASK_COMMIT]
    assert task.out_stream.output_buf_size == 3
    assert not dev.ao_generation_done()
    task.out_stream.total_samp_per_chan_generated = 2
    assert dev.current_ao_voltages() == {'x': 0.5, 'y': 0.}
    task.out_stream.total_samp_per_chan_generated = 3        # the card finished
    assert dev.ao_generation_done()
    dev.finish_ao_generation()
    assert dev.ao_voltages == {'x': 1., 'y': 0.}
    assert task.auto_start is False
    # The same shape again: no retiming
    dev.start_ao_generation({'x': [1., 1., 2.], 'y': [0., 0., 0.]}, rate_hz=1000.)
    assert task.timing.calls == [(1000., 3)]
    task.out_stream.total_samp_per_chan_generated = 3
    dev.finish_ao_generation()
    dev.start_ao_generation({'x': [2., 2.], 'y': [0., 0.]}, rate_hz=1000.)
    assert task.timing.calls == [(1000., 3), (1000., 2)]
    # A new shape: unreserved, retimed, committed again
    assert task.control_calls == [ni_daq.TaskMode.TASK_COMMIT,
                                  ni_daq.TaskMode.TASK_UNRESERVE,
                                  ni_daq.TaskMode.TASK_COMMIT]
    assert task.out_stream.output_buf_size == 2


def test_restart_and_refusal_keep_the_reached_sample(dev):
    dev.connect()
    task = dev.ao_task
    dev.start_ao_generation({'x': [0., 0.25, 0.5, 0.75, 1.], 'y': [0.] * 5}, rate_hz=1000.)
    task.out_stream.total_samp_per_chan_generated = 3        # reached 0.5
    dev.start_ao_generation({'x': [0.5, 0.5], 'y': [0., -1.]}, rate_hz=1000.)
    assert dev.ao_voltages['x'] == 0.5                       # settled where it was
    task.out_stream.total_samp_per_chan_generated = 1        # the new one just started
    task.refuse_start = True
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0.5, 2.], 'y': [0., 0.]}, rate_hz=1000.)
    assert dev.current_ao_voltages() == {'x': 0.5, 'y': 0.}  # not the abandoned target
    assert dev.ao_generation_done()


def test_refused_inputs(dev):
    dev.connect()
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0., 1.]}, rate_hz=1000.)          # y missing
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0., 1.], 'y': [0.]}, rate_hz=1000.)  # ragged
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0.], 'y': [0.]}, rate_hz=1000.)      # one sample
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0., 1.], 'y': [0., 0.]}, rate_hz=0.)


def test_set_voltage_in_hardware_mode_is_a_step(dev):
    dev.connect()
    dev.ao_voltages.update({'x': 0.3, 'y': -0.1})
    dev.set_voltage('y', 0.4)
    assert dev.ao_task.written[-1] == [[0.3, 0.3], [0.4, 0.4]]


def test_on_demand_mode_is_unchanged(monkeypatch):
    FakeTask.instances.clear()
    monkeypatch.setattr(ni_daq.nidaqmx, 'Task', FakeTask)
    dev = ni_daq.NIDAQ({k: v for k, v in CONFIG.items() if k != 'AOTiming'})
    assert dev.ao_timing == 'on-demand' and set(dev.ao_tasks) == {'x', 'y'}
    dev.connect()
    assert dev.ao_task is None
    assert all(t.running for t in dev.ao_tasks.values())
    dev.set_voltage('x', 1.5)
    assert dev.ao_voltages['x'] == 1.5
    with pytest.raises(DeviceError):
        dev.start_ao_generation({'x': [0., 1.], 'y': [0., 0.]}, rate_hz=1000.)
    # The on-demand tasks are never committed or retimed (the piezo server's mode)
    assert all(t.control_calls == [] and t.timing.calls == []
               for t in dev.ao_tasks.values())
