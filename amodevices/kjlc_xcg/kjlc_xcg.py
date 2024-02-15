# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC Carbon XCG Series pressure gauge.
An Arduino micro interfaces with an ADS1115 analog to digital converter (ADC) and an SSD1306 mini OLED
display. The Arduino reads and measures voltages, converting to pressure every 0.5 s and updates the display.
When a serial query is sent to the device it measures the pressure for the requested gauge and sends it to the
computer in a response. The communication protocol is nearly identical to that used for the KJLC 354 and 352 ion
pressure gauges.

For more information on how the Arduino microcontroller operates check its github repository at:
https://github.com/jack-mango/XCG-pressure-gauges

A USB type C cable can be used to connect to the front panel of the controller box to directly
interface with the Arduino over serial.
"""
import logging
import serial
from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger()


class Device(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)
        try:
            self.connection = serial.Serial(
                device["Address"], timeout=device["Timeout"],
                **device.get('SerialConnectionParams', {}))
        except serial.SerialException:
            raise DeviceError(
                f"Serial connection on port {device['Address']} couldn't be opened")

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        internal_address = self.device["DeviceSpecificParams"]["InternalAddress"]
        query = f'#{internal_address}{command}\r'.encode(encoding="ASCII")
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
        if rsp.startswith("?"):
            raise DeviceError(
                f"Received an error response: '{rsp}'")
        if not rsp.startswith(f"*{internal_address} "):
            raise DeviceError(
                f"Didn't receive correct acknowledgement (response received: '{rsp}')")
        return rsp[4:]

    def read_pressure(self):
        """Read pressure."""
        rsp = self.query("RD")
        return float(rsp)

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Pressure']:
                value = self.read_pressure()
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
