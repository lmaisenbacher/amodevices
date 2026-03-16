# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/Berkeley

Device driver for Thorlabs KPA101 beam position aligner, using pyserial and
the Thorlabs APT binary protocol directly.
"""

import struct
import time
import numpy as np
from collections import namedtuple
from serial.tools import list_ports
import serial
import logging

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# APT protocol constants
_DEST = 0x50   # generic USB device
_SRC  = 0x01   # host

# Voltage scaling: ±10 V maps to ±32767 (signed short)
_SCALE = 10.0 / 32767

# Operation mode: string ↔ integer mapping
_MODE_STR_TO_INT = {
    'monitor':     1,
    'open_loop':   2,
    'closed_loop': 3,
    'auto_loop':   4,
}
_MODE_INT_TO_STR = {v: k for k, v in _MODE_STR_TO_INT.items()}

DeviceInfo = namedtuple(
    'DeviceInfo',
    ['serial_number', 'model', 'fw_version', 'hw_version', 'num_channels'])


class ThorlabsKPA101(dev_generic.Device):
    """Device driver for Thorlabs KPA101 beam position aligner.

    Uses pyserial and the Thorlabs APT binary protocol directly; does not
    require pylablib.
    """

    def __init__(self, device, update_callback_func=None):
        """Initialize class for device with settings `device` (dict)."""
        super().__init__(device)

        # Serial number to open
        self.serial_number = device['SerialNumber']
        # Init device open status
        self.device_connected = False
        # pyserial Serial instance
        self._ser = None
        # Timestamp of last reading
        self._last_reading_time = None
        # Cache interval for readings (s)
        self._cache_interval = device.get('CacheInterval', 0.1)
        # Cached readings
        self._xdiff = np.nan
        self._ydiff = np.nan
        self._sum = np.nan
        self._xpos = np.nan
        self._ypos = np.nan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_port(self):
        """Return the COM port device path matching `self.serial_number`.

        The Windows FTDI driver appends a channel-letter suffix to the base
        serial number stored in the chip EEPROM ('A' for the first/only port).
        For a single-port device like the KPA101, the USB serial number string
        reported by the OS is therefore ``str(serial_number) + 'A'``.

        Raises `DeviceError` if no matching port is found.
        """
        target = str(self.serial_number) + 'A'
        ports = list_ports.comports()
        for p in ports:
            if p.serial_number == target:
                return p.device, target
        raise DeviceError(
            f'Thorlabs KPA101: No COM port found for serial number '
            f'{self.serial_number} (looked for USB serial \'{target}\'; '
            f'available: '
            f'{[(p.device, p.serial_number) for p in ports]})')

    def _short_msg(self, msg_id, param1=0, param2=0):
        """Build and return a 6-byte APT short (no-data) message."""
        return struct.pack('<HBBBB', msg_id, param1, param2, _DEST, _SRC)

    def _long_msg(self, msg_id, data):
        """Build and return an APT long message with payload `data`."""
        return struct.pack('<HHBB', msg_id, len(data), _DEST | 0x80, _SRC) + data

    def _send(self, msg):
        """Send raw bytes `msg` to the device."""
        self._ser.write(msg)

    def _recv(self):
        """Receive one APT message from the device.

        Returns ``(msg_id, data)`` where `data` is `bytes` (empty for short
        messages).
        """
        hdr = self._ser.read(6)
        if len(hdr) < 6:
            raise DeviceError(
                'Thorlabs KPA101: Timeout reading message header')
        msg_id, param_or_len, byte4, byte5 = struct.unpack('<HHBB', hdr)
        if byte4 & 0x80:
            # Long message: param_or_len is the data length
            data = self._ser.read(param_or_len)
            if len(data) < param_or_len:
                raise DeviceError(
                    'Thorlabs KPA101: Timeout reading message data')
        else:
            data = b''
        return msg_id, data

    def _quad_req(self, subid):
        """Send MGMSG_QUAD_REQ_PARAMS (0x0871) for sub-message `subid`.

        Returns the data bytes from the MGMSG_QUAD_GET_PARAMS (0x0872) reply.
        """
        self._send(self._short_msg(0x0871, param1=subid))
        msg_id, data = self._recv()
        if msg_id != 0x0872:
            raise DeviceError(
                f'Thorlabs KPA101: Unexpected reply 0x{msg_id:04X} '
                f'(expected 0x0872)')
        return data

    def _quad_set(self, subid, payload):
        """Send MGMSG_QUAD_SET_PARAMS (0x0870) for sub-message `subid`.

        `payload` is the data bytes that follow the 2-byte SubMsgID word.
        """
        data = struct.pack('<H', subid) + payload
        self._send(self._long_msg(0x0870, data))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_connection(self):
        """Check whether connection to device is open."""
        if not self.device_connected:
            msg = (
                f'Thorlabs KPA101: Connection to device with serial number '
                f'{self.serial_number} not open')
            logger.error(msg)
            raise DeviceError(msg)

    def connect(self):
        """Open connection to device."""
        port, usb_serial = self._find_port()
        timeout = self.device.get('Timeout', 5.)
        try:
            self._ser = serial.Serial(
                port, baudrate=115200, rtscts=True, timeout=timeout)
        except serial.SerialException as e:
            msg = (
                f'Thorlabs KPA101: Could not open {port} for device with serial number '
                f'{self.serial_number}: {e}')
            logger.error(msg)
            raise DeviceError(msg)
        # Init sequence per Thorlabs APT protocol manual §2.1
        time.sleep(0.05)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        time.sleep(0.05)
        self._ser.setRTS(True)
        logger.info(
            'Thorlabs KPA101: Connected to device with serial number %d (USB serial %s) on %s',
            self.serial_number, usb_serial, port)
        self.device_connected = True
        self.get_readings_cached()

    def close(self):
        """Close connection to device."""
        if self.device_connected and self._ser is not None:
            self._ser.close()
            self._ser = None
            self.device_connected = False

    def get_device_info(self):
        """Request and return device information as a `DeviceInfo` namedtuple.

        Sends MGMSG_HW_REQ_INFO (0x0005) and parses the
        MGMSG_HW_GET_INFO (0x0006) response.
        """
        self.check_connection()
        self._send(self._short_msg(0x0005))
        msg_id, data = self._recv()
        if msg_id != 0x0006:
            raise DeviceError(
                f'Thorlabs KPA101: Unexpected reply 0x{msg_id:04X} '
                f'(expected 0x0006)')
        # MGMSG_HW_GET_INFO data layout (84 bytes):
        #   serial_number (I, 4), model (8s), hw_type (H, 2),
        #   fw_minor (B), fw_interim (B), fw_major (B), fw_unused (B),
        #   notes (48s), unused (12s), hw_version (H), mod_state (H),
        #   num_channels (H)
        serial_num, model_bytes, _ = struct.unpack_from('<I8sH', data, 0)
        fw_minor, fw_interim, fw_major = struct.unpack_from('<BBB', data, 14)
        hw_version, _, num_channels = struct.unpack_from('<HHH', data, 78)
        return DeviceInfo(
            serial_number=serial_num,
            model=model_bytes.rstrip(b'\x00').decode('ascii', errors='replace'),
            fw_version=f'{fw_major}.{fw_interim}.{fw_minor}',
            hw_version=hw_version,
            num_channels=num_channels,
        )

    def get_readings_cached(self):
        """Read and cache detector signals from the device.

        Sends a new request only if the cache has expired (interval set by
        `_cache_interval`).
        """
        if (self._last_reading_time is None
                or time.time() - self._last_reading_time > self._cache_interval):
            self.check_connection()
            data = self._quad_req(0x03)
            # data[0:2] = SubMsgID (word); data[2:12] = XDiff, YDiff, Sum,
            # XPos, YPos (hhHhh = signed, signed, unsigned, signed, signed)
            xdiff_r, ydiff_r, sum_r, xpos_r, ypos_r = struct.unpack(
                '<hhHhh', data[2:12])
            self._xdiff = xdiff_r * _SCALE
            self._ydiff = ydiff_r * _SCALE
            self._sum   = sum_r * _SCALE / 2  # unsigned word, half the signed range
            self._xpos  = xpos_r * _SCALE
            self._ypos  = ypos_r * _SCALE
            self._last_reading_time = time.time()

    @property
    def xdiff(self):
        """Get x-axis alignment difference signal in volts."""
        self.get_readings_cached()
        return self._xdiff

    @property
    def ydiff(self):
        """Get y-axis alignment difference signal in volts."""
        self.get_readings_cached()
        return self._ydiff

    @property
    def sum(self):
        """Get summed signal in volts."""
        self.get_readings_cached()
        return self._sum

    @property
    def xpos(self):
        """Get x position in millimeter. Only meaningful for some sensor types."""
        self.get_readings_cached()
        return self._xpos

    @property
    def ypos(self):
        """Get y position in millimeter. Only meaningful for some sensor types."""
        self.get_readings_cached()
        return self._ypos

    @property
    def xpos_pdp90a(self):
        """
        Only valid for Thorlabs PDP90A:
        x position in millimeter, calculated from x-axis alignment difference
        signal and summed signal.
        """
        self.get_readings_cached()
        return 5 * self._xdiff / self._sum

    @property
    def ypos_pdp90a(self):
        """
        Only valid for Thorlabs PDP90A:
        y position in millimeter, calculated from y-axis alignment difference
        signal and summed signal.
        """
        self.get_readings_cached()
        return 5 * self._ydiff / self._sum

    @property
    def operation_mode(self):
        """Get current operation mode as a string.

        Returns one of 'monitor', 'open_loop', 'closed_loop', or 'auto_loop'.
        """
        self.check_connection()
        data = self._quad_req(0x07)
        mode_int, = struct.unpack('<H', data[2:4])
        return _MODE_INT_TO_STR.get(mode_int, f'unknown({mode_int})')

    @operation_mode.setter
    def operation_mode(self, operation_mode):
        """Set current operation mode to `operation_mode` (str).

        Valid values: 'monitor', 'open_loop', 'closed_loop', 'auto_loop'.
        """
        if operation_mode not in _MODE_STR_TO_INT:
            raise DeviceError(
                f'Thorlabs KPA101: Unknown operation mode {operation_mode!r}; '
                f'must be one of {list(_MODE_STR_TO_INT)}')
        self.check_connection()
        mode_int = _MODE_STR_TO_INT[operation_mode]
        self._quad_set(0x07, struct.pack('<H', mode_int))
