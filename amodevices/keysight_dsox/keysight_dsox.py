# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Device driver for Keysight (Agilent) InfiniiVision X-Series oscilloscopes
(DSO-X and MSO-X models of the 2000/3000 X-Series), controlled through VISA.

The command set is documented in the Keysight InfiniiVision 2000 X-Series
Oscilloscopes Programmer's Guide, publication 9018-06893 (2024-02-01); page
references below are to that guide. Tested with an MSO-X 2024A. Moved from
the MPQ package `pyhs` (`pyhs.devices.DSOX`).

Waveform transfer is fixed to signed 16-bit little-endian WORD data
(':WAVeform:FORMat WORD', ':WAVeform:UNSigned OFF', ':WAVeform:BYTeorder
LSBFirst'), decoded with the scaling of the waveform preamble (p. 731):

    voltage = (raw - yreference) * yincrement + yorigin
    time = (index - xreference) * xincrement + xorigin

The raw acquisition record (waveform points mode 'RAW', the driver's default)
can only be transferred while the oscilloscope is stopped (p. 742):
`acquire_single()` arms a single acquisition, waits for it to complete, and
reads the record; `read_waveforms()` alone reads whatever record the
oscilloscope currently holds.
"""

import datetime
import json
import logging
import re
import time

import numpy as np

from .. import dev_generic
from ..dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

# Fields of the ':WAVeform:PREamble?' response, in order (p. 744)
PREAMBLE_FIELDS = (
    ('format', int),
    ('type_code', int),
    ('points', int),
    ('count', int),
    ('xincrement', float),
    ('xorigin', float),
    ('xreference', int),
    ('yincrement', float),
    ('yorigin', float),
    ('yreference', int),
    )

# SCPI keyword sets as long forms; the capitals are the short form, which
# queries return
TRIGGER_MODES = (
    'EDGE', 'GLITch', 'PATTern', 'TV', 'DELay', 'EBURst', 'OR', 'RUNT', 'SHOLd',
    'TRANsition', 'SBUS1', 'USB')
TRIGGER_SWEEPS = ('AUTO', 'NORMal')
SLOPES = ('POSitive', 'NEGative')
EDGE_SLOPES = ('POSitive', 'NEGative', 'EITHer', 'ALTernate')
EDGE_COUPLINGS = ('AC', 'DC', 'LFReject')
CHANNEL_COUPLINGS = ('AC', 'DC')
ACQUIRE_TYPES = ('NORMal', 'AVERage', 'HRESolution', 'PEAK')
ACQUIRE_MODES = ('RTIMe', 'SEGMented')
TIMEBASE_MODES = ('MAIN', 'WINDow', 'XY', 'ROLL')
POINTS_MODES = ('NORMal', 'MAXimum', 'RAW')

# Sources whose trigger level is set with ':TRIGger:EDGE:LEVel' (p. 679):
# analog channels and the external trigger input. Digital channels use
# thresholds instead.
_LEVEL_SOURCE_PATTERN = re.compile(r'^(CHAN(NEL)?\d+|EXT(ERNAL)?)$', re.IGNORECASE)
_CHANNEL_SOURCE_PATTERN = re.compile(r'^CHAN(?:NEL)?(\d+)$', re.IGNORECASE)


def scpi_keyword(value, keywords, what, device=''):
    """
    Match `value` (str, case-insensitive) against the SCPI `keywords` (tuple
    of long forms with the short form in capitals, e.g. 'NORMal') and return
    the matching long form; both the short and the long form are accepted.
    Raises `DeviceError`, naming `what` and prefixed with the `device` name,
    for any other value.
    """
    text = str(value).strip().upper()
    for keyword in keywords:
        short_form = ''.join(char for char in keyword if not char.islower())
        if text in (short_form, keyword.upper()):
            return keyword
    prefix = f'{device}: ' if device else ''
    raise DeviceError(
        f'{prefix}{what} must be one of {", ".join(keywords)}, not \'{value}\'')


def channel_source(channel):
    """
    SCPI source name of `channel`: an int `n` becomes 'CHANnel<n>', a str
    (e.g. 'CHAN2', 'DIGital3', 'EXTernal') is passed through.
    """
    if isinstance(channel, (int, np.integer)) and not isinstance(channel, bool):
        return f'CHANnel{int(channel):d}'
    return str(channel).strip()


def channel_number(source):
    """
    Channel number (int) of an analog channel `source` (2, 'CHAN2', or
    'CHANnel2'); None for any other source.
    """
    if isinstance(source, (int, np.integer)) and not isinstance(source, bool):
        return int(source)
    match = _CHANNEL_SOURCE_PATTERN.match(str(source).strip())
    return int(match.group(1)) if match else None


def is_level_source(source):
    """
    True if the trigger level of `source` (int channel number or str source)
    is set with ':TRIGger:EDGE:LEVel' (p. 679): analog channels and the
    external trigger input. Digital channels use thresholds instead.
    """
    return bool(_LEVEL_SOURCE_PATTERN.match(channel_source(source)))


def parse_preamble(text):
    """
    Parse the response `text` (str) of ':WAVeform:PREamble?' (p. 744) into a
    dict with keys 'format', 'type_code', 'points', 'count', 'xincrement',
    'xorigin', 'xreference', 'yincrement', 'yorigin', and 'yreference'
    (ints and floats). Raises `DeviceError` if the response does not hold
    the ten fields.
    """
    fields = text.strip().split(',')
    if len(fields) != len(PREAMBLE_FIELDS):
        raise DeviceError(
            f'Waveform preamble holds {len(fields)} fields instead of'
            +f' {len(PREAMBLE_FIELDS)}: \'{text}\'')
    preamble = {}
    for (key, cast), field in zip(PREAMBLE_FIELDS, fields):
        try:
            # Integer fields may be sent in NR3 format (e.g. '+1.88E+4')
            preamble[key] = cast(float(field))
        except ValueError as e:
            raise DeviceError(
                f'Waveform preamble field \'{key}\' is not a number: \'{field}\'') from e
    return preamble


def scale_waveform(raw, preamble):
    """
    Convert the raw WORD data `raw` (int array) to volts (float64 array) with
    the scaling of `preamble` (dict, see `parse_preamble()`; p. 731):
    ``(raw - yreference) * yincrement + yorigin``.
    """
    raw = np.asarray(raw, dtype=np.float64)
    return (raw-preamble['yreference'])*preamble['yincrement']+preamble['yorigin']


def time_axis(preamble, n_values):
    """
    Time axis (s, float64 array of length `n_values`) of a waveform record
    with `preamble` (dict, see `parse_preamble()`; p. 731):
    ``(index - xreference) * xincrement + xorigin``. In peak-detect
    acquisitions the record holds two values (min, max) per time bucket
    (p. 731), so `n_values` may be twice the preamble's point count; each
    bucket's time is then repeated. Any other length raises `DeviceError`.
    """
    points = preamble['points']
    times = (
        (np.arange(points, dtype=np.float64)-preamble['xreference'])
        *preamble['xincrement']+preamble['xorigin'])
    if n_values == points:
        return times
    if n_values == 2*points:
        return np.repeat(times, 2)
    raise DeviceError(
        f'Waveform holds {n_values} values, but its preamble announces {points} points')


def _json_default(value):
    """JSON encoder fallback: NumPy scalars and arrays become Python numbers
    and lists."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def save_waveforms(path, record):
    """
    Save the waveform `record` (dict as returned by
    `KeysightDSOX.read_waveforms()`) to the compressed NumPy archive `path`
    (.npz, appended if missing): arrays 'Time' (s), 'Data' (V, one row per
    channel), 'Channels' (channel numbers), and 'Metadata', the metadata dict
    as a JSON string in a 0-d array, so loading needs no pickling. Returns
    `path`.
    """
    metadata = json.dumps(record['metadata'], default=_json_default)
    np.savez_compressed(
        path,
        Time=np.asarray(record['time'], dtype=np.float64),
        Data=np.asarray(record['data'], dtype=np.float64),
        Channels=np.asarray(record['channels'], dtype=np.int64),
        Metadata=np.array(metadata))
    return path


def load_waveforms(path):
    """
    Load a waveform record (dict, see `KeysightDSOX.read_waveforms()`) from
    `path`: an .npz archive written by `save_waveforms()`, or one in the
    legacy layout of the `pyhs` driver ('rawData' with the time axis in
    column 0 and one channel per further column, 'params' dict), which the
    CRD scripts wrote before the move to amodevices. A legacy file's metadata
    is its 'params' dict verbatim, plus 'Channels', 'ChannelNames', and
    'ChannelUnits' derived from its 'Columns' entries.
    """
    with np.load(path, allow_pickle=False) as archive:
        if 'rawData' not in archive.files:
            return {
                'time': archive['Time'],
                'data': archive['Data'],
                'channels': [int(channel) for channel in archive['Channels']],
                'metadata': json.loads(str(archive['Metadata'])),
                }
    # Legacy layout: the params dict is pickled
    with np.load(path, allow_pickle=True) as archive:
        raw_data = np.asarray(archive['rawData'], dtype=np.float64)
        params = dict(archive['params'][()])
    n_channels = raw_data.shape[1]-1
    columns = list(params.get('Columns', []))[1:]
    channels = []
    for i in range(n_channels):
        match = re.match(r'^Ch(\d+)$', columns[i]) if i < len(columns) else None
        channels.append(int(match.group(1)) if match else i+1)
    names = list(params.get('ColumnNames', []))[1:]
    units = list(params.get('ColumnUnits', []))[1:]
    metadata = {
        **params,
        'Channels': channels,
        'ChannelNames': [
            names[i] if i < len(names) else f'Ch{channel:d}'
            for i, channel in enumerate(channels)],
        'ChannelUnits': [units[i] if i < len(units) else 'V' for i in range(n_channels)],
        }
    return {
        'time': raw_data[:, 0],
        'data': raw_data[:, 1:].T,
        'channels': channels,
        'metadata': metadata,
        }


class KeysightDSOX(dev_generic.Device):
    """Device driver for Keysight (Agilent) InfiniiVision X-Series oscilloscopes."""

    # Polling interval of `wait_for_stop()` (s)
    POLL_INTERVAL = 0.05
    # Depth of the instrument's error queue (p. 624)
    ERROR_QUEUE_DEPTH = 30

    class _channel():

        def __init__(self, outer_instance, channel):
            self.outer_instance = outer_instance
            self.channel = int(channel)
            self.source = channel_source(self.channel)

        @property
        def scale(self):
            """Get vertical scale (V/div, float)."""
            return float(self.outer_instance.visa_query(f':CHANnel{self.channel:d}:SCALe?'))

        @scale.setter
        def scale(self, scale):
            """Set vertical scale to `scale` (V/div, float)."""
            return self.outer_instance.visa_write(f':CHANnel{self.channel:d}:SCALe {scale}')

        @property
        def offset(self):
            """Get vertical offset (V, float)."""
            return float(self.outer_instance.visa_query(f':CHANnel{self.channel:d}:OFFSet?'))

        @offset.setter
        def offset(self, offset):
            """Set vertical offset to `offset` (V, float)."""
            return self.outer_instance.visa_write(f':CHANnel{self.channel:d}:OFFSet {offset}')

        @property
        def coupling(self):
            """Get input coupling (str): 'AC' or 'DC'."""
            return self.outer_instance.visa_query(f':CHANnel{self.channel:d}:COUPling?')

        @coupling.setter
        def coupling(self, coupling):
            """Set input coupling to `coupling` (str): 'AC' or 'DC'."""
            coupling = self.outer_instance._keyword(
                coupling, CHANNEL_COUPLINGS, 'Channel coupling')
            return self.outer_instance.visa_write(
                f':CHANnel{self.channel:d}:COUPling {coupling}')

        @property
        def display(self):
            """Get whether the channel is displayed (bool)."""
            return bool(int(self.outer_instance.visa_query(f':CHANnel{self.channel:d}:DISPlay?')))

        @display.setter
        def display(self, display):
            """Display the channel (`display` (bool) True) or turn it off."""
            return self.outer_instance.visa_write(
                f':CHANnel{self.channel:d}:DISPlay {int(bool(display)):d}')

        @property
        def label(self):
            """Get the channel label (str, up to 10 ASCII characters)."""
            return self.outer_instance.visa_query(f':CHANnel{self.channel:d}:LABel?').strip('"')

        @label.setter
        def label(self, label):
            """Set the channel label to `label` (str, up to 10 ASCII characters)."""
            label = str(label)
            if len(label) > 10 or not label.isascii() or '"' in label:
                raise DeviceError(
                    f'{self.outer_instance.device["Device"]}: Channel label must be'
                    +f' up to 10 ASCII characters without quotation marks, not \'{label}\'')
            return self.outer_instance.visa_write(f':CHANnel{self.channel:d}:LABel "{label}"')

        @property
        def probe(self):
            """Get probe attenuation ratio (float)."""
            return float(self.outer_instance.visa_query(f':CHANnel{self.channel:d}:PROBe?'))

        @probe.setter
        def probe(self, attenuation):
            """Set probe attenuation ratio to `attenuation` (float)."""
            return self.outer_instance.visa_write(
                f':CHANnel{self.channel:d}:PROBe {attenuation}')

        @property
        def preamble(self):
            """
            Get the preamble of this channel's waveform record (dict, see
            `parse_preamble()`; p. 744) plus 'type', the acquisition type
            (str, p. 754): 'NORM', 'PEAK', 'AVER', or 'HRES'. Selects this
            channel as the waveform source.
            """
            outer_instance = self.outer_instance
            outer_instance.visa_write(f':WAVeform:SOURce {self.source}')
            preamble = parse_preamble(outer_instance.visa_query(':WAVeform:PREamble?'))
            preamble['type'] = outer_instance.visa_query(':WAVeform:TYPE?')
            return preamble

        def _read(self):
            """
            Select this channel as waveform source and read its preamble
            (dict, see `preamble`) and raw WORD data (int16 array). Waveform
            data can only be read from displayed channels (p. 729).
            """
            if not self.display:
                raise DeviceError(
                    f'{self.outer_instance.device["Device"]}: Channel {self.channel:d}'
                    +' is not displayed; waveform data can only be read from'
                    +' displayed channels')
            preamble = self.preamble
            raw = self.outer_instance.visa_query_binary(
                ':WAVeform:DATA?', datatype='h', is_big_endian=False)
            return preamble, raw

        def read_waveform(self):
            """
            Read this channel's waveform from the current acquisition as a
            tuple (time (s), voltage (V)) of float64 arrays. See
            `KeysightDSOX.read_waveforms()` for the record with metadata.
            """
            preamble, raw = self._read()
            return time_axis(preamble, len(raw)), scale_waveform(raw, preamble)

    class _trigger_edge():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def source(self):
            """Get the edge trigger source (str), e.g. 'CHAN1', 'EXT', 'LINE'."""
            return self.outer_instance.visa_query(':TRIGger:EDGE:SOURce?')

        @source.setter
        def source(self, source):
            """Set the edge trigger source to `source` (int channel number or
            str source, e.g. 'CHANnel1', 'EXTernal', 'LINE', 'WGEN')."""
            return self.outer_instance.visa_write(
                f':TRIGger:EDGE:SOURce {channel_source(source)}')

        @property
        def slope(self):
            """Get the edge trigger slope (str): 'POS', 'NEG', 'EITH', or 'ALT'."""
            return self.outer_instance.visa_query(':TRIGger:EDGE:SLOPe?')

        @slope.setter
        def slope(self, slope):
            """Set the edge trigger slope to `slope` (str): 'POSitive',
            'NEGative', 'EITHer', or 'ALTernate'."""
            slope = self.outer_instance._keyword(slope, EDGE_SLOPES, 'Edge trigger slope')
            return self.outer_instance.visa_write(f':TRIGger:EDGE:SLOPe {slope}')

        @property
        def coupling(self):
            """Get the edge trigger coupling (str): 'AC', 'DC', or 'LFR'."""
            return self.outer_instance.visa_query(':TRIGger:EDGE:COUPling?')

        @coupling.setter
        def coupling(self, coupling):
            """Set the edge trigger coupling to `coupling` (str): 'AC', 'DC',
            or 'LFReject'."""
            coupling = self.outer_instance._keyword(
                coupling, EDGE_COUPLINGS, 'Edge trigger coupling')
            return self.outer_instance.visa_write(f':TRIGger:EDGE:COUPling {coupling}')

    class _trigger_delay():
        """The edge-then-edge trigger (trigger mode 'DELay', pp. 665-671):
        an arming edge on one source, then the n-th trigger edge on another
        after a delay time."""

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def arm_source(self):
            """Get the arming edge source (str), e.g. 'CHAN2' or 'DIG0'."""
            return self.outer_instance.visa_query(':TRIGger:DELay:ARM:SOURce?')

        @arm_source.setter
        def arm_source(self, source):
            """Set the arming edge source to `source` (int channel number or
            str source: 'CHANnel<n>' or 'DIGital<d>')."""
            return self.outer_instance.visa_write(
                f':TRIGger:DELay:ARM:SOURce {channel_source(source)}')

        @property
        def arm_slope(self):
            """Get the arming edge slope (str): 'POS' or 'NEG'."""
            return self.outer_instance.visa_query(':TRIGger:DELay:ARM:SLOPe?')

        @arm_slope.setter
        def arm_slope(self, slope):
            """Set the arming edge slope to `slope` (str): 'POSitive' or 'NEGative'."""
            slope = self.outer_instance._keyword(slope, SLOPES, 'Arming edge slope')
            return self.outer_instance.visa_write(f':TRIGger:DELay:ARM:SLOPe {slope}')

        @property
        def trigger_source(self):
            """Get the trigger edge source (str), e.g. 'CHAN1' or 'DIG0'."""
            return self.outer_instance.visa_query(':TRIGger:DELay:TRIGger:SOURce?')

        @trigger_source.setter
        def trigger_source(self, source):
            """Set the trigger edge source to `source` (int channel number or
            str source: 'CHANnel<n>' or 'DIGital<d>')."""
            return self.outer_instance.visa_write(
                f':TRIGger:DELay:TRIGger:SOURce {channel_source(source)}')

        @property
        def trigger_slope(self):
            """Get the trigger edge slope (str): 'POS' or 'NEG'."""
            return self.outer_instance.visa_query(':TRIGger:DELay:TRIGger:SLOPe?')

        @trigger_slope.setter
        def trigger_slope(self, slope):
            """Set the trigger edge slope to `slope` (str): 'POSitive' or 'NEGative'."""
            slope = self.outer_instance._keyword(slope, SLOPES, 'Trigger edge slope')
            return self.outer_instance.visa_write(f':TRIGger:DELay:TRIGger:SLOPe {slope}')

        @property
        def trigger_count(self):
            """Get which trigger edge (n-th) triggers (int)."""
            return int(float(self.outer_instance.visa_query(':TRIGger:DELay:TRIGger:COUNt?')))

        @trigger_count.setter
        def trigger_count(self, count):
            """Trigger on the `count`-th (int) trigger edge."""
            return self.outer_instance.visa_write(
                f':TRIGger:DELay:TRIGger:COUNt {int(count):d}')

        @property
        def delay_time(self):
            """Get the delay time between arming edge and trigger edge (s, float)."""
            return float(self.outer_instance.visa_query(':TRIGger:DELay:TDELay:TIME?'))

        @delay_time.setter
        def delay_time(self, delay):
            """Set the delay time between arming edge and trigger edge to
            `delay` (s, float; 4 ns to 10 s)."""
            return self.outer_instance.visa_write(f':TRIGger:DELay:TDELay:TIME {delay}')

    class _trigger():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance
            self.edge = KeysightDSOX._trigger_edge(outer_instance)
            self.delay = KeysightDSOX._trigger_delay(outer_instance)

        @property
        def mode(self):
            """Get the trigger mode (str, p. 662), e.g. 'EDGE' or 'DEL'
            (edge then edge); 'NONE' in the ROLL and XY timebase modes."""
            return self.outer_instance.visa_query(':TRIGger:MODE?')

        @mode.setter
        def mode(self, mode):
            """Set the trigger mode to `mode` (str, p. 662): 'EDGE', 'GLITch',
            'PATTern', 'TV', 'DELay', 'EBURst', 'OR', 'RUNT', 'SHOLd',
            'TRANsition', 'SBUS1', or 'USB'."""
            mode = self.outer_instance._keyword(mode, TRIGGER_MODES, 'Trigger mode')
            return self.outer_instance.visa_write(f':TRIGger:MODE {mode}')

        @property
        def sweep(self):
            """Get the trigger sweep mode (str, p. 664; "Mode" on the front
            panel): 'AUTO' (triggers itself when no trigger arrives) or 'NORM'."""
            return self.outer_instance.visa_query(':TRIGger:SWEep?')

        @sweep.setter
        def sweep(self, sweep):
            """Set the trigger sweep mode to `sweep` (str): 'AUTO' or 'NORMal'."""
            sweep = self.outer_instance._keyword(sweep, TRIGGER_SWEEPS, 'Trigger sweep')
            return self.outer_instance.visa_write(f':TRIGger:SWEep {sweep}')

        @property
        def holdoff(self):
            """Get the trigger holdoff time (s, float)."""
            return float(self.outer_instance.visa_query(':TRIGger:HOLDoff?'))

        @holdoff.setter
        def holdoff(self, holdoff):
            """Set the trigger holdoff time to `holdoff` (s, float; 40 ns to 10 s)."""
            return self.outer_instance.visa_write(f':TRIGger:HOLDoff {holdoff}')

        def force(self):
            """Force an acquisition without the trigger condition being met (p. 656)."""
            return self.outer_instance.visa_write(':TRIGger:FORCe')

        def level(self, source=None):
            """
            Get the trigger level (V, float) of `source` (int channel number or
            str source such as 'CHAN2' or 'EXTernal'; default: the active
            trigger source) with ':TRIGger:EDGE:LEVel?' (p. 679). This is also
            the level of the arming and trigger edges of the edge-then-edge
            trigger when their sources are analog channels (p. 665).
            """
            query = ':TRIGger:EDGE:LEVel?'
            if source is not None:
                query += f' {channel_source(source)}'
            return float(self.outer_instance.visa_query(query))

        def set_level(self, level, source=None):
            """Set the trigger level of `source` (see `level()`) to `level`
            (V, float). The active trigger source is unaffected when another
            `source` is given."""
            command = f':TRIGger:EDGE:LEVel {level}'
            if source is not None:
                command += f',{channel_source(source)}'
            return self.outer_instance.visa_write(command)

        def _level_or_nan(self, source):
            """Trigger level (V) of `source`; NaN for a source without one
            (digital channels)."""
            return self.level(source) if is_level_source(source) else np.nan

        @property
        def settings(self):
            """
            Get a snapshot of the trigger configuration as a flat dict for
            records: 'TriggerMode', 'TriggerSweep', 'TriggerHoldoff_s' and, in
            edge-then-edge mode ('DEL'), 'TriggerArmSource', 'TriggerArmSlope',
            'TriggerArmLevel_V', 'TriggerSource', 'TriggerSlope',
            'TriggerLevel_V', 'TriggerCount', 'TriggerDelayTime_s'; in edge
            mode ('EDGE'), 'TriggerSource', 'TriggerSlope', 'TriggerCoupling',
            'TriggerLevel_V'. Levels are NaN for sources without one (digital
            channels).
            """
            mode = self.mode
            settings = {
                'TriggerMode': mode,
                'TriggerSweep': self.sweep,
                'TriggerHoldoff_s': self.holdoff,
                }
            if mode == 'DEL':
                delay = self.delay
                arm_source = delay.arm_source
                trigger_source = delay.trigger_source
                settings.update({
                    'TriggerArmSource': arm_source,
                    'TriggerArmSlope': delay.arm_slope,
                    'TriggerArmLevel_V': self._level_or_nan(arm_source),
                    'TriggerSource': trigger_source,
                    'TriggerSlope': delay.trigger_slope,
                    'TriggerLevel_V': self._level_or_nan(trigger_source),
                    'TriggerCount': delay.trigger_count,
                    'TriggerDelayTime_s': delay.delay_time,
                    })
            elif mode == 'EDGE':
                source = self.edge.source
                settings.update({
                    'TriggerSource': source,
                    'TriggerSlope': self.edge.slope,
                    'TriggerCoupling': self.edge.coupling,
                    'TriggerLevel_V': self._level_or_nan(source),
                    })
            return settings

    class _acquire():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def type(self):
            """Get the acquisition type (str, p. 215): 'NORM', 'AVER', 'HRES',
            or 'PEAK'."""
            return self.outer_instance.visa_query(':ACQuire:TYPE?')

        @type.setter
        def type(self, acquisition_type):
            """Set the acquisition type to `acquisition_type` (str): 'NORMal',
            'AVERage', 'HRESolution', or 'PEAK'."""
            acquisition_type = self.outer_instance._keyword(
                acquisition_type, ACQUIRE_TYPES, 'Acquisition type')
            return self.outer_instance.visa_write(f':ACQuire:TYPE {acquisition_type}')

        @property
        def count(self):
            """Get the number of averages of the averaging acquisition type (int)."""
            return int(float(self.outer_instance.visa_query(':ACQuire:COUNt?')))

        @count.setter
        def count(self, count):
            """Set the number of averages of the averaging acquisition type to
            `count` (int, 2 to 65536)."""
            return self.outer_instance.visa_write(f':ACQuire:COUNt {int(count):d}')

        @property
        def points(self):
            """Get the number of points the hardware acquires (int, p. 208).
            Not settable; see `KeysightDSOX.waveform_points` for the number
            transferred."""
            return int(float(self.outer_instance.visa_query(':ACQuire:POINts?')))

        @property
        def mode(self):
            """Get the acquisition mode (str, p. 207): 'RTIM' (real time) or
            'SEGM' (segmented memory)."""
            return self.outer_instance.visa_query(':ACQuire:MODE?')

        @mode.setter
        def mode(self, mode):
            """Set the acquisition mode to `mode` (str): 'RTIMe' or 'SEGMented'."""
            mode = self.outer_instance._keyword(mode, ACQUIRE_MODES, 'Acquisition mode')
            return self.outer_instance.visa_write(f':ACQuire:MODE {mode}')

    class _timebase():

        def __init__(self, outer_instance):
            self.outer_instance = outer_instance

        @property
        def scale(self):
            """Get the horizontal scale (s/div, float)."""
            return float(self.outer_instance.visa_query(':TIMebase:SCALe?'))

        @scale.setter
        def scale(self, scale):
            """Set the horizontal scale to `scale` (s/div, float)."""
            return self.outer_instance.visa_write(f':TIMebase:SCALe {scale}')

        @property
        def position(self):
            """Get the time from the trigger event to the display reference
            point (s, float)."""
            return float(self.outer_instance.visa_query(':TIMebase:POSition?'))

        @position.setter
        def position(self, position):
            """Set the time from the trigger event to the display reference
            point to `position` (s, float)."""
            return self.outer_instance.visa_write(f':TIMebase:POSition {position}')

        @property
        def range(self):
            """Get the full-screen horizontal range (s, float; 10 divisions)."""
            return float(self.outer_instance.visa_query(':TIMebase:RANGe?'))

        @range.setter
        def range(self, time_range):
            """Set the full-screen horizontal range to `time_range` (s, float)."""
            return self.outer_instance.visa_write(f':TIMebase:RANGe {time_range}')

        @property
        def mode(self):
            """Get the timebase mode (str): 'MAIN', 'WIND', 'XY', or 'ROLL'."""
            return self.outer_instance.visa_query(':TIMebase:MODE?')

        @mode.setter
        def mode(self, mode):
            """Set the timebase mode to `mode` (str): 'MAIN', 'WINDow', 'XY',
            or 'ROLL'."""
            mode = self.outer_instance._keyword(mode, TIMEBASE_MODES, 'Timebase mode')
            return self.outer_instance.visa_write(f':TIMebase:MODE {mode}')

    def __init__(self, device, update_callback_func=None):
        """
        Initialize class for device `device` (dict) and connect. Keys:
        'Device' (str, name), 'Address' (str, VISA resource name, e.g.
        'TCPIP0::192.168.50.29::inst0::INSTR'), optional 'Timeout' (s, float;
        the VISA timeout of every command and query).
        """
        super().__init__(device)

        self.init_visa()
        self._read_idn()
        self.trigger = self._trigger(self)
        self.acquire = self._acquire(self)
        self.timebase = self._timebase(self)

        self._configure_waveform_transfer()
        # Transfer the raw acquisition record with all its points
        self.waveform_points_mode = 'RAW'
        self.waveform_points = 'MAXimum'

    def close(self):
        """Close connection to device."""
        self.visa_resource.close()

    def _keyword(self, value, keywords, what):
        """`scpi_keyword()` with this device's name in the error message."""
        return scpi_keyword(value, keywords, what, self.device['Device'])

    def _read_idn(self):
        """Read '*IDN?' (p. 147) into `manufacturer`, `model`, `serial_number`,
        and `firmware` (str)."""
        fields = [field.strip() for field in self.visa_query('*IDN?').split(',')]
        fields += ['']*(4-len(fields))
        self.manufacturer, self.model, self.serial_number, self.firmware = fields[:4]

    def _configure_waveform_transfer(self):
        """Fix the waveform transfer encoding the decoder assumes: signed
        16-bit WORD data, least significant byte first (pp. 735, 739, 755)."""
        self.visa_write(':WAVeform:UNSigned OFF')
        self.visa_write(':WAVeform:BYTeorder LSBFirst')
        self.visa_write(':WAVeform:FORMat WORD')

    @property
    def system_error(self):
        """Get the next entry of the instrument's error queue (p. 624) as a
        tuple (code (int), message (str)); code 0 means the queue is empty."""
        code, _, message = self.visa_query(':SYSTem:ERRor?').partition(',')
        return int(float(code)), message.strip().strip('"')

    def check_errors(self):
        """Drain the instrument's error queue and raise `DeviceError` listing
        the errors if there were any."""
        errors = []
        for _ in range(self.ERROR_QUEUE_DEPTH):
            code, message = self.system_error
            if code == 0:
                break
            errors.append(f'{code:d}: {message}')
        if errors:
            raise DeviceError(
                f'{self.device["Device"]}: Instrument reported error(s): '
                +'; '.join(errors))

    @property
    def waveform_points_mode(self):
        """Get the waveform record `read_waveforms()` transfers (str, p. 742):
        'NORM' (the measurement record), 'RAW' (the raw acquisition record,
        available only while stopped), or 'MAX' (whichever holds more points)."""
        return self.visa_query(':WAVeform:POINts:MODE?')

    @waveform_points_mode.setter
    def waveform_points_mode(self, mode):
        """Set the waveform record to `mode` (str): 'NORMal', 'MAXimum', or 'RAW'."""
        mode = self._keyword(mode, POINTS_MODES, 'Waveform points mode')
        return self.visa_write(f':WAVeform:POINts:MODE {mode}')

    @property
    def waveform_points(self):
        """Get the number of waveform points transferred per channel (int, p. 740)."""
        return int(float(self.visa_query(':WAVeform:POINts?')))

    @waveform_points.setter
    def waveform_points(self, points):
        """Set the number of waveform points transferred per channel to
        `points` (int, from the instrument's 1-2-5 sequence 100, 250, 500,
        1000, 2000, ...) or 'MAXimum' (str) for all available points (p. 740)."""
        if isinstance(points, str):
            points = self._keyword(points, ('MAXimum',), 'Waveform points')
        else:
            points = f'{int(points):d}'
        return self.visa_write(f':WAVeform:POINts {points}')

    def channel(self, channel):
        """Return instance of channel class (class `_channel`) for analog
        channel number `channel` (int)."""
        return self._channel(self, channel)

    def run(self):
        """Start repetitive acquisitions (front-panel Run, p. 195)."""
        return self.visa_write(':RUN')

    def stop(self):
        """Stop acquisitions (front-panel Stop, p. 199)."""
        return self.visa_write(':STOP')

    def single(self):
        """Arm a single acquisition (front-panel Single, p. 197): the run
        state is 'SING' until the trigger condition is met, then 'STOP'. See
        `wait_for_stop()` and `acquire_single()`."""
        return self.visa_write(':SINGle')

    def digitize(self, *channels):
        """
        Acquire `channels` (ints; none given = the displayed channels) with
        ':DIGitize' (p. 177) and stop. The instrument blocks its interface
        until the acquisition completes, so any following query times out
        unless the device's 'Timeout' exceeds the time to the trigger; use
        `acquire_single()` unless the trigger is guaranteed to come quickly.
        """
        sources = ','.join(channel_source(channel) for channel in channels)
        return self.visa_write(':DIGitize'+(f' {sources}' if sources else ''))

    @property
    def state(self):
        """Get the run state (str, p. 194): 'RUN', 'STOP', or 'SING' (a single
        acquisition is armed and waiting for the trigger)."""
        return self.visa_query(':RSTate?')

    @property
    def running(self):
        """True while the oscilloscope is not stopped (bit 3 of the operation
        status condition register, p. 185), i.e. until an armed acquisition
        has completed."""
        return bool(int(float(self.visa_query(':OPERegister:CONDition?'))) & 8)

    @property
    def armed(self):
        """True if the trigger system is armed (p. 170). Reading clears the
        arm event register, so the value is one-shot."""
        return bool(int(float(self.visa_query(':AER?'))))

    @property
    def triggered(self):
        """True if a trigger has occurred since the last read (p. 200).
        Reading clears the trigger event register, so the value is one-shot."""
        return bool(int(float(self.visa_query(':TER?'))))

    def wait_for_stop(self, timeout):
        """
        Poll the run state every `POLL_INTERVAL` seconds until the
        oscilloscope is stopped, i.e. an armed acquisition has completed;
        raise `DeviceError` after `timeout` (s, float). This is the polling
        wait of the programmer's guide (p. 886); unlike ':DIGitize', it never
        blocks the interface.
        """
        deadline = time.monotonic()+timeout
        while self.running:
            if time.monotonic() >= deadline:
                raise DeviceError(
                    f'{self.device["Device"]}: Acquisition did not complete within'
                    +f' {timeout:.1f} s (run state \'{self.state}\')')
            time.sleep(self.POLL_INTERVAL)

    def acquire_single(self, channels, timeout=10., names=None):
        """
        Arm a single acquisition (`single()`), wait up to `timeout` (s, float)
        for it to complete (`wait_for_stop()`), and return the waveform record
        of `channels` (see `read_waveforms()`, also for `names`). On a timeout
        the acquisition is stopped and `DeviceError` raised. In trigger sweep
        mode 'AUTO' the oscilloscope triggers itself when no trigger arrives
        (p. 664), so a warning is logged; set `trigger.sweep` to 'NORMal' for
        a record that is guaranteed to have been triggered.
        """
        if self.trigger.sweep == 'AUTO':
            logger.warning(
                '%s: Trigger sweep mode is AUTO, so the single acquisition may'
                +' trigger itself instead of on the trigger condition; set'
                +' trigger.sweep = \'NORMal\' to require a trigger',
                self.device['Device'])
        self.single()
        try:
            self.wait_for_stop(timeout)
        except DeviceError:
            self.stop()
            raise
        return self.read_waveforms(channels, names=names)

    def read_waveforms(self, channels, names=None):
        """
        Read the waveform record of the analog `channels` (iterable of int)
        from the current acquisition as a dict:

            'time': time axis (s, float64 array of length N)
            'data': voltages (V, float64 array of shape (n_channels, N))
            'channels': channel numbers (list of int)
            'metadata': record metadata (dict, JSON-serializable)

        `names` (list of str, optional) labels the channels in the metadata
        key 'ChannelNames' (default 'Ch<n>'). The metadata carries the
        instrument identity, the read time ('Timestamp', ISO 8601 with UTC
        offset, from the PC clock), the run state, the acquisition type and
        preamble scaling, the timebase, and each channel's vertical settings.
        Callers may add keys (e.g. 'Comment' or `trigger.settings`) before
        `save_waveforms()`.

        All channels must hold the same number of points; the time axis is
        the first channel's. In peak-detect acquisitions the record holds two
        values (min, max) per point (p. 731), so N is twice the point count
        and each time value appears twice. The raw acquisition record
        (waveform points mode 'RAW') can only be transferred while the
        oscilloscope is stopped (p. 742); reading while running logs a
        warning, as the instrument serves the shorter measurement record
        instead. 'PointsAcquired' (from ':ACQuire:POINts?') also counts
        acquisition memory beyond the displayed window, which is never
        transferred (p. 740), so it exceeds 'NPoints' even for a raw record.
        """
        channels = [int(channel) for channel in channels]
        if not channels:
            raise DeviceError(f'{self.device["Device"]}: No channels to read')
        if names is None:
            names = [f'Ch{channel:d}' for channel in channels]
        names = [str(name) for name in names]
        if len(names) != len(channels):
            raise DeviceError(
                f'{self.device["Device"]}: {len(names)} channel names given for'
                +f' {len(channels)} channels')
        timestamp = datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')
        state = self.state
        # A requested 'RAW' mode reads back as 'MAX' on the MSO-X 2024A
        # (firmware 02.65), stopped or running; the acquired count includes
        # memory beyond the displayed window, which is never transferred
        points_mode = self.waveform_points_mode
        points_acquired = self.acquire.points

        data = None
        times = None
        preambles = []
        scales = []
        offsets = []
        couplings = []
        for i, channel in enumerate(channels):
            channel_instance = self.channel(channel)
            preamble, raw = channel_instance._read()
            if i == 0:
                times = time_axis(preamble, len(raw))
                data = np.empty((len(channels), len(raw)), dtype=np.float64)
            elif preamble['points'] != preambles[0]['points'] or len(raw) != data.shape[1]:
                raise DeviceError(
                    f'{self.device["Device"]}: Channel {channel:d} holds'
                    +f' {preamble["points"]:d} points ({len(raw)} values), channel'
                    +f' {channels[0]:d} {preambles[0]["points"]:d} points'
                    +f' ({data.shape[1]} values)')
            data[i] = scale_waveform(raw, preamble)
            preambles.append(preamble)
            scales.append(channel_instance.scale)
            offsets.append(channel_instance.offset)
            couplings.append(channel_instance.coupling)

        first = preambles[0]
        if state == 'RUN' and points_mode != 'NORM':
            # Bench-verified on an MSO-X 2024A: 60000 points while running
            # versus 480000 of the same acquisition once stopped
            logger.warning(
                '%s: Read while the oscilloscope is running: the raw acquisition'
                +' record is only available while stopped, so the measurement'
                +' record was returned instead (%d points transferred, %d acquired)',
                self.device['Device'], first['points'], points_acquired)
        metadata = {
            'Manufacturer': self.manufacturer,
            'Model': self.model,
            'SerialNumber': self.serial_number,
            'Firmware': self.firmware,
            'Timestamp': timestamp,
            'RunState': state,
            'AcquisitionType': first['type'],
            'AverageCount': first['count'],
            'PointsMode': points_mode,
            'NPoints': first['points'],
            'NValues': int(data.shape[1]),
            'PointsAcquired': points_acquired,
            'XIncrement_s': first['xincrement'],
            'XOrigin_s': first['xorigin'],
            'TimebaseScale_s': self.timebase.scale,
            'TimebasePosition_s': self.timebase.position,
            'Channels': channels,
            'ChannelNames': names,
            'ChannelUnits': ['V']*len(channels),
            'ChannelScales_V': scales,
            'ChannelOffsets_V': offsets,
            'ChannelCouplings': couplings,
            'YIncrements_V': [preamble['yincrement'] for preamble in preambles],
            'YOrigins_V': [preamble['yorigin'] for preamble in preambles],
            'YReferences': [preamble['yreference'] for preamble in preambles],
            }
        return {
            'time': times,
            'data': data,
            'channels': channels,
            'metadata': metadata,
            }

    # File I/O of waveform records, re-exported for consumers that only
    # import the driver
    save_waveforms = staticmethod(save_waveforms)
    load_waveforms = staticmethod(load_waveforms)
