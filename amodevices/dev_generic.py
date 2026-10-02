# -*- coding: utf-8 -*-
"""
Created on Wed Mar  7 16:55:25 2018

@author: Lothar Maisenbacher/UC Berkeley

Generic device driver.
"""

import numpy as np
import pyvisa
import pyvisa.rname
import logging
import re
import serial
import threading

from .dev_exceptions import DeviceError

# Thread lock to avoid writing/reading of serial ports from different threads
# at the same time
# All writers have to lock this
write_lock = threading.Lock()

logger = logging.getLogger(__name__)


def visa_resource_key(name):
    """A comparable form of the VISA resource name `name` (str): USB names
    become (interface, board, vendor ID, product code, serial number,
    USB interface number, resource class) with the IDs as integers, so a
    name written with hexadecimal IDs ('USB0::0x1313::0x8078::P0035092::INSTR',
    the usual spelling) equals the same resource as a backend lists it
    (pyvisa-py: 'USB0::4883::32888::P0035092::0::INSTR'). Other names, and
    names that do not parse, compare as their upper-case text."""
    def number(text):
        # '0x1313' and '4883' alike; a decimal with a leading zero too
        try:
            return int(text, 0)
        except ValueError:
            return int(text, 10)

    try:
        parsed = pyvisa.rname.parse_resource_name(name)
        if parsed.interface_type == 'USB':
            return (
                'USB', int(parsed.board or 0),
                number(parsed.manufacturer_id), number(parsed.model_code),
                parsed.serial_number, int(parsed.usb_interface_number or 0),
                parsed.resource_class.upper())
    except Exception:
        pass
    return str(name).upper()


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
        # Release any previously held port first, so repeated calls
        # (reconnection attempts) cannot fail on a still-open handle
        self.serial_close()
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
        if self.ser is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')
        query = command+eol
        try:
            with write_lock:
                n_write_bytes = self.ser.write((query).encode(encoding))
        except serial.SerialException as e:
            self.device_connected = False
            raise DeviceError(
                f'{self.device["Device"]}: Serial write failed: {e}')
        if n_write_bytes != len(query):
            raise DeviceError(f'{self.device["Device"]}: Query failed')

    def visa_address_is_lan(self) -> bool:
        """Whether the configured VISA address names a LAN resource
        ('TCPIP...::INSTR' or '...::SOCKET'), which `init_visa` opens
        without enumerating the VISA library's resources first."""
        return str(self.device.get('Address', '')).upper().startswith('TCPIP')

    def visa_backend(self):
        """The PyVISA backend the device dict names under 'VISABackend'
        (e.g. '@py' for the pure-Python pyvisa-py; absent or None = PyVISA's
        default, the installed VISA library such as NI-VISA)."""
        return self.device.get('VISABackend') or None

    def init_visa(self):
        """Initialize VISA connection.

        The device dict's optional 'VISABackend' selects the PyVISA backend
        (`visa_backend`). With '@py', USB instruments are reached through
        pyvisa-py and libusb (packages `pyvisa-py`, `pyusb` and, on Windows,
        `libusb-package`); the instrument must then be bound to a generic
        USB driver (WinUSB on Windows) rather than to a VISA vendor's
        driver, which libusb cannot open.
        """
        # Release any previously opened resource first, so repeated
        # calls (reconnection attempts) do not leak sessions
        if self.visa_resource is not None:
            try:
                self.visa_resource.close()
            except Exception:
                pass
            self.visa_resource = None
        self.device_connected = False
        # Initialize PyVISA to talk to VISA devices
        backend = self.visa_backend()
        visa_rm = (pyvisa.ResourceManager(backend) if backend
                   else pyvisa.ResourceManager())

        # Check if device can be found, then open device connection
        logger.info(
            'Connecting to device \'%s\' with VISA resource name \'%s\'%s',
            self.device['Device'], self.device['Address'],
            f' (PyVISA backend \'{backend}\')' if backend else '')
        if self.visa_address_is_lan():
            # A LAN resource is opened directly: the enumeration below
            # lists a LAN INSTR address only when it is registered with
            # the VISA library (e.g. in NI MAX) — so it decides nothing —
            # and NI-VISA probes the network for it, which took two
            # minutes on the DAQ PC while an instrument still held the
            # link of a process stopped seconds before (2026-09-21; the
            # server's web UI is unreachable until the device is open)
            logger.info(
                'LAN resource: opening the connection directly and reading'
                +' the instrument IDN...')
        elif self._visa_address_enumerated(visa_rm):
            logger.info(
                'A device with VISA resource name \'%s\' was found.'
                +' Trying to open connection and read instrument IDN...',
                self.device['Address'])
        else:
            logger.warning(
                'No device with VISA resource name \'%s\' is enumerated by the'
                +' VISA library. Trying to open connection directly and read'
                +' instrument IDN...',
                self.device['Address'])
        try:
            self.visa_resource = visa_rm.open_resource(self.device['Address'])
            visa_rcvd_idn = self.visa_resource.query('*IDN?').rstrip()
        except Exception as e:
            if self.visa_resource is not None:
                try:
                    self.visa_resource.close()
                except Exception:
                    pass
                self.visa_resource = None
            msg = (
                f'VISA error: Could not connect to device \'{self.device["Device"]}\''
                +f' with VISA resource name \'{self.device["Address"]}\': {e}')
            logger.error(msg)
            raise DeviceError(msg) from e
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
        self.device_connected = True

    def _visa_address_enumerated(self, visa_rm):
        """Whether the VISA library lists the configured address, compared
        by `visa_resource_key` (backends spell the same USB resource
        differently). Only the address's own interface is enumerated
        ('USB?*' for a USB address): a full listing makes pyvisa-py also
        search the network."""
        address = self.device['Address']
        match = re.match(r'[A-Za-z]+', str(address))
        query = f'{match.group(0).upper()}?*' if match else '?*::INSTR'
        key = visa_resource_key(address)
        try:
            names = visa_rm.list_resources(query)
        except Exception as e:
            # The open below decides; it reports what is missing
            logger.warning('VISA resource enumeration failed: %s', e)
            return False
        return any(visa_resource_key(name) == key for name in names)

    def visa_write(self, cmd):
        """Write VISA command `cmd` (str)."""
        if self.visa_resource is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')
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
        except pyvisa.errors.InvalidSession:
            # Raised on any use after the resource was closed; not a
            # `VisaIOError`
            self.device_connected = False
            msg = f'VISA session to device \'{self.device["Device"]}\' is closed'
            logger.error(msg)
            raise DeviceError(msg)
        except OSError as e:
            # The pyvisa-py backend lets USB failures through as libusb's
            # errors (`usb.core.USBError`, an `OSError`), e.g. an
            # instrument gone from the bus
            self.device_connected = False
            msg = (
                f'USB error in VISA communication with device '
                f'\'{self.device["Device"]}\' (VISA resource name '
                f'\'{self.device["Address"]}\'): {e}')
            logger.error(msg)
            raise DeviceError(msg) from e

    def visa_query(self, query, return_ascii=False):
        """
        Send VISA query `query` (str) and return response.
        """
        if self.visa_resource is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')
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
        except pyvisa.errors.InvalidSession:
            # Raised on any use after the resource was closed; not a
            # `VisaIOError`
            self.device_connected = False
            msg = f'VISA session to device \'{self.device["Device"]}\' is closed'
            logger.error(msg)
            raise DeviceError(msg)
        except OSError as e:
            # The pyvisa-py backend lets USB failures through as libusb's
            # errors (`usb.core.USBError`, an `OSError`), e.g. an
            # instrument gone from the bus
            self.device_connected = False
            msg = (
                f'USB error in VISA communication with device '
                f'\'{self.device["Device"]}\' (VISA resource name '
                f'\'{self.device["Address"]}\'): {e}')
            logger.error(msg)
            raise DeviceError(msg) from e

    def visa_query_binary(
            self, query, datatype='h', is_big_endian=False, chunk_size=2**20):
        """
        Send VISA query `query` (str) whose response is an IEEE 488.2
        definite-length binary block and return the values as a NumPy array.
        `datatype` (str) is the struct format code of one value (default 'h',
        signed 16-bit) and `is_big_endian` (bool) the byte order (default
        little-endian). `chunk_size` (int, bytes) is the read chunk size;
        the default of 1 MiB keeps long records from being read in the
        library's small default chunks.
        """
        if self.visa_resource is None:
            raise DeviceError(f'{self.device["Device"]}: Not connected')
        try:
            response = self.visa_resource.query_binary_values(
                query, datatype=datatype, is_big_endian=is_big_endian,
                container=np.array, header_fmt='ieee', chunk_size=chunk_size)
            logger.debug(
                'VISA binary query to device \'%s\': \'%s\'', self.device['Device'], query)
            logger.debug(
                'VISA device \'%s\' response: %d values', self.device['Device'], len(response))
            return response
        except pyvisa.VisaIOError as e:
            msg = (
                'Error in VISA communication with device \'{}\' (VISA resource name \'{}\'): {}'
                .format(
                    self.device['Device'], self.device['Address'], e.description))
            logger.error(msg)
            raise DeviceError(msg)
        except pyvisa.errors.InvalidSession:
            # Raised on any use after the resource was closed; not a
            # `VisaIOError`
            self.device_connected = False
            msg = f'VISA session to device \'{self.device["Device"]}\' is closed'
            logger.error(msg)
            raise DeviceError(msg)
        except OSError as e:
            # The pyvisa-py backend lets USB failures through as libusb's
            # errors (`usb.core.USBError`, an `OSError`), e.g. an
            # instrument gone from the bus
            self.device_connected = False
            msg = (
                f'USB error in VISA communication with device '
                f'\'{self.device["Device"]}\' (VISA resource name '
                f'\'{self.device["Address"]}\'): {e}')
            logger.error(msg)
            raise DeviceError(msg) from e

    def to_float(self, value):
        """Convert `value` to float."""
        try:
            value_ = float(value)
        except ValueError as e:
            raise DeviceError(
                f'Value \'{value}\' is not of expected type \'float\'.'
            ) from e
        return value_

    def to_int(self, value):
        """Convert `value` to int."""
        msg = f'Value \'{value}\' is not of expected type \'int\'.'
        try:
            value_float = float(value)
        except ValueError as e:
            raise DeviceError(msg) from e
        if not value_float.is_integer():
            raise DeviceError(msg)
        return int(value_float)
