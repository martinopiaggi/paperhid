"""Combined status semantics — shipped core.status_merge."""
from __future__ import annotations

import unittest

from core.status_merge import (
    classify_keyboard_status,
    classify_pointer_status,
    format_status_report,
    merge_exit_codes,
)


class TestClassifyPointer(unittest.TestCase):
    def test_not_installed_exit_zero(self):
        p = classify_pointer_status(
            home_present=False, unit_file_present=False, is_active=None
        )
        self.assertEqual(p.state, "not_installed")
        self.assertTrue(p.is_absent)
        self.assertEqual(p.exit_code, 0)

    def test_home_only_after_uninstall_is_staged_exit_zero(self):
        """uninstall --pointer keeps ~/.paperpointer; that is residual, not unhealthy."""
        p = classify_pointer_status(
            home_present=True, unit_file_present=False, is_active=False
        )
        self.assertEqual(p.state, "staged")
        self.assertTrue(p.is_absent)
        self.assertEqual(p.exit_code, 0)
        self.assertEqual(merge_exit_codes(0, p), 0)

    def test_active_healthy(self):
        p = classify_pointer_status(
            home_present=True, unit_file_present=True, is_active=True
        )
        self.assertEqual(p.state, "active")
        self.assertEqual(p.exit_code, 0)

    def test_unit_present_inactive_nonzero(self):
        p = classify_pointer_status(
            home_present=True, unit_file_present=True, is_active=False
        )
        self.assertEqual(p.state, "inactive")
        self.assertEqual(p.exit_code, 1)

    def test_failed_nonzero(self):
        p = classify_pointer_status(
            home_present=True,
            unit_file_present=True,
            is_active=False,
            is_failed=True,
        )
        self.assertEqual(p.state, "failed")
        self.assertEqual(p.exit_code, 1)


class TestClassifyKeyboard(unittest.TestCase):
    def test_absent_exit_zero(self):
        k = classify_keyboard_status(service_present=False, service_active=None)
        self.assertEqual(k.state, "not_installed")
        self.assertEqual(k.exit_code, 0)

    def test_active_healthy(self):
        k = classify_keyboard_status(service_present=True, service_active=True)
        self.assertEqual(k.state, "active")
        self.assertEqual(k.exit_code, 0)

    def test_installed_inactive_nonzero(self):
        k = classify_keyboard_status(service_present=True, service_active=False)
        self.assertEqual(k.state, "inactive")
        self.assertEqual(k.exit_code, 1)
        self.assertEqual(
            merge_exit_codes(
                k,
                classify_pointer_status(
                    home_present=False, unit_file_present=False, is_active=None
                ),
            ),
            1,
        )

    def test_failed_nonzero(self):
        k = classify_keyboard_status(
            service_present=True, service_active=False, service_failed=True
        )
        self.assertEqual(k.state, "failed")
        self.assertEqual(k.exit_code, 1)


class TestMergeExit(unittest.TestCase):
    def test_absent_pointer_does_not_fail(self):
        p = classify_pointer_status(
            home_present=False, unit_file_present=False, is_active=None
        )
        self.assertEqual(merge_exit_codes(0, p), 0)

    def test_staged_pointer_does_not_fail(self):
        p = classify_pointer_status(
            home_present=True, unit_file_present=False, is_active=None
        )
        self.assertEqual(merge_exit_codes(0, p), 0)

    def test_keyboard_failure_wins(self):
        p = classify_pointer_status(
            home_present=False, unit_file_present=False, is_active=None
        )
        self.assertEqual(merge_exit_codes(3, p), 3)

    def test_inactive_pointer_fails_combined(self):
        p = classify_pointer_status(
            home_present=True, unit_file_present=True, is_active=False
        )
        self.assertEqual(merge_exit_codes(0, p), 1)

    def test_report_has_sections(self):
        p = classify_pointer_status(
            home_present=False, unit_file_present=False, is_active=None
        )
        k = classify_keyboard_status(service_present=True, service_active=True)
        text = format_status_report(["service_installed: True"], p, keyboard=k)
        self.assertIn("=== keyboard ===", text)
        self.assertIn("=== pointer ===", text)
        self.assertIn("not_installed", text)
        self.assertIn("state: active", text)


if __name__ == "__main__":
    unittest.main()
