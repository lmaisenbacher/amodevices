# -*- coding: utf-8 -*-
"""
Created on Tue May 8 01:48:26 2024

@author: Jack Mango/Berkeley

Device driver for Keysight 53220A series universal counter, controlled through VISA.
"""

import numpy as np
import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class Keysight53220A(dev_generic.Device):
    """Device driver for Keysight 53220A series universal counter."""

    class _input():

        def __init__(self, outer_instance, channel):
            self.channel = channel
            self.outer_instance = outer_instance

        @property
        def level(self):
            """ Get trigger level (Volts, float)."""
            return float(self.outer_instance.visa_query(f'INPut{self.channel}:LEVel?'))

        @level.setter
        def level(self, voltage):
            """ Set trigger level to `voltage` (Volts, float)."""
            return self.outer_instance.visa_write(f'INPut{self.channel}:LEVel {voltage}')
        
        @property
        def noise_reject(self):
            """ Get state of noise rejection algorithm (hysteresis) on input channel."""
            rsp = self.outer_instance.visa_query(f'INPut{self.channel}:NREject?')
            return rsp == 'ON'
        
        @noise_reject.setter
        def noise_reject(self, state):
            """ Set state of noise rejection algorithm (hysteresis) on input channel to `state`."""
            return self.outer_instance.visa_write(f'INPut{self.channel}:NREject {"ON" if state else "OFF"}')

    class _gate():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def slope(self):
            """Get the triggering slope level."""
            return self.outer_instance.visa_query(':GATE:STARt:SLOPe?')

        @slope.setter
        def slope(self, slope_type):
            """Set the slope of the gate start trigger to `slope_type`, either: POSitive or NEGative."""
            return self.outer_instance.visa_write(f':GATE:STARt:SLOPe {slope_type}')
        
        @property
        def delay(self):
            """Get gate start delay time."""
            return self.outer_instance.visa_query(':GATE:STARt:DELay:TIME?')

        @slope.setter
        def delay(self, delay_time):
            """Set gate start delay time (s, float)"""
            return self.outer_instance.visa_write(f'SENSe:GATE:STARt:SLOPe {delay_time}')

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        self.init_visa()
        self.inp_1 = self._input(self, 1)
        self.inp_2 = self._input(self, 2)
        self.gate = self._gate(self)

    @property
    def totalize_data(self):
        """ Get the number of totalize events recorded."""
        return float(self.visa_query(':TOTalize:DATA?'))
    
    @property
    def totalize_gate_time(self):
        """ Get gate time for totalize measurements in seconds."""
        return float(self.visa_query(':TOTalize:GATE:TIME?'))
    
    @totalize_gate_time.setter
    def totalize_gate_time(self, time):
        """ Set gate time (s, float) for totalize measurements."""
        return self.visa_write(f':TOTalize:GATE:TIME {time}')

    def close(self):
        """Close connection to device."""
        self.visa_resource.close()

