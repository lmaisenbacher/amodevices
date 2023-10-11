# -*- coding: utf-8 -*-
"""
Created on Tue Oct 10 15:33:26 2023

@author: Lothar Maisenbacher/Berkeley

Device driver for Thorlabs PM100D power meter, controlled through VISA.
Other Thorlabs power meters are supported in "PM100D" mode (see below), including:
- PM101(R)
- PM16-121

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
            """Get x-axis alignment difference signal in volts."""
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

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device with serial number `serial_number` (int)."""
        super().__init__(device)

        self.init_visa()
        self.sensor = self._sensor(self)
        self.power = self._power(self)

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
        """Get applied voltage offset fron zero adjustment (float) in units of volt."""
        return float(self.visa_query('SENSe:CORRection:COLLect:ZERO:MAGNitude?'))
