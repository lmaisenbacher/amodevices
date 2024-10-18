# -*- coding: utf-8 -*-
"""
Created on Thu Oct 17 10:48:14 2024

@author: Lothar Maisenbacher/UC Berkeley
"""

import logging
import matplotlib.pyplot as plt

from amodevices import ThorlabsBC
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Thorlabs BC207',
    'SerialNumber': 18416,
    }

device_instance = None
try:
    device_instance = ThorlabsBC(device)
    print(f'Auto exposure: {device_instance.auto_exposure}')
    device_instance.auto_exposure = True
    print(f'Auto exposure: {device_instance.auto_exposure}')
    print(f'Exposure time: {device_instance.exposure_time:.3f} ms')
    device_instance.exposure_time = 50
    print(f'Exposure time: {device_instance.exposure_time:.3f} ms')
    print(f'Auto exposure: {device_instance.auto_exposure}')
    scan_data, image_data = device_instance.read_frame()
    plt.imshow(image_data)
    device_instance.auto_exposure = True
    print(f'Auto exposure: {device_instance.auto_exposure}')
except DeviceError as e:
    print(e.value)
finally:
    if device_instance is not None:
        device_instance.close()
