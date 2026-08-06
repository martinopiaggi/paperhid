"""Live integration tests against a USB-connected reMarkable Paper Pro.

Requires:
  PAPERHID_LIVE=1  (legacy: PAPERWRITER_LIVE / MOVEWRITER_LIVE)
  PAPERHID_PASSWORD (SSH root password; legacy aliases accepted)
  Device at PAPERHID_IP (legacy PAPERWRITER_IP / MOVEWRITER_IP; default 10.11.99.1)

Skip entirely when LIVE is not set, so CI/unit runs stay offline.
"""
import os
import time
import unittest

LIVE = (
    os.environ.get("PAPERHID_LIVE", "") == "1"
    or os.environ.get("PAPERWRITER_LIVE", "") == "1"
    or os.environ.get("MOVEWRITER_LIVE", "") == "1"
)
# Empty string is valid when the tablet accepts blank USB SSH password.
PASSWORD = (
    os.environ.get("PAPERHID_PASSWORD")
    or os.environ.get("PAPERWRITER_PASSWORD")
    or os.environ.get("MOVEWRITER_PASSWORD")
    or ""
)
# When LIVE=1 and neither password env is set, still try empty password.
_PASSWORD_ENV_SET = any(
    k in os.environ
    for k in ("PAPERHID_PASSWORD", "PAPERWRITER_PASSWORD", "MOVEWRITER_PASSWORD")
)
IP = (
    os.environ.get("PAPERHID_IP")
    or os.environ.get("PAPERWRITER_IP")
    or os.environ.get("MOVEWRITER_IP")
    or "10.11.99.1"
)


@unittest.skipUnless(
    LIVE,
    "set PAPERHID_LIVE=1 (optional PAPERHID_PASSWORD; blank USB root ok)",
)
class TestLivePaperPro(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.ssh_client import SSHClient

        cls.ssh = SSHClient()
        # Prefer explicit env password (including empty); else try blank USB root.
        pw = PASSWORD if _PASSWORD_ENV_SET else ""
        cls.ssh.connect(IP, pw, timeout=15)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.ssh.disconnect()
        except Exception:
            pass

    def test_01_identity_is_paper_pro_class(self):
        from core import device as device_mod

        info = device_mod.detect(self.ssh)
        self.assertEqual(info["model"], device_mod.MODEL_PAPER_PRO)
        out, _, code = self.ssh.exec("uname -a")
        self.assertEqual(code, 0)
        self.assertIn("aarch64", out)

    def test_02_service_install_powers_bt(self):
        from core import service_installer, bluetooth, config

        service_installer.install(self.ssh)
        time.sleep(12)
        state = bluetooth.verify_device_state(self.ssh, config.load())
        self.assertTrue(state["service_installed"])
        out, _, _ = self.ssh.exec("lsmod")
        self.assertIn("btnxpuart", out)
        self.assertIn("uhid", out)
        out, _, _ = self.ssh.exec("bluetoothctl show", timeout=15)
        self.assertIn("Powered: yes", out)

    def test_03_scan_or_report_nxp_broken(self):
        """Scan returns a list, or raises the known Paper Pro NXP discovery error."""
        from core import bluetooth

        try:
            devices = bluetooth.scan_devices(self.ssh, timeout=8)
            self.assertIsInstance(devices, list)
        except RuntimeError as e:
            msg = str(e).lower()
            self.assertTrue(
                "discovery is broken" in msg
                or "0x2005" in msg
                or "paper pro bluetooth" in msg,
                msg=str(e),
            )

    def test_04_mac_save_and_clear(self):
        from core import service_installer, bluetooth

        fake = "AA:BB:CC:DD:EE:FF"
        service_installer.save_keyboard_mac(self.ssh, fake)
        out, _, code = self.ssh.exec(f"cat {service_installer.KEYBOARD_MAC_PATH}")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip().upper(), fake)
        self.ssh.exec(f"rm -f {service_installer.KEYBOARD_MAC_PATH}")
        # unpair path must not crash for unknown MAC
        bluetooth.remove(self.ssh, fake)

    def test_05_layout_supported(self):
        from core import device as device_mod

        info = device_mod.detect(self.ssh)
        self.assertTrue(info.get("supports_layout_patch"))

    def test_06_libepaper_layout_discover_and_italian(self):
        """Discover keymap, apply Italian, verify [ → è / Shift+[ → é.

        Leaves Italian applied (Windows-style on a US keyboard).
        """
        import struct

        from core import layout_patcher

        if hasattr(self.ssh, "ensure_connected"):
            self.ssh.ensure_connected()

        data = self.ssh.download_bytes(layout_patcher.LIBEPAPER_PATH)
        found = layout_patcher.find_keymap_offset(data)
        self.assertIsNotNone(found, "expected discoverable keymap in libepaper.so")
        _off, count = found
        self.assertGreaterEqual(count, 80)

        layout_patcher.apply_layout(self.ssh, "it")
        if hasattr(self.ssh, "ensure_connected"):
            self.ssh.ensure_connected()

        out, _, code = self.ssh.exec(f"cat {layout_patcher.LAYOUT_FILE}", timeout=5)
        self.assertEqual(code, 0)
        self.assertEqual((out or "").strip(), "it")

        patched = self.ssh.download_bytes(layout_patcher.LIBEPAPER_PATH)
        off2, count2 = layout_patcher.find_keymap_offset(patched)
        levels = {}
        for i in range(count2):
            o = off2 + i * 16
            kc = struct.unpack_from("<H", patched, o)[0]
            if kc != 26:
                continue
            mod = patched[o + 8]
            if mod in levels:
                continue
            levels[mod] = struct.unpack_from("<H", patched, o + 2)[0]
        self.assertEqual(levels.get(0), 0x00E8, "plain [ should be è")
        self.assertEqual(levels.get(1), 0x00E9, "Shift+[ should be é")


if __name__ == "__main__":
    unittest.main()
