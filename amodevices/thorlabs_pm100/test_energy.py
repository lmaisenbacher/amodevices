# -*- coding: utf-8 -*-
"""
Created on Wed Jul 29 2026

@author: Lothar Maisenbacher/UC Berkeley

Test script for non-blocking, per-pulse energy readout with a pyroelectric energy sensor
(e.g. Thorlabs ES111C).
Arms a continuously running energy measurement, then polls the new-value flag and fetches the
energy of each detected pulse. If no pulse is detected for longer than `no_pulse_timeout`
(e.g. because the beam is blocked), this is reported repeatedly, and the loop keeps polling
without ever blocking.
"""

import logging
import time

from amodevices import ThorlabsPM100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Thorlabs PM100',
    'Address': 'USB0::0x1313::0x8078::P0045954::INSTR',
    }

# Polling interval (s)
poll_interval = 0.02
# Time without pulse after which 'No pulse detected' is reported (s).
# Must be longer than the time between pulses (e.g. 0.1 s for a 10 Hz laser).
no_pulse_timeout = 0.15
# Total duration of readout loop (s)
duration = 10.

try:
    device_instance = ThorlabsPM100(device)
    if not device_instance.sensor.energy_sensor:
        raise DeviceError(
            f'{device["Device"]}: '
            +f'Connected sensor \'{device_instance.sensor.name}\' is not an energy sensor')
    logger.info(
        'Connected to energy sensor \'%s\' (serial number \'%s\')',
        device_instance.sensor.name, device_instance.sensor.serial_number)

    # Each pulse is a single sample, so averaging must be disabled for per-pulse readout
    device_instance.num_averages = 1
    # Configure energy mode and start free-running measurement.
    # The device now updates the measurement value with each incoming pulse.
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
            # A new pulse arrived: fetch its energy, which returns immediately
            pulse_energy = device_instance.energy.last_value
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
