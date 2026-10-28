# -*- coding: utf-8 -*-
"""Tests of `usbtmc.usbtmc_clear` against fake pyusb devices: the
instrument is found by its serial number among devices with the same
IDs (passing over those another process holds), the class requests go
in the specification's order (INITIATE_CLEAR, CHECK_CLEAR_STATUS while
pending, then the bulk endpoints' halts cleared), and the interface is
released whatever happens. No USB device or libusb needed.
"""

import pytest
import usb.core
import usb.util

from amodevices import usbtmc
from amodevices.dev_exceptions import DeviceError

ADDRESS = 'USB0::0x1313::0x8078::P0045954::INSTR'


class FakeEndpoint:
    bmAttributes = 0x02         # bulk

    def __init__(self, address):
        self.bEndpointAddress = address
        self.wMaxPacketSize = 64
        self.reads = 0

    def read(self, size, timeout):
        self.reads += 1
        return b''


class FakeInterface(list):
    bInterfaceClass, bInterfaceSubClass, bInterfaceNumber = 0xFE, 0x03, 0


class FakeDevice:
    iSerialNumber = 3

    def __init__(self, serial, answers=()):
        self.serial = serial
        self.ep_in, self.ep_out = FakeEndpoint(0x81), FakeEndpoint(0x02)
        self.intf = FakeInterface([self.ep_in, self.ep_out])
        # Per request code, the answers in turn
        self.answers = {code: list(a) for code, a in dict(answers).items()}
        self.calls = []

    def get_active_configuration(self):
        return [self.intf]

    def ctrl_transfer(self, request_type, request, value, index, length,
                      timeout=None):
        assert (request_type, value, index) == (0xA1, 0, 0)
        self.calls.append(request)
        return self.answers[request].pop(0)

    def clear_halt(self, ep):
        self.calls.append(('clear_halt', ep))


@pytest.fixture
def fake_usb(monkeypatch):
    """The devices `libusb_package.find` returns and what `usb.util` did
    to them."""
    import libusb_package
    state = {'devices': [], 'claimed': [], 'released': [], 'disposed': []}

    def find(find_all, idVendor, idProduct):
        assert find_all and (idVendor, idProduct) == (0x1313, 0x8078)
        return iter(state['devices'])

    def get_string(dev, index):
        if dev.serial is None:
            # Held by another process: its descriptor cannot be read
            raise usb.core.USBError('The device has no langid')
        return dev.serial
    monkeypatch.setattr(libusb_package, 'find', find)
    monkeypatch.setattr(usb.util, 'get_string', get_string)
    monkeypatch.setattr(usb.util, 'claim_interface',
                        lambda dev, n: state['claimed'].append(dev))
    monkeypatch.setattr(usb.util, 'release_interface',
                        lambda dev, n: state['released'].append(dev))
    monkeypatch.setattr(usb.util, 'dispose_resources',
                        lambda dev: state['disposed'].append(dev))
    return state


def test_usb_address_ids():
    assert usbtmc.usb_address_ids(ADDRESS) == (0x1313, 0x8078, 'P0045954')
    # pyvisa-py's own spelling of the same resource
    assert usbtmc.usb_address_ids(
        'USB0::4883::32888::P0045954::0::INSTR') == (0x1313, 0x8078, 'P0045954')
    assert usbtmc.usb_address_ids('TCPIP0::192.168.50.22::inst0::INSTR') is None
    assert usbtmc.usb_address_ids('USB0::0x1313::0x8078::P0045954::RAW') is None
    assert usbtmc.usb_address_ids('not a resource') is None


def test_the_clear_goes_in_order_and_clears_both_halts(fake_usb):
    dev = FakeDevice('P0045954', {
        usbtmc.INITIATE_CLEAR: [[0x01]],
        # Pending twice, the second time asking for a Bulk-IN read
        usbtmc.CHECK_CLEAR_STATUS: [[0x02, 0x00], [0x02, 0x01], [0x01, 0x00]]})
    fake_usb['devices'] = [dev]
    assert usbtmc.usbtmc_clear(ADDRESS) >= 0.
    assert dev.calls == [usbtmc.INITIATE_CLEAR] + 3*[usbtmc.CHECK_CLEAR_STATUS] + [
        ('clear_halt', 0x81), ('clear_halt', 0x02)]
    assert dev.ep_in.reads == 1
    assert fake_usb['claimed'] == fake_usb['released'] == fake_usb['disposed'] == [dev]


def test_the_instrument_is_found_by_its_serial_number(fake_usb):
    held, other = FakeDevice(None), FakeDevice('P0035092')
    dev = FakeDevice('P0045954', {usbtmc.INITIATE_CLEAR: [[0x01]],
                                  usbtmc.CHECK_CLEAR_STATUS: [[0x01, 0x00]]})
    fake_usb['devices'] = [held, other, dev]
    usbtmc.usbtmc_clear(ADDRESS)
    assert held.calls == other.calls == []
    assert dev.calls[0] == usbtmc.INITIATE_CLEAR


def test_a_refused_clear_raises_and_releases_the_interface(fake_usb):
    dev = FakeDevice('P0045954', {usbtmc.INITIATE_CLEAR: [[0x80]]})
    fake_usb['devices'] = [dev]
    with pytest.raises(DeviceError, match='INITIATE_CLEAR answered 0x80'):
        usbtmc.usbtmc_clear(ADDRESS)
    assert fake_usb['released'] == fake_usb['disposed'] == [dev]


def test_a_missing_instrument_or_resource_raises(fake_usb):
    fake_usb['devices'] = [FakeDevice('P0035092')]
    with pytest.raises(DeviceError, match='no USB device'):
        usbtmc.usbtmc_clear(ADDRESS)
    with pytest.raises(DeviceError, match='no USB INSTR resource'):
        usbtmc.usbtmc_clear('TCPIP0::192.168.50.22::inst0::INSTR')
