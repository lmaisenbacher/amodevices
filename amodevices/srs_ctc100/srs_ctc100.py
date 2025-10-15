# -*- coding: utf-8 -*-
"""
Device driver for Stanford Research Instruments CTC100 cryogenic temperature controller
(using its USB interface, which implements a virtual serial port).
"""

import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class SRSCTC100(dev_generic.Device):
    """
    Device driver for Stanford Research Instruments CTC100 cryogenic temperature controller
    (using its USB interface, which implements a virtual serial port).
    """

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device with settings `device` (dict)."""
        super().__init__(device)

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
        if not rsp.endswith("\r\n"):
            raise DeviceError(
                f'{self.device["Device"]}: Response does not end with \'\r\n\' as expected')
        return rsp.rstrip()

    def read_temperature(self, name):
        """Read temperature of channel with name `name` (str)."""
        rsp = self.query(f"{name}?")
        return float(rsp)

    def read_pid_setpoint(self, name):
        """Read PID temperature setpoint of channel with name `name` (str)."""
        rsp = self.query(f"{name}.PID.Setpoint?")
        return float(rsp)

    def read_heater_power(self, name):
        """Read heater power of channel with name `name` (str)."""
        rsp = self.query(f"{name}?")
        return float(rsp)

    def query_custom_command(self, command):
        """Send custom command `command` (str) and read response."""
        rsp = self.query(f"{command}")
        return float(rsp)
