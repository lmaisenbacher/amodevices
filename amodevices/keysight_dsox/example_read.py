# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/UC Berkeley

Example: read the trace the oscilloscope currently holds, save it with
`save_waveforms()`, and plot it. `example_plot.py` plots such a file again.
"""

import datetime
from pathlib import Path

import matplotlib.pyplot as plt

from amodevices import KeysightDSOX

device = {
    'Device': 'Keysight MSO-X 2024A',
    'Address': 'TCPIP0::192.168.50.29::inst0::INSTR',
    'Timeout': 4.,
    }
# Channels to read, with the names recorded in the file's metadata
channels = {
    1: 'Signal',
    # 2: 'Reference',
    }
# Directory for the trace file and the plot
dir_data = Path('.')

scope = KeysightDSOX(device)
# The record the oscilloscope currently holds; `scope.acquire_single(...)`
# would instead arm a single acquisition and wait for its trigger
record = scope.read_waveforms(list(channels), names=list(channels.values()))
# Further metadata goes into the record before saving
record['metadata'].update(scope.trigger.settings)
record['metadata']['Comment'] = 'Example trace'
scope.close()

# Name the files after the read time
timestamp = datetime.datetime.fromisoformat(record['metadata']['Timestamp'])
filebase = timestamp.strftime('%Y-%m-%d %H-%M-%S')
path = KeysightDSOX.save_waveforms(Path(dir_data, filebase+'.npz'), record)
print(f'Saved trace to \'{path}\'')

fig, ax = plt.subplots(
    num='KeysightDSOX example', figsize=(8, 6), clear=True, constrained_layout=True)
for i, (channel, name) in enumerate(
        zip(record['channels'], record['metadata']['ChannelNames'])):
    ax.plot(record['time'], record['data'][i], label=f'Ch {channel}: {name}')
ax.legend()
ax.set_xlabel('Time (s)')
ax.set_ylabel('Voltage (V)')
ax.set_title(filebase)
plt.savefig(Path(dir_data, filebase+'.png'), dpi=300)
plt.show()
