# -*- coding: utf-8 -*-
"""
Created on Thu Oct 17 10:48:14 2024

@author: Lothar Maisenbacher/UC Berkeley

Device driver for Thorlabs BC207 and BC210 beam profilers.

Thorlabs Beam >9.1 must be installed for the necessary DLLs to be present in the system.
"""

import numpy as np
import logging
import os
from ctypes import (
    c_uint32, c_uint16, c_uint8, byref, create_string_buffer, c_bool, c_int16,
    c_double, c_ubyte, c_ushort)

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# Import Thorlabs TLBC2 module from file "amodevices/thorlabs_bc/TLBC2.py".
# Available at https://github.com/Thorlabs/Light_Analysis_Examples/blob/main/Python/Thorlabs%20BC207%20Beam%20Profiler/TLBC2.py
try:
    from . import TLBC2
except ImportError as e:
    logger.error(
        f'Failed to import Thorlabs TLBC2 module: {e}')

class ThorlabsBC(dev_generic.Device):
    """Device driver for Thorlabs BC207 and BC210 beam profilers."""

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        # Add Thorlabs VISA DLL directory for 64-bit Python
        os.add_dll_directory(r'C:\Program Files\IVI Foundation\VISA\Win64\Bin')

        if 'TLBC2' not in globals():
            msg = (
                'Thorlabs TLBC2 module not imported, check if it is present in '
                +'\'amodevices/thorlabs_bc\''
                )
            logger.error(msg)
            raise DeviceError(msg)
        # Init camera instance
        bc2 = TLBC2.TLBC2()
        self.bc2 = bc2

        # Look for available devices
        num_devices_c = c_uint32()
        err = bc2.get_device_count(byref(num_devices_c))
        if err != 0:
            self.error_exit(bc2, err)
        num_devices = num_devices_c.value
        manufacturer_c = create_string_buffer(1024)
        resource_name_c = create_string_buffer(1024)
        model_name_c = create_string_buffer(1024)
        serial_number_c = create_string_buffer(1024)
        available_c = c_int16()
        devices_found = {}
        for k in range(0, num_devices):
            err = bc2.get_device_information(
                c_uint32(k), manufacturer_c, model_name_c, serial_number_c,
                byref(available_c), resource_name_c)
            serial_number = int(serial_number_c.value.decode())
            devices_found[serial_number] = {
                'DeviceIndex': k,
                'Manufacturer': manufacturer_c.value.decode(),
                'Model': model_name_c.value.decode(),
                'ResourceName': resource_name_c.value.decode(),
                'Available': bool(available_c.value),
                }

        serial_number = device['SerialNumber']
        if serial_number not in devices_found.keys():
            msg = (
                f'Cannot find device with serial number {serial_number:d} in system')
            logger.error(msg)
            raise DeviceError(msg)
        device_info = devices_found[serial_number]
        device_info_str = (
            f'{device_info["Model"]} from {device_info["Manufacturer"]}'
            +f' with serial number {serial_number:d}'
            )
        if not device_info['Available']:
            msg = (
                f'Found requested device ({device_info_str}) in system, '
                +'but it is not available; make sure it\'s not open elsewhere')
            logger.error(msg)
            raise DeviceError(msg)
        logger.info(f'Found requested device ({device_info_str}) in system')

        # Open device
        err = bc2.open(device_info['ResourceName'].encode('ASCII'), c_bool(True), c_bool(True))
        if err != 0:
            self.error_exit(bc2, err)

        driver_rev_c = create_string_buffer(1024)
        firmware_rev_c = create_string_buffer(1024)
        err = bc2.revision_query(driver_rev_c, firmware_rev_c)
        if err != 0:
            self.error_exit(bc2, err)

        device_info['DriverRevision'] = driver_rev_c.value.decode()
        device_info['FirmwareRevision'] = firmware_rev_c.value.decode()

        # Get sensor information
        pixels_h_c = c_uint16()
        pixels_v_c = c_uint16()
        pixel_pitch_h_c = c_double()
        pixel_pitch_v_c = c_double()
        err = bc2.get_sensor_information(
            byref(pixels_h_c), byref(pixels_v_c), byref(pixel_pitch_h_c), byref(pixel_pitch_v_c))
        if err != 0:
            self.error_exit(bc2, err)
        device_info['Pixels'] = [pixels_h_c.value, pixels_v_c.value]
        device_info['PixelPitch'] = [pixel_pitch_h_c.value, pixel_pitch_v_c.value]

        self.device_info = device_info
        print(device_info)

        # Init variables
        self.scan_data = None

    def error_exit(self, err):
        ebuf = create_string_buffer(1024)
        self.bc2.error_message(err, ebuf)
        self.bc2.close()
        msg = f'Error: {ebuf.value}'
        logger.error(msg)
        raise DeviceError(msg)

    def close(self):
        """Close connection to device."""
        self.bc2.close()

    def read_frame(self):
        """
        Read frame from camera, analyse it, and return both analysis results
        `scan_data` (`TLBC2.TLBC1_Calculations`) and image `image_data` (array).
        """
        scan_data = TLBC2.TLBC1_Calculations()
        err = self.bc2.get_scan_data(byref(scan_data))
        if err != 0:
            self.error_exit(err)
        self.scan_data = scan_data
        if(scan_data.isValid):
            # Read image
            pixel_data = (((c_ubyte*scan_data.imageWidth)*scan_data.imageHeight)*2)()
            width, height = c_ushort(0), c_ushort(0)
            bytes_per_pixel = c_uint8(2)
            err = self.bc2.get_image(
                pixel_data, byref(width), byref(height), byref(bytes_per_pixel))
            image_data_flat = np.frombuffer(pixel_data, dtype='uint16')
            image_data = image_data_flat.reshape(scan_data.imageHeight, scan_data.imageWidth)
            return scan_data, image_data
        else:
            return scan_data, None

    @property
    def exposure_time(self):
        """Get exposure time (float, units of ms)."""
        exposure_time_c = c_double(0)
        err = self.bc2.get_exposure_time(byref(exposure_time_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return exposure_time_c.value

    @exposure_time.setter
    def exposure_time(self, value):
        """Set exposure time to value `value` (float, units of ms)."""
        exposure_time_c = c_double(value)
        err = self.bc2.set_exposure_time(exposure_time_c)
        if err != 0:
            self.error_exit(self.bc2, err)
