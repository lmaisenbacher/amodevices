# -*- coding: utf-8 -*-
"""
Created on Tue Oct 10 15:33:26 2023

@author: Lothar Maisenbacher/UC Berkeley

Device driver for Thorlabs PM100D power meter, controlled through VISA.
Other Thorlabs power meters are supported in "PM100D" mode (see below), including:
- PM101(R)
- PM16-121

Both power sensors (photodiode and thermal) and pyroelectric energy sensors (e.g. ES111C) are
supported, with power measurements accessed through the `power` interface and pulse energy
measurements through the `energy` interface.

The Thorlabs power meters are controlled through NI VISA
(https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html), which must be installed on
the system. Additionally, the power meters must be configured to use NI VISA: use "Driver Switcher",
installed with the "Thorlabs Optical Power Monitor"
(https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=OPM) software, to switch the
power meters from "TLPM (libusb)" driver/mode to "PM100D" mode, which allows control through NI
VISA.
"""

import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class ThorlabsPM100(dev_generic.Device):
    """Device driver for Thorlabs PM100 power meter, controlled through VISA."""

    class _sensor():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

            self._idn = None
            self._name = None
            self._sn = None
            self._cal_msg = None
            self._type = None
            self._subtype = None
            self._flags = None

        @property
        def idn(self):
            """Get sensor identification string (str) and parse its fields."""
            self._idn = self.outer_instance.visa_query('SYSTem:SENSor:IDN?')
            self._name, self._sn, self._cal_msg, _type, _subtype, _flags = (
                self._idn.split(','))
            self._type = int(_type)
            self._subtype = int(_subtype)
            self._flags = int(_flags)
            return self._idn

        @property
        def name(self):
            """Get sensor name (str)."""
            _ = self.idn
            return self._name

        @property
        def serial_number(self):
            """Get sensor serial number (str)."""
            _ = self.idn
            return self._sn

        @property
        def cal_msg(self):
            """Get sensor calibration message (str)."""
            _ = self.idn
            return self._cal_msg

        @property
        def type(self):
            """Get sensor type (int)."""
            _ = self.idn
            return self._type

        @property
        def subtype(self):
            """Get sensor subtype (int)."""
            _ = self.idn
            return self._subtype

        @property
        def flags(self):
            """Get sensor flags (int)."""
            _ = self.idn
            return self._flags

        @property
        def power_sensor(self):
            """Is power sensor? (bool)."""
            _ = self.idn
            return bool((self._flags >> 0) % 2)

        @property
        def energy_sensor(self):
            """Is energy sensor? (bool)."""
            _ = self.idn
            return bool((self._flags >> 1) % 2)

        @property
        def wavelength_settable(self):
            """Is wavelength settable? (bool)."""
            _ = self.idn
            return bool((self._flags >> 5) % 2)

        @property
        def temperature_sensor(self):
            """Has temperature sensor? (bool)."""
            _ = self.idn
            return bool((self._flags >> 8) % 2)

    class _power():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def unit(self):
            """Get power unit (str), either 'W' for Watt (W) or 'DBM' for dBm."""
            return self.outer_instance.visa_query('SENSe:POWer:UNIT?')

        @unit.setter
        def unit(self, unit):
            """Set power unit (str), either 'W' for watt (W) or 'DBM' for dBm."""
            if unit not in ['W', 'DBM']:
                raise DeviceError(
                    f'{self.outer_instance.device["Device"]}: '
                    +'Power unit must be \'W\' for watt (W) or \'DBM\' for dBm')
            return self.outer_instance.visa_write(f'SENSe:POWer:UNIT {unit}')

        @property
        def auto_range(self):
            """Get state of auto-ranging function (bool)."""
            return bool(int(self.outer_instance.visa_query('SENSe:POWer:RANGe:AUTO?')))

        @auto_range.setter
        def auto_range(self, state):
            """Set state of auto-ranging function (bool)."""
            return self.outer_instance.visa_write(f'SENSe:POWer:RANGe:AUTO {int(state)}')

        @property
        def value(self):
            """Get current power reading (float) in units of `self.unit`."""
            return float(self.outer_instance.visa_query('MEASure:POWer?'))

    class _energy():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def range(self):
            """Get energy range (float) in units of joule (J)."""
            return float(self.outer_instance.visa_query('SENSe:ENERgy:RANGe?'))

        @range.setter
        def range(self, range_):
            """
            Set energy range to `range_` (float) in units of joule (J).
            Only manual ranging is available for energy sensors; the device will round the value
            to the next suitable range.
            """
            return self.outer_instance.visa_write(f'SENSe:ENERgy:RANGe {range_}')

        @property
        def trigger_level(self):
            """Get trigger level (float) in percent (%) of the selected energy range."""
            return float(self.outer_instance.visa_query('SENSe:PEAKdetector:THReshold?'))

        @trigger_level.setter
        def trigger_level(self, trigger_level):
            """Set trigger level to `trigger_level` (float) in percent (%) of the selected
            energy range. Must be between 1% and 70%."""
            if not 1 <= trigger_level <= 70:
                raise DeviceError(
                    f'{self.outer_instance.device["Device"]}: '
                    +'Trigger level must be between 1% and 70% of the selected energy range')
            return self.outer_instance.visa_write(f'SENSe:PEAKdetector:THReshold {trigger_level}')

        @property
        def value(self):
            """
            Get energy reading (float) in units of joule (J).
            This starts a new measurement, which completes with the next pulse exceeding the
            trigger level. The VISA timeout ('Timeout' key of device dict) must be longer than
            the time between pulses, otherwise a timeout error occurs.
            """
            return float(self.outer_instance.visa_query('MEASure:ENERgy?'))

        @property
        def last_value(self):
            """Get last completed energy reading (float) in units of joule (J), without waiting
            for a new pulse."""
            return float(self.outer_instance.visa_query('FETCh?'))

        def arm(self):
            """
            Configure device for energy measurement and start a continuously running
            measurement. The device then updates the measurement value with each incoming pulse
            exceeding the trigger level, setting the new-value flag of the operation status
            register (see `new_value_available`). Read the value with `last_value`.
            Unlike `value`, this never blocks, allowing a non-blocking readout loop that polls
            `new_value_available` and fetches `last_value` when it returns True.
            """
            outer_instance = self.outer_instance
            outer_instance.visa_write('CONFigure:ENERgy')
            outer_instance.visa_write('ABORt')
            # Reading the operation status event register clears it
            outer_instance.visa_query('STATus:OPERation?')
            outer_instance.visa_write('INITiate')

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        self.init_visa()
        self.sensor = self._sensor(self)
        self.power = self._power(self)
        self.energy = self._energy(self)

    def close(self):
        """Close connection to device."""
        self.visa_resource.close()

    @property
    def wavelength(self):
        """Get operation wavelength (float) in units of nm."""
        return float(self.visa_query('SENSe:CORRection:WAVElength?'))

    @wavelength.setter
    def wavelength(self, wavelength):
        """Set operation wavelength to `wavelength` (float) in units of nm."""
        return self.visa_write(f'SENSe:CORRection:WAVElength {wavelength}')

    @property
    def beam_diameter(self):
        """Get beam diameter (float) in units of mm."""
        return float(self.visa_query('SENSe:CORRection:BEAMdiameter?'))

    @beam_diameter.setter
    def beam_diameter(self, diameter):
        """Set beam diameter to `diameter` (float) in units of mm."""
        return self.visa_write(f'SENSe:CORRection:BEAMdiameter {diameter}')

    @property
    def new_value_available(self):
        """
        Check whether a new measurement value is available to read with `FETCh?` (bool).
        Reads the operation status event register and tests the new-value flag (bit 512).
        Reading the register clears it, so this returns True only once per new value.
        """
        return bool(int(self.visa_query('STATus:OPERation?')) & 512)

    @property
    def frequency(self):
        """
        Get frequency reading (float) in units of hertz (Hz).
        For energy sensors this is the pulse repetition rate, for power sensors the frequency of
        a pulsed, modulated, or chopped light source.
        """
        return float(self.visa_query('MEASure:FREQuency?'))

    @property
    def num_averages(self):
        """Get number of averages (int)."""
        return int(self.visa_query('SENSe:AVERage:COUNt?'))

    @num_averages.setter
    def num_averages(self, num_averages):
        """Set number of averages to `num_averages` (int)."""
        return self.visa_write(f'SENSe:AVERage:COUNt {num_averages:d}')

    def zero(self):
        """Perform zero adjustment routine."""
        self.visa_write('SENSe:CORRection:COLLect:ZERO')

    @property
    def zero_magnitude(self):
        """Get applied voltage offset from zero adjustment (float) in units of volt."""
        return float(self.visa_query('SENSe:CORRection:COLLect:ZERO:MAGNitude?'))
