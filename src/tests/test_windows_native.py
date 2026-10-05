import unittest
from unittest.mock import Mock, patch

from windows_native import AIClient, WindowsApp, fingerprint, window_long_setter


class ClientTests(unittest.TestCase):
    def test_x86_and_x64_use_correct_window_long_export(self):
        user = Mock()
        self.assertIs(window_long_setter(user, 4), user.SetWindowLongW)
        self.assertIs(window_long_setter(user, 8), user.SetWindowLongPtrW)

    def test_ollama_only_even_if_legacy_key_is_present(self):
        client = AIClient({'DEEPSEEK_API_KEY': 'unused-test-key', 'OLLAMA_MODEL': 'qwen2.5-coder:7b'})
        client.post = Mock(return_value={'message': {'content': 'local'}})
        self.assertEqual(client.ask('question'), ('local', 'Ollama'))
        self.assertEqual(client.post.call_count, 1)
        self.assertTrue(client.post.call_args.args[0].startswith('http://127.0.0.1:11434/'))

    def test_local_answer(self):
        client = AIClient({'OLLAMA_MODEL': 'qwen2.5-coder:7b'})
        client.post = Mock(return_value={'message': {'content': 'local'}})
        self.assertEqual(client.ask('question'), ('local', 'Ollama'))
        self.assertEqual(client.post.call_count, 1)

    def test_qwen3_enables_thinking_and_sufficient_output_budget(self):
        client = AIClient({'OLLAMA_MODEL': 'qwen3:8b'})
        client.post = Mock(return_value={'message': {'content': 'verified code'}})
        self.assertEqual(client.ask('hard problem'), ('verified code', 'Ollama'))
        request = client.post.call_args.args[1]
        self.assertTrue(request['think'])
        self.assertEqual(request['options']['num_ctx'], 8192)
        self.assertGreaterEqual(request['options']['num_predict'], 8192)

    def test_truncated_ollama_answer_is_not_copied(self):
        client = AIClient({'OLLAMA_MODEL': 'qwen3:8b'})
        client.post = Mock(return_value={'message': {'content': 'partial code'}, 'done_reason': 'length'})
        with self.assertRaises(ValueError):
            client.ask('hard problem')

    def test_legacy_coder_does_not_use_thinking(self):
        client = AIClient({'OLLAMA_MODEL': 'qwen2.5-coder:7b'})
        client.post = Mock(return_value={'message': {'content': 'code'}})
        client.ask('problem')
        self.assertFalse(client.post.call_args.args[1]['think'])


class ClipboardTests(unittest.TestCase):
    def app(self):
        import queue
        app = WindowsApp.__new__(WindowsApp)
        app.enabled, app.self_test = True, False
        app.own_sequence = 12
        app.output_fingerprints = set()
        app.last_input_fingerprint = None
        app.config = {}
        app.jobs = queue.Queue(maxsize=1)
        app.tooltip = Mock()
        app.read_clipboard = Mock(return_value=('AI answer', 12))
        return app

    def test_own_answer_never_enqueued(self):
        app = self.app()
        app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_copy_does_not_replace_pending_explicit_request(self):
        app = self.app()
        app.jobs.put(('old question', 10, fingerprint('old question')))
        app.read_clipboard.return_value = ('new question', 15)
        app.clipboard_changed()
        self.assertEqual(app.jobs.get_nowait(), ('old question', 10, fingerprint('old question')))

    def test_answer_republished_with_new_sequence_never_enqueued(self):
        app = self.app()
        app.output_fingerprints.add(fingerprint('AI answer'))
        for sequence in range(20, 120):
            app.read_clipboard.return_value = ('AI answer\r\n', sequence)
            app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_older_answer_is_still_suppressed_after_new_question(self):
        app = self.app()
        app.output_fingerprints.add(fingerprint('previous answer'))
        app.last_input_fingerprint = fingerprint('another question')
        app.read_clipboard.return_value = ('previous answer', 99)
        app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_copy_events_never_send_without_hotkey(self):
        app = self.app()
        app.read_clipboard.return_value = ('question', 20)
        app.clipboard_changed()
        for sequence in range(21, 100):
            app.read_clipboard.return_value = ('question', sequence)
            app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_new_copy_after_output_waits_for_f8(self):
        app = self.app()
        app.output_fingerprints.add(fingerprint('AI answer'))
        app.last_input_fingerprint = fingerprint('old question')
        app.read_clipboard.return_value = ('new question', 20)
        app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_output_is_registered_before_clipboard_write_event(self):
        import ctypes
        app = self.app()
        app.hwnd = 1
        app.user, app.kernel = Mock(), Mock()
        app.user.GetClipboardSequenceNumber.side_effect = [20, 21]
        app.user.OpenClipboard.return_value = True
        buffer = ctypes.create_string_buffer(100)
        app.kernel.GlobalAlloc.return_value = 123
        app.kernel.GlobalLock.return_value = ctypes.addressof(buffer)
        app.read_clipboard.return_value = ('AI answer', 22)

        def notification(*args):
            app.clipboard_changed()
            self.assertTrue(app.jobs.empty())
            return 123

        app.user.SetClipboardData.side_effect = notification
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('AI answer', 20))
        self.assertIn(fingerprint('AI answer'), app.output_fingerprints)

    def test_sequence_change_with_different_text_is_discarded(self):
        app = self.app()
        app.hwnd = 1
        app.user = Mock()
        app.user.OpenClipboard.return_value = True
        app.user.GetClipboardSequenceNumber.return_value = 99
        app.read_open_clipboard_text = Mock(return_value='new copy')
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('old answer', 20, fingerprint('old question')))
        app.user.EmptyClipboard.assert_not_called()

    def test_secret_not_sent(self):
        app = self.app()
        app.read_clipboard.return_value = ('sk-' + 'a' * 32, 15)
        with patch('windows_native.log_event'):
            app.clipboard_changed()
        self.assertTrue(app.jobs.empty())

    def test_stale_response_does_not_overwrite_new_copy(self):
        app = self.app()
        app.hwnd = 1
        app.user = Mock()
        app.user.OpenClipboard.return_value = True
        app.user.GetClipboardSequenceNumber.return_value = 25
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('answer to older copy', 20))
        app.user.EmptyClipboard.assert_not_called()
        app.user.SetClipboardData.assert_not_called()
        app.user.CloseClipboard.assert_called_once()


if __name__ == '__main__':
    unittest.main()
