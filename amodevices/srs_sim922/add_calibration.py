# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley

Add user calibration for diode temperature sensor for SRS SIM922.
"""

import numpy as np
import time
import logging
from amodevices import SRSSIM922
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

device = {
    "Device": "SRS SIM922",
    "Address": "COM19",
    "Timeout": 1.,
    "SerialConnectionParams":
        {
            "baudrate": 9600,
            "bytesize": 8,
            "stopbits": 1,
            "parity": "N"
        }
    }

# Channel ID
channel_id = 2
# Calibration name to add
calibration_name = 'DT670'

try:    
    device_instance = SRSSIM922(device)
    device_instance.connect()
    
    # Get current calibration curve used for this channel: 0 = Standard, 1 = User
    print(device_instance.query(f'CURV? {channel_id}'))
    
    # Get current user calibration curve
    print(device_instance.query(f'CINI? {channel_id}'))
    
    # Add new user calibration curve
    device_instance.write(f'CINI {channel_id},0,{calibration_name}')
    
    # Get new user calibration curve
    print(device_instance.query(f'CINI? {channel_id}'))
    
    ## Add points to calibration curve
    # Load calibration curve from file
    # Calibration curve for Lake Shore DT-600 sensors
    data_ = np.loadtxt('dt600.txt', skiprows=3)
    # Sort data by increasing sensor voltage
    data = data_[np.argsort(data_[:, 1]), :]
    for i in range(len(data)):
        # Add calibration point
        device_instance.write(f'CAPT {channel_id},{data[i,1]},{data[i,0]}')
        time.sleep(0.5)
    
    print(device_instance.query(f'CINI? {channel_id}'))
    
    # Set calibration curve of channel to user calibration curve
    device_instance.write(f'CURV {channel_id},USER')
    print(device_instance.query(f'CURV? {channel_id}'))
finally:
    device_instance.close()
