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
        # Channel names in the order `getOutput?` lists their values, read
        # once per connection (`read_outputs`)
        self._output_names = None

    def connect(self):
        """Open serial connection to device."""
        self._output_names = None
        self.serial_connect()

    def close(self):
        """Close serial connection to device."""
        self.serial_close()

    def write(self, command):
        """Write command `command` (str) to device."""
        self.serial_write(command, encoding='ASCII', eol='\n')

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        # A reply that arrived after an earlier query timed out would
        # otherwise be read as this query's reply, and every later reply
        # would belong to the query before it
        if self.ser is not None:
            self.ser.reset_input_buffer()
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

    def read_output_names(self):
        """Read the names of all channels (list of str), in the order in
        which `getOutput?` lists their values."""
        rsp = self.query('getOutputNames?')
        return [name.strip() for name in rsp.split(',')]

    def read_outputs(self):
        """
        Read the latest conversion of every channel with a single
        `getOutput?` query and return a dict of channel name (str, as the
        CTC100 reports it) to value (float; NaN for a sensor that is
        disconnected or out of range).

        The channel names are read once per connection
        (`getOutputNames?`) and again after a reply whose number of values
        does not match them.
        """
        if self._output_names is None:
            self._output_names = self.read_output_names()
        names = self._output_names
        values = self.query('getOutput?').split(',')
        if len(values) != len(names):
            self._output_names = None
            raise DeviceError(
                f'{self.device["Device"]}: getOutput? returned {len(values)}'
                f' values for {len(names)} channels')
        try:
            return {name: float(value) for name, value in zip(names, values)}
        except ValueError:
            raise DeviceError(
                f'{self.device["Device"]}: Non-numeric value in getOutput?'
                f' reply \'{",".join(values)}\'')
