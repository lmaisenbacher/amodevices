"""
Test script for the LIOP-TEC LiopStar-E dye laser driver.

Update 'Address' to the IP address of the LiopStar Control PC before running.
"""

import logging
import time
from pathlib import Path

from amodevices import LioptecLiopStar
from amodevices.dev_exceptions import DeviceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

device = {
    'Device': 'LIOP-TEC LiopStar-E',
    'Address': 'localhost',
    'Port': 65510,
    'Timeout': 5.,
    'GratingParamsXML': Path(__file__).parent / 'LiopStar_0923LT0226_2400_Rh6G_560-570.xml',
}

dev = LioptecLiopStar(device)
try:
    dev.connect()

    # These commands work without remote access
    print('Remote status:', dev.get_remote_status())
    print('System status:', dev.get_status())
    print('Drive positions:', dev.get_actual_position())
    print('Errors:', dev.get_error())

    dev.remote_connect()

    # Tune to a wavelength and wait for the move to complete
    target_nm = 560.0
    print(f'Setting wavelength to {target_nm} nm ...')
    start_time = time.perf_counter()
    dev.set_wavelength_and_wait(target_nm, timeout=60.)
    end_time = time.perf_counter()

    pos = dev.get_actual_position()
    wl_read = dev.get_wavelength()
    steps_target = dev._wavelength_to_resonator_steps(target_nm)
    print(f'Done after {end_time-start_time:.2f} s.')
    print(f'  Target:   {target_nm:.4f} nm  →  {steps_target} steps (calculated)')
    print(f'  Actual:   {wl_read:.4f} nm  →  {pos["Resonator"]} steps  '
          f'(error: {(wl_read - target_nm)*1000:+.3f} pm, '
          f'{pos["Resonator"] - steps_target:+d} steps)')

    dev.remote_disconnect()
except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
