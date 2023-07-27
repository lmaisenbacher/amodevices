# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Definitions of device exceptions.
"""

class DeviceError(Exception):
    """Device error."""
    def __init__(self, value, **kwds):
        super().__init__(**kwds)
        self.value = value
