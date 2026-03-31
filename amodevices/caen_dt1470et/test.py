"""
Test script for the CAEN DT1470ET HV power supply driver.

Update 'Address' to the IP address of the power supply before running.

WARNING: This script sets voltages on channel 0. All test voltages are kept
well below 30 V (the threshold above which voltages are considered high
voltage). The software-enforced 'MaxVoltage' cap provides an additional
safety layer.
"""

import logging
import time

from amodevices import CAENDT1470ET
from amodevices.dev_exceptions import DeviceError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Test voltage — should stay well below 30 V
TEST_VOLTAGE = 10.0
# assert TEST_VOLTAGE <= 30.0, 'Test voltage must not exceed 30 V'

device = {
    'Device': 'CAEN DT1470ET',
    'Address': '192.168.50.50',
    'Port': 1470,
    'Timeout': 5.,
    'ConnectionType': 'ethernet',
    'BoardAddress': 0,
    'MaxVoltage': 30.,
}

dev = CAENDT1470ET(device)
try:
    dev.connect()

    # Board info
    print(f'Board name:       {dev.get_board_name()}')
    print(f'Serial number:    {dev.get_serial_number()}')
    print(f'Firmware release: {dev.get_firmware_release()}')
    num_ch = dev.get_num_channels()
    print(f'Num channels:     {num_ch}')
    print(f'Control mode:     {dev.get_control_mode()}')
    print(f'Interlock status: {dev.get_interlock_status()}')
    print(f'Interlock mode:   {dev.get_interlock_mode()}')
    print(f'Board alarm:      {dev.get_board_alarm_str()}')

    # Read all channel states
    print()
    for ch in range(num_ch):
        vmon = dev.get_vmon(ch)
        imon = dev.get_imon(ch)
        vset = dev.get_vset(ch)
        iset = dev.get_iset(ch)
        pol = dev.get_polarity(ch)
        status = dev.get_status_str(ch)
        print(f'CH{ch}: Vmon={vmon:.1f} V, Imon={imon:.3f} uA, '
              f'Vset={vset:.1f} V, Iset={iset:.1f} uA, '
              f'pol={pol}, status={status}')

    # Set a safe test voltage on channel 0 and monitor
    ch = 0
    monitor_time = 10  # seconds to monitor after setting voltage
    print(f'\nSetting channel {ch} to {TEST_VOLTAGE} V ...')
    dev.set_vset(ch, TEST_VOLTAGE)
    dev.set_on(ch)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < monitor_time:
        vmon = dev.get_vmon(ch)
        imon = dev.get_imon(ch)
        status = dev.get_status_str(ch)
        print(f'  t={time.perf_counter()-t0:5.1f} s: Vmon={vmon:.1f} V, '
              f'Imon={imon:.3f} uA, status={status}')
        time.sleep(1)

    # Turn off and monitor ramp-down
    print(f'\nTurning channel {ch} off ...')
    dev.set_off(ch)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < monitor_time:
        vmon = dev.get_vmon(ch)
        status = dev.get_status_str(ch)
        print(f'  t={time.perf_counter()-t0:5.1f} s: Vmon={vmon:.1f} V, '
              f'status={status}')
        if vmon == 0.0 and 'RDW' not in status:
            break
        time.sleep(1)

except DeviceError as e:
    print('DeviceError:', e.value)
finally:
    dev.close()
