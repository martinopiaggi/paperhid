"""Unit tests for device detection and Paper Pro safety gates.

These drive the real shipped functions with stub SSH clients — no network.
Live device checks live in tests/test_live_paper_pro.py (opt-in).
"""
import os
import unittest
from unittest.mock import MagicMock

from core import device as device_mod
from core import layout_patcher


class FakeSSH:
    """Minimal SSH stand-in returning canned exec output."""

    def __init__(self, exec_map=None, default=("", "", 0)):
        self.exec_map = exec_map or {}
        self.default = default
        self.calls = []
        self.uploads = []  # (kind, remote_path, payload)

    def exec(self, cmd, timeout=30):
        self.calls.append(cmd)
        for key, val in self.exec_map.items():
            if key in cmd:
                return val
        return self.default

    def upload_string(self, content, remote_path):
        self.uploads.append(("string", remote_path, content))

    def upload_bytes(self, data, remote_path):
        self.uploads.append(("bytes", remote_path, data))


PAPER_PRO_DETECT_OUT = (
    "reMarkable Ferrari\x00\n"
    "reMarkable Ferrari\n"
    "imx8mm-ferrari\n"
    "Linux imx8mm-ferrari 6.12.49 aarch64 GNU/Linux\n"
    'IMG_VERSION="3.28.0.162"\n'
)

MOVE_DETECT_OUT = (
    "reMarkable Chill\x00\n"
    "reMarkable Chill\n"
    "imx8mm-chill\n"
    "Linux imx8mm-chill 6.1.0 aarch64 GNU/Linux\n"
    'IMG_VERSION="3.26.0.0"\n'
)


class TestDeviceDetect(unittest.TestCase):
    def test_detect_paper_pro_ferrari(self):
        ssh = FakeSSH(default=(PAPER_PRO_DETECT_OUT, "", 0))
        info = device_mod.detect(ssh)
        self.assertEqual(info["model"], device_mod.MODEL_PAPER_PRO)
        self.assertIn("Paper Pro", info["label"])
        self.assertTrue(info["supports_layout_patch"])
        self.assertTrue(info["supports_bt_keyboard_service"])
        self.assertIn("3.28", info.get("img_version", ""))
        self.assertTrue(device_mod.is_paper_pro(info))
        self.assertFalse(device_mod.is_move(info))

    def test_detect_move_chill(self):
        ssh = FakeSSH(default=(MOVE_DETECT_OUT, "", 0))
        info = device_mod.detect(ssh)
        self.assertEqual(info["model"], device_mod.MODEL_MOVE)
        self.assertEqual(info["label"], "reMarkable Move")
        self.assertTrue(info["supports_layout_patch"])
        self.assertTrue(device_mod.is_move(info))

    def test_detect_hostname_ferrari_only(self):
        out = "something\nimx8mm-ferrari\nLinux x\n"
        ssh = FakeSSH(default=(out, "", 0))
        info = device_mod.detect(ssh)
        self.assertEqual(info["model"], device_mod.MODEL_PAPER_PRO)

    def test_detect_ssh_failure_is_unknown_and_safe(self):
        class BoomSSH:
            def exec(self, cmd, timeout=30):
                raise OSError("ssh down")

        info = device_mod.detect(BoomSSH())
        self.assertEqual(info["model"], device_mod.MODEL_UNKNOWN)
        self.assertFalse(info["supports_layout_patch"])

    def test_classify_move_requires_remarkable_with_move(self):
        # "move" alone must not match random blobs
        self.assertEqual(device_mod._classify("random move only", ""), device_mod.MODEL_UNKNOWN)
        self.assertEqual(
            device_mod._classify("remarkable move tablet", ""),
            device_mod.MODEL_MOVE,
        )


