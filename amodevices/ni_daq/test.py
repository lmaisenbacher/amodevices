#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:49:28 2025

@author: Isaac Pope and Lothar Maisenbacher/UC Berkeley
"""

from amodevices import NIDAQ
from amodevices.dev_exceptions import DeviceError

# Configuration with your device names
device = {
    'AOChannelDefault': {
        'MinVal': 0.,
        'MaxVal': 10.,
        },
    'AOChannels': {
        'x': {'ChannelName': 'cDAQ1Mod2/ao8',},
        'y': {'ChannelName': 'cDAQ1Mod2/ao9',},
        'z': {'ChannelName': 'cDAQ1Mod2/ao10',},
        },
    'AIChannelDefault': {
        'MinVal': -10.,
        'MaxVal': 10.,
        },
    'AIChannels': {
        'x': {'ChannelName': 'cDAQ1Mod4/ai8',},
        'y': {'ChannelName': 'cDAQ1Mod4/ai9',},
        'z': {'ChannelName': 'cDAQ1Mod4/ai10',},
        }
}

daq = NIDAQ(device)

axes = ['x', 'y', 'z']

try:
    daq.connect()
    print("DAQ connected.")

    # Read voltages on each AI channel
    for axis in axes:
        voltage = daq.read_voltage(axis)
        print(f"Read AI voltage on '{axis}' as {voltage:.6f} V")

    # Set voltages on each AO channel
    for axis, voltage in zip(axes, [0.5, 1.0, 1.5]):
        daq.set_voltage(axis, voltage)
        print(f"Set AO voltage on '{axis}' to {voltage:.2f} V")

    # Read voltages on each AI channel
    for axis in axes:
        voltage = daq.read_voltage(axis)
        print(f"Read AI voltage on '{axis}' as {voltage:.6f} V")

except DeviceError as e:
    print(e.value)
finally:
    daq.close()
    print("DAQ connection closed.")
