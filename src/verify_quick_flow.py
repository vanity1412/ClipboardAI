"""Native hidden-window smoke test; synthetic input and no real clipboard/API writes.

Only Explorer's tray registration is mocked because the tool host cannot register
an icon there. The actual Windows windows, hover popup and callbacks are tested.
"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chat_modes import CHAT, ANALYSIS


def run(native):
    original_root, app = native.ROOT, None
    user, kernel, shell = native.setup_winapi()
    tray = SimpleNamespace(Shell_NotifyIconW=Mock(return_value=True))
    report = dict(ok=False, real_api_calls=0, real_clipboard_writes=0,
                  real_desktop_capture=False, explorer_tray_mocked=True)
    try:
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_QuickVerify_') as directory:
            native.ROOT = Path(directory)
            with patch.object(native, 'setup_winapi', return_value=(user, kernel, tray)), \
                    patch.object(native.WindowsApp, 'register_hotkeys', lambda app: None):
                app = native.WindowsApp(self_test=True, config_override={
                    'BACKEND': 'DeepSeek', 'DEEPSEEK_MODEL': 'deepseek-flash', 'F4_CAPTURE': 'window',
                    'DEEPSEEK_API_KEY': 'test', 'ANSWER_STYLE': 'short'})
            # Avoid worker races when testing synthetic done/hover callbacks.
            app.read_clipboard = Mock(return_value=('Câu 1: Chọn phương án đúng', 12))
            def copied(*args):
                app.display_state().copy = 'copied'
                return True
            app.write_clipboard = Mock(side_effect=copied)
            app.client.ask = Mock(return_value=('Câu 1: 2\nCâu 2: A', 'synthetic'))
            self_test, app.self_test = app.self_test, False
            assert app.session.mode == CHAT and app.session.auto_copy
            assert not app.user.IsWindowVisible(app.hwnd)
            foreground = app.user.GetForegroundWindow()
            app.send_clipboard()
            until = time.monotonic() + 3
            while app.busy and time.monotonic() < until:
                app.tick()
                time.sleep(.01)
            assert not app.busy
            app.write_clipboard.assert_called_once()
            assert app.session.last_answer == 'Câu 1: 2\nCâu 2: A'
            assert 'không có nhãn' in app.client.ask.call_args.kwargs['instruction']
            assert not app.user.IsWindowVisible(app.hwnd)
            assert app.user.IsWindowVisible(app.notice), 'Completed answer popup did not appear'
            assert app.user.GetForegroundWindow() == foreground, 'Completed answer popup took focus'
            with patch('windows_native.time.monotonic', return_value=app.notice_until + .01):
                app.update_notice()
            assert not app.user.IsWindowVisible(app.notice)
            report.update(auto_copy=True, concise_instruction=True, completion_popup=True,
                          completion_popup_expires=True, completion_popup_does_not_take_focus=True,
                          panel_stays_hidden=True)
            calls = app.client.ask.call_count
            app.window_proc(app.hwnd, 0x0312, 215, 0)
            app.tick()
            assert app.client.ask.call_count == calls
            assert app.write_clipboard.call_count == 2
            assert app.write_clipboard.call_args.args[0] == 'Câu 1: 2\nCâu 2: A'
            assert not app.user.IsWindowVisible(app.hwnd)
            report['copy_last_without_api'] = True
            foreground = app.user.GetForegroundWindow()
            app.window_proc(app.hwnd, 0x8001, 0, (1 << 16) | 0x406)
            assert app.hover_window and app.user.IsWindowVisible(app.hover_window)
            length = app.user.GetWindowTextLengthW(app.hover_window)
            value = C.create_unicode_buffer(length + 1)
            app.user.GetWindowTextW(app.hover_window, value, length + 1)
            assert 'Đã copy' in value.value and '1. 2\r\n2. A' in value.value
            rect = W.RECT()
            app.user.GetWindowRect(app.hover_window, C.byref(rect))
            assert 84 <= rect.right - rect.left <= 330 and rect.bottom - rect.top <= 110
            report.update(compact_hover=True, hover_width=rect.right - rect.left,
                          hover_height=rect.bottom - rect.top)
            original_answer = app.session.last_answer
            app.session.last_answer = (
                '**Câu 4.** Một câu hỏi rất dài?\\n→ **Chọn d. Phương án đầy đủ**\n'
                '**Câu 5.** Câu hỏi tiếp?\\n→ **Chọn b. Nội dung**\n'
                '**Câu 6.** Câu hỏi tiếp?\\n→ **Chọn a.** Nội dung dài\n'
                '**Câu 7.** Câu hỏi tiếp?\\n→ **Chọn a. Nội dung**').replace('\\n', '\n')
            app.display_state().finish('done', 18, app.session.last_answer, copy='copied')
            app.refresh_tray_result()
            length = app.user.GetWindowTextLengthW(app.hover_window)
            value = C.create_unicode_buffer(length + 1)
            app.user.GetWindowTextW(app.hover_window, value, length + 1)
            assert value.value == 'Đã copy · 18s\r\n4. d\r\n5. b\r\n6. a\r\n7. a'
            app.user.GetWindowRect(app.hover_window, C.byref(rect))
            assert 84 <= rect.right - rect.left <= 330 and rect.bottom - rect.top <= 110
            getter = app.user.GetWindowLongW
            getter.argtypes, getter.restype = [W.HWND, C.c_int], C.c_long
            assert not getter(app.hover_window, -16) & 0x00800000, 'Hover has an outer border'
            dc = app.user.GetDC(app.hover_window)
            try:
                app.window_proc(app.hwnd, 0x0138, dc, app.hover_window)
                app.gdi.GetTextColor.argtypes, app.gdi.GetTextColor.restype = [W.HDC], W.DWORD
                app.gdi.GetBkColor.argtypes, app.gdi.GetBkColor.restype = [W.HDC], W.DWORD
                assert app.gdi.GetTextColor(dc) == 0xB8B8B8
                assert app.gdi.GetBkColor(dc) == 0xFFFFFF
            finally:
                app.user.ReleaseDC(app.hover_window, dc)
            report.update(hover_borderless=True, hover_background='#FFFFFF', hover_text_color='#B8B8B8')
            partial = ('1. Artificial Intelligence\n2. Machine\n3. Chưa xác định (ảnh bị cắt)\n'
                       '4. Chưa xác định (ảnh bị cắt)\n5. Generative Pre-trained Transformer\n'
                       '6. Chưa xác định (ảnh bị cắt)')
            app.display_state().finish('done', 34, partial, copy='copied')
            app.refresh_tray_result()
            length = app.user.GetWindowTextLengthW(app.hover_window)
            value = C.create_unicode_buffer(length + 1)
            app.user.GetWindowTextW(app.hover_window, value, length + 1)
            assert '1. Artificial Intelligence' in value.value and '2. Machine' in value.value
            assert '5. Generative Pre-trained' in value.value and 'Chưa rõ: 3, 4, 6' in value.value
            assert app.display_state().answer == partial
            app.user.GetWindowRect(app.hover_window, C.byref(rect))
            assert rect.right - rect.left <= 330 and rect.bottom - rect.top <= 110
            report.update(partial_answers_visible=True, partial_hover_width=rect.right - rect.left)
            # Capture only this synthetic test popup for appearance verification.
            from PIL import ImageGrab
            app.user.UpdateWindow.argtypes = [W.HWND]
            app.user.UpdateWindow(app.hover_window)
            preview = ImageGrab.grab(window=app.hover_window)
            colors = set(preview.convert('RGB').get_flattened_data())
            assert (255, 255, 255) in colors and (184, 184, 184) in colors
            preview.save(original_root / 'hover-preview.png')
            def hover_text():
                length = app.user.GetWindowTextLengthW(app.hover_window)
                value = C.create_unicode_buffer(length + 1)
                app.user.GetWindowTextW(app.hover_window, value, length + 1)
                return value.value
            ten_choices = '\n'.join(f'{i}. B' if i != 5 else '5. Chưa xác định' for i in range(1, 11))
            app.display_state().finish('done', 34, ten_choices, copy='copied')
            app.refresh_tray_result()
            value = hover_text()
            assert len(app.hover_pages) == 1 and app.hover_pages[0].columns == 3
            assert len(value.splitlines()) == 5 and 'Chưa rõ: 5' in value
            for i in range(1, 11):
                assert f'{i}. ' + ('?' if i == 5 else 'B') in value
            assert '…' not in value
            app.user.GetWindowRect(app.hover_window, C.byref(rect))
            assert rect.right - rect.left <= 330 and rect.bottom - rect.top <= 110
            app.user.UpdateWindow(app.hover_window)
            ImageGrab.grab(window=app.hover_window).save(original_root / 'hover-ten-answers.png')
            report.update(ten_choices_one_page=True, unknown_choice_keeps_cell=True,
                          three_columns=True, ten_choices_height=rect.bottom - rect.top)
            ten_fill = '\n'.join(f'{i}. Machine' for i in range(1, 11))
            app.display_state().finish('done', 34, ten_fill, copy='copied')
            app.refresh_tray_result()
            assert len(app.hover_pages) == 1 and app.hover_pages[0].columns == 2
            assert '10. Machine' in hover_text() and len(hover_text().splitlines()) == 6
            app.user.GetWindowRect(app.hover_window, C.byref(rect))
            assert rect.right - rect.left <= 330 and rect.bottom - rect.top <= 134
            report.update(ten_fill_answers_one_page=True, two_columns=True,
                          ten_fill_height=rect.bottom - rect.top)
            long_fill = '\n'.join(f'{i}. ' + 'Generative Pre-trained Transformer ' * 3 for i in range(1, 11))
            app.display_state().finish('done', 34, long_fill, copy='copied')
            with patch('windows_native.time.monotonic', return_value=200):
                app.refresh_tray_result()
            count = len(app.hover_pages)
            assert count > 1 and hover_text().startswith(f'1/{count}')
            collected = []
            for index in range(count):
                with patch('windows_native.time.monotonic', return_value=200 + 5 * index):
                    app.refresh_tray_result()
                value = hover_text()
                assert value.startswith(f'{index + 1}/{count}') and '…' not in value
                collected.extend(value.splitlines()[1:])
            assert ''.join(''.join(collected).split()) == ''.join(long_fill.split())
            assert app.display_state().answer == long_fill
            app.hide_tray_result()
            with patch('windows_native.time.monotonic', return_value=500):
                app.show_tray_result()
            assert hover_text().startswith(f'1/{count}')
            report.update(long_answers_all_pages_readable=True, hover_pages_every_five_seconds=True,
                          reopening_resets_first_page=True, pagination_keeps_clipboard_answer=True)
            app.busy, app.preview_answer = True, 'SECRET STREAM CONTENT'
            app.display_state().begin(image=True)
            assert 's' not in app.display_state().header().split('·')[-1][-1:]
            app.display_state().begin_model(100)
            for now, seconds in ((100, 0), (101, 1), (104, 4)):
                with patch('request_display.time.monotonic', return_value=now):
                    app.refresh_tray_result()
                length = app.user.GetWindowTextLengthW(app.hover_window)
                value = C.create_unicode_buffer(length + 1)
                app.user.GetWindowTextW(app.hover_window, value, length + 1)
                assert f'Model đang xử lý · {seconds}s' in value.value
                assert 'SECRET' not in value.value
            app.busy, app.session.last_answer = False, original_answer
            app.display_state().finish('done', 18, original_answer, copy='copied')
            app.refresh_tray_result()
            report.update(verbose_answers_compacted=True, streaming_content_hidden=True,
                          model_seconds_realtime=True, capture_time_excluded=True)
            # Real native menu item updates, without opening a menu over the user's desktop.
            menu = app.user.CreatePopupMenu()
            try:
                app.active_status_menu = menu
                app.last_menu_status = 'Model đang xử lý · 0s'
                app.user.AppendMenuW(menu, 1, 0, app.last_menu_status)
                app.display_state().begin_model(100)
                app.display_state().phase = 'running'
                with patch('request_display.time.monotonic', return_value=103):
                    app.refresh_status_menu()
                app.user.GetMenuStringW.argtypes = [W.HMENU, W.UINT, W.LPWSTR, C.c_int, W.UINT]
                label = C.create_unicode_buffer(150)
                app.user.GetMenuStringW(menu, 0, label, len(label), 0x400)
                assert label.value == 'Model đang xử lý · 3s'
                report['native_menu_header_updates'] = True
            finally:
                app.active_status_menu = None
                app.user.DestroyMenu(menu)
                app.display_state().finish('done', 18, original_answer, copy='copied')
            app.display_state().finish('failed', 9, error='Tài khoản API hết số dư/quota (402)')
            assert 'Lỗi 402' in app.result_text() and 'Câu 1' not in app.result_text()
            app.display_state().finish('done', 1, 'Chưa xác định (không đọc rõ)', copy='copied')
            assert 'không đọc rõ' in app.result_text() and 'Shift+F9' in app.result_text()
            app.display_state().finish('done', 18, original_answer, copy='copied')
            report.update(old_answer_hidden_on_error=True, unresolved_has_recovery_hint=True)
            assert app.user.GetForegroundWindow() == foreground
            app.window_proc(app.hwnd, 0x8001, 0, (1 << 16) | 0x407)
            assert not app.user.IsWindowVisible(app.hover_window)
            app.window_proc(app.hwnd, 0x8001, 0, (1 << 16) | 0x406)
            assert app.user.IsWindowVisible(app.hover_window), 'Hover did not reopen'
            app.hide_tray_result()
            report.update(native_hover_text=True, hover_hides_on_leave=True,
                          hover_reopens=True, hover_does_not_take_focus=True)
            ident = app.session.active_id
            app.images.add(ident, b'first-synthetic-image')
            history = list(app.session.messages)
            with patch('screen_capture.capture_foreground_png', return_value=b'second-synthetic-image'):
                app.send_screenshot(app.hwnd, append=True)
                until = time.monotonic() + 3
                while app.busy and time.monotonic() < until:
                    app.tick()
                    time.sleep(.01)
            assert not app.busy and app.session.active_id == ident
            content, sent_history = app.client.ask.call_args.args[:2]
            assert sent_history == history
            assert len(content) == 3 and 'Ảnh mới bổ sung' in content[0]['text']
            assert app.images.get(ident) == [b'first-synthetic-image', b'second-synthetic-image']
            assert app.write_clipboard.call_count == 3
            assert not app.user.IsWindowVisible(app.hwnd)
            report.update(append_image_same_session=True, append_image_keeps_history=True,
                          append_image_sends_old_and_new=True, append_image_auto_copy=True)
            from screen_capture import CaptureError
            calls, copies = app.client.ask.call_count, app.write_clipboard.call_count
            with patch('screen_capture.capture_foreground_png', side_effect=CaptureError('Ảnh chụp toàn đen; chưa gửi AI')):
                app.send_screenshot(app.hwnd)
                until = time.monotonic() + 3
                while app.busy and time.monotonic() < until:
                    app.tick()
                    time.sleep(.01)
            assert not app.busy and 'Ảnh đen' in app.result_text()
            assert app.client.ask.call_count == calls and app.write_clipboard.call_count == copies
            report['black_capture_no_api_or_clipboard_write'] = True
            assert app.user.SendMessageW(app.controls['mode'], 0x146, 0, 0) == 2
            labels = []
            original_append = app.user.AppendMenuW
            def record_menu(handle, flags, ident, label):
                labels.append(label)
                return original_append(handle, flags, ident, label)
            with patch.object(app.user, 'AppendMenuW', side_effect=record_menu), patch.object(app.user, 'TrackPopupMenu', return_value=0):
                app.menu()
            assert labels.count('Hỏi đáp / phân tích') == 1 and labels.count('Lập trình') == 1
            assert sum(isinstance(label, str) and label.startswith('Prompt đang dùng') for label in labels) == 1
            assert not any(isinstance(label, str) and label.startswith('Dùng chế độ') for label in labels)
            app.set_mode_prompt(CHAT, 'custom', 'SYNTHETIC MENU PROMPT')
            app.set_reasoning('careful')
            from prompt_profiles import instruction_for
            from chat_modes import reply_config
            assert instruction_for(CHAT, app.config) == 'SYNTHETIC MENU PROMPT'
            assert reply_config(app.config, CHAT)['REPLY_MODE'] == ANALYSIS
            app.set_reasoning('fast')
            assert instruction_for(CHAT, app.config) == 'SYNTHETIC MENU PROMPT'
            assert reply_config(app.config, CHAT)['REPLY_MODE'] == CHAT
            assert any(item['text'] == 'SYNTHETIC MENU PROMPT' for item in app.config['SAVED_PROMPTS'])
            with patch.object(app.user, 'TrackPopupMenu', return_value=0):
                app.menu()
            saved_ident = next(ident for ident, item in app.saved_prompt_commands.items()
                               if item['text'] == 'SYNTHETIC MENU PROMPT')
            assert 50000 <= saved_ident < 50064
            app.command(saved_ident)
            assert instruction_for(CHAT, app.config) == 'SYNTHETIC MENU PROMPT'
            assert app.client.ask.call_count == calls and app.write_clipboard.call_count == copies
            report.update(merged_reply_menu=True, two_purpose_choices=True,
                          one_prompt_submenu=True, reasoning_independent_of_prompt=True,
                          saved_prompt_library=True)
            app.self_test = self_test
            report['ok'] = True
    finally:
        if app:
            app.user.DestroyWindow(app.hwnd)
            app.kernel.CloseHandle(app.mutex)
        native.ROOT = original_root
    return report


if __name__ == '__main__':
    import json
    import windows_native as native
    print(json.dumps(run(native), ensure_ascii=True, indent=2))