def _synth_keymap_blob(offset=0x1000, entry_count=120, pad_before=0x1000):
    """Build a fake libepaper-like blob with a Move-style US keymap table."""
    import struct as st

    data = bytearray(b"\x00" * (offset + entry_count * 16 + 64))

    def put_entry(i, kc, uni, qt, mod=0, flags=0, special=0):
        o = offset + i * 16
        st.pack_into("<H", data, o, kc)
        st.pack_into("<H", data, o + 2, uni)
        st.pack_into("<I", data, o + 4, qt)
        data[o + 8] = mod
        data[o + 9] = flags
        st.pack_into("<H", data, o + 10, special)
        st.pack_into("<I", data, o + 12, 0)

    i = 0
    # Escape first (Move / Paper Pro US table signature)
    put_entry(i, 1, 0xFFFF, 0x01000000, 0)
    i += 1
    # Digit row plain+shift
    for d in range(10):
        kc = d + 2
        plain = ord("1") + d if d < 9 else ord("0")
        put_entry(i, kc, plain, plain, 0)
        i += 1
        put_entry(i, kc, 0x21 + d, 0x21 + d, 1)
        i += 1
    # Letters plain+shift
    for kc, uni in layout_patcher._US_PLAIN_LETTERS.items():
        upper = uni - 0x20 if uni >= 0x61 else uni
        put_entry(i, kc, uni, upper, 0, flags=1, special=2)
        i += 1
        put_entry(i, kc, upper, upper, 1, flags=1, special=2)
        i += 1
    # US apostrophe + brackets (with Move stock dead on [ optional)
    put_entry(i, 26, 0x00B4, 0x01001251, 0, special=1)  # dead acute
    i += 1
    put_entry(i, 26, 0x60, 0x01001250, 1, special=1)  # dead grave
    i += 1
    put_entry(i, 27, 0x5D, 0x5D, 0)
    i += 1
    put_entry(i, 27, 0x7D, 0x7D, 1)
    i += 1
    put_entry(i, 40, 0x27, 0x27, 0)  # '
    i += 1
    put_entry(i, 40, 0x22, 0x22, 1)  # "
    i += 1
    put_entry(i, 41, 0x60, 0x60, 0)
    i += 1
    put_entry(i, 41, 0x7E, 0x7E, 1)
    i += 1
    while i < entry_count:
        kc = 51 + (i % 4)
        if kc > 54:
            kc = 51
        put_entry(i, kc, 0x2C, 0x2C, i % 2)
        i += 1
    return bytes(data), offset, entry_count


class TestLayoutGate(unittest.TestCase):
    def test_apply_layout_refused_on_unknown(self):
        unknown_out = "SomeBoard\nother\nhost\nLinux x\n"
        ssh = FakeSSH(default=(unknown_out, "", 0))
        with self.assertRaises(RuntimeError) as ctx:
            layout_patcher.apply_layout(ssh, "us")
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "refusing" in msg or "not a supported" in msg,
            msg=str(ctx.exception),
        )
        joined = " ".join(ssh.calls)
        self.assertNotIn("remount", joined)

    def test_apply_layout_allowed_on_paper_pro_gate(self):
        """PP passes the model gate; fails later without a real binary."""
        ssh = FakeSSH(
            exec_map={
                "device-tree": (PAPER_PRO_DETECT_OUT, "", 0),
                "test -f": ("", "", 1),
            },
            default=("", "", 0),
        )
        ssh.download_bytes = MagicMock(side_effect=RuntimeError("stop-after-gate"))
        with self.assertRaises(RuntimeError) as ctx:
            layout_patcher.apply_layout(ssh, "us")
        # Gate passed → backup/download attempted
        self.assertIn("stop-after-gate", str(ctx.exception))

    def test_apply_layout_force_bypasses_check(self):
        ssh = FakeSSH(
            exec_map={"test -f": ("", "", 1)},
            default=("", "", 0),
        )
        ssh.download_bytes = MagicMock(side_effect=RuntimeError("stop-after-gate"))
        with self.assertRaises(RuntimeError) as ctx:
            layout_patcher.apply_layout(ssh, "us", force=True)
        self.assertIn("stop-after-gate", str(ctx.exception))

    def test_find_keymap_in_synthetic_blob(self):
        blob, off, count = _synth_keymap_blob(offset=0x2000, entry_count=200)
        found = layout_patcher.find_keymap_offset(blob)
        self.assertIsNotNone(found)
        found_off, found_count = found
        self.assertEqual(found_off, off)
        self.assertGreaterEqual(found_count, 80)

    def test_patch_us_intl_sets_dead_acute_on_apostrophe(self):
        from tools.generate_qmap import Key_Dead_Acute, DEAD_KEY_UNICODE
        import struct as st

        # Synth blob already has US ' / " on keycode 40 (Move-style table)
        blob, off, count = _synth_keymap_blob(offset=0x1000, entry_count=120)
        patched, n = layout_patcher._patch_binary(
            blob, "us_intl", keymap_offset=off, entry_count=count
        )
        self.assertGreaterEqual(n, 20)
        found_dead = False
        for i in range(count):
            o = off + i * 16
            kc = st.unpack_from("<H", patched, o)[0]
            mod = patched[o + 8]
            if kc == 40 and mod == 0:
                uni = st.unpack_from("<H", patched, o + 2)[0]
                qt = st.unpack_from("<I", patched, o + 4)[0]
                special = st.unpack_from("<H", patched, o + 10)[0]
                self.assertEqual(qt, Key_Dead_Acute)
                self.assertEqual(uni, DEAD_KEY_UNICODE[Key_Dead_Acute])
                self.assertEqual(special, 1)
                found_dead = True
                break
        self.assertTrue(found_dead, "apostrophe key should be dead acute")

    def test_patch_italian_direct_accents_on_us_keys(self):
        """Italian = Windows-style: US [ becomes è, Shift+[ becomes é."""
        import struct as st

        blob, off, count = _synth_keymap_blob(offset=0x2000, entry_count=120)
        patched, n = layout_patcher._patch_binary(
            blob, "it", keymap_offset=off, entry_count=count
        )
        self.assertGreaterEqual(n, 20)
        got = {}
        for i in range(count):
            o = off + i * 16
            kc = st.unpack_from("<H", patched, o)[0]
            if kc != 26:
                continue
            mod = patched[o + 8]
            if mod in got:
                continue
            uni = st.unpack_from("<H", patched, o + 2)[0]
            special = st.unpack_from("<H", patched, o + 10)[0]
            got[mod] = (uni, special)
        self.assertEqual(got.get(0, (None,))[0], 0x00E8)  # è
        self.assertEqual(got.get(1, (None,))[0], 0x00E9)  # é
        self.assertEqual(got[0][1], 0)  # not a dead key

    def test_us_intl_layout_registered(self):
        from tools.generate_qmap import get_layout_mappings, Key_Dead_Acute

        letters, punct = get_layout_mappings("us_intl")
        self.assertTrue(letters)
        apostrophe = [p for p in punct if p[0] == 40]
        self.assertEqual(len(apostrophe), 1)
        self.assertEqual(apostrophe[0][2], Key_Dead_Acute)


