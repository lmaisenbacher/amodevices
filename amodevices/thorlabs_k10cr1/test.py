# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging
from amodevices import ThorlabsK10CR1

logger = logging.getLogger(__name__)

device = {
    'Device': 'Thorlabs K10CR1',
    'SerialNumber': 55193694,
    }

device = ThorlabsK10CR1(device)
device.connect()
kinesis = device.kinesis
device.kinesis.ISC_MoveRelative(device.serial_number_byte, 1000000)
