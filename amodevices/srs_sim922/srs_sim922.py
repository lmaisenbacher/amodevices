# -*- coding: utf-8 -*-
"""
@author: Your name here!

Brief description of what this code does/is. Check other drivers
for 'inspiration'
"""

import logging
import serial

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)


class srsSim922(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(srsSim922, self).__init__(device)
        try:
            self.connection = serial.Serial(
                device["Address"], timeout=device["Timeout"],
                **device.get('SerialConnectionParams', {}))
        except serial.SerialException:
            raise DeviceError(
                f"Serial connection on port {device['Address']} couldn't be opened")

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        query = f'{command}\n'.encode(encoding="ASCII")
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise DeviceError("Failed to write to device")
        rsp = self.connection.readline()
        try:
            rsp = rsp.decode(encoding="ASCII")
        except UnicodeDecodeError:
            raise DeviceError(f"Error in decoding response ('{rsp}') received")
        if rsp == '':
            raise DeviceError(
                "No response received")
        return rsp

    def read_temperature(self, channel):
        """Read temperature of channel with number `channel` (str)"""
        command = 'TVAL? ' + channel
        temperature = self.query(command)
        return float(temperature)

    def get_values(self):
        """Read channels"""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Temperature'] and chan['tags']['SRSSIM922ChannelName'] in ['1', '2', '3', '4']:
                value = self.read_temperature(
                    chan['tags']['SRSSIM922ChannelName'])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
