"""
Check motor-step to wavelength conversion formula consistency for the
LIOP-TEC LiopStar-E dye laser.

Sweeps over a set of wavelengths and compares the resonator step position
reported by the hardware after each move to the position predicted by the
motor-step to wavelength conversion formula.  A systematic offset or trend
indicates a mismatch between our formula and the one used by the LiopStar
Control software; zero or near-zero differences confirm consistency.

Update 'Address' to the IP address of the LiopStar Control PC before running.
"""

import logging
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
WAVELENGTHS = np.linspace(560, 570, 11)   # nm
MOVE_TIMEOUT = 60.                         # s


dev = LioptecLiopStar(device)
try:
    dev.connect()

    print('Remote status:', dev.get_remote_status())
    print('System status:', dev.get_status())
    print('Errors:', dev.get_error())

    dev.remote_connect()

    results = []
    for target_nm in WAVELENGTHS:
        print(f'  -> {target_nm:.4f} nm', end='', flush=True)
        dev.set_wavelength_and_wait(target_nm, timeout=MOVE_TIMEOUT)
        pos = dev.get_actual_position()
        wl_read = dev.get_wavelength()
        steps_calc = dev._wavelength_to_resonator_steps(target_nm)
        steps_actual = pos['Resonator']
        results.append({
            'target_nm':    target_nm,
            'steps_calc':   steps_calc,
            'steps_actual': steps_actual,
            'wl_read_nm':   wl_read,
        })
        delta_nu_MHz = -(wl_read - target_nm) * 1e-9 * 299792458 / (target_nm * 1e-9)**2 / 1e6
        print(f'  done  (Δsteps = {steps_actual - steps_calc:+d}, '
              f'Δλ = {(wl_read - target_nm)*1000:+.3f} pm, '
              f'Δν = {delta_nu_MHz:+.3f} MHz)')

    dev.remote_disconnect()

    print(f'\n| {"target (nm)":>12} | {"calc steps":>12} | {"actual steps":>12} '
          f'| {"Δ steps":>8} | {"λ_read (nm)":>12} | {"Δλ (pm)":>10} | {"Δν (MHz)":>10} |')
    print(f'|{"-"*14}:|{"-"*14}:|{"-"*14}:|{"-"*10}:|{"-"*14}:|{"-"*12}:|{"-"*12}:|')
    for r in results:
        delta_nu_MHz = -(r["wl_read_nm"] - r["target_nm"]) * 1e-9 * 299792458 / (r["target_nm"] * 1e-9)**2 / 1e6
        print(f'| {r["target_nm"]:12.4f} | {r["steps_calc"]:12d} | {r["steps_actual"]:12d} '
              f'| {r["steps_actual"] - r["steps_calc"]:+8d} '
              f'| {r["wl_read_nm"]:12.4f} | {(r["wl_read_nm"] - r["target_nm"])*1000:+10.3f} '
              f'| {delta_nu_MHz:+10.3f} |')

except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
