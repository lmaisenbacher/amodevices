#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  1 16:49:28 2025

@author: isaac/uc berkeley
"""

from amodevices import NI9264andNI9205
import time

import sys
from pathlib import Path

# # Add the path to the folder containing `thorlabs-piezo-controller` to sys.path
# channel_path = Path(__file__).resolve().parents[3] / 'unitrap-pydase-apps' / 'thorlabs-piezo-controller'
# sys.path.append(str(channel_path))

# # Now you can import the Channel class
# from server import Channel  # use the actual module name
# from pint import UnitRegistry

# Configuration with your device names
device = {
    'AODevice': 'cDAQ1Mod2', #replace accordingly
    'AIDevice': 'cDAQ1Mod4', #replace accordingly
}

daq = NI9264andNI9205(device)

try:
    daq.connect()
    print("DAQ connected.")

    # Set voltages on each AO channel
    for axis, voltage in zip(['x', 'y', 'z', 'g'], [1.0, 2.5, 4.0, 0.0]):
        daq.set_voltage(axis, voltage)
        print(f"Set AO voltage on '{axis}' to {voltage:.2f} V")
        time.sleep(0.2)  # Small delay for hardware to settle

    print("\nReading back voltages from AI channels:")

    # Read back the voltages from AI
    for axis in ['x', 'y', 'z']:
        read_voltage = daq.read_voltage(axis)
        print(f"AI voltage on '{axis}': {read_voltage:.3f} V")

finally:
    daq.close()
    print("DAQ connection closed.")

# # Initialize Pint unit registry
# u = UnitRegistry()

# # Configuration for NI DAQ modules
# config = {
#     'AODevice': 'cDAQ1Mod2',
#     'AIDevice': 'cDAQ1Mod4',
# }

# # Initialize DAQ interface
# daq = NI9264andNI9205(config)
# daq.connect()

# # Create Channel instances for x, y, z axes
# channel_x = Channel(daq, 'x', update_rate=1*u.s)
# channel_y = Channel(daq, 'y', update_rate=1*u.s)
# channel_z = Channel(daq, 'z', update_rate=1*u.s)
# channel_g = Channel(daq, 'g', update_rate=1*u.s)

# # Set voltages
# channel_x.set_voltage = 1.0 * u.V
# channel_y.set_voltage = 5.0 * u.V
# channel_z.set_voltage = 6.0 * u.V
# channel_g.set_voltage = 0.0 * u.V

# print("Voltages set. Waiting for DAQ to settle...")
# time.sleep(0.2)

# # Manually read from DAQ
# channel_x._read_from_device()
# channel_y._read_from_device()
# channel_z._read_from_device()

# # Print the results
# print(f"X voltage: {channel_x.voltage}")
# print(f"Y voltage: {channel_y.voltage}")
# print(f"Z voltage: {channel_z.voltage}")

# # Cleanup
# daq.close()
