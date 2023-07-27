# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/MPQ

Device driver for Thorlabs K10CR1 motorized rotation mount.
"""

import numpy as np
import ctypes
import time
from pathlib import Path

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

# Define KCube hardware info structure
class TLI_HardwareInformation(ctypes.Structure):

    _fields_ = [('serialNumber', ctypes.c_ulong),
                ('modelNumber', ctypes.c_char * 8),
                ('type', ctypes.c_ulong),
                ('numChannels', ctypes.c_short),
                ('notes', ctypes.c_char * 48),
                ('firmwareVersion', ctypes.c_ulong),
                ('hardwareVersion', ctypes.c_ulong),
                ('deviceDependentData', ctypes.c_byte),
                ('modificationState', ctypes.c_ulong)
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

class ThorlabsK10CR1(dev_generic.Device):
    """Device driver for Thorlabs K10CR1 motorized rotation mount."""
    # Device type ID (self Thorlabs Kinesis C API documentation):
    # 55: "cage rotator", including K10CR1
    DEVICE_TYPE_ID: int = 55
    # Timeout for device commands except homing (in s)
    TIMEOUT = 30
     # Timeout for homing of motors (in s)
    TIMEOUT_HOMING = 30
    # Default value for the precision to which device position units are converted to SI units
    # (in mm)
    POSITION_PRECISION = 1e-5
    # Tolerance (in device steps) within position should be reached (otherwise a timeout occurs)
    STEP_TOLERANCE = 0
    # Time to wait between position checks while motor is moving to a position (in s)
    WAIT_LOOP = 0.01
    # Final wait time to check whether motor stays on target position (in multiples of WAIT_LOOP)
    WAIT_FINAL = 0.5
    # Time to wait between position checks while motor is homing (in s)
    WAIT_LOOP_HOME = 0.1
    # Compare position device units from defined in this class with those calculated from the
    # retrieved device parameters
    CHECK_DEVICE_UNITS = True

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

        # Serial number to open
        self.serial_number = device['SerialNumber']
        # Convert serial number to byte string
        self.serial_number_byte = str(self.serial_number).encode('ascii')
        # Init device open status
        self.open = False
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

        # Get Kinesis device info.
        # This is derived from the serial number and USB info, and does not include device settings.
        self.kinesis.TLI_GetDeviceInfo(
            self.serial_number_byte, ctypes.byref(self.kinesis_device_info))

    def check_connection(self):
        """Check whether connection to device is open."""
        if not self.open:
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
        self.open = True
        self.kinesis.ISC_ClearMessageQueue(self.serial_number_byte)
        # Start polling device with 200 ms interval
        self.kinesis.ISC_StartPolling(self.serial_number_byte, 200)

    def close(self):
        """Close connection to device."""
        if self.open:
            self.kinesis.ISC_StopPolling(self.serial_number_byte)
            status = self.kinesis.ISC_Close(self.serial_number_byte)
            print(status)
            self.open = False

    def get_position(self):
        """Get device position."""
        self.check_connection()
        return self.kinesis.ISC_GetPosition(self.serial_number_byte)

    def stop(self, method='profiled'):
        """Stop device movement."""
        self.check_connection()
        return self.kinesis.ISC_StopProfiled(self.serial_number_byte)

# serial_number = 55193694

# device = {
#     'Device': 'Thorlabs K10CR1',
#     'SerialNumber': 55193694,
#     }

# device = ThorlabsK10CR1(device)
# device.connect()
# kinesis = device.kinesis
# kinesis.ISC_MoveRelative(device.serial_number_byte, 1000000)
# device.kinesis.ISC_MoveRelative(device.serial_number_byte, 1000000)
# device.close()
# device.getDeviceList()
# print(device.serial_numbers)
# # Open device
# print(device.kinesis.ISC_Open(serial_number))
# # device.openDevice(0)
# device.kinesis.ISC_ClearMessageQueue(serial_number)
# device.kinesis.ISC_StartPolling(serial_number, 200)

# # device.kinesis.ISC_Home(serial_number)
# device.kinesis.ISC_MoveRelative(serial_number, 1000000)

# #
# # device2 = K10CR1()
# # device2.getDeviceList()
# # print(device2.serial_numbers)
# # print(device2.kinesis.ISC_Open(serial_number))
# # device2.kinesis.ISC_MoveRelative(serial_number, 1000000)

# # Close device
# device.kinesis.ISC_StopPolling(serial_number)
# device.kinesis.ISC_Close(serial_number)

#     def openDevice(self, deviceID, motor_settings=None,
#                    positionPrecision=POSITION_PRECISION):

#         self.checkDeviceID(deviceID)
#         motor_settings = motor_settings if motor_settings is not None else {}
#         if not self.deviceOpen[deviceID]:
#             logger.info('KDC: Opening device with serial no. {:d} and ID {:d}.' \
#                         .format(self.serial_numbers[deviceID], deviceID))
#             # Open device
#             status = self.kinesis.CC_Open(self.serial_numbers_bin[deviceID])
#             if status != 0:
#                 msg = 'Could not open device with serial no. {:d} and ID {:d}, error code {:d}.' \
#                     .format(self.serial_numbers[deviceID], deviceID, status)
#                 raise KDCerror(msg)
#             # Check if device could be opened, otherwise raise error
#             # Load device settings into DLL
#             self.kinesis.CC_LoadSettings(self.serial_numbers_bin[deviceID])
#             # Start internal loop, requesting position and status continuously
#             self.kinesis.CC_StartPolling(self.serial_numbers_bin[deviceID], 100)
#             # Get device and hardware info
#             self.deviceInfo[deviceID] = TLI_DeviceInfo()
#             status = self.kinesis.TLI_GetDeviceInfo(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(self.deviceInfo[deviceID]))
#             self.hardwareInfo[deviceID] = TLI_HardwareInformation()
#             status = self.kinesis.CC_GetHardwareInfoBlock(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(self.hardwareInfo[deviceID]))
#             # Set conversion factors
#             if (stage_type := motor_settings.get('StageType')) not in def_device_units:
#                 msg = (
#                     f'Device units for stage type \'{stage_type}\' not defined'
#                     +f' (for device with serial no. {self.serial_numbers[deviceID]:d} '
#                     +f'and ID {deviceID:d})')
#                 raise KDCerror(msg)
#             device_units = def_device_units[stage_type]
#             self.device_units[deviceID] = device_units
#             # Calculate conversion factor from device units to mm
#             stepsPerRev = ctypes.c_double()
#             gearBoxRatio = ctypes.c_double()
#             pitch = ctypes.c_double()
#             status = self.kinesis.CC_GetMotorParamsExt(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(stepsPerRev),
#                 ctypes.byref(gearBoxRatio), ctypes.byref(pitch))
#             if status != 0:
#                 msg = (
#                     'Could not get motor parameters for device with serial'
#                     +f' no. {self.serial_numbers[deviceID]:d} and ID {deviceID:d}'
#                     +f', error code {status:d}.')
#                 raise KDCerror(msg)
#             steps_per_mm_calc = stepsPerRev.value*gearBoxRatio.value/pitch.value
#             steps_per_mm_calc_round = np.round(steps_per_mm_calc, 2)
#             steps_per_mm_def_round = np.round(device_units['Position'], 2)
#             if self.CHECK_DEVICE_UNITS and (steps_per_mm_calc_round != steps_per_mm_def_round):
#                 msg = (
#                     f'Steps per mm for device with serial no. {self.serial_numbers[deviceID]:d}'
#                     +f' and ID {deviceID:d} is {steps_per_mm_calc_round:.2f}'
#                     +f', not {steps_per_mm_def_round:.2f} as expected.')
#                 raise KDCerror(msg)
#             self.positionSigDigits[deviceID] = int(-np.log10(positionPrecision))
#             # Get hardware axis limits
#             axisMinPos = self.kinesis.CC_GetStageAxisMinPos(self.serial_numbers_bin[deviceID])
#             axisMaxPos = self.kinesis.CC_GetStageAxisMaxPos(self.serial_numbers_bin[deviceID])
#             self.axisLimitsHardware[deviceID] = [axisMinPos, axisMaxPos]
#             # Mark device as open
#             self.deviceOpen[deviceID] = True
#             # Set user axis limits (implemented in software)
#             axis_limits_user = [
#                 motor_settings.get('AxisLimitMin'), motor_settings.get('AxisLimitMax')]
#             self.setAxisLimitsUser(deviceID, axis_limits_user)
#             # Get device velocity and acceleration
#             velocity_params = MOT_VelocityParameters()
#             status = self.kinesis.CC_GetVelParamsBlock(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(velocity_params))
#             # Set velocity parameters if given in settings
#             if (value := motor_settings.get('Acceleration')) is not None:
#                 velocity_params.acceleration = int(value*device_units['Acceleration'])
#             if (value := motor_settings.get('MaxVelocity')) is not None:
#                 velocity_params.maxVelocity = int(value*device_units['Velocity'])
#             status = self.kinesis.CC_SetVelParamsBlock(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(velocity_params))
#             status = self.kinesis.CC_GetVelParamsBlock(
#                 self.serial_numbers_bin[deviceID], ctypes.byref(velocity_params))
#             self.velocity_params[deviceID] = velocity_params
#         else:
#             msg = 'KDC: Device with serial no. {:d} and ID {:d} is already open.' \
#                 .format(self.serial_numbers[deviceID], deviceID)
#             logger.debug(msg)

#     def closeDevice(self, deviceID):

#         self.checkDeviceOpen(deviceID)
#         logger.info('KDC: Closing device with serial no. {:d} and ID {:d}.' \
#                     .format(self.serial_numbers[deviceID], deviceID))
#         status = self.kinesis.CC_StopPolling(self.serial_numbers_bin[deviceID])
#         status = self.kinesis.CC_Close(self.serial_numbers_bin[deviceID])
#         self.deviceOpen[deviceID] = False

#     def setAxisLimitsUser(self, device_id, axis_limits):
#         """
#         Set user axis limits `axis_limits = [lower_limit, upper_limit]` (2-element list-like of
#         floats) (in units of mm), where `lower_limit` (`upper_limit`) are the lower and upper limit,
#         for device `device_id` (int).
#         If either limit is set to `np.nan` or None, the limit is not set.
#         These limits are enforced in software, i.e., the limits of the controller are left
#         unchanged.
#         """
#         self.checkDeviceOpen(device_id)
#         self.axisLimitsUser[device_id] = [
#             self.convertmmtoDevUnits(device_id, elem) if ~np.isnan(elem) and elem is not None
#             else np.nan for elem in axis_limits]

