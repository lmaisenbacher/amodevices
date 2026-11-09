# -*- coding: utf-8 -*-
"""
Device driver for Stanford Research Instruments CTC100 cryogenic temperature controller
(using its USB interface, which implements a virtual serial port).
"""

import logging
import re
import time

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# Values of a channel's Figures setting (Channel.Figures)
FIGURES = ('0', '1', '2', '3', '4', '5', '6', 'Default')

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
        # When the latest query was sent (ns since the epoch, `time.time_ns`)
        self.query_sent_ns = None

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
        self.query_sent_ns = time.time_ns()
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

    @staticmethod
    def _setting(rsp):
        """The value in a query's reply `rsp`, without the parameter's
        name that the CTC100 puts before it when System.COM.Verbose is
        High ("In1.Figures = 6")."""
        return rsp.split('=', 1)[-1].strip()

    def read_figures(self, name):
        """Read the Figures setting of channel `name` (str): '0' to '6',
        the digits after the decimal point of its value in replies to
        remote queries, or 'Default' (System.Display.Figures applies)."""
        return self._setting(self.query(f'{name.replace(" ", "")}.Figures?'))

    def set_figures(self, name, figures):
        """
        Set the Figures setting of channel `name` (str) to `figures` (0 to
        6, or 'Default'): the digits after the decimal point of its value
        in replies to remote queries and on the front-panel display. The
        CTC100's own log, its PID feedback and its plots are not affected
        (manual, Channel.Figures). Read it back with `read_figures`.
        """
        if str(figures) not in FIGURES:
            raise DeviceError(
                f'{self.device["Device"]}: Figures must be 0 to 6 or'
                f' \'Default\', not {figures!r}')
        self.write(f'{name.replace(" ", "")}.Figures {figures}')

    def read_ad_period(self):
        """Read the A/D rate (System.Other.A/D rate), the time between two
        conversions of an input, in s."""
        rsp = self._setting(self.query('"System.Other.A/D rate?"'))
        match = re.fullmatch(r'([0-9]*\.?[0-9]+)\s*(ms|s)', rsp)
        if match is None:
            raise DeviceError(
                f'{self.device["Device"]}: A/D rate \'{rsp}\' not understood')
        value = float(match[1])
        return value * 1e-3 if match[2] == 'ms' else value

    def set_ad_rate(self, rate_ms):
        """
        Set the A/D rate (System.Other.A/D rate) to `rate_ms` ms: one of
        the values the CTC100 offers for its line frequency (60 Hz: 16.7,
        33.3, 50, 66.7, 83.3, 100, 150, ... 1000 ms; 50 Hz: 20, 40, ...
        1000 ms; manual, system.other.A/D rate). Sent as text with its
        unit (firmware 4.65 refused a bare number). Read it back with
        `read_ad_period`.
        """
        if (not isinstance(rate_ms, (int, float)) or isinstance(rate_ms, bool)
                or not rate_ms > 0):
            raise DeviceError(
                f'{self.device["Device"]}: A/D rate must be a positive number'
                f' of ms, not {rate_ms!r}')
        self.write(f'"System.Other.A/D rate" = "{rate_ms:g} ms"')

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
        does not match them. `query_sent_ns` then holds when the
        `getOutput?` was sent: the values are the conversions completed by
        the time the CTC100 received it.
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
