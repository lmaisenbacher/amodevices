# -*- coding: utf-8 -*-
"""
Manual hardware test for the KJLC 354 ion pressure gauge driver.
"""

import logging
import time

from amodevices import KJLC354
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'KJLC 354 ion pressure gauge',
    'Address': 'COM1',
    'Timeout': 1,
    'DeviceSpecificParams': {
        'InternalAddress': '01',
        },
    }

try:
    device_instance = KJLC354(device)
    device_instance.connect()
except DeviceError as e:
    print(e.value)
else:
    for _ in range(10):
        print(f'Pressure (Torr): {device_instance.read_pressure()}')
        time.sleep(1)
