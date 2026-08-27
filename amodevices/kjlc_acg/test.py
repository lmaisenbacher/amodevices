# -*- coding: utf-8 -*-
"""
Manual hardware test for the KJLC ACG capacitance manometer driver.
"""

import logging
import time

from amodevices import KJLCACG
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'KJLC ACG capacitance manometer',
    'Address': 'COM1',
    'Timeout': 1,
    }

try:
    device_instance = KJLCACG(device)
    device_instance.connect()
except DeviceError as e:
    print(e.value)
else:
    # The gauge broadcasts a message every 10 ms; give the buffer a
    # moment to fill before the first read
    time.sleep(0.1)
    for _ in range(10):
        print(f'Pressure (Torr): {device_instance.read_pressure()}')
        time.sleep(1)
