# -*- coding: utf-8 -*-
"""
Created on Wed Jul 29 2026

@author: Lothar Maisenbacher/UC Berkeley

Test script for non-blocking, per-pulse energy readout with a pyroelectric energy sensor
(e.g. Thorlabs ES111C).
Energy measurements on the PM100D are single-shot: `arm()` configures the device and starts
the first measurement, which completes with the next pulse exceeding the trigger level.
The readout cycle is: poll `new_value_available`, `fetch()` the completed reading (retrying
past the transient no-data sentinel), then `rearm()` for the next pulse — see the docstrings
in the driver for the details, including the transition-filter cycling in `arm()` that keeps
the new-value event latching.
If no pulse is detected for longer than `no_pulse_timeout` (e.g. because the beam is
blocked), this is reported repeatedly, and the loop keeps polling without ever blocking.
"""

import logging
import time
from pathlib import Path

from amodevices import ThorlabsPM100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
# Log to console AND to a file next to this script (appended, with a
# run-start separator below), so runs on the meter's host PC can be read
# elsewhere via the synced folder
_log_path = Path(__file__).parent / 'test_energy.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_log_path, mode='a', encoding='utf-8'),
    ],
)

device = {
    'Device': 'Thorlabs PM100',
    'Address': 'USB0::0x1313::0x8078::P0045954::INSTR',
    }

# Energy range (float, J), or None to keep the current device setting.
# The device rounds the value to the next suitable range.
energy_range = 2e-4
# Trigger level (float) in percent (%) of the selected energy range (1% to 70%),
# or None to keep the current device setting
trigger_level = None
# Polling interval (s)
poll_interval = 0.02
# Time without pulse after which 'No pulse detected' is reported (s).
# Must be longer than the time between pulses (e.g. 0.1 s for a 10 Hz laser).
no_pulse_timeout = 0.15
# Total duration of readout loop (s)
duration = 10.

logger.info(
    '=== run started: energy_range=%s, trigger_level=%s, poll_interval=%s, duration=%s ===',
    energy_range, trigger_level, poll_interval, duration)

try:
    device_instance = ThorlabsPM100(device)
    if not device_instance.sensor.energy_sensor:
        raise DeviceError(
            f'{device["Device"]}: '
            +f'Connected sensor \'{device_instance.sensor.name}\' is not an energy sensor')
    logger.info(
        'Connected to energy sensor \'%s\' (serial number \'%s\')',
        device_instance.sensor.name, device_instance.sensor.serial_number)

    # Set energy range and trigger level if requested, and report the values actually in use
    if energy_range is not None:
        device_instance.energy.range = energy_range
    if trigger_level is not None:
        device_instance.energy.trigger_level = trigger_level
    logger.info(
        'Energy range: %.3e J, trigger level: %.1f%% of range',
        device_instance.energy.range, device_instance.energy.trigger_level)

    # Each pulse is a single sample, so averaging must be disabled for per-pulse readout
    device_instance.num_averages = 1
    # Configure energy mode and arm the first single-shot measurement
    device_instance.energy.arm()

    time_start = time.monotonic()
    time_last_pulse = time_start
    num_pulses = 0
    beam_present = True
    # Detection times of pulses (s), used to calculate the mean time between pulses
    pulse_times = []
    while time.monotonic() - time_start < duration:
        # Check (and clear) the new-value flag of the operation status register
        if device_instance.new_value_available:
            # A new pulse arrived: fetch its energy (retries past the
            # transient no-data sentinel) and arm the next measurement
            pulse_energy = device_instance.energy.fetch()
            device_instance.energy.rearm()
            if pulse_energy is None:
                logger.info('New-value flag set but only the no-data sentinel'
                            ' was fetched; pulse lost')
            else:
                num_pulses += 1
                time_last_pulse = time.monotonic()
                pulse_times.append(time_last_pulse)
                if not beam_present:
                    logger.info('Pulses detected again')
                    beam_present = True
                logger.info('Pulse %d: energy %.3e J', num_pulses, pulse_energy)
        elif time.monotonic() - time_last_pulse > no_pulse_timeout:
            # No pulse for longer than `no_pulse_timeout`: report and reset the timer, so this
            # keeps being reported every `no_pulse_timeout` while no pulses arrive
            logger.info('No pulse detected')
            beam_present = False
            time_last_pulse = time.monotonic()
        time.sleep(poll_interval)
    logger.info('Detected %d pulses in %.1f s', num_pulses, duration)
    if num_pulses >= 2:
        # Mean of the successive pulse intervals, which equals the total span divided by the
        # number of intervals
        mean_pulse_interval = (pulse_times[-1]-pulse_times[0])/(num_pulses-1)
        logger.info(
            'Mean time between pulses: %.1f ms (%.2f Hz)',
            mean_pulse_interval*1e3, 1/mean_pulse_interval)
except DeviceError as e:
    print(e.value)
