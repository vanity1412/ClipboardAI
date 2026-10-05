from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from PIL import Image

from hotkey_settings import bindings, parse_shortcut, DEFAULTS, normalized_shortcuts
from region_capture import crop_bounds
from runtime_settings import validated_preferences
from screen_capture import CaptureError, encode_png
from tests import test_selected_fixes as fixtures


class RegionHotkeyTests(unittest.TestCase):
    def test_reverse_drag_and_negative_monitor_preserve_exact_pixels(self):
        self.assertEqual(crop_bounds((-120, 250), (-420, -20), (-500, -100, 200, 500)), (80, 80, 380, 350))

    def test_drag_clips_to_frozen_window_and_rejects_tiny_region(self):
        self.assertEqual(crop_bounds((-10, -10), (300, 400), (0, 0, 200, 200)), (0, 0, 200, 200))
        with self.assertRaises(CaptureError):
            crop_bounds((5, 5), (6, 80), (0, 0, 200, 200))

    def test_black_crop_rejected_and_code_crop_kept(self):
        image = Image.new('RGB', (80, 60), 'black')
        with self.assertRaises(CaptureError):
            encode_png(image)
        image.putpixel((15, 15), (255, 255, 255))
        self.assertEqual(Image.open(BytesIO(encode_png(image))).size, (80, 60))

    def test_duplicate_and_reserved_shortcuts_rejected(self):
        for settings in ({'201': 'F9'}, {'204': 'Alt+F4'}, {'201': 'A'}, {'201': 'Shift+A'}, {'201': 'F12'}):
            with self.assertRaises(ValueError):
                bindings(settings)
        self.assertEqual(parse_shortcut('alt+CTRL+q'), ('Ctrl+Alt+Q', 3, 81))
        self.assertEqual(bindings({'201': 'Ctrl+Alt+Q'})[201], ('Ctrl+Alt+Q', 3, 81))

    def test_preferences_reject_invalid_bindings_keep_other_settings(self):
        values, invalid = validated_preferences({'HOTKEYS': {'201': 'F9'}, 'F4_CAPTURE': 'region', 'REGION_CUE': 'clear'})
        self.assertEqual(invalid, ['HOTKEYS'])
        self.assertEqual(values, {'F4_CAPTURE': 'region', 'REGION_CUE': 'clear'})

    def test_hotkey_registration_and_save_failure_roll_back(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hotkeys = list(bindings())
            app.user.RegisterHotKey.side_effect = lambda hwnd, ident, modifiers, key: key != 81
            app.save_input_preferences = Mock()
            with self.assertRaises(ValueError):
                app.apply_hotkeys({'201': 'Ctrl+Alt+Q'})
            self.assertEqual(app.config['HOTKEYS'], {})
            self.assertEqual(len(app.hotkeys), 9)
            app.save_input_preferences.assert_not_called()
            app.user.RegisterHotKey.side_effect = None
            app.user.RegisterHotKey.return_value = True
            app.save_input_preferences.side_effect = OSError('test failure')
            with self.assertRaises(OSError):
                app.apply_hotkeys({'201': 'Ctrl+Alt+Q'})
            self.assertEqual(app.config['HOTKEYS'], {})
            self.assertEqual(len(app.hotkeys), 9)

    def test_region_cancel_keeps_session_clipboard_and_pending_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.region_pending = True
            app.region_cancel = threading.Event()
            app.pending_write = ('waiting answer', 12, None)
            app.write_clipboard = Mock()
            previous = Path(app.session.path).read_bytes()
            previous_id = app.current_id
            app.cancel_request()
            self.assertTrue(app.region_cancel.is_set())
            self.assertEqual(app.pending_write, ('waiting answer', 12, None))
            self.assertEqual(app.current_id, previous_id)
            self.assertEqual(Path(app.session.path).read_bytes(), previous)
            app.write_clipboard.assert_not_called()
            app.client.cancel.assert_not_called()

    def test_repeated_region_hotkey_does_not_start_another_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.region_pending = True
            app.start_region_capture = Mock()
            app.send_screenshot()
            app.start_region_capture.assert_not_called()
            self.assertFalse(app.busy)

    def test_selected_image_preserves_clipboard_baseline_and_append_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.region_pending = True
            app.region_token = 3
            app.start_request = Mock()
            png = encode_png(Image.new('RGB', (100, 80), 'white'))
            result = ('region_done', 3, threading.Event(), png, '', 'Supplement image', 42, b'baseline', True, app.session.active_id)
            app.finish_region_capture(result)
            self.assertFalse(app.region_pending)
            app.start_request.assert_called_once_with('Supplement image', sequence=42, digest=b'baseline', image_png=png,
                                                     replace=True, action='image', new_session=False)
            app.finish_region_capture(result)
            self.assertEqual(app.start_request.call_count, 1)

    def test_cancelled_late_image_and_stale_token_cannot_submit(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.region_pending = True
            app.region_token = 5
            app.region_clipboard_hold = True
            app.start_request = Mock()
            cancel = threading.Event()
            cancel.set()
            old_session = app.session.active_id
            app.finish_region_capture(('region_done', 4, cancel, b'late image', '', 'Question', 12, None, False, old_session))
            self.assertTrue(app.region_pending)
            app.finish_region_capture(('region_done', 5, cancel, b'late image', '', 'Question', 12, None, False, old_session))
            app.start_request.assert_not_called()
            self.assertFalse(app.region_pending)
            self.assertTrue(app.region_clipboard_hold)
            self.assertEqual(app.session.active_id, old_session)

    def test_region_menu_does_not_replace_network_recovery_or_legacy_styles(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.save_input_preferences = Mock()
            app.open_adapter_settings = Mock()
            with patch('windows_native.log_event'):
                app.command(680)
            app.save_input_preferences.assert_called_once_with({'F4_CAPTURE': 'region'})
            with patch('windows_native.log_event'):
                app.command(233)
            app.open_adapter_settings.assert_called_once()
            app.save_input_preferences.assert_called_once()

    def test_hotkey_editor_save_leaves_keys_free_until_editor_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hotkey_open = True
            app.hotkeys = []
            app.user.RegisterHotKey.return_value = True
            app.save_input_preferences = Mock()
            app.apply_hotkeys({'201': 'Ctrl+Alt+Q'})
            self.assertEqual(app.hotkeys, [])
            self.assertEqual(app.user.UnregisterHotKey.call_count, 9)
            self.assertEqual(app.config['HOTKEYS']['201'], 'Ctrl+Alt+Q')

    def test_changed_shortcuts_appear_in_hints_without_rewriting_answer(self):
        from request_display import RequestDisplay
        display = RequestDisplay(shortcut_names={'Shift+F8': 'Ctrl+Alt+C', 'F10': 'Ctrl+Alt+X'})
        display.finish('done', 2, 'F10 is a key in this code example', copy='changed')
        self.assertIn('Ctrl+Alt+C để copy', display.preview())
        self.assertEqual(display.answer, 'F10 is a key in this code example')
        display.begin()
        self.assertIn('Ctrl+Alt+X', display.preview())

    def test_region_reads_clipboard_baseline_before_selector_and_keeps_image_only(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.config['F4_CAPTURE'] = 'region'
            app.hide_tray_result = Mock()
            app.start_request = Mock()
            png = encode_png(Image.new('RGB', (100, 80), 'white'))
            with patch('region_capture.select_region', return_value=png):
                app.send_screenshot()
                result = app.results.get(timeout=2)
            self.assertTrue(app.region_pending)
            app.start_request.assert_not_called()
            app.read_clipboard.return_value = ('Different copied content', 99)
            app.finish_region_capture(result)
            kwargs = app.start_request.call_args.kwargs
            self.assertEqual(kwargs['sequence'], 12)
            self.assertEqual(kwargs['image_png'], png)
            self.assertNotIn('Question input', app.start_request.call_args.args[0])
            self.assertTrue(kwargs['new_session'])
            app.client.ask.assert_not_called()

    def test_hotkey_recording_does_not_send_ai_request(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hotkey_open = True
            app.send_clipboard = Mock()
            app.window_proc(app.hwnd, 0x0312, 201, 0)
            app.send_clipboard.assert_not_called()
            app.client.ask.assert_not_called()

    def test_changed_api_while_selecting_cannot_redirect_existing_image(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.region_pending, app.region_token = True, 4
            app.region_config_snapshot = dict(app.config)
            app.region_mode_snapshot = app.session.mode
            app.config['SELECTED_MODEL'] = 'different-provider'
            app.start_request = Mock()
            app.finish_region_capture(('region_done', 4, threading.Event(), b'prepared image', '',
                                       'Question', 12, None, False, app.session.active_id))
            app.start_request.assert_not_called()
            self.assertIn('Cấu hình AI đã đổi', app.region_notice)

    def test_chat_shortcut_removed_without_changing_other_defaults(self):
        self.assertNotIn(212, DEFAULTS)
        self.assertNotIn(212, bindings({'212': 'F7', '201': 'Ctrl+Alt+Q'}))
        self.assertEqual(bindings()[201][0], 'F8')
        self.assertEqual(bindings()[204][0], 'F4')
        self.assertEqual(bindings()[209][0], 'F3')
        self.assertEqual(bindings()[213][0], 'Shift+F10')

    def test_disabled_shortcuts_are_not_registered_but_custom_keys_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.config.update(HOTKEYS={'201': 'Ctrl+Alt+Q', '212': 'F7'}, HOTKEYS_DISABLED=['201', '209'])
            app.user.RegisterHotKey.return_value = True
            app.register_hotkeys()
            self.assertEqual(len(app.hotkeys), 7)
            self.assertNotIn(201, app.hotkeys)
            self.assertNotIn(209, app.hotkeys)
            self.assertNotIn(212, app.hotkeys)
            self.assertEqual(normalized_shortcuts(app.config['HOTKEYS'])['201'], 'Ctrl+Alt+Q')
            self.assertEqual(app.key_label(201), 'menu tray')

    def test_disabled_duplicates_only_conflict_when_enabled(self):
        self.assertNotIn(201, bindings({'201': 'F9'}, ['201']))
        with self.assertRaises(ValueError):
            bindings({'201': 'F9'}, [])

    def test_disabled_preferences_round_trip_and_migrate_old_chat_key(self):
        values, issues = validated_preferences({'HOTKEYS': {'201': 'Ctrl+Alt+Q', '212': 'F7'},
                                              'HOTKEYS_DISABLED': ['201', '212']})
        self.assertFalse(issues)
        self.assertEqual(values['HOTKEYS_DISABLED'], ['201'])
        self.assertNotIn('212', values['HOTKEYS'])
        self.assertEqual(values['HOTKEYS']['201'], 'Ctrl+Alt+Q')
        with tempfile.TemporaryDirectory() as folder:
            from runtime_settings import save_preferences, load_runtime_settings
            path = Path(folder) / 'preferences.json'
            save_preferences(path, values)
            config = {}
            self.assertFalse(load_runtime_settings(config, path))
            self.assertNotIn(201, bindings(config['HOTKEYS'], config['HOTKEYS_DISABLED']))
            self.assertEqual(bindings(config['HOTKEYS'], [])[201][0], 'Ctrl+Alt+Q')

    def test_invalid_disabled_preferences_are_rejected(self):
        for disabled in ('201', [201], ['999'], {}, False):
            with self.subTest(disabled=disabled):
                values, issues = validated_preferences({'HOTKEYS_DISABLED': disabled})
                self.assertEqual(issues, ['HOTKEYS_DISABLED'])
                self.assertNotIn('HOTKEYS_DISABLED', values)

    def test_all_shortcuts_can_be_disabled_and_menu_commands_still_work(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hotkeys = list(DEFAULTS)
            app.save_input_preferences = Mock()
            app.apply_hotkeys({}, [str(i) for i in DEFAULTS])
            self.assertEqual(app.hotkeys, [])
            app.user.RegisterHotKey.assert_not_called()
            self.assertEqual(app.shortcut_help(), 'Phím tắt đã tắt · dùng menu tray')
            app.open_network_picker = Mock()
            app.command(209)
            app.open_network_picker.assert_called_once()

    def test_removed_or_disabled_queued_hotkeys_cannot_trigger_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.config['HOTKEYS_DISABLED'] = ['201']
            app.send_clipboard = Mock()
            app.toggle_panel = Mock()
            app.window_proc(app.hwnd, 0x0312, 201, 0)
            app.window_proc(app.hwnd, 0x0312, 212, 0)
            app.send_clipboard.assert_not_called()
            app.toggle_panel.assert_not_called()

    def test_failed_save_restores_disabled_actions_and_previous_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.config.update(HOTKEYS={'201': 'Ctrl+Alt+Q'}, HOTKEYS_DISABLED=['209'])
            app.hotkeys = list(bindings(app.config['HOTKEYS'], ['209']))
            app.user.RegisterHotKey.return_value = True
            app.save_input_preferences = Mock(side_effect=OSError('test failure'))
            with self.assertRaises(OSError):
                app.apply_hotkeys({}, ['201'])
            self.assertEqual(app.config['HOTKEYS_DISABLED'], ['209'])
            self.assertEqual(app.config['HOTKEYS']['201'], 'Ctrl+Alt+Q')
            self.assertIn(201, app.hotkeys)
            self.assertNotIn(209, app.hotkeys)

    def test_hotkey_error_stays_visible_after_other_status_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hotkey_errors = ['F8']
            app.state = 'Đã copy kết quả'
            self.assertIn('Phím chưa bật: F8', app.result_text())

    def test_paused_save_checks_occupied_keys_without_saving_or_resuming(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.enabled = False
            app.hotkey_open = True
            app.hotkeys, app.hotkey_errors = [], []
            app.config.update(HOTKEYS={'201': 'Ctrl+Alt+S'}, HOTKEYS_DISABLED=['209'])
            app.user.RegisterHotKey.side_effect = lambda hwnd, ident, modifiers, key: key != 81
            app.save_input_preferences = Mock()
            app.send_clipboard = Mock()
            with self.assertRaisesRegex(ValueError, 'Ctrl\\+Alt\\+Q'):
                app.apply_hotkeys({'201': 'Ctrl+Alt+Q'}, ['209'])
            self.assertFalse(app.enabled)
            self.assertEqual(app.hotkeys, [])
            self.assertEqual(app.hotkey_errors, [])
            self.assertEqual(app.config['HOTKEYS'], {'201': 'Ctrl+Alt+S'})
            self.assertEqual(app.config['HOTKEYS_DISABLED'], ['209'])
            app.save_input_preferences.assert_not_called()
            self.assertEqual(app.user.UnregisterHotKey.call_count, 7)
            app.window_proc(app.hwnd, 0x0312, 201, 0)
            app.send_clipboard.assert_not_called()

    def test_paused_save_and_editor_close_keep_shortcuts_paused(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.enabled = False
            app.hotkey_open = True
            app.hotkeys, app.hotkey_errors = [], []
            app.user.RegisterHotKey.return_value = True
            app.save_input_preferences = Mock()
            app.apply_hotkeys({'201': 'Ctrl+Alt+Q'})
            self.assertFalse(app.enabled)
            self.assertEqual(app.hotkeys, [])
            self.assertEqual(app.user.RegisterHotKey.call_count, 9)
            self.assertEqual(app.user.UnregisterHotKey.call_count, 9)
            self.assertEqual(app.config['HOTKEYS']['201'], 'Ctrl+Alt+Q')
            app.save_input_preferences.assert_called_once()
            app.user.RegisterHotKey.reset_mock()
            app.results.put(('hotkey_closed',))
            app.tick()
            self.assertFalse(app.enabled)
            self.assertFalse(app.hotkey_open)
            app.user.RegisterHotKey.assert_not_called()

    def test_paused_save_io_failure_keeps_previous_settings_and_keys_free(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.enabled = False
            app.hotkeys, app.hotkey_errors = [], []
            app.config.update(HOTKEYS={'201': 'Ctrl+Alt+S'}, HOTKEYS_DISABLED=['209'])
            app.user.RegisterHotKey.return_value = True
            app.save_input_preferences = Mock(side_effect=OSError('test failure'))
            with self.assertRaises(OSError):
                app.apply_hotkeys({'201': 'Ctrl+Alt+Q'})
            self.assertFalse(app.enabled)
            self.assertEqual(app.hotkeys, [])
            self.assertEqual(app.config['HOTKEYS'], {'201': 'Ctrl+Alt+S'})
            self.assertEqual(app.config['HOTKEYS_DISABLED'], ['209'])
            self.assertEqual(app.user.UnregisterHotKey.call_count, 9)
