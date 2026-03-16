"""
Driver for the LIOP-TEC LiopStar-E dye laser, controlled via TCP/IP through
the "LiopStar Control" software.

Protocol reference: LIOPTEC - TCP-IP remote communication protocol (2012)
Tested with LiopStar Control v4.
Default port: 65510
"""

import math
import re
import socket
import logging
import time
import xml.etree.ElementTree as ET

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

_LVDATA_NS = 'http://www.ni.com/LVData'

# ----------------------------------------------------------------------
# LabVIEW XML helpers and calibration loader
#
# LiopStar-E ships a LabVIEW XML file containing grating and drive
# parameters for the specific dye installed (grating density, orders,
# angles, lever length, screw pitch, etc.). These are used to convert
# between wavelength and resonator motor step counts.
#
# Pass the XML path as 'GratingParamsXML' in the device dict, or
# call `load_grating_params_from_xml()` directly and pass the result
# as 'GratingParams'.
# ----------------------------------------------------------------------


def _lv_cluster_vals(cluster_elem):
    """Return a dict of {name: val_text} for all direct typed children of a
    LabVIEW Cluster element (handles DBL, I32, U32, Boolean, String tags)."""
    ns = _LVDATA_NS
    result = {}
    for child in cluster_elem:
        name_elem = child.find(f'{{{ns}}}Name')
        val_elem  = child.find(f'{{{ns}}}Val')
        if name_elem is not None and val_elem is not None:
            result[name_elem.text] = val_elem.text
    return result


def _find_cluster(parent, name):
    """Return the first Cluster child whose Name equals `name`."""
    ns = _LVDATA_NS
    for cluster in parent.iter(f'{{{ns}}}Cluster'):
        name_elem = cluster.find(f'{{{ns}}}Name')
        if name_elem is not None and name_elem.text == name:
            return cluster
    return None


