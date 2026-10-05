import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cloud_client import CloudClient, DEFAULT_MODELS, load_cloud_config
from coding_prompt import CODE_PROMPT
from deepseek_client import EXTRACT_PROMPT
from windows_native import WindowsApp


class CloudTests(unittest.TestCase):
    def client(self, model="claude-fable-5.1", content="int main(){}", finish="stop"):
        client = CloudClient(dict(SELECTED_MODEL=model, MODEL_CHOICES=DEFAULT_MODELS,
            DEEPSEEK_MODEL="deepseek-flash", DEEPSEEK_API_KEY="deepseek-test",
            MIRAI_API_KEY="mirai-test", MIRAI_BASE_URL="https://api.miraiapi.com"))
        client.post = Mock(return_value={"choices": [{"finish_reason": finish, "message": {"content": content}}]})
        return client

    def test_default_routes_only_to_deepseek(self):
        client = self.client("deepseek-flash")
        client.ask("problem")
        args = client.post.call_args
        self.assertEqual(args.args[0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(args.kwargs["key"], "deepseek-test")
        self.assertEqual(args.args[1]["model"], "deepseek-flash")

    def test_three_exact_mirai_models_keep_prompt_history_and_separate_key(self):
        history = [{"role": "user", "content": "problem"}, {"role": "assistant", "content": "old code"}]
        for model, _ in DEFAULT_MODELS[1:]:
            client = self.client(model)
            client.cancel_event = threading.Event()
            client.config.update(MIRAI_MAX_TOKENS="8192", MIRAI_TIMEOUT_S="3000")
            answer, label = client.ask("fix failing test", history)
            args = client.post.call_args
            self.assertEqual(args.args[0], "https://api.miraiapi.com/v1/chat/completions")
            self.assertEqual(args.kwargs["key"], "mirai-test")
            body = args.args[1]
            self.assertEqual(body["model"], model)
            self.assertFalse(any(m["role"] == "system" for m in body["messages"]))
            self.assertEqual(body["messages"][:2], history)
            self.assertEqual(body["max_tokens"], 8192)
            self.assertTrue(body["stream"])
            self.assertNotIn("thinking", body)
            self.assertNotIn("reasoning_effort", body)
            self.assertEqual(args.args[2], 3000)
            self.assertIn(model, label)

    def test_mirai_f4_uses_selected_model_and_previous_draft(self):
        client = self.client(content='{"readable":true,"complete":true,"text":"full statement","missing":[]}')
        result = client.read_problem(b"synthetic image", "previous constraints")
        self.assertTrue(result["complete"])
        body = client.post.call_args.args[1]
        self.assertEqual(body["messages"][0]["content"], EXTRACT_PROMPT)
        self.assertIn("previous constraints", body["messages"][-1]["content"][0]["text"])
        self.assertEqual(body["messages"][-1]["content"][1]["type"], "image_url")
        self.assertNotIn("response_format", body)

    def test_sonnet_and_opus_use_fable_for_image_but_selected_model_for_code(self):
        for model in ("claude-sonnet-5.5", "claude-opus-5.5"):
            client = self.client(model, content='{"complete":true,"text":"statement","missing":[]}')
            client.read_problem(b"image")
            self.assertEqual(client.post.call_args.args[1]["model"], "claude-fable-5.1")
            self.assertEqual(client.config["SELECTED_MODEL"], model)
            client.post.return_value["choices"][0]["message"]["content"] = "int main(){}"
            client.ask("statement")
            self.assertEqual(client.post.call_args.args[1]["model"], model)

    def test_image_reader_error_restores_selected_model(self):
        client = self.client("claude-sonnet-5.5")
        client.post.side_effect = RuntimeError("AI HTTP 429")
        with self.assertRaises(RuntimeError):
            client.read_problem(b"image")
        self.assertEqual(client.config["SELECTED_MODEL"], "claude-sonnet-5.5")

    def test_incomplete_empty_and_malformed_responses_never_succeed(self):
        for content, finish in (("partial code", "length"), ("", "stop"), ("code", None), ("blocked", "content_filter")):
            with self.assertRaises(RuntimeError):
                self.client(content=content, finish=finish).ask("problem")
        client = self.client()
        for data in ({}, {"choices": []}, {"choices": [None]}):
            client.post.return_value = data
            with self.assertRaises(RuntimeError):
                client.ask("problem")

    def test_missing_key_and_unknown_model_do_not_send(self):
        client = self.client()
        client.config.pop("MIRAI_API_KEY")
        with self.assertRaises(RuntimeError):
            client.ask("problem")
        client.post.assert_not_called()
        client.config["SELECTED_MODEL"] = "unexpected-model"
        with self.assertRaises(RuntimeError):
            client.ask("problem")
        client.post.assert_not_called()

    def test_startup_is_deepseek_even_with_mirai_config_and_slot_default(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mirai_config.json"
            path.write_text(json.dumps({"model": "opus", "env": {"ANTHROPIC_AUTH_TOKEN": "test", "ANTHROPIC_BASE_URL": "https://api.miraiapi.com/v1", "API_TIMEOUT_MS": "3000000"}}), encoding="utf-8")
            config = load_cloud_config(root, {"DEEPSEEK_API_KEY": "test-d"})
            self.assertEqual(config["SELECTED_MODEL"], "deepseek-flash")
            self.assertEqual(config["MIRAI_TIMEOUT_S"], "3000")
            self.assertEqual(config["MIRAI_BASE_URL"], "https://api.miraiapi.com")
            self.assertEqual(len(config["MODEL_CHOICES"]), 4)

    def test_bad_config_does_not_break_deepseek_or_forward_key(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mirai_config.json"
            for payload in ("not JSON", '[]', '{"env":{"ANTHROPIC_BASE_URL":"https://other.example","ANTHROPIC_AUTH_TOKEN":"test"}}'):
                path.write_text(payload, encoding="utf-8")
                config = load_cloud_config(root, {})
                self.assertIn("MIRAI_CONFIG_ERROR", config)
                self.assertNotIn("MIRAI_API_KEY", config)
                self.assertEqual(config["SELECTED_MODEL"], "deepseek-flash")

    def test_openai_compatible_config_preserves_endpoint_and_exact_model_ids(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mirai_config.json"
            path.write_text(json.dumps({"provider": "OpenAI Compatible", "base_url": "https://api.miraiapi.com/v1", "api_key": "test", "timeout_seconds": 3000, "models": [m for m, _ in DEFAULT_MODELS[1:]]}), encoding="utf-8")
            config = load_cloud_config(root, {})
            self.assertEqual(config["MIRAI_API_KEY"], "test")
            self.assertEqual(config["MIRAI_BASE_URL"], "https://api.miraiapi.com")
            self.assertEqual(config["MODEL_CHOICES"], DEFAULT_MODELS)
            self.assertEqual(config["SELECTED_MODEL"], "deepseek-flash")

    def test_invalid_openai_model_list_cannot_break_tray(self):
        with tempfile.TemporaryDirectory() as root:
            for models in ([], [None], ["deepseek-flash"], "not a list", ["claude-a b"]):
                (Path(root) / "mirai_config.json").write_text(json.dumps({"models": models}), encoding="utf-8")
                self.assertIn("MIRAI_CONFIG_ERROR", load_cloud_config(root, {}))

    def test_model_change_cancels_without_resetting_history(self):
        app = WindowsApp.__new__(WindowsApp)
        app.config = {"MODEL_CHOICES": DEFAULT_MODELS, "SELECTED_MODEL": "deepseek-flash"}
        app.is_deepseek = True
        app.session = Mock()
        app.session.messages = [{"role": "user", "content": "original"}]
        app.cancel_request = Mock()
        app.set_text = Mock()
        app.tooltip = Mock()
        app.refresh_panel = Mock()
        app.select_model(402)
        app.cancel_request.assert_called_once()
        self.assertEqual(app.config["SELECTED_MODEL"], "claude-fable-5.1")
        self.assertEqual(app.session.messages, [{"role": "user", "content": "original"}])
        app.session.reset.assert_not_called()
        self.assertEqual(app.request_timeout(), 3000)
        self.assertEqual(app.token_limit(), 0)
        app.select_model(401)
        self.assertEqual(app.config["SELECTED_MODEL"], "deepseek-flash")
        self.assertEqual(app.request_timeout(), 0)
        self.assertEqual(app.token_limit(), 393216)