class TestRestartDisplayPrefersXovi(unittest.TestCase):
    """Layout must not plain-start xochitl when XOVI is installed (Settings)."""

    def test_restart_display_uses_xovi_start(self):
        from shared import layout_patcher as shared_lp
        from shared.transport import Transport

        calls = []

        class T(Transport):
            def run(self, cmd, timeout=30):
                calls.append(cmd)
                if "test -x /home/root/xovi/start" in cmd:
                    return "", "", 0
                return "", "", 0

            def read_bytes(self, path):
                raise NotImplementedError

            def write_bytes(self, path, data):
                raise NotImplementedError

        shared_lp.restart_display(T())
        joined = "\n".join(calls)
        self.assertIn("/home/root/xovi/start", joined)
        # Must not only bare-start without attempting XOVI first.
        xovi_i = next(i for i, c in enumerate(calls) if "/home/root/xovi/start" in c)
        bare = [i for i, c in enumerate(calls) if c.strip() == "systemctl start xochitl"]
        self.assertTrue(not bare or bare[0] > xovi_i)

    def test_restart_display_falls_back_without_xovi(self):
        from shared import layout_patcher as shared_lp
        from shared.transport import Transport

        calls = []

        class T(Transport):
            def run(self, cmd, timeout=30):
                calls.append(cmd)
                if "test -x /home/root/xovi/start" in cmd:
                    return "", "", 1
                return "", "", 0

            def read_bytes(self, path):
                raise NotImplementedError

            def write_bytes(self, path, data):
                raise NotImplementedError

        shared_lp.restart_display(T())
        self.assertTrue(any("systemctl start xochitl" in c for c in calls))
        self.assertFalse(any(c.strip() == "/home/root/xovi/start" for c in calls))


