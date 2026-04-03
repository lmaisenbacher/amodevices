"""
Driver for CAEN R14xxET/DT14xxET/R1570ET/DT1570ET HV power supplies.

Communicates via TCP/IP (Ethernet, default port 1470) or USB serial
(9600 baud, 8N1, Xon/Xoff). The ASCII command protocol is documented in
CAEN user manual UM3372 rev 20 (April 2024), pages 29-31.
"""

import socket
import threading
import logging

import serial

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

DEFAULT_PORT = 1470
DEFAULT_TIMEOUT = 5.0
DELIMITER = '\r\n'

# Thread lock to avoid concurrent access to the shared TCP/serial socket
# from different threads at the same time.
# All callers of `_query()` are protected automatically.
comm_lock = threading.Lock()

# Channel status bits (p. 30 of UM3372)
_STATUS_BITS = {
    0: 'ON',
    1: 'RUP',
    2: 'RDW',
    3: 'OVC',
    4: 'OVV',
    5: 'UNV',
    6: 'MAXV',
    7: 'TRIP',
    8: 'OVP',
    9: 'OVT',
    10: 'DIS',
    11: 'KILL',
    12: 'ILK',
    13: 'NOCAL',
}

# Board alarm bits (p. 31 of UM3372)
_ALARM_BITS = {
    0: 'CH0',
    1: 'CH1',
    2: 'CH2',
    3: 'CH3',
    4: 'PWFAIL',
    5: 'OVP',
    6: 'HVCKFAIL',
}


def _decode_bits(value, bit_map):
    """Return list of flag names that are set in integer `value`."""
    return [name for bit, name in sorted(bit_map.items()) if value & (1 << bit)]


