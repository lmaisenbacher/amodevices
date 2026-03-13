"""
Characterize (grating) motor settling time for the LIOP-TEC LiopStar-E dye laser.

Sweeps over a set of step sizes and directions, repeats each move N times,
and prints per-motor and overall settling times as a markdown table.

For each move the following completion times are recorded:

- ``t_settle``: step-based criterion (SETTLE_COUNT consecutive identical reads)
- ``t_ok``: when status first returned to 'OK' after being non-OK
- ``t_target``: when the resonator first reached the calculated target step
  count (requires calibration via 'GratingParamsXML')
- ``t_done``: max(t_ok, t_target) — time ``wait_for_move_complete`` would
  return when calibration is available

`raise_on_warning=True` is set so that out-of-range wavelength warnings are
treated as errors.

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

# --- Test parameters ---
START_NM = 560.0          # wavelength to start from
STEP_SIZES_NM = [10.0, 1.0, 0.1, 0.01, 0.001, 0.0001]  # step sizes to characterize
N_REPEATS = 5             # number of forward/backward pairs per step size
POLL_INTERVAL = 0.010     # seconds between position reads
SETTLE_COUNT = 5          # consecutive identical readings to declare settled
SETTLE_TIMEOUT = 60.      # seconds before giving up


def move_and_measure(dev, target_nm, verbose=True):
    """Set wavelength to `target_nm`, poll until all motors settle.

    Returns a tuple:
    ``(per_motor_settled_times, overall_settled_time, final_positions,
    t_ok, t_target)``

    - ``per_motor_settled_times``: dict ``{motor_name: time_s}`` for
      step-based settle criterion
    - ``overall_settled_time``: time when the last motor satisfied the
      step-based criterion
    - ``final_positions``: dict ``{motor_name: step_count}``
    - ``t_ok``: time when status first returned to 'OK' after a non-OK
      reading; ``None`` if status never left 'OK'
    - ``t_target``: time when the resonator first reached the calculated
      target step count; ``None`` if calibration is unavailable
    """
    if verbose:
        print(f'  -> {target_nm:.4f} nm', end='', flush=True)
    dev.set_wavelength(target_nm)

    target_steps = (dev._wavelength_to_resonator_steps(target_nm)
                    if 'GratingParams' in dev.device else None)

    motors = None
    stable_counts = {}
    settled_times = {}   # {motor: time when first stable reading seen}
    confirmed = set()    # motors that reached SETTLE_COUNT
    prev_pos = {}
    start = time.perf_counter()

    seen_non_ok = False
    t_ok     = None   # first OK after a non-OK reading
    t_target = None   # first time resonator reached target steps

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

        # Track status transitions
        if status != 'OK':
            seen_non_ok = True
        elif seen_non_ok and t_ok is None:
            t_ok = elapsed

        # Track when resonator first reaches target steps
        if target_steps is not None and t_target is None:
            if pos.get('Resonator') == target_steps:
                t_target = elapsed

        if motors is None:
            motors = list(pos.keys())
            stable_counts = {m: 0 for m in motors}

        # Only count stable reads once the move has truly started or ended:
        # - seen_non_ok: status left OK, so a move is/was in progress
        # - calibration target reached + OK: move finished (all motors done)
        # - elapsed > MOVE_START_TIMEOUT: no non-OK seen within the start
        #   window — motor was already at target (mirrors driver behavior)
        settle_active = seen_non_ok or (
            target_steps is not None
            and pos.get('Resonator') == target_steps
            and status == 'OK'
        ) or elapsed > dev.MOVE_START_TIMEOUT

        for m in motors:
            if m in confirmed:
                continue
            if settle_active and pos.get(m) == prev_pos.get(m):
                if stable_counts[m] == 0:
                    settled_times[m] = elapsed
                stable_counts[m] += 1
                if stable_counts[m] >= SETTLE_COUNT:
                    confirmed.add(m)
            elif not settle_active or pos.get(m) != prev_pos.get(m):
                stable_counts[m] = 0
                settled_times.pop(m, None)

        prev_pos = dict(pos)

        if motors is not None and confirmed == set(motors):
            overall_time = max(settled_times[m] for m in motors)
            if verbose:
                t_done = max(t for t in (t_ok, t_target) if t is not None) \
                    if any(t is not None for t in (t_ok, t_target)) else None
                parts = [f'settle={overall_time:.3f}s']
                if t_ok is not None:
                    parts.append(f't_ok={t_ok:.3f}s')
                if t_target is not None:
                    parts.append(f't_target={t_target:.3f}s')
                if t_done is not None:
                    parts.append(f't_done={t_done:.3f}s')
                print(f'  ({", ".join(parts)})  pos={pos}')
            return settled_times, overall_time, pos, t_ok, t_target

        time.sleep(POLL_INTERVAL)


def avg_sd(values):
    n = len(values)
    a = sum(values) / n
    sd = (sum((v - a)**2 for v in values) / n)**0.5
    return a, sd


def fmt_opt(val, fmt='.3f'):
    """Format an optional float value, returning '-' if None."""
    return f'{val:{fmt}}' if val is not None else '-'


dev = LioptecLiopStar(device, raise_on_warning=True)
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
                try:
                    motor_times, overall_time, pos, t_ok, t_target = \
                        move_and_measure(dev, target_nm)
                except DeviceError as e:
                    print(f'  Skipped ({e.value})')
                    continue
                t_done = max(t for t in (t_ok, t_target) if t is not None) \
                    if any(t is not None for t in (t_ok, t_target)) else None
                results.append({
                    'step_nm':   step_nm,
                    'direction': direction,
                    'rep':       rep + 1,
                    'from_nm':   current_nm,
                    'target_nm': target_nm,
                    'overall_s': overall_time,
                    't_ok_s':    t_ok,
                    't_target_s': t_target,
                    't_done_s':  t_done,
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

    has_calibration = results[0]['t_target_s'] is not None

    # --- Per-move summary table ---
    extra_cols = ' | t_ok (s) | t_target (s) | t_done (s)' if has_calibration \
        else ' | t_ok (s)'
    extra_sep  = ' |--------: |------------: |---------:' if has_calibration \
        else ' |--------:'
    motor_cols = ''.join(f' | {m} (s)' for m in motors)
    motor_sep  = ''.join(f' |-------:' for _ in motors)
    print(f'\n| step (nm) | dir | rep | from (nm) | target (nm) | t_settle (s)'
          f'{extra_cols}{motor_cols} |')
    print(f'|----------:|:---:|----:|----------:|------------:|------------:'
          f'{extra_sep}{motor_sep} |')
    for r in results:
        extra_vals = (f' | {fmt_opt(r["t_ok_s"])} | {fmt_opt(r["t_target_s"])}'
                      f' | {fmt_opt(r["t_done_s"])}') if has_calibration \
            else f' | {fmt_opt(r["t_ok_s"])}'
        motor_vals = ''.join(f' | {r[f"settle_{m}_s"]:7.3f}' for m in motors)
        print(f'| {r["step_nm"]:9.3f} | {r["direction"]:^3} | {r["rep"]:3d}'
              f' | {r["from_nm"]:9.4f} | {r["target_nm"]:11.4f}'
              f' | {r["overall_s"]:10.3f}{extra_vals}{motor_vals} |')

    # --- Averages / SD table ---
    extra_cols = ' | avg t_ok (s) | SD | avg t_target (s) | SD | avg t_done (s) | SD' \
        if has_calibration else ' | avg t_ok (s) | SD'
    extra_sep  = ' |------------:|---:|----------------:|---:|--------------:|---:' \
        if has_calibration else ' |------------:|---:'
    motor_cols = ''.join(f' | avg {m} (s) | SD (s)' for m in motors)
    motor_sep  = ''.join(f' |-----------:|-------:' for _ in motors)
    print(f'\n| step (nm) | dir | avg t_settle (s) | SD (s){extra_cols}{motor_cols} |')
    print(f'|----------:|:---:|----------------:|-------:{extra_sep}{motor_sep} |')
    for step_nm in STEP_SIZES_NM:
        for direction in ('+', '-'):
            subset = [r for r in results
                      if r['step_nm'] == step_nm and r['direction'] == direction]
            if not subset:
                continue
            oa, osd = avg_sd([r['overall_s'] for r in subset])
            ok_vals = [r['t_ok_s'] for r in subset if r['t_ok_s'] is not None]
            ok_stats = f' | {avg_sd(ok_vals)[0]:.3f} | {avg_sd(ok_vals)[1]:.3f}' \
                if ok_vals else ' | - | -'
            if has_calibration:
                target_vals = [r['t_target_s'] for r in subset if r['t_target_s'] is not None]
                target_stats = f' | {avg_sd(target_vals)[0]:.3f} | {avg_sd(target_vals)[1]:.3f}' \
                    if target_vals else ' | - | -'
                done_vals = [r['t_done_s'] for r in subset if r['t_done_s'] is not None]
                done_stats = f' | {avg_sd(done_vals)[0]:.3f} | {avg_sd(done_vals)[1]:.3f}' \
                    if done_vals else ' | - | -'
                extra_stats = ok_stats + target_stats + done_stats
            else:
                extra_stats = ok_stats
            motor_stats = ''.join(
                f' | {a:.3f} | {sd:.3f}'
                for a, sd in (avg_sd([r[f'settle_{m}_s'] for r in subset]) for m in motors)
            )
            print(f'| {step_nm:9.3f} | {direction:^3} | {oa:14.3f} | {osd:6.3f}'
                  f'{extra_stats}{motor_stats} |')

except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
