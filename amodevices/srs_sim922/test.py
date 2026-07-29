# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging
from amodevices import SRSSIM922
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

device = {
    "Device": "SRS SIM922",
    "Address": "COM19",
    "Timeout": 10.,
    "SerialConnectionParams":
        {
            "baudrate": 9600,
            "bytesize": 8,
            "stopbits": 1,
            "parity": "N"
        }
    }

try:
    device_instance = SRSSIM922(device)
    device_instance.connect()
    for chan_id in range(1, 5):
        print(f'Channel ID: {chan_id}')
        temperature = device_instance.read_temperature(chan_id)
        print(f'Temperature: {temperature} K')
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
