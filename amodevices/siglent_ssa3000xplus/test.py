# -*- coding: utf-8 -*-
"""
Created on Mon Feb 10 15:40:58 2025

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging

from amodevices import SiglentSSA3000XPlus
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Siglent SSA 3021X Plus',
    'Address': 'TCPIP0::192.168.50.40::inst0::INSTR',
    'Timeout': 10.,
    }

try:
    device_instance = SiglentSSA3000XPlus(device)
    trace1 = device_instance.trace(1)
    print(trace1.detector)
except DeviceError as e:
    print(e.value)