#     def get_axis_limits_dev_units(self, device_id):
#         """
#         Get axis limits (2-element 1D array of floats) of device `device_id` (int) in device units.
#         """
#         self.checkDeviceOpen(device_id)
#         axis_limits = (np.array([
#             np.nanmax([self.axisLimitsHardware[device_id][0], self.axisLimitsUser[device_id][0]]),
#             np.nanmin([self.axisLimitsHardware[device_id][1], self.axisLimitsUser[device_id][1]])
#             ])
#             .astype(int))
#         return axis_limits

#     def get_axis_limits(self, device_id):
#         """
#         Get axis limits (2-element 1D array of floats) of device `device_id` (int) in units of mm.
#         """
#         return self.convertDevUnitsTomm(device_id, self.get_axis_limits_dev_units(device_id))

#     def getDeviceStatus(self, deviceID):

#         self.checkDeviceOpen(deviceID)
#         self.kinesis.CC_RequestStatusBits(self.serial_numbers_bin[deviceID])
#         statusBits = self.kinesis.CC_GetStatusBits(self.serial_numbers_bin[deviceID])
#         statusBits = [int(i) for i in bin(statusBits).split('b')[1].zfill(32)]
#         statusBits.reverse()
#         statusDict = {
#             'CWHardwareLimitSwitch' : statusBits[0],
#             'CCWHardwareLimitSwitch' : statusBits[1],
#             'CWSoftwareLimitSwitch' : statusBits[2],
#             'CCWSoftwareLimitSwitch' : statusBits[3],
#             'MotorShaftMovingClockwise' : 1-statusBits[4],
#             'MotorShaftMovingCounterclockwise' : 1-statusBits[5],
#             "ShaftJoggingClockwise" : 1-statusBits[6],
#             "ShaftJoggingCounterclockwise" : 1-statusBits[7],
#             'MotorConnected' : 1-statusBits[8],
#             "MotorHoming" : 1-statusBits[9],
#             "MotorHomed" : bool(1-statusBits[10]),
#             "DigitalInput1" : statusBits[20],
#             "DigitalInput2" : statusBits[21],
#             "DigitalInput3" : statusBits[22],
#             "DigitalInput4" : statusBits[23],
#             "DigitalInput5": statusBits[24],
#             "DigitalInput6": statusBits[25],
#             "Active": 1-statusBits[29],
#             "ChannelEnabled" : statusBits[31],
#             }
#         self.deviceStatus[deviceID] = statusDict
#         return statusDict

