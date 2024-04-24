# -*- coding: utf-8 -*-
"""
@author: Your name here!

Brief description of what this code does/is. Check other drivers
for 'inspiration'
"""

import logging, serial

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class srsSim922(dev_generic.Device):

    def __init__(self, device):
        super().__init__(device)

    def query(self, message):
        return
    
    def read_temperature(self, message):
        return
    
    