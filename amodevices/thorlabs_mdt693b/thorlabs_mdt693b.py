# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Driver for Thorlabs MDT693B 3-axis piezo controller.
"""

import numpy as np
import time
import serial
import logging
import threading

# Thread lock to avoid writing/reading of serial ports from different threads
# at the same time
# All readers have to lock this
read_lock = threading.Lock()

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class ThorlabsMDT693B(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(ThorlabsMDT693B, self).__init__(device)
        self.ser = None
        # Timestamp of last reading
        self._last_reading_time = None
        # Cache interval for readings (s)
        self._cache_interval = 0.1
        # Cached readings
        self._voltage = np.nan

    def connect(self):
        """Open serial connection to device."""
        self.serial_connect()

    def close(self):
        """Close serial connection to device."""
        self.serial_close()

    def write(self, command):
        """Write command `command` (str) to device."""
        self.serial_write(command, encoding='ASCII', eol='\n')

    def query(self, command):
        """Query device with command `command` (str) and return response (str)."""
        self.write(command)
        with read_lock:
            response = self.ser.read_until(b'\r')
            ack = self.ser.read(1).decode()
        if ack != '>':
            raise DeviceError(f'{self.device["Device"]}: Device failed to acknowledge command')
        return response.rstrip()

    def send_command(self, command):
        """Send command `command` (str) to device and read acknowledgment."""
        self.write(command)
        with read_lock:
            ack = self.ser.read(1).decode()
        if ack != '>':
            raise DeviceError(f'{self.device["Device"]}: Device failed to acknowledge command')

    def _check_axis(self, axis):
        if axis not in ['x', 'y', 'z']:
            raise DeviceError(
                f'{self.device["Device"]}: Unknown axis \'{axis}\'')

    def read_voltage(self, axis):
        """Read voltage of axis `axis` (str, either 'x', 'y', or 'z')."""
        self._check_axis(axis)
        if np.isnan(self._voltage) or self._last_reading_time is None \
                or (time.time()-self._last_reading_time > self._cache_interval):
            self._voltage = np.nan
            command = f'{axis}voltage?'
            try:
                response = self.query(command)
            except serial.SerialException as e:
                raise DeviceError(
                    f'{self.device["Device"]}: Serial exception encountered: {e}')
            try:
                voltage = float(response[1:-1])
            except ValueError:
                raise DeviceError(
                    f'{self.device["Device"]}: Could not convert response {response} to float')
            self._voltage = voltage
        else:
            voltage = self._voltage
        return voltage

    def set_voltage(self, axis, voltage):
        """
        Set voltage of axis `axis` (str, either 'x', 'y', or 'z') to voltage `voltage` (float, units
        of V).
        """
        self._check_axis(axis)
        min_voltage = 0
        if voltage < min_voltage:
            raise DeviceError(
                f'{self.device["Device"]}: Voltage must not be below {min_voltage:.2f} V')
        command = f'{axis}voltage={voltage}'
        try:
            self.send_command(command)
        except serial.SerialException as e:
            raise DeviceError(f'Serial exception encountered: {e}')