#     def convertDevUnitsTomm(self, deviceID, position):
#         """
#         Convert position `position` (int) from device units to units of mm (float) for motor
#         `deviceID` (int).
#         """
#         self.checkDeviceOpen(deviceID)
#         position_mm = np.around(
#             position/self.device_units[deviceID]['Position'], self.positionSigDigits[deviceID])
#         return position_mm

#     def convertmmtoDevUnits(self, deviceID, position_mm):
#         """
#         Convert position `position_mm` (float) from units of mm to device units (int) for motor
#         `deviceID` (int).
#         """
#         self.checkDeviceOpen(deviceID)
#         position = int(np.around(position_mm*self.device_units[deviceID]['Position'], 0))
#         return position

#     def getPosition(self, deviceID):
#         """Get current position (float) of motor `deviceID` (int) in units of mm."""
#         position = self.getPositionDevUnits(deviceID)
#         return self.convertDevUnitsTomm(deviceID, position)

#     def getPositionDevUnits(self, deviceID):
#         """Get current position (int) of motor `deviceID` (int) in device units."""
#         self.checkDeviceOpen(deviceID)
#         self.kinesis.CC_RequestPosition(self.serial_numbers_bin[deviceID])
#         position = self.kinesis.CC_GetPosition(self.serial_numbers_bin[deviceID])
#         return position

