# -*- coding: utf-8 -*-
"""
Created on Tue Oct 10 15:33:26 2023

@author: Lothar Maisenbacher/Berkeley

Device driver for FLIR Boson thermal camera.
"""

import numpy as np
import logging
import sys
import os
import cv2
from pathlib import Path

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# Construct the path to the directory containing SDK_USER_PERMISSIONS
module_path = Path(Path(os.path.realpath(__file__)).parent).absolute()

# Add the path to sys.path
sys.path.append(str(module_path))

# Now you can import the module
from flir_boson_sdk import *

class FLIRBOSON(dev_generic.Device):
    """Device driver for FLIR Boson thermal camera."""

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        # Initialize the camera
        myCam = CamAPI.pyClient(manualport=device['Address'])
        self.myCam = myCam

        # Set camera gain mode
        myCam.bosonSetGainMode(FLR_BOSON_GAINMODE_E.FLR_BOSON_HIGH_GAIN)
        myCam.TLinearSetControl(FLR_ENABLE_E.FLR_ENABLE)
        status = myCam.sysctrlSetUsbVideoIR16Mode(
            FLR_SYSCTRL_USBIR16_MODE_E.FLR_SYSCTRL_USBIR16_MODE_TLINEAR)
        print(status)
        print(myCam.sysctrlGetUsbVideoIR16Mode())
        radiometry_config = device.get('Radiometry', {})
        myCam.radiometrySetTempWindow(radiometry_config.get('TempWindow', 295))
        myCam.radiometrySetTransmissionWindow(radiometry_config.get('TransmissionWindow', 100))
        myCam.radiometrySetReflectivityWindow(0)
        myCam.radiometrySetTempWindowReflection(295)
        myCam.radiometrySetTransmissionAtmosphere(100)
        myCam.radiometrySetTempAtmosphere(295)
        myCam.radiometrySetTempBackground(295)
        # necessary after setting radiometry parameters like window transmission
        myCam.TLinearRefreshLUT(FLR_BOSON_GAINMODE_E.FLR_BOSON_HIGH_GAIN)
        myCam.bosonRunFFC()

        device_index = 1
        cap = cv2.VideoCapture(device_index + cv2.CAP_DSHOW)
        self.cap = cap
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 256)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc('Y', '1', '6', ' '))
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

    def close(self):
        """Close connection to device."""
        self.cap.release()
        cv2.destroyAllWindows()
        self.myCam.closeComm()

    def read_frame(self):
        """Read frame from camera and return status `stream_ret` and image array `frame`."""
        stream_ret, frame = self.cap.read()
        return stream_ret, frame
