"""
Characterize motor settling time for the LIOP-TEC LiopStar-E dye laser.

Sweeps over a set of step sizes and directions, repeats each move N times,
and prints per-motor and overall settling times as a markdown table.

Update 'Address' to the IP address of the LiopStar Control PC before running.
"""

import logging
import time
from amodevices import LioptecLiopStar
from amodevices.dev_exceptions import DeviceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

device = {
    'Device': 'LIOP-TEC LiopStar-E',
    'Address': 'localhost',
    'Port': 65510,
    'Timeout': 5.,
}

# --- Test parameters ---
START_NM = 560.0          # wavelength to start from
STEP_SIZES_NM = [10.0, 1.0, 0.1, 0.01]  # step sizes to characterize
N_REPEATS = 5             # number of forward/backward pairs per step size
POLL_INTERVAL = 0.05      # seconds between position reads
SETTLE_COUNT = 5          # consecutive identical readings to declare settled
SETTLE_TIMEOUT = 60.      # seconds before giving up


def move_and_measure(dev, target_nm, verbose=True):
    """Set wavelength to `target_nm`, poll until all motors settle.

    Returns (per_motor_settled_times, overall_settled_time, final_positions).
    per_motor_settled_times is a dict {motor_name: time_s}.
    overall_settled_time is the time when the last motor settled.
    """
    if verbose:
        print(f'  -> {target_nm:.4f} nm', end='', flush=True)
    dev.set_wavelength(target_nm)

    motors = None
    stable_counts = {}
    settled_times = {}   # {motor: time when first stable reading seen}
    confirmed = set()    # motors that reached SETTLE_COUNT
    prev_pos = {}
    start = time.perf_counter()

    while True:
        status = dev.get_status()
        if status == 'ERROR':
            raise DeviceError(
                f'{dev.device["Device"]}: System reported an error while waiting for motor')

        pos = dev.get_actual_position()
        elapsed = time.perf_counter() - start

        if elapsed > SETTLE_TIMEOUT:
            raise DeviceError(
                f'{dev.device["Device"]}: Timed out waiting for motor to settle')

        if motors is None:
            motors = list(pos.keys())
            stable_counts = {m: 0 for m in motors}

        for m in motors:
            if m in confirmed:
                continue
            if pos.get(m) == prev_pos.get(m):
                if stable_counts[m] == 0:
                    settled_times[m] = elapsed
                stable_counts[m] += 1
                if stable_counts[m] >= SETTLE_COUNT:
                    confirmed.add(m)
            else:
                stable_counts[m] = 0
                settled_times.pop(m, None)

        prev_pos = dict(pos)

        if motors is not None and confirmed == set(motors):
            overall_time = max(settled_times[m] for m in motors)
            if verbose:
                times_str = '  '.join(
                    f'{m}: {settled_times[m]:.3f}s' for m in motors)
                print(f'  settled ({times_str})  pos={pos}')
            return settled_times, overall_time, pos

        time.sleep(POLL_INTERVAL)


def avg_sd(values):
    n = len(values)
    a = sum(values) / n
    sd = (sum((v - a)**2 for v in values) / n)**0.5
    return a, sd


dev = LioptecLiopStar(device)
try:
    dev.connect()

    print('Remote status:', dev.get_remote_status())
    print('System status:', dev.get_status())
    print('Drive positions:', dev.get_actual_position())
    print('Errors:', dev.get_error())

    dev.remote_connect()

    # Move to start wavelength and wait for it to settle
    print(f'\nMoving to start wavelength {START_NM} nm ...')
    dev.set_wavelength_and_wait(START_NM, timeout=SETTLE_TIMEOUT)

    results = []
    current_nm = START_NM

    for step_nm in STEP_SIZES_NM:
        print(f'\n--- Step size: {step_nm} nm ---')
        for rep in range(N_REPEATS):
            for direction, sign in [('+', +1), ('-', -1)]:
                target_nm = current_nm + sign * step_nm
                motor_times, overall_time, pos = move_and_measure(dev, target_nm)
                results.append({
                    'step_nm': step_nm,
                    'direction': direction,
                    'rep': rep + 1,
                    'from_nm': current_nm,
                    'target_nm': target_nm,
                    'overall_s': overall_time,
                    **{f'settle_{m}_s': motor_times[m] for m in motor_times},
                    **{f'pos_{m}': v for m, v in pos.items()},
                })
                current_nm = target_nm

    dev.remote_disconnect()

    # Determine motor names from results, ordered resonator first then FCU1, FCU2, ...
    motors = sorted(
        (k[len('settle_'):-len('_s')] for k in results[0] if k.startswith('settle_')),
        key=lambda m: (0, '') if m == 'Resonator' else (1, m),
    )

    # --- Per-move summary table ---
    motor_cols = ''.join(f' | {m} (s)' for m in motors)
    motor_sep  = ''.join(f' |-------:' for _ in motors)
    print(f'\n| step (nm) | dir | rep | from (nm) | target (nm) | overall (s){motor_cols} |')
    print(f'|----------:|:---:|----:|----------:|------------:|-----------:{motor_sep} |')
    for r in results:
        motor_vals = ''.join(f' | {r[f"settle_{m}_s"]:7.3f}' for m in motors)
        print(f'| {r["step_nm"]:9.3f} | {r["direction"]:^3} | {r["rep"]:3d}'
              f' | {r["from_nm"]:9.4f} | {r["target_nm"]:11.4f}'
              f' | {r["overall_s"]:11.3f}{motor_vals} |')

    # --- Averages / SD table ---
    motor_cols = ''.join(f' | avg {m} (s) | SD (s)' for m in motors)
    motor_sep  = ''.join(f' |-----------:|-------:' for _ in motors)
    print(f'\n| step (nm) | dir | avg overall (s) | SD (s){motor_cols} |')
    print(f'|----------:|:---:|----------------:|-------:{motor_sep} |')
    for step_nm in STEP_SIZES_NM:
        for direction in ('+', '-'):
            subset = [r for r in results
                      if r['step_nm'] == step_nm and r['direction'] == direction]
            if not subset:
                continue
            oa, osd = avg_sd([r['overall_s'] for r in subset])
            motor_stats = ''.join(
                f' | {a:.3f} | {sd:.3f}'
                for a, sd in (avg_sd([r[f'settle_{m}_s'] for r in subset]) for m in motors)
            )
            print(f'| {step_nm:9.3f} | {direction:^3} | {oa:15.3f} | {osd:6.3f}{motor_stats} |')

except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