class TestServiceResourcesPresent(unittest.TestCase):
    def test_inactive_service_files_still_count_as_installed(self):
        """An inactive unit must not be mistaken for a missing installation."""
        from core import bluetooth

        ssh = FakeSSH(
            exec_map={
                "systemctl is-active": ("inactive\n", "", 3),
                "systemctl is-failed": ("active\n", "", 1),
                "test -f": ("", "", 0),
                "bluetoothctl show": ("Powered: no\n", "", 0),
                "cat /home/root/.paperwriter-keyboard": ("", "", 1),
            }
        )

        state = bluetooth.verify_device_state(ssh, {})

        self.assertTrue(state["service_present"])
        self.assertTrue(state["service_installed"])
        self.assertFalse(state["service_active"])
        self.assertTrue(
            any("/etc/systemd/system/remarkable-bt-keyboard.service" in cmd for cmd in ssh.calls)
        )

    def test_bt_script_and_unit_exist(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(root, "resources", "bt-keyboard.sh")
        lib = os.path.join(root, "resources", "bt-lib.sh")
        resume = os.path.join(root, "resources", "bt-resume.sh")
        sleep_hook = os.path.join(root, "resources", "zz-paperwriter-bt.sh")
        unit = os.path.join(root, "resources", "remarkable-bt-keyboard.service")
        bootstrap_script = os.path.join(root, "resources", "paperwriter-bt-bootstrap.sh")
        bootstrap_unit = os.path.join(root, "resources", "paperwriter-bt-bootstrap.service")
        for path in (
            script, lib, resume, sleep_hook, unit, bootstrap_script, bootstrap_unit
        ):
            self.assertTrue(os.path.isfile(path), path)

        with open(script, "r", encoding="utf-8") as f:
            body = f.read()
        with open(lib, "r", encoding="utf-8") as f:
            lib_body = f.read()
        combined = body + "\n" + lib_body

        self.assertIn("btnxpuart", combined)
        self.assertIn("uhid", combined)
        self.assertIn("UserspaceHID=true", combined)
        self.assertIn("ClassicBondedOnly=false", combined)
        self.assertIn("wake_lock", combined)
        # Shared library is sourced by the long-running service
        self.assertIn("bt-lib.sh", body)
        # Adapter-loss / not-powered recovery in the reconnect loop
        self.assertIn("hci0 missing", body)
        self.assertIn("adapter not powered", body)
        # Paper Pro safety: do not force-remove the NXP driver at runtime
        self.assertNotRegex(combined, r"(?m)^\s*modprobe\s+-r\s+btnxpuart")
        # NXP auto-sleep disable (HCI vendor 0xFC23)
        self.assertIn("0x3f 0x23", combined)

        with open(resume, "r", encoding="utf-8") as f:
            resume_body = f.read()
        self.assertIn("bt-lib.sh", resume_body)
        self.assertIn("pw_init_adapter", resume_body)
        self.assertIn("pw_connect_saved", resume_body)

        with open(sleep_hook, "r", encoding="utf-8") as f:
            hook_body = f.read()
        self.assertIn("bt-resume.sh", hook_body)
        self.assertIn("after", hook_body)

        with open(unit, "r", encoding="utf-8") as f:
            unit_body = f.read()
        self.assertIn("bt-keyboard.sh", unit_body)
        self.assertNotIn("Requires=bluetooth.service", unit_body)

        with open(bootstrap_script, "r", encoding="utf-8") as f:
            boot = f.read()
        self.assertIn("remarkable-bt-keyboard.service", boot)
        self.assertIn("bt-keyboard.sh", boot)
        # Bootstrap must re-seed itself (OTA / overlay recovery)
        self.assertIn("paperwriter-bt-bootstrap.service", boot)
        self.assertIn("zz-paperwriter-bt.sh", boot)
        # Language uses libepaper.so patch, not a Qt drop-in
        self.assertNotIn("paperwriter-keymap.conf", boot)
        self.assertNotIn("QT_QPA_EVDEV", boot)

    def test_service_installer_has_ensure_and_home_paths(self):
        from core import service_installer

        self.assertTrue(hasattr(service_installer, "ensure_installed"))
        self.assertTrue(hasattr(service_installer, "wait_for_controller"))
        self.assertIn("/home/root/.paperwriter", service_installer.SCRIPT_DIR)
        self.assertEqual(
            service_installer.BOOTSTRAP_NAME,
            "paperwriter-bt-bootstrap.service",
        )
        self.assertEqual(service_installer.SLEEP_HOOK_NAME, "zz-paperwriter-bt.sh")
        self.assertIn("system-sleep", service_installer.SLEEP_HOOK_PATH)
        self.assertTrue(hasattr(service_installer, "LIB_SCRIPT_NAME"))
        self.assertTrue(hasattr(service_installer, "RESUME_SCRIPT_NAME"))

    def test_scan_parses_device_lines(self):
        from core import bluetooth

        blob = (
            "Device AA:BB:CC:DD:EE:FF CLVX S\n"
            "Device 11:22:33:44:55:66 Some Phone\n"
            "AA:BB:CC:DD:EE:01\tOther KB\n"
            "Device AA:BB:CC:DD:EE:FF CLVX S again\n"  # dedupe
        )
        devs = bluetooth._parse_device_lines(blob)
        self.assertEqual(len(devs), 3)
        names = {d["name"] for d in devs}
        self.assertIn("CLVX S", names)
        self.assertIn("Other KB", names)
        # MACs normalized to upper
        self.assertTrue(all(d["mac"] == d["mac"].upper() for d in devs))

    def test_find_device_by_name(self):
        from core import bluetooth

        devices = [
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "CLVX S"},
            {"mac": "11:22:33:44:55:66", "name": "Phone"},
        ]
        self.assertEqual(
            bluetooth.find_device_by_name(devices, "clvx")["mac"],
            "AA:BB:CC:DD:EE:FF",
        )
        self.assertIsNone(bluetooth.find_device_by_name(devices, "missing"))
        self.assertIsNone(bluetooth.find_device_by_name(devices, ""))


if __name__ == "__main__":
    unittest.main()
