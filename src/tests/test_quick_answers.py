import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from answer_policy import answer_instruction, short_tooltip, tray_result
from chat_modes import CHAT, ANALYSIS, CODING
from coding_prompt import CODE_PROMPT
from runtime_settings import load_runtime_settings, save_preferences, validated_preferences
from session_images import SessionImages
from session_state import Session
from windows_native import WindowsApp


class QuickAnswerTests(unittest.TestCase):
    def app(self, root):
        app = WindowsApp.__new__(WindowsApp)
        app.session = Session(Path(root) / 'session.json')
        app.session.commit([dict(role='user', content='Old question'),
                            dict(role='assistant', content='Old answer')], 'Old answer')
        app.config = {'BACKEND': 'DeepSeek', 'DEEPSEEK_MODEL': 'deepseek-flash',
                      'SESSION_MAX_CHARS': '48000', 'F4_INPUT': 'image', 'ANSWER_STYLE': 'short'}
        app.is_deepseek = app.enabled = True
        app.busy = app.network_busy = False
        app.current_id = 0
        app.hwnd = 123
        app.user = Mock()
        app.user.GetForegroundWindow.return_value = 456
        app.client = Mock()
        app.cancel_event = threading.Event()
        app.jobs = queue.Queue(maxsize=1)
        app.results = queue.Queue()
        app.images = SessionImages()
        app.images.add(app.session.active_id, b'old-image')
        app.output_fingerprints = set()
        app.started = None
        app.state = 'Ready'
        app.last_image = app.pending_image = None
        app.tooltip = app.refresh_panel = app.set_text = Mock()
        app.read_clipboard = Mock(return_value=('New question', 12))
        return app

    def test_screenshot_starts_independent_job_and_repeat_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            old = app.session.active_id
            app.send_screenshot()
            new = app.session.active_id
            self.assertNotEqual(new, old)
            job = app.jobs.queue[0]
            self.assertEqual(job['memory_messages'], [])
            self.assertEqual(job['history'], [])
            self.assertEqual(app.images.get(new), [])
            self.assertEqual(app.images.get(old), [b'old-image'])
            self.assertEqual(job['sequence'], 12)
            self.assertNotIn('New question', job['text'])
            app.send_screenshot()
            self.assertEqual(app.current_id, job['id'])
            self.assertEqual(app.jobs.qsize(), 1)
            self.assertEqual(app.session.active_id, new)

    def test_shift_f9_keeps_session_history_and_images_without_clipboard_text(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.config['F4_INPUT'] = 'image_clipboard'
            old = app.session.active_id
            with patch('windows_native.log_event'):
                app.window_proc(app.hwnd, 0x0312, 214, 0)
            job = app.jobs.queue[0]
            self.assertEqual(app.session.active_id, old)
            self.assertEqual(job['history'], app.session.messages)
            self.assertEqual(job['memory_messages'], app.session.messages)
            self.assertEqual(app.images.get(old), [b'old-image'])
            self.assertEqual(job['screenshot_hwnd'], 456)
            self.assertNotIn('New question', job['text'])
            self.assertEqual(job['sequence'], 12)  # Baseline for safe auto-copy only.
            app.send_screenshot(append=True)
            self.assertEqual(app.jobs.qsize(), 1)
            self.assertEqual(app.current_id, job['id'])

    def test_shift_f9_needs_prior_question_and_registers_without_repeat(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.session.new_problem()
            app.send_screenshot(append=True)
            self.assertTrue(app.jobs.empty())
            self.assertIn('Chưa có câu hỏi', app.state)
            app.hotkeys, app.hotkey_errors = [], []
            app.user.RegisterHotKey.return_value = True
            app.register_hotkeys()
            app.user.RegisterHotKey.assert_any_call(app.hwnd, 214, 0x4004, 0x78)
            self.assertEqual(len(app.hotkeys), 10)

    def test_f8_deduplicates_but_f9_preserves_context(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.send_clipboard()
            ident = app.session.active_id
            job = app.jobs.queue[0]
            app.send_clipboard()
            self.assertEqual(app.current_id, job['id'])
            self.assertEqual(app.jobs.qsize(), 1)
            app.busy = False
            app.jobs.get_nowait()
            app.session.commit([dict(role='user', content='New question'),
                                dict(role='assistant', content='New answer')], 'New answer')
            app.read_clipboard.return_value = ('Explain this', 13)
            app.reply_clipboard()
            self.assertEqual(app.session.active_id, ident)
            self.assertEqual(app.jobs.queue[0]['memory_messages'], app.session.messages)

    def test_numbered_choices_policy_and_provider_independent_modes(self):
        for mode in (CHAT, ANALYSIS):
            instruction = answer_instruction(mode)
            self.assertIn('không có nhãn', instruction)
            self.assertIn('1, 2, 3, 4', instruction)
            self.assertIn('không tự gán A/B/C/D', instruction)
            self.assertIn('Chưa xác định', instruction)
        self.assertEqual(answer_instruction(CODING), CODE_PROMPT)
        self.assertEqual(answer_instruction(CHAT, 'free'), '')

    def test_style_preference_survives_save_and_bad_style_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'preferences.json'
            save_preferences(path, {'ANSWER_STYLE': 'choices'})
            config = {}
            load_runtime_settings(config, path)
            self.assertEqual(config['ANSWER_STYLE'], 'choices')
            self.assertEqual(validated_preferences({'ANSWER_STYLE': 'bad'}), ({}, ['ANSWER_STYLE']))

    def test_auto_copy_upgrade_and_explicit_opt_out_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            path.write_text(json.dumps(dict(version=3, mode=CHAT, auto_copy=False)), encoding='utf-8')
            session = Session(path)
            self.assertTrue(session.auto_copy)
            session.auto_copy = False
            session.copy_modes[str(CHAT)] = False
            session.save()
            self.assertFalse(Session(path).auto_copy)
            session.set_mode(ANALYSIS)
            self.assertTrue(session.auto_copy)
            session.set_mode(CHAT)
            self.assertFalse(session.auto_copy)

    def test_tooltip_utf16_bounds_and_current_stream_does_not_show_old_answer(self):
        text = short_tooltip('Đáp án ' + '😀' * 100)
        self.assertLessEqual(len(text.encode('utf-16-le')), 254)
        self.assertTrue(text.endswith('…'))
        self.assertEqual(tray_result('Ready', '1. 2\n2. A'), '1. 2\n2. A')
        self.assertEqual(tray_result('Working', 'OLD', True, 'NEW'), 'Đang xử lý…')
        self.assertNotIn('OLD', tray_result('Working', 'OLD', True, 'NEW'))

    def test_hover_event_decodes_version4_and_hides_on_leave(self):
        app = WindowsApp.__new__(WindowsApp)
        app.show_tray_result = Mock()
        app.hide_tray_result = Mock()
        app.menu = Mock()
        app.tray_version4 = True
        app.window_proc(1, 0x8001, 0, (1 << 16) | 0x406)
        app.show_tray_result.assert_called_once()
        app.window_proc(1, 0x8001, 0, (1 << 16) | 0x407)
        app.hide_tray_result.assert_called_once()
        app.window_proc(1, 0x8001, 0, (1 << 16) | 0x7B)
        app.menu.assert_called_once()

    def test_retry_hotkey_never_opens_panel(self):
        app = WindowsApp.__new__(WindowsApp)
        app.busy = False
        app.resend_session = Mock()
        app.show_panel = Mock()
        with patch('windows_native.log_event'):
            app.window_proc(1, 0x0312, 213, 0)
        app.resend_session.assert_called_once()
        app.show_panel.assert_not_called()

    def test_done_auto_copies_once_and_stale_result_does_not_copy(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.pending_read = app.self_test = False
            app.update_notice, app.show_completion = Mock(), Mock()
            app.write_clipboard = Mock(return_value=True)
            app.started = time.monotonic()
            app.current_id = 2
            app.busy = True
            app.pending_write = None
            job = dict(id=2, mode=CHAT, action='chat', text='Question', sequence=12, digest=b'test')
            turns = [dict(role='user', content='Question'), dict(role='assistant', content='1. 2')]
            app.results.put(('done', dict(job, id=1), 'stale', turns))
            with patch('windows_native.log_event'):
                app.tick()
                app.write_clipboard.assert_not_called()
                app.results.put(('done', job, '1. 2', turns))
                app.tick()
            app.write_clipboard.assert_called_once_with('1. 2', 12, b'test')
            self.assertIsNone(app.pending_write)
            self.assertEqual(app.session.last_answer, '1. 2')


if __name__ == '__main__':
    unittest.main()
