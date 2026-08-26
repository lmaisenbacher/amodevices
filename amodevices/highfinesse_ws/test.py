# -*- coding: utf-8 -*-
"""
Manual hardware test for the HighFinesse WS wavemeter driver.
Requires the HighFinesse wavemeter software running on the same PC.
"""

import logging
import time

from amodevices import HighFinesseWS
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'HighFinesse WS/7 wavemeter',
    # 'ReadOnce': True,
    # 'PulseMode': 0,
    }

try:
    device_instance = HighFinesseWS(device)
except DeviceError as e:
    print(e.value)
else:
    print(f'Measurement mode: {device_instance.get_pulse_mode()}')
    print(f'Automatic exposure: {device_instance.get_automatic_exposure()}')
    print(f'Exposures (ms): {device_instance.get_exposures()}')
    print(f'Levels: {device_instance.get_levels()}')
    print('Frequency readings over 2 s (THz; error codes <= 0 pass through,'
          ' with \'ReadOnce\' already-read results read as 0):')
    for _ in range(20):
        print(device_instance.get_frequency())
        time.sleep(0.1)
