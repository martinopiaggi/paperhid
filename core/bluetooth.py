"""Desktop Bluetooth API — SSH transport over shared implementation."""
from shared import bluetooth as _bt
from shared.transport import as_transport

_parse_device_lines = _bt._parse_device_lines
find_device_by_name = _bt.find_device_by_name


def verify_device_state(ssh, cfg):
    return _bt.verify_device_state(as_transport(ssh), cfg)


def disable_nxp_autosleep(ssh):
    return _bt.disable_nxp_autosleep(as_transport(ssh))


def ensure_adapter_ready(ssh, timeout=30, gate_wifi=False):
    # Keep service scripts seeded when talking over SSH.
    try:
        from core import service_installer
        service_installer.ensure_installed(ssh)
    except Exception:
        pass
    return _bt.ensure_adapter_ready(
        as_transport(ssh), timeout=timeout, gate_wifi=gate_wifi
    )


def probe_radio_scan_health(ssh):
    return _bt.probe_radio_scan_health(as_transport(ssh))


def scan_devices(
    ssh, timeout=5, gate_wifi=True, until_mac=None, until_name=None
):
    try:
        from core import service_installer
        service_installer.ensure_installed(ssh)
    except Exception:
        pass
    return _bt.scan_devices(
        as_transport(ssh),
        timeout=timeout,
        gate_wifi=gate_wifi,
        until_mac=until_mac,
        until_name=until_name,
    )


def pair(ssh, mac):
    return _bt.pair(as_transport(ssh), mac)


def trust(ssh, mac):
    return _bt.trust(as_transport(ssh), mac)


def remove(ssh, mac):
    return _bt.remove(as_transport(ssh), mac)


def connect(ssh, mac):
    return _bt.connect(as_transport(ssh), mac)


def get_connection_status(ssh, mac):
    return _bt.get_connection_status(as_transport(ssh), mac)


def device_known(ssh, mac):
    return _bt.device_known(as_transport(ssh), mac)


def pair_interactive(ssh, mac, passkey_callback=None, timeout=60):
    return _bt.pair_interactive(
        as_transport(ssh), mac, passkey_callback=passkey_callback, timeout=timeout
    )


def get_device_info(ssh, mac):
    return _bt.get_device_info(as_transport(ssh), mac)


def get_device_name(ssh, mac):
    return _bt.get_device_name(as_transport(ssh), mac)


def classify_device_role(info_text="", name=""):
    return _bt.classify_device_role(info_text, name)


normalize_mac = _bt.normalize_mac


def pair_and_connect(
    ssh,
    mac,
    old_mac=None,
    passkey_callback=None,
    pre_scan=True,
    replace_previous=False,
):
    try:
        from core import service_installer
        service_installer.ensure_installed(ssh)
    except Exception:
        pass
    return _bt.pair_and_connect(
        as_transport(ssh),
        mac,
        old_mac=old_mac,
        passkey_callback=passkey_callback,
        pre_scan=pre_scan,
        replace_previous=replace_previous,
    )
