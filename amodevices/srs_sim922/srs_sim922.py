# -*- coding: utf-8 -*-
"""
@author: Chris Zavik and Lothar Maisenbacher/Berkeley

Device driver for Stanford Research Instruments SIM922 diode temperature monitor.
"""

import logging
import serial

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class SRSSIM922(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(SRSSIM922, self).__init__(device)

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
        """Query device with command `command` (str) and return response."""
        self.write(command)
        rsp = self.ser.readline()
        try:
            rsp = rsp.decode(encoding="ASCII")
        except UnicodeDecodeError:
            raise DeviceError(
                f'{self.device["Device"]}: Error in decoding response (\'{rsp}\') received')
        if rsp == '':
            raise DeviceError(f'{self.device["Device"]}: No response received')
        return rsp

    def read_temperature(self, channel):
        """Read temperature of channel with number `channel` (int, 1-3)."""
        command = f'TVAL? {channel}'
        try:
            response = self.query(command)
        except serial.SerialException as e:
            raise DeviceError(
                f'{self.device["Device"]}: Serial exception encountered: {e}')
        try:
            temperature = float(response)
        except ValueError:
            raise DeviceError(
                f'{self.device["Device"]}: Could not convert response {response} to float')
        return temperature
