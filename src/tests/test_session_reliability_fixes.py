"""Storage/context/capture regressions using synthetic data and offline mocks."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from conversation_memory import prepare_history
from prompt_profiles import merged_preferences
from runtime_settings import load_runtime_settings
from screen_capture import CaptureError, capture_foreground_png
from session_images import SessionImages
from session_state import Session
from tests import test_screen_capture as capture_fixtures


class SessionWriteTests(unittest.TestCase):
    def test_noop_save_keeps_file_and_timestamp_without_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            self.assertTrue(session.new_problem('Question'))
            original, timestamp = session.path.read_bytes(), session.updated_at
            with patch.object(Path, 'replace', side_effect=AssertionError('unexpected disk write')):
                self.assertTrue(session.save())
                restored = Session(session.path)
                self.assertTrue(restored.save())
            self.assertEqual(session.path.read_bytes(), original)
            self.assertEqual(session.updated_at, timestamp)

    def test_failed_reset_restores_active_session_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            session.commit([dict(role='user', content='Question'),
                            dict(role='assistant', content='Answer')], 'Answer')
            ident, original = session.active_id, session.path.read_bytes()
            with patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                self.assertFalse(session.reset())
            self.assertEqual(session.active_id, ident)
            self.assertEqual(session.last_answer, 'Answer')
            self.assertEqual(len(session.messages), 2)
            self.assertIn(ident, session._sessions)
            self.assertEqual(session.path.read_bytes(), original)
            self.assertTrue(session.error)

    def test_save_rewrites_missing_or_changed_archive_instead_of_false_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            session.commit([], 'Preserved answer')
            session.path.write_text('{}', encoding='utf-8')
            self.assertTrue(session.save())
            self.assertEqual(Session(session.path).last_answer, 'Preserved answer')
            session.path.unlink()
            self.assertTrue(session.save())
            self.assertEqual(Session(session.path).last_answer, 'Preserved answer')

    def test_async_snapshot_is_immutable_and_reverting_state_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            started, release = threading.Event(), threading.Event()
            write = session._write_archive
            def delayed(*args):
                started.set()
                if not release.wait(3):
                    raise AssertionError('test writer was not released')
                return write(*args)
            try:
                with patch.object(session, '_write_archive', side_effect=delayed):
                    session.mode = 0
                    first = session.save_async()
                    self.assertTrue(started.wait(1))
                    self.assertFalse(first.done())
                    session.mode = 3
                    second = session.save_async()
                    self.assertIsNot(second, first)
                    release.set()
                    self.assertTrue(first.result(3))
                    self.assertTrue(second.result(3))
                self.assertEqual(Session(session.path).mode, 3)
                self.assertTrue(session.flush())
            finally:
                release.set()
                session.close()

    def test_async_failure_flush_protects_transition_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            ident = session.active_id
            try:
                with patch.object(Path, 'replace', side_effect=PermissionError('locked')):
                    turns = [dict(role='user', content='Question'), dict(role='assistant', content='Answer')]
                    saved = session.commit_async(turns, 'Answer')
                    self.assertFalse(saved.result(3))
                    turns[-1]['content'] = 'Changed after snapshot'
                    self.assertFalse(session.new_problem('Must not replace current'))
                self.assertEqual(session.active_id, ident)
                self.assertEqual(session.last_answer, 'Answer')
                self.assertTrue(session.save())
                self.assertEqual(Session(session.path).last_answer, 'Answer')
            finally:
                session.close()

    def test_async_writer_uses_frozen_message_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            started, release = threading.Event(), threading.Event()
            write = session._write_archive
            def delayed(*args):
                started.set()
                if not release.wait(3):
                    raise AssertionError('test writer was not released')
                return write(*args)
            try:
                with patch.object(session, '_write_archive', side_effect=delayed):
                    turns = [dict(role='user', content='Question'), dict(role='assistant', content='Original answer')]
                    saved = session.commit_async(turns, 'Original answer')
                    self.assertTrue(started.wait(1))
                    turns[-1]['content'] = 'Changed after queueing'
                    release.set()
                    self.assertTrue(saved.result(3))
                self.assertEqual(Session(session.path).messages[-1]['content'], 'Original answer')
            finally:
                release.set()
                session.close()

    def test_async_success_is_returned_only_after_fsync_and_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            original = session.path.read_bytes()
            try:
                with patch('session_state.os.fsync', side_effect=OSError('full disk')):
                    saved = session.commit_async([], 'Only in memory')
                    self.assertFalse(saved.result(3))
                self.assertEqual(session.path.read_bytes(), original)
                self.assertEqual(session.last_answer, 'Only in memory')
                self.assertTrue(session.error)
                self.assertFalse(session.flush())
                self.assertTrue(session.save())
            finally:
                session.close()

    def test_sync_save_retries_failed_background_write_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            try:
                with patch.object(Path, 'replace', side_effect=PermissionError('temporarily locked')):
                    saved = session.commit_async([], 'Recoverable answer')
                    self.assertFalse(saved.result(3))
                # No explicit flush between the failed Future and manual retry.
                self.assertTrue(session.save())
                self.assertFalse(session.error)
                self.assertEqual(Session(session.path).last_answer, 'Recoverable answer')
            finally:
                session.close()

    def test_close_retries_failed_background_write_and_preserves_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('Question')
            with patch.object(Path, 'replace', side_effect=PermissionError('temporarily locked')):
                saved = session.commit_async([], 'Answer before exit')
                self.assertFalse(saved.result(3))
            self.assertTrue(session.close())
            self.assertEqual(Session(session.path).last_answer, 'Answer before exit')


class HistoryBoundaryTests(unittest.TestCase):
    def client(self):
        client = Mock(config={'REPLY_MENU_VERSION': 1, 'REASONING_MODES': {'3': 'careful'}}, on_stream=Mock())
        client.ask.return_value = ('Keep original requirement and newest answer.', 'mock')
        return client

    def test_large_latest_pair_fits_by_reducing_reserve(self):
        client = self.client()
        messages = [dict(role='user', content='old' * 1000), dict(role='assistant', content='old answer'),
                    dict(role='user', content='question'), dict(role='assistant', content='A' * 29000)]
        history, summary, count = prepare_history(client, messages, '', 0, 31992, threading.Event(), Mock())
        self.assertEqual(history[-2:], messages[-2:])
        self.assertEqual(count, 2)
        self.assertTrue(summary)
        self.assertLessEqual(sum(len(m['content']) for m in history), 31992)

    def test_oversized_old_message_is_chunked_and_each_prompt_fits_budget(self):
        client = self.client()
        snapshots = []
        def summarize(text, **kwargs):
            snapshots.append(dict(client.config))
            return 'summary', 'mock'
        client.ask.side_effect = summarize
        messages = [dict(role='user', content='O' * 12000), dict(role='assistant', content='prior'),
                    dict(role='user', content='latest'), dict(role='assistant', content='answer')]
        history, _, count = prepare_history(client, messages, '', 0, 2000, threading.Event(), Mock())
        self.assertEqual(count, 2)
        self.assertEqual(history[-2:], messages[-2:])
        self.assertGreater(client.ask.call_count, 1)
        self.assertTrue(all(len(call.args[0]) <= 2000 for call in client.ask.call_args_list))
        payloads = [call.args[0].split('\nTIN NHẮN:\n', 1)[1] for call in client.ask.call_args_list]
        self.assertEqual(sum(payload.count('O') for payload in payloads), 12000)
        # Compression must use fast mode even when the user's main reply is careful.
        self.assertTrue(all(config['REPLY_MODE'] == 3 for config in snapshots))
        self.assertEqual(client.config['REASONING_MODES']['3'], 'careful')

    def test_cancel_between_summary_chunks_restores_client_and_original_history(self):
        client = self.client()
        original_config, original_callback = client.config, client.on_stream
        cancel = threading.Event()
        def summarize(*args, **kwargs):
            cancel.set()
            return 'summary', 'mock'
        client.ask.side_effect = summarize
        messages = [dict(role='user', content='original' * 1000)]
        with self.assertRaises(InterruptedError):
            prepare_history(client, messages, '', 0, 2000, cancel, Mock())
        self.assertEqual(client.ask.call_count, 1)
        self.assertIs(client.config, original_config)
        self.assertIs(client.on_stream, original_callback)
        self.assertEqual(messages[0]['content'], 'original' * 1000)

    def test_smaller_setting_rebuilds_oversized_old_summary(self):
        client = self.client()
        messages = [dict(role='user' if i % 2 == 0 else 'assistant', content='x' * 800) for i in range(8)]
        history, summary, _ = prepare_history(client, messages, 's' * 3000, 4, 2000, threading.Event(), Mock())
        self.assertLessEqual(sum(len(m['content']) for m in history), 2000)
        self.assertLess(len(summary), 1000)


class InputBoundaryTests(unittest.TestCase):
    def test_invalid_env_structures_do_not_survive_preference_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {'PROMPT_MODES': '{}', 'PROMPT_CUSTOM': 'bad', 'HOTKEYS': '{}',
                      'HOTKEYS_DISABLED': '[]', 'REASONING_MODES': '{}', 'SAVED_PROMPTS': '[]',
                      'DEEPSEEK_API_KEY': 'test-placeholder'}
            invalid = load_runtime_settings(config, Path(folder) / 'missing.json')
            self.assertEqual(len(invalid), 6)
            self.assertTrue(all(name not in config for name in invalid))
            self.assertTrue(merged_preferences(config, 3))
            self.assertEqual(config['DEEPSEEK_API_KEY'], 'test-placeholder')

    def test_foreground_or_bounds_change_during_grab_rejects_image(self):
        from PIL import Image
        for changed in ('foreground', 'bounds'):
            with self.subTest(changed=changed):
                user = capture_fixtures.CaptureTests().user()
                image = Image.new('RGB', (500, 780), 'white')
                def grab(**kwargs):
                    if changed == 'foreground':
                        user.GetForegroundWindow.return_value = 999
                    else:
                        def moved(hwnd, ref):
                            ref._obj.left, ref._obj.top, ref._obj.right, ref._obj.bottom = 20, 20, 500, 900
                            return True
                        user.GetWindowRect.side_effect = moved
                    return image
                with patch('screen_capture.C.WinDLL', return_value=user), patch('PIL.ImageGrab.grab', side_effect=grab):
                    with self.assertRaises(CaptureError):
                        capture_foreground_png(123)

    def test_image_eviction_notice_survives_reads_and_clears_with_session(self):
        cache = SessionImages()
        for i in range(9):
            cache.add('a', str(i).encode())
        self.assertIn('1 ảnh', cache.warning('a'))
        cache.get('a')
        self.assertTrue(cache.warning('a'))
        with patch('session_images.MAX_CACHE_BYTES', 3):
            cache.add('b', b'new')
        self.assertEqual(cache.get('a'), [])
        self.assertIn('9 ảnh', cache.warning('a'))
        self.assertFalse(cache.warning('b'))
        cache.clear('a')
        self.assertFalse(cache.warning('a'))


if __name__ == '__main__':
    unittest.main()