#     def moveToPosition(self, deviceID, position_mm):
#         """
#         Move motor `deviceID` (int) to new position `position_mm` (float), given in units of mm.
#         """
#         position = self.convertmmtoDevUnits(deviceID, position_mm)
#         status, position_mm = self.moveToPositionDevUnits(deviceID, position)
#         return status, position

#     def moveToPositionDevUnits(self, deviceID, position):
#         """
#         Move motor `deviceID` (int) to new position `position` (int), given in device units.
#         """
#         self.checkDeviceOpen(deviceID)
#         position_mm = self.convertDevUnitsTomm(deviceID, position)
#         # Check whether requested position is in hardware and software limits
#         axis_limits = self.get_axis_limits_dev_units(deviceID)
#         axis_limits_mm = self.get_axis_limits(deviceID)

#         if not axis_limits[0] <= position <= axis_limits[1]:
#             msg = (
#                 f'Requested position position {position:d} dev. u./{position_mm:.6f} mm'
#                 +' outside axis limits'
#                 +f' ([{axis_limits[0]:d}, {axis_limits[1]:d}] dev. u.'
#                 +f'/[{axis_limits_mm[0]:.3f}, {axis_limits_mm[1]:.3f}] mm)'
#                 +f' for device serial no. {self.serial_numbers[deviceID]:d} and ID {deviceID:d}.')
#             raise KDCerror(msg)
#         # Moves motor to given position
#         self.motorStopped[deviceID] = False
#         status = self.kinesis.CC_MoveToPosition(self.serial_numbers_bin[deviceID], position)
#         return status, position_mm

#     def waitTillPositionReached(self, deviceID, position_mm, **kwargs):

#         self.checkDeviceOpen(deviceID)
#         position = self.convertmmtoDevUnits(deviceID, position_mm)
#         return self.waitTillPositionReachedDevUnits(deviceID, position, **kwargs)

#     def waitTillPositionReachedDevUnits(self, deviceID, position, stepTol=STEP_TOLERANCE, loopSleep=WAIT_LOOP, finalWait=WAIT_FINAL):

