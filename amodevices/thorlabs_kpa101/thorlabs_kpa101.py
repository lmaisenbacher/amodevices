# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Device driver for Thorlabs KPA101 beam position aligner, using `pylablib` library.
"""

import numpy as np
import time

from pylablib.devices.Thorlabs.kinesis import KinesisQuadDetector
from pylablib.devices.Thorlabs import ThorlabsError

import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class ThorlabsKPA101(dev_generic.Device):
    """Device driver for Thorlabs KPA101 beam position aligner, using `pylablib` library."""

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device with serial number `serial_number` (int)."""
        super().__init__(device)

        # Serial number to open
        self.serial_number = device['SerialNumber']
        # Init device open status
        self.device_connected = False
        # Instance of `pylablib.devices.Thorlabs.kinesis` class
        self.KinesisQuadDetector = None
        # Cached readings
        self._xdiff = np.nan
        self._ydiff = np.nan
        self._sum = np.nan
        self._xpos = np.nan
        self._ypos = np.nan
        # Timestamp of last reading
        self._last_reading_time = None
        # Cache interval for readings (s)
        self._cache_interval = 0.1

    def check_connection(self):
        """Check whether connection to device is open."""
        if not self.device_connected:
            msg = (
                f'Thorlabs KPA101: Connection to device with serial number {self.serial_number:d}'
                +' not open')
            logger.error(msg)
            raise DeviceError(msg)

    def connect(self):
        """Open connection to device."""
        try:
            self.KinesisQuadDetector = KinesisQuadDetector(self.serial_number)
        except ThorlabsError:
            msg = (
                'Thorlabs KPA101: Could not connect to device with serial number '
                +f'{self.serial_number:d} (is it open in another instance?)')
            logger.error(msg)
            raise DeviceError(msg)
        logger.info(
            'SN %d: Connected to device', self.serial_number)
        self.device_connected = True
        self.get_readings_cached()

    def close(self):
        """Close connection to device."""
        if self.device_connected:
            self.KinesisQuadDetector.close()
            self.device_connected = False

    def get_readings_cached(self):
        """Get readings from device."""
        if self._last_reading_time is None \
                or (time.time()-self._last_reading_time > self._cache_interval):
            self.check_connection()
            self._xdiff, self._ydiff, self._sum, self._xpos, self._ypos = (
                self.KinesisQuadDetector.get_readings())
            self._last_reading_time = time.time()

    @property
    def xdiff(self):
        """Get x-axis alignment difference signal in volts."""
        self.get_readings_cached()
        return self._xdiff

    @property
    def ydiff(self):
        """Get y-axis alignment difference signal in volts."""
        self.get_readings_cached()
        return self._ydiff

    @property
    def sum(self):
        """Get summed signal in volts."""
        self.get_readings_cached()
        return self._sum

    @property
    def xout(self):
        """Get x position in millimeter. Only meaningful for some sensor types."""
        self.get_readings_cached()
        return self._xpos

    @property
    def yout(self):
        """Get y position in millimeter. Only meaningful for some sensor types."""
        self.get_readings_cached()
        return self._ypos

    @property
    def operation_mode(self):
        """
        Get current operation mode `operation_mode` (str):
        "monitor", "open_loop", "closed_loop", or "auto_loop".
        """
        operation_mode = self.KinesisQuadDetector.get_operation_mode()
        return operation_mode

    @operation_mode.setter
    def operation_mode(self, operation_mode):
        """
        Set current operation mode to `operation_mode` (str):
        "monitor", "open_loop", "closed_loop", or "auto_loop".
        """
        self.KinesisQuadDetector.set_operation_mode(operation_mode)
