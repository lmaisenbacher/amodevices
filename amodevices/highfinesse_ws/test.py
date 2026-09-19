# -*- coding: utf-8 -*-
"""
Manual hardware test for the HighFinesse WS wavemeter driver.
Requires the HighFinesse wavemeter software running on the same PC.

With 'ReadOnce' on and a pulsed laser firing, the two probes at the end
settle what the manual leaves open: whether `GetPowerNum` (and
`GetAmplitudeNum`) still deliver after `GetFrequencyNum` consumed the
result — the order the logger uses — and, swapped, whether the power
read consumes the frequency. Also tells whether this unit has a
pressure sensor.
"""

import logging
import time

from amodevices import HighFinesseWS
from amodevices.dev_exceptions import DeviceError
from amodevices.highfinesse_ws.highfinesse_ws import cAvg1, cAvg2, cMax1, cMax2

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'HighFinesse WS/7 wavemeter',
    # 'ReadOnce': True,
    # 'PulseMode': 0,
    }

PROBE_POLLS = 150          # 3 s at the 20 ms poll period of the logger
PROBE_PERIOD_S = 0.02


def probe(dev, power_first):
    """Poll like the logger: per poll the frequency return, the four
    amplitudes, and the power return; count what came back."""
    seen = {'results': 0, 'power_values': 0, 'amplitudes_nonzero': 0}
    print(f'\nProbe, {"power" if power_first else "frequency"} read first,'
          f' {PROBE_POLLS} polls at {PROBE_PERIOD_S*1e3:.0f} ms'
          ' (lines only for polls with a frequency result or power value):')
    for _ in range(PROBE_POLLS):
        if power_first:
            power = dev.get_power()
            freq = dev.get_frequency()
        else:
            freq = dev.get_frequency()
            power = dev.get_power()
        amps = tuple(dev.get_amplitude(i) for i in (cMax1, cMax2, cAvg1, cAvg2))
        if freq != 0:
            seen['results'] += 1
        if power > 0:
            seen['power_values'] += 1
        if any(a > 0 for a in amps):
            seen['amplitudes_nonzero'] += 1
        if freq != 0 or power > 0:
            print(f'  frequency {freq:.6f}  power {power:.4f}'
                  f'  max1/max2/avg1/avg2 {amps}')
        time.sleep(PROBE_PERIOD_S)
    print(f'  -> {seen}')
    return seen


try:
    device_instance = HighFinesseWS(device)
except DeviceError as e:
    print(e.value)
else:
    print(f'Measurement mode: {device_instance.get_pulse_mode()}')
    print(f'Automatic exposure: {device_instance.get_automatic_exposure()}')
    print(f'Exposures (ms): {device_instance.get_exposures()}')
    print(f'Levels: {device_instance.get_levels()}')
    print(f'Optical unit temperature (°C; <= -1000 = a code):'
          f' {device_instance.get_temperature()}')
    print(f'Optical unit pressure (mbar; <= -1000 = a code, -1006 = no sensor):'
          f' {device_instance.get_pressure()}')
    print('Frequency readings over 2 s (THz; error codes <= 0 pass through,'
          ' with \'ReadOnce\' already-read results read as 0):')
    for _ in range(20):
        print(device_instance.get_frequency())
        time.sleep(0.1)
    first = probe(device_instance, power_first=False)
    second = probe(device_instance, power_first=True)
    if first['results'] and not first['power_values']:
        print('\nGetPowerNum returned nothing after the frequency reads: the'
              ' ReadOnce flag is shared'
              + (' (and the power read consumes the frequency too)'
                 if not second['results'] else
                 ' one way only (a frequency read after the power read still'
                 ' delivers)')
              + ' - log the energy through the callback mode instead.')
    elif first['results']:
        print('\nGetPowerNum delivers after the frequency read: the logger'
              ' can log the energy per result.')
    if first['results'] and not first['amplitudes_nonzero']:
        print('GetAmplitudeNum returned no amplitudes: the amplitude channels'
              ' cannot be logged in ReadOnce mode.')
