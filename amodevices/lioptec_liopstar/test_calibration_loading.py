"""
Offline test for the wavelength ↔ motor-step conversion formulas.

No hardware connection required. Tests `load_grating_params_from_xml()` and
the round-trip conversion using the supplied calibration XML file.

The calibration XML file must be in the same directory as this script.
"""

import logging
import numpy as np
from pathlib import Path

from amodevices import LioptecLiopStar

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Wavelengths (nm) for which to test conversion
wls = np.linspace(560, 570, 11)

# --- Set up a minimal device dict (no network address needed) ---
device = {
    'Device': 'LiopStar-E (offline)',
    'Address': 'localhost',   # not used — no connect() called
    'GratingParamsXML': Path(__file__).parent / 'LiopStar_0923LT0226_2400_Rh6G_560-570.xml',
}
dev = LioptecLiopStar(device)

print('GratingParams:')
for k, v in dev.device['GratingParams'].items():
    print(f'  {k:10s} = {v}')

# --- Round-trip test ---
# The only error source is the rounding of the exact step count to the nearest
# integer in _wavelength_to_resonator_steps(). The inverse is purely analytical.
print('\nRound-trip λ → steps → λ (error is solely from ±0.5-step rounding):')
print(f'{"λ_in (nm)":>12}  {"steps":>10}  {"λ_out (nm)":>12}  {"error (pm)":>12}')
print('-' * 52)
for wl in wls:
    steps = dev._wavelength_to_resonator_steps(wl)
    wl_out = dev._resonator_steps_to_wavelength(steps)
    error_pm = (wl_out - wl) * 1000
    print(f'{wl:12.4f}  {steps:10d}  {wl_out:12.4f}  {error_pm:+12.3f}')
