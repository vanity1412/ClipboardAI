import asyncio
import unittest
from unittest.mock import Mock, patch

from cloud_client import CloudClient, DEFAULT_MODELS
from deepseek_client import DeepSeekClient
from windows_native import AIClient, AIResponseError, WindowsApp, request_error_message


class ResponseLimitTests(unittest.TestCase):
    def client(self):
        client = DeepSeekClient({"DEEPSEEK_MODEL": "deepseek-flash", "DEEPSEEK_API_KEY": "test"})
        client.post = Mock(return_value={"choices": [{"finish_reason": "stop", "message": {"content": "int main(){}"}}]})
        return client

    def test_new_defaults_use_api_maximum_and_no_client_deadline(self):
        client = self.client()
        with patch("deepseek_client.log_event"):
            client.ask("synthetic problem")
        self.assertEqual(client.post.call_args.args[1]["max_tokens"], 393216)
        self.assertEqual(client.post.call_args.args[2], 0)

    def test_zero_token_setting_means_deepseek_api_maximum(self):
        client = self.client()
        client.config.update(DEEPSEEK_MAX_TOKENS="0", DEEPSEEK_TIMEOUT_S="0")
        with patch("deepseek_client.log_event"):
            client.ask("problem")
        self.assertEqual(client.post.call_args.args[1]["max_tokens"], 393216)

    def test_explicit_smaller_limits_are_still_respected(self):
        client = self.client()
        client.config.update(DEEPSEEK_MAX_TOKENS="4096", DEEPSEEK_TIMEOUT_S="3600")
        with patch("deepseek_client.log_event"):
            client.ask("problem")
        self.assertEqual(client.post.call_args.args[1]["max_tokens"], 4096)
        self.assertEqual(client.post.call_args.args[2], 3600)

    def test_invalid_limits_are_not_sent_to_api(self):
        for field, value in (("DEEPSEEK_MAX_TOKENS", "393217"), ("DEEPSEEK_MAX_TOKENS", "-1"),
                             ("DEEPSEEK_TIMEOUT_S", "-1"), ("DEEPSEEK_TIMEOUT_S", "nan"),
                             ("DEEPSEEK_TIMEOUT_S", "Infinity"), ("DEEPSEEK_TIMEOUT_S", "abc")):
            client = self.client()
            client.config[field] = value
            with self.assertRaises(AIResponseError) as raised:
                client.ask("problem")
            self.assertEqual(raised.exception.code, "config_limits")
            client.post.assert_not_called()

    def test_empty_truncated_unfinished_and_malformed_are_distinct(self):
        for data, expected in (({"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}, "response_length"),
                               ({"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}, "response_empty"),
                               ({"choices": [{"finish_reason": None, "message": {"content": "partial"}}]}, "response_unfinished"),
                               ({"choices": []}, "response_format")):
            client = self.client()
            client.post.return_value = data
            with patch("deepseek_client.log_event"), self.assertRaises(AIResponseError) as raised:
                client.ask("problem")
            self.assertEqual(raised.exception.code, expected)

    def test_missing_key_is_not_misreported_as_token_exhaustion(self):
        client = self.client()
        client.config.pop("DEEPSEEK_API_KEY")
        with self.assertRaises(AIResponseError) as raised:
            client.ask("problem")
        self.assertEqual(raised.exception.code, "config_key")
        self.assertIn("key", request_error_message(raised.exception, 0))
        client.post.assert_not_called()

    def test_response_log_contains_only_counts_not_sensitive_text(self):
        client = self.client()
        client.post.return_value = {"choices": [{"finish_reason": "stop", "message": {
            "content": "private answer", "reasoning_content": "private reasoning"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 120, "total_tokens": 130,
                      "completion_tokens_details": {"reasoning_tokens": 100},
                      "unknown": "private data"}}
        with patch("deepseek_client.log_event") as log:
            client.ask("private question")
        stats = log.call_args.kwargs
        self.assertEqual(stats["reasoning_tokens"], 100)
        self.assertEqual(stats["completion_tokens"], 120)
        self.assertNotIn("private", str(log.call_args_list))
        self.assertNotIn("DEEPSEEK_API_KEY", str(log.call_args_list))

    def test_timeout_network_and_generic_value_error_are_distinct(self):
        self.assertIn("1800s", request_error_message(TimeoutError(), 1800))
        self.assertIn("1800s", request_error_message(asyncio.TimeoutError(), 1800))
        self.assertIn("không đặt hạn", request_error_message(TimeoutError(), 0))
        self.assertIn("kết nối", request_error_message(OSError(), 0))
        self.assertIn("Dữ liệu/cấu hình", request_error_message(ValueError("sensitive"), 0))
        self.assertNotIn("sensitive", request_error_message(ValueError("sensitive"), 0))

    def test_nonstream_zero_deadline_passes_none_to_socket(self):
        client = AIClient({})
        with patch("windows_native.build_opener") as opener:
            open_url = opener.return_value.open
            open_url.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
            self.assertEqual(client.post("https://example.invalid", {}, 0), {"ok": True})
        self.assertIsNone(open_url.call_args.kwargs["timeout"])

    def test_mirai_auto_omits_app_token_limit_and_accepts_zero_deadline(self):
        client = CloudClient(dict(SELECTED_MODEL="claude-opus-5.5", MODEL_CHOICES=DEFAULT_MODELS,
                                 MIRAI_API_KEY="test", MIRAI_MAX_TOKENS="0", MIRAI_TIMEOUT_S="0"))
        client.post = self.client().post
        client.ask("problem")
        self.assertNotIn("max_tokens", client.post.call_args.args[1])
        self.assertEqual(client.post.call_args.args[2], 0)

    def test_tray_default_values_match_provider_request_limits(self):
        app = WindowsApp.__new__(WindowsApp)
        app.is_deepseek = True
        app.config = {"MODEL_CHOICES": DEFAULT_MODELS, "SELECTED_MODEL": "deepseek-flash"}
        self.assertEqual(app.token_limit(), 393216)
        self.assertEqual(app.token_ceiling(), 393216)
        self.assertEqual(app.request_timeout(), 0)
        app.config["SELECTED_MODEL"] = "claude-opus-5.5"
        self.assertEqual(app.token_limit(), 0)
        self.assertEqual(app.token_ceiling(), 32768)
