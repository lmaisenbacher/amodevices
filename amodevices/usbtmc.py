# -*- coding: utf-8 -*-
"""
USBTMC device clear through libusb, for USB instruments reached through
pyvisa-py.

A process that ends between a query and the read of its reply leaves the
reply in the instrument's output queue. The next connection then reads
that reply as the answer to its first query, and every later reply comes
one behind. A device clear discards it. pyvisa-py implements `clear` for
LAN and GPIB resources but not for USB (it returns "operation not
supported"), so this module sends the USBTMC class requests itself, over
the same pyusb/libusb stack pyvisa-py uses (USBTMC 1.00, 4.2.1.6
INITIATE_CLEAR and 4.2.1.7 CHECK_CLEAR_STATUS, followed by
CLEAR_FEATURE(ENDPOINT_HALT) on the Bulk-OUT endpoint, which the
specification requires after a clear, and on the Bulk-IN endpoint).

The clear also frees an interface left waiting for a Bulk-IN transfer
that the host abandoned: after a read that times out with nothing queued,
an instrument may time out every following write until it is cleared or
unplugged.

It must run while no other handle holds the instrument: before pyvisa
opens it.
"""

import logging
import time

import pyvisa.rname

from .dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

#: USBTMC interface class and subclass
USBTMC_CLASS, USBTMC_SUBCLASS = 0xFE, 0x03
#: USBTMC class requests (USBTMC 1.00, table 15)
INITIATE_CLEAR, CHECK_CLEAR_STATUS = 5, 6
#: USBTMC_status values (USBTMC 1.00, table 16)
STATUS_SUCCESS, STATUS_PENDING = 0x01, 0x02
#: bmRequestType of a class request to an interface, device to host
_CLASS_INTERFACE_IN = 0xA1
#: How often CHECK_CLEAR_STATUS is asked while the clear is pending
_CHECK_TRIES, _CHECK_WAIT_S = 50, 0.05


def usb_address_ids(address):
    """(vendor ID, product code, serial number) of the USB INSTR VISA
    resource name `address` (str), or None for any other resource."""
    try:
        parsed = pyvisa.rname.parse_resource_name(address)
    except Exception:
        return None
    if not isinstance(parsed, pyvisa.rname.USBInstr):
        return None
    return (int(parsed.manufacturer_id, 0), int(parsed.model_code, 0),
            parsed.serial_number)


def _find(vendor, product, serial):
    """The pyusb device with these IDs and serial number, or None.
    Devices whose serial number cannot be read (held by another process)
    are passed over."""
    import libusb_package
    import usb.core
    import usb.util
    for dev in libusb_package.find(find_all=True, idVendor=vendor,
                                   idProduct=product):
        try:
            if usb.util.get_string(dev, dev.iSerialNumber) == serial:
                return dev
        except (usb.core.USBError, ValueError):
            pass
    return None


def usbtmc_clear(address, timeout_ms=5000):
    """Send a USBTMC device clear to the USB instrument named by the VISA
    resource name `address` (str) and clear the halt of its bulk
    endpoints. Returns the time it took (s). Raises `DeviceError` when
    the instrument is not found or the clear fails."""
    import usb.core
    import usb.util
    ids = usb_address_ids(address)
    if ids is None:
        raise DeviceError(f'USBTMC clear: \'{address}\' is no USB INSTR'
                          ' resource')
    dev = _find(*ids)
    if dev is None:
        raise DeviceError(f'USBTMC clear: no USB device for \'{address}\'')
    t0 = time.perf_counter()
    try:
        intf = next(
            (i for i in dev.get_active_configuration()
             if i.bInterfaceClass == USBTMC_CLASS
             and i.bInterfaceSubClass == USBTMC_SUBCLASS), None)
        if intf is None:
            raise DeviceError(f'USBTMC clear: \'{address}\' has no USBTMC'
                              ' interface')
        bulk = [ep for ep in intf if usb.util.endpoint_type(ep.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK]
        ep_in = next(ep for ep in bulk if usb.util.endpoint_direction(
            ep.bEndpointAddress) == usb.util.ENDPOINT_IN)
        number = intf.bInterfaceNumber
        usb.util.claim_interface(dev, number)
        try:
            status = dev.ctrl_transfer(_CLASS_INTERFACE_IN, INITIATE_CLEAR,
                                       0, number, 1, timeout=timeout_ms)
            if status[0] != STATUS_SUCCESS:
                raise DeviceError(
                    f'USBTMC clear of \'{address}\': INITIATE_CLEAR answered'
                    f' 0x{status[0]:02x}')
            for _ in range(_CHECK_TRIES):
                status = dev.ctrl_transfer(
                    _CLASS_INTERFACE_IN, CHECK_CLEAR_STATUS, 0, number, 2,
                    timeout=timeout_ms)
                if status[0] != STATUS_PENDING:
                    break
                if status[1] & 1:
                    # bmClear.D0: the device asks the host to read the
                    # Bulk-IN endpoint while it clears (4.2.1.7)
                    try:
                        ep_in.read(ep_in.wMaxPacketSize, 100)
                    except usb.core.USBError:
                        pass
                time.sleep(_CHECK_WAIT_S)
            if status[0] != STATUS_SUCCESS:
                raise DeviceError(
                    f'USBTMC clear of \'{address}\': CHECK_CLEAR_STATUS'
                    f' answered 0x{status[0]:02x}')
            for ep in bulk:
                dev.clear_halt(ep.bEndpointAddress)
        finally:
            usb.util.release_interface(dev, number)
    except usb.core.USBError as e:
        raise DeviceError(f'USBTMC clear of \'{address}\': {e}') from e
    finally:
        usb.util.dispose_resources(dev)
    return time.perf_counter() - t0
