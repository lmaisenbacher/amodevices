# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging

from amodevices import ThorlabsPM100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Thorlabs PM100',
    'Address': 'USB0::0x1313::0x8078::P0035093::INSTR',
    }

try:
    device_instance = ThorlabsPM100(device)
except DeviceError as e:
    print(e.value)
