# -*- coding: utf-8 -*-
"""
Device driver for the Kurt J. Lesker KJLC Carbon XCG Series pressure gauge.
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

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class KJLCXCG(dev_generic.Device):
    """
    Device driver for Kurt J. Lesker KJLC Carbon XCG series pressure gauges, read out through a
    custom Arduino controller (through USB serial interface).
    """

    def __init__(self, device):
        """Initialize class for device with settings `device` (dict)."""
        super().__init__(device)

    def connect(self):
        """Open serial connection to device."""
        self.serial_connect()

    def close(self):
        """Close serial connection to device."""
        self.serial_close()

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        internal_address = self.device["DeviceSpecificParams"]["InternalAddress"]
        self.serial_write(f'#{internal_address}{command}', encoding='ASCII', eol='\r')
        rsp = self.ser.readline()
        try:
            rsp = rsp.decode(encoding="ASCII")
        except UnicodeDecodeError:
            raise DeviceError(
                f'{self.device["Device"]}: Error in decoding response (\'{rsp}\') received')
        if rsp == '':
            raise DeviceError(f'{self.device["Device"]}: No response received')
        if rsp.startswith("?"):
            raise DeviceError(
                f'{self.device["Device"]}: Received an error response: \'{rsp}\'')
        if not rsp.startswith(f"*{internal_address} "):
            raise DeviceError(
                f'{self.device["Device"]}: Didn\'t receive correct acknowledgement'
                f' (response received: \'{rsp}\')')
        return rsp[4:]

    def read_pressure(self):
        """Read pressure."""
        rsp = self.query("RD")
        return float(rsp)
