# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Isaac Pope/UC Berkeley
"""

import logging
import matplotlib.pyplot as plt
import time
plt.ion()

from amodevices import FLIRBoson
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'FLIR Boson',
    'Address': 'COM3',
    'CV2Config': {
        'DeviceIndex': 0,
        'Resolution': [320, 256],
        },
    'Radiometry': {
        'TempWindow': 295,
        'TransmissionWindow': 100,
        },
    }

fig, ax = plt.subplots(num='FLIR Boson', clear=True)

try:
    device_instance = FLIRBoson(device)
    stream_ret, frame = device_instance.read_frame()
    print(stream_ret)
    ax.imshow(frame)
    while True:
        stream_ret, frame = device_instance.read_frame()
        print(stream_ret)
        ax.imshow(frame)
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
        time.sleep(1)
except DeviceError as e:
    print(e.value)
finally:
    device_instance.close()
