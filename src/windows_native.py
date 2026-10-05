"""Windows clipboard/tray application; no Qt or third-party runtime imports."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import json
import hashlib
import asyncio
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from api_zoo import APIHTTPError, APIConnectionError, selected_profile
from urllib.parse import urlsplit
from coding_prompt import CODE_PROMPT
from answer_policy import answer_instruction, tray_result, short_tooltip, STYLES, STYLE_LABELS
from prompt_profiles import PRESETS, LABELS as PROMPT_LABELS, selected_prompt, instruction_for, prompt_preferences, merged_preferences, default_prompt
from request_display import RequestDisplay
from secret_policy import SECRET
from chat_modes import CODING, CHAT, ANALYSIS, MODE_ORDER, MODE_LABELS, MENU_MODES, MENU_LABELS, purpose_mode, reasoning_for, supports_reasoning_control, is_chat, request_messages, reply_config, limits
from session_images import SessionImages
from session_state import Session, ANALYZE, GENERATE, format_session_time
from network_switch import NetworkManager, DoublePress
from runtime_settings import (DEEPSEEK_TOKEN_CEILING, DEEPSEEK_DEFAULT_MAX_TOKENS,
                              DEEPSEEK_DEFAULT_TIMEOUT_S, load_runtime_settings,
                              validated_preferences, save_preferences)

ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
from hotkey_settings import (bindings as hotkey_bindings, DEFAULTS as HOTKEY_DEFAULTS,
                             normalized_shortcuts, disabled_actions)
PROMPT = CODE_PROMPT
MAX_STREAM_RECORD_BYTES = 32 * 1024 * 1024
REPAIR = ""


def fingerprint(text):
    return hashlib.sha256(text.replace("\r\n", "\n").strip().encode("utf-8")).digest()


def window_long_setter(user, pointer_size=None):
    # SetWindowLongPtrW is a C macro on x86, not an exported DLL function.
    size = C.sizeof(C.c_void_p) if pointer_size is None else pointer_size
    return user.SetWindowLongPtrW if size == 8 else user.SetWindowLongW


def read_config():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("\"'")
    return values


def log_event(event, **fields):
    # Never log request content, responses, API keys, or upstream error bodies.
    try:
        with (ROOT / "status.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}) + "\n")
    except OSError:
        pass


class AIResponseError(ValueError):
    """Safe, specific API error; never includes the upstream response body."""
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def request_error_message(exc, timeout):
    if isinstance(exc, AIResponseError):
        return str(exc)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        try:
            timeout = float(timeout or 0)
            if not 0 <= timeout <= 2147483647:
                timeout = 0
        except (ValueError, TypeError, OverflowError):
            timeout = 0
        if timeout == 0:
            return "Kết nối/API báo timeout; tool không đặt hạn chờ. Clipboard giữ nguyên; chọn Gửi lại ở tray."
        return f"Hết thời gian chờ {int(timeout)}s; clipboard giữ nguyên. Chọn Gửi lại ở tray để thử lại."
    if isinstance(exc, OSError):
        return "Lỗi kết nối/mạng; chưa nhận đủ câu trả lời. Kiểm tra mạng rồi chọn Gửi lại ở tray."
    if isinstance(exc, ValueError):
        return "Dữ liệu/cấu hình không hợp lệ; xem status.log, không phải lỗi hết token."
    error = str(exc) if isinstance(exc, RuntimeError) else "AI xử lý lỗi; xem status.log"
    return {"AI HTTP 401": "API key không hợp lệ (401)",
            "AI HTTP 402": "Tài khoản API hết số dư/quota (402)",
            "AI HTTP 429": "API đang giới hạn lượt gửi (429); chờ rồi chọn Gửi lại ở tray",
            "AI HTTP 400": "API từ chối yêu cầu (400); kiểm tra model/token hoặc thử F8",
            "AI HTTP 403": "Key không có quyền dùng model (403)",
            "AI HTTP 404": "Endpoint/model chưa được API hỗ trợ (404)",
            "AI HTTP 503": "API đang quá tải (503); chờ rồi chọn Gửi lại ở tray"}.get(error, error)


class AIClient:
    def __init__(self, config):
        self.config = config
        self.cancel_event = None
        self.active_loop = None
        self.active_task = None

    def cancel(self):
        if self.cancel_event:
            self.cancel_event.set()
        loop, task = self.active_loop, self.active_task
        if loop and task:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def post(self, url, body, timeout, key=None):
        deadline = getattr(self, 'deadline', None)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            timeout = min(timeout or remaining, remaining)
        headers = {"Content-Type": "application/json", 'User-Agent': 'ClipboardAI/2.0', 'Accept': 'application/json'}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
        if body.get("stream"):
            return self.stream_post(url, body, timeout, key)
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with build_opener(NoRedirect()).open(req, timeout=None if not timeout else timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except HTTPError as exc:
            error = APIHTTPError(exc.code, exc.headers.get('Retry-After'))
            exc.close()
            raise error from None

    def stream_post(self, url, body, timeout, key=None):
        callback = getattr(self, 'on_stream', None)
        if callback:
            callback(None)  # A fallback starts a fresh preview, never concatenate attempts.
        async def request():
            self.active_loop = asyncio.get_running_loop()
            self.active_task = asyncio.current_task()
            if self.cancel_event.is_set():
                raise InterruptedError()
            address = urlsplit(url)
            port = address.port or (443 if address.scheme == "https" else 80)
            reader, writer = await asyncio.open_connection(address.hostname, port, ssl=True if address.scheme == "https" else None)
            try:
                payload = json.dumps(body).encode("utf-8")
                path = address.path or "/"
                if address.query:
                    path += "?" + address.query
                header = f"POST {path} HTTP/1.1\r\nHost: {address.hostname}:{port}\r\nUser-Agent: ClipboardAI/2.0\r\nAccept: text/event-stream\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
                if key:
                    header = header[:-2] + "Authorization: Bearer " + key + "\r\n\r\n"
                writer.write(header.encode("ascii") + payload)
                await writer.drain()
                status = (await reader.readline()).split()
                if len(status) < 2 or not status[1].isdigit():
                    raise RuntimeError('API trả HTTP không hợp lệ')
                headers = {}
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n"):
                        break
                    if not line:
                        raise APIConnectionError("Incomplete HTTP headers")
                    name, value = line.decode("ascii").split(":", 1)
                    headers[name.lower()] = value.strip().lower()
                if status[1] != b'200':
                    raise APIHTTPError(int(status[1]), headers.get('retry-after'))
                chunked = "chunked" in headers.get("transfer-encoding", "")
                content, pending, finish_reason, usage = [], b"", None, None
                content_bytes = 0
                preview, last_preview = [], time.monotonic()
                def flush_preview(force=False):
                    nonlocal last_preview
                    if callback and preview and (force or time.monotonic() - last_preview >= .08 or sum(map(len, preview)) >= 2048):
                        if not self.cancel_event.is_set():
                            callback(''.join(preview))
                        preview.clear()
                        last_preview = time.monotonic()
                def append_content(value):
                    nonlocal content_bytes
                    if value is None or value == "":
                        return
                    if not isinstance(value, str):
                        raise AIResponseError("Stream trả nội dung sai định dạng; clipboard giữ nguyên.", "stream_format")
                    content_bytes += len(value.encode("utf-8"))
                    if content_bytes > MAX_STREAM_RECORD_BYTES:
                        raise AIResponseError("Phản hồi vượt giới hạn an toàn 32 MiB; clipboard giữ nguyên.", "stream_size")
                    content.append(value)
                    preview.append(value)
                    flush_preview()
                while True:
                    if self.cancel_event.is_set():
                        raise InterruptedError()
                    if chunked:
                        size_line = await reader.readline()
                        if not size_line:
                            raise APIConnectionError("Kết nối ngắt trước khi hoàn tất")
                        size = int(size_line.split(b";", 1)[0].strip(), 16)
                        if size < 0 or size > MAX_STREAM_RECORD_BYTES:
                            raise AIResponseError("Stream vượt giới hạn an toàn 32 MiB; clipboard giữ nguyên.", "stream_size")
                        if size == 0:
                            data = b""
                        else:
                            data = await reader.readexactly(size)
                            if await reader.readexactly(2) != b"\r\n":
                                raise RuntimeError("Invalid HTTP chunk")
                    else:
                        # Body records may exceed StreamReader's 64 KiB line
                        # limit. Read bounded blocks and reassemble ourselves.
                        data = await reader.read(64 * 1024)
                    pending += data
                    if not data and pending:
                        pending += b"\n"
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if len(line) > MAX_STREAM_RECORD_BYTES:
                            raise AIResponseError("Stream vượt giới hạn an toàn 32 MiB; clipboard giữ nguyên.", "stream_size")
                        if not line.strip():
                            continue
                        if key:
                            if not line.startswith(b"data:"):
                                continue
                            line = line[5:].strip()
                            if line == b"[DONE]":
                                if finish_reason is None:
                                    raise APIConnectionError("AI stream ended without finish_reason")
                                result = {"choices": [{"finish_reason": finish_reason, "message": {"content": "".join(content)}}]}
                                if usage is not None:
                                    result["usage"] = usage
                                flush_preview(True)
                                return result
                        try:
                            item = json.loads(line)
                        except (ValueError, TypeError, RecursionError):
                            raise AIResponseError("Stream trả JSON không hợp lệ; clipboard giữ nguyên.", "stream_format") from None
                        if not isinstance(item, dict):
                            raise AIResponseError("Stream trả cấu trúc sai; clipboard giữ nguyên.", "stream_format")
                        if item.get("error"):
                            raise RuntimeError("API báo lỗi xử lý; clipboard giữ nguyên" if key else "Ollama báo lỗi xử lý")
                        if key:
                            if isinstance(item.get("usage"), dict):
                                usage = item["usage"]
                            choices = item.get("choices", [])
                            if not isinstance(choices, list):
                                raise AIResponseError("Stream trả choices sai định dạng; clipboard giữ nguyên.", "stream_format")
                            for choice in choices:
                                if not isinstance(choice, dict) or not isinstance(choice.get("delta") or {}, dict):
                                    raise AIResponseError("Stream trả delta sai định dạng; clipboard giữ nguyên.", "stream_format")
                                append_content((choice.get("delta") or {}).get("content"))
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]
                            continue
                        if not isinstance(item.get("message") or {}, dict):
                            raise AIResponseError("Stream trả message sai định dạng; clipboard giữ nguyên.", "stream_format")
                        append_content((item.get("message") or {}).get("content"))
                        if item.get("done"):
                            item["message"] = {"content": "".join(content)}
                            flush_preview(True)
                            return item
                    if len(pending) > MAX_STREAM_RECORD_BYTES:
                        raise AIResponseError("Stream vượt giới hạn an toàn 32 MiB; clipboard giữ nguyên.", "stream_size")
                    if not data:
                        raise APIConnectionError("Kết nối ngắt trước khi hoàn tất")
            finally:
                writer.close()
                # Python 3.8's Windows Proactor must finish transport teardown
                # before asyncio.run closes the loop, especially for SSL.
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
                except (Exception, asyncio.CancelledError):
                    writer.transport.abort()
                    await asyncio.sleep(0)

        async def run():
            if not timeout:
                return await request()
            return await asyncio.wait_for(request(), timeout=timeout)

        try:
            return asyncio.run(run())
        except asyncio.CancelledError:
            raise InterruptedError("Đã hủy yêu cầu") from None
        except asyncio.IncompleteReadError:
            raise APIConnectionError('Kết nối ngắt trước khi hoàn tất') from None
        finally:
            self.active_loop = None
            self.active_task = None

    def ask(self, text, history=None, instruction=None):
        messages = request_messages(text, history, instruction)
        base = (self.config.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        model = self.config.get("OLLAMA_MODEL") or "auto"
        if model == "auto":
            with urlopen(base + "/api/tags", timeout=5) as r:
                models = json.loads(r.read())["models"]
            if not models:
                raise RuntimeError("No Ollama model installed")
            model = next((m["name"] for m in models if m["name"] == "qwen3:8b"), models[0]["name"])
        thinking = (model.startswith("qwen3:") or model.startswith("qwen3.5:")) and self.config.get('REPLY_MODE') != CHAT
        predict, timeout = limits(self.config, int(self.config.get('OLLAMA_NUM_PREDICT', '8192' if thinking else '2048')),
                                  float(self.config.get('OLLAMA_TIMEOUT_S', '900' if thinking else '300')))
        data = self.post(base + "/api/chat", {"model": model, "stream": self.cancel_event is not None, "think": thinking,
                                              "messages": messages,
                                              "options": {"temperature": 0,
                                                          "num_ctx": int(self.config.get("OLLAMA_NUM_CTX", "8192" if thinking else "4096")),
                                                          "num_predict": predict}}, timeout)
        if data.get("done_reason") == "length":
            raise AIResponseError("Ollama hết token; code dở dang không copy. Tăng token rồi chọn Gửi lại ở tray.", "response_length")
        answer = data["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise AIResponseError("Ollama trả nội dung trống; clipboard giữ nguyên. Chọn Gửi lại ở tray.", "response_empty")
        return answer.strip(), "Ollama"


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", C.c_byte * 8)]


class NID(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                ("uCallbackMessage", W.UINT), ("hIcon", W.HANDLE), ("szTip", W.WCHAR * 128),
                ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                ("uTimeoutOrVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64), ("dwInfoFlags", W.DWORD),
                ("guidItem", GUID), ("hBalloonIcon", W.HANDLE)]


def setup_winapi():
    user = C.WinDLL("user32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    shell = C.WinDLL("shell32", use_last_error=True)
    # Explicit pointer-sized signatures are essential on 64-bit Windows.
    signatures = [
        (kernel, "GetModuleHandleW", [W.LPCWSTR], W.HMODULE),
        (kernel, "GlobalAlloc", [W.UINT, C.c_size_t], W.HGLOBAL),
        (kernel, "GlobalLock", [W.HGLOBAL], C.c_void_p),
        (kernel, "GlobalUnlock", [W.HGLOBAL], W.BOOL),
        (kernel, "GlobalFree", [W.HGLOBAL], W.HGLOBAL),
        (kernel, "CreateMutexW", [C.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
        (kernel, "CloseHandle", [W.HANDLE], W.BOOL),
        (user, "CreateWindowExW", [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p], W.HWND),
        (user, "DefWindowProcW", [W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t),
        (user, "CallWindowProcW", [C.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t),
        (user, "DestroyWindow", [W.HWND], W.BOOL),
        (user, "AddClipboardFormatListener", [W.HWND], W.BOOL),
        (user, "RemoveClipboardFormatListener", [W.HWND], W.BOOL),
        (user, "GetClipboardSequenceNumber", [], W.DWORD),
        (user, "OpenClipboard", [W.HWND], W.BOOL),
        (user, "CloseClipboard", [], W.BOOL),
        (user, "EmptyClipboard", [], W.BOOL),
        (user, "GetClipboardData", [W.UINT], W.HANDLE),
        (user, "SetClipboardData", [W.UINT, W.HANDLE], W.HANDLE),
        (user, "LoadIconW", [W.HINSTANCE, C.c_void_p], W.HANDLE),
        (user, "CreatePopupMenu", [], W.HMENU),
        (user, "AppendMenuW", [W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR], W.BOOL),
        (user, "DestroyMenu", [W.HMENU], W.BOOL),
        (user, "SetForegroundWindow", [W.HWND], W.BOOL),
        (user, "SetFocus", [W.HWND], W.HWND),
        (user, "GetKeyState", [C.c_int], C.c_short),
        (user, "IsWindowVisible", [W.HWND], W.BOOL),
        (user, "GetClientRect", [W.HWND, C.POINTER(W.RECT)], W.BOOL),
        (user, "GetWindowRect", [W.HWND, C.POINTER(W.RECT)], W.BOOL),
        (user, "GetDC", [W.HWND], W.HDC),
        (user, "ReleaseDC", [W.HWND, W.HDC], C.c_int),
        (user, "GetForegroundWindow", [], W.HWND),
        (user, "SystemParametersInfoW", [W.UINT, W.UINT, C.c_void_p, W.UINT], W.BOOL),
        (user, "SetWindowPos", [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT], W.BOOL),
        (user, "SetWindowDisplayAffinity", [W.HWND, W.DWORD], W.BOOL),
        (user, "GetCursorPos", [C.POINTER(W.POINT)], W.BOOL),
        (user, "TrackPopupMenu", [W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p], W.UINT),
        (user, "SetTimer", [W.HWND, C.c_size_t, W.UINT, C.c_void_p], C.c_size_t),
        (user, "KillTimer", [W.HWND, C.c_size_t], W.BOOL),
        (user, "GetMessageW", [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
        (user, "TranslateMessage", [C.POINTER(W.MSG)], W.BOOL),
        (user, "DispatchMessageW", [C.POINTER(W.MSG)], C.c_ssize_t),
        (user, "PostQuitMessage", [C.c_int], None),
        (user, "RegisterWindowMessageW", [W.LPCWSTR], W.UINT),
        (user, "RegisterHotKey", [W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
        (user, "UnregisterHotKey", [W.HWND, C.c_int], W.BOOL),
        (user, "ShowWindow", [W.HWND, C.c_int], W.BOOL),
        (user, "SetWindowTextW", [W.HWND, W.LPCWSTR], W.BOOL),
        (user, "GetWindowTextLengthW", [W.HWND], C.c_int),
        (user, "GetWindowTextW", [W.HWND, W.LPWSTR, C.c_int], C.c_int),
        (user, "SendMessageW", [W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t),
        (user, "EnableWindow", [W.HWND, W.BOOL], W.BOOL),
        (user, "IsDialogMessageW", [W.HWND, C.POINTER(W.MSG)], W.BOOL),
        (user, "MessageBoxW", [W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT], C.c_int),
        (shell, "Shell_NotifyIconW", [W.DWORD, C.POINTER(NID)], W.BOOL),
    ]
    for dll, name, args, result in signatures:
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return user, kernel, shell


class WindowsApp:
    def __init__(self, self_test=False, config_override=None):
        self.user, self.kernel, self.shell = setup_winapi()
        self.self_test = self_test
        self.config = read_config()
        self.config.update(config_override or {})
        self.is_deepseek = self.config.get("BACKEND") == "DeepSeek"
        self.preference_errors = load_runtime_settings(self.config, ROOT / "preferences.json")
        if self.preference_errors:
            log_event("preferences_invalid", fields=self.preference_errors)
        self.client = AIClient(self.config)
        self.session = Session(ROOT / "session.json")
        migration = merged_preferences(self.config, self.session.mode)
        if migration:
            try:
                path = ROOT / 'preferences.json'
                prefs = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))[0] if path.exists() else {}
                if path.exists() and not path.with_name('preferences.before_merged_menu.json').exists():
                    path.with_name('preferences.before_merged_menu.json').write_bytes(path.read_bytes())
                save_preferences(path, dict(prefs, **migration))
            except (OSError, ValueError, TypeError):
                self.preference_errors.append('merged_menu_save')
            self.config.update(migration)
            if self.session.mode == ANALYSIS:
                self.session.copy_modes[str(CHAT)] = self.session.auto_copy
        self.last_completed_answer = self.session.last_answer
        if self.session.needs_chat_start and not self.session.error:
            try:
                backup = self.session.path.with_name('session.before_chat.json')
                if not backup.exists():
                    backup.write_bytes(self.session.path.read_bytes())
                self.session.new_problem(mode=CHAT)
            except OSError:
                self.session.error = 'Chưa sao lưu được phiên cũ; chọn Hỏi đáp để tiếp tục.'
        self.state = "Đang chờ"
        if self.preference_errors:
            self.state = "Đã bỏ qua cấu hình sai; dùng giá trị hợp lệ/mặc định. Lưu lại trong Cấu hình."
        if self.session.error:
            self.state = "Session lỗi; vẫn khởi động, giữ file cũ. Chọn Xóa phiên để tạo phiên mới."
        if self.session.capture_text:
            self.state = "Có bản nháp đề ảnh: " + "; ".join(self.session.capture_missing)[:100] + ". F4 để bổ sung."
        self.current_id = 0
        self.busy = False
        self.started = None
        self.elapsed_done = 0
        self.request_display = RequestDisplay()
        if self.session.last_answer:
            self.request_display.finish('done', 0, self.session.last_answer)
        self.active_status_menu = None
        self.last_menu_status = ''
        self.last_request = ""
        self.cancel_event = threading.Event()
        self.controls = {}
        self.probe_busy = False
        self.zoo_open = False
        self.network = NetworkManager(ROOT)
        self.network_busy = False
        self.network_press = DoublePress()
        self.hotkeys = []
        self.hotkey_errors = []
        self.notice_until = 0
        self.notice_text = None
        self.last_tooltip = ""
        self.hover_window = None
        self.hover_visible = False
        self.hover_text = ""
        self.tray_version4 = False
        self.capture_pending = False
        self.region_pending = False
        self.region_cancel = threading.Event()
        self.region_token = 0
        self.enabled = True
        self.jobs = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.closed = threading.Event()
        self.pending_write = None
        self.pending_image = None
        self.images = SessionImages()
        self.last_image = None
        self.preview_answer = ""
        self.preview_request = ""
        self.settings_visible = False
        self.pending_read = False
        self.own_sequence = None
        self.output_fingerprints = set()
        self.last_input_fingerprint = None
        self.mutex = self.kernel.CreateMutexW(None, False, "Local\\ClipboardAI.Native.v1" + (".test" if self_test else ""))
        if not self.mutex:
            raise C.WinError(C.get_last_error())
        if C.get_last_error() == 183:
            self.kernel.CloseHandle(self.mutex)
            log_event("another_instance_running")
            if not self_test:
                self.user.MessageBoxW(None, "Một bản ClipboardAI đang chạy. Thoát bản cũ bằng icon tray rồi mở bản mới; không chạy hai bản cùng phím tắt.", "ClipboardAI chưa khởi động", 0x40)
            raise SystemExit(0)
        self.callback_type = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        self.callback = self.callback_type(self.window_proc)

        class WC(C.Structure):
            _fields_ = [("style", W.UINT), ("lpfnWndProc", self.callback_type), ("cbClsExtra", C.c_int),
                        ("cbWndExtra", C.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HANDLE),
                        ("hCursor", W.HANDLE), ("hbrBackground", W.HANDLE), ("lpszMenuName", W.LPCWSTR),
                        ("lpszClassName", W.LPCWSTR)]

        instance = self.kernel.GetModuleHandleW(None)
        wc = WC()
        wc.hbrBackground = 16  # COLOR_BTNFACE + 1: native dialog background.
        self.class_name = "ClipboardAI.Native.Window" + (f'.{id(self):x}' if self_test else '')
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self.callback, instance, self.class_name
        self.user.RegisterClassW.argtypes = [C.POINTER(WC)]
        self.user.RegisterClassW.restype = W.ATOM
        if not self.user.RegisterClassW(C.byref(wc)):
            raise C.WinError(C.get_last_error())
        self.instance = instance
        self.hwnd = self.user.CreateWindowExW(0, wc.lpszClassName, "ClipboardAI", 0x00CF0000, 80, 60, 620, 560, None, None, instance, None)
        if not self.hwnd or not self.user.AddClipboardFormatListener(self.hwnd):
            raise C.WinError(C.get_last_error())
        # Treat existing clipboard content as a baseline, not a new user copy.
        initial_clipboard = self.read_clipboard()
        if initial_clipboard is not None:
            self.last_input_fingerprint = fingerprint(initial_clipboard[0])
        self.taskbar_message = self.user.RegisterWindowMessageW("TaskbarCreated")
        self.nid = NID()
        self.nid.cbSize, self.nid.hWnd, self.nid.uID = C.sizeof(NID), self.hwnd, 1
        self.nid.uFlags, self.nid.uCallbackMessage = 7, 0x8001
        self.nid.hIcon = self.user.LoadIconW(None, C.c_void_p(32516))
        self.nid.szTip = "ClipboardAI: F4 ảnh | F8 mới | F9 hỏi tiếp | Shift+F8 copy | F10 hủy"
        if not self.shell.Shell_NotifyIconW(0, C.byref(self.nid)):
            raise RuntimeError("Cannot create system tray icon")
        self.nid.uTimeoutOrVersion = 4
        self.tray_version4 = bool(self.shell.Shell_NotifyIconW(4, C.byref(self.nid)))
        if not self.user.SetTimer(self.hwnd, 1, 150, None):
            raise C.WinError(C.get_last_error())
        threading.Thread(target=self.worker, daemon=True).start()
        self.create_panel()
        self.create_notice()
        self.register_hotkeys()
        if not self.self_test:
            self.tooltip(self.state + "; F4 ảnh, F8 mới, F9 hỏi tiếp, Shift+F8 copy, F10 hủy")
        log_event("ready", backend="Windows native", clipboard_listener=True, auto_send=False)

    def control(self, name, kind, text, x, y, width, height, ident=0, style=0):
        handle = self.user.CreateWindowExW(0x200 if kind == "EDIT" else 0, kind, text,
            0x50010000 | style, x, y, width, height, self.hwnd, ident, self.instance, None)
        if not handle:
            raise C.WinError(C.get_last_error())
        self.controls[name] = handle
        font = C.WinDLL("gdi32").GetStockObject
        font.argtypes, font.restype = [C.c_int], W.HANDLE
        self.user.SendMessageW(handle, 0x30, font(17), 1)
        if kind == "EDIT":
            self.user.SendMessageW(handle, 0xC5, 200000, 0)
        return handle

    def text(self, name):
        handle = self.controls[name]
        value = C.create_unicode_buffer(self.user.GetWindowTextLengthW(handle) + 1)
        self.user.GetWindowTextW(handle, value, len(value))
        return value.value

    def set_text(self, name, value):
        if self.text(name) != str(value):
            self.user.SetWindowTextW(self.controls[name], str(value))

    def create_notice(self):
        # Transparent 48px checkmark only; no status box, focus or mouse capture.
        self.notice = self.user.CreateWindowExW(0x080800A8, "STATIC", "", 0x80000000,
            0, 0, 48, 48, None, None, self.instance, None)
        if not self.notice:
            raise C.WinError(C.get_last_error())
        self.gdi = C.WinDLL("gdi32")
        self.gdi.CreateFontW.argtypes = [C.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]
        self.gdi.CreateFontW.restype = W.HANDLE
        self.check_font = self.gdi.CreateFontW(-34, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI Symbol")
        self.gdi.SelectObject.argtypes, self.gdi.SelectObject.restype = [W.HDC, W.HANDLE], W.HANDLE
        self.gdi.SetTextColor.argtypes = [W.HDC, W.DWORD]
        self.gdi.SetBkMode.argtypes = [W.HDC, C.c_int]
        self.gdi.DeleteObject.argtypes = [W.HANDLE]
        self.user.SetLayeredWindowAttributes.argtypes = [W.HWND, W.DWORD, W.BYTE, W.DWORD]
        self.user.SetLayeredWindowAttributes(self.notice, 0xFFFFFF, 255, 1)
        self.notice_callback = self.callback_type(self.notice_proc)
        set_window_long = window_long_setter(self.user)
        set_window_long.argtypes, set_window_long.restype = [W.HWND, C.c_int, C.c_ssize_t], C.c_ssize_t
        self.user.CallWindowProcW.argtypes, self.user.CallWindowProcW.restype = [C.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM], C.c_ssize_t
        self.old_notice_proc = set_window_long(self.notice, -4, C.cast(self.notice_callback, C.c_void_p).value)
        self.user.SetWindowDisplayAffinity(self.notice, 0x11)

    def notice_proc(self, hwnd, msg, wp, lp):
        if msg == 0x14:  # WM_ERASEBKGND
            return 1
        if msg == 0xF:  # WM_PAINT
            class Paint(C.Structure):
                _fields_ = [("hdc", W.HDC), ("erase", W.BOOL), ("rect", W.RECT),
                            ("restore", W.BOOL), ("update", W.BOOL), ("reserved", W.BYTE * 32)]
            paint = Paint()
            self.user.BeginPaint.argtypes, self.user.BeginPaint.restype = [W.HWND, C.POINTER(Paint)], W.HDC
            self.user.EndPaint.argtypes = [W.HWND, C.POINTER(Paint)]
            self.user.FillRect.argtypes = [W.HDC, C.POINTER(W.RECT), W.HBRUSH]
            self.user.GetSysColorBrush.argtypes, self.user.GetSysColorBrush.restype = [C.c_int], W.HBRUSH
            self.user.DrawTextW.argtypes = [W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT]
            dc = self.user.BeginPaint(hwnd, C.byref(paint))
            rect = W.RECT(0, 0, 48, 48)
            self.gdi.GetStockObject.argtypes, self.gdi.GetStockObject.restype = [C.c_int], W.HANDLE
            self.user.FillRect(dc, C.byref(rect), self.gdi.GetStockObject(0))  # WHITE_BRUSH / transparent key
            old = self.gdi.SelectObject(dc, self.check_font)
            self.gdi.SetTextColor(dc, 0x309030)
            self.gdi.SetBkMode(dc, 1)
            self.user.DrawTextW(dc, "✓", 1, C.byref(rect), 0x25)
            self.gdi.SelectObject(dc, old)
            self.user.EndPaint(hwnd, C.byref(paint))
            return 0
        return self.user.CallWindowProcW(C.c_void_p(self.old_notice_proc), hwnd, msg, wp, lp)

    def show_completion(self):
        # Background workflow: no completion overlay or automatic window.
        self.notice_until = 0
        self.update_notice()

    def update_notice(self):
        if not getattr(self, "notice", None) or self.self_test:
            return
        if self.capture_pending or self.busy:
            self.user.ShowWindow(self.notice, 0)
            return
        self.user.ShowWindow(self.notice, 0)

    def hide_tray_result(self):
        self.hover_visible = False
        if getattr(self, 'hover_window', None):
            self.user.ShowWindow(self.hover_window, 0)

    def show_tray_result(self):
        # NIN_POPUPOPEN/CLOSE: content appears only while the icon is hovered.
        # This window never activates or steals the foreground window.
        if not getattr(self, 'hover_window', None):
            self.hover_window = self.user.CreateWindowExW(0x08000088, 'STATIC', '',
                0x80000080, 0, 0, 168, 24, self.hwnd, None, self.instance, None)
            if not self.hover_window:
                return
            self.hover_font = self.gdi.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, 'Segoe UI')
            if self.hover_font:
                self.user.SendMessageW(self.hover_window, 0x30, self.hover_font, 1)
            self.gdi.GetStockObject.argtypes, self.gdi.GetStockObject.restype = [C.c_int], W.HANDLE
            self.gdi.SetBkColor.argtypes = [W.HDC, W.DWORD]
            self.hover_background = self.gdi.GetStockObject(0)  # WHITE_BRUSH, owned by Windows.
        self.hover_visible = True
        self.hover_text = ''
        self.hover_page_started = time.monotonic()
        self.hover_layout_key = None
        self.refresh_tray_result()

    def refresh_tray_result(self):
        if not getattr(self, 'hover_visible', False) or not getattr(self, 'hover_window', None):
            return
        point, rect = W.POINT(), W.RECT()
        self.user.GetCursorPos(C.byref(point))
        if not self.user.SystemParametersInfoW(0x30, 0, C.byref(rect), 0):
            return
        max_width = min(330, max(24, rect.right - rect.left - 16))
        from hover_layout import MAX_HOVER_HEIGHT, MAX_HOVER_ROWS, page_index
        display = self.display_state()
        if getattr(self, 'region_notice', ''):
            display = RequestDisplay(notice=self.region_notice)
        storage_error = getattr(getattr(self, 'session', None), 'error', '')
        shortcut_error = ', '.join(getattr(self, 'hotkey_errors', []))
        # Cache the measured layout; only its page changes while hovering.
        dc = self.user.GetDC(self.hover_window)
        font = getattr(self, 'hover_font', None)
        old = self.gdi.SelectObject(dc, font) if dc and font else None
        self.gdi.GetTextExtentPoint32W.argtypes = [W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.SIZE)]
        self.gdi.GetTextExtentPoint32W.restype = W.BOOL
        def measure(value):
            size = W.SIZE()
            units = len(value.encode('utf-16-le')) // 2
            if dc and self.gdi.GetTextExtentPoint32W(dc, value, units, C.byref(size)):
                return size.cx
            return len(value) * 8
        try:
            key = (display.phase, display.header(), display.answer, storage_error, shortcut_error, max_width)
            now = time.monotonic()
            if key != getattr(self, 'hover_layout_key', None):
                self.hover_pages = display.hover_pages(measure, max_width)
                if storage_error:
                    for page in self.hover_pages:
                        page.rows[0] = 'Chưa lưu lịch sử · ' + page.rows[0]
                if shortcut_error:
                    for page in self.hover_pages:
                        page.rows[0] = 'Phím chưa bật: ' + shortcut_error + ' · ' + page.rows[0]
                self.hover_layout_key = key
                self.hover_page_started = now
            self.hover_page = page_index(now, getattr(self, 'hover_page_started', now), len(self.hover_pages))
            page = self.hover_pages[self.hover_page]
            rows = page.rows[:MAX_HOVER_ROWS] or ['Đang chờ']
            for i, line in enumerate(rows):
                if measure(line) > max_width - 12:
                    while line and measure(line + '…') > max_width - 12:
                        line = line[:-1]
                    rows[i] = line + '…'
            width = max_width if page.columns > 1 else min(max_width, int(1.5 * max(56, max(measure(line) for line in rows) + 12)))
        finally:
            if old:
                self.gdi.SelectObject(dc, old)
            if dc:
                self.user.ReleaseDC(self.hover_window, dc)
        text = '\r\n'.join(rows)
        if text == getattr(self, 'hover_text', ''):
            return
        self.hover_text = text
        self.user.SetWindowTextW(self.hover_window, text)
        height = min(MAX_HOVER_HEIGHT, max(24, 18 * len(rows) + 8), rect.bottom - rect.top)
        x = max(rect.left, min(point.x + 12, rect.right - width))
        y = max(rect.top, min(point.y - height - 12, rect.bottom - height))
        self.user.SetWindowPos(self.hover_window, W.HWND(-1), x, y, width, height, 0x10 | 0x40)

    def result_text(self):
        if getattr(self, 'region_notice', ''):
            return self.region_notice
        session = getattr(self, 'session', None)
        error = getattr(session, 'error', '')
        value = self.display_state().preview()
        if getattr(self, 'hotkey_errors', []):
            rows = value.splitlines()
            rows.insert(1, 'Phím chưa bật: ' + ', '.join(self.hotkey_errors))
            value = '\n'.join(rows)
        if error:
            rows = value.splitlines()
            rows.insert(1, 'Chưa lưu được lịch sử' if 'lưu' in error.lower() else 'Có lỗi lưu phiên')
            value = '\n'.join(rows[:5])
        return value

    def display_state(self):
        if not hasattr(self, 'request_display'):
            self.request_display = RequestDisplay()
            if getattr(self, 'busy', False):
                self.request_display.begin(getattr(self, 'started', None))
                self.request_display.stage = 'Model đang xử lý'
            elif getattr(getattr(self, 'session', None), 'last_answer', ''):
                self.request_display.finish('done', 0, self.session.last_answer)
        self.request_display.shortcut_names = {name: self.key_label(i) for i, name in HOTKEY_DEFAULTS.items()}
        return self.request_display

    def create_panel(self):
        self.panel_ready = False
        self.control("status", "STATIC", "Đang chờ", 15, 12, 945, 25)
        self.control("details", "STATIC", "", 15, 39, 945, 24)
        self.control("mode_label", "STATIC", "Chế độ:", 15, 75, 65, 25)
        combo = self.control("mode", "COMBOBOX", "", 85, 70, 230, 160, 101, 3 | 0x200000)
        for option in (MENU_LABELS[m] for m in MENU_MODES):
            value = C.create_unicode_buffer(option)
            self.user.SendMessageW(combo, 0x143, 0, C.cast(value, C.c_void_p).value)
        self.user.SendMessageW(combo, 0x14E, MENU_MODES.index(purpose_mode(self.session.mode)), 0)
        check = self.control("auto", "BUTTON", "Tự copy kết quả", 335, 73, 170, 25, 102, 3)
        self.user.SendMessageW(check, 0xF1, int(self.session.auto_copy), 0)
        for name, title, x, width, ident in (("pause", "Tạm dừng phím tắt", 525, 140, 103),
                ("new", "Bài mới", 675, 100, 104), ("clear", "Xóa lịch sử", 785, 130, 105)):
            self.control(name, "BUTTON", title, x, 70, width, 30, ident)
        self.control("problem_label", "STATIC", "Đề / nội dung gửi (copy rồi nhấn F8 để gửi bài mới):", 15, 110, 900, 20)
        self.control("problem", "EDIT", self.session.problem, 15, 135, 945, 145, 110, 0x00201044)
        for name, title, x, ident in (("send", "Gửi đề", 15, 106), ("retry", "Gửi lại", 140, 107),
                ("code", "Sinh code", 265, 108), ("cancel", "Hủy yêu cầu", 390, 109),
                ("copy", "Copy kết quả", 515, 111), ("probe", "Kiểm tra kết nối", 665, 112)):
            self.control(name, "BUTTON", title, x, 290, 140 if name == "probe" else 115, 30, ident)
        self.control("answer_label", "STATIC", "Kết quả mới nhất:", 15, 330, 900, 20)
        self.control("answer", "EDIT", self.session.last_answer, 15, 355, 945, 170, 113, 0x00201844)
        self.control("feedback_label", "STATIC", "Lỗi / test sai / yêu cầu bổ sung cho bài hiện tại:", 15, 535, 850, 20)
        self.control("feedback", "EDIT", "", 15, 560, 795, 70, 114, 0x00201044)
        self.control("repair", "BUTTON", "Sửa code", 825, 560, 135, 35, 115)
        self.control("config_label", "STATIC", "Model                        " + ("Ký tự lịch sử" if self.is_deepseek else "Context") + "       Token sinh         Timeout (0=tắt)", 15, 643, 650, 20)
        for name, value, x, width in (("model", self.model_name(), 15, 230),
                ("ctx", self.config.get("SESSION_MAX_CHARS", "48000") if self.is_deepseek else self.config.get("OLLAMA_NUM_CTX", "4096"), 255, 100),
                ("predict", str(self.token_limit()), 365, 100),
                ("timeout", str(self.request_timeout()), 475, 110)):
            self.control(name, "EDIT", value, x, 668, width, 28, 0, 0x80)
        if self.is_deepseek:
            self.user.EnableWindow(self.controls["model"], False)
        self.control("apply", "BUTTON", "Lưu cấu hình", 600, 668, 135, 30, 116)
        self.control("hotkeys", "BUTTON", "Cài đặt phím tắt", 205, 310, 180, 30, 665)
        self.control("note", "STATIC", "F3×2: Wi-Fi/LAN. F4: chụp đề. F6: chọn phiên. F8: bài mới. F9: phản hồi. F10: hủy AI.", 15, 710, 940, 35)
        self.control("menu", "BUTTON", "Menu", 540, 10, 80, 28, 216)
        self.set_text("problem_label", "Câu hỏi · Enter gửi · Shift+Enter xuống dòng")
        self.set_text("answer_label", "Hội thoại")
        self.set_text("send", "Gửi")
        self.set_text("new", "Phiên mới")
        self.set_text("repair", "Gửi phản hồi")
        self.set_text("note", self.shortcut_help())
        self.set_text("problem", "" if is_chat(self.session.mode) else self.session.problem)
        self.input_callback = self.callback_type(self.input_proc)
        setter = window_long_setter(self.user)
        setter.argtypes, setter.restype = [W.HWND, C.c_int, C.c_ssize_t], C.c_ssize_t
        self.old_input_proc = setter(self.controls["problem"], -4, C.cast(self.input_callback, C.c_void_p).value)
        self.panel_ready = True
        self.layout_panel()
        self.render_conversation()
        self.refresh_panel()

    def layout_panel(self):
        if not getattr(self, "panel_ready", False):
            return
        rect = W.RECT()
        self.user.GetClientRect(self.hwnd, C.byref(rect))
        width, height = max(440, rect.right), max(410, rect.bottom)
        settings = getattr(self, 'settings_visible', False)
        layout = {
            'status': (12, 10, width - 108, 40), 'menu': (width - 88, 10, 76, 28),
            'details': (12, 52, width - 24, 26), 'mode_label': (12, 86, 60, 24),
            'mode': (75, 81, width - 273, 220), 'auto': (width - 188, 84, 176, 25),
        }
        if settings:
            layout.update(config_label=(12, 130, width - 24, 38), model=(12, 175, width - 24, 28),
                          ctx=(12, 218, 110, 28), predict=(138, 218, 110, 28), timeout=(264, 218, 110, 28),
                          apply=(12, 264, 135, 30), probe=(160, 264, 150, 30), pause=(12, 310, 180, 30),
                          hotkeys=(205, 310, 180, 30), clear=(12, 350, 135, 30),
                          note=(12, 390, width - 24, 65))
            self.set_text('config_label', 'Model / Ký tự lịch sử / Token / Timeout (0=tắt)')
            if selected_profile(self.config):
                layout.pop('predict')
                layout.pop('timeout')
                self.set_text('config_label', 'Model / Giới hạn lịch sử (ký tự)')
        else:
            layout.update(answer_label=(12, 120, width - 24, 20), answer=(12, 144, width - 24, height - 335),
                          problem_label=(12, height - 178, width - 24, 20), problem=(12, height - 151, width - 24, 82),
                          send=(12, height - 59, 76, 30), cancel=(98, height - 59, 100, 30),
                          copy=(208, height - 59, 114, 30), new=(332, height - 59, 94, 30),
                          note=(12, height - 24, width - 24, 20))
        for name, handle in self.controls.items():
            self.user.ShowWindow(handle, 5 if name in layout else 0)
            if name in layout:
                self.user.SetWindowPos(handle, None, *layout[name], 0x14)

    def render_conversation(self):
        if not is_chat(self.session.mode):
            self.set_text('answer', getattr(self, 'preview_answer', '') or self.session.last_answer)
            return
        parts = []
        for message in self.session.messages:
            parts.append(('Bạn' if message['role'] == 'user' else 'AI') + ':\r\n' + message['content'])
        if self.busy and getattr(self, 'preview_request', ''):
            parts.append('Bạn:\r\n' + self.preview_request)
            parts.append('AI:\r\n' + (getattr(self, 'preview_answer', '') or 'Đang trả lời…'))
        value = '\r\n\r\n'.join(parts)
        # Only the visible text is bounded; all conversation turns stay on disk.
        if len(value) > 180000:
            value = '[Các tin nhắn cũ vẫn được lưu trong phiên]\r\n' + value[-180000:]
        self.set_text('answer', value)
        self.user.SendMessageW(self.controls['answer'], 0xB1, -1, -1)
        self.user.SendMessageW(self.controls['answer'], 0xB7, 0, 0)

    def input_proc(self, hwnd, msg, wp, lp):
        if msg == 0x87:  # Keep Enter/Escape in this edit, not dialog navigation.
            return 0x84
        if msg == 0x100 and wp == 27:
            self.user.ShowWindow(self.hwnd, 0)
            return 0
        if wp == 13 and msg in (0x100, 0x102) and not self.user.GetKeyState(0x10) & 0x8000:
            if msg == 0x100 and not self.busy:
                self.command(106)
            return 0
        return self.user.CallWindowProcW(C.c_void_p(self.old_input_proc), hwnd, msg, wp, lp)

    def toggle_panel(self):
        if self.user.IsWindowVisible(self.hwnd):
            self.user.ShowWindow(self.hwnd, 0)
        else:
            self.settings_visible = False
            self.layout_panel()
            self.show_panel()

    def change_mode(self, mode):
        if self.busy:
            return
        if mode == ANALYSIS and self.config.get('REPLY_MENU_VERSION') == 1:
            self.set_reasoning('careful')
        mode = purpose_mode(mode)
        if purpose_mode(self.session.mode) == mode:
            self.session.copy_modes[str(mode)] = self.session.auto_copy
        self.session.set_mode(mode)
        self.user.SendMessageW(self.controls['mode'], 0x14E, MENU_MODES.index(mode), 0)
        self.user.SendMessageW(self.controls['auto'], 0xF1, int(self.session.auto_copy), 0)
        self.pending_write = None
        self.render_conversation()
        self.refresh_panel()

    def model_name(self):
        if self.config.get("MODEL_CHOICES"):
            return dict(self.config["MODEL_CHOICES"]).get(self.config.get("SELECTED_MODEL", "deepseek-flash"), "DeepSeek Flash")
        return self.config.get("DEEPSEEK_MODEL", "deepseek-v4-pro") + " (max)" if self.is_deepseek else self.config.get("OLLAMA_MODEL", "auto")

    def is_mirai(self):
        return bool(self.config.get("MODEL_CHOICES")) and self.config.get("SELECTED_MODEL", "deepseek-flash") != "deepseek-flash"

    def select_model(self, ident):
        choices = self.config.get("MODEL_CHOICES", ())
        index = ident - 401
        if not 0 <= index < len(choices):
            return
        model, label = choices[index]
        if model == self.config.get("SELECTED_MODEL", "deepseek-flash"):
            return
        self.cancel_request()
        self.config["SELECTED_MODEL"] = model
        # Selection is intentionally not saved: every launch starts on DeepSeek.
        self.set_text("model", label)
        self.set_text("predict", str(self.token_limit()))
        self.set_text("timeout", str(self.request_timeout()))
        self.state = "Đã chọn " + label + "; giữ nguyên đề và lịch sử phiên"
        self.tooltip(self.state)
        self.refresh_panel()

    def context_capacity(self):
        return int(self.config.get("SESSION_MAX_CHARS", "48000")) // 3 if self.is_deepseek else int(self.config.get("OLLAMA_NUM_CTX", "4096"))

    def token_limit(self):
        profile = selected_profile(self.config)
        if profile:
            return profile['max_tokens']
        if self.is_mirai():
            return int(self.config.get("MIRAI_MAX_TOKENS", "0"))
        return int(self.config.get("DEEPSEEK_MAX_TOKENS", str(DEEPSEEK_DEFAULT_MAX_TOKENS)) if self.is_deepseek else self.config.get("OLLAMA_NUM_PREDICT", "2048"))

    def request_timeout(self):
        profile = selected_profile(self.config)
        if profile:
            return int(profile['timeout'])
        if self.is_mirai():
            return int(self.config.get("MIRAI_TIMEOUT_S", "3000"))
        return int(self.config.get("DEEPSEEK_TIMEOUT_S", str(DEEPSEEK_DEFAULT_TIMEOUT_S)) if self.is_deepseek else self.config.get("OLLAMA_TIMEOUT_S", "300"))

    def token_ceiling(self):
        return DEEPSEEK_TOKEN_CEILING if self.is_deepseek and not self.is_mirai() else 32768

    def refresh_panel(self):
        if not self.controls:
            return
        value = self.display_state().header()
        if getattr(self, 'region_notice', ''):
            value = self.region_notice
        if not self.busy and self.display_state().phase == 'idle':
            value = self.state
        self.set_text("status", value + (' · Phím tắt tạm dừng' if not self.enabled else ''))
        self.set_text("details", f"{getattr(self, 'last_api_used', '') or self.model_name()} · {len(self.session.messages)} tin nhắn")
        self.set_text('note', self.shortcut_help())
        self.set_text("pause", "Tạm dừng phím tắt" if self.enabled else "Bật phím tắt")
        for name in ("send", "retry", "code", "repair", "apply", "mode"):
            self.user.EnableWindow(self.controls[name], not (self.busy or getattr(self, "network_busy", False)))
        self.user.EnableWindow(self.controls["probe"], not (self.probe_busy or getattr(self, "network_busy", False)))
        self.user.EnableWindow(self.controls["cancel"], self.busy)
        if self.session.error:
            self.set_text("note", self.session.error)
            self.displayed_session_error = self.session.error
        elif getattr(self, "displayed_session_error", ""):
            self.set_text("note", "Lịch sử đã lưu lại được; F6 chọn phiên, F9 gửi phản hồi.")
            self.displayed_session_error = ""

    def show_panel(self):
        self.user.ShowWindow(self.hwnd, 9)
        self.user.SetForegroundWindow(self.hwnd)
        self.refresh_panel()
        self.user.SetFocus(self.controls['problem'])

    def register_hotkeys(self):
        self.hotkey_errors = []
        self.hotkeys = []
        for ident, (name, modifiers, key) in hotkey_bindings(self.config.get('HOTKEYS'), self.config.get('HOTKEYS_DISABLED')).items():
            if ident in (204, 214) and self.config.get('DEEPSEEK_MODEL') != 'deepseek-flash':
                continue
            if self.user.RegisterHotKey(self.hwnd, ident, 0x4000 | modifiers, key):
                self.hotkeys.append(ident)
            else:
                self.hotkey_errors.append(name)
        if self.hotkey_errors:
            self.state = 'Phím bị ứng dụng khác chiếm: ' + ', '.join(self.hotkey_errors) + '; dùng menu tray'
            self.tooltip(self.state)

    def shortcut_help(self):
        disabled = self.config.get('HOTKEYS_DISABLED', [])
        return ' · '.join(self.key_label(i) + ' ' + name for i, name in
                          ((204, 'ảnh'), (207, 'phiên'), (201, 'mới'), (202, 'hỏi tiếp'), (203, 'hủy'))
                          if str(i) not in disabled) or 'Phím tắt đã tắt · dùng menu tray'

    def key_label(self, ident):
        if str(ident) in getattr(self, 'config', {}).get('HOTKEYS_DISABLED', []):
            return 'menu tray'
        selected = getattr(self, 'config', {}).get('HOTKEYS', {})
        return selected.get(str(ident), HOTKEY_DEFAULTS[ident]) if isinstance(selected, dict) else HOTKEY_DEFAULTS[ident]

    def save_input_preferences(self, values):
        path = ROOT / 'preferences.json'
        prefs = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))[0] if path.exists() else {}
        save_preferences(path, dict(prefs, **values))
        self.config.update(values)

    def apply_hotkeys(self, selected, disabled=None):
        hotkey_bindings(selected, disabled)
        selected = normalized_shortcuts(selected)
        disabled = disabled_actions(disabled)
        previous = self.config.get('HOTKEYS', {})
        previous_disabled = self.config.get('HOTKEYS_DISABLED', [])
        values = {'HOTKEYS': selected, 'HOTKEYS_DISABLED': disabled}
        editing = getattr(self, 'hotkey_open', False)
        if getattr(self, 'region_pending', False):
            raise ValueError('Hủy chọn vùng trước khi đổi phím.')
        if not self.enabled:
            self.save_input_preferences(values)
            if editing:
                for ident in self.hotkeys:
                    self.user.UnregisterHotKey(self.hwnd, ident)
                self.hotkeys = []
            return
        for ident in self.hotkeys:
            self.user.UnregisterHotKey(self.hwnd, ident)
        self.config['HOTKEYS'] = selected
        self.config['HOTKEYS_DISABLED'] = disabled
        try:
            self.register_hotkeys()
            if self.hotkey_errors:
                raise ValueError('Phím bị ứng dụng khác chiếm: ' + ', '.join(self.hotkey_errors))
            self.save_input_preferences(values)
            if editing:
                for ident in self.hotkeys:
                    self.user.UnregisterHotKey(self.hwnd, ident)
                self.hotkeys = []
        except Exception:
            for ident in self.hotkeys:
                self.user.UnregisterHotKey(self.hwnd, ident)
            self.config['HOTKEYS'] = previous
            self.config['HOTKEYS_DISABLED'] = previous_disabled
            if not editing:
                self.register_hotkeys()
            else:
                self.hotkeys = []
            raise

    def open_hotkey_editor(self):
        if self.busy or getattr(self, 'region_pending', False):
            self.state = 'Chờ yêu cầu hiện tại xong hoặc hủy trước khi đổi phím'
            self.tooltip(self.state)
            return
        if getattr(self, 'hotkey_open', False):
            self.state = 'Cửa sổ đổi phím đã mở; xem trên thanh tác vụ'
            self.tooltip(self.state)
            return
        from hotkey_settings import open_editor
        self.hotkey_open = True
        for ident in self.hotkeys:
            self.user.UnregisterHotKey(self.hwnd, ident)
        self.hotkeys = []
        config = {'HOTKEYS': dict(self.config.get('HOTKEYS', {})),
                  'HOTKEYS_DISABLED': list(self.config.get('HOTKEYS_DISABLED', []))}
        def editor():
            try:
                open_editor(config, self.results)
            except Exception:
                self.results.put(('hotkey_closed',))
                self.results.put(('probe', 'Không mở được cửa sổ đổi phím'))
        threading.Thread(target=editor, daemon=True).start()

    def network_hotkey(self):
        if self.network_press.press(time.monotonic()):
            self.open_network_picker()

    def open_network_picker(self):
        self.start_network('refresh', show_picker=True)

    def start_network(self, target="toggle", adapter_id=None, show_picker=False, profile_name=None, ssid=None):
        if getattr(self, "network_busy", False):
            self.state = "Đang chuyển/đọc mạng; chờ thao tác hiện tại hoàn tất"
        elif target not in ('refresh', 'wifi_scan') and (self.busy or self.probe_busy):
            self.state = "Đang gọi AI/kiểm tra API; chờ xong hoặc F10 hủy AI trước khi chuyển mạng"
        elif target == "toggle" and not self.enabled:
            self.state = "Phím tắt đang tạm dừng"
        else:
            self.network_busy = True
            # Network inventory and switching do not own clipboard output.
            # A completed answer may still be waiting for another app to unlock it.
            message = ("Đang đọc card mạng" if target == "refresh" else
                       "Đang đọc các mạng Wi-Fi" if target == 'wifi_scan' else
                       "Đang chuẩn bị chuyển mạng; xác nhận UAC nếu Windows hỏi")
            if not self.busy:
                self.state = message
            try:
                profile = selected_profile(self.config)
                probe_host = urlsplit(profile['base_url']).hostname if profile else urlsplit(self.config.get("MIRAI_BASE_URL", "https://api.miraiapi.com")).hostname if self.is_mirai() else "api.deepseek.com"
            except (ValueError, AttributeError):
                probe_host = "api.deepseek.com"
            def switch():
                try:
                    options = dict(adapter_id=adapter_id)
                    if target == 'wifi_connect':
                        options.update(profile_name=profile_name, ssid=ssid)
                    result = self.network.perform(target, probe_host or "api.deepseek.com", **options)
                except Exception:
                    result = dict(ok=False, message='Không thực hiện được thao tác mạng; thử làm mới.')
                if target == 'wifi_scan':
                    result.setdefault('adapter_id', adapter_id)
                self.results.put(("network", result, show_picker))
            threading.Thread(target=switch, daemon=True).start()
        self.tooltip(self.state)
        self.refresh_panel()

    def send_clipboard(self):
        if not self.enabled:
            self.state = "Phím tắt đang tạm dừng"
        else:
            value = self.read_clipboard()
            if value is None:
                self.state = "Clipboard đang bận; nhấn F8 lại"
            else:
                text, sequence = value
                if fingerprint(text) in self.output_fingerprints or (self.session.last_answer and fingerprint(text) == fingerprint(self.session.last_answer)):
                    self.state = "Clipboard là câu trả lời AI; copy nội dung mới rồi F9, hoặc chọn Gửi lại"
                else:
                    self.pending_image = None
                    self.set_text("problem", text)
                    self.start_request(text, sequence, fingerprint(text), replace=True)
        self.tooltip(self.state)
        self.refresh_panel()

    def resend_session(self):
        if not self.enabled:
            self.state = "Phím tắt đang tạm dừng"
        elif is_chat(self.session.mode):
            text = self.session.last_request or self.session.problem
            image = getattr(self, 'last_image', None)
            png = image['png'] if image and image['session_id'] == self.session.active_id else None
            if self.session.last_action == 'image' and png is None and not self.images.get(self.session.active_id):
                self.state = 'Ảnh cũ không còn trong bộ nhớ; F4 chụp và gửi lại'
            elif text:
                self.start_request(text, action='retry_chat', image_png=png if self.session.last_action == 'image' else None, replace=True, new_session=False)
            else:
                self.state = 'Mở chat từ menu tray hoặc copy nội dung rồi F8'
        elif self.session.capture_text and not is_chat(self.session.mode):
            self.state = "Đề ảnh còn thiếu: " + "; ".join(self.session.capture_missing)[:130] + ". Cuộn tới phần thiếu rồi F4; chưa gửi bài cũ."
        elif not self.session.messages:
            text = self.session.last_request or self.session.problem
            if text:
                self.start_request(text, replace=True, new_session=False)
            else:
                self.state = "Chưa có phiên; copy đề rồi nhấn F8"
        else:
            # Reuse saved context, not whatever happens to be on the clipboard.
            text = self.session.last_request or next((m["content"] for m in reversed(self.session.messages) if m["role"] == "user"), self.session.problem)
            action = "problem" if self.session.last_action == "problem" and text != self.session.problem else "retry"
            self.start_request(text, action=action, replace=True, new_session=False)
        self.tooltip(self.state)
        self.refresh_panel()

    def reply_clipboard(self):
        if not self.enabled:
            self.state = "Phím tắt đang tạm dừng"
        elif self.session.capture_text and not is_chat(self.session.mode):
            self.state = "Đề ảnh chưa đủ; F4 bổ sung trước khi sửa code"
        elif (not self.session.messages or not self.session.last_answer) and not is_chat(self.session.mode):
            self.state = "Chưa có lời giải trong phiên; gửi đề bằng F4/F8 trước"
        elif self.busy:
            self.state = "Đang xử lý; chờ xong hoặc F10 hủy rồi F9 gửi phản hồi"
        else:
            value = self.read_clipboard()
            if value is None:
                self.state = "Clipboard bận; copy nội dung rồi F9 lại"
            else:
                text, sequence = value
                if not text.strip():
                    self.state = "Copy câu hỏi hoặc nội dung cần gửi rồi F9"
                elif fingerprint(text) in self.output_fingerprints or fingerprint(text) == fingerprint(self.session.last_answer):
                    self.state = "Clipboard vẫn là câu trả lời AI; copy nội dung mới rồi F9"
                else:
                    chat = is_chat(self.session.mode)
                    self.start_request(text if chat else REPAIR + text, sequence, fingerprint(text), action="chat" if chat else "repair", replace=True, new_session=False)
        self.tooltip(self.state)
        self.refresh_panel()

    def select_session(self, ident):
        if self.busy:
            self.state = "Đang xử lý; chờ xong hoặc F10 hủy trước khi chọn phiên"
        elif self.session.select(ident):
            # Invalidate queued results and clipboard writes from the old session.
            self.current_id += 1
            self.pending_write = None
            self.notice_until = 0
            self.last_request = self.session.last_request
            self.elapsed_done = 0
            self.pending_image = None
            self.last_api_used = ''
            self.preview_answer = self.preview_request = ""
            self.set_text("problem", "" if is_chat(self.session.mode) else self.session.capture_text or self.session.problem)
            self.set_text("answer", self.session.last_answer)
            self.set_text("feedback", "")
            self.user.SendMessageW(self.controls["mode"], 0x14E, MENU_MODES.index(purpose_mode(self.session.mode)), 0)
            self.user.SendMessageW(self.controls["auto"], 0xF1, int(self.session.auto_copy), 0)
            self.state = "Đã khôi phục phiên; mở chat từ menu tray, F9 hỏi tiếp"
            self.request_display = RequestDisplay()
            if self.session.last_answer:
                self.request_display.finish('done', 0, self.session.last_answer)
            if getattr(self, 'panel_ready', False):
                self.render_conversation()
        else:
            self.state = self.session.error or "Không tìm thấy phiên đã chọn"
        self.tooltip(self.state)
        self.refresh_panel()

    def session_menu(self):
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        self.menu_open = True
        try:
            entries = self.session.entries()
            commands = {}
            self.user.AppendMenuW(menu, 1, 0, "Hội thoại — chọn phiên để xem thao tác")
            self.user.AppendMenuW(menu, 1 if self.busy else 0, 201, 'Hội thoại mới từ clipboard — ' + self.key_label(201))
            self.user.AppendMenuW(menu, 1 if self.busy else 0, 104, "Tạo phiên trống")
            if not entries:
                self.user.AppendMenuW(menu, 1, 0, "Chưa có hội thoại")
            for index, entry in enumerate(entries):
                submenu = self.user.CreatePopupMenu()
                title = entry['title'].replace('&', '&&').replace('\t', ' ')
                flags = (8 if entry['id'] == self.session.active_id else 0) | (1 if self.busy else 0)
                self.user.AppendMenuW(menu, flags | 0x10, submenu, title + "\t" + format_session_time(entry['updated_at']))
                for offset, (action, label) in enumerate((('select', 'Chọn phiên'), ('chat', 'Mở chat'),
                        ('retry', 'Gửi lại yêu cầu'), ('copy', 'Copy câu trả lời'),
                        ('images', 'Xóa ảnh đã thu thập'), ('save', 'Lưu lại lịch sử'), ('delete', 'Xóa phiên…'))):
                    ident = 1001 + index if offset == 0 else 20000 + index * 10 + offset
                    commands[ident] = (entry['id'], action)
                    self.user.AppendMenuW(submenu, 1 if self.busy else 0, ident, label)
            rect = W.RECT()
            if not self.user.SystemParametersInfoW(0x30, 0, C.byref(rect), 0):
                return
            self.user.SetForegroundWindow(self.hwnd)
            choice = self.user.TrackPopupMenu(menu, 0x128, rect.right - 16, rect.bottom - 16, 0, self.hwnd, None)
            if choice in (201, 104):
                if choice == 201:
                    self.send_clipboard()
                else:
                    self.command(104)
            elif choice in commands and not self.busy:
                ident, action = commands[choice]
                self.select_session(ident)
                if self.session.active_id != ident:
                    return
                if action == 'chat':
                    self.show_panel()
                elif action == 'retry':
                    self.resend_session()
                elif action == 'copy':
                    self.command(111)
                elif action == 'images':
                    self.command(222)
                elif action == 'save':
                    self.command(208)
                elif action == 'delete':
                    self.command(105)
        finally:
            self.menu_open = False
            self.user.DestroyMenu(menu)

    def network_menu(self):
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        try:
            commands = {}
            self.user.AppendMenuW(menu, 1, 0, "Chọn một card · tắt mọi Wi-Fi/LAN còn lại")
            for kind, title in (('wifi', 'Wi-Fi'), ('lan', 'LAN / Ethernet')):
                rows = [row for row in self.network.adapters if row['kind'] == kind]
                if not rows:
                    self.user.AppendMenuW(menu, 1, 0, title + ' · không có card')
                    continue
                target = menu if len(rows) == 1 else self.user.CreatePopupMenu()
                for row in rows:
                    ident = 5001 + len(commands)
                    commands[ident] = (kind, row['id'])
                    label = self.adapter_menu_label(row, title if len(rows) == 1 else '')
                    connected = row['enabled'] and row.get('status') == 'Up'
                    self.user.AppendMenuW(target, 8 if connected else 0, ident, label.replace('&', '&&'))
                    if kind == 'wifi':
                        scan_id = 5001 + len(commands)
                        commands[scan_id] = ('wifi_scan', row['id'])
                        scan_label = 'Chọn mạng Wi-Fi (SSID)' + (' · ' + row['name'] if len(rows) > 1 else '') + '…'
                        self.user.AppendMenuW(target, 0, scan_id, scan_label.replace('&', '&&'))
                if target != menu:
                    self.user.AppendMenuW(menu, 0x10, target, title + f' · {len(rows)} card')
            self.user.AppendMenuW(menu, 0, 5999, 'Thiết lập Wi-Fi trong Windows…')
            self.user.AppendMenuW(menu, 0, 5998, 'Mở card mạng để khôi phục thủ công…')
            self.user.SetForegroundWindow(self.hwnd)
            point = W.POINT()
            self.user.GetCursorPos(C.byref(point))
            choice = self.user.TrackPopupMenu(menu, 0x102, point.x, point.y, 0, self.hwnd, None)
            if choice in commands:
                kind, ident = commands[choice]
                self.start_network(kind, adapter_id=ident, show_picker='wifi' if kind == 'wifi_scan' else False)
            elif choice == 5999:
                self.open_wifi_settings()
            elif choice == 5998:
                self.open_adapter_settings()
        finally:
            self.user.DestroyMenu(menu)

    @staticmethod
    def adapter_menu_label(row, title=''):
        label = (title + ' · ' if title else '') + row['name']
        description = row.get('description', '')
        if description and description != row['name']:
            label += ' · ' + description[:70]
        status = ('Đã tắt' if not row['enabled'] else
                  'Đã kết nối' if row.get('status') == 'Up' else 'Đã bật · Chưa kết nối')
        return label + ' · ' + status

    def open_wifi_settings(self):
        try:
            os.startfile('ms-settings:network-wifi')
        except OSError:
            self.state = 'Không mở được cài đặt Wi-Fi; mở Cài đặt mạng trong Windows'
            self.tooltip(self.state)

    def open_adapter_settings(self):
        """Open Windows recovery UI without changing any adapter automatically."""
        try:
            os.startfile('ncpa.cpl')
        except OSError:
            self.state = 'Không mở được danh sách card; mở Cài đặt mạng trong Windows'
            self.tooltip(self.state)

    def wifi_menu(self, result):
        """User-requested SSID picker; Windows retains all credential handling."""
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        try:
            commands = {}
            self.user.AppendMenuW(menu, 1, 0, result.get('message', 'Chọn mạng Wi-Fi').replace('&', '&&'))
            networks = result.get('networks', [])
            if result.get('ok') and not networks:
                self.user.AppendMenuW(menu, 1, 0, 'Không tìm thấy mạng Wi-Fi; thử làm mới hoặc thiết lập trong Windows')
            for row in networks:
                ident = 6001 + len(commands)
                commands[ident] = row
                label = row.get('ssid') or 'Mạng ẩn'
                label += ' · ' + str(row.get('signal', 0)) + '%'
                label += ' · ' + ('Đã lưu' if row.get('profile') else 'Chưa lưu · thiết lập trong Windows')
                if row.get('connected'):
                    label += ' · Đang kết nối'
                connectable = bool(row.get('connectable') and row.get('profile'))
                # Unknown profiles open Windows setup only after this explicit click.
                flags = 8 if row.get('connected') else 0
                if not connectable and row.get('profile'):
                    flags |= 1
                self.user.AppendMenuW(menu, flags, ident, label.replace('&', '&&'))
            self.user.AppendMenuW(menu, 0, 6998, 'Làm mới danh sách Wi-Fi')
            self.user.AppendMenuW(menu, 0, 6999, 'Thiết lập Wi-Fi trong Windows…')
            self.user.SetForegroundWindow(self.hwnd)
            point = W.POINT()
            self.user.GetCursorPos(C.byref(point))
            choice = self.user.TrackPopupMenu(menu, 0x102, point.x, point.y, 0, self.hwnd, None)
            if choice in commands:
                row = commands[choice]
                if row.get('connectable') and row.get('profile'):
                    self.start_network('wifi_connect', adapter_id=result.get('adapter_id'),
                                       profile_name=row['profile'], ssid=row.get('ssid'))
                else:
                    self.open_wifi_settings()
            elif choice == 6998:
                self.start_network('wifi_scan', adapter_id=result.get('adapter_id'), show_picker='wifi')
            elif choice == 6999:
                self.open_wifi_settings()
        finally:
            self.user.DestroyMenu(menu)

    def cancel_request(self):
        if getattr(self, 'region_pending', False):
            self.region_cancel.set()
            self.state = 'Đã hủy chọn vùng; clipboard và phiên giữ nguyên'
            self.tooltip(self.state)
            return
        model_elapsed = self.display_state().seconds()
        self.current_id += 1
        self.cancel_event.set()
        self.client.cancel()
        self.pending_write = None
        self.notice_until = 0
        while not self.jobs.empty():
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        self.busy = False
        self.capture_pending = False
        self.preview_answer = self.preview_request = ""
        self.elapsed_done = int(time.monotonic() - self.started) if self.started else 0
        self.state = "Đã hủy; sẵn sàng nhận F4/F8/F9"
        self.display_state().finish('cancelled', model_elapsed)
        self.tooltip(self.state)
        self.refresh_panel()
        if getattr(self, 'panel_ready', False):
            self.render_conversation()

    def send_screenshot(self, hwnd=None, append=False):
        attempt_id = self.current_id
        if not self.enabled:
            self.state = "Phím tắt đang tạm dừng; bật lại trong menu tray"
        elif self.config.get("DEEPSEEK_MODEL") != "deepseek-flash":
            self.state = "Chụp ảnh chỉ có trong bản DeepSeek Flash"
        elif getattr(self, 'network_busy', False):
            self.state = 'Đang chuyển/đọc mạng; chờ xong rồi chụp'
        elif self.busy or getattr(self, 'region_pending', False):
            self.state = ('Đang xử lý; chờ xong hoặc F10 hủy rồi Shift+F9 bổ sung ảnh' if append else
                          'Đang xử lý; chờ xong hoặc F10 hủy rồi F4 chụp câu hỏi mới')
            self.display_state().notice = 'F4 chưa gửi · F10 hủy hoặc chờ xong'
        elif append and not (self.session.messages or self.session.last_request):
            self.state = 'Chưa có câu hỏi; F4/F8 gửi trước rồi Shift+F9 bổ sung ảnh'
        else:
            try:
                hwnd = hwnd or self.user.GetForegroundWindow()
                if not hwnd:
                    raise RuntimeError("Không có cửa sổ đang xem")
                value = self.read_clipboard() if not append and self.config.get('F4_INPUT', 'image_clipboard') == 'image_clipboard' else None
                clipboard = value[0].strip() if value else ""
                text = clipboard or (
                    "Trả lời hoặc giải các câu hỏi/bài tập nhìn thấy trong ảnh theo kiểu đáp án đã chọn; "
                    "không chỉ mô tả ảnh hoặc chép lại đề. Với trắc nghiệm, chọn theo nội dung câu hỏi, "
                    "không dựa vào ô đang tích hay trạng thái Chưa trả lời. Câu thiếu dữ kiện hoặc không "
                    "đọc rõ: giữ số câu và ghi Chưa xác định. Nếu không thấy câu hỏi/yêu cầu rõ ràng: "
                    "ghi Chưa xác định: không thấy câu hỏi trong ảnh. Không đoán hay tự bịa đáp án.")
                if append:
                    text = 'Ảnh mới bổ sung cho yêu cầu trước trong phiên này. Dùng ảnh mới cùng dữ kiện và ảnh trước để cập nhật đáp án theo yêu cầu đã có; không coi đây là câu hỏi độc lập.'
                    if not self.session.messages:
                        text += '\nYêu cầu trước:\n' + self.session.last_request
                if self.config.get('F4_CAPTURE', 'window') == 'region':
                    self.start_region_capture(hwnd, text, value, append)
                else:
                    self.start_request(text, sequence=value[1] if value else None,
                        digest=fingerprint(value[0]) if value else None, screenshot_hwnd=hwnd,
                        replace=True, action="image", new_session=not append)
            except Exception:
                self.state = 'Không chụp được cửa sổ; mở đề và nhấn ' + ('Shift+F9' if append else 'F4') + ' lại'
        if not self.busy and not getattr(self, 'region_pending', False) and self.current_id == attempt_id:
            display = self.display_state()
            display.finish('failed', 0, error=self.state)
            display.image = True
        self.tooltip(self.state)
        self.refresh_panel()

    def start_region_capture(self, hwnd, text, value, append):
        from region_capture import select_region
        if getattr(self, 'network_busy', False):
            raise RuntimeError('Đang chuyển mạng')
        self.hide_tray_result()
        self.region_token = getattr(self, 'region_token', 0) + 1
        token = self.region_token
        cancel = self.region_cancel = threading.Event()
        session_id = self.session.active_id
        baseline = value if value else self.read_clipboard()
        sequence = baseline[1] if baseline else self.user.GetClipboardSequenceNumber()
        digest = fingerprint(baseline[0]) if baseline else None
        cue = self.config.get('REGION_CUE', 'light')
        import copy
        self.region_config_snapshot = copy.deepcopy(self.config)
        self.region_mode_snapshot = self.session.mode
        self.region_pending = True
        self.region_clipboard_hold = True
        self.state = 'Kéo chọn vùng rồi thả chuột · Esc / ' + self.key_label(203) + ' hủy'
        self.display_state().notice = self.state
        self.region_notice = self.state
        def capture():
            png, error = None, ''
            try:
                png = select_region(hwnd, cancel, cue)
            except Exception as exc:
                log_event('region_capture_failed', error_type=type(exc).__name__)
                from screen_capture import CaptureError
                error = str(exc) if isinstance(exc, CaptureError) else 'Không chọn được vùng; mở đề rồi chụp lại'
            self.results.put(('region_done', token, cancel, png, error, text, sequence, digest, append, session_id))
        try:
            threading.Thread(target=capture, daemon=True).start()
        except Exception:
            self.region_pending = False
            cancel.set()
            raise

    def finish_region_capture(self, result):
        _, token, cancel, png, error, text, sequence, digest, append, session_id = result
        if (not getattr(self, 'region_pending', False) or token != getattr(self, 'region_token', 0)
                or getattr(self, 'closed', threading.Event()).is_set()):
            return
        self.region_pending = False
        config_changed = (getattr(self, 'region_config_snapshot', self.config) != self.config
                          or getattr(self, 'region_mode_snapshot', self.session.mode) != self.session.mode)
        if cancel.is_set() or png is None and not error:
            self.state = 'Đã hủy chọn vùng; clipboard và phiên giữ nguyên'
            self.display_state().notice = self.state
        elif error:
            self.state = error
            self.display_state().notice = error
        elif append and self.session.active_id != session_id:
            self.state = 'Phiên đã đổi; chưa gửi ảnh. Chọn lại phiên rồi chụp.'
            self.display_state().notice = self.state
        elif config_changed:
            self.state = 'Cấu hình AI đã đổi khi chọn vùng; chưa gửi ảnh. Chụp lại để dùng cấu hình mới.'
            self.display_state().notice = self.state
        else:
            self.start_request(text, sequence=sequence, digest=digest, image_png=png,
                               replace=True, action='image', new_session=not append)
        if cancel.is_set() or error or png is None or config_changed or append and self.session.active_id != session_id:
            self.region_notice = self.state
        self.tooltip(self.state)
        self.refresh_panel()

    def start_request(self, text, sequence=None, digest=None, action="problem", image_png=None, screenshot_hwnd=None, replace=False, capture_source="", capture_previous="", new_session=True):
        if getattr(self, 'region_pending', False):
            self.state = 'Đang chọn vùng; thả chuột để gửi hoặc Esc để hủy'
            self.tooltip(self.state)
            return
        if getattr(self, "network_busy", False):
            self.state = "Đang chuyển/đọc mạng; chờ xong rồi gửi AI"
            self.tooltip(self.state)
            self.refresh_panel()
            return
        if self.busy and not replace:
            self.state = "Đang xử lý; nội dung mới giữ trong ô đề. Hủy hoặc chờ rồi bấm Gửi đề."
            self.refresh_panel()
            return
        if not text.strip() or SECRET.search(text) or SECRET.search(capture_previous):
            self.state = "Nội dung trống hoặc có khóa/mật khẩu; chưa gửi"
            self.refresh_panel()
            return
        signature = (action, text, screenshot_hwnd)
        if self.busy and signature == getattr(self, 'request_signature', None):
            # Key autorepeat or repeated presses must not restart/bill twice.
            return
        fresh = new_session and action in ('problem', 'image')
        try:
            history = self.session.context(self.context_capacity()) if not fresh and action not in ("problem", "capture") else []
            context = self.context_capacity()
            if is_chat(self.session.mode):
                if len(text) > context:
                    raise ValueError("Câu hỏi quá dài; tăng giới hạn lịch sử trong Cài đặt hoặc chia nội dung.")
            elif sum(len(m["content"]) for m in history) + len(text) + len(capture_previous) + len(PROMPT) > context * 3:
                raise ValueError("Đề/lịch sử quá dài; tăng context hoặc tạo bài mới. Không tự cắt đề.")
        except ValueError as exc:
            self.state = str(exc)
            self.refresh_panel()
            return
        memory_messages = list(self.session.messages) if not fresh and action not in ('problem', 'capture') else []
        replace_last = (action == 'retry_chat' and len(memory_messages) >= 2 and
            memory_messages[-2]['role'] == 'user' and memory_messages[-1]['role'] == 'assistant' and
            memory_messages[-2]['content'] in (text, text + '\n[Ảnh đính kèm]'))
        if replace_last:
            memory_messages = memory_messages[:-2]
        if replace:
            self.cancel_request()
        if sequence is None:
            value = self.read_clipboard()
            if value:
                sequence, digest = value[1], fingerprint(value[0])
        if fresh:
            if not self.session.new_problem(text):
                self.state = self.session.error
                self.tooltip(self.state)
                self.refresh_panel()
                return
            self.set_text("answer", "")
            self.set_text("feedback", "")
        self.current_id += 1
        self.request_signature = signature
        self.hide_tray_result()
        self.hover_text = ''
        self.cancel_event = threading.Event()
        self.busy = True
        self.started = time.monotonic()
        self.display_state().begin(image=image_png is not None or screenshot_hwnd is not None)
        self.elapsed_done = 0
        self.last_request = text
        if action != "capture":
            if action == "problem":
                self.session.clear_capture(save=False)
            self.session.last_request = text
            if action != 'retry_chat':
                self.session.last_action = action
            self.session.save()
        self.last_action = action
        self.pending_write = None
        self.notice_until = 0
        self.preview_answer = ""
        self.region_clipboard_hold = False
        self.region_notice = ''
        self.preview_request = text if action != "capture" else ""
        self.last_api_used = ''
        self.state = ("Đang phân tích" if self.session.mode == ANALYSIS else "Đang trả lời") if is_chat(self.session.mode) else "Đang phân tích" if (self.session.mode == 2 and action != "code") or (self.session.mode == 1 and action == "problem") else "Đang sinh code"
        self.capture_pending = screenshot_hwnd is not None
        if self.capture_pending and getattr(self, "notice", None):
            self.user.ShowWindow(self.notice, 0)
        if image_png is not None or screenshot_hwnd is not None:
            self.state = "Đang chụp / đọc ảnh"
        self.jobs.put_nowait(dict(id=self.current_id, text=text, history=history, mode=self.session.mode,
            session_id=self.session.active_id, image_generation=self.images.generation(self.session.active_id),
            config=reply_config(self.config, self.session.mode), context_capacity=context,
            memory_messages=memory_messages, replace_last=replace_last,
            summary=self.session.summary if not fresh and action != 'problem' else '', summary_count=min(self.session.summary_count, len(memory_messages)) if not fresh and action != 'problem' else 0,
            action=action, sequence=sequence, digest=digest, cancel=self.cancel_event, image_png=image_png, screenshot_hwnd=screenshot_hwnd,
            capture_source=capture_source, capture_previous=capture_previous))
        self.tooltip(self.state)
        self.refresh_panel()
        if getattr(self, 'panel_ready', False):
            self.render_conversation()

    def command(self, ident):
        if getattr(self, 'region_pending', False):
            if ident in (109, 203):
                self.cancel_request()
            return
        if ident == 665:
            self.open_hotkey_editor()
            return
        if ident in (680, 681, 682, 683):
            name, value = {680: ('F4_CAPTURE', 'region'), 681: ('F4_CAPTURE', 'window'),
                           682: ('REGION_CUE', 'light'), 683: ('REGION_CUE', 'clear')}[ident]
            try:
                self.save_input_preferences({name: value})
                self.state = 'Đã lưu cách chụp / hiển thị vùng chọn'
            except (OSError, ValueError):
                self.state = 'Chưa lưu được lựa chọn; giữ cấu hình trước'
            self.tooltip(self.state)
            return
        if ident in (660, 661, 662, 663):
            if self.busy:
                return
            try:
                if ident in (660, 661):
                    self.set_reasoning('fast' if ident == 660 else 'careful')
                else:
                    self.set_mode_prompt(self.session.mode, default_prompt(self.session.mode) if ident == 662 else 'free')
            except (OSError, ValueError, TypeError):
                self.state = 'Không lưu được lựa chọn; giữ cấu hình đang dùng'
                self.tooltip(self.state)
            return
        if ident == 664:
            self.open_prompt_editor(purpose_mode(self.session.mode))
            return
        if ident in getattr(self, 'saved_prompt_commands', {}):
            if not self.busy:
                item = self.saved_prompt_commands[ident]
                try:
                    self.set_mode_prompt(self.session.mode, item['style'], item['text'])
                except (OSError, ValueError, TypeError):
                    self.state = 'Không chọn được prompt đã lưu; giữ bản đang dùng'
                    self.tooltip(self.state)
            return
        if 600 <= ident < 630:
            index, preset = divmod(ident - 600, 10)
            if index < len(MODE_ORDER) and preset < len(PRESETS) - 1 and not self.busy:
                mode = MODE_ORDER[index]
                try:
                    self.set_mode_prompt(mode, PRESETS[preset])
                    self.change_mode(mode)
                except (OSError, ValueError, TypeError):
                    self.state = 'Không lưu được prompt; lựa chọn cũ vẫn giữ.'
                    self.tooltip(self.state)
            return
        if 650 <= ident < 653:
            self.open_prompt_editor(MODE_ORDER[ident - 650])
            return
        if ident in (241, 242, 243):
            style = STYLES[ident - 241]
            try:
                path = ROOT / 'preferences.json'
                prefs = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))[0] if path.exists() else {}
                prefs['ANSWER_STYLE'] = style
                save_preferences(path, prefs)
                self.config['ANSWER_STYLE'] = style
                self.state = 'Đáp án: ' + STYLE_LABELS[style]
            except (OSError, ValueError, TypeError, RecursionError):
                self.state = 'Chưa lưu được kiểu đáp án; giữ lựa chọn trước'
            self.tooltip(self.state)
            return
        if ident in (231, 232):
            mode = 'image' if ident == 231 else 'image_clipboard'
            try:
                path = ROOT / 'preferences.json'
                prefs = {}
                if path.exists():
                    prefs, _ = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))
                prefs['F4_INPUT'] = mode
                save_preferences(path, prefs)
                self.config['F4_INPUT'] = mode
                self.state = 'F4: ' + ('Chỉ ảnh · không gửi clipboard' if mode == 'image' else 'Ảnh + nội dung clipboard')
            except (OSError, ValueError, TypeError, RecursionError):
                self.state = 'Chưa lưu được lựa chọn F4; giữ cách chụp trước đó'
            self.tooltip(self.state)
            self.refresh_panel()
            return
        if ident == 222:
            self.images.clear(self.session.active_id)
            self.last_image = None
            self.pending_image = None
            self.set_text('problem_label', 'Câu hỏi · Enter gửi · Shift+Enter xuống dòng')
            self.state = 'Đã bỏ ảnh đính kèm'
            self.refresh_panel()
            return
        if ident == 216:
            self.menu()
            return
        if ident == 221:
            self.settings_visible = not self.settings_visible
            self.layout_panel()
            self.show_panel()
            return
        if ident in getattr(self, 'zoo_model_commands', {}):
            import copy
            from api_zoo import ZooStore, apply_config, update_catalog
            profile_id, model_id = self.zoo_model_commands[ident]
            data = copy.deepcopy(self.config['API_ZOO'])
            profile = next((p for p in data['profiles'] if p['id'] == profile_id), None)
            if not profile or model_id not in {m['id'] for m in profile['models']}:
                return
            profile['model'] = model_id
            data['primary'] = profile_id
            try:
                data = ZooStore(ROOT).save(data)
            except (OSError, ValueError):
                self.state = 'Không lưu được lựa chọn model'
                self.tooltip(self.state)
                return
            self.cancel_request()
            apply_config(self.config, data, select_primary=True)
            self.set_text('model', self.model_name())
            self.set_text('predict', str(self.token_limit()))
            self.set_text('timeout', str(self.request_timeout()))
            self.state = 'Đã chọn ' + self.model_name() + '; giữ nguyên phiên'
            self.tooltip(self.state)
            self.refresh_panel()
            return
        if ident == 220:
            if getattr(self, 'zoo_open', False):
                self.activate_editor(getattr(self, 'zoo_hwnd', None))
            else:
                import copy
                from api_zoo_ui import open_editor
                self.zoo_open = True
                config = copy.deepcopy(self.config)
                def editor():
                    try:
                        open_editor(ROOT, config, self.results)
                    except Exception:
                        self.results.put(('zoo_closed',))
                        self.results.put(('probe', 'Không mở được cửa sổ API Zoo'))
                threading.Thread(target=editor, daemon=True).start()
            return
        if ident == 209:
            self.open_network_picker()
            return
        elif ident == 210:
            self.start_network("refresh")
            return
        if 401 <= ident < 401 + len(self.config.get("MODEL_CHOICES", ())):
            self.select_model(ident)
            return
        elif ident == 208:
            self.state = ("Đã lưu lại lịch sử; kết quả và các phiên đã giữ trên ổ đĩa."
                          if self.session.save() else self.session.error)
            self.tooltip(self.state)
        elif ident == 205:
            self.cancel_request()
            self.session.clear_capture()
            self.state = "Đã xóa bản nháp ảnh; mở bài mới rồi F4"
            self.tooltip(self.state)
        elif ident == 206:
            value = self.read_clipboard()
            if value and self.session.capture_text:
                self.pending_write = (self.session.capture_text, value[1], fingerprint(value[0]))
        elif ident == 101:
            index = self.user.SendMessageW(self.controls["mode"], 0x147, 0, 0)
            if 0 <= index < len(MENU_MODES):
                self.change_mode(MENU_MODES[index])
        elif ident == 102:
            self.session.auto_copy = bool(self.user.SendMessageW(self.controls["auto"], 0xF0, 0, 0))
            self.session.copy_modes[str(purpose_mode(self.session.mode))] = self.session.auto_copy
            if purpose_mode(self.session.mode) == CHAT:
                self.session.copy_modes[str(ANALYSIS)] = self.session.auto_copy
            if not self.session.auto_copy:
                self.pending_write = None
            self.session.save()
        elif ident == 103:
            self.enabled = not self.enabled
            self.pending_write = None
            if self.enabled:
                if not getattr(self, 'hotkey_open', False):
                    self.register_hotkeys()
            else:
                for key_id in getattr(self, 'hotkeys', []):
                    self.user.UnregisterHotKey(self.hwnd, key_id)
                self.hotkeys = []
                self.hotkey_errors = []
        elif ident == 233:
            self.open_adapter_settings()
        elif ident in (104, 105):
            if ident == 105 and self.user.MessageBoxW(self.hwnd, "Xóa đề và lịch sử bài đang lưu?", "Xóa lịch sử", 0x24) != 6:
                return
            self.cancel_request()
            if ident == 104:
                if not self.session.new_problem():
                    self.state = self.session.error
                    self.tooltip(self.state)
                    self.refresh_panel()
                    return
            else:
                self.images.clear(self.session.active_id)
                self.session.reset()
            self.last_request = ""
            self.pending_image = None
            self.preview_answer = self.preview_request = ""
            self.last_input_fingerprint = None
            for name in ("problem", "answer", "feedback"):
                self.set_text(name, "")
        elif ident == 107:
            self.resend_session()
            return
        elif ident == 106:
            text = self.text("problem")
            value = self.read_clipboard()
            sequence = value[1] if value else None
            digest = fingerprint(value[0]) if value else None
            chat = is_chat(self.session.mode)
            image = getattr(self, 'pending_image', None)
            png = image['png'] if image and image['session_id'] == self.session.active_id else None
            if png and not text.strip():
                self.state = 'Nhập câu hỏi về ảnh rồi nhấn Enter'
            else:
                before = self.current_id
                self.start_request(text, sequence, digest, action="image" if png else "chat" if chat else "problem", image_png=png, new_session=not chat)
                if self.current_id != before and png:
                    self.last_image = image
                    self.pending_image = None
                if self.busy and chat:
                    self.set_text('problem', '')
        elif ident in (108, 115):
            if not self.session.messages:
                self.state = "Hãy gửi đề trước khi sinh/sửa code"
            else:
                text = GENERATE if ident == 108 else self.text("feedback").strip()
                if ident == 115:
                    text = text if is_chat(self.session.mode) else REPAIR + text if text else ""
                self.start_request(text, action="code" if ident == 108 else "chat" if is_chat(self.session.mode) else "repair", new_session=False)
        elif ident == 109:
            self.cancel_request()
        elif ident in (111, 215):
            self.copy_last_answer(current_session=ident == 111)
        elif ident == 112:
            if getattr(self, "network_busy", False):
                self.state = "Đang chuyển/đọc mạng; chờ xong rồi kiểm tra API"
                self.tooltip(self.state)
                self.refresh_panel()
                return
            if not self.probe_busy:
                self.probe_busy = True
                provider = "Mirai" if self.is_mirai() else "DeepSeek" if self.is_deepseek else "Ollama"
                probe_config = dict(self.config)
                self.state = "Đang kiểm tra kết nối " + provider
                def probe():
                    try:
                        profile = selected_profile(probe_config)
                        if profile:
                            from api_zoo import probe_profile
                            found, count = probe_profile(profile)
                            self.results.put(('probe', profile['name'] + ': kết nối OK; ' + str(count) + ' model; ' + ('có model đã chọn' if found else 'model chưa được liệt kê')))
                            return
                        if provider == "Mirai":
                            request = Request(probe_config.get("MIRAI_BASE_URL", "https://api.miraiapi.com") + "/v1/models", headers={"Authorization": "Bearer " + probe_config.get("MIRAI_API_KEY", "")})
                        elif self.is_deepseek:
                            request = Request("https://api.deepseek.com/models", headers={"Authorization": "Bearer " + probe_config.get("DEEPSEEK_API_KEY", "")})
                        else:
                            base = self.config.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
                            request = base + "/api/tags"
                        with urlopen(request, timeout=5) as response:
                            data = json.loads(response.read())
                        names = [m["id"] for m in data.get("data", [])] if self.is_deepseek else [m["name"] for m in data.get("models", [])]
                        self.results.put(("probe", provider + " kết nối OK: " + ", ".join(names)))
                    except Exception:
                        self.results.put(("probe", "Không kết nối được " + provider))
                threading.Thread(target=probe, daemon=True).start()
        elif ident == 116:
            if selected_profile(self.config):
                try:
                    ctx = int(self.text('ctx'))
                    if not 1024 <= ctx <= 196608:
                        raise ValueError()
                    prefs = {}
                    if (ROOT / 'preferences.json').exists():
                        prefs, _ = validated_preferences(json.loads((ROOT / 'preferences.json').read_text(encoding='utf-8')))
                    prefs['SESSION_MAX_CHARS'] = str(ctx)
                    save_preferences(ROOT / 'preferences.json', prefs)
                    self.config.update(prefs)
                    self.state = 'Đã lưu giới hạn lịch sử: ' + str(ctx) + ' ký tự'
                except (OSError, ValueError, TypeError, RecursionError):
                    self.state = 'Không lưu được giới hạn lịch sử; nhập 1024–196608 ký tự'
                self.tooltip(self.state)
                self.refresh_panel()
                return
            try:
                model = self.text("model").strip()
                ctx, predict, timeout = (int(self.text(n)) for n in ("ctx", "predict", "timeout"))
                if not model or not 1024 <= ctx <= (196608 if self.is_deepseek else 65536) or not (128 <= predict <= self.token_ceiling() or predict == 0 and self.is_deepseek) or not (timeout == 0 or timeout >= 30):
                    raise ValueError()
                prefs = dict(SESSION_MAX_CHARS=str(ctx), MIRAI_MAX_TOKENS=str(predict), MIRAI_TIMEOUT_S=str(timeout)) if self.is_mirai() else dict(SESSION_MAX_CHARS=str(ctx), DEEPSEEK_MAX_TOKENS=str(predict), DEEPSEEK_TIMEOUT_S=str(timeout)) if self.is_deepseek else dict(OLLAMA_MODEL=model, OLLAMA_NUM_CTX=str(ctx), OLLAMA_NUM_PREDICT=str(predict), OLLAMA_TIMEOUT_S=str(timeout))
                try:
                    previous_prefs = json.loads((ROOT / "preferences.json").read_text(encoding="utf-8"))
                    previous_valid, _ = validated_preferences(previous_prefs)
                    prefs = {**previous_valid, **prefs}
                except (OSError, ValueError, TypeError, RecursionError):
                    pass
                save_preferences(ROOT / "preferences.json", prefs)
                self.config.update(prefs)
                self.preference_errors = []
                self.state = "Đã lưu cấu hình Mirai" if self.is_mirai() else "Đã lưu cấu hình DeepSeek" if self.is_deepseek else "Đã lưu cấu hình (model phải được cài sẵn trong Ollama)"
            except (ValueError, OSError):
                self.state = f"Cấu hình không hợp lệ/không ghi được: token 128–{self.token_ceiling()} (cloud: 0=API); timeout 0=tắt hoặc >=30s."
        self.refresh_panel()

    def activate_editor(self, hwnd):
        if hwnd:
            self.user.GetAncestor.argtypes, self.user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
            hwnd = self.user.GetAncestor(hwnd, 2) or hwnd
            self.user.ShowWindow(hwnd, 9)
            self.user.SetForegroundWindow(hwnd)

    def set_mode_prompt(self, mode, style, text=''):
        values = prompt_preferences(mode, style, text, self.config)
        path = ROOT / 'preferences.json'
        prefs = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))[0] if path.exists() else {}
        prefs.update(values)
        if self.config.get('REPLY_MENU_VERSION') == 1:
            prefs.update(REPLY_MENU_VERSION=1, REASONING_MODES=self.config.get('REASONING_MODES', {}))
        save_preferences(path, prefs)
        self.config.update(values)
        self.state = 'Đã lưu prompt · ' + MENU_LABELS[purpose_mode(mode)]
        self.tooltip(self.state)

    def set_reasoning(self, value):
        if self.busy:
            return
        values = dict(self.config.get('REASONING_MODES', {}))
        values[str(purpose_mode(self.session.mode))] = value
        path = ROOT / 'preferences.json'
        prefs = validated_preferences(json.loads(path.read_text(encoding='utf-8-sig')))[0] if path.exists() else {}
        save_preferences(path, dict(prefs, REASONING_MODES=values,
            **{k: self.config[k] for k in ('REPLY_MENU_VERSION', 'PROMPT_MODES', 'PROMPT_CUSTOM', 'SAVED_PROMPTS') if k in self.config}))
        self.config['REASONING_MODES'] = values
        self.state = 'Suy luận: ' + ('Nhanh' if value == 'fast' else 'Suy nghĩ kỹ') + ' · áp dụng từ yêu cầu tiếp theo'
        self.tooltip(self.state)
        self.refresh_panel()

    def open_prompt_editor(self, mode):
        if getattr(self, 'prompt_open', False):
            self.activate_editor(getattr(self, 'prompt_hwnd', None))
            return
        from prompt_editor import open_editor
        import copy
        config = {key: copy.deepcopy(self.config[key]) for key in
                  ('PROMPT_MODES', 'PROMPT_CUSTOM', 'ANSWER_STYLE', 'REPLY_MENU_VERSION', 'SAVED_PROMPTS') if key in self.config}
        self.prompt_open = True
        def editor():
            try:
                open_editor(config, mode, self.results)
            except Exception:
                self.results.put(('prompt_closed',))
                self.results.put(('probe', 'Không mở được cửa sổ prompt'))
        threading.Thread(target=editor, daemon=True).start()

    def tooltip(self, status):
        session_error = getattr(getattr(self, "session", None), "error", "")
        body = self.result_text()
        if session_error:
            body = 'ClipboardAI: ' + ('Chưa lưu được lịch sử' if 'lưu' in session_error.lower() else 'Có lỗi lưu phiên') + '\n' + body
        value = short_tooltip(body)
        if value != self.last_tooltip:
            self.nid.szTip = value
            self.shell.Shell_NotifyIconW(1, C.byref(self.nid))
            self.last_tooltip = value
        self.refresh_tray_result()
        self.refresh_status_menu()

    def read_clipboard(self):
        if not self.user.OpenClipboard(self.hwnd):
            return None
        try:
            text = self.read_open_clipboard_text()
            return text, self.user.GetClipboardSequenceNumber()
        finally:
            self.user.CloseClipboard()

    def read_open_clipboard_text(self):
        handle = self.user.GetClipboardData(13)
        if not handle:
            return ""
        ptr = self.kernel.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return C.wstring_at(ptr)
        finally:
            self.kernel.GlobalUnlock(handle)

    def copy_last_answer(self, current_session=False):
        """Explicitly copy a completed answer; never submit another AI request."""
        answer = getattr(self.session, 'last_answer', '') if current_session else (
            getattr(self, 'last_completed_answer', '') or getattr(self.session, 'last_answer', ''))
        if not answer:
            self.state = 'Chưa có đáp án hoàn tất để copy'
        else:
            value = self.read_clipboard()
            if value is None:
                self.state = 'Clipboard đang bận; nhấn Shift+F8 lại để copy đáp án'
            else:
                self.region_clipboard_hold = False
                self.region_notice = ''
                self.pending_write = (answer, value[1], fingerprint(value[0]))
                display = self.display_state()
                if display.phase != 'done' or display.answer != answer:
                    display.finish('done', 0, answer, copy='pending')
                else:
                    display.copy = 'pending'
                self.state = 'Đang copy đáp án hoàn tất gần nhất'
        self.tooltip(self.state)
        self.refresh_panel()

    def write_clipboard(self, text, original_sequence, original_fingerprint=None):
        if not self.user.OpenClipboard(self.hwnd):
            return False
        try:
            if self.user.GetClipboardSequenceNumber() != original_sequence:
                # Any new clipboard event may contain images/files, even when its
                # text is empty or unchanged. Recover explicitly with Shift+F8.
                log_event("stale_answer_discarded")
                self.state = "Đã có kết quả; clipboard đã đổi nên không ghi đè. Shift+F8 để copy lại."
                self.display_state().copy = 'changed'
                self.tooltip(self.state)
                return True
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = self.kernel.GlobalAlloc(0x42, len(data))
            if not handle:
                raise C.WinError(C.get_last_error())
            ptr = self.kernel.GlobalLock(handle)
            if not ptr:
                self.kernel.GlobalFree(handle)
                raise C.WinError(C.get_last_error())
            C.memmove(ptr, data, len(data))
            self.kernel.GlobalUnlock(handle)
            # Register output before Windows emits any clipboard notification.
            self.output_fingerprints.add(fingerprint(text))
            if not self.user.EmptyClipboard() or not self.user.SetClipboardData(13, handle):
                self.kernel.GlobalFree(handle)
                raise C.WinError(C.get_last_error())
            self.own_sequence = self.user.GetClipboardSequenceNumber()
            log_event("answer_copied")
            self.display_state().copy = 'copied'
            self.state = ("Đã copy kết quả nhưng chưa lưu lịch sử; đừng thoát app."
                          if getattr(getattr(self, "session", None), "error", "") else
                          "Đã copy kết quả. F6 chọn phiên, F9 gửi phản hồi, F8 bài mới.")
            self.tooltip(self.state)
            self.show_completion()
            return True
        finally:
            self.user.CloseClipboard()

    def clipboard_changed(self):
        # Sending is explicit through F8/menu; copy notifications never enqueue.
        return

    def worker(self):
        while not self.closed.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                # Freeze provider/settings for all stages of this job. Changing
                # the tray choice must never redirect an old request or image.
                self.client.config = dict(job.get("config", self.config))
                self.client.cancel_event = job["cancel"]
                self.client.deadline = time.monotonic() + job['config']['REPLY_TIMEOUT_S'] if 'REPLY_TIMEOUT_S' in job.get('config', {}) else None
                self.client.on_stream = lambda delta: self.results.put(('stream', job['id'], delta))
                self.client.zoo_notify = lambda message: self.results.put(('zoo_progress', job['id'], message))
                job_capacity = job.get("context_capacity", self.context_capacity())
                history = list(job["history"])
                text = job["text"]
                turns = []
                if job["cancel"].is_set():
                    raise InterruptedError()
                png = job.pop("image_png", None)
                if job.get("screenshot_hwnd") is not None:
                    from screen_capture import CaptureError, capture_foreground_png
                    try:
                        png = capture_foreground_png(job["screenshot_hwnd"])
                    except Exception as exc:
                        log_event("capture_failed", error_type=type(exc).__name__)
                        if isinstance(exc, CaptureError):
                            raise RuntimeError(str(exc)) from None
                        raise RuntimeError("Không chụp được cửa sổ; mở đề ở phía trước rồi nhấn F4. Màn hình khóa hoặc cửa sổ được bảo vệ không chụp được.") from None
                    if job["cancel"].is_set():
                        raise InterruptedError()
                    self.results.put(("captured", job["id"]))
                if is_chat(job['mode']):
                    from conversation_memory import prepare_history
                    memory = job.get('memory_messages', history)
                    if any(SECRET.search(m['content']) for m in memory):
                        raise ValueError('Lịch sử có khóa/mật khẩu; chưa gửi')
                    history, summary, count = prepare_history(self.client, memory, job.get('summary', ''), job.get('summary_count', 0),
                        max(1000, job_capacity * 2 - len(text)), job['cancel'],
                        lambda status: self.results.put(('zoo_progress', job['id'], status)))
                    if SECRET.search(summary):
                        raise ValueError('Tóm tắt có khóa/mật khẩu; chưa gửi')
                    job['summary'], job['summary_count'] = summary, count
                    frames = self.images.get(job['session_id'])
                    if png is not None:
                        frames = self.images.add(job['session_id'], png, job['image_generation'])
                        self.results.put(('image_ready', job['id'], job['session_id'], png))
                    content = text
                    if frames:
                        import base64
                        content = [{'type': 'text', 'text': text}] + [
                            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                             base64.b64encode(frame).decode('ascii'), 'detail': 'original'}} for frame in frames]
                    instruction = instruction_for(job['mode'], job['config'])
                    job['model_started'] = time.monotonic()
                    self.results.put(('model_started', job['id'], job['model_started']))
                    answer, provider = self.client.ask(content, history, instruction=instruction)
                    job['model_seconds'] = max(0, int(time.monotonic() - job['model_started']))
                    if job['cancel'].is_set():
                        raise InterruptedError()
                    stored = text + ('\n[Ảnh đính kèm]' if png is not None else '')
                    turns = [dict(role='user', content=stored), dict(role='assistant', content=answer)]
                    job['api_used'] = provider
                    self.results.put(('done', job, answer, turns))
                    continue
            except Exception as exc:
                job['model_seconds'] = max(0, int(time.monotonic() - job['model_started'])) if 'model_started' in job else 0
                log_event("request_failed", error_type=type(exc).__name__,
                          code=getattr(exc, "code", "timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "request"), request_id=job["id"],
                          phase="vision" if job.get("screenshot_hwnd") is not None and job["text"] == "Đang đọc đề từ ảnh" else "solve")
                if job["cancel"].is_set():
                    error = "Đã hủy yêu cầu"
                else:
                    config = self.client.config
                    timeout = config.get("MIRAI_TIMEOUT_S", "3000") if config.get("SELECTED_MODEL", "deepseek-flash") != "deepseek-flash" and config.get("MODEL_CHOICES") else config.get("DEEPSEEK_TIMEOUT_S", DEEPSEEK_DEFAULT_TIMEOUT_S) if self.is_deepseek else config.get("OLLAMA_TIMEOUT_S", "300")
                    timeout = config.get('REPLY_TIMEOUT_S', timeout)
                    cause = exc.__cause__
                    error = request_error_message(cause if isinstance(cause, (APIHTTPError, TimeoutError, ConnectionError)) else exc, timeout)
                self.results.put(("failed", job, error))
            finally:
                self.client.cancel_event = None
                self.client.on_stream = None
                self.client.deadline = None

    def menu(self):
        self.hide_tray_result()
        capture_hwnd = self.user.GetForegroundWindow()
        self.menu_open = True
        menu = self.user.CreatePopupMenu()
        modes = self.user.CreatePopupMenu()
        try:
            self.last_menu_status = self.display_state().header()
            self.active_status_menu = menu
            self.user.AppendMenuW(menu, 1, 0, self.last_menu_status)
            ai = self.user.CreatePopupMenu()
            self.user.AppendMenuW(menu, 0, 3, 'Mở chat')
            self.user.AppendMenuW(menu, 0, 207, 'Hội thoại / thao tác phiên — ' + self.key_label(207))
            self.user.AppendMenuW(menu, 0, 202, 'Hỏi tiếp từ clipboard — ' + self.key_label(202))
            self.user.AppendMenuW(menu, 0 if self.session.last_answer else 1, 111, "Copy đáp án của phiên đang chọn")
            can_copy = bool(getattr(self, 'last_completed_answer', '') or self.session.last_answer)
            self.user.AppendMenuW(menu, 0 if can_copy else 1, 215, 'Copy đáp án gần nhất — ' + self.key_label(215))
            self.user.AppendMenuW(menu, 1 if self.busy else 0, 107, 'Gửi lại — ' + self.key_label(213))
            network = self.user.CreatePopupMenu()
            self.user.AppendMenuW(network, 1 if self.network_busy else 0, 209, 'Chọn mạng · Wi-Fi / LAN — ' + self.key_label(209) + '×2')
            self.user.AppendMenuW(network, 0, 233, "Mở card mạng để khôi phục thủ công…")
            self.user.AppendMenuW(menu, 0x10, network, 'Mạng · Wi-Fi / LAN — ' + self.key_label(209) + '×2')
            if 'API_ZOO' in self.config:
                self.user.AppendMenuW(ai, 0, 220, 'Quản lý API Zoo…')
                models = self.user.CreatePopupMenu()
                self.zoo_model_commands = {}
                next_model_command = 1000
                for p in self.config['API_ZOO']['profiles']:
                    if not p['enabled']:
                        continue
                    provider_menu = self.user.CreatePopupMenu()
                    if not p['models']:
                        self.user.AppendMenuW(provider_menu, 0, 220, 'Lấy model trong API Zoo…')
                    for offset in range(0, len(p['models']), 30):
                        chunk = p['models'][offset:offset + 30]
                        target_menu = provider_menu if len(p['models']) <= 30 else self.user.CreatePopupMenu()
                        for model in chunk:
                            checked = self.config.get('SELECTED_MODEL') == 'zoo:' + p['id'] and p['model'] == model['id']
                            self.zoo_model_commands[next_model_command] = (p['id'], model['id'])
                            self.user.AppendMenuW(target_menu, 8 if checked else 0, next_model_command, model['id'].replace('&', '&&'))
                            next_model_command += 1
                        if target_menu != provider_menu:
                            self.user.AppendMenuW(provider_menu, 0x10, target_menu, f'{offset + 1}–{offset + len(chunk)}')
                    self.user.AppendMenuW(models, 0x10, provider_menu, p['name'].replace('&', '&&'))
                if not self.zoo_model_commands:
                    self.user.AppendMenuW(models, 0, 220, 'Thêm API / lấy model…')
                self.user.AppendMenuW(ai, 0x10, models, "Chọn model")
            if self.config.get("DEEPSEEK_MODEL") == "deepseek-flash":
                capture = self.user.CreatePopupMenu()
                image_only = self.config.get('F4_INPUT', 'image_clipboard') == 'image'
                self.user.AppendMenuW(capture, 0, 204, 'Chụp câu hỏi mới — ' + self.key_label(204))
                self.user.AppendMenuW(capture, 1 if self.busy else 0, 214, 'Bổ sung ảnh vào phiên — ' + self.key_label(214))
                self.user.AppendMenuW(capture, 0x800, 0, None)
                region = self.config.get('F4_CAPTURE', 'region') == 'region'
                light = self.config.get('REGION_CUE', 'light') == 'light'
                self.user.AppendMenuW(capture, 8 if region else 0, 680, 'Kéo chọn vùng')
                self.user.AppendMenuW(capture, 0 if region else 8, 681, 'Chụp cả cửa sổ')
                self.user.AppendMenuW(capture, 8 if light else 0, 682, 'Dấu chọn: bốn góc nhạt')
                self.user.AppendMenuW(capture, 0 if light else 8, 683, 'Dấu chọn: viền rõ hơn')
                self.user.AppendMenuW(capture, 0x800, 0, None)
                self.user.AppendMenuW(capture, 8 if image_only else 0, 231, 'Chỉ ảnh · không gửi clipboard')
                self.user.AppendMenuW(capture, 0 if image_only else 8, 232, 'Ảnh + clipboard')
                self.user.AppendMenuW(menu, 0x10, capture, 'Chụp ảnh — ' + self.key_label(204) + ' · ' + ('Chỉ ảnh' if image_only else 'Kèm clipboard'))
            current = purpose_mode(self.session.mode)
            for mode in MENU_MODES:
                self.user.AppendMenuW(modes, (8 if current == mode else 0) | (1 if self.busy else 0),
                                      301 + mode, MENU_LABELS[mode])
            self.user.AppendMenuW(modes, 0x800, 0, None)
            reasoning = self.user.CreatePopupMenu()
            selected = reasoning_for(self.config, self.session.mode)
            supported = supports_reasoning_control(self.config)
            for ident, value, label in ((660, 'fast', 'Nhanh'), (661, 'careful', 'Suy nghĩ kỹ')):
                self.user.AppendMenuW(reasoning, (8 if supported and selected == value else 0) | (1 if self.busy or not supported else 0), ident, label)
            if not supported:
                self.user.AppendMenuW(reasoning, 1, 0, 'API/model này tự quyết định suy luận')
            self.user.AppendMenuW(modes, 0x10, reasoning, 'Suy luận · ' + (('Nhanh' if selected == 'fast' else 'Suy nghĩ kỹ') if supported else 'Model quyết định'))
            prompts = self.user.CreatePopupMenu()
            active = selected_prompt(self.session.mode, self.config)
            for ident, value, label in ((662, default_prompt(current), 'Mặc định'), (663, 'free', 'Theo yêu cầu')):
                self.user.AppendMenuW(prompts, (8 if active == value else 0) | (1 if self.busy else 0), ident, label)
            library = self.user.CreatePopupMenu()
            self.saved_prompt_commands = {}
            for index, item in enumerate(self.config.get('SAVED_PROMPTS', [])):
                ident = 50000 + index
                self.saved_prompt_commands[ident] = item
                checked = active == item['style'] and instruction_for(self.session.mode, self.config) == item['text']
                self.user.AppendMenuW(library, (8 if checked else 0) | (1 if self.busy else 0), ident, item['name'].replace('&', '&&'))
            if not self.saved_prompt_commands:
                self.user.AppendMenuW(library, 1, 0, 'Chưa có prompt đã lưu')
            self.user.AppendMenuW(prompts, 0x10, library, 'Prompt đã lưu')
            self.user.AppendMenuW(modes, 0x10, prompts, 'Prompt đang dùng · ' + ('Mặc định' if active == default_prompt(current) else 'Theo yêu cầu' if active == 'free' else 'Đã lưu / riêng'))
            self.user.AppendMenuW(modes, 0, 664, 'Sửa prompt hiện tại…')
            self.user.AppendMenuW(menu, 0x10, modes, "Chế độ trả lời")
            if not is_chat(self.session.mode):
                self.user.AppendMenuW(menu, 0 if self.session.messages else 1, 108, 'Sinh code từ phân tích')
            self.user.AppendMenuW(modes, 8 if self.session.auto_copy else 0, 102, "Tự copy câu trả lời")
            if self.busy:
                self.user.AppendMenuW(menu, 0, 109, 'Hủy yêu cầu — ' + self.key_label(203))
            self.user.AppendMenuW(ai, 0, 103, "Tạm dừng / trả phím cho ứng dụng khác" if self.enabled else "Bật lại phím tắt")
            self.user.AppendMenuW(ai, 0, 221, "Cài đặt / kiểm tra kết nối")
            self.user.AppendMenuW(ai, 0, 665, 'Cài đặt phím tắt…')
            self.user.AppendMenuW(menu, 0x10, ai, "Model / API / cài đặt")
            self.user.AppendMenuW(menu, 0, 2, "Thoát")
            point = W.POINT()
            self.user.GetCursorPos(C.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            choice = self.user.TrackPopupMenu(menu, 0x100 | 2, point.x, point.y, 0, self.hwnd, None)
            if choice == 2:
                self.user.DestroyWindow(self.hwnd)
            elif choice == 3:
                self.settings_visible = False
                self.layout_panel()
                self.show_panel()
            elif choice == 201:
                self.send_clipboard()
            elif choice == 202:
                self.reply_clipboard()
            elif choice == 207:
                self.session_menu()
            elif choice == 204:
                self.send_screenshot(capture_hwnd)
            elif choice == 214:
                self.send_screenshot(capture_hwnd, append=True)
            elif 401 <= choice < 401 + len(self.config.get("MODEL_CHOICES", ())):
                self.select_model(choice)
            elif choice in (301, 302, 303, 304, 305):
                self.change_mode(choice - 301)
            elif choice == 102:
                self.user.SendMessageW(self.controls["auto"], 0xF1, int(not self.session.auto_copy), 0)
                self.command(102)
            elif choice:
                self.command(choice)
        finally:
            self.menu_open = False
            self.active_status_menu = None
            self.user.DestroyMenu(menu)

    def refresh_status_menu(self):
        menu = getattr(self, 'active_status_menu', None)
        if not menu:
            return
        value = self.display_state().header()
        if value == getattr(self, 'last_menu_status', ''):
            return
        class Item(C.Structure):
            _fields_ = [('cbSize', W.UINT), ('fMask', W.UINT), ('fType', W.UINT),
                        ('fState', W.UINT), ('wID', W.UINT), ('hSubMenu', W.HMENU),
                        ('hbmpChecked', W.HANDLE), ('hbmpUnchecked', W.HANDLE),
                        ('dwItemData', C.c_size_t), ('dwTypeData', W.LPWSTR),
                        ('cch', W.UINT), ('hbmpItem', W.HANDLE)]
        text = C.create_unicode_buffer(value)
        item = Item(cbSize=C.sizeof(Item), fMask=0x40, dwTypeData=C.cast(text, W.LPWSTR))
        self.user.SetMenuItemInfoW.argtypes = [W.HMENU, W.UINT, W.BOOL, C.POINTER(Item)]
        self.user.SetMenuItemInfoW.restype = W.BOOL
        if self.user.SetMenuItemInfoW(menu, 0, True, C.byref(item)):
            self.last_menu_status = value
            self.user.GetMenuItemRect.argtypes = [W.HWND, W.HMENU, W.UINT, C.POINTER(W.RECT)]
            rect = W.RECT()
            if self.user.GetMenuItemRect(self.hwnd, menu, 0, C.byref(rect)):
                self.user.WindowFromPoint.argtypes, self.user.WindowFromPoint.restype = [W.POINT], W.HWND
                popup = self.user.WindowFromPoint(W.POINT(rect.left + 2, rect.top + 2))
                if popup:
                    self.user.RedrawWindow.argtypes = [W.HWND, C.c_void_p, W.HANDLE, W.UINT]
                    self.user.RedrawWindow(popup, None, None, 0x101)

    def tick(self):
        if self.pending_read:
            self.clipboard_changed()
        until = time.monotonic() + .025
        stream_dirty = False
        while time.monotonic() < until:
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                break
            kind = result[0]
            if kind == 'region_done':
                self.finish_region_capture(result)
                continue
            if kind == 'hotkey_closed':
                self.hotkey_open = False
                if self.enabled:
                    self.register_hotkeys()
                self.state = ('Phím bị ứng dụng khác chiếm: ' + ', '.join(self.hotkey_errors)
                              if self.hotkey_errors else 'Đã đóng cửa sổ đổi phím; phím tắt đang bật' if self.enabled else 'Phím tắt đang tạm dừng')
                self.tooltip(self.state)
                continue
            if kind == 'hotkey_save':
                try:
                    self.apply_hotkeys(result[1], result[3] if len(result) > 3 else None)
                    self.state = 'Đã lưu; đóng cửa sổ để bật phím mới' if getattr(self, 'hotkey_open', False) else 'Đã lưu phím tắt mới'
                    self.refresh_panel()
                    result[2].put(self.state)
                except (ValueError, OSError) as exc:
                    result[2].put((str(exc) if isinstance(exc, ValueError) else 'Không lưu được phím') + '; đã khôi phục bộ phím trước.')
                self.tooltip(self.state)
                continue
            if kind == 'model_started':
                if result[1] == self.current_id and self.busy:
                    self.display_state().begin_model(result[2])
                continue
            if kind in ('zoo_opened', 'prompt_opened'):
                setattr(self, 'zoo_hwnd' if kind == 'zoo_opened' else 'prompt_hwnd', result[1])
                continue
            if kind == 'prompt_closed':
                self.prompt_open = False
                self.prompt_hwnd = None
                continue
            if kind == 'prompt_save':
                try:
                    if self.busy:
                        raise ValueError('Chờ yêu cầu hiện tại xong rồi lưu prompt.')
                    self.set_mode_prompt(result[1], result[2], result[3])
                    values = {k: self.config.get(k, {} if k != 'SAVED_PROMPTS' else []) for k in ('PROMPT_MODES', 'PROMPT_CUSTOM', 'SAVED_PROMPTS')}
                    result[4].put(('saved', 'Đã lưu · áp dụng từ yêu cầu tiếp theo.', values, result[2]))
                except (ValueError, OSError, TypeError, RecursionError) as exc:
                    message = str(exc) if isinstance(exc, ValueError) else 'Không lưu được prompt; lựa chọn cũ vẫn giữ.'
                    result[4].put(('error', message))
                continue
            if kind == 'stream':
                if result[1] == self.current_id and self.busy:
                    self.preview_answer = '' if result[2] is None else self.preview_answer + result[2]
                    stream_dirty = True
                continue
            if kind == 'zoo_closed':
                self.zoo_open = False
                self.zoo_hwnd = None
                continue
            if kind == 'zoo_save':
                from api_zoo import ZooStore, apply_config, ZooRouter
                if self.busy or self.probe_busy or self.network_busy:
                    result[2].put(('save', 'Đang xử lý AI/mạng; chờ hoàn tất rồi Lưu & áp dụng lại.'))
                    continue
                try:
                    data = ZooStore(ROOT).save(result[1])
                    self.config['ZOO_ONLY'] = True
                    apply_config(self.config, data, select_primary=True)
                    self.client.zoo_router = ZooRouter()
                    self.set_text('model', self.model_name())
                    self.set_text('predict', str(self.token_limit()))
                    self.set_text('timeout', str(self.request_timeout()))
                    self.state = 'Đã lưu API Zoo — ' + self.model_name()
                    result[2].put(('save', self.state))
                    self.tooltip(self.state)
                except (OSError, ValueError, TypeError):
                    result[2].put(('save', 'Không lưu được API Zoo; cấu hình đang dùng vẫn giữ nguyên.'))
                continue
            if kind == 'zoo_progress':
                if result[1] == self.current_id and self.busy:
                    self.state = result[2]
                    attempt = re.search(r'lần (\d+)/', result[2])
                    if attempt and int(attempt.group(1)) > 1:
                        self.display_state().stage = 'API dự phòng đang xử lý'
                    self.tooltip(self.state)
                continue
            if kind == "network":
                self.network_busy = False
                if not self.busy:
                    self.state = result[1]["message"]
                self.tooltip(self.state)
                log_event("network_done", ok=result[1].get("ok", False))
                if len(result) > 2 and result[2]:
                    if result[2] == 'wifi':
                        self.wifi_menu(result[1])
                    elif result[1].get('ok'):
                        self.network_menu()
                continue
            if kind == "probe":
                self.probe_busy = False
                self.set_text("note", result[1])
                if not self.busy:
                    self.state = result[1]
                continue
            if kind == "stage":
                if result[1] == self.current_id:
                    self.state = result[2]
                    self.set_text("answer", result[3])
                    self.tooltip(self.state)
                continue
            if kind == "captured":
                if result[1] == self.current_id:
                    self.capture_pending = False
                    self.state = "Đã chụp ảnh"
                    self.display_state().stage = 'Đã chụp · chuẩn bị gửi AI'
                    self.tooltip(self.state)
                continue
            if kind == 'image_ready':
                if result[1] == self.current_id:
                    self.last_image = dict(session_id=result[2], png=result[3])
                continue
            if kind == "extracted":
                if result[1] == self.current_id:
                    if not result[3] and not self.session.new_problem(result[2]):
                        self.cancel_request()
                        self.state = self.session.error
                        continue
                    self.set_text("answer", "")
                    self.set_text("feedback", "")
                    self.session.problem = result[2]
                    self.session.clear_capture(save=False)
                    self.session.last_request = result[2]
                    self.session.last_action = "problem"
                    self.session.save()
                    self.set_text("problem", result[2])
                    self.state = "Đã đọc đề; đang phân tích / sinh code"
                    self.tooltip(self.state)
                continue
            job = result[1]
            if job["id"] != self.current_id:
                continue
            self.busy = False
            self.capture_pending = False
            self.preview_answer = self.preview_request = ''
            self.elapsed_done = int(time.monotonic() - self.started) if self.started else 0
            if kind == "failed":
                self.state = result[2]
                self.display_state().finish('failed', job.get('model_seconds', self.display_state().seconds()),
                                            error=result[2])
                if is_chat(job.get('mode', 0)):
                    restore_input = not self.text('problem').strip()
                    if restore_input:
                        self.set_text('problem', job['text'])
                    if restore_input and job.get('action') == 'image' and getattr(self, 'last_image', None):
                        self.pending_image = self.last_image
            elif kind == "partial":
                reading = result[2]
                if reading["readable"]:
                    old_pages = self.session.capture_pages if job.get("capture_previous") and not reading["new_problem"] else 0
                    if not old_pages and not self.session.new_problem():
                        self.state = self.session.error
                        self.tooltip(self.state)
                        continue
                    if not old_pages:
                        self.set_text("answer", "")
                        self.set_text("feedback", "")
                    self.session.capture_text = reading["text"]
                    self.session.capture_source = job.get("capture_source", "")
                    self.session.capture_pages = old_pages + 1
                    self.session.capture_missing = reading["missing"]
                    self.session.save()
                    self.set_text("problem", reading["text"])
                    self.state = f"Đã giữ {self.session.capture_pages} phần đề. Thiếu: " + "; ".join(reading["missing"])[:115] + ". Cuộn trang rồi F4 tiếp."
                    log_event("capture_partial", pages=self.session.capture_pages)
                else:
                    self.state = "Chưa đọc được chữ đề; phóng to chữ và F4 lại. Bản nháp trước vẫn giữ."
            else:
                answer, turns = result[2:]
                if job["action"] == "problem":
                    self.session.messages = []
                if job.get('replace_last'):
                    self.session.messages = self.session.messages[:-2]
                if is_chat(job.get('mode', 0)):
                    self.session.summary = job.get('summary', '')
                    self.session.summary_count = job.get('summary_count', 0)
                    if not self.session.problem:
                        self.session.problem = job['text']
                saved = self.session.commit(turns, answer, job["text"] if job["action"] == "problem" else None)
                self.display_state().finish('done', job.get('model_seconds', self.display_state().seconds()), answer,
                                            copy='pending' if self.session.auto_copy and self.enabled and job['sequence'] is not None else 'manual')
                self.last_completed_answer = answer
                self.set_text("answer", answer)
                self.state = ("Hoàn tất; kết quả đã lưu trong phiên" if saved else
                              "Đã có kết quả nhưng chưa lưu lịch sử; đừng thoát app, kiểm tra ổ đĩa/quyền ghi.")
                if saved and job.get('api_used'):
                    self.state += ' — ' + job['api_used'][:80]
                    self.last_api_used = job['api_used']
                if self.session.auto_copy and self.enabled and job["sequence"] is not None:
                    self.pending_write = (answer, job["sequence"], job["digest"])
                else:
                    self.show_completion()
                self.output_fingerprints.add(fingerprint(answer))
                log_event("request_done", request_id=job["id"], elapsed=self.elapsed_done, session_saved=bool(saved))
            if getattr(self, 'panel_ready', False):
                self.render_conversation()
                self.set_text('problem_label', 'Ảnh đã đính kèm · Nhập câu hỏi về ảnh' if getattr(self, 'pending_image', None) else 'Câu hỏi · Enter gửi · Shift+Enter xuống dòng')
            self.tooltip(self.state)
        if self.pending_write and not getattr(self, 'region_pending', False) and not getattr(self, 'region_clipboard_hold', False):
            try:
                if self.write_clipboard(*self.pending_write):
                    self.pending_write = None
            except OSError:
                self.pending_write = None
                self.state = "Đã có kết quả nhưng không copy được; chọn Copy kết quả ở tray"
                self.display_state().copy = 'error'
                self.tooltip(self.state)
        if stream_dirty and getattr(self, 'panel_ready', False):
            self.render_conversation()
        self.refresh_panel()
        self.tooltip(self.state)
        self.update_notice()
        if self.self_test:
            # This runs inside the actual packaged Windows message loop.
            report = {"ok": True, "backend": "Windows native", "tray": True, "clipboard_listener": True,
                      "clipboard_read": self.read_clipboard() is not None, "message_loop": True,
                      "qt_imported": any(m.startswith("PySide6") for m in sys.modules)}
            report.update(panel=len(self.controls) > 20, session=True)
            report.update(hotkeys=len(self.hotkeys), hotkey_errors=self.hotkey_errors, auto_send=False)
            report.update(status_card=False, completion_check=bool(self.notice), screenshot_module=False)
            if self.config.get("DEEPSEEK_MODEL") == "deepseek-flash":
                import screen_capture
                from PIL import ImageGrab
                report["screenshot_module"] = callable(screen_capture.capture_foreground_png) and callable(ImageGrab.grab)
            expected = sum(i not in (204, 214) or self.config.get('DEEPSEEK_MODEL') == 'deepseek-flash'
                           for i in hotkey_bindings(self.config.get('HOTKEYS'), self.config.get('HOTKEYS_DISABLED')))
            report["ok"] = len(self.hotkeys) == expected and not self.hotkey_errors and bool(self.notice)
            (ROOT / "self-test.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            self.user.DestroyWindow(self.hwnd)

    def window_proc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x0138 and lp == getattr(self, 'hover_window', None):
                self.gdi.SetTextColor(W.HDC(wp), 0xB8B8B8)
                self.gdi.SetBkColor(W.HDC(wp), 0xFFFFFF)
                return self.hover_background
            if msg == 0x031D:
                self.clipboard_changed()
                return 0
            if msg == 0x0113:
                self.tick()
                return 0
            if msg == 0x8001:
                event = lp & 0xFFFF
                if event == 0x406:
                    self.show_tray_result()
                elif event == 0x407:
                    self.hide_tray_result()
                elif event in ((0x7B, 0x400, 0x401) if getattr(self, 'tray_version4', False) else (0x0205, 0x0202)):
                    self.menu()
                return 0
            if msg == 0x0312:
                if getattr(self, 'hotkey_open', False) or not getattr(self, 'enabled', True):
                    return 0
                shortcut_config = getattr(self, 'config', {})
                if wp not in hotkey_bindings(shortcut_config.get('HOTKEYS'), shortcut_config.get('HOTKEYS_DISABLED')):
                    return 0
                if getattr(self, 'region_pending', False):
                    if wp == 203:
                        self.cancel_request()
                    return 0
                log_event("hotkey", key=self.key_label(wp))
                if wp == 201:
                    self.send_clipboard()
                elif wp == 202:
                    self.reply_clipboard()
                elif wp == 203:
                    self.cancel_request()
                    self.tooltip(self.state)
                elif wp == 204:
                    self.send_screenshot()
                elif wp == 207:
                    self.session_menu()
                elif wp == 209:
                    self.network_hotkey()
                elif wp == 213:
                    if not self.busy:
                        self.resend_session()
                elif wp == 214:
                    self.send_screenshot(append=True)
                elif wp == 215:
                    self.copy_last_answer()
                return 0
            if msg == 5:
                self.layout_panel()
                return 0
            if msg == 0x24:  # WM_GETMINMAXINFO
                class MinMax(C.Structure):
                    _fields_ = [('reserved', W.POINT), ('max_size', W.POINT), ('max_pos', W.POINT), ('min_track', W.POINT), ('max_track', W.POINT)]
                bounds = C.cast(lp, C.POINTER(MinMax)).contents
                bounds.min_track.x, bounds.min_track.y = 460, 450
                return 0
            if msg == 0x0111:
                if not getattr(self, "panel_ready", False):
                    return 0
                self.command(wp & 0xFFFF)
                return 0
            if msg == 0x0010:
                self.user.ShowWindow(hwnd, 0)
                return 0
            if msg == 2:
                self.closed.set()
                if getattr(self, 'region_pending', False):
                    self.region_cancel.set()
                self.client.cancel()
                if getattr(self, 'hover_window', None):
                    self.user.DestroyWindow(self.hover_window)
                    if getattr(self, 'hover_font', None):
                        self.gdi.DeleteObject(self.hover_font)
                if getattr(self, "notice", None):
                    self.user.DestroyWindow(self.notice)
                    self.gdi.DeleteObject(self.check_font)
                for ident in self.hotkeys:
                    self.user.UnregisterHotKey(hwnd, ident)
                self.user.RemoveClipboardFormatListener(hwnd)
                self.user.KillTimer(hwnd, 1)
                if hasattr(self, "nid"):
                    self.shell.Shell_NotifyIconW(2, C.byref(self.nid))
                self.user.PostQuitMessage(0)
                return 0
            if msg == getattr(self, "taskbar_message", -1) and hasattr(self, "nid"):
                self.shell.Shell_NotifyIconW(0, C.byref(self.nid))
                self.nid.uTimeoutOrVersion = 4
                self.tray_version4 = bool(self.shell.Shell_NotifyIconW(4, C.byref(self.nid)))
                return 0
        except Exception as exc:
            log_event("windows_callback_error", error_type=type(exc).__name__)
        return self.user.DefWindowProcW(hwnd, msg, wp, lp)

    def run(self):
        msg = W.MSG()
        try:
            while True:
                result = self.user.GetMessageW(C.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise C.WinError(C.get_last_error())
                if msg.message == 0x100 and msg.wParam == 27:
                    self.user.ShowWindow(self.hwnd, 0)
                    continue
                if not self.user.IsDialogMessageW(self.hwnd, C.byref(msg)):
                    self.user.TranslateMessage(C.byref(msg))
                    self.user.DispatchMessageW(C.byref(msg))
        finally:
            self.kernel.CloseHandle(self.mutex)


if __name__ == "__main__":
    bundled_server = None
    setup_mutex = None
    setup_kernel = None
    try:
        config_override = None
        if (ROOT / "payload" / "manifest.json").exists() and "--self-test" not in sys.argv:
            _, setup_kernel, _ = setup_winapi()
            setup_mutex = setup_kernel.CreateMutexW(None, False, "Local\\ClipboardAI.USB.Setup.v1")
            if not setup_mutex:
                raise C.WinError(C.get_last_error())
            if C.get_last_error() == 183:
                raise SystemExit(0)
            from usb_setup import prepare_server
            config_override, bundled_server = prepare_server(ROOT)
        app = WindowsApp("--self-test" in sys.argv or "--setup-test" in sys.argv, config_override)
        app.run()
    except Exception as exc:
        log_event("startup_failed", error_type=type(exc).__name__)
        if (ROOT / "payload" / "manifest.json").exists():
            (ROOT / "setup-status.txt").write_text("Setup failed: " + str(exc), encoding="utf-8")
        if "--self-test" in sys.argv:
            (ROOT / "self-test.json").write_text(json.dumps({"ok": False, "error": str(exc)}), encoding="utf-8")
        raise
    finally:
        if bundled_server is not None:
            bundled_server.terminate()
            try:
                bundled_server.wait(timeout=10)
            except Exception:
                bundled_server.kill()
        if setup_mutex is not None:
            setup_kernel.CloseHandle(setup_mutex)
