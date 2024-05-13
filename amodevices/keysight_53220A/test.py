# -*- coding: utf-8 -*-
"""

@author: Jack Mango/Berkeley
"""

import logging

from amodevices import Keysight53220A
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Keysight 53220A',
    'Address': 'USB0::0x0957::0x1807::MY63100204::INSTR',
    'Timeout': 10.,
    }

try:
    device_instance = Keysight53220A(device)
    print(device_instance.totalize_data)
except DeviceError as e:
    print(e.value)
