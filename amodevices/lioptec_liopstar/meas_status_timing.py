"""
Diagnostic: compare GetStatus and GetActualPosition (LiopStar Control API commands) timing during a
(grating) motor move for the LIOP-TEC LiopStar-E dye laser.

Sweeps a range of move sizes and for each one polls status and resonator
step count at ~10 ms intervals, reporting when status left OK, when it
returned to OK, and when steps reached their final value (if calibration is loaded).

`raise_on_warning=True` is set so that out-of-range wavelength warnings are
treated as errors and abort the current move cleanly.

Update 'Address' before running.
"""

import time
import logging
from pathlib import Path

from amodevices import LioptecLiopStar
from amodevices.dev_exceptions import DeviceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.010   # s between polls
SETTLE_COUNT  = 5       # extra identical step readings collected after apparent settle
START_NM      = 560.0
STEP_SIZES_NM = [0.0001, 0.001, 0.01, 0.1, 1, 10]

device = {
    'Device':        'LIOP-TEC LiopStar-E',
    'Address':       'localhost',
    'Port':          65510,
    'Timeout':       5.,
    'GratingParamsXML': Path(__file__).parent / 'LiopStar_0923LT0226_2400_Rh6G_560-570.xml',
}


def measure_move(dev, start_nm, target_nm):
    """Move from `start_nm` to `target_nm` and return timing records."""
    dev.set_wavelength_and_wait(start_nm, timeout=60.)

    t0 = time.perf_counter()
    dev.set_wavelength(target_nm)

    target_steps = (dev._wavelength_to_resonator_steps(target_nm)
                    if 'GratingParams' in dev.device else None)

    records = []   # (elapsed_s, status, resonator_steps)
    prev_steps = None
    stable     = 0
    post_count = 0   # extra reads collected after target+OK first seen
    deadline   = time.perf_counter() + 60.

    while True:
        t      = time.perf_counter() - t0
        status = dev.get_status()
        pos    = dev.get_actual_position()
        steps  = pos.get('Resonator')
        records.append((t, status, steps))

        if target_steps is not None:
            # Mirror the driver's calibration path: exit when target reached AND OK,
            # then collect SETTLE_COUNT extra reads to catch post-settle activity
            if post_count > 0:
                post_count += 1
                if post_count > SETTLE_COUNT:
                    break
            elif steps == target_steps and status == 'OK':
                post_count = 1
        else:
            # No calibration: exit on step stability
            if steps == prev_steps:
                stable += 1
                if stable >= SETTLE_COUNT:
                    break
            else:
                stable = 0

        if time.perf_counter() > deadline:
            print('  WARNING: timed out before target step count was reached')
            break

        prev_steps = steps
        elapsed = time.perf_counter() - t0 - t
        time.sleep(max(0.0, POLL_INTERVAL - elapsed))

    return records


def summarize(dev, records, target_nm):
    final_steps = records[-1][2]

    t_moving = next((t for t, s, _ in records if s != 'OK'), None)

    last_moving_idx = next(
        (i for i, rec in enumerate(reversed(records)) if rec[1] != 'OK'), None)
    if last_moving_idx is not None:
        ok_after_idx = len(records) - last_moving_idx
        t_status_ok = records[ok_after_idx][0] if ok_after_idx < len(records) else None
    else:
        t_status_ok = None

    t_steps_final = next((t for t, _, s in records if s == final_steps), None)

    # Print table
    print(f'{"t (s)":>8}  {"status":^8}  {"steps":>10}')
    print('-' * 34)
    for t, status, steps in records:
        print(f'{t:8.3f}  {status:^8}  {steps!s:>10}')
    print()

    if t_moving is not None:
        print(f'  Status left OK at      t = {t_moving:.3f} s')
    else:
        print('  Status never left OK during the move')
    if t_status_ok is not None:
        print(f'  Status returned OK at  t = {t_status_ok:.3f} s')
    if t_steps_final is not None:
        print(f'  Steps at final value   t = {t_steps_final:.3f} s  ({final_steps} steps)')

    if t_moving is None:
        print('  --> status never showed MOVING (tiny move or already at target)')
    elif t_status_ok is not None and t_steps_final is not None:
        delta = t_status_ok - t_steps_final
        if abs(delta) < POLL_INTERVAL:
            print('  --> status OK and steps final within one poll interval')
        elif delta > 0:
            print(f'  --> status OK {delta*1000:.1f} ms AFTER steps final')
        else:
            print(f'  --> status OK {-delta*1000:.1f} ms BEFORE steps final')

    # Step accuracy check (requires calibration)
    if 'GratingParams' in dev.device:
        expected_steps = dev._wavelength_to_resonator_steps(target_nm)
        delta_steps = final_steps - expected_steps
        print(f'  Expected steps: {expected_steps}  actual: {final_steps}  '
              f'delta = {delta_steps:+d} steps')


dev = LioptecLiopStar(device, raise_on_warning=True)
try:
    dev.connect()
    dev.remote_connect()

    print(f'Moving to start position {START_NM} nm ...')
    dev.set_wavelength_and_wait(START_NM, timeout=60.)
    print('Start position reached.\n')

    for step_nm in STEP_SIZES_NM:
        target_nm = START_NM + step_nm
        print(f'{"="*50}')
        print(f'Move: {START_NM} -> {target_nm} nm  (Δ = {step_nm} nm)')
        print(f'{"="*50}')
        try:
            records = measure_move(dev, START_NM, target_nm)
            summarize(dev, records, target_nm)
        except DeviceError as e:
            print(f'  Skipped: {e.value}')
        print()

    dev.remote_disconnect()
except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