#         # Loop parameters
#         self.checkDeviceOpen(deviceID)
#         posReached = -1
#         nLoops = 0
#         nLoopsBounce = np.zeros(0)
#         nLoopsTol = np.zeros(0)
#         startTime = time.time()
#         while True:
#             # Get current device postion
#             currPosition = self.getPositionDevUnits(deviceID)
#             # Check if device was stopped manually while this loop is executing
#             if self.motorStopped[deviceID]:
#                 # Release device stop
#                 self.motorStopped[deviceID] = False
#                 msg = 'Motor stopped manually while waiting for motor (device serial no. {:d} and ID {:d}) to reach position {:d} dev. u./{:.6f} mm.' \
#                     .format(self.serial_numbers[deviceID], deviceID, position, self.convertDevUnitsTomm(deviceID, position))
#                 raise KDCerror(msg)
#             # Check whether device has reached target position within tolerance (stepTol)
#             if position-stepTol <= currPosition <= position+stepTol:
#                 # Device has reached target position from different position in previous cycle
#                 if posReached == -1:
# #                    print('Position reached with tolerance {:d}'.format(currPosition-position))
#                     nLoopsTol = np.append(nLoopsTol, currPosition-position)
#                     # Set loop counter to current loop count
#                     posReached = nLoops
#                 # Check whether device has stayed on target position for defined amount of time (finalWait), measured in units of loop cycles
#                 if nLoops > (posReached+finalWait/loopSleep):
#                     logger.info('KDC: Motor (device serial no. {:d} and ID {:d}) has reached position {:d} dev. u./{:.6f} mm after {:.2f} s.'
#                                 .format(
#                                         self.serial_numbers[deviceID], deviceID, currPosition,
#                                         self.convertDevUnitsTomm(deviceID, currPosition), time.time()-startTime)
#                                         )
#                     break
#             else:
#                 # Check if device had reached target position before
#                 if posReached != -1:
# #                    print('Position moved after again after {:d}'.format(nLoops-posReached))
#                     nLoopsBounce = np.append(nLoopsBounce, nLoops-posReached)
#                 # Reset loop counter
#                 posReached = -1
#             if self.update_callback_func is not None:
#                 self.update_callback_func()
#             time.sleep(loopSleep)
#             if time.time()-startTime > self.TIMEOUT:
#                 # Operation timed out
#                 # Stop motor
#                 self.stopMotor(deviceID)
#                 # Release device stop
#                 self.motorStopped[deviceID] = False
#                 msg = 'Timeout while waiting for motor (device serial no. {:d} and ID {:d}) to reach position {:d} dev. u./{:.6f} mm, motor stopped.' \
#                     .format(self.serial_numbers[deviceID],deviceID,position,self.convertDevUnitsTomm(deviceID, position))
#                 raise KDCerror(msg)
#             nLoops = nLoops + 1
#         return nLoopsTol, nLoopsBounce*loopSleep

#     def homeMotor(self, deviceID):

#         self.checkDeviceOpen(deviceID)
#         self.kinesis.CC_Home(self.serial_numbers_bin[deviceID])

#     def waitTillMotorHomed(self, deviceID, loopSleep=WAIT_LOOP_HOME):

#         self.checkDeviceOpen(deviceID)
#         startTime = time.time()
#         time.sleep(1)
#         while True:
#             motorHomed = self.getDeviceStatus(deviceID)['MotorHomed']
#             # Check if device was stopped manually while this loop is executing
#             if self.motorStopped[deviceID]:
#                 # Release device stop
#                 self.motorStopped[deviceID] = False
#                 msg = 'Motor stopped manually while waiting for motor (device serial no. {:d} and ID {:d}) to home.' \
#                     .format(self.serial_numbers[deviceID], deviceID)
#                 raise KDCerror(msg)
#             if motorHomed:
#                 logger.info('KDC: Motor (device serial no. {:d} and ID {:d}) was homed after {:.2f} s.' \
#                             .format(self.serial_numbers[deviceID], deviceID, time.time()-startTime))
#                 break
#             time.sleep(loopSleep)
#             if time.time()-startTime > self.TIMEOUT_HOMING:
#                 # Operation timed out
#                 msg = 'Timeout while waiting for motor to home (device serial no. {:d} and ID {:d}).' \
#                     .format(self.serial_numbers[deviceID], deviceID)
#                 raise KDCerror(msg)
#         return True

#     def stopMotor(self, deviceID):

#         self.checkDeviceOpen(deviceID)
#         # Signal to other functions that motor has been stopped manually
#         self.motorStopped[deviceID] = True
#         self.kinesis.CC_StopProfiled(self.serial_numbers_bin[deviceID])
