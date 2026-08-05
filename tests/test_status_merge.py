"""Combined status semantics — shipped core.status_merge."""
from __future__ import annotations

import unittest

from core.status_merge import (
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

    def test_active_healthy(self):
        p = classify_pointer_status(
            home_present=True, unit_file_present=True, is_active=True
        )
        self.assertEqual(p.state, "active")
        self.assertEqual(p.exit_code, 0)

    def test_installed_inactive_nonzero(self):
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


class TestMergeExit(unittest.TestCase):
    def test_absent_pointer_does_not_fail(self):
        p = classify_pointer_status(
            home_present=False, unit_file_present=False, is_active=None
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
        text = format_status_report(["service_installed: True"], p)
        self.assertIn("=== keyboard ===", text)
        self.assertIn("=== pointer ===", text)
        self.assertIn("not_installed", text)


if __name__ == "__main__":
    unittest.main()
