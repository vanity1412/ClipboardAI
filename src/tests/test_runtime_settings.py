import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_settings import load_runtime_settings, validated_preferences, save_preferences
from windows_native import WindowsApp, request_error_message


class RuntimeSettingsTests(unittest.TestCase):
    def test_f4_choice_is_validated_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            config = {}
            load_runtime_settings(config, path)
            self.assertEqual(config['F4_INPUT'], 'image')
            save_preferences(path, {'F4_INPUT': 'image', 'SESSION_MAX_CHARS': '64000'})
            restored = {}
            self.assertEqual(load_runtime_settings(restored, path), [])
            self.assertEqual(restored['F4_INPUT'], 'image')
            self.assertEqual(restored['SESSION_MAX_CHARS'], '64000')
            valid, invalid = validated_preferences({'F4_INPUT': 'unsupported'})
            self.assertEqual(valid, {})
            self.assertEqual(invalid, ['F4_INPUT'])

    def test_bad_numbers_fall_back_and_file_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "preferences.json"
            payload = json.dumps({"DEEPSEEK_MAX_TOKENS": "abc", "DEEPSEEK_TIMEOUT_S": "30.5",
                                  "SESSION_MAX_CHARS": "NaN", "MIRAI_TIMEOUT_S": True,
                                  "MIRAI_MAX_TOKENS": -1})
            path.write_text(payload, encoding="utf-8")
            config = {}
            issues = load_runtime_settings(config, path)
            app = WindowsApp.__new__(WindowsApp)
            app.config, app.is_deepseek = config, True
            self.assertEqual(app.token_limit(), 393216)
            self.assertEqual(app.request_timeout(), 0)
            self.assertEqual(app.context_capacity(), 16000)
            self.assertEqual(len(issues), 5)
            self.assertEqual(path.read_text(encoding="utf-8"), payload)

    def test_null_array_and_invalid_json_are_safe(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "preferences.json"
            for payload in ("[]", "null", "42", "not JSON"):
                path.write_text(payload, encoding="utf-8")
                config = {}
                self.assertEqual(load_runtime_settings(config, path), ["preferences_file"])
                self.assertEqual(config["DEEPSEEK_TIMEOUT_S"], "0")

    def test_limits_nan_infinity_boolean_and_out_of_range_are_rejected(self):
        for value in (None, True, False, {}, [], "abc", "nan", float("inf"),
                      -1, 393217, "1e400", "9" * 100):
            valid, invalid = validated_preferences({"DEEPSEEK_MAX_TOKENS": value})
            self.assertEqual(valid, {})
            self.assertEqual(invalid, ["DEEPSEEK_MAX_TOKENS"])

    def test_zero_deadline_and_integral_decimal_strings_are_preserved(self):
        valid, invalid = validated_preferences({"DEEPSEEK_TIMEOUT_S": "0.0",
                                              "DEEPSEEK_MAX_TOKENS": 393216.0,
                                              "MIRAI_MAX_TOKENS": 0,
                                              "MIRAI_TIMEOUT_S": "3600.0"})
        self.assertFalse(invalid)
        self.assertEqual(valid["DEEPSEEK_TIMEOUT_S"], "0")
        self.assertEqual(valid["DEEPSEEK_MAX_TOKENS"], "393216")
        self.assertEqual(valid["MIRAI_TIMEOUT_S"], "3600")

    def test_bad_base_settings_do_not_poison_defaults_or_other_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "preferences.json"
            path.write_text('{"SESSION_MAX_CHARS":64000}', encoding="utf-8")
            config = {"OLLAMA_MODEL": None, "DEEPSEEK_TIMEOUT_S": "Infinity", "DEEPSEEK_API_KEY": "private-test"}
            issues = load_runtime_settings(config, path)
            self.assertEqual(config["OLLAMA_MODEL"], "auto")
            self.assertEqual(config["DEEPSEEK_TIMEOUT_S"], "0")
            self.assertEqual(config["SESSION_MAX_CHARS"], "64000")
            self.assertEqual(config["DEEPSEEK_API_KEY"], "private-test")
            self.assertEqual(issues, ["DEEPSEEK_TIMEOUT_S", "OLLAMA_MODEL"])

    def test_save_is_atomic_and_failed_replace_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "preferences.json"
            path.write_text('{"DEEPSEEK_TIMEOUT_S":"0"}', encoding="utf-8")
            original = path.read_text()
            with patch.object(Path, "replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    save_preferences(path, {"DEEPSEEK_TIMEOUT_S": "900"})
            self.assertEqual(path.read_text(), original)
            save_preferences(path, {"DEEPSEEK_TIMEOUT_S": "0", "DEEPSEEK_API_KEY": "must not persist"})
            self.assertEqual(json.loads(path.read_text()), {"DEEPSEEK_TIMEOUT_S": "0"})

    def test_timeout_error_reporting_cannot_crash_on_bad_setting(self):
        for value in ("NaN", "Infinity", "abc", None, {}):
            self.assertIn("không đặt hạn", request_error_message(TimeoutError(), value))
