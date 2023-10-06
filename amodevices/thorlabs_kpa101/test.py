# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging
import time
from amodevices import ThorlabsKPA101
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

device = {
    'Device': 'Thorlabs KPA101',
    'SerialNumber': 69252254,
    }

try:
    device_instance = ThorlabsKPA101(device)
    device_instance.connect()
    print(device_instance.KinesisQuadDetector.get_full_info())
    print(device_instance.xout)
    print(device_instance.xdiff)
    print(device_instance.ydiff)
    time.sleep(1)
    print(device_instance.ydiff)
    print(device_instance.operation_mode)
    device_instance.operation_mode = 'open_loop'
    print(device_instance.operation_mode)
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
