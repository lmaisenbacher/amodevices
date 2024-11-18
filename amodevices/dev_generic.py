# -*- coding: utf-8 -*-
"""
Created on Wed Mar  7 16:55:25 2018

@author: Lothar Maisenbacher/Berkeley

Generic device driver.
"""

import numpy as np
import pyvisa
import logging
import serial
import threading

from .dev_exceptions import DeviceError

# Thread lock to avoid writing/reading of serial ports from different threads
# at the same time
# All writers have to lock this
write_lock = threading.Lock()

logger = logging.getLogger(__name__)

class Device:

    def __init__(self, device):
        """Init device."""
        # Add default values
        device = {
            'DeviceSpecificParams': {},
            **device
            }
        self.device_present = False
        self.device_connected = False
        self.device = device
        self.ser = None
        self.visa_warning = False
        self.visa_resource = None

    def connect(self):
        """Open connection to device."""
        None

    def close(self):
        """Close connection to device."""
        None

    def serial_connect(self):
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

    def serial_close(self):
        """Close serial connection to device."""
        if self.ser is not None:
            self.ser.close()
        self.device_connected = False

    def serial_write(self, command, encoding='ASCII', eol='\n'):
        """
        Write command `command` (str) to device over serial connection,
        using encoding `encoding` (str; default is 'ASCII') and end-of-line character
        `eol` (str; default is '\n').
        """
        query = command+eol
        with write_lock:
            n_write_bytes = self.ser.write((query).encode(encoding))
        if n_write_bytes != len(query):
            raise DeviceError(f'{self.device["Device"]}: Query failed')

    def init_visa(self):
        """Initialize VISA connection."""
        # Initialize PyVISA to talk to VISA devices
        visa_rm = pyvisa.ResourceManager()
        visa_rsrc_list = visa_rm.list_resources()

        # Check if device can be found, if yes open device connection
        logger.info(
            'Connecting to device \'%s\' with VISA resource name \'%s\'',
            self.device['Device'], self.device['Address'])
        if self.device['Address'] in visa_rsrc_list:
            logger.info(
                'A device with VISA resource name \'%s\' was found.'
                +' Trying to open connection and read instrument IDN...',
                self.device['Address'])
            try:
                self.visa_resource = visa_rm.open_resource(self.device['Address'])
                visa_rcvd_idn = self.visa_resource.query('*IDN?').rstrip()
            except:
                msg = 'VISA error: Could not connect to device!'
                logger.error(msg)
                raise DeviceError(msg)
            else:
                logger.info(
                    'Connected to device \'%s\' with VISA resource name \'%s\'',
                    self.device['Device'], self.device['Address'])
            if 'Timeout' in self.device:
                self.visa_resource.timeout = self.device['Timeout']*1e3
            if self.device.get('VISAIDN', None) is not None:
                if visa_rcvd_idn == self.device['VISAIDN']:
                    logger.info(
                        'Received instrument IDN (\'%s\') matches saved IDN!', visa_rcvd_idn
                        )
                else:
                    logger.warning(
                        'VISA warning: Received instrument IDN (\'%s\')'
                        +' DOES NOT match saved IDN!',
                        visa_rcvd_idn)
                    self.visa_warning = True
            if self.device.get('CmdOnInit', None) is not None:
                logger.info(
                    'Sending initialization command \'%s\' to VISA device \'%s\'',
                    self.device['CmdOnInit'], self.device['Device'])
                self.visa_write(self.device['CmdOnInit'])
            self.device_present = True
        else:
            msg = (
                f'VISA error: No device with VISA resource name \'{self.device["Address"]}\''
                +' found!')
            logger.error(msg)
            raise DeviceError(msg)

    def visa_write(self, cmd):
        """Write VISA command `cmd` (str)."""
        try:
            self.visa_resource.write(cmd)
            logger.debug('VISA write to device \'%s\': \'%s\'', self.device['Device'], cmd)
        except pyvisa.VisaIOError as e:
            msg = (
                'Error in VISA communication with device \'{}\' (VISA resource name {}): {}'
                .format(
                    self.device['Device'], self.device['Address'], e.description))
            logger.error(msg)
            raise DeviceError(msg)

    def visa_query(self, query, return_ascii=False):
        """
        Send VISA query `query` (str) and return response.
        """
        try:
            if return_ascii:
                response = self.visa_resource.query_ascii_values(query, container=np.array)
            else:
                response = self.visa_resource.query(query).rstrip()
            logger.debug('VISA query to device \'%s\': \'%s\'', self.device['Device'], query)
            logger.debug('VISA device \'%s\' response: \'%s\'', self.device['Device'], response)
            return response
        except pyvisa.VisaIOError as e:
            msg = (
                'Error in VISA communication with device \'{}\' (VISA resource name \'{}\'): {}'
                .format(
                    self.device['Device'], self.device['Address'], e.description))
            logger.error(msg)
            raise DeviceError(msg)

    def to_float(self, value):
        """Convert `value` to float."""
        try:
            value_ = float(value)
        except ValueError:
            raise DeviceError('Value \'%s\' is not of expected type \'float\'.', value)
        return value_

    def to_int(self, value):
        """Convert `value` to int."""
        e ='Value \'{}\' is not of expected type \'int\'.'.format(value)
        try:
            value_float = float(value)
        except ValueError:
            raise DeviceError(e)
        if not value_float.is_integer():
            raise DeviceError(e)
        else:
            return int(value_float)
