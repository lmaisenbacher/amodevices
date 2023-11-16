# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging

from amodevices import RigolRSA3000
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Rigol RSA3000',
    'Address': 'TCPIP0::192.168.50.30::inst0::INSTR',
    'Timeout': 10.,
    }

try:
    device_instance = RigolRSA3000(device)
except DeviceError as e:
    print(e.value)
