"""
Driver for the LIOP-TEC LiopStar-E dye laser, controlled via TCP/IP through
the "LiopStar Control" software.

Protocol reference: LIOPTEC - TCP-IP remote communication protocol
Default port: 65510
"""

import re
import socket
import logging
import time

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)


class LioptecLiopStar(dev_generic.Device):
    """Driver for the LIOP-TEC LiopStar-E dye laser.

    Communicates with the LiopStar Control software via a TCP/IP text protocol.
    Commands and responses are ASCII strings terminated by CR/LF (\\r\\n).

    Connection sequence:
        dev.connect()          # open TCP socket
        dev.remote_connect()   # acquire remote control (required for move commands)
        ...
        dev.remote_disconnect()
        dev.close()

    'Get*' commands do not require a remote connection; all other commands do.
    """

    DEFAULT_PORT = 65510
    DEFAULT_TIMEOUT = 5.0
    DELIMITER = '\r\n'
    SETTLE_POLL_INTERVAL = 0.05  # seconds between position reads in wait_for_motor_settle
    SETTLE_COUNT = 5             # consecutive identical readings to declare motor settled

    def __init__(self, device):
        """Initialize driver for device configuration dict `device`.

        Required keys:
            'Device' (str): human-readable name
            'Address' (str): hostname or IP address of the LiopStar Control PC

        Optional keys:
            'Port' (int): TCP port (default: 65510)
            'Timeout' (float): socket timeout in seconds (default: 5.0)
        """
        super().__init__(device)
        self._socket = None
        self._remote_connected = False
        self.last_wavelength_nm = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        """Open TCP/IP connection to the LiopStar Control software."""
        host = self.device['Address']
        port = self.device.get('Port', self.DEFAULT_PORT)
        timeout = self.device.get('Timeout', self.DEFAULT_TIMEOUT)

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(timeout)
        try:
            self._socket.connect((host, port))
        except (socket.timeout, OSError) as err:
            self._socket = None
            raise DeviceError(
                f'{self.device["Device"]}: Failed to connect to {host}:{port}. Error: {err}')
        logger.info('%s: Connected to %s:%d', self.device['Device'], host, port)
        self.device_connected = True

    def close(self):
        """Close the TCP/IP connection, issuing RemoteDisconnect first if needed."""
        if self._remote_connected:
            try:
                self.remote_disconnect()
            except DeviceError:
                pass
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self.device_connected = False
        logger.info('%s: Connection closed', self.device['Device'])

    def __del__(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    # ------------------------------------------------------------------
    # Low-level communication
    # ------------------------------------------------------------------

    def _send(self, command):
        """Send `command` string (CR/LF appended automatically)."""
        logger.debug('%s TX: %s', self.device['Device'], command)
        try:
            self._socket.sendall((command + self.DELIMITER).encode('ASCII'))
        except (OSError, socket.timeout) as err:
            raise DeviceError(
                f'{self.device["Device"]}: Failed to send command "{command}". Error: {err}')

    def _recv(self):
        """Receive response string (CR/LF delimiter stripped)."""
        buf = b''
        delim = self.DELIMITER.encode('ASCII')
        try:
            while not buf.endswith(delim):
                chunk = self._socket.recv(4096)
                if not chunk:
                    raise DeviceError(
                        f'{self.device["Device"]}: Connection closed by remote host')
                buf += chunk
        except socket.timeout as err:
            raise DeviceError(
                f'{self.device["Device"]}: Timeout waiting for response. Error: {err}')
        response = buf.decode('ASCII').rstrip('\r\n')
        logger.debug('%s RX: %s', self.device['Device'], response)
        return response

    def _query(self, command):
        """Send `command` and return the response string."""
        self._send(command)
        return self._recv()

    def _parse_response(self, response, allow_warning=False):
        """Return `response`; raise DeviceError if it starts with 'ERROR:'.

        WARNING responses are logged and returned (not raised) unless
        `allow_warning` is False, in which case they are also logged but still
        returned — warnings indicate non-critical issues per the protocol spec.
        """
        upper = response.upper()
        if upper.startswith('ERROR:'):
            raise DeviceError(f'{self.device["Device"]}: {response}')
        if upper.startswith('WARNING:'):
            logger.warning('%s: %s', self.device['Device'], response)
        return response

    # ------------------------------------------------------------------
    # Protocol commands
    # ------------------------------------------------------------------

    def get_remote_status(self):
        """Query how many clients are connected and whether a remote connection exists.

        Does not require remote access.

        :returns: raw response string from the control software
        """
        response = self._query('GetRemoteStatus')
        return self._parse_response(response)

    def remote_connect(self):
        """Request remote control of the LiopStar Control software.

        Only one remote client is allowed at a time.

        :returns: raw response string from the control software
        :raises DeviceError: if remote access is denied or another client is connected
        """
        response = self._query('RemoteConnect')
        self._parse_response(response)
        self._remote_connected = True
        logger.info('%s: Remote connection established', self.device['Device'])
        return response

    def remote_disconnect(self):
        """Release the remote control connection.

        :returns: raw response string from the control software
        """
        response = self._query('RemoteDisconnect')
        self._parse_response(response)
        self._remote_connected = False
        logger.info('%s: Remote connection released', self.device['Device'])
        return response

    def exit_software(self):
        """Close the LiopStar Control software and stop all drives.

        Use with caution — this terminates the control software on the laser PC.

        :returns: raw response string from the control software
        """
        response = self._query('Exit')
        return self._parse_response(response)

    def stop_drives(self):
        """Immediately halt all drives and abort any running scan.

        :returns: raw response string from the control software
        """
        response = self._query('StopDrives')
        return self._parse_response(response)

    def move_home(self):
        """Move all drives to their home (zero) position.

        This command returns immediately; use :meth:`wait_for_ready` to block
        until the move completes.

        :returns: raw response string from the control software
        """
        response = self._query('MoveHome')
        return self._parse_response(response)

    def set_wavelength(self, wavelength_nm):
        """Request the laser to tune to `wavelength_nm` [nm].

        This command returns immediately; use :meth:`set_wavelength_and_wait`
        to block until the move completes.

        :wavelength_nm: target wavelength in nm (decimal point separator)
        :returns: raw response string from the control software
        """
        response = self._query(f'SetWavelength {wavelength_nm:.4f}')
        self._parse_response(response)
        self.last_wavelength_nm = wavelength_nm
        return response

    def set_scan_table(self, n_shots, wavelengths):
        """Upload scan parameters (does not start the scan).

        Use :meth:`start_scan` afterwards to execute the scan.

        :n_shots: number of shots per wavelength position
        :wavelengths: list of wavelengths in nm (at least one value; max 9999 positions)
        :returns: raw response string from the control software
        """
        wl_str = ' '.join(f'{w:.4f}' for w in wavelengths)
        response = self._query(f'SetScanTable {n_shots:d} {wl_str}')
        return self._parse_response(response)

    def start_scan_param(self, n_shots, start_nm, stop_nm, increment_nm):
        """Upload scan parameters and immediately start a triggered scan.

        :n_shots: number of shots per position
        :start_nm: start wavelength in nm
        :stop_nm: stop wavelength in nm
        :increment_nm: wavelength step size in nm
        :returns: raw response string from the control software
        """
        response = self._query(
            f'StartScanParam {n_shots:d} {start_nm:.4f} {stop_nm:.4f} {increment_nm:.4f}')
        return self._parse_response(response)

    def start_scan(self):
        """Start the scan previously configured with :meth:`set_scan_table`.

        :returns: raw response string from the control software
        """
        response = self._query('StartScan')
        return self._parse_response(response)

    def stop_scan(self):
        """Stop any active scan.

        :returns: raw response string from the control software
        """
        response = self._query('StopScan')
        return self._parse_response(response)

    def get_status(self):
        """Return the current system status as a prefix string.

        Does not require remote access.

        :returns: one of 'OK', 'ERROR', 'CALIB', 'HOME', 'MOVING', 'SCAN'
        """
        response = self._query('GetStatus')
        prefix = response.split(':')[0].strip().upper()
        return prefix

    def get_actual_position(self):
        """Return the current drive positions in steps.

        Does not require remote access.

        :returns: dict with keys 'Resonator', 'FCU1', 'FCU2' mapping to int step counts.
                  Keys may be absent if the response format is unexpected.
        """
        response = self._query('GetActualPosition')
        self._parse_response(response)
        positions = {}
        for line in re.split(r'[\r\n]+', response):
            m = re.match(r'(Resonator|FCU\d+):\s*(-?\d+)', line, re.IGNORECASE)
            if m:
                positions[m.group(1)] = int(m.group(2))
        return positions

    def get_error(self):
        """Check for errors that occurred during positioning.

        Does not require remote access.

        :returns: raw response string from the control software
        """
        response = self._query('GetError')
        return response

    def acknowledge_error(self):
        """Acknowledge any present errors in the control system.

        :returns: raw response string from the control software
        """
        response = self._query('AcknowledgeError')
        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def wait_for_ready(self, timeout=30., poll_interval=0.2):
        """Block until the system status is 'OK' (idle).

        Polls :meth:`get_status` every `poll_interval` seconds. Raises
        :class:`DeviceError` if status is 'ERROR' or `timeout` is exceeded.

        :timeout: maximum wait time in seconds (default: 30)
        :poll_interval: polling interval in seconds (default: 0.2)
        """
        deadline = time.monotonic() + timeout
        while True:
            status = self.get_status()
            if status == 'OK':
                return
            if status == 'ERROR':
                raise DeviceError(
                    f'{self.device["Device"]}: System reported an error while waiting for ready')
            if time.monotonic() > deadline:
                raise DeviceError(
                    f'{self.device["Device"]}: Timed out after {timeout:.0f} s '
                    f'waiting for ready (last status: {status})')
            time.sleep(poll_interval)

    def wait_for_motor_settle(self, timeout=30., poll_interval=None, settle_count=None):
        """Block until all motor positions have stopped changing.

        Each poll cycle reads :meth:`get_actual_position` and :meth:`get_status`.
        Raises :class:`DeviceError` if status is 'ERROR' or `timeout` is exceeded.
        Returns the final positions dict once all motors have reported the same
        position for `settle_count` consecutive reads.

        :timeout: maximum wait time in seconds (default: 30)
        :poll_interval: seconds between position reads (default: 0.05)
        :settle_count: consecutive identical readings required (default: 5)
        :returns: final positions dict {motor_name: step_count}
        """
        if poll_interval is None:
            poll_interval = self.SETTLE_POLL_INTERVAL
        if settle_count is None:
            settle_count = self.SETTLE_COUNT
        motors = None
        stable_counts = {}
        confirmed = set()
        prev_pos = {}
        deadline = time.monotonic() + timeout

        while True:
            status = self.get_status()
            if status == 'ERROR':
                raise DeviceError(
                    f'{self.device["Device"]}: System reported an error while waiting for motor')

            pos = self.get_actual_position()

            if motors is None:
                motors = list(pos.keys())
                stable_counts = {m: 0 for m in motors}

            for m in motors:
                if m in confirmed:
                    continue
                if pos.get(m) == prev_pos.get(m):
                    stable_counts[m] += 1
                    if stable_counts[m] >= settle_count:
                        confirmed.add(m)
                else:
                    stable_counts[m] = 0

            prev_pos = dict(pos)

            if motors is not None and confirmed == set(motors):
                return pos

            if time.monotonic() > deadline:
                raise DeviceError(
                    f'{self.device["Device"]}: Timed out after {timeout:.0f} s '
                    f'waiting for motors to settle')

            time.sleep(poll_interval)

    def set_wavelength_and_wait(self, wavelength_nm, timeout=30.):
        """Tune to `wavelength_nm` [nm] and block until all motors have settled.

        :wavelength_nm: target wavelength in nm
        :timeout: maximum wait time in seconds (default: 30)
        :returns: raw response from SetWavelength command
        """
        response = self.set_wavelength(wavelength_nm)
        self.wait_for_motor_settle(timeout=timeout)
        return response
