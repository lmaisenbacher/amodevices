# -*- coding: utf-8 -*-
"""
Manual lab checks of the Ophir EA-1 driver (not a pytest file): the
first contact with an adapter, and the checks that only a firing laser
can answer. Close StarLab first, the adapter has one client.

    python test.py -a 192.168.50.25 --identify --settings
    python test.py -a 192.168.50.25 --stream 60
    python test.py -a 192.168.50.25 --restart
    python test.py -a 192.168.50.25 --polled 10
    python test.py -a 192.168.50.25 --range 4 --stream 10

`--identify` and `--settings` print the adapter's replies verbatim
(nothing is changed). `--stream <s>` prints every line of the per-pulse
stream raw and parsed, then a summary: index steps (anything but 1 is a
missed pulse), timestamp steps (100 000 us at 10 Hz), and the spread of
arrival time minus device time (its floor is the network latency, the
rest is host jitter). `--restart` streams twice with a pause between and
reports whether the index and the timestamp continue across the stop.
`--polled <s>` exercises the ~10 Hz `$EF`/`$SE` readout for comparison.
`--range <i>` selects a range index before streaming (the lowest range,
4 on the PE50BF-DFH-C, provokes over-range pulses with the beam on, to
see how the stream reports them); it is the one option that changes a
setting, and it is not saved: a power cycle restores the head's stored
range.

@author: Lothar Maisenbacher/UC Berkeley
"""

import argparse
import logging
import statistics
import time

from amodevices.dev_exceptions import DeviceError
from amodevices.ophir_ea1.ophir_ea1 import OphirEA1

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def identify(dev):
    print(f'Adapter:  {dev.adapter_type} {dev.adapter_serial} {dev.adapter_name}')
    print(f'Firmware: {dev.firmware_version}')
    print(f'Head:     {dev.head_type} {dev.head_serial} {dev.head_name}'
          f' {dev.head_extra}')


def settings(dev):
    for command in ('$MM', '$AR', '$RN', '$AW', '$PL', '$DQ', '$UT', '$EE'):
        try:
            print(f'{command:5s} -> *{dev._query(command)}')
        except DeviceError as exc:
            print(f'{command:5s} -> {exc}')


def stream(dev, seconds, label=''):
    """Stream for `seconds`, print every line, return the pulses."""
    dev.start_stream()
    print(f'--- stream {label}started, {seconds:.0f} s')
    pulses = []
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        for pulse in dev.read_pulses(0.2):
            pulses.append(pulse)
            if len(pulses) <= 50 or pulse.status != 'ok':
                print(f'{pulse.t_recv:.6f}  {pulse.raw!r:40s}  index {pulse.index}'
                      f'  t_dev {pulse.timestamp_us} us  E {pulse.energy_j}'
                      f'  {pulse.status}')
    leftovers = dev.stop_stream()
    print(f'--- stream stopped; {len(leftovers)} pulse(s) arrived with the stop')
    pulses.extend(leftovers)
    return pulses


def summarize(pulses):
    good = [p for p in pulses if p.index is not None]
    print(f'{len(pulses)} line(s): {len(good)} with an index,'
          f' {sum(p.status == "overexposed" for p in pulses)} over-range,'
          f' {sum(p.status == "unparseable" for p in pulses)} unparseable')
    if len(good) < 2:
        return
    index_steps = [b.index - a.index for a, b in zip(good, good[1:])]
    ts_steps = [b.timestamp_us - a.timestamp_us for a, b in zip(good, good[1:])]
    latency = [p.t_recv - p.timestamp_us * 1e-6 for p in good]
    floor = min(latency)
    print(f'index steps: {sorted(set(index_steps))} (missed pulses:'
          f' {sum(s - 1 for s in index_steps if s > 1)})')
    print(f'timestamp steps (us): min {min(ts_steps)} median'
          f' {statistics.median(ts_steps):.0f} max {max(ts_steps)}')
    spread = [(x - floor) * 1e3 for x in latency]
    print(f'arrival minus device time, above its floor (ms): median'
          f' {statistics.median(spread):.2f} p95'
          f' {sorted(spread)[int(0.95 * (len(spread) - 1))]:.2f} max'
          f' {max(spread):.2f}')
    energies = [p.energy_j for p in good if p.energy_j is not None]
    if energies:
        print(f'energy (J): mean {statistics.mean(energies):.4e} sd'
              f' {statistics.pstdev(energies):.2e} min {min(energies):.4e}'
              f' max {max(energies):.4e}')


def restart(dev):
    first = stream(dev, 10, 'A ')
    summarize(first)
    print('--- 3 s pause')
    time.sleep(3)
    second = stream(dev, 10, 'B ')
    summarize(second)
    a = [p for p in first if p.index is not None]
    b = [p for p in second if p.index is not None]
    if a and b:
        print(f'last index of A {a[-1].index}, first of B {b[0].index}'
              f' (a continuing counter reads about'
              f' {a[-1].index + 30 + 1}; a reset reads 0 or 1)')
        print(f'last timestamp of A {a[-1].timestamp_us} us, first of B'
              f' {b[0].timestamp_us} us (a continuing clock reads about'
              f' {a[-1].timestamp_us + 13_000_000} us)')


def polled(dev, seconds):
    t_end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < t_end:
        new, energy, status = dev.read_energy_polled()
        if new:
            n += 1
            print(f'{time.time():.3f}  {energy}  {status}')
        time.sleep(0.02)
    print(f'{n} new value(s) in {seconds:.0f} s')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('-a', '--address', default='192.168.50.25')
    parser.add_argument('--port', type=int, default=23)
    parser.add_argument('--identify', action='store_true')
    parser.add_argument('--settings', action='store_true')
    parser.add_argument('--stream', type=float, metavar='S')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--polled', type=float, metavar='S')
    parser.add_argument('--range', type=int, metavar='I',
                        help='select this range index first (not saved)')
    args = parser.parse_args()

    dev = OphirEA1({'Device': 'Ophir EA-1', 'Address': args.address,
                    'Port': args.port})
    try:
        dev.connect()
        if args.identify:
            identify(dev)
        if args.settings:
            settings(dev)
        if args.range is not None:
            dev.set_range_index(args.range)
            print(f'range index {args.range} selected; the head settles for a'
                  ' few seconds')
            time.sleep(3)
        if args.stream:
            summarize(stream(dev, args.stream))
        if args.restart:
            restart(dev)
        if args.polled:
            polled(dev, args.polled)
    except DeviceError as exc:
        print(exc)
    finally:
        dev.close()


if __name__ == '__main__':
    main()
