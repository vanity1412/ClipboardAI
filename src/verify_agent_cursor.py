"""Frozen cursor smoke check with no capture, observation, or input actions."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import re
import threading
import time

from cua_mcp import CuaMCP


def run(root):
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, desktop_captures=0, input_actions=0,
        observed_user_windows=0, owned_cursor_session_only=True)
    arrow = hwnd = None
    user = None
    old_dpi = None
    failure = None
    try:
        assert os.name == 'nt', 'Cursor verification requires Windows'
        user = C.WinDLL('user32', use_last_error=True)
        user.GetForegroundWindow.argtypes = []
        user.GetForegroundWindow.restype = W.HWND
        user.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
        user.GetCursorPos.restype = W.BOOL
        user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        user.GetWindowRect.restype = W.BOOL
        user.IsWindowVisible.argtypes = [W.HWND]
        user.IsWindowVisible.restype = W.BOOL
        user.IsWindow.argtypes = [W.HWND]
        user.IsWindow.restype = W.BOOL
        get_style = user.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user.GetWindowLongW
        get_style.argtypes, get_style.restype = [W.HWND, C.c_int], C.c_ssize_t
        set_dpi = getattr(user, 'SetThreadDpiAwarenessContext', None)
        if set_dpi:
            set_dpi.argtypes, set_dpi.restype = [C.c_void_p], C.c_void_p
            old_dpi = set_dpi(C.c_void_p(-4))
        with CuaMCP(destination, threading.Event()) as mcp:
            report['cursor_style'] = mcp.cursor_style
            assert mcp.cursor_style == 'windows-arrow', 'Native Windows arrow unavailable'
            action_sessions = {name: 'session' in mcp.tools.get(name, {}).get(
                'inputSchema', {}).get('properties', {})
                for name in ('click', 'scroll', 'type_text', 'press_key')}
            report['action_session_arguments'] = action_sessions
            report['all_actions_use_cursor_session'] = all(action_sessions.values())
            assert report['all_actions_use_cursor_session'], 'Action cursor session unsupported'
            state = mcp.call('get_agent_cursor_state', {'session': mcp.cursor_session})
            structured = state.get('structuredContent')
            data = structured if isinstance(structured, dict) else None
            report['cursor_state_is_error'] = bool(state.get('isError'))
            report['cursor_state_structured_fields'] = sorted(
                key for key in structured if isinstance(key, str) and
                re.fullmatch(r'[A-Za-z0-9_]{1,64}', key))[:32] if isinstance(structured, dict) else []
            report['cursor_state_source'] = 'structured' if data is not None else 'absent'
            if data is None:
                for block in state.get('content', []):
                    if (isinstance(block, dict) and block.get('type') == 'text'
                            and isinstance(block.get('text'), str) and len(block['text']) <= 8192):
                        try:
                            candidate = json.loads(block['text'])
                        except (ValueError, TypeError):
                            continue
                        if isinstance(candidate, dict):
                            data = candidate
                            report['cursor_state_source'] = 'text-json'
                            break
            data = data or {}
            report['cursor_state_enabled'] = data.get('enabled') if type(data.get('enabled')) is bool else None
            report['cursor_state_session_matches'] = data.get('session') == mcp.cursor_session
            error = data.get('error')
            code = error.get('code') if isinstance(error, dict) else None
            report['cursor_state_error_code'] = code if isinstance(code, str) and re.fullmatch(
                r'[A-Za-z0-9_.:-]{1,64}', code) else None
            report['cua_cursor_disabled'] = not state.get('isError') and data.get('enabled') is False
            assert report['cua_cursor_disabled'], 'Cua pointer effects remain enabled'
            arrow = mcp.arrow_overlay
            assert arrow and arrow.available, 'Owned native cursor unavailable'
            hwnd = arrow._hwnd
            pixels = arrow._pixels_bgra
            width, height = arrow._cursor_size
            report['cursor_size'] = [width, height]
            report['hotspot'] = list(arrow._hotspot)
            assert len(pixels) == width * height * 4, 'Owned cursor pixel buffer invalid'
            alphas = pixels[3::4]
            report['alpha_range'] = [min(alphas), max(alphas)]
            report['visible_cursor_pixels'] = sum(alpha != 0 for alpha in alphas)
            assert 0 < report['visible_cursor_pixels'] < width * height, 'Arrow transparency invalid'
            foreground_before = user.GetForegroundWindow()
            pointer_before = W.POINT()
            assert user.GetCursorPos(C.byref(pointer_before)), 'Could not read pointer position'
            started = time.monotonic()
            assert arrow.update(pointer_before.x, pointer_before.y), 'Could not show owned arrow'
            while not user.IsWindowVisible(hwnd) and time.monotonic() - started < .06:
                time.sleep(.002)
            report['owned_window_visible'] = bool(user.IsWindowVisible(hwnd))
            assert report['owned_window_visible'], 'Owned arrow did not become visible'
            extended_style = get_style(hwnd, -20)
            report['owned_window_styles'] = {
                'no_activate': bool(extended_style & 0x08000000),
                'transparent': bool(extended_style & 0x00000020),
                'layered': bool(extended_style & 0x00080000),
                'tool_window': bool(extended_style & 0x00000080)}
            assert all(report['owned_window_styles'].values()), 'Owned arrow input/focus styles incorrect'
            bounds = W.RECT()
            assert user.GetWindowRect(hwnd, C.byref(bounds)), 'Could not read owned cursor bounds'
            report['owned_bounds_match_hotspot'] = (
                bounds.left == pointer_before.x - arrow._hotspot[0]
                and bounds.top == pointer_before.y - arrow._hotspot[1]
                and bounds.right - bounds.left == width
                and bounds.bottom - bounds.top == height)
            assert report['owned_bounds_match_hotspot'], 'Owned arrow hotspot incorrect'
            time.sleep(max(0, .06 - (time.monotonic() - started)))
            report['hide_acknowledged'] = arrow.hide(wait=True)
            report['owned_window_hidden'] = not user.IsWindowVisible(hwnd)
            report['display_interval_ms'] = round((time.monotonic() - started) * 1000, 2)
            assert report['hide_acknowledged'] and report['owned_window_hidden'], 'Owned arrow hide failed'
            assert report['display_interval_ms'] <= 100, 'Owned arrow display exceeded smoke interval'
            pointer_after = W.POINT()
            assert user.GetCursorPos(C.byref(pointer_after)), 'Could not reread pointer position'
            # A person or another program can change focus/pointer during the
            # sample. Record what was observed without attributing it to this
            # overlay; native input/focus styles are the owned safety check.
            report['foreground_unchanged_during_sample'] = user.GetForegroundWindow() == foreground_before
            report['system_pointer_unchanged_during_sample'] = (
                pointer_after.x == pointer_before.x and pointer_after.y == pointer_before.y)
    except Exception as exc:
        failure = exc
        report['error_type'] = type(exc).__name__
    finally:
        if arrow:
            arrow.close()
            report['owned_thread_stopped'] = not arrow._thread or not arrow._thread.is_alive()
        if hwnd and user:
            report['owned_window_destroyed'] = not user.IsWindow(hwnd)
        if old_dpi:
            user.SetThreadDpiAwarenessContext(old_dpi)
        report['ok'] = failure is None and report.get('owned_thread_stopped') is True and report.get(
            'owned_window_destroyed') is True
        (destination / 'agent-cursor-verification.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if failure:
        raise failure
    assert report['ok'], 'Owned cursor cleanup incomplete'
    return report
