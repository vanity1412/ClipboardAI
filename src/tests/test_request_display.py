import ctypes
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch
from request_display import RequestDisplay, failure_hint
from compact_preview import uncertain_answers
from tests import test_selected_fixes as fixtures
from chat_modes import CHAT


class RequestDisplayTests(unittest.TestCase):
    def test_counts_model_seconds_not_capture_or_prep(self):
        display = RequestDisplay()
        display.begin(image=True)
        self.assertEqual(display.seconds(200), 0)
        self.assertNotIn('s', display.header(200).split('·')[-1].strip()[-1:])
        display.begin_model(200)
        self.assertEqual(display.header(200), 'Model đang xử lý · 0s')
        self.assertEqual(display.header(201.3), 'Model đang xử lý · 1s')
        display.finish('done', 3, '1. A', copy='copied')
        self.assertEqual(display.header(999), 'Đã copy · 3s')
        self.assertEqual(display.preview(999), 'Đã copy · 3s\n1. A')

    def test_error_and_cancel_never_display_previous_answers(self):
        display = RequestDisplay(answer='OLD PRIVATE ANSWER')
        display.finish('failed', 9, error='Tài khoản API hết số dư/quota (402)')
        self.assertIn('Lỗi 402', display.preview())
        self.assertNotIn('OLD', display.preview())
        display.finish('cancelled', 10)
        self.assertEqual(display.preview(), 'Đã hủy · 10s')
        self.assertIn('model đọc ảnh', failure_hint('API Zoo thiếu key hoặc model đọc ảnh; kiểm tra cấu hình'))
        self.assertIn('AI trả rỗng', failure_hint('Mirai trả nội dung trống'))

    def test_unresolved_last_question_is_visible_after_cutoff(self):
        answer = '\n'.join([f'{i}. A' for i in range(1, 60)] + ['60. Chưa xác định'])
        display = RequestDisplay()
        display.finish('done', 14, answer, copy='copied')
        value = display.preview()
        self.assertIn('Chưa rõ: 60', value)
        self.assertIn('1. A', value)
        self.assertLessEqual(len(value.splitlines()), 5)
        self.assertEqual(uncertain_answers('Câu 7. Câu hỏi chưa đọc đủ?\nChưa xác định do thiếu dữ kiện.'), ['câu 7'])
        self.assertEqual(uncertain_answers('Câu 2, ô 1: Chưa xác định'), ['câu 2, ô 1'])

    def test_copy_outcomes_do_not_claim_clipboard_overwrite(self):
        display = RequestDisplay()
        display.finish('done', 4, 'B', copy='changed')
        self.assertIn('clipboard đã đổi', display.header())
        self.assertIn('F7', display.preview())
        display.copy = 'error'
        self.assertIn('copy lỗi', display.header())

    def test_known_fill_answers_are_visible_before_cut_off_warning(self):
        answer = ('1. Artificial Intelligence\n2. Machine\n'
                  '3. Chưa xác định (ảnh bị cắt)\n4. Chưa xác định (ảnh bị cắt)\n'
                  '5. Generative Pre-trained Transformer\n6. Chưa xác định (ảnh bị cắt)')
        display = RequestDisplay()
        display.finish('done', 34, answer, copy='copied')
        self.assertEqual(display.preview().splitlines(), [
            'Đã copy · 34s', '1. Artificial Intelligence', '2. Machine',
            '5. Generative Pre-trained Transformer', 'Chưa rõ: 3, 4, 6 · ảnh bị cắt'])
        self.assertEqual(display.answer, answer)

    def test_long_known_answer_does_not_hide_following_short_answer(self):
        display = RequestDisplay()
        display.finish('done', 2, '1. ' + 'Long answer ' * 20 + '\n2. B', copy='copied')
        self.assertIn('2. B', display.preview())
        self.assertEqual(failure_hint('Ảnh chụp toàn đen; chưa gửi AI'), 'Ảnh đen · chưa gửi AI · F4 chụp lại')

    def test_uncertain_reason_is_only_displayed_when_explicit(self):
        display = RequestDisplay()
        display.finish('done', 1, 'Chưa xác định (ảnh bị cắt)', copy='copied')
        self.assertIn('ảnh bị cắt', display.preview())
        self.assertIn('Shift+F9', display.preview())
        display.finish('done', 1, 'Chưa xác định', copy='copied')
        self.assertNotIn('ảnh bị cắt', display.preview())

    def test_failed_followup_and_stale_done_have_correct_display(self):
        with tempfile.TemporaryDirectory() as root:
            app = fixtures.SelectedFixTests().app(root)
            app.request_display = RequestDisplay()
            app.request_display.begin_model(100)
            app.request_display.phase = 'running'
            app.busy = True
            app.text = Mock(return_value='existing input')
            job = dict(id=7, action='retry_chat', mode=CHAT, text='new input', model_seconds=9)
            app.results.put(('failed', job, 'Tài khoản API hết số dư/quota (402)'))
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.request_display.phase, 'failed')
            self.assertNotIn('Completed answer', app.result_text())
            self.assertIn('Lỗi 402', app.result_text())
            turns = [dict(role='user', content='x'), dict(role='assistant', content='STALE')]
            app.results.put(('done', dict(job, id=6), 'STALE', turns))
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.request_display.phase, 'failed')

    def test_blocked_f4_reports_not_sent(self):
        with tempfile.TemporaryDirectory() as root:
            app = fixtures.SelectedFixTests().app(root)
            app.busy = True
            app.request_display = RequestDisplay()
            app.request_display.begin(100, image=True)
            app.send_screenshot()
            self.assertIn('F4 chưa gửi', app.result_text())
            self.assertTrue(app.jobs.empty())

    def test_menu_header_changes_without_reopening(self):
        with tempfile.TemporaryDirectory() as root:
            app = fixtures.SelectedFixTests().app(root)
            app.request_display = RequestDisplay()
            app.request_display.begin(100)
            app.request_display.begin_model(100)
            app.active_status_menu = 42
            app.last_menu_status = 'Model đang xử lý · 0s'
            app.user.GetMenuItemRect.return_value = False
            with patch('request_display.time.monotonic', return_value=102):
                app.refresh_status_menu()
            self.assertEqual(app.last_menu_status, 'Model đang xử lý · 2s')
            app.user.SetMenuItemInfoW.assert_called_once()
            with patch('request_display.time.monotonic', return_value=102.5):
                app.refresh_status_menu()
            app.user.SetMenuItemInfoW.assert_called_once()

    def test_pause_releases_global_keys_and_resume_registers_again(self):
        with tempfile.TemporaryDirectory() as root:
            app = fixtures.SelectedFixTests().app(root)
            app.hotkeys, app.hotkey_errors = [201, 213], []
            app.register_hotkeys = Mock()
            with patch('windows_native.log_event'):
                app.command(103)
            self.assertFalse(app.enabled)
            self.assertEqual(app.user.UnregisterHotKey.call_count, 2)
            self.assertEqual(app.hotkeys, [])
            with patch('windows_native.log_event'):
                app.command(103)
            self.assertTrue(app.enabled)
            app.register_hotkeys.assert_called_once()

    def test_manual_network_recovery_only_opens_windows_control_panel(self):
        with tempfile.TemporaryDirectory() as root:
            app = fixtures.SelectedFixTests().app(root)
            with patch('windows_native.os.startfile') as start:
                app.open_adapter_settings()
            start.assert_called_once_with('ncpa.cpl')
            app.network.perform.assert_not_called()
