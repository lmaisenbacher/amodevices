# -*- coding: utf-8 -*-
"""Tests of `Device.init_visa` against a fake pyvisa resource manager:
a LAN address is opened without enumerating the VISA library's
resources (NI-VISA's enumeration probes the network — two minutes on the
DAQ PC on 2026-09-21 while an instrument held a stale link, with the
server's web UI unreachable meanwhile), a USB address still is, and a
failed open raises `DeviceError`. No VISA library needed.
"""

import pytest

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError
from amodevices.dev_generic import Device


class FakeResource:
    def __init__(self, idn):
        self.idn = idn
        self.timeout = None
        self.closed = False

    def query(self, command):
        assert command == '*IDN?'
        return self.idn + '\n'

    def write(self, command):
        pass

    def close(self):
        self.closed = True


class FakeResourceManager:
    """Counts enumerations; opens any address in `known`."""
    instances = []

    def __init__(self):
        self.enumerations = 0
        self.known = {
            'USB0::0x1AB1::0x0960::DSA8A1234::INSTR': 'RIGOL,DSA815,1,1',
            'TCPIP0::192.168.50.22::inst0::INSTR': 'Rigol Technologies,RSA3030,1,1',
        }
        FakeResourceManager.instances.append(self)

    def list_resources(self):
        self.enumerations += 1
        return tuple(self.known)

    def open_resource(self, address):
        if address not in self.known:
            raise OSError(f'VI_ERROR_RSRC_NFOUND: {address}')
        return FakeResource(self.known[address])


@pytest.fixture
def fake_visa(monkeypatch):
    FakeResourceManager.instances.clear()
    monkeypatch.setattr(dev_generic.pyvisa, 'ResourceManager', FakeResourceManager)
    return FakeResourceManager


def _device(address, **extra):
    return Device({'Device': 'under test', 'Address': address, **extra})


def test_a_lan_address_is_opened_without_enumerating(fake_visa):
    dev = _device('TCPIP0::192.168.50.22::inst0::INSTR', Timeout=2.)
    assert dev.visa_address_is_lan()
    dev.init_visa()
    (rm,) = fake_visa.instances
    assert rm.enumerations == 0
    assert dev.device_connected
    assert dev.visa_resource.timeout == 2000.


def test_a_usb_address_is_still_enumerated(fake_visa):
    dev = _device('USB0::0x1AB1::0x0960::DSA8A1234::INSTR')
    assert not dev.visa_address_is_lan()
    dev.init_visa()
    (rm,) = fake_visa.instances
    assert rm.enumerations == 1
    assert dev.device_connected


def test_a_socket_address_counts_as_lan():
    assert _device('TCPIP0::192.168.50.25::23::SOCKET').visa_address_is_lan()
    assert not _device('GPIB0::12::INSTR').visa_address_is_lan()


def test_an_unreachable_lan_address_raises_at_once(fake_visa):
    dev = _device('TCPIP0::192.168.50.99::inst0::INSTR')
    with pytest.raises(DeviceError, match='Could not connect'):
        dev.init_visa()
    (rm,) = fake_visa.instances
    assert rm.enumerations == 0
    assert dev.visa_resource is None and not dev.device_connected
