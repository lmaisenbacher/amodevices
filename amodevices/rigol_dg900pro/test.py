# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging

from amodevices import RigolDG900Pro
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Rigol DG900Pro',
    'Address': 'TCPIP0::192.168.50.42::inst0::INSTR',
    'Timeout': 10.,
    }

try:
    device_instance = RigolDG900Pro(device)
    channel_2 = device_instance.channel(2)
    print(channel_2.frequency)
    channel_2.frequency = 9.195e6
    print(channel_2.frequency)
except DeviceError as e:
    print(e.value)
