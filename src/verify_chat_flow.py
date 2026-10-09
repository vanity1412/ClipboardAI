"""Real native chat window, synthetic content, mocked APIs, no clipboard writes."""
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import Mock, patch

from chat_modes import CODING, CHAT, ANALYSIS
from cloud_client import CloudClient, DEFAULT_MODELS


def run(native):
    original_root, app = native.ROOT, None
    report = dict(ok=False, real_api_calls=0, clipboard_writes=0, desktop_capture=False)
    try:
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_ChatVerify_') as directory:
            native.ROOT = Path(directory)
            legacy = dict(mode=0, auto_copy=True, problem='Legacy example', messages=[
                dict(role='user', content='Legacy example'), dict(role='assistant', content='int main(){}')],
                last_answer='int main(){}')
            (native.ROOT / 'session.json').write_text(json.dumps(legacy), encoding='utf-8')
            config = dict(BACKEND='DeepSeek', DEEPSEEK_MODEL='deepseek-flash', DEEPSEEK_API_KEY='test', F4_CAPTURE='window',
                          SELECTED_MODEL='deepseek-flash', MODEL_CHOICES=DEFAULT_MODELS)
            with patch.object(native.WindowsApp, 'register_hotkeys', lambda app: None):
                app = native.WindowsApp(self_test=True, config_override=config)
            app.self_test = False
            app.session.auto_copy = False
            app.read_clipboard = Mock(return_value=('Câu hỏi mẫu về một kế hoạch học tập.', 991))
            app.write_clipboard = Mock(return_value=True)  # Never change the real clipboard.
            app.client = CloudClient(dict(app.config))
            calls = []
            partial, release = threading.Event(), threading.Event()

            def post(url, body, timeout, key=None):
                calls.append(body)
                if body['messages'][-1]['content'] == 'Streaming sample':
                    app.client.on_stream(None)
                    app.client.on_stream('Đang viết từng phần')
                    partial.set()
                    release.wait(4)
                    answer = 'Đang viết từng phần — hoàn tất.'
                elif 'TÓM TẮT TRƯỚC:' in str(body['messages'][-1]['content']):
                    answer = 'Đã chọn kế hoạch học tập; cần giữ các quyết định trước.'
                else:
                    answer = 'Câu trả lời mẫu bằng tiếng Việt.'
                return {'choices': [{'finish_reason': 'stop', 'message': {'content': answer}}]}

            app.client.post = Mock(side_effect=post)
            app.user.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
            app.user.PeekMessageW.restype = W.BOOL

            def pump(check, timeout=5):
                until = time.monotonic() + timeout
                while time.monotonic() < until:
                    msg = W.MSG()
                    while app.user.PeekMessageW(C.byref(msg), None, 0, 0, 1):
                        app.user.TranslateMessage(C.byref(msg))
                        app.user.DispatchMessageW(C.byref(msg))
                    app.tick()
                    if check():
                        return
                    time.sleep(.01)
                raise AssertionError('Native chat check timed out')

            assert app.session.mode == CHAT
            assert (native.ROOT / 'session.before_chat.json').exists()
            old = next(record for record in app.session._sessions.values() if record['problem'] == 'Legacy example')
            assert old['mode'] == 0 and old['last_answer'] == 'int main(){}'
            report['legacy_backup_and_chat_default'] = True
            app.window_proc(app.hwnd, 0x0312, 212, 0)
            assert not app.user.IsWindowVisible(app.hwnd)
            with patch.object(app.user, 'TrackPopupMenu', return_value=3):
                app.menu()
            assert app.user.IsWindowVisible(app.hwnd)
            app.input_proc(app.controls['problem'], 0x100, 27, 0)
            assert not app.user.IsWindowVisible(app.hwnd)
            report['chat_manual_only'] = True
            # Native menu construction, no card mutation or API call.
            menu_rows = []
            original_append = app.user.AppendMenuW
            def record_menu(handle, flags, ident, label):
                menu_rows.append((handle, ident, label))
                return original_append(handle, flags, ident, label)
            with patch.object(app.user, 'AppendMenuW', side_effect=record_menu), patch.object(app.user, 'TrackPopupMenu', return_value=0):
                app.menu()
            root_menu = menu_rows[0][0]
            top_labels = [label for handle, ident, label in menu_rows if handle == root_menu]
            assert len(top_labels) <= 12 and len(top_labels) == len(set(top_labels))
            assert 'Hội thoại / thao tác phiên — F6' in top_labels
            assert 'Thao tác hội thoại' not in top_labels
            assert not any('Ưu tiên Wi-Fi' in label for handle, ident, label in menu_rows if isinstance(label, str))
            with patch.object(app.user, 'AppendMenuW', side_effect=record_menu), patch.object(app.user, 'TrackPopupMenu', return_value=0):
                app.session_menu()
            assert any(label == 'Xóa phiên…' for handle, ident, label in menu_rows)
            assert any(label == 'Gửi lại yêu cầu' for handle, ident, label in menu_rows)
            app.network.adapters = [dict(id='11111111-1111-1111-1111-111111111111', kind='wifi', name='Wi-Fi 1', enabled=True),
                dict(id='22222222-2222-2222-2222-222222222222', kind='wifi', name='Wi-Fi 2', enabled=False),
                dict(id='33333333-3333-3333-3333-333333333333', kind='lan', name='LAN', enabled=True)]
            with patch.object(app.user, 'TrackPopupMenu', return_value=5003), patch.object(app, 'start_network') as switch:
                app.network_menu()
                switch.assert_called_once_with('wifi', adapter_id='22222222-2222-2222-2222-222222222222', show_picker=False)
            report.update(compact_menu=True, session_actions_in_f6=True,
                          individual_adapter_picker=True, no_automatic_network_switch=True)
            foreground = app.user.GetForegroundWindow()
            app.send_clipboard()
            pump(lambda: not app.busy)
            ident = app.session.active_id
            assert len(app.session.messages) == 2
            assert not app.user.IsWindowVisible(app.hwnd)
            assert app.user.IsWindowVisible(app.notice), 'Completed answer popup did not appear'
            assert app.user.GetForegroundWindow() == foreground, 'Completed answer popup took focus'
            with patch('windows_native.time.monotonic', return_value=app.notice_until + .01):
                app.update_notice()
            assert not app.user.IsWindowVisible(app.notice), 'Completed answer popup did not expire'
            report.update(completion_popup=True, completion_popup_expires=True,
                          completion_popup_does_not_take_focus=True, panel_stays_hidden=True)
            assert 'copy và dán ngay' in calls[-1]['messages'][0]['content']
            assert calls[-1]['messages'][0]['content'] != native.CODE_PROMPT
            assert calls[-1]['thinking']['type'] == 'disabled'
            app.read_clipboard.return_value = ('Tóm tắt giúp tôi.', 992)
            app.reply_clipboard()
            pump(lambda: not app.busy)
            assert app.session.active_id == ident and len(app.session.messages) == 4
            assert calls[-1]['messages'][-1]['content'] == 'Tóm tắt giúp tôi.'
            assert 'Bạn:' in app.text('answer') and 'AI:' in app.text('answer')
            report.update(f8_new_session=True, f9_followup=True, no_coding_prompt=True, conversation_history=True)
            app.set_text('problem', 'Line one')
            before = len(calls)
            with patch.object(app.user, 'GetKeyState', return_value=-32768):
                app.user.SendMessageW(app.controls['problem'], 0x102, 13, 0)
            assert '\r\n' in app.text('problem') and len(calls) == before
            report['shift_enter_newline'] = True
            app.set_text('problem', 'Streaming sample')
            with patch.object(app.user, 'GetKeyState', return_value=0):
                app.user.SendMessageW(app.controls['problem'], 0x100, 13, 0)
            assert partial.wait(2)
            pump(lambda: 'Đang viết từng phần' in app.text('answer'))
            assert app.busy and app.session.last_answer != 'Đang viết từng phần'
            app.user.SendMessageW(app.controls['problem'], 0x100, 27, 0)
            assert not app.user.IsWindowVisible(app.hwnd) and app.busy
            report['escape_hides_without_cancel'] = True
            release.set()
            pump(lambda: not app.busy)
            assert len(app.session.messages) == 6
            app.resend_session()
            pump(lambda: not app.busy)
            assert len(app.session.messages) == 6, 'Retry duplicated a committed turn'
            report.update(enter_sends=True, stream_preview=True, retry_no_duplicate=True)
            app.change_mode(ANALYSIS)
            app.set_text('problem', 'Phân tích kế hoạch này.')
            app.command(106)
            pump(lambda: not app.busy)
            assert calls[-1]['reasoning_effort'] == 'high'
            assert 'copy và dán ngay' in calls[-1]['messages'][0]['content']
            report['analysis_without_persona'] = True
            app.change_mode(CHAT)
            from PIL import Image, ImageGrab
            image = BytesIO()
            Image.new('RGB', (200, 120), 'white').save(image, format='PNG')
            app.command(232)  # Exercise the opt-in image + clipboard path.
            before = len(calls)
            with patch('screen_capture.capture_foreground_png', return_value=image.getvalue()):
                app.send_screenshot(app.hwnd)
                pump(lambda: not app.busy)
            assert len(calls) == before + 1 and not app.pending_image, f'F4 calls={len(calls)-before}, state={app.state}'
            content = calls[-1]['messages'][-1]['content']
            assert content[0]['text'] == app.read_clipboard.return_value[0]
            assert content[1]['type'] == 'image_url'
            assert app.session.active_id != ident
            ident = app.session.active_id
            assert not app.user.IsWindowVisible(app.hwnd)
            app.resend_session()
            pump(lambda: not app.busy)
            assert calls[-1]['messages'][-1]['content'] == content
            app.change_mode(CODING)
            Image.new('RGB', (200, 120), 'blue').save(image, format='PNG')
            app.read_clipboard.return_value = ('Giải bằng Python và giải thích.', 993)
            with patch('screen_capture.capture_foreground_png', return_value=image.getvalue()):
                app.send_screenshot(app.hwnd)
                pump(lambda: not app.busy)
            content = calls[-1]['messages'][-1]['content']
            assert content[0]['text'] == 'Giải bằng Python và giải thích.'
            assert len(content) == 2
            ident = app.session.active_id
            assert calls[-1]['messages'][0]['content'] == native.CODE_PROMPT
            assert 'base64' not in app.session.path.read_text(encoding='utf-8')
            app.command(222)
            assert not app.images.get(ident)
            app.change_mode(CHAT)
            report.update(f4_image_and_clipboard_immediate=True, f4_new_session=True,
                f4_no_focus_steal=True, image_retry=True, independent_image_questions=True,
                programming_code_and_explanation=True, clear_images=True, image_not_in_session_json=True)
            before = len(calls)
            app.command(231)
            assert len(calls) == before and app.config['F4_INPUT'] == 'image'
            app.read_clipboard.return_value = ('CLIPBOARD_KHONG_DUOC_GUI_7835', 994)
            with patch('screen_capture.capture_foreground_png', return_value=image.getvalue()):
                app.send_screenshot(app.hwnd)
                pump(lambda: not app.busy)
            body = calls[-1]
            content = body['messages'][-1]['content']
            assert content[1]['type'] == 'image_url'
            assert 'theo kiểu đáp án đã chọn' in content[0]['text']
            assert 'kèm giải thích' not in content[0]['text']
            assert 'CLIPBOARD_KHONG_DUOC_GUI_7835' not in str(body)
            from runtime_settings import load_runtime_settings
            restored_config = {}
            assert not load_runtime_settings(restored_config, native.ROOT / 'preferences.json')
            assert restored_config['F4_INPUT'] == 'image'
            app.command(232)
            assert app.config['F4_INPUT'] == 'image_clipboard'
            report.update(f4_image_only_excludes_clipboard=True, f4_choice_saved=True,
                          image_multiple_choice_request=True)
            app.config['SESSION_MAX_CHARS'] = '6000'
            for i in range(24):
                app.session.messages.append(dict(role='user' if i % 2 == 0 else 'assistant', content=f'Mẫu {i}: ' + 'x' * 500))
            old_count = len(app.session.messages)
            app.set_text('problem', 'Tiếp tục dựa trên các quyết định trước.')
            app.command(106)
            pump(lambda: not app.busy)
            assert app.session.summary and app.session.summary_count > 0
            assert len(app.session.messages) == old_count + 2
            restored = native.Session(app.session.path)
            assert restored.messages == app.session.messages and restored.summary == app.session.summary
            report.update(long_history_summary=True, full_history_on_restart=True)
            app.set_text('problem', 'Một câu hỏi lỗi mẫu')
            app.client.post = Mock(side_effect=RuntimeError('Lỗi mẫu'))
            app.command(106)
            pump(lambda: not app.busy)
            assert app.text('problem') == 'Một câu hỏi lỗi mẫu'
            assert len(app.session.messages) == old_count + 2
            report['failed_turn_preserves_input_and_history'] = True
            entered, fail_release = threading.Event(), threading.Event()
            def delayed_failure(*args, **kwargs):
                entered.set()
                fail_release.wait(3)
                raise RuntimeError('Lỗi mẫu')
            app.client.post = Mock(side_effect=delayed_failure)
            app.set_text('problem', 'Câu hỏi đang chờ')
            app.command(106)
            assert entered.wait(2)
            app.set_text('problem', 'Bản nháp mới đang nhập')
            fail_release.set()
            pump(lambda: not app.busy)
            assert app.text('problem') == 'Bản nháp mới đang nhập'
            report['failed_turn_preserves_new_draft'] = True
            stale_id = app.current_id
            app.cancel_request()
            app.results.put(('stream', stale_id, 'stale'))
            app.tick()
            assert not app.preview_answer and 'stale' not in app.text('answer')
            report['cancel_discards_stale_stream'] = True
            from api_zoo import validate, apply_config
            old_config = dict(app.config)
            zoo = validate(dict(profiles=[dict(id='a', name='Test API', base_url='https://test.example/v1',
                                              api_key='test', model='chat', vision_model='vision')]))
            apply_config(app.config, zoo)
            app.config['SELECTED_MODEL'] = 'zoo:a'
            app.set_text('ctx', '12000')
            app.command(116)
            assert app.config['SESSION_MAX_CHARS'] == '12000'
            assert json.loads((native.ROOT / 'preferences.json').read_text(encoding='utf-8'))['SESSION_MAX_CHARS'] == '12000'
            report['zoo_context_settings_saved'] = True
            app.config = old_config
            app.config['SESSION_MAX_CHARS'] = '48000'
            app.session.messages = [dict(role='user', content='Mình muốn lập kế hoạch học tập.'),
                                    dict(role='assistant', content='Bạn có thể chia thành ba bước: xác định mục tiêu, chọn tài liệu và lên lịch ôn tập.')]
            app.session.last_answer = app.session.messages[-1]['content']
            app.state = 'Đang chờ'
            app.set_text('problem', '')
            app.show_panel()
            app.render_conversation()
            app.refresh_panel()
            pump(lambda: app.user.IsWindowVisible(app.hwnd))
            # Exact HWND capture of this synthetic test window only.
            ImageGrab.grab(window=app.hwnd).save(original_root / 'chat-ui.png')
            report['own_window_screenshot'] = True
            assert not app.pending_write
            report['ok'] = True
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        trace = exc.__traceback__
        while trace and trace.tb_next:
            trace = trace.tb_next
        if trace:
            report['failed_line'] = trace.tb_lineno
    finally:
        if app:
            app.user.DestroyWindow(app.hwnd)
            app.kernel.CloseHandle(app.mutex)
        native.ROOT = original_root
        (original_root / 'chat-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['ok']:
        raise RuntimeError('Chat verification failed: ' + report.get('error', 'unknown'))
    return report
