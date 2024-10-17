# -*- coding: utf-8 -*-
"""
Created on Tue Oct 10 15:33:26 2023

@author: Isaac Pope/UC Berkeley

Device driver for FLIR Boson thermal camera.
"""

import numpy as np
import logging
import cv2

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# Import FLIR Boson SDK from subdirectory "SDK_USER_PERMISSIONS".
# The SDK itself is not part of the repo and must be copied here manually.
# Tested with Boson 3.0 IDD & SDK. The subdirectory "SDK_USER_PERMISSIONS" must be the one
# containing "__init__.py".
# Available at https://www.dropbox.com/scl/fi/t81amkxiaxo11uriez0bm/SDK_USER_PERMISSIONS.zip for
# Unitrap members.
try:
    from . import SDK_USER_PERMISSIONS as fbsdk
except ImportError as e:
    logger.error(
        'Failed to import FLIR Boson SDK, which must be copied to '
        +'\'amodevices/flir_boson/SKD_USER_PERMISSIONS\' manually; '
        +f'`FLIRBoson` device driver not functional: {e}')

class FLIRBoson(dev_generic.Device):
    """Device driver for FLIR Boson thermal camera."""

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        # Initialize the camera
        if 'fbsdk' not in globals():
            msg = (
                'FLIR Boson SDK not imported, check if it is present in '
                +'\'amodevices/flir_boson/SKD_USER_PERMISSIONS\''
                )
            logger.error(msg)
            raise DeviceError(msg)
        myCam = fbsdk.CamAPI.pyClient(manualport=device['Address'])
        self.myCam = myCam

        # Set camera gain mode
        myCam.bosonSetGainMode(fbsdk.FLR_BOSON_GAINMODE_E.FLR_BOSON_HIGH_GAIN)

        # Set output to TLinear
        myCam.TLinearSetControl(fbsdk.FLR_ENABLE_E.FLR_ENABLE)
        _ = myCam.sysctrlSetUsbVideoIR16Mode(
            fbsdk.FLR_SYSCTRL_USBIR16_MODE_E.FLR_SYSCTRL_USBIR16_MODE_TLINEAR)

        # Configure radiometry
        radiometry_config = device.get('Radiometry', {})
        myCam.radiometrySetEmissivityTarget(radiometry_config.get('EmissivityTarget', 100))
        myCam.radiometrySetTempWindow(radiometry_config.get('TempWindow', 295))
        myCam.radiometrySetTransmissionWindow(radiometry_config.get('TransmissionWindow', 100))
        myCam.radiometrySetReflectivityWindow(radiometry_config.get('ReflectivityWindow', 0))
        myCam.radiometrySetTempWindowReflection(radiometry_config.get('TempWindowReflection', 295))
        myCam.radiometrySetTransmissionAtmosphere(
            radiometry_config.get('TransmissionAtmosphere', 100))
        myCam.radiometrySetTempAtmosphere(radiometry_config.get('TempAtmosphere', 295))
        myCam.radiometrySetTempBackground(radiometry_config.get('TempBackground', 295))
        # Necessary after setting radiometry parameters like window transmission
        myCam.TLinearRefreshLUT(fbsdk.FLR_BOSON_GAINMODE_E.FLR_BOSON_HIGH_GAIN)
        myCam.bosonRunFFC()

        # Configure CV2 to read out USB video from camera
        cv2_config = device.get('CV2Config', {})
        device_index = cv2_config.get('DeviceIndex', 1)
        cap = cv2.VideoCapture(device_index + cv2.CAP_DSHOW)
        camera_resolution = cv2_config.get('Resolution', [320, 256])
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, camera_resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_resolution[1])
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc('Y', '1', '6', ' '))
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.cap = cap

    def close(self):
        """Close connection to device."""
        self.cap.release()
        cv2.destroyAllWindows()
        self.myCam.closeComm()

    def read_frame(self):
        """Read frame from camera and return status `stream_ret` and image array `frame`."""
        stream_ret, frame = self.cap.read()
        return stream_ret, frame

    def convert_frame_to_celsius(self, frame: np.ndarray) -> np.ndarray:
        """Convert frame data from kelvin to degree Celsius."""
        temp_map_c = (frame / 100.0) - 273.15
        return temp_map_c