def load_grating_params_from_xml(xml_path):
    """Load GratingParams from a LiopStar calibration XML file.

    Parses the LabVIEW XML configuration file shipped with each LiopStar-E
    laser and returns a dict suitable for use as `device['GratingParams']`.

    All angles are stored in radians in the XML. Grating densities are
    stored in lines/m in the XML; they are converted to lines/mm here.

    :xml_path: path to the LiopStar calibration XML file
    :returns: `GratingParams` dict with keys d, m, theta, d_prime, m_prime,
              L, x0, phi0, p, n
    :raises ValueError: if required fields are missing or the file cannot be
                        parsed
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    res_cluster = _find_cluster(root, 'ResonatorConfiguration')
    if res_cluster is None:
        raise ValueError(
            f'Could not find ResonatorConfiguration in {xml_path}')

    vals = _lv_cluster_vals(res_cluster)

    required = ['Litrow', 'Littrow Order', 'Grazing', 'Grazing order',
                'Grazing Angle', 'Linear Offset', 'Angle Offset',
                'Screw Pitch', 'Lever Length',
                'Steps per turn', 'Microsteps per Step']
    missing = [k for k in required if k not in vals]
    if missing:
        raise ValueError(
            f'Missing fields in ResonatorConfiguration: {missing}')

    return {
        'd':       float(vals['Grazing']) / 1000,     # lines/m → lines/mm
        'm':       int(vals['Grazing order']),
        'theta':   float(vals['Grazing Angle']),       # radians
        'd_prime': float(vals['Litrow']) / 1000,       # lines/m → lines/mm
        'm_prime': int(vals['Littrow Order']),
        'L':       float(vals['Lever Length']),        # mm
        'x0':      float(vals['Linear Offset']),       # mm
        'phi0':    float(vals['Angle Offset']),        # radians
        'p':       float(vals['Screw Pitch']),         # mm/turn
        'n':       int(float(vals['Steps per turn']) *
                       float(vals['Microsteps per Step'])),
    }


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

    ``Get*`` commands do not require a remote connection; all other commands do.
    """

    DEFAULT_PORT = 65510
    DEFAULT_TIMEOUT = 5.0
    DELIMITER = '\r\n'
    MOVE_POLL_INTERVAL = 0.020   # seconds between status polls in wait_for_move_complete
    MOVE_START_TIMEOUT = 0.200   # seconds to wait for status to leave 'OK' after a move command

    def __init__(self, device, raise_on_warning=False):
        """Initialize driver for device configuration dict `device`.

        Required keys:
            'Device' (str): human-readable name
            'Address' (str): hostname or IP address of the LiopStar Control PC

        Optional keys:
            'Port' (int): TCP port (default: 65510)
            'Timeout' (float): socket timeout in seconds (default: 5.0)
            'GratingParamsXML' (str or Path): path to a LiopStar calibration
                XML file; loaded automatically and stored as 'GratingParams'
            'GratingParams' (dict): pre-loaded grating parameters (see
                :func:`load_grating_params_from_xml`)

        :param raise_on_warning: if `True`, protocol WARNING responses raise
            :class:`DeviceError` in addition to being logged. Can also be
            changed on the instance after construction. Default is `False`.
        """
        super().__init__(device)
        self._socket = None
        self._remote_connected = False
        self.last_wavelength_nm = None
        self.raise_on_warning = raise_on_warning
        if 'GratingParamsXML' in device:
            self.device['GratingParams'] = load_grating_params_from_xml(
                device['GratingParamsXML'])
            logger.info('%s: Loaded grating parameters from "%s"',
                        self.device['Device'], device['GratingParamsXML'])

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

    def _parse_response(self, response):
        """Return `response`; raise :class:`DeviceError` on error or warning responses.

        'ERROR:' responses are always logged and always raise
        :class:`DeviceError`. 'WARNING:' responses are always logged; if
        `raise_on_warning` is `True` on this instance, :class:`DeviceError`
        is raised as well.
        """
        response = response.strip()
        upper = response.upper()
        if upper.startswith('ERROR:'):
            logger.error('%s: %s', self.device['Device'], response)
            raise DeviceError(f'{self.device["Device"]}: {response}')
        if upper.startswith('WARNING:'):
            logger.warning('%s: %s', self.device['Device'], response)
            if self.raise_on_warning:
                raise DeviceError(f'{self.device["Device"]}: {response}')
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

        The wavelength is sent to the hardware rounded to 4 decimal places
        (0.0001 nm resolution), which is the wire format used by the LiopStar
        Control software.

        This command returns immediately; use :meth:`set_wavelength_and_wait`
        to block until the move completes.

        :wavelength_nm: target wavelength in nm
        :returns: raw response string from the control software
        """
        wavelength_nm = round(wavelength_nm, 4)
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

        .. note::
            After a move command, `GetStatus` may continue returning 'OK'
            for up to ~60 ms before transitioning to 'MOVING'. Calling
            this method immediately after a move command may therefore return
            prematurely before the move has started. Use
            :meth:`wait_for_move_complete` for move completion detection.

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

    def wait_for_move_complete(self, timeout=30., poll_interval=None,
                               start_timeout=None, target_wavelength_nm=None):
        """Block until a move command has completed.

        Two detection strategies are used depending on whether calibration
        is available:

        **With calibration** (`target_wavelength_nm` is given and
        'GratingParams' is set): polls `GetActualPosition` and
        `GetStatus` each iteration; returns when the resonator has reached
        the target step count *and* status is 'OK' (confirming FCU1/FCU2
        have also finished). Handles all move sizes including tiny moves where
        the status never transitions to 'MOVING'.

        **Without calibration**: status-based two-phase polling.
        Phase 1 — polls until status leaves 'OK' (move started), up to
        `start_timeout` seconds; returns immediately if it never does
        (already at target). Phase 2 — polls until status returns to 'OK'.

        Raises :class:`DeviceError` if status is 'ERROR' or `timeout`
        is exceeded.

        :param timeout: maximum total wait time in seconds (default: 30)
        :param poll_interval: seconds between polls
            (default: `MOVE_POLL_INTERVAL`)
        :param start_timeout: (no-calibration path only) seconds to wait for
            the move to start before assuming already at target
            (default: `MOVE_START_TIMEOUT`)
        :param target_wavelength_nm: target wavelength in nm; enables the
            calibration-based completion path when 'GratingParams' is set
        :returns: final positions dict `{motor_name: step_count}`
        """
        if poll_interval is None:
            poll_interval = self.MOVE_POLL_INTERVAL
        if start_timeout is None:
            start_timeout = self.MOVE_START_TIMEOUT

        # --- calibration-based path ---
        if target_wavelength_nm is not None and 'GratingParams' in self.device:
            target_steps = self._wavelength_to_resonator_steps(target_wavelength_nm)
            deadline = time.monotonic() + timeout
            while True:
                status = self.get_status()
                if status == 'ERROR':
                    raise DeviceError(
                        f'{self.device["Device"]}: System reported an error '
                        f'while waiting for move to complete')
                pos = self.get_actual_position()
                if pos.get('Resonator') == target_steps and status == 'OK':
                    return pos
                if time.monotonic() > deadline:
                    raise DeviceError(
                        f'{self.device["Device"]}: Timed out after {timeout:.0f} s '
                        f'waiting for move to complete (last status: {status})')
                time.sleep(poll_interval)

        # --- status-based path (no calibration) ---
        deadline       = time.monotonic() + timeout
        start_deadline = time.monotonic() + start_timeout

        # Phase 1: wait for move to start
        while True:
            status = self.get_status()
            if status == 'ERROR':
                raise DeviceError(
                    f'{self.device["Device"]}: System reported an error '
                    f'while waiting for move to start')
            if status != 'OK':
                break
            if time.monotonic() > start_deadline:
                # Status never left OK — already at target
                return self.get_actual_position()
            time.sleep(poll_interval)

        # Phase 2: wait for move to finish
        while True:
            status = self.get_status()
            if status == 'ERROR':
                raise DeviceError(
                    f'{self.device["Device"]}: System reported an error '
                    f'while waiting for move to complete')
            if status == 'OK':
                return self.get_actual_position()
            if time.monotonic() > deadline:
                raise DeviceError(
                    f'{self.device["Device"]}: Timed out after {timeout:.0f} s '
                    f'waiting for move to complete (last status: {status})')
            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Motor-step to wavelength conversion (resonator only)
    # ------------------------------------------------------------------

    def _get_grating_params(self):
        """Return `GratingParams` dict from device config or raise :class:`DeviceError`."""
        p = self.device.get('GratingParams')
        if p is None:
            raise DeviceError(
                f'{self.device["Device"]}: GratingParams not set in device '
                f'config. Load them with load_grating_params_from_xml().')
        return p

    def _wavelength_to_resonator_steps(self, wavelength_nm):
        """Convert `wavelength_nm` [nm] to resonator motor steps.

        Uses the analytical formula from the LIOP-TEC document "Formula steps
        to lambda.pdf". Raises :class:`DeviceError` if the wavelength is out
        of the grating's valid range.

        The input is rounded to 4 decimal places before computing, matching
        the wire format used by :meth:`set_wavelength` (``:.4f``). The step
        count is computed as ``ceil(val)`` via ``round(0.5 + val)``, which
        matches the rounding convention of the LiopStar Control software.

        :returns: integer step count
        """
        g = self._get_grating_params()
        wavelength_nm = round(wavelength_nm, 4)   # match :.4f wire format
        psi1 = (g['m_prime'] * g['d_prime'] / 1e6) * (wavelength_nm / 2)
        psi2 = (g['m'] * g['d'] / 1e6) * wavelength_nm - math.sin(g['theta'])
        for label, val in (('ψ₁', psi1), ('ψ₂', psi2)):
            if not -1.0 <= val <= 1.0:
                raise DeviceError(
                    f'{self.device["Device"]}: Wavelength {wavelength_nm} nm '
                    f'is out of range ({label}={val:.4f} outside [-1, 1])')
        phi = math.asin(psi1) + math.asin(psi2) - g['phi0']
        return round(0.5 + (g['x0'] + g['L'] * math.sin(phi)) * g['n'] / g['p'])

    def _resonator_steps_to_wavelength(self, steps):
        """Convert resonator motor `steps` to wavelength in nm.

        Uses the analytical formula from the LIOP-TEC document "Formula steps
        to lambda.pdf". Raises :class:`DeviceError` if the step count is out
        of the valid range.

        :returns: wavelength in nm
        """
        g = self._get_grating_params()
        x = steps * g['p'] / g['n'] - g['x0']
        ratio = x / g['L']
        if not -1.0 <= ratio <= 1.0:
            raise DeviceError(
                f'{self.device["Device"]}: resonator position {steps} steps '
                f'is out of range (x/L={ratio:.4f} outside [-1, 1])')
        phi = math.asin(ratio) + g['phi0']
        alpha = g['m'] * g['d'] / 1e6
        beta  = g['m_prime'] * g['d_prime'] / 1e6
        numerator = (
            (4*alpha + 2*beta*math.cos(phi)) * math.sin(g['theta'])
            + 2*math.sin(phi) * math.sqrt(
                (beta * math.cos(g['theta']))**2
                + 4*alpha * (alpha + beta*math.cos(phi)))
        )
        denominator = beta**2 + 4*alpha**2 + 4*alpha*beta*math.cos(phi)
        return numerator / denominator

    def get_wavelength(self):
        """Return current wavelength in nm derived from the resonator position.

        Requires `device['GratingParams']` to be set (use
        :func:`load_grating_params_from_xml` to populate it).

        Does not require remote access.

        :returns: wavelength in nm
        """
        pos = self.get_actual_position()
        return self._resonator_steps_to_wavelength(pos['Resonator'])

    def set_wavelength_and_wait(self, wavelength_nm, timeout=30.):
        """Tune to `wavelength_nm` [nm] and block until the move has completed.

        The wavelength is rounded to 4 decimal places by :meth:`set_wavelength`
        before being sent to the hardware. Uses the calibration-based completion
        path if 'GratingParams' is set (see :meth:`wait_for_move_complete`),
        otherwise falls back to status-based polling.

        :wavelength_nm: target wavelength in nm
        :timeout: maximum wait time in seconds (default: 30)
        :returns: final positions dict `{motor_name: step_count}`
        """
        self.set_wavelength(wavelength_nm)
        return self.wait_for_move_complete(timeout=timeout,
                                           target_wavelength_nm=wavelength_nm)
