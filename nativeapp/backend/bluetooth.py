"""On-device Bluetooth API — local transport over shared implementation."""
from shared import bluetooth as _bt
from shared.transport import LocalTransport

_t = LocalTransport()


def prepare_adapter():
    return _bt.ensure_adapter_ready(_t, timeout=30, gate_wifi=False)


def read_device_keyboard():
    return _bt.read_device_keyboard(_t)


def verify_device_state(cfg):
    return _bt.verify_device_state(_t, cfg)


def scan_devices(timeout=15):
    return _bt.scan_devices(_t, timeout=timeout, gate_wifi=True)


def pair(mac):
    return _bt.pair(_t, mac)


def trust(mac):
    return _bt.trust(_t, mac)


def remove(mac):
    return _bt.remove(_t, mac)


def connect(mac):
    return _bt.connect(_t, mac)


def get_connection_status(mac):
    return _bt.get_connection_status(_t, mac)


def get_device_name(mac):
    return _bt.get_device_name(_t, mac)


def pair_interactive(mac, passkey_callback=None, timeout=60):
    return _bt.pair_interactive(
        _t, mac, passkey_callback=passkey_callback, timeout=timeout
    )


def pair_and_connect(mac, old_mac=None, passkey_callback=None):
    return _bt.pair_and_connect(
        _t, mac, old_mac=old_mac, passkey_callback=passkey_callback, pre_scan=True
    )


def reconnect_now(mac=None):
    return _bt.reconnect_now(_t, mac=mac)
