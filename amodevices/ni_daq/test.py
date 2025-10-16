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
    'AOChannels': {
        'x': 'cDAQ1Mod2/ao8',
        'y': 'cDAQ1Mod2/ao9',
        'z': 'cDAQ1Mod2/ao10',
        }
}

daq = NIDAQ(device)

try:
    daq.connect()
    print("DAQ connected.")

    # Set voltages on each AO channel
    for axis, voltage in zip(['x', 'y', 'z', 'g'], [0.5, 2.5, 4.0, 0.0]):
        daq.set_voltage(axis, voltage)
        print(f"Set AO voltage on '{axis}' to {voltage:.2f} V")
except DeviceError as e:
    print(e.value)
finally:
    daq.close()
    print("DAQ connection closed.")
