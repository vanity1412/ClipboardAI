"""User-facing regressions with no real clipboard, capture, API or card changes."""
import ctypes
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from chat_modes import CHAT
from session_images import SessionImages
from session_state import Session
from windows_native import WindowsApp, fingerprint


class SelectedFixTests(unittest.TestCase):
    def app(self, root):
        app = WindowsApp.__new__(WindowsApp)
        app.session = Session(Path(root) / 'session.json')
        app.session.commit([dict(role='user', content='Old question'),
                            dict(role='assistant', content='Completed answer')],
                           'Completed answer')
        app.last_completed_answer = app.session.last_answer
        app.config = {'BACKEND': 'DeepSeek', 'DEEPSEEK_MODEL': 'deepseek-flash',
                      'SESSION_MAX_CHARS': '48000', 'F4_INPUT': 'image',
                      'ANSWER_STYLE': 'short'}
        app.is_deepseek = app.enabled = True
        app.busy = app.network_busy = app.probe_busy = False
        app.pending_read = app.self_test = False
        app.current_id = 7
        app.hwnd = 123
        app.user = Mock()
        app.user.GetForegroundWindow.return_value = 456
        app.user.GetWindowLongPtrW.return_value = 0
        app.user.GetWindowLongW.return_value = 0
        app.client = Mock()
        app.network = Mock()
        app.cancel_event = threading.Event()
        app.jobs = queue.Queue(maxsize=1)
        app.results = queue.Queue()
        app.images = SessionImages()
        app.output_fingerprints = set()
        app.started = None
        app.state = 'Ready'
        app.last_image = app.pending_image = app.pending_write = None
        app.tooltip = Mock()
        app.refresh_panel = Mock()
        app.set_text = Mock()
        app.update_notice = Mock()
        app.read_clipboard = Mock(return_value=('Question input', 12))
        return app

    def clipboard_app(self, *, sequence, text, has_text):
        app = WindowsApp.__new__(WindowsApp)
        app.hwnd = 1
        app.user, app.kernel = Mock(), Mock()
        app.user.OpenClipboard.return_value = True
        app.user.GetClipboardSequenceNumber.return_value = sequence
        app.user.IsClipboardFormatAvailable.return_value = has_text
        app.user.EmptyClipboard.return_value = True
        app.user.SetClipboardData.return_value = 123
        app.read_open_clipboard_text = Mock(return_value=text)
        app.output_fingerprints = set()
        app.tooltip = Mock()
        app.show_completion = Mock()
        # Only copy into private test memory; all Windows calls are mocks.
        app.clipboard_buffer = ctypes.create_string_buffer(1024)
        app.kernel.GlobalAlloc.return_value = 123
        app.kernel.GlobalLock.return_value = ctypes.addressof(app.clipboard_buffer)
        return app

    def test_new_image_or_file_is_preserved_after_empty_screenshot_baseline(self):
        for format_name in ('image', 'file'):
            with self.subTest(format=format_name):
                app = self.clipboard_app(sequence=21, text='', has_text=False)
                with patch('windows_native.log_event'):
                    self.assertTrue(app.write_clipboard('AI answer', 20, fingerprint('')))
                app.user.EmptyClipboard.assert_not_called()
                app.user.SetClipboardData.assert_not_called()
                app.kernel.GlobalAlloc.assert_not_called()
                app.user.CloseClipboard.assert_called_once()
                self.assertIn('clipboard đã đổi', app.state)

    def test_changed_empty_unicode_clipboard_is_also_preserved(self):
        app = self.clipboard_app(sequence=21, text='', has_text=True)
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('AI answer', 20, fingerprint('')))
        app.user.EmptyClipboard.assert_not_called()
        app.user.SetClipboardData.assert_not_called()

    def test_new_clipboard_sequence_preserves_even_same_text_or_mixed_formats(self):
        app = self.clipboard_app(sequence=21, text='Question\r\n', has_text=True)
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('AI answer', 20, fingerprint('Question')))
        app.user.EmptyClipboard.assert_not_called()
        app.user.SetClipboardData.assert_not_called()
        app.kernel.GlobalAlloc.assert_not_called()

    def test_unchanged_nontext_baseline_still_allows_screenshot_answer_copy(self):
        app = self.clipboard_app(sequence=20, text='', has_text=False)
        with patch('windows_native.log_event'):
            self.assertTrue(app.write_clipboard('AI answer', 20, fingerprint('')))
        app.user.EmptyClipboard.assert_called_once()
        app.user.SetClipboardData.assert_called_once_with(13, 123)

    def test_f3_read_only_picker_preserves_answer_waiting_for_clipboard(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.pending_write = pending = ('Completed answer', 12, fingerprint('Question input'))
            with patch('windows_native.threading.Thread') as thread:
                app.open_network_picker()
            thread.return_value.start.assert_called_once()
            self.assertIs(app.pending_write, pending)
            app.write_clipboard = Mock(return_value=True)
            with patch('windows_native.log_event'):
                app.tick()
            app.write_clipboard.assert_called_once_with(*pending)
            self.assertIsNone(app.pending_write)
            app.client.ask.assert_not_called()

    def test_busy_f4_keeps_request_and_explains_how_to_send_new_image(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            active_id, current_id = app.session.active_id, app.current_id
            app.send_screenshot()
            self.assertIn('Đang', app.state)
            self.assertIn('F10', app.state)
            self.assertEqual(app.session.active_id, active_id)
            self.assertEqual(app.current_id, current_id)
            self.assertEqual(app.session.last_answer, 'Completed answer')
            self.assertTrue(app.jobs.empty())
            app.read_clipboard.assert_not_called()
            app.user.GetForegroundWindow.assert_not_called()
            app.tooltip.assert_called_with(app.state)

    def test_f4_does_not_inject_a_request_for_explanation(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.send_screenshot()
            text = app.jobs.queue[0]['text']
            self.assertNotIn('kèm giải thích', text.lower())
            self.assertNotIn('Question input', text)

    def test_shift_f8_copies_completed_answer_without_resending_or_opening_gui(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            app.preview_answer = 'Partial response that must not be copied'
            app.start_request = Mock()
            app.show_panel = Mock()
            app.write_clipboard = Mock(return_value=True)
            active_id, current_id = app.session.active_id, app.current_id
            with patch('windows_native.log_event'):
                app.window_proc(app.hwnd, 0x0312, 215, 0)
                app.tick()
            app.write_clipboard.assert_called_once_with(
                'Completed answer', 12, fingerprint('Question input'))
            self.assertEqual(app.current_id, current_id)
            self.assertEqual(app.session.active_id, active_id)
            self.assertTrue(app.busy)
            self.assertTrue(app.jobs.empty())
            app.start_request.assert_not_called()
            app.client.ask.assert_not_called()
            app.show_panel.assert_not_called()

    def test_shift_f8_registers_without_repeat_for_nonflash_build_too(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.config['DEEPSEEK_MODEL'] = 'deepseek-reasoner'
            app.hotkeys, app.hotkey_errors = [], []
            app.user.RegisterHotKey.return_value = True
            app.register_hotkeys()
            app.user.RegisterHotKey.assert_any_call(app.hwnd, 215, 0x4000, 0x76)

    def test_copy_last_answer_without_result_or_with_locked_clipboard_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.session.last_answer = ''
            app.last_completed_answer = ''
            app.copy_last_answer()
            self.assertIsNone(app.pending_write)
            app.read_clipboard.assert_not_called()
            self.assertIn('Chưa', app.state)
            app.session.last_answer = 'Completed answer'
            app.read_clipboard.return_value = None
            app.copy_last_answer()
            self.assertIsNone(app.pending_write)
            self.assertIn('Clipboard', app.state)
            self.assertIn('F7', app.state)
            app.client.ask.assert_not_called()

    def test_shift_f8_keeps_last_complete_answer_when_f4_or_f8_starts_new_session(self):
        for send in ('send_screenshot', 'send_clipboard'):
            with self.subTest(send=send), tempfile.TemporaryDirectory() as root:
                app = self.app(root)
                getattr(app, send)()
                self.assertEqual(app.session.last_answer, '')
                self.assertTrue(app.busy)
                request_id = app.current_id
                app.write_clipboard = Mock(return_value=True)
                with patch('windows_native.log_event'):
                    app.window_proc(app.hwnd, 0x0312, 215, 0)
                    app.tick()
                app.write_clipboard.assert_called_once_with(
                    'Completed answer', 12, fingerprint('Question input'))
                self.assertEqual(app.current_id, request_id)
                self.assertEqual(app.jobs.qsize(), 1)
                self.assertEqual(app.session.last_answer, '')
                self.assertEqual(app.last_completed_answer, 'Completed answer')
                app.client.ask.assert_not_called()

    def test_selected_session_copy_uses_selected_answer_instead_of_latest_cache(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.last_completed_answer = 'A different recently completed answer'
            app.write_clipboard = Mock(return_value=True)
            with patch('windows_native.log_event'):
                app.command(111)
                app.tick()
            app.write_clipboard.assert_called_once_with(
                'Completed answer', 12, fingerprint('Question input'))
            self.assertEqual(app.last_completed_answer, 'A different recently completed answer')
            app.client.ask.assert_not_called()

    def test_stream_and_late_canceled_result_do_not_replace_last_complete_answer(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            app.preview_answer = ''
            app.write_clipboard = Mock(return_value=True)
            request_id = app.current_id
            app.results.put(('stream', request_id, 'Incomplete answer'))
            with patch('windows_native.log_event'):
                app.tick()
                self.assertEqual(app.preview_answer, 'Incomplete answer')
                self.assertEqual(app.last_completed_answer, 'Completed answer')
                app.cancel_request()
                late_job = dict(id=request_id, mode=CHAT, action='chat', text='New question',
                                sequence=12, digest=fingerprint('Question input'))
                app.results.put(('done', late_job, 'Late canceled answer', []))
                app.tick()
            self.assertEqual(app.last_completed_answer, 'Completed answer')
            self.assertEqual(app.session.last_answer, 'Completed answer')
            app.write_clipboard.assert_not_called()

    def test_current_complete_result_updates_latest_answer_cache(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            app.write_clipboard = Mock(return_value=True)
            job = dict(id=app.current_id, mode=CHAT, action='chat', text='New question',
                       sequence=12, digest=fingerprint('Question input'))
            turns = [dict(role='user', content='New question'),
                     dict(role='assistant', content='New complete answer')]
            app.results.put(('done', job, 'New complete answer', turns))
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.last_completed_answer, 'New complete answer')
            self.assertEqual(app.session.last_answer, 'New complete answer')

    def test_adapter_labels_distinguish_enabled_from_connected(self):
        row = dict(name='Wi-Fi 2', description='USB Realtek', enabled=False, status='Disabled')
        label = WindowsApp.adapter_menu_label(row, 'Wi-Fi')
        self.assertIn('Wi-Fi 2 · USB Realtek', label)
        self.assertIn('Đã tắt', label)
        row.update(enabled=True, status='Disconnected')
        label = WindowsApp.adapter_menu_label(row)
        self.assertIn('Đã bật · Chưa kết nối', label)
        self.assertNotIn('Đã kết nối', label)
        row['status'] = 'Up'
        self.assertIn('Đã kết nối', WindowsApp.adapter_menu_label(row))

    def wifi_result(self):
        return dict(ok=True, message='Chọn mạng Wi-Fi',
                    adapter_id='11111111-1111-1111-1111-111111111111',
                    networks=[dict(ssid='Nhà & Office', profile='Saved Profile',
                                   signal=87, connectable=True, connected=False)])

    def test_saved_wifi_selection_uses_exact_card_profile_and_ssid(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.start_network = Mock()
            app.open_wifi_settings = Mock()
            app.user.CreatePopupMenu.return_value = 1
            app.user.TrackPopupMenu.return_value = 6001
            result = self.wifi_result()
            app.wifi_menu(result)
            app.start_network.assert_called_once_with(
                'wifi_connect', adapter_id=result['adapter_id'],
                profile_name='Saved Profile', ssid='Nhà & Office')
            app.open_wifi_settings.assert_not_called()
            app.user.DestroyMenu.assert_called_once_with(1)
            app.read_clipboard.assert_not_called()
            app.client.ask.assert_not_called()

    def test_unsaved_wifi_selection_opens_windows_settings_without_connecting(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.start_network = Mock()
            app.open_wifi_settings = Mock()
            app.user.TrackPopupMenu.return_value = 6001
            result = self.wifi_result()
            result['networks'][0]['profile'] = ''
            app.wifi_menu(result)
            app.open_wifi_settings.assert_called_once_with()
            app.start_network.assert_not_called()
            app.network.perform.assert_not_called()
            labels = [call.args[3] for call in app.user.AppendMenuW.call_args_list]
            self.assertTrue(any('Chưa lưu · thiết lập trong Windows' in label for label in labels))

    def test_dismissing_wifi_picker_has_no_mutation_or_settings_window(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.start_network = Mock()
            app.open_wifi_settings = Mock()
            app.user.TrackPopupMenu.return_value = 0
            app.wifi_menu(self.wifi_result())
            app.start_network.assert_not_called()
            app.network.perform.assert_not_called()
            app.open_wifi_settings.assert_not_called()
            app.read_clipboard.assert_not_called()

    def test_wifi_refresh_is_read_only_and_saved_unavailable_network_is_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.start_network = Mock()
            app.open_wifi_settings = Mock()
            app.user.TrackPopupMenu.return_value = 6998
            result = self.wifi_result()
            result['networks'][0]['connectable'] = False
            app.wifi_menu(result)
            app.start_network.assert_called_once_with(
                'wifi_scan', adapter_id=result['adapter_id'], show_picker='wifi')
            network_entry = next(call for call in app.user.AppendMenuW.call_args_list
                                 if call.args[2] == 6001)
            self.assertEqual(network_entry.args[1] & 1, 1)
            app.open_wifi_settings.assert_not_called()

    def test_card_selection_and_ssid_scan_are_distinct_menu_actions(self):
        for choice, target, show_picker in ((5001, 'wifi', False),
                                            (5002, 'wifi_scan', 'wifi'), (0, None, None)):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as root:
                app = self.app(root)
                wifi_id = '11111111-1111-1111-1111-111111111111'
                app.network.adapters = [
                    dict(id=wifi_id, name='Wi-Fi', description='Wireless', kind='wifi',
                         status='Disconnected', enabled=True),
                    dict(id='22222222-2222-2222-2222-222222222222', name='LAN',
                         description='Ethernet', kind='lan', status='Up', enabled=True)]
                app.start_network = Mock()
                app.open_wifi_settings = Mock()
                app.user.TrackPopupMenu.return_value = choice
                app.network_menu()
                if target:
                    app.start_network.assert_called_once_with(
                        target, adapter_id=wifi_id, show_picker=show_picker)
                else:
                    app.start_network.assert_not_called()
                app.open_wifi_settings.assert_not_called()
                entries = {call.args[2]: call.args for call in app.user.AppendMenuW.call_args_list}
                self.assertIn('Chưa kết nối', entries[5001][3])
                self.assertEqual(entries[5001][1] & 8, 0)
                self.assertIn('SSID', entries[5002][3])
                # A check marks exclusive selection; both physical cards are enabled here.
                self.assertEqual(entries[5003][1] & 8, 0)
                self.assertIn('Đã kết nối', entries[5003][3])
                app.network.perform.assert_not_called()

    def test_wifi_scan_during_ai_keeps_answer_and_processing_status(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            app.state = 'Đang trả lời'
            app.pending_write = pending = ('Completed answer', 12, fingerprint('Question input'))
            with patch('windows_native.threading.Thread') as thread:
                app.start_network('wifi_scan', adapter_id='card-id', show_picker='wifi')
            thread.return_value.start.assert_called_once()
            self.assertEqual(app.state, 'Đang trả lời')
            self.assertIs(app.pending_write, pending)
            app.wifi_menu = Mock()
            app.write_clipboard = Mock(return_value=False)
            scan = self.wifi_result()
            app.results.put(('network', scan, 'wifi'))
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.state, 'Đang trả lời')
            self.assertIs(app.pending_write, pending)
            self.assertFalse(app.network_busy)
            app.wifi_menu.assert_called_once_with(scan)

    def test_saved_wifi_connect_is_blocked_while_ai_is_running(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = True
            with patch('windows_native.threading.Thread') as thread:
                app.start_network('wifi_connect', adapter_id='card-id',
                                  profile_name='Saved Profile', ssid='SSID')
            thread.assert_not_called()
            app.network.perform.assert_not_called()
            self.assertIn('F10', app.state)
            self.assertFalse(app.network_busy)


if __name__ == '__main__':
    unittest.main()
