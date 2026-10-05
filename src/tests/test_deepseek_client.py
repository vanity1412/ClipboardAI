import unittest
from unittest.mock import Mock
from deepseek_client import DeepSeekClient
from coding_prompt import CODE_PROMPT


class DeepSeekTests(unittest.TestCase):
    def client(self, content="answer", finish="stop"):
        client = DeepSeekClient({"DEEPSEEK_API_KEY": "test"})
        client.post = Mock(return_value={"choices": [{
            "finish_reason": finish,
            "message": {"content": content, "reasoning_content": "not copied"}}]})
        return client

    def test_pro_max_returns_final_answer_only(self):
        client = self.client()
        self.assertEqual(client.ask("code question"), ("answer", "DeepSeek V4 Pro Max"))
        args = client.post.call_args
        self.assertEqual(args.args[1]["model"], "deepseek-v4-pro")
        self.assertEqual(args.args[1]["reasoning_effort"], "max")
        self.assertEqual(args.kwargs["key"], "test")

    def test_truncated_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client(finish="length").ask("question")

    def test_reasoning_without_final_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client(content="").ask("question")

    def test_session_analysis_and_stream_settings_are_forwarded(self):
        import threading
        client = self.client()
        client.cancel_event = threading.Event()
        client.config.update(DEEPSEEK_MAX_TOKENS="4096", DEEPSEEK_TIMEOUT_S="900")
        history = [{"role": "user", "content": "original problem"}, {"role": "assistant", "content": "previous code"}]
        client.ask("fix sample", history, "analysis first")
        args = client.post.call_args
        self.assertTrue(args.args[1]["stream"])
        self.assertEqual(args.args[1]["messages"][0]["content"], "analysis first")
        self.assertEqual(args.args[1]["messages"][1:3], history)
        self.assertEqual(args.args[1]["max_tokens"], 4096)
        self.assertEqual(args.args[2], 900)

    def test_flash_uses_exact_model_id_with_max_thinking(self):
        client = self.client()
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        self.assertEqual(client.ask("code"), ("answer", "DeepSeek Flash Max"))
        body = client.post.call_args.args[1]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "max")
        self.assertEqual(body["messages"], [{"role": "user", "content": "code"}])

    def test_programming_instruction_is_explicit_and_flexible(self):
        client = self.client()
        client.ask('Explain this Python code', instruction=CODE_PROMPT)
        self.assertEqual(client.post.call_args.args[1]['messages'][0], dict(role='system', content=CODE_PROMPT))
        self.assertNotIn('GNU C++17', CODE_PROMPT)
        self.assertNotIn('Chỉ trả code', CODE_PROMPT)

    def test_repair_request_keeps_new_system_prompt_and_history(self):
        client = self.client()
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        history = [{"role": "user", "content": "original full problem"},
                   {"role": "assistant", "content": "previous code"}]
        client.ask("TLE on large tests", history)
        messages = client.post.call_args.args[1]["messages"]
        self.assertFalse(any(m["role"] == "system" for m in messages))
        self.assertEqual(messages[:2], history)

    def test_screenshot_extracts_text_with_image_not_code_prompt(self):
        client = self.client('{"complete":true,"text":"N <= 1000"}')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        self.assertEqual(client.extract_problem(b"synthetic image"), "N <= 1000")
        messages = client.post.call_args.args[1]["messages"]
        self.assertIn("Không giải bài", messages[0]["content"])
        image = messages[-1]["content"][1]["image_url"]
        self.assertEqual(image["detail"], "original")
        self.assertTrue(image["url"].startswith("data:image/png;base64,"))
        self.assertEqual(client.post.call_args.args[1]["response_format"], {"type": "json_object"})

    def test_fenced_json_is_accepted_without_guessing_text(self):
        client = self.client('```json\n{"complete":true,"text":"full statement"}\n```')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        self.assertEqual(client.extract_problem(b"image"), "full statement")

    def test_incomplete_or_invalid_extraction_is_not_solved(self):
        for response in ('{"complete":false,"text":"missing constraints"}', 'bad JSON', '[]', '{"complete":true,"text":""}'):
            client = self.client(response)
            client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
            with self.assertRaises(RuntimeError):
                client.extract_problem(b"image")

    def test_pro_rejects_screenshot_without_api_call(self):
        client = self.client()
        with self.assertRaises(RuntimeError):
            client.extract_problem(b"image")
        client.post.assert_not_called()

    def test_partial_reading_keeps_text_and_specific_missing_parts(self):
        client = self.client('{"readable":true,"complete":false,"text":"Input N","missing":["giới hạn N"]}')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        result = client.read_problem(b"image")
        self.assertEqual(result["text"], "Input N")
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing"], ["giới hạn N"])

    def test_next_capture_receives_previous_draft(self):
        client = self.client('{"complete":true,"text":"full merged statement","missing":[]}')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        result = client.read_problem(b"image", "previous input and constraints")
        content = client.post.call_args.args[1]["messages"][-1]["content"]
        self.assertIn("previous input and constraints", content[0]["text"])
        self.assertTrue(result["complete"])

    def test_uncertain_numbers_do_not_get_sent_as_complete_problem(self):
        client = self.client('{"complete":true,"text":"N <= [KHÔNG ĐỌC RÕ]","missing":[]}')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        self.assertFalse(client.read_problem(b"image")["complete"])

    def test_complete_flag_with_missing_fields_does_not_trigger_solver(self):
        client = self.client('{"complete":true,"text":"problem","missing":["output format"]}')
        client.config["DEEPSEEK_MODEL"] = "deepseek-flash"
        self.assertFalse(client.read_problem(b"image")["complete"])
