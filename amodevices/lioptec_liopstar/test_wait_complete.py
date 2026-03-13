"""
Validate `wait_for_move_complete` timing for the LIOP-TEC LiopStar-E dye laser.

Sweeps over the same step sizes and directions as `meas_settling_time.py`,
calling `set_wavelength_and_wait()` for each move and timing how long it
blocks. The elapsed times can be compared against the `t_done` values from
`meas_settling_time.py` to confirm the routine returns at the right moment.

Two passes are run back-to-back:
  1. Calibration path — 'GratingParamsXML' provided; `wait_for_move_complete`
     uses resonator step count + status to detect completion.
  2. Status-only path — no calibration; `wait_for_move_complete` uses
     two-phase status polling. For tiny moves where status never leaves 'OK',
     it falls back to waiting `MOVE_START_TIMEOUT` before returning.

Update 'Address' to the IP address of the LiopStar Control PC before running.
"""

import logging
import time
from pathlib import Path

from amodevices import LioptecLiopStar
from amodevices.dev_exceptions import DeviceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADDRESS = 'localhost'
PORT    = 65510

device_with_cal = {
    'Device': 'LIOP-TEC LiopStar-E',
    'Address': ADDRESS,
    'Port': PORT,
    'Timeout': 5.,
    'GratingParamsXML': Path(__file__).parent / 'LiopStar_0923LT0226_2400_Rh6G_560-570.xml',
}

device_no_cal = {
    'Device': 'LIOP-TEC LiopStar-E',
    'Address': ADDRESS,
    'Port': PORT,
    'Timeout': 5.,
}

# --- Test parameters ---
START_NM = 560.0
STEP_SIZES_NM = [10.0, 1.0, 0.1, 0.01, 0.001, 0.0001]
N_REPEATS = 5
MOVE_TIMEOUT = 60.


def avg_sd(values):
    n = len(values)
    a = sum(values) / n
    sd = (sum((v - a)**2 for v in values) / n)**0.5
    return a, sd


def run_sweep(dev):
    """Run the step-size sweep on `dev`. Returns list of result dicts."""
    print(f'\nMoving to start wavelength {START_NM} nm ...')
    dev.set_wavelength_and_wait(START_NM, timeout=MOVE_TIMEOUT)

    results = []
    current_nm = START_NM

    for step_nm in STEP_SIZES_NM:
        print(f'\n--- Step size: {step_nm} nm ---')
        for rep in range(N_REPEATS):
            for direction, sign in [('+', +1), ('-', -1)]:
                target_nm = current_nm + sign * step_nm
                print(f'  -> {target_nm:.4f} nm', end='', flush=True)
                try:
                    t0 = time.perf_counter()
                    pos = dev.set_wavelength_and_wait(target_nm, timeout=MOVE_TIMEOUT)
                    elapsed = time.perf_counter() - t0
                except DeviceError as e:
                    print(f'  Skipped ({e.value})')
                    continue
                print(f'  {elapsed:.3f} s  pos={pos}')
                results.append({
                    'step_nm':   step_nm,
                    'direction': direction,
                    'rep':       rep + 1,
                    'from_nm':   current_nm,
                    'target_nm': target_nm,
                    'elapsed_s': elapsed,
                })
                current_nm = target_nm

    return results


def print_tables(results, label):
    print(f'\n=== {label} ===')

    print(f'\n| step (nm) | dir | rep | from (nm) | target (nm) | elapsed (s) |')
    print(f'|----------:|:---:|----:|----------:|------------:|------------:|')
    for r in results:
        print(f'| {r["step_nm"]:9.4f} | {r["direction"]:^3} | {r["rep"]:3d}'
              f' | {r["from_nm"]:9.4f} | {r["target_nm"]:11.4f}'
              f' | {r["elapsed_s"]:11.3f} |')

    print(f'\n| step (nm) | dir | avg elapsed (s) | SD (s) |')
    print(f'|----------:|:---:|----------------:|-------:|')
    for step_nm in STEP_SIZES_NM:
        for direction in ('+', '-'):
            subset = [r for r in results
                      if r['step_nm'] == step_nm and r['direction'] == direction]
            if not subset:
                continue
            a, sd = avg_sd([r['elapsed_s'] for r in subset])
            print(f'| {step_nm:9.4f} | {direction:^3} | {a:15.3f} | {sd:6.3f} |')


# --- Pass 1: calibration path ---
results_cal = []
dev = LioptecLiopStar(device_with_cal, raise_on_warning=True)
try:
    dev.connect()
    print('Remote status:', dev.get_remote_status())
    print('System status:', dev.get_status())
    print('Drive positions:', dev.get_actual_position())
    print('Errors:', dev.get_error())
    dev.remote_connect()
    results_cal = run_sweep(dev)
    dev.remote_disconnect()
except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()

# --- Pass 2: status-only path ---
results_no_cal = []
dev = LioptecLiopStar(device_no_cal, raise_on_warning=True)
try:
    dev.connect()
    dev.remote_connect()
    results_no_cal = run_sweep(dev)
    dev.remote_disconnect()
except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()

# --- Print results ---
if results_cal:
    print_tables(results_cal, 'Calibration path')
if results_no_cal:
    print_tables(results_no_cal, 'Status-only path')
