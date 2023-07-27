# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/MPQ

Device driver for Thorlabs K10CR1 motorized rotation mount.
"""

import numpy as np
import ctypes
import time
from pathlib import Path
from ctypes import wintypes

import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class TLI_DeviceInfo(ctypes.Structure):
    """Thorlabs Kinesis device information, generated from serial number and USB info block."""
    _fields_ = [('typeID', ctypes.c_ulong),
                ('description', ctypes.c_char * 65),
                ('serialNo', ctypes.c_char * 9),
                ('PID', ctypes.c_ulong),
                ('isKnownType', ctypes.c_bool),
                ('motorType', ctypes.c_ulong),
                ('isPiezoDevice', ctypes.c_bool),
                ('isLaser', ctypes.c_bool),
                ('isCustomType', ctypes.c_bool),
                ('isRack', ctypes.c_bool),
                ('maxChannels', ctypes.c_short)
                ]

class MOT_VelocityParameters(ctypes.Structure):
    """Structure containing the velocity parameters."""
    _fields_ = [('minVelocity', ctypes.c_int),
                ('acceleration', ctypes.c_int),
                ('maxVelocity', ctypes.c_int)
                ]

# Conversion factors for device units to real-world units (mm, mm/s, mm/s^2),
# taken from Thorlabs Motion Controllers, Host-Controller Communications Protocol (28 Nov 2022)
# (https://www.thorlabs.com/Software/Motion%20Control/APT_Communications_Protocol.pdf)
def_device_units = {}
# Device units for stage type 'Z8xx'
def_device_units['Z8xx'] = {
    'Position': 34554.96,
    'Velocity': 772981.3692,
    'Acceleration': 263.8443072,
    }
# Device units for stage type 'K10CR1', extracted from Thorlabs Kinesis software
def_device_units['K10CR1'] = {
    'Position': 24576000/180,
    'Velocity': 7329109,
    'Acceleration': 1502,
    }

class ThorlabsK10CR1(dev_generic.Device):
    """Device driver for Thorlabs K10CR1 motorized rotation mount."""
    # Device type ID (self Thorlabs Kinesis C API documentation):
    # 55: "cage rotator", including K10CR1
    DEVICE_TYPE_ID: int = 55

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device with serial number `serial_number` (int)."""
        super().__init__(device)

        ## Load Thorlabs Kinesis library
        # Filename of required DLL
        # (must be in the subdirectory 'bin' of the directory of this script)
        script_dir = Path(__file__).resolve().parent
        dll_filename = 'Thorlabs.MotionControl.IntegratedStepperMotors.dll'
        self.kinesis = ctypes.windll.LoadLibrary(str(Path(script_dir, 'bin', dll_filename)))

        # Callback function
        self.update_callback_func = update_callback_func

        # Device units to use
        self.def_device_units = def_device_units['K10CR1']
        # Serial number to open
        self.serial_number = device['SerialNumber']
        # Convert serial number to byte string
        self.serial_number_byte = str(self.serial_number).encode('ascii')
        # Init device open status
        self.device_connected = False
        # Init device information struct
        self.kinesis_device_info = TLI_DeviceInfo()

        # Build list of Kinesis devices
        self.kinesis.TLI_BuildDeviceList()
        # Built array for serial numbers size 100 and get device information
        serial_numbers_byte = ctypes.create_string_buffer(100)
        self.kinesis.TLI_GetDeviceListByTypeExt(serial_numbers_byte, 100, self.DEVICE_TYPE_ID)
        # Convert from byte array to Python string
        serial_numbers = serial_numbers_byte.value.decode().split(',')
        self.serial_numbers = [int(elem) for elem in serial_numbers if elem]

        if self.serial_number not in self.serial_numbers:
            msg = (
                f'Thorlabs Kinesis: Cannot find device with serial number {self.serial_number:d}'
                +f' and device type ID {self.DEVICE_TYPE_ID:d} in system')
            logger.error(msg)
            raise DeviceError(msg)
        self.device_present = True
        logger.info(
            'SN %d: Found device with device type ID %d in system',
            self.serial_number, self.DEVICE_TYPE_ID)

        # Get Kinesis device info.
        # This is derived from the serial number and USB info, and does not include device settings.
        self.kinesis.TLI_GetDeviceInfo(
            self.serial_number_byte, ctypes.byref(self.kinesis_device_info))

    def check_connection(self):
        """Check whether connection to device is open."""
        if not self.device_connected:
            msg = (
                f'Thorlabs Kinesis: Connection to device with serial number {self.serial_number:d}'
                +' not open')
            logger.error(msg)
            raise DeviceError(msg)

    def connect(self):
        """Open connection to device."""
        status = self.kinesis.ISC_Open(self.serial_number_byte)
        if status != 0:
            msg = (
                'Thorlabs Kinesis: Could not connect to device with serial number '
                +f'{self.serial_number:d} (is it open in another instance?)')
            logger.error(msg)
            raise DeviceError(msg)
        logger.info(
            'SN %d: Connected to device', self.serial_number)
        # Start internal loop, requesting position and status every 200 ms
        self.kinesis.ISC_StartPolling(self.serial_number_byte, 200)
        self.kinesis.ISC_ClearMessageQueue(self.serial_number_byte)
        # Wait for next status message
        message_type = wintypes.WORD()
        message_id = wintypes.WORD()
        message_data = wintypes.DWORD()
        _ = self.kinesis.ISC_WaitForMessage(
            self.serial_number_byte, ctypes.byref(message_type),
            ctypes.byref(message_id), ctypes.byref(message_data))
        self.device_connected = True
        self.get_device_status()

    def close(self):
        """Close connection to device."""
        if self.device_connected:
            self.kinesis.ISC_StopPolling(self.serial_number_byte)
            _ = self.kinesis.ISC_Close(self.serial_number_byte)
            self.device_connected = False

    def stop(self, method='profiled'):
        """Stop device movement."""
        self.check_connection()
        return self.kinesis.ISC_StopProfiled(self.serial_number_byte)

    def home(self):
        """Home device."""
        self.check_connection()
        return self.kinesis.ISC_Home(self.serial_number_byte)

    def convert_pos_from_dev_units(self, position_dev_units):
        """Convert position `position_dev_units` (int) from device units to degree (float)."""
        return float(np.mod(position_dev_units/self.def_device_units['Position'], 360))

    def convert_pos_to_dev_units(self, position):
        """Convert position `position` (float) from degree to device units (int)."""
        return int(np.mod(position, 360)*self.def_device_units['Position'])

    @property
    def position_dev_units(self):
        """Get device position in device units."""
        self.check_connection()
        return self.kinesis.ISC_GetPosition(self.serial_number_byte)

    @property
    def position(self):
        """Get position in degree."""
        return self.convert_pos_from_dev_units(self.position_dev_units)

    def move_relative_in_dev_units(self, distance_dev_units):
        """Move by relative distance `distance_dev_units` (int), given in device units."""
        self.check_connection()
        return self.kinesis.ISC_MoveRelative(self.serial_number_byte, int(distance_dev_units))

    def move_absolute_in_dev_units(self, position_dev_units):
        """Move to absolute position `position_dev_units` (int), given in device units."""
        self.check_connection()
        return self.kinesis.ISC_MoveToPosition(self.serial_number_byte, int(position_dev_units))

    def move_relative(self, distance):
        """Move by relative distance `distance` (float), given in degreee."""
        return self.move_relative_in_dev_units(self.convert_pos_to_dev_units(distance))

    def move_absolute(self, position):
        """Move to absolute position `position_dev_units` (float), given in degree."""
        # return self.move_absolute_in_dev_units(self.convert_pos_to_dev_units(position))
        return self.move_absolute_in_dev_units(int(position*self.def_device_units['Position']))

    def get_device_status(self):
        """Get device status."""
        self.check_connection()
        # Request status bit
        self.kinesis.ISC_RequestStatusBits(self.serial_number_byte)
        status_bits = self.kinesis.ISC_GetStatusBits(self.serial_number_byte)
        status_dict = {
            'CWHardwareLimitSwitch': status_bits & 0x00000001 != 0,
            'CCWHardwareLimitSwitch': status_bits & 0x00000002 != 0,
            'CWSoftwareLimitSwitch': status_bits & 0x00000004 != 0,
            'CCWSoftwareLimitSwitch': status_bits & 0x00000008 != 0,
            'MotorShaftMovingClockwise': status_bits & 0x00000010 != 0,
            'MotorShaftMovingCounterclockwise': status_bits & 0x00000020 != 0,
            'ShaftJoggingClockwise': status_bits & 0x00000040 != 0,
            'ShaftJoggingCounterclockwise': status_bits & 0x00000080 != 0,
            'MotorConnected': status_bits & 0x00000100 != 0,
            'MotorHoming': status_bits & 0x00000200 != 0,
            'MotorHomed': status_bits & 0x00000400 != 0,
            'DigitalInput1': status_bits & 0x00100000 != 0,
            'DigitalInput2': status_bits & 0x00200000 != 0,
            'DigitalInput3': status_bits & 0x00400000 != 0,
            'DigitalInput4': status_bits & 0x00800000 != 0,
            'DigitalInput5': status_bits & 0x01000000 != 0,
            'DigitalInput6': status_bits & 0x02000000 != 0,
            'Active': status_bits & 0x20000000 != 0,
            'ChannelEnabled' : status_bits & 0x80000000 != 0,
            }
        return status_dict

    @property
    def homed(self):
        """Device homed?"""
        status_dict = self.get_device_status()
        return status_dict['MotorHomed']
