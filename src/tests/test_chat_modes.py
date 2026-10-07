import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from chat_modes import CHAT, ANALYSIS, reply_config
from cloud_client import CloudClient, DEFAULT_MODELS
from conversation_memory import prepare_history
from deepseek_client import DeepSeekClient
from session_state import Session


class ChatTests(unittest.TestCase):
    def test_fresh_default_and_legacy_session_migration(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            self.assertEqual((Session(path).mode, Session(path).auto_copy), (CHAT, True))
            path.write_text(json.dumps(dict(mode=1, problem='old problem', auto_copy=True)), encoding='utf-8')
            old = Session(path)
            self.assertEqual((old.mode, old.problem, old.auto_copy), (0, 'old problem', True))
            self.assertTrue(old.set_mode(CHAT))
            self.assertTrue(old.auto_copy)
            old.auto_copy = True
            old.set_mode(ANALYSIS)
            self.assertTrue(old.auto_copy)
            old.set_mode(CHAT)
            self.assertTrue(old.auto_copy)
            self.assertEqual(Session(path).mode, CHAT)

    def test_chat_keeps_full_history_and_summary_on_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            session = Session(path)
            session.messages = [dict(role='user' if i % 2 == 0 else 'assistant', content=str(i)) for i in range(60)]
            session.summary, session.summary_count = 'Previous decisions', 30
            session.commit([dict(role='user', content='next'), dict(role='assistant', content='reply')], 'reply')
            restored = Session(path)
            self.assertEqual(len(restored.messages), 62)
            self.assertEqual((restored.summary, restored.summary_count), ('Previous decisions', 30))
            restored.set_mode(0)
            restored.set_mode(CHAT)
            self.assertEqual(len(Session(path).messages), 62)

    def test_deepseek_no_prompt_fast_chat_and_deep_analysis(self):
        for mode, tokens, timeout in ((CHAT, 8192, 180), (ANALYSIS, 32768, 600)):
            client = DeepSeekClient(reply_config(dict(DEEPSEEK_API_KEY='test', DEEPSEEK_MODEL='deepseek-flash'), mode))
            client.post = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': 'reply'}}]})
            with patch('deepseek_client.log_event'):
                client.ask('Giải thích giúp tôi', [dict(role='assistant', content='earlier')], instruction='')
            body = client.post.call_args.args[1]
            self.assertEqual(body['messages'], [dict(role='assistant', content='earlier'), dict(role='user', content='Giải thích giúp tôi')])
            self.assertEqual((body['max_tokens'], client.post.call_args.args[2]), (tokens, timeout))
            self.assertEqual(body['thinking']['type'], 'disabled' if mode == CHAT else 'enabled')
            if mode == CHAT:
                self.assertNotIn('reasoning_effort', body)
            else:
                self.assertEqual(body['reasoning_effort'], 'high')

    def test_mirai_empty_instruction_does_not_restore_code_prompt(self):
        client = CloudClient(reply_config(dict(SELECTED_MODEL='claude-sonnet-5.5', MODEL_CHOICES=DEFAULT_MODELS,
            MIRAI_API_KEY='test', MIRAI_TIMEOUT_S='3000'), CHAT))
        client.post = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': 'reply'}}]})
        client.ask('Dịch đoạn này', instruction='')
        body = client.post.call_args.args[1]
        self.assertEqual(body['messages'], [dict(role='user', content='Dịch đoạn này')])
        self.assertEqual((body['max_tokens'], client.post.call_args.args[2]), (8192, 180))

    def test_summary_bounds_context_without_mutating_original_messages(self):
        client = Mock()
        client.config = reply_config({}, ANALYSIS)
        client.on_stream = Mock()
        client.ask.return_value = ('User chose option A; preserve 2026 deadline.', 'test')
        messages = [dict(role='user' if i % 2 == 0 else 'assistant', content=str(i) + 'x' * 500) for i in range(40)]
        original = list(messages)
        history, summary, count = prepare_history(client, messages, '', 0, 4000, threading.Event(), Mock())
        self.assertGreater(count, 0)
        self.assertEqual(messages, original)
        self.assertLessEqual(sum(len(m['content']) for m in history), 4040)
        self.assertEqual(history[-2:], messages[-2:])
        self.assertIn('2026', summary)
        self.assertEqual(client.config['REPLY_MODE'], ANALYSIS)
        self.assertTrue(all(call.kwargs['instruction'] == '' for call in client.ask.call_args_list))

    def test_zoo_fallback_keeps_no_prompt_and_chat_limits(self):
        from api_zoo import validate, apply_config, APIHTTPError
        data = validate(dict(profiles=[dict(id=i, name=i, base_url='https://' + i + '.example/v1',
            api_key='test-' + i, model='chat', vision_model='vision', priority=n) for n, i in enumerate(('a', 'b'))]))
        config = reply_config({}, CHAT)
        apply_config(config, data)
        config['SELECTED_MODEL'] = 'zoo:a'
        client = CloudClient(config)
        client.cancel_event = threading.Event()
        client.post = Mock(side_effect=[APIHTTPError(401), {'choices': [{'finish_reason': 'stop', 'message': {'content': 'reply'}}]}])
        answer, provider = client.ask('Một câu hỏi', instruction='')
        self.assertEqual((answer, provider), ('reply', 'b: chat'))
        self.assertEqual(client.post.call_count, 2)
        for call in client.post.call_args_list:
            self.assertEqual(call.args[1]['messages'], [dict(role='user', content='Một câu hỏi')])
            self.assertEqual((call.args[1]['max_tokens'], call.args[2]), (8192, 180))

    def test_cancelled_summary_restores_client_and_does_not_commit(self):
        client = Mock(config={}, on_stream=Mock())
        cancel = threading.Event()
        cancel.set()
        messages = [dict(role='user', content='x' * 600) for _ in range(20)]
        with self.assertRaises(InterruptedError):
            prepare_history(client, messages, '', 0, 3000, cancel, Mock())
        client.ask.assert_not_called()
        self.assertEqual(client.config, {})

    def test_oversized_last_turn_is_summarized_without_deleting_original(self):
        client = Mock(config={})
        client.ask.return_value = ('Preserved user request.', 'mock')
        messages = [dict(role='user', content='x' * 5000)]
        history, summary, count = prepare_history(client, messages, '', 0, 2000, threading.Event(), Mock())
        self.assertEqual(messages[0]['content'], 'x' * 5000)
        self.assertEqual(count, 1)
        self.assertEqual(summary, 'Preserved user request.')
        self.assertLessEqual(sum(len(m['content']) for m in history), 2000)
        self.assertGreater(client.ask.call_count, 1)


if __name__ == '__main__':
    unittest.main()
