# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Driver for Thorlabs MDT693B 3-axis piezo controller.
"""

import serial
import logging
import threading

# Thread lock to avoid writing/reading of serial ports from different threads
# at the same time
# All writers have to lock this
write_lock = threading.Lock()
# All readers have to lock this
read_lock = threading.Lock()

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

class ThorlabsMDT693B(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(ThorlabsMDT693B, self).__init__(device)
        self.ser = None

    def connect(self):
        """Open serial connection to device."""
        device = self.device
        try:
            ser = serial.Serial(
                device['Address'], timeout=device.get('Timeout'),
                **device.get('SerialConnectionParams', {}))
        except serial.SerialException:
            raise DeviceError(
                f'{device["Device"]}: Serial connection couldn\'t be opened')
        logger.info(
            '%s: Opened serial connection on port \'%s\'',
            device['Device'], device['Address']
            )
        self.ser = ser
        self.device_present = True
        self.device_connected = True

    def close(self):
        """Close serial connection to device."""
        if self.ser is not None:
            self.ser.close()
        self.device_connected = False

    def write(self, command):
        """Write command `command` (str) to device."""
        query = command+'\n'
        with write_lock:
            n_write_bytes = self.ser.write((command+'\n').encode('ASCII'))
        if n_write_bytes != len(query):
            raise DeviceError(f'{self.device["Device"]}: Query failed')

    def query(self, command):
        """Query device with command `command` (str) and return response (str)."""
        self.write(command)
        with read_lock:
            response = self.ser.read_until(b'\r')
            ack = self.ser.read(1).decode()
        if ack != '>':
            raise DeviceError(f'{self.device["Device"]}: Device failed to acknowledge command')
        return response.rstrip()

    def send_command(self, command):
        """Send command `command` (str) to device and read acknowledgment."""
        self.write(command)
        with read_lock:
            ack = self.ser.read(1).decode()
        if ack != '>':
            raise DeviceError(f'{self.device["Device"]}: Device failed to acknowledge command')

    def _check_axis(self, axis):
        if axis not in ['x', 'y', 'z']:
            raise DeviceError(
                f'{self.device["Device"]}: Unknown axis \'{axis}\'')

    def read_voltage(self, axis):
        """Read voltage of axis `axis` (str, either 'x', 'y', or 'z')."""
        self._check_axis(axis)
        command = f'{axis}voltage?'
        try:
            response = self.query(command)
        except serial.SerialException as e:
            raise DeviceError(
                f'{self.device["Device"]}: Serial exception encountered: {e}')
        try:
            voltage = float(response[1:-1])
        except ValueError:
            raise DeviceError(
                f'{self.device["Device"]}: Could not convert response {response} to float')
        return voltage

    def set_voltage(self, axis, voltage):
        """
        Set voltage of axis `axis` (str, either 'x', 'y', or 'z') to voltage `voltage` (float, units
        of V).
        """
        self._check_axis(axis)
        min_voltage = 0
        if voltage < min_voltage:
            raise DeviceError(
                f'{self.device["Device"]}: Voltage must not be below {min_voltage:.2f} V')
        command = f'{axis}voltage={voltage}'
        try:
            self.send_command(command)
        except serial.SerialException as e:
            raise DeviceError(f'Serial exception encountered: {e}')

    # def get_values(self):
    #     """Read channels."""
    #     chans = self.device['Channels']
    #     readings = {}
    #     for channel_id, chan in chans.items():
    #         if chan['Type'] in ['PV1', 'SV1']:
    #             value = self.read_temperature(chan['Type'])
    #             readings[channel_id] = value
    #         else:
    #             raise LoggerError(
    #                 f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
    #                 +f' of device \'{self.device["Device"]}\'')
    #     return readings

# device = {
#     'Device': 'Thorlabs MDT693B',
#     'Address': 'COM3',
#     'Timeout': 1,
#     'SerialConnectionParams': {
#         "baudrate":115200,
#         "bytesize":8,
#         "stopbits":1,
#         "parity":"N"
#         }
#     }
# device_instance = Device(device)
# try:
#     device_instance.connect()
#     # print(device_instance.write('echo=0'))
#     # print(device_instance.query('echo?'))
#     print(device_instance.set_voltage('y', 10))
#     # print(device_instance.read_voltage('x'))
#     print(device_instance.read_voltage('y'))
#     for i in range(100):
#         print(device_instance.set_voltage('x', 10))
#         print(device_instance.read_voltage('x'))
#         print(device_instance.read_voltage('y'))
#         print(device_instance.read_voltage('z'))
#     # print(device_instance.read_voltage('z'))
# except LoggerError as e:
#     print(e.value)
# finally:
#     None
#     device_instance.close()
