"""Native request/storage integration without clipboard, API or card changes."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from chat_modes import CHAT
from request_display import RequestDisplay
from session_state import Session
from session_images import SessionImages
from tests import test_selected_fixes as fixtures
import windows_native as native


class NativePersistenceTests(unittest.TestCase):
    def app(self, root):
        app = fixtures.SelectedFixTests().app(root)
        app.request_display = RequestDisplay()
        app.write_clipboard = Mock(return_value=True)
        app.show_completion = Mock()
        app.session.mode = CHAT
        app.session.save()
        return app

    def complete(self, app, answer='New answer'):
        app.busy = True
        job = dict(id=app.current_id, mode=CHAT, action='chat', text='New question',
                   sequence=12, digest=native.fingerprint('Question input'))
        turns = [dict(role='user', content=job['text']),
                 dict(role='assistant', content=answer)]
        app.results.put(('done', job, answer, turns))
        return job

    def test_invalid_api_unicode_does_not_poison_session_or_copy_timer(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            original = app.session.path.read_bytes()
            self.complete(app, 'invalid\ud800')
            with patch('windows_native.log_event'):
                app.tick()
                app.tick()
            self.assertEqual(app.session.path.read_bytes(), original)
            self.assertEqual(app.session.last_answer, 'Completed answer')
            self.assertEqual(app.last_completed_answer, 'Completed answer')
            self.assertEqual(app.request_display.phase, 'failed')
            self.assertIsNone(app.pending_write)
            app.write_clipboard.assert_not_called()

    def test_configured_history_capacity_accepts_large_question_and_reserves_it(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            question = 'q' * 30000
            app.start_request(question, action='chat', new_session=False)
            self.assertTrue(app.busy)
            job = app.jobs.get_nowait()
            self.assertEqual(job['text'], question)
            self.assertEqual(job['history_budget'], 18000)
            self.assertEqual(job['memory_messages'], app.session.messages)
            app.session.close()

    def test_oversized_question_rejected_before_session_or_worker_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            original = app.session.path.read_bytes()
            app.start_request('q' * 48001, action='chat', new_session=False)
            self.assertFalse(app.busy)
            self.assertTrue(app.jobs.empty())
            self.assertEqual(app.session.path.read_bytes(), original)

    def test_completion_does_not_wait_for_disk_and_only_acknowledges_durable_write(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.async_storage = True
            entered, release = threading.Event(), threading.Event()
            write = app.session._write_archive
            def blocked_write(*args):
                entered.set()
                release.wait(3)
                return write(*args)
            try:
                with patch.object(app.session, '_write_archive', side_effect=blocked_write), patch('windows_native.log_event'):
                    self.complete(app)
                    before = time.monotonic()
                    app.tick()
                    self.assertLess(time.monotonic() - before, .5)
                    self.assertTrue(entered.wait(1))
                    self.assertIn('đang lưu', app.state)
                    self.assertIn('đang lưu', app.result_text())
                    self.assertIn('New answer', app.result_text())
                    self.assertEqual(Session(app.session.path).last_answer, 'Completed answer')
                    app.write_clipboard.assert_called_once()
                    release.set()
                    self.assertTrue(app.session.flush())
                    app.tick()
                    self.assertIn('đã lưu', app.state)
                    self.assertNotIn('đang lưu', app.result_text())
                    self.assertEqual(Session(app.session.path).last_answer, 'New answer')
            finally:
                release.set()
                app.session.close()

    def test_async_failure_visible_and_manual_save_recovers_answer(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.async_storage = True
            with patch.object(app.session, '_write_archive', return_value=False), patch('windows_native.log_event'):
                self.complete(app)
                app.tick()
                self.assertFalse(app.session.flush())
                app.tick()
                self.assertIn('chưa lưu', app.state.lower())
                self.assertIn('chưa lưu', app.result_text().lower())
            self.assertEqual(Session(app.session.path).last_answer, 'Completed answer')
            self.assertTrue(app.session.save())
            self.assertEqual(Session(app.session.path).last_answer, 'New answer')
            app.session.close()

    def test_image_eviction_warning_remains_visible_after_completion(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            for index in range(9):
                app.images.add(app.session.active_id, bytes([index + 1]))
            self.complete(app)
            with patch('windows_native.log_event'):
                app.tick()
            self.assertIn('ảnh cũ đã bị bỏ', app.request_display.preview())
            pages = app.request_display.hover_pages(lambda value: len(value) * 7, 340)
            self.assertTrue(all(any('ảnh cũ đã bị bỏ' in row for row in page.rows) for page in pages))
            self.assertEqual(app.last_completed_answer, 'New answer')

    def test_exit_cancels_network_immediately_and_waits_for_storage_and_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.network.shutdown.return_value = False
            entered, release = threading.Event(), threading.Event()
            def save():
                entered.set()
                release.wait(3)
                return True
            try:
                with patch.object(app.session, 'save', side_effect=save), patch('windows_native.log_event'):
                    app.request_exit()
                    self.assertTrue(entered.wait(1))
                    self.assertIn('lưu lịch sử trước khi thoát', app.result_text())
                    app.network.shutdown.assert_called_once_with(timeout=0)
                    app.tick()
                    app.user.DestroyWindow.assert_not_called()
                    release.set()
                    deadline = time.monotonic() + 2
                    while not app.exit_storage_ready and time.monotonic() < deadline:
                        app.tick()
                        time.sleep(.01)
                    self.assertTrue(app.exit_storage_ready)
                    app.user.DestroyWindow.assert_not_called()
                    app.network.shutdown.return_value = True
                    app.tick()
                    app.user.DestroyWindow.assert_called_once_with(app.hwnd)
            finally:
                release.set()

    def test_failed_exit_save_reopens_app_and_allows_future_network_switch(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.exiting = True
            app.results.put(('exit_saved', False))
            app.tick()
            self.assertFalse(app.exiting)
            app.network.abort_shutdown.assert_called_once()
            app.user.DestroyWindow.assert_not_called()

    def test_image_notice_follows_session_and_clears_when_images_removed(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.controls = {'mode': 1, 'auto': 2}
            first = app.session.active_id
            for index in range(9):
                app.images.add(first, bytes([index + 1]))
            self.assertTrue(app.session.new_problem('Second session'))
            second = app.session.active_id
            app.select_session(first)
            self.assertIn('ảnh cũ đã bị bỏ', app.result_text())
            app.select_session(second)
            self.assertNotIn('ảnh cũ đã bị bỏ', app.result_text())
            app.select_session(first)
            app.command(222)
            self.assertNotIn('ảnh cũ đã bị bỏ', app.result_text())
            self.assertEqual(app.images.warning(first), '')

    def test_unreadable_archive_allows_quit_only_without_new_memory_content(self):
        for new_content in ('', 'Unsaved answer'):
            with self.subTest(new_content=bool(new_content)), tempfile.TemporaryDirectory() as root:
                app = self.app(root)
                original = b'{corrupt synthetic archive'
                app.session.path.write_bytes(original)
                app.session = Session(app.session.path)
                app.session.last_answer = new_content
                app.last_completed_answer = new_content
                app.network.shutdown.return_value = True
                with patch('windows_native.log_event'):
                    app.request_exit()
                    deadline = time.monotonic() + 2
                    while app.results.empty() and time.monotonic() < deadline:
                        time.sleep(.01)
                    app.tick()
                self.assertEqual(app.session.path.read_bytes(), original)
                if new_content:
                    self.assertFalse(app.exiting)
                    app.user.DestroyWindow.assert_not_called()
                else:
                    app.user.DestroyWindow.assert_called_once_with(app.hwnd)

    def test_status_log_rotates_and_keeps_only_current_and_previous_file(self):
        with tempfile.TemporaryDirectory() as root, patch('windows_native.ROOT', Path(root)), \
                patch('windows_native.LOG_MAX_BYTES', 128):
            for index in range(30):
                native.log_event('synthetic', request_id=index)
            self.assertEqual(sorted(file.name for file in Path(root).iterdir()),
                             ['status.log', 'status.previous.log'])
            self.assertLess((Path(root) / 'status.log').stat().st_size, 256)
            self.assertLess((Path(root) / 'status.previous.log').stat().st_size, 256)

    def test_followup_after_restart_sends_restored_images_and_preserves_data(self):
        import base64
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.images = SessionImages(Path(root) / 'images.sqlite3')
            app.images.add(app.session.active_id, b'synthetic-png')
            app.session.commit([dict(role='user', content='Original image\n[Ảnh đính kèm]'),
                                dict(role='assistant', content='Old image answer')], 'Old image answer')
            app.session = Session(app.session.path)
            app.images = SessionImages(Path(root) / 'images.sqlite3')
            app.closed = threading.Event()
            def answer(content, history, instruction):
                app.closed.set()
                return 'Restored image answer', 'Synthetic provider'
            app.client.ask.side_effect = answer
            app.start_request('Followup', action='chat', new_session=False)
            with patch('windows_native.log_event'):
                app.worker()
                app.tick()
            content = app.client.ask.call_args.args[0]
            self.assertEqual(content[0], {'type': 'text', 'text': 'Followup'})
            self.assertEqual(content[1]['image_url']['url'],
                             'data:image/png;base64,' + base64.b64encode(b'synthetic-png').decode())
            self.assertEqual(Session(app.session.path).last_answer, 'Restored image answer')
            self.assertEqual(SessionImages(Path(root) / 'images.sqlite3').get(app.session.active_id), [b'synthetic-png'])

    def test_image_manager_blocks_requests_and_failed_open_does_not_leave_app_stuck(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.images_manager_open = True
            app.start_request('Question', action='chat', new_session=False)
            self.assertTrue(app.jobs.empty())
            self.assertIn('Đóng cửa sổ quản lý ảnh', app.result_text())
            app.images_manager_open = False
            with patch('windows_native.threading.Thread') as thread:
                thread.return_value.start.side_effect = RuntimeError('synthetic')
                app.command(223)
            self.assertFalse(app.images_manager_open)

    def test_clear_images_while_model_running_keeps_request_images(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.images.add(app.session.active_id, b'synthetic')
            app.busy = True
            app.command(222)
            self.assertEqual(app.images.get(app.session.active_id), [b'synthetic'])


if __name__ == '__main__':
    unittest.main()
