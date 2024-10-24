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

        # Set clip level to 13.5 %
        self.clip_level = 0.135

        self.device_info = device_info

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

    @staticmethod
    def convert_scan_data_struct_to_dict(scan_data_struct):
        scan_data_dict = {
            field: (
                getattr(scan_data_struct, field)
                if len(np.ctypeslib.as_array(getattr(scan_data_struct, field)).shape) == 0
                else np.ctypeslib.as_array(getattr(scan_data_struct, field))
                )
            for field, _ in scan_data_struct._fields_
            }
        return scan_data_dict

    def convert_px_to_um(self, value, axis=None):
        """
        Convert value `value` from pixel units to μm for axis `axis`
        (either 'h' or 'v' or their aliases 'x' or 'y', respectively).
        """
        if axis in ['v', 'y']:
            pixel_pitch = self.device_info['PixelPitch'][1]
        else:
            pixel_pitch = self.device_info['PixelPitch'][0]
        return value*pixel_pitch

    def clear_frame_queue(self):
        """Clear frame queue to make sure we acquire a new frame."""
        self.bc2.clearFrameQueue()

    def read_frame(self):
        """
        Read frame from camera, analyse it, and return both analysis results
        `scan_data` (dict) and image `image_data` (array).
        """
        scan_data_struct = TLBC2.TLBC1_Calculations()
        err = self.bc2.get_scan_data(byref(scan_data_struct))
        scan_data = self.convert_scan_data_struct_to_dict(scan_data_struct)
        if err != 0:
            self.error_exit(err)
        self.scan_data = scan_data
        if(scan_data['isValid']):
            # Read image
            pixel_data = (((c_ubyte*scan_data['imageWidth'])*scan_data['imageHeight'])*2)()
            width, height = c_ushort(0), c_ushort(0)
            bytes_per_pixel = c_uint8(2)
            err = self.bc2.get_image(
                pixel_data, byref(width), byref(height), byref(bytes_per_pixel))
            image_data_flat = np.frombuffer(pixel_data, dtype='uint16')
            image_data = image_data_flat.reshape(scan_data['imageHeight'], scan_data['imageWidth'])
            return scan_data, image_data
        else:
            return scan_data, None

    def convert_scan_data(self, scan_data):
        """
        Convert some values from scan data `scan_data` (dict) returned by Thorlabs TLBC2 library
        from sensor units (pixels) to absolute sensor dimensions (um), as measured from the sensor
        center, along with reporting some other values such as sensor saturation.
        Note that the zero position is the center of the sensor, with positive x and y values
        towards the right and top, respectively, as seen by the laser beam.
        """
        left, top, width, height = self.roi
        binning = self.binning
        convert_px_to_um_x = lambda value: self.convert_px_to_um(binning*value, axis='x')
        convert_px_to_um_y = lambda value: self.convert_px_to_um(binning*value, axis='y')
        px_to_um_mean = np.mean([
            self.convert_px_to_um(binning, axis='x'),
            self.convert_px_to_um(binning, axis='y')
            ])
        px_to_um = lambda value: value*px_to_um_mean
        convert_px_pos_x = lambda pos_px_x: (
            convert_px_to_um_x(pos_px_x+left-self.device_info['Pixels'][0]/2/binning))
        convert_px_pos_y = lambda pos_px_y: (
            -convert_px_to_um_y(pos_px_y+top-self.device_info['Pixels'][1]/2/binning))
        auto_calculation_area, calculation_area_shape = self.calculation_area_mode
        if calculation_area_shape == 0:
            calculation_area_shape_name = 'Rectangle'
        elif calculation_area_shape == 1:
            calculation_area_shape_name = 'Ellipse'
        elif calculation_area_shape == 2:
            calculation_area_shape_name = 'IsoAuto'
        # All positions and widths are in units of um
        beam_profile_data = {
            # Camera parameters
            # Exposure time (ms)
            'ExposureTime': self.exposure_time,
            # Auto exposure state
            'AutoExposure': self.auto_exposure,
            # Gain (dB)
            'Gain': self.gain,
            # Pixel binning (NxN pixels are binned, where N = `binning`)
            'Binning': binning,
            # Various intensity counts (or analog-to-digital units (ADU)) of the sensor.
            # Note that the raw image data, as it uses unsigned integer for the intensity counts,
            # has minimum and maximum intensity counts values of 0 and (2^N)-2, respectively,
            # where N is the bit-depth of the digitizer.
            # Base level intensity counts
            # This seems to be a camera-specific, hardcoded value if the ambient light correction
            # is disabled.
            # Note that the raw image data, as it uses unsigned integer for the intensity counts,
            # does not have this base level removed.
            'Intensity_BaseLevel_ADU': scan_data['baseLevel'],
            # Minimum intensity counts supported by camera, after having subtracted base level
            # intensity counts, i.e., 0-'Intensity_BaseLevel_ADU'
            'Intensity_Range_Min_ADU': scan_data['minIntensity'],
            # Maximum intensity counts support by camera, after having subtracted base level
            # intensity counts
            'Intensity_Range_Max_ADU': scan_data['maxIntensity'],
            # Peak intensity counts of current image, before having subtracted base level
            # intensity counts (maximum is (2^N)-2)
            'Intensity_Peak_ADU': scan_data['peakIntensity'],
            # Saturation, i.e., ratio of peak intensity counts to maximum intensity counts possible
            'Saturation_Rel': scan_data['saturation'],
            # Clip level used for beam clip width and ellipse calculation (default is 0.135 ~ 1/e^2)
            'ClipLevel': self.clip_level,
            # Region of interest (ROI)
            'ROI_Left': convert_px_to_um_x(left-self.device_info['Pixels'][0]/2/binning),
            'ROI_Top': -convert_px_to_um_y(top-self.device_info['Pixels'][1]/2/binning),
            'ROI_Width': convert_px_to_um_x(width),
            'ROI_Height': convert_px_to_um_y(height),
            # Calculation area used to determine beam parameters (need not be identical to ROI)
            'CalcArea_Auto': auto_calculation_area,
            'CalcArea_Shape': calculation_area_shape_name,
            'CalcArea_Center_X': convert_px_pos_x(scan_data['calcAreaCenterX']),
            'CalcArea_Center_Y': convert_px_pos_y(scan_data['calcAreaCenterY']),
            'CalcArea_Width': convert_px_to_um_x(scan_data['calcAreaWidth']),
            'CalcArea_Height': convert_px_to_um_y(scan_data['calcAreaHeight']),
            'CalcArea_Angle': scan_data['calcAreaAngle'],
            # X and Y position of centroid, as measured from center of image
            'Centroid_Position_X': convert_px_pos_x(scan_data['centroidPositionX']),
            'Centroid_Position_Y': convert_px_pos_y(scan_data['centroidPositionY']),
            # Ellipse fit
            'Ellipse_Position_X': convert_px_pos_x(scan_data['ellipseCenterX']),
            'Ellipse_Position_Y': convert_px_pos_y(scan_data['ellipseCenterY']),
            # Ellipse diameters for the set clip level
            'Ellipse_Diameter_Min': px_to_um(scan_data['ellipseDiaMin']),
            'Ellipse_Diameter_Max': px_to_um(scan_data['ellipseDiaMax']),
            'Ellipse_Diameter_Mean': px_to_um(scan_data['ellipseDiaMean']),
            # Ellipse radii for the set clip level
            'Ellipse_Radius_Min': px_to_um(scan_data['ellipseDiaMin'])/2,
            'Ellipse_Radius_Max': px_to_um(scan_data['ellipseDiaMax'])/2,
            'Ellipse_Radius_Mean': px_to_um(scan_data['ellipseDiaMean'])/2,
            'Ellipse_Ellipticity': scan_data['ellipseEllipticity'],
            'Ellipse_Orientation': scan_data['ellipseOrientation'],
            # # 1D Gaussian fit for cuts along x- and y-axis through position defined with
            # # `bc2.set_profile_cut_position`.
            # # Center positions
            # 'GaussFit_Position_X': convert_px_pos_x(scan_data['gaussianFitCentroidPositionX']),
            # 'GaussFit_Position_Y': convert_px_pos_y(scan_data['gaussianFitCentroidPositionY']),
            # # Beam diameters are measured at the 1/e^2 level
            # 'GaussFit_Diameter_X': convert_px_to_um_x(scan_data['gaussianFitDiameterX']),
            # 'GaussFit_Diameter_Y': convert_px_to_um_y(scan_data['gaussianFitDiameterY']),
            # # Beam radii are measured at the 1/e^2 level
            # 'GaussFit_Radius_X': convert_px_to_um_x(scan_data['gaussianFitDiameterX'])/2,
            # 'GaussFit_Radius_Y': convert_px_to_um_y(scan_data['gaussianFitDiameterY'])/2,
            }
        return beam_profile_data

    @property
    def exposure_time(self) -> float:
        """Get exposure time (float, units of ms)."""
        exposure_time_c = c_double(0)
        err = self.bc2.get_exposure_time(byref(exposure_time_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return exposure_time_c.value

    @exposure_time.setter
    def exposure_time(self, value: float) -> None:
        """Set exposure time to value `value` (float, units of ms)."""
        exposure_time_c = c_double(value)
        err = self.bc2.set_exposure_time(exposure_time_c)
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def auto_exposure(self) -> bool:
        """Get auto exposure state (bool)."""
        auto_exposure_c = c_bool(False)
        err = self.bc2.get_auto_exposure(byref(auto_exposure_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return auto_exposure_c.value

    @auto_exposure.setter
    def auto_exposure(self, value: bool) -> None:
        """Set auto exposure state (bool)."""
        err = self.bc2.set_auto_exposure(TLBC2.VI_ON if value else TLBC2.VI_OFF)
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def gain(self) -> float:
        """Get gain (float, units of dB)."""
        gain_c = c_double(0)
        err = self.bc2.get_gain(byref(gain_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return gain_c.value

    @gain.setter
    def gain(self, value: float) -> None:
        """Set gain to value `value` (float, units of dB)."""
        err = self.bc2.set_gain(c_double(value))
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def binning(self) -> int:
        """
        Get pixel binning (int):
        1: full sensor resolution
        2: 2x2 binning
        4: 4x4 binning
        8: 8x8 binning
        16: 16x16 binning
        """
        binning_c = c_uint8()
        err = self.bc2.get_binning(byref(binning_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return binning_c.value

    @binning.setter
    def binning(self, value: int) -> None:
        """
        Set pixel binning to `value` (int):
        1: full sensor resolution
        2: 2x2 binning
        4: 4x4 binning
        8: 8x8 binning
        16: 16x16 binning
        """
        if value not in [
                TLBC2.TLBC2_No_Binning, TLBC2.TLBC2_Binning_2, TLBC2.TLBC2_Binning_4,
                TLBC2.TLBC2_Binning_8, TLBC2.TLBC2_Binning_16]:
            logger.error(f'Invalid pixel binning of {value} requested (must be 1, 2, 4, 8, or 16)')
        else:
            err = self.bc2.set_binning(c_uint8(value))
            if err != 0:
                self.error_exit(self.bc2, err)

    @property
    def roi(self) -> tuple:
        """
        Get the rectangle defining the region of interest (ROI) `(left, top, width, height)`
        (tuple of four int, units of pixels).
        """
        left_c = c_uint16()
        top_c = c_uint16()
        width_c = c_uint16()
        height_c = c_uint16()
        err = self.bc2.get_roi(byref(left_c), byref(top_c), byref(width_c), byref(height_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return (left_c.value, top_c.value, width_c.value, height_c.value)

    @roi.setter
    def roi(self, rectangle: tuple) -> None:
        """
        Set the rectangle defining the region of interest (ROI) (units of pixels).
        """
        left, top, width, height = rectangle
        err = self.bc2.set_roi(c_uint16(left), c_uint16(top), c_uint16(width), c_uint16(height))
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def calculation_area_mode(self) -> tuple:
        """
        Get the method for determining the calculation area. Returns the tuple `(automatic, form)`.
        `automatic` (bool) is True if the calculation area is determined automatically.
        Otherwise, the user-defined calculation area is used
        (get/set with property `user_calculation_area`).
        `form` (int) gives the shape of the calculation area:
        0: rectangle, 1: ellipse, 2: "IsoAuto".
        """
        automatic_c = c_int16()
        form_c = c_uint8()
        err = self.bc2.get_calculation_area_mode(byref(automatic_c), byref(form_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return (automatic_c.value == TLBC2.VI_ON, form_c.value)

    @calculation_area_mode.setter
    def calculation_area_mode(self, calculation_area: tuple) -> None:
        """
        Set the method for determining the calculation area, using the tuple `(automatic, form)`.
        `automatic` (bool) is True if the calculation area is determined automatically.
        Otherwise, the user-defined calculation area is used
        (get/set with property `user_calculation_area`).
        `form` (int) gives the shape of the calculation area:
        0: rectangle, 1: ellipse, 2: "IsoAuto".

        BUG:
        Using "TLBC2.py" from July 12, 2024 and Thorlabs Beam version 9.1.5787.615,
        the calculation area mode cannot be set to user. An access violation is reported from the
        DLL instead.
        """
        automatic, form = calculation_area
        err = self.bc2.set_calculation_area_mode(
            TLBC2.VI_ON if automatic else TLBC2.VI_OFF, c_uint8(form))
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def user_calculation_area(self) -> tuple:
        """
        Get the user calculation area in pixels,
        returning the tuple `(center_x_pos, center_y_pos, width, height, angle)`.
        """
        center_x_pos_c = c_double()
        center_y_pos_c = c_double()
        width_c = c_double()
        height_c = c_double()
        angle_c = c_double()
        err = self.bc2.get_user_calculation_area(
            byref(center_x_pos_c), byref(center_y_pos_c), byref(width_c), byref(height_c),
            byref(angle_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return (
            center_x_pos_c.value, center_y_pos_c.value, width_c.value, height_c.value,
            angle_c.value)

    @user_calculation_area.setter
    def user_calculation_area(self, user_calculation_area: tuple) -> None:
        """
        Get the user calculation area in pixels,
        setting the tuple `(center_x_pos, center_y_pos, width, height, angle)`.
        """
        center_x_pos, center_y_pos, width, height, angle = user_calculation_area
        err = self.bc2.set_user_calculation_area(
            c_double(center_x_pos), c_double(center_y_pos), c_double(width), c_double(height),
            c_double(angle))
        if err != 0:
            self.error_exit(self.bc2, err)

    @property
    def clip_level(self) -> float:
        """Get clip level (float)."""
        clip_level_c = c_double()
        err = self.bc2.get_clip_level(byref(clip_level_c))
        if err != 0:
            self.error_exit(self.bc2, err)
        return clip_level_c.value

    @clip_level.setter
    def clip_level(self, value: float) -> None:
        """Set clip level to value `value` (float)."""
        err = self.bc2.set_clip_level(c_double(value))
        if err != 0:
            self.error_exit(self.bc2, err)
