# -*- coding: utf-8 -*-
"""
Created on Wed Jul 26 16:35:19 2023

@author: Lothar Maisenbacher/Berkeley
"""

import logging
from amodevices import ThorlabsK10CR1
from amodevices.thorlabs_k10cr1 import thorlabs_k10cr1
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

device = {
    'Device': 'Thorlabs K10CR1',
    'SerialNumber': 55193694,
    }

device = ThorlabsK10CR1(device)
device.connect()
kinesis = device.kinesis
# device.move_relative_in_dev_units(device.def_device_units['Position']*10)
# device.move_absolute_in_dev_units(1000000)
# device.kinesis.ISC_MoveRelative(
#     device.serial_number_byte,
#     int(
#         thorlabs_k10cr1.def_device_units['K10CR1']['Position']*90))

# kinesis_hardware_info = thorlabs_k10cr1.TLI_HardwareInformation()
# device.kinesis.ISC_GetHardwareInfo(device.serial_number_byte, ctypes.byref(kinesis_hardware_info))
print(device.position_dev_units)
print(device.position)

# status_bits = device.get_device_status()
# print(status_bits)

# # import time
# # time.sleep(0.03)

# status_bits = device.get_device_status()
# print(status_bits)

# device.close()
# message_type = wintypes.WORD()
# message_id = wintypes.WORD()
# message_data = wintypes.DWORD()
# status = device.kinesis.ISC_WaitForMessage(
#     device.serial_number_byte, ctypes.byref(message_type),
#     ctypes.byref(message_id), ctypes.byref(message_data))

# real_unit = ctypes.c_double()
# device.kinesis.ISC_GetRealValueFromDeviceUnit(
#     device.serial_number_byte, position, ctypes.byref(real_unit), 0)
# print(real_unit)

# Calculate conversion factor from device units to mm
steps_per_rev = ctypes.c_double()
gear_box_ratio = ctypes.c_double()
pitch = ctypes.c_double()
status = device.kinesis.ISC_GetMotorParamsExt(
    device.serial_number_byte, ctypes.byref(steps_per_rev),
    ctypes.byref(gear_box_ratio), ctypes.byref(pitch))

status_dict = device.get_device_status()
print(status_dict)

print(device.homed)

# device.close()
