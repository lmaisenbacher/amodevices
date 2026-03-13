"""
Check (grating) motor-step to wavelength conversion formula consistency for the
LIOP-TEC LiopStar-E dye laser.

Sweeps over a set of wavelengths and compares the resonator step position
reported by the hardware after each move to the position predicted by the
motor-step to wavelength conversion formula.  A systematic offset or trend
indicates a mismatch between our formula and the one used by the LiopStar
Control software; zero or near-zero differences confirm consistency.

Update 'Address' to the IP address of the LiopStar Control PC before running.
"""

import logging
import random
import numpy as np
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

# --- Sweep parameters ---
WL_MIN       = 560.0   # nm
WL_MAX       = 570.0   # nm
N_POINTS     = 30
MOVE_TIMEOUT = 10.     # s
RANDOMIZE    = True    # True: random wavelengths; False: linspace

if RANDOMIZE:
    WAVELENGTHS = sorted(random.uniform(WL_MIN, WL_MAX) for _ in range(N_POINTS))
else:
    WAVELENGTHS = list(np.linspace(WL_MIN, WL_MAX, N_POINTS))


dev = LioptecLiopStar(device)
try:
    dev.connect()

    print('Remote status:', dev.get_remote_status())
    print('System status:', dev.get_status())
    print('Errors:', dev.get_error())

    dev.remote_connect()

    results = []
    for target_nm in WAVELENGTHS:
        sent_nm    = round(target_nm, 4)
        steps_calc = dev._wavelength_to_resonator_steps(target_nm)
        print(f'  -> {target_nm:.5f} nm  ({steps_calc} steps)', end='', flush=True)
        try:
            dev.set_wavelength(target_nm)
            dev.wait_for_move_complete(timeout=MOVE_TIMEOUT)
        except DeviceError as e:
            print(f'  FAILED: {e.value}')
            continue
        pos = dev.get_actual_position()
        wl_read = dev.get_wavelength()
        steps_actual = pos['Resonator']
        results.append({
            'target_nm':    target_nm,
            'sent_nm':      sent_nm,
            'steps_calc':   steps_calc,
            'steps_actual': steps_actual,
            'wl_read_nm':   wl_read,
        })
        delta_nu_MHz = -(wl_read - sent_nm) * 1e-9 * 299792458 / (sent_nm * 1e-9)**2 / 1e6
        print(f'  done  (Δsteps = {steps_actual - steps_calc:+d}, '
              f'Δλ = {(wl_read - sent_nm)*1000:+.3f} pm, '
              f'Δν = {delta_nu_MHz:+.3f} MHz)')

    dev.remote_disconnect()

    print(f'\n| {"target (nm)":>12} | {"sent (nm)":>12} | {"calc steps":>12} | {"actual steps":>12} '
          f'| {"Δ steps":>8} | {"λ_read (nm)":>12} | {"Δλ (pm)":>10} | {"Δν (MHz)":>10} |')
    print(f'|{"-"*14}:|{"-"*14}:|{"-"*14}:|{"-"*14}:|{"-"*10}:|{"-"*14}:|{"-"*12}:|{"-"*12}:|')
    for r in results:
        delta_nu_MHz = -(r["wl_read_nm"] - r["sent_nm"]) * 1e-9 * 299792458 / (r["sent_nm"] * 1e-9)**2 / 1e6
        print(f'| {r["target_nm"]:12.5f} | {r["sent_nm"]:12.5f} | {r["steps_calc"]:12d} | {r["steps_actual"]:12d} '
              f'| {r["steps_actual"] - r["steps_calc"]:+8d} '
              f'| {r["wl_read_nm"]:12.5f} | {(r["wl_read_nm"] - r["sent_nm"])*1000:+10.3f} '
              f'| {delta_nu_MHz:+10.3f} |')

except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
