# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging
from amodevices import SRSCTC100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

device = {
    "Device": "SRS CTC100",
    "Address": "COM3",
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
    device_instance = SRSCTC100(device)
    device_instance.connect()
    for chan_id in ["T50K"]:
        print(f'Channel ID: {chan_id}')
        temperature = device_instance.read_temperature(chan_id)
        print(f'Temperature: {temperature} K')
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