class CAENDT1470ET(dev_generic.Device):
    """Driver for CAEN R/DT14xxET and R/DT1570ET HV power supplies.

    Communicates via TCP/IP (Ethernet) or USB serial using the CAEN ASCII
    command protocol.
    """

    def __init__(self, device):
        """Initialize driver for device configuration dict `device`.

        Required keys:
            'Device' (str): human-readable name
            'Address' (str): IP address (Ethernet) or COM port (USB)

        Optional keys:
            'Port' (int): TCP port (default: 1470, Ethernet only)
            'Timeout' (float): timeout in seconds (default: 5.0)
            'ConnectionType' (str): 'ethernet' or 'usb' (default: 'ethernet')
            'BoardAddress' (int): module address 0-31 (default: 0)
            'MaxVoltage' (float): software-enforced voltage cap in V; if set,
                `set_vset` raises `DeviceError` when the requested voltage
                exceeds this value
        """
        super().__init__(device)
        self._conn = None
        self._conn_type = device.get('ConnectionType', 'ethernet').lower()
        self._bd = device.get('BoardAddress', 0)
        self._max_voltage = device.get('MaxVoltage')
        self._num_ch = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        """Open connection to the power supply."""
        if self._conn_type == 'ethernet':
            self._connect_ethernet()
        elif self._conn_type == 'usb':
            self._connect_usb()
        else:
            raise DeviceError(
                f'{self.device["Device"]}: Unknown ConnectionType '
                f'{self._conn_type!r}; must be "ethernet" or "usb"')
        self.device_connected = True
        self._num_ch = int(self._mon_board('BDNCH'))
        logger.info('%s: Connected via %s to %s (%d channels)',
                    self.device['Device'], self._conn_type,
                    self.device['Address'], self._num_ch)

    def _connect_ethernet(self):
        """Open TCP/IP connection."""
        host = self.device['Address']
        port = self.device.get('Port', DEFAULT_PORT)
        timeout = self.device.get('Timeout', DEFAULT_TIMEOUT)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except (socket.timeout, OSError) as err:
            sock.close()
            raise DeviceError(
                f'{self.device["Device"]}: Failed to connect to '
                f'{host}:{port}: {err}')
        self._conn = sock

    def _connect_usb(self):
        """Open USB serial connection."""
        port = self.device['Address']
        timeout = self.device.get('Timeout', DEFAULT_TIMEOUT)
        try:
            self._conn = serial.Serial(
                port, baudrate=9600, bytesize=8, parity='N', stopbits=1,
                xonxoff=True, timeout=timeout)
        except serial.SerialException as err:
            raise DeviceError(
                f'{self.device["Device"]}: Failed to open {port}: {err}')

    def close(self):
        """Close connection to the power supply."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self.device_connected = False
        logger.info('%s: Connection closed', self.device['Device'])

    # ------------------------------------------------------------------
    # Low-level communication
    # ------------------------------------------------------------------

    def _send(self, command):
        """Send `command` string (CR/LF appended automatically)."""
        logger.debug('%s TX: %s', self.device['Device'], command)
        data = (command + DELIMITER).encode('ASCII')
        try:
            if self._conn_type == 'ethernet':
                self._conn.sendall(data)
            else:
                self._conn.write(data)
        except (OSError, serial.SerialException) as err:
            raise DeviceError(
                f'{self.device["Device"]}: Failed to send command '
                f'"{command}": {err}')

    def _recv(self):
        """Receive one response line (CR/LF stripped)."""
        try:
            if self._conn_type == 'ethernet':
                buf = b''
                delim = DELIMITER.encode('ASCII')
                while not buf.endswith(delim):
                    chunk = self._conn.recv(4096)
                    if not chunk:
                        raise DeviceError(
                            f'{self.device["Device"]}: Connection closed '
                            f'by remote host')
                    buf += chunk
                response = buf.decode('ASCII').rstrip('\r\n')
            else:
                response = self._conn.readline().decode('ASCII').rstrip('\r\n')
                if not response:
                    raise DeviceError(
                        f'{self.device["Device"]}: Timeout waiting for '
                        f'response')
        except socket.timeout:
            raise DeviceError(
                f'{self.device["Device"]}: Timeout waiting for response')
        logger.debug('%s RX: %s', self.device['Device'], response)
        return response

    def _query(self, command):
        """Send `command` and return the response string."""
        with comm_lock:
            self._send(command)
            return self._recv()

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    def _build_mon_cmd(self, par, channel=None):
        """Build a MON command string."""
        cmd = f'$BD:{self._bd:02d},CMD:MON'
        if channel is not None:
            cmd += f',CH:{channel:d}'
        cmd += f',PAR:{par}'
        return cmd

    def _build_set_cmd(self, par, channel=None, val=None):
        """Build a SET command string."""
        cmd = f'$BD:{self._bd:02d},CMD:SET'
        if channel is not None:
            cmd += f',CH:{channel:d}'
        cmd += f',PAR:{par}'
        if val is not None:
            cmd += f',VAL:{val}'
        return cmd

    def _parse_response(self, response):
        """Check `response` for errors; raise `DeviceError` on ERR responses.

        Returns `response` unchanged on success.
        """
        if ',CMD:ERR' in response:
            raise DeviceError(
                f'{self.device["Device"]}: Command error: {response}')
        if ',CH:ERR' in response:
            raise DeviceError(
                f'{self.device["Device"]}: Channel error: {response}')
        if ',PAR:ERR' in response:
            raise DeviceError(
                f'{self.device["Device"]}: Parameter error: {response}')
        if ',VAL:ERR' in response:
            raise DeviceError(
                f'{self.device["Device"]}: Value error: {response}')
        if ',LOC:ERR' in response:
            raise DeviceError(
                f'{self.device["Device"]}: Module is in LOCAL mode: '
                f'{response}')
        return response

    def _parse_value(self, response):
        """Extract the VAL field from a response string.

        Returns the value as a string.
        """
        self._parse_response(response)
        # Response format: #BD:xx,CMD:OK,VAL:value
        for field in response.split(','):
            if field.startswith('VAL:'):
                return field[4:]
        raise DeviceError(
            f'{self.device["Device"]}: No VAL field in response: {response}')

    def _parse_values(self, response):
        """Extract semicolon-separated VAL fields from a multi-channel response.

        Returns a list of value strings, one per channel.
        """
        return self._parse_value(response).split(';')

    def _mon_channel(self, par, channel):
        """Send a MON command for `par` on `channel` and return the value string."""
        response = self._query(self._build_mon_cmd(par, channel=channel))
        return self._parse_value(response)

    def _mon_channel_float(self, par, channel):
        """Send a MON command for `par` on `channel` and return a float."""
        return float(self._mon_channel(par, channel))

    def _mon_board(self, par):
        """Send a board-level MON command for `par` and return the value string."""
        response = self._query(self._build_mon_cmd(par))
        return self._parse_value(response)

    def _set_channel(self, par, channel, val=None):
        """Send a SET command for `par` on `channel`."""
        response = self._query(self._build_set_cmd(par, channel=channel,
                                                    val=val))
        self._parse_response(response)

    def _set_board(self, par, val=None):
        """Send a board-level SET command for `par`."""
        response = self._query(self._build_set_cmd(par, val=val))
        self._parse_response(response)

    def _check_channel_active(self, channel):
        """Raise `DeviceError` if `channel` is killed or disabled."""
        status = self.get_status(channel)
        flags = _decode_bits(status, _STATUS_BITS)
        blocked = [f for f in flags if f in ('KILL', 'DIS')]
        if blocked:
            raise DeviceError(
                f'{self.device["Device"]}: Channel {channel} is '
                f'{"/".join(blocked)}; command ignored by hardware')

    def _check_all_channels_active(self):
        """Raise `DeviceError` if any channel is killed or disabled."""
        for ch in range(self._num_ch):
            self._check_channel_active(ch)

    def _mon_all_channels(self, par):
        """Send a MON command for `par` on all channels; return list of value strings."""
        response = self._query(self._build_mon_cmd(par, channel=self._num_ch))
        return self._parse_values(response)

    def _mon_all_channels_float(self, par):
        """Send a MON command for `par` on all channels; return list of floats."""
        return [float(v) for v in self._mon_all_channels(par)]

    def _set_all_channels(self, par, val=None):
        """Send a SET command for `par` on all channels at once."""
        response = self._query(
            self._build_set_cmd(par, channel=self._num_ch, val=val))
        self._parse_response(response)

    # ------------------------------------------------------------------
    # Channel monitoring
    # ------------------------------------------------------------------

    def get_vmon(self, channel):
        """Return monitored output voltage of `channel` in V."""
        return self._mon_channel_float('VMON', channel)

    def get_imon(self, channel):
        """Return monitored output current of `channel` in uA."""
        return self._mon_channel_float('IMON', channel)

    def get_vset(self, channel):
        """Return voltage set point of `channel` in V."""
        return self._mon_channel_float('VSET', channel)

    def get_iset(self, channel):
        """Return current limit set point of `channel` in uA."""
        return self._mon_channel_float('ISET', channel)

    def get_max_voltage(self, channel):
        """Return MAXV protection limit of `channel` in V."""
        return self._mon_channel_float('MAXV', channel)

    def get_ramp_up(self, channel):
        """Return ramp-up rate of `channel` in V/s."""
        return self._mon_channel_float('RUP', channel)

    def get_ramp_down(self, channel):
        """Return ramp-down rate of `channel` in V/s."""
        return self._mon_channel_float('RDW', channel)

    def get_trip(self, channel):
        """Return trip time of `channel` in seconds (1000 = infinite)."""
        return self._mon_channel_float('TRIP', channel)

    def get_status(self, channel):
        """Return raw status bit field of `channel` as an integer."""
        return int(self._mon_channel('STAT', channel))

    def get_status_str(self, channel):
        """Return list of active status flag names of `channel`."""
        return _decode_bits(self.get_status(channel), _STATUS_BITS)

    def get_polarity(self, channel):
        """Return polarity of `channel` as '+' or '-'."""
        return self._mon_channel('POL', channel)

    def get_power_down(self, channel):
        """Return power-down mode of `channel` ('RAMP' or 'KILL')."""
        return self._mon_channel('PDWN', channel)

    def get_imon_range(self, channel):
        """Return current monitor range of `channel` ('HIGH' or 'LOW')."""
        return self._mon_channel('IMRANGE', channel)

    # ------------------------------------------------------------------
    # All-channels monitoring
    # ------------------------------------------------------------------

    def get_vmon_all(self):
        """Return monitored output voltages of all channels in V."""
        return self._mon_all_channels_float('VMON')

    def get_imon_all(self):
        """Return monitored output currents of all channels in uA."""
        return self._mon_all_channels_float('IMON')

    def get_vset_all(self):
        """Return voltage set points of all channels in V."""
        return self._mon_all_channels_float('VSET')

    def get_iset_all(self):
        """Return current limit set points of all channels in uA."""
        return self._mon_all_channels_float('ISET')

    def get_status_all(self):
        """Return raw status bit fields of all channels as a list of integers."""
        return [int(v) for v in self._mon_all_channels('STAT')]

    def get_status_str_all(self):
        """Return active status flag names of all channels as a list of lists."""
        return [_decode_bits(s, _STATUS_BITS) for s in self.get_status_all()]

    # ------------------------------------------------------------------
    # Channel setting
    # ------------------------------------------------------------------

    def set_vset(self, channel, voltage):
        """Set output voltage of `channel` to `voltage` (V).

        Raises `DeviceError` if the channel is killed or disabled, or if
        'MaxVoltage' is configured in the device dict and `voltage` exceeds it.
        """
        self._check_channel_active(channel)
        if self._max_voltage is not None and voltage > self._max_voltage:
            raise DeviceError(
                f'{self.device["Device"]}: Requested voltage {voltage} V '
                f'exceeds software limit MaxVoltage={self._max_voltage} V')
        self._set_channel('VSET', channel, val=voltage)

    def set_iset(self, channel, current):
        """Set current limit of `channel` to `current` (uA)."""
        self._set_channel('ISET', channel, val=current)

    def set_on(self, channel):
        """Turn `channel` on.

        Raises `DeviceError` if the channel is killed or disabled.
        """
        self._check_channel_active(channel)
        self._set_channel('ON', channel)

    def set_off(self, channel):
        """Turn `channel` off."""
        self._set_channel('OFF', channel)

    def set_ramp_up(self, channel, rate):
        """Set ramp-up rate of `channel` to `rate` (V/s)."""
        self._set_channel('RUP', channel, val=rate)

    def set_ramp_down(self, channel, rate):
        """Set ramp-down rate of `channel` to `rate` (V/s)."""
        self._set_channel('RDW', channel, val=rate)

    def set_trip(self, channel, time):
        """Set trip time of `channel` to `time` (s). 1000 = infinite."""
        self._set_channel('TRIP', channel, val=time)

    def set_power_down(self, channel, mode):
        """Set power-down mode of `channel` to `mode` ('RAMP' or 'KILL')."""
        self._set_channel('PDWN', channel, val=mode)

    def set_imon_range(self, channel, imon_range):
        """Set current monitor range of `channel` ('HIGH' or 'LOW')."""
        self._set_channel('IMRANGE', channel, val=imon_range)

    def set_max_voltage(self, channel, voltage):
        """Set MAXV hardware protection limit of `channel` to `voltage` (V)."""
        self._set_channel('MAXV', channel, val=voltage)

    # ------------------------------------------------------------------
    # All-channels setting
    # ------------------------------------------------------------------

    def set_vset_all(self, voltage):
        """Set output voltage of all channels to `voltage` (V).

        Raises `DeviceError` if any channel is killed or disabled, or if
        'MaxVoltage' is configured and `voltage` exceeds it.
        """
        self._check_all_channels_active()
        if self._max_voltage is not None and voltage > self._max_voltage:
            raise DeviceError(
                f'{self.device["Device"]}: Requested voltage {voltage} V '
                f'exceeds software limit MaxVoltage={self._max_voltage} V')
        self._set_all_channels('VSET', val=voltage)

    def set_on_all(self):
        """Turn all channels on.

        Raises `DeviceError` if any channel is killed or disabled.
        """
        self._check_all_channels_active()
        self._set_all_channels('ON')

    def set_off_all(self):
        """Turn all channels off."""
        self._set_all_channels('OFF')

    def set_ramp_up_all(self, rate):
        """Set ramp-up rate of all channels to `rate` (V/s)."""
        self._set_all_channels('RUP', val=rate)

    def set_ramp_down_all(self, rate):
        """Set ramp-down rate of all channels to `rate` (V/s)."""
        self._set_all_channels('RDW', val=rate)

    # ------------------------------------------------------------------
    # Board monitoring
    # ------------------------------------------------------------------

    def get_board_name(self):
        """Return module name string."""
        return self._mon_board('BDNAME')

    def get_num_channels(self):
        """Return number of channels."""
        return self._num_ch

    def get_firmware_release(self):
        """Return firmware release string."""
        return self._mon_board('BDFREL')

    def get_serial_number(self):
        """Return module serial number."""
        return int(self._mon_board('BDSNUM'))

    def get_interlock_status(self):
        """Return interlock status ('YES' or 'NO')."""
        return self._mon_board('BDILK')

    def get_interlock_mode(self):
        """Return interlock mode ('OPEN' or 'CLOSED')."""
        return self._mon_board('BDILKM')

    def get_control_mode(self):
        """Return control mode ('LOCAL' or 'REMOTE')."""
        return self._mon_board('BDCTR')

    def get_board_alarm(self):
        """Return raw board alarm bit field as an integer."""
        return int(self._mon_board('BDALARM'))

    def get_board_alarm_str(self):
        """Return list of active board alarm flag names."""
        return _decode_bits(self.get_board_alarm(), _ALARM_BITS)

    # ------------------------------------------------------------------
    # Board setting
    # ------------------------------------------------------------------

    def set_interlock_mode(self, mode):
        """Set interlock mode to `mode` ('OPEN' or 'CLOSED')."""
        self._set_board('BDILKM', val=mode)

    def clear_alarm(self):
        """Clear the board alarm signal."""
        self._set_board('BDCLR')
