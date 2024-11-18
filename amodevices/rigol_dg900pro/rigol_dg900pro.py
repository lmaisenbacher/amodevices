# -*- coding: utf-8 -*-
"""
Created on Mon Nov 18 15:17:00 2024

@author: Lothar Maisenbacher/Berkeley

Device driver for Rigol DG800 Pro/DG900 Pro function generator, controlled through VISA.
"""

import numpy as np
import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class RigolDG900Pro(dev_generic.Device):
    """Device driver for Rigol DG800 Pro/DG900 Pro function generator."""

    class _channel():

        def __init__(self, outer_instance, channel_number):
            self.outer_instance = outer_instance
            self.channel_number = channel_number

        @property
        def frequency(self):
            """Get frequency (Hz, float)."""
            return float(self.outer_instance.visa_query(f':SOUR{self.channel_number:d}:FREQ?'))

        @frequency.setter
        def frequency(self, freq):
            """Set frequency to `freq` (Hz, float)."""
            return self.outer_instance.visa_write(f':SOUR{self.channel_number:d}:FREQ {freq}')

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device `device` (dict)."""
        super().__init__(device)

        self.init_visa()

    def close(self):
        """Close connection to device."""
        self.visa_resource.close()

    def channel(self, channel_number):
        """
        Return instance of channel class (class `_channel`) for channel number
        `channel_number` (int), which is either 1 or 2.
        """
        return self._channel(self, channel_number)
