import sys
import unittest
from unittest.mock import patch

import preflight


class PreflightModeTests(unittest.TestCase):
    def test_default_mode_is_offline_and_skips_external_checks(self):
        with patch.object(preflight, "check_source_files", return_value=[]), \
            patch.object(preflight, "check_config_contract", return_value=[]), \
            patch.object(preflight, "check_dependencies", return_value=[]), \
            patch.object(
                preflight,
                "check_external_tools",
                side_effect=AssertionError("external checks should be skipped"),
            ) as external_check, \
            patch.object(
                preflight,
                "check_adb_devices",
                side_effect=AssertionError("ADB checks should be skipped"),
            ) as adb_check, \
            patch.object(sys, "argv", ["preflight.py"]):
            self.assertEqual(preflight.main(), 0)

        external_check.assert_not_called()
        adb_check.assert_not_called()

    def test_integration_mode_checks_tools_without_requiring_a_device(self):
        with patch.object(preflight, "check_source_files", return_value=[]), \
            patch.object(preflight, "check_config_contract", return_value=[]), \
            patch.object(preflight, "check_dependencies", return_value=[]), \
            patch.object(preflight, "check_external_tools", return_value=[]), \
            patch.object(preflight, "check_adb_devices", return_value=[]) as adb_check, \
            patch.object(sys, "argv", ["preflight.py", "--integration"]):
            self.assertEqual(preflight.main(), 0)

        adb_check.assert_called_once_with(require_device=False)

    def test_require_device_implies_integration_and_strict_device_check(self):
        with patch.object(preflight, "check_source_files", return_value=[]), \
            patch.object(preflight, "check_config_contract", return_value=[]), \
            patch.object(preflight, "check_dependencies", return_value=[]), \
            patch.object(preflight, "check_external_tools", return_value=[]), \
            patch.object(preflight, "check_adb_devices", return_value=[]) as adb_check, \
            patch.object(sys, "argv", ["preflight.py", "--require-device"]):
            self.assertEqual(preflight.main(), 0)

        adb_check.assert_called_once_with(require_device=True)


if __name__ == "__main__":
    unittest.main()