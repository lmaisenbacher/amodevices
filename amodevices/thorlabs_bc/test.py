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
    print(device_instance.exposure_time)
    device_instance.exposure_time = 50
    print(device_instance.exposure_time)
    scan_data, image_data = device_instance.read_frame()
    plt.imshow(image_data)
except DeviceError as e:
    print(e.value)
finally:
    if device_instance is not None:
        device_instance.close()


#%%

import numpy as np

scan_data_dict = {
    field: (
        getattr(scan_data, field)
        if len(np.ctypeslib.as_array(getattr(scan_data, field)).shape) == 0
        else np.ctypeslib.as_array(getattr(scan_data, field))
        )
    for field, _ in scan_data._fields_
    }
