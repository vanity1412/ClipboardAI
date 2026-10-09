"""Answer popup timing/focus and accepted-result regressions; no real desktop IO."""
import ctypes
import tempfile
import unittest
from unittest.mock import Mock, patch

from chat_modes import CHAT
from completion_popup import POPUP_MAX_CHARS, POPUP_SECONDS, popup_bounds, popup_rows
from request_display import RequestDisplay
from tests import test_selected_fixes as fixtures
from windows_native import WindowsApp


class CompletionPopupTests(unittest.TestCase):
    def popup(self):
        app = WindowsApp.__new__(WindowsApp)
        app.user, app.gdi = Mock(), Mock()
        app.gdi.GetTextExtentPoint32W.return_value = False
        app.notice, app.notice_font = 88, 99
        app.notice_until, app.notice_text = 0, None
        app.self_test = app.busy = app.capture_pending = app.region_pending = app.exiting = False
        app.config = {}
        app.tooltip = Mock()
        app.request_display = RequestDisplay()
        app.request_display.finish('done', 3, 'Câu 1: A\nCâu 2: B')
        return app

    def test_shows_answer_topmost_without_activating_then_expires(self):
        app = self.popup()
        with patch('window_layout.work_area', return_value=(-1920, 0, 0, 1040)) as work, \
                patch('windows_native.time.monotonic', return_value=100):
            app.show_completion()
        work.assert_called_once_with(hwnd=app.user.GetForegroundWindow.return_value)
        self.assertEqual(app.notice_until, 100 + POPUP_SECONDS)
        self.assertIn('1. A', app.notice_text)
        self.assertIn('2. B', app.notice_text)
        call = app.user.SetWindowPos.call_args.args
        self.assertEqual(call[1].value, ctypes.c_void_p(-1).value)
        self.assertEqual(call[-1], 0x10 | 0x40)
        self.assertLess(call[2], 0)
        app.user.SetForegroundWindow.assert_not_called()
        app.user.SetFocus.assert_not_called()
        app.user.OpenClipboard.assert_not_called()
        app.user.SetWindowPos.reset_mock()
        with patch('windows_native.time.monotonic', return_value=100 + POPUP_SECONDS - .01):
            app.update_notice()
        app.user.SetWindowPos.assert_called_once()
        with patch('windows_native.time.monotonic', return_value=100 + POPUP_SECONDS):
            app.update_notice()
        app.user.ShowWindow.assert_called_with(88, 0)
        self.assertEqual(app.notice_until, 0)
        self.assertEqual(app.notice_lines, [])
        self.assertIsNone(app.notice_text)

    def test_capture_privacy_exit_and_self_test_hide_pending_answer(self):
        for state in ('busy', 'capture_pending', 'region_pending', 'exiting', 'self_test'):
            with self.subTest(state=state):
                app = self.popup()
                app.notice_until, app.notice_text = 101, 'Previous answer'
                setattr(app, state, True)
                with patch('windows_native.time.monotonic', return_value=100):
                    app.update_notice()
                app.user.ShowWindow.assert_called_once_with(88, 0)
                app.user.SetWindowPos.assert_not_called()
                self.assertIsNone(app.notice_text)

    def test_popup_never_claims_unresolved_answers_are_solved(self):
        rows = popup_rows('Câu 1: A\nCâu 2: Chưa xác định (ảnh bị cắt)', len, 200)
        self.assertIn('1. A', '\n'.join(rows))
        self.assertIn('Chưa rõ: 2', '\n'.join(rows))

    def test_code_and_long_responses_have_real_preview_and_full_answer_hint(self):
        answer = '```python\n' + '\n'.join(f'print({index})' for index in range(25)) + '\n```'
        rows = popup_rows(answer, len, 200)
        self.assertIn('print(0)', '\n'.join(rows))
        self.assertIn('Xem đầy đủ', rows[-1])
        self.assertLessEqual(len(rows), 8)

    def test_bounds_fit_small_and_negative_monitors(self):
        for work in ((-800, -100, 0, 500), (0, 0, 100, 80), (10, 20, 11, 21)):
            with self.subTest(work=work):
                x, y, width, height = popup_bounds(work, 480, 8)
                self.assertGreaterEqual(x, work[0])
                self.assertGreaterEqual(y, work[1])
                self.assertLessEqual(x + width, work[2])
                self.assertLessEqual(y + height, work[3])
                self.assertGreater(width, 0)
                self.assertGreater(height, 0)

    def test_measures_bounded_preview_for_multimegabyte_response(self):
        measured = []
        def measure(text):
            measured.append(len(text))
            return len(text)
        rows = popup_rows('x' * (4 * 1024 * 1024), measure, 200)
        self.assertLessEqual(max(measured), POPUP_MAX_CHARS)
        self.assertIn('Xem đầy đủ', rows[-1])

    def test_notice_rejects_activation_and_passes_mouse_to_underlying_window(self):
        app = self.popup()
        self.assertEqual(app.notice_proc(88, 0x21, 0, 0), 3)
        self.assertEqual(app.notice_proc(88, 0x84, 0, 0), -1)
        app.user.SetFocus.assert_not_called()

    def test_explicit_replay_during_request_preserves_progress_and_restarts_timer(self):
        app = self.popup()
        app.busy = True
        app.request_display.begin(started=90)
        progress = app.request_display.__dict__.copy()
        with patch('window_layout.work_area', return_value=(0, 0, 1920, 1040)), \
                patch('windows_native.time.monotonic', return_value=100):
            app.show_completion('Câu 1: C')
        self.assertIn('1. C', app.notice_text)
        self.assertEqual(app.notice_until, 100 + POPUP_SECONDS)
        self.assertEqual(app.request_display.__dict__, progress)
        app.user.ShowWindow.assert_not_called()
        with patch('window_layout.work_area', return_value=(0, 0, 1920, 1040)), \
                patch('windows_native.time.monotonic', return_value=101):
            app.show_completion('Câu 1: C')
        self.assertEqual(app.notice_until, 101 + POPUP_SECONDS)

    def test_f7_copies_and_replays_same_completed_answer_even_if_read_is_busy(self):
        for busy, clipboard in ((False, ('Question', 12)), (True, ('Question', 12)), (True, None)):
            with self.subTest(busy=busy, clipboard=clipboard), tempfile.TemporaryDirectory() as root:
                app = fixtures.SelectedFixTests().app(root)
                app.busy = busy
                app.show_completion = Mock()
                app.read_clipboard.return_value = clipboard
                display = app.display_state()
                if busy:
                    display.begin(started=90)
                progress = display.__dict__.copy()
                state, request_id = app.state, app.current_id
                app.window_proc(app.hwnd, 0x0312, 215, 0)
                app.show_completion.assert_called_once_with('Completed answer')
                if clipboard:
                    self.assertEqual(app.pending_write[:2], ('Completed answer', 12))
                else:
                    self.assertIsNone(app.pending_write)
                if busy:
                    self.assertEqual(display.__dict__, progress)
                    self.assertEqual(app.state, state)
                self.assertEqual(app.current_id, request_id)
                self.assertTrue(app.jobs.empty())
                app.client.ask.assert_not_called()

    def test_native_menu_suppresses_popup_and_restores_owner_after_exception(self):
        for owner_style in (0, 0x8):
            for fails in (False, True):
                with self.subTest(style=owner_style, fails=fails):
                    app = self.popup()
                    app.hwnd = 123
                    app.notice_until, app.notice_text = 100 + POPUP_SECONDS, 'Answer'
                    app.notice_bounds = (1428, 930, 480, 100)
                    app.user.GetWindowLongPtrW.return_value = owner_style
                    app.user.GetWindowLongW.return_value = owner_style
                    def track(*args):
                        self.assertTrue(app.menu_tracking)
                        app.user.SetWindowPos.reset_mock()
                        app.update_notice()
                        app.user.ShowWindow.assert_called_with(app.notice, 0)
                        app.user.SetWindowPos.assert_not_called()
                        self.assertEqual(app.notice_until, 100 + POPUP_SECONDS)
                        if fails:
                            raise OSError('synthetic menu failure')
                        return 5998
                    app.user.TrackPopupMenu.side_effect = track
                    with patch('windows_native.time.monotonic', return_value=100):
                        if fails:
                            with self.assertRaises(OSError):
                                app.track_popup_menu(11, 0x102, 100, 200)
                        else:
                            self.assertEqual(app.track_popup_menu(11, 0x102, 100, 200), 5998)
                    self.assertFalse(app.menu_tracking)
                    calls = app.user.SetWindowPos.call_args_list
                    self.assertEqual(calls[0].args[0], app.hwnd)
                    self.assertEqual(calls[0].args[1].value, ctypes.c_void_p(-1 if owner_style else -2).value)
                    self.assertEqual(calls[0].args[-1], 0x13)
                    self.assertEqual(calls[-1].args[0], app.notice)
                    self.assertFalse(any(call.args == (app.hwnd, 5) for call in app.user.ShowWindow.call_args_list))

    def test_f7_and_shift_f7_replay_exactly_the_answer_selected_for_copy(self):
        for key, answer in ((215, 'Global answer'), (217, 'Session answer')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                app = fixtures.SelectedFixTests().app(root)
                app.last_completed_answer = 'Global answer'
                app.session.last_answer = 'Session answer'
                app.show_completion = Mock()
                with patch('windows_native.log_event'):
                    app.window_proc(app.hwnd, 0x0312, key, 0)
                self.assertEqual(app.pending_write[0], answer)
                app.show_completion.assert_called_once_with(answer)
                app.client.ask.assert_not_called()

    def test_copy_success_during_request_does_not_replace_running_status(self):
        app = fixtures.SelectedFixTests().clipboard_app(sequence=12, text='Question', has_text=True)
        app.busy = True
        app.state = 'Model đang xử lý'
        app.request_display = RequestDisplay()
        app.request_display.begin(started=90)
        app.display_state()
        progress = app.request_display.__dict__.copy()
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('Previous completed answer', 12))
        self.assertEqual(app.state, 'Model đang xử lý')
        self.assertEqual(app.request_display.__dict__, progress)
        app.user.SetClipboardData.assert_called_once_with(13, 123)

    def test_delayed_old_f7_copy_does_not_mark_new_or_failed_answer_as_copied(self):
        for phase in ('done', 'failed'):
            for status in ('copied', 'changed', 'error'):
                with self.subTest(phase=phase, status=status), tempfile.TemporaryDirectory() as root:
                    app = fixtures.SelectedFixTests().app(root)
                    clipboard = fixtures.SelectedFixTests().clipboard_app(sequence=12, text='Question', has_text=True)
                    app.user, app.kernel = clipboard.user, clipboard.kernel
                    app.user.OpenClipboard.return_value = False
                    app.show_completion = Mock()
                    app.text = Mock(return_value='New question')
                    app.busy = True
                    app.session.auto_copy = False
                    app.display_state().begin(started=90)
                    app.copy_last_answer()
                    job = dict(id=app.current_id, mode=CHAT, action='chat', text='New question',
                               sequence=12, digest=b'test')
                    if phase == 'done':
                        turns = [dict(role='user', content='New question'), dict(role='assistant', content='New answer')]
                        app.results.put(('done', job, 'New answer', turns))
                    else:
                        app.results.put(('failed', job, 'New request failed'))
                    with patch('windows_native.log_event'):
                        app.tick()
                        self.assertEqual(app.pending_write[0], 'Completed answer')
                        self.assertEqual(app.display_state().phase, phase)
                        before = app.display_state().__dict__.copy()
                        state = app.state
                        app.user.OpenClipboard.return_value = True
                        if status == 'changed':
                            app.user.GetClipboardSequenceNumber.return_value = 13
                        elif status == 'error':
                            app.user.EmptyClipboard.side_effect = OSError('synthetic clipboard failure')
                        app.tick()
                    self.assertIsNone(app.pending_write)
                    self.assertEqual(app.display_state().__dict__, before)
                    self.assertEqual(app.state, state)
                    if status == 'copied':
                        self.assertEqual(ctypes.wstring_at(ctypes.addressof(clipboard.clipboard_buffer)), 'Completed answer')

    def test_accepted_result_shows_once_even_when_clipboard_cannot_copy(self):
        for copy_result in (False, True, OSError('synthetic clipboard failure')):
            with self.subTest(copy_result=copy_result), tempfile.TemporaryDirectory() as root:
                app = fixtures.SelectedFixTests().app(root)
                app.show_completion = Mock()
                app.write_clipboard = Mock(side_effect=copy_result) if isinstance(copy_result, OSError) else Mock(return_value=copy_result)
                app.session.mode = CHAT
                app.session.auto_copy = True
                app.busy = True
                job = dict(id=app.current_id, mode=CHAT, action='chat', text='Question',
                           sequence=12, digest=b'test')
                turns = [dict(role='user', content='Question'), dict(role='assistant', content='New answer')]
                app.results.put(('done', job, 'New answer', turns))
                with patch('windows_native.log_event'):
                    app.tick()
                    app.tick()
                app.show_completion.assert_called_once_with()

    def test_stale_and_invalid_results_do_not_flash_old_answer(self):
        for stale, answer in ((True, 'Stale answer'), (False, 'invalid\ud800')):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as root:
                app = fixtures.SelectedFixTests().app(root)
                app.show_completion = Mock()
                job = dict(id=app.current_id - 1 if stale else app.current_id, mode=CHAT,
                           action='chat', text='Question', sequence=12, digest=b'test')
                turns = [dict(role='user', content='Question'), dict(role='assistant', content=answer)]
                app.results.put(('done', job, answer, turns))
                with patch('windows_native.log_event'):
                    app.tick()
                app.show_completion.assert_not_called()


if __name__ == '__main__':
    unittest.main()
