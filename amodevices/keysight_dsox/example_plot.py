# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Example: plot a saved trace file, i.e. an .npz file written by
`save_waveforms()` (for instance by `example_read.py`) or in the legacy pyhs
layout. Usage: `python example_plot.py <file.npz>`.
"""

import sys
from pathlib import Path

import matplotlib.pyplot as plt

from amodevices import KeysightDSOX

file_trace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('trace.npz')

record = KeysightDSOX.load_waveforms(file_trace)
metadata = record['metadata']
# Legacy files carry the read time as 'Time', current ones as 'Timestamp'
timestamp = metadata.get('Timestamp', metadata.get('Time', ''))

fig, ax = plt.subplots(
    num='KeysightDSOX example', figsize=(8, 6), clear=True, constrained_layout=True)
for i, (channel, name) in enumerate(zip(record['channels'], metadata['ChannelNames'])):
    ax.plot(record['time'], record['data'][i], label=f'Ch {channel}: {name}')
ax.legend()
ax.set_xlabel('Time (s)')
ax.set_ylabel('Voltage (V)')
ax.set_title(f'{file_trace.stem} ({timestamp})')
plt.show()
