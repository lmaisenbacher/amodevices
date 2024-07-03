# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging
from amodevices import ThorlabsMDT693B
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

device = {
    "Device": "Thorlabs MDT693B",
    "Address": "COM14",
    "Timeout": 1,
    "SerialConnectionParams":
        {
            "baudrate": 115200,
            "bytesize": 8,
            "stopbits": 1,
            "parity": "N"
        }
    }

try:
    device_instance = ThorlabsMDT693B(device)
    device_instance.connect()
    for axis in ['x', 'y', 'z']:
        voltage = device_instance.read_voltage(axis)
        print(f'Voltage on axis {axis}: {voltage} V')
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
