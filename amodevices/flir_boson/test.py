# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Isaac Pope/UC Berkeley
"""

import logging

from amodevices import FLIRBoson
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'FLIR Boson',
    'Address': 'COM4',
    'CV2Config': {
        'DeviceIndex': 1,
        'Resolution': [320, 256],
        },
    'Radiometry': {
        'TempWindow': 295,
        'TransmissionWindow': 100,
        },
    }

try:
    device_instance = FLIRBoson(device)
    stream_ret, frame = device_instance.read_frame()
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
