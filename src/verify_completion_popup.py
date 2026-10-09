"""Verify the native answer popup using owned synthetic windows only.

Run ``run(output_directory)`` on an interactive Windows desktop. This deliberately
does not construct the normal app, read its configuration, register hotkeys,
inspect the clipboard, or contact an API.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import time


def run(output):
    if os.name != 'nt':
        raise RuntimeError('Completion popup verification requires Windows')
    from PIL import ImageGrab
    from completion_popup import POPUP_SECONDS, POPUP_TEXT_COLOR
    from request_display import RequestDisplay
    from window_layout import work_area
    from windows_native import WindowsApp, setup_winapi

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, synthetic_windows_only=True, network_calls=0,
                  clipboard_reads=0, clipboard_writes=0, normal_app_initialized=False)
    user, kernel, shell = setup_winapi()
    previous_foreground = user.GetForegroundWindow()
    app, background, owner, menu = None, None, None, None
    old_dpi, foreground_before, menu_timer = None, None, None
    set_dpi = getattr(user, 'SetThreadDpiAwarenessContext', None)
    if set_dpi is not None:
        set_dpi.argtypes, set_dpi.restype = [C.c_void_p], C.c_void_p
        old_dpi = set_dpi(C.c_void_p(-4))

    user.PeekMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
    user.PeekMessageW.restype = W.BOOL
    user.GetWindow.argtypes, user.GetWindow.restype = [W.HWND, W.UINT], W.HWND
    getter = user.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user.GetWindowLongW
    getter.argtypes, getter.restype = [W.HWND, C.c_int], C.c_ssize_t

    def pump():
        message = W.MSG()
        while user.PeekMessageW(C.byref(message), None, 0, 0, 1):
            if message.message != 0x12:
                user.TranslateMessage(C.byref(message))
                user.DispatchMessageW(C.byref(message))

    def rect(hwnd):
        value = W.RECT()
        assert user.GetWindowRect(hwnd, C.byref(value)), 'Could not read owned window bounds'
        return value.left, value.top, value.right, value.bottom

    def above(upper, lower):
        current = lower
        for _ in range(10000):
            current = user.GetWindow(current, 3)  # GW_HWNDPREV
            if not current:
                return False
            if current == upper:
                return True
        return False

    def show(answer):
        app.request_display.finish('done', 2, answer)
        app.show_completion()
        pump()
        assert user.IsWindowVisible(app.notice), 'Answer popup was not visible'
        assert user.GetForegroundWindow() == foreground_before, 'Popup took foreground focus'
        assert above(app.notice, background), 'Popup was below the owned fullscreen window'

    try:
        class MonitorInfo(C.Structure):
            _fields_ = [('size', W.DWORD), ('monitor', W.RECT),
                        ('work', W.RECT), ('flags', W.DWORD)]
        user.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        user.MonitorFromWindow.restype = W.HANDLE
        user.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
        user.MonitorFromPoint.restype = W.HANDLE
        user.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
        user.GetMonitorInfoW.restype = W.BOOL
        if previous_foreground:
            monitor = user.MonitorFromWindow(previous_foreground, 2)
        else:
            point = W.POINT()
            user.GetCursorPos(C.byref(point))
            monitor = user.MonitorFromPoint(point, 2)
        info = MonitorInfo(size=C.sizeof(MonitorInfo))
        assert user.GetMonitorInfoW(monitor, C.byref(info)), 'Could not read monitor bounds'
        box = info.monitor
        monitor_box = box.left, box.top, box.right, box.bottom
        instance = kernel.GetModuleHandleW(None)
        # A topmost borderless native window covers the entire chosen monitor.
        # Any preview below is restricted to these owned synthetic pixels.
        background = user.CreateWindowExW(0x8, 'STATIC',
            'SYNTHETIC FULLSCREEN TEST\r\nClipboardAI answer popup\r\n'
            'No live website, private desktop content, clipboard, or API is used.\r\n'
            'The answer card should appear at the bottom-right without taking focus.',
            0x80000000 | 0x1, box.left, box.top, box.right - box.left,
            box.bottom - box.top, None, None, instance, None)
        assert background, 'Could not create the synthetic fullscreen window'
        user.ShowWindow(background, 5)
        user.SetWindowPos(background, W.HWND(-1), box.left, box.top,
                          box.right - box.left, box.bottom - box.top, 0x40)
        user.SetForegroundWindow(background)
        user.UpdateWindow(background)
        pump()
        # Windows may deny activation for a process started from a background
        # shell. Keep the observed foreground baseline in that case; the owned
        # topmost sample still covers the chosen monitor and protects previews.
        foreground_before = user.GetForegroundWindow()
        assert work_area(hwnd=foreground_before) == (
            info.work.left, info.work.top, info.work.right, info.work.bottom
        ), 'Foreground moved to another monitor during synthetic setup'
        report['synthetic_window_was_foreground'] = foreground_before == background
        assert rect(background) == monitor_box, 'Synthetic fullscreen bounds differ from monitor'

        app = WindowsApp.__new__(WindowsApp)
        app.user, app.kernel, app.shell = user, kernel, shell
        app.instance = instance
        app.callback_type = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        app.config = {}
        app.self_test = app.busy = app.capture_pending = app.region_pending = app.exiting = False
        app.notice_until, app.notice_text = 0, None
        app.request_display = RequestDisplay()
        app.create_notice()

        class LogFont(C.Structure):
            _fields_ = [('height', W.LONG), ('width', W.LONG), ('escapement', W.LONG),
                        ('orientation', W.LONG), ('weight', W.LONG), ('italic', W.BYTE),
                        ('underline', W.BYTE), ('strikeout', W.BYTE), ('charset', W.BYTE),
                        ('out_precision', W.BYTE), ('clip_precision', W.BYTE),
                        ('quality', W.BYTE), ('pitch_and_family', W.BYTE),
                        ('face_name', W.WCHAR * 32)]
        app.gdi.GetObjectW.argtypes = [W.HANDLE, C.c_int, C.c_void_p]
        app.gdi.GetObjectW.restype = C.c_int
        font = LogFont()
        assert app.gdi.GetObjectW(app.notice_font, C.sizeof(font), C.byref(font)), 'Could not read popup font'
        assert (font.height, font.weight, font.face_name) == (-14, 400, 'Segoe UI'), 'Popup font differs from tray font'
        assert POPUP_TEXT_COLOR == 0xB8B8B8, 'Popup text color differs from requested gray'
        report.update(font_height=font.height, font_weight=font.weight, font_face=font.face_name,
                      text_color='#B8B8B8', expected_visible_seconds=POPUP_SECONDS)

        synthetic_answer = '1. A\n2. B\n3. C\n4. D\n5. A\n6. Chưa xác định · thiếu dữ kiện'
        show(synthetic_answer)
        styles = getter(app.notice, -20)
        for name, flag in (('topmost', 0x8), ('no_activate', 0x08000000),
                           ('click_through', 0x20), ('layered', 0x80000)):
            assert styles & flag, 'Required popup style missing: ' + name
            report[name] = True
        assert user.SendMessageW(app.notice, 0x21, 0, 0) == 3, 'Popup permits mouse activation'
        bounds = rect(app.notice)
        hit_point = ((bounds[1] + 5) & 0xffff) << 16 | ((bounds[0] + 5) & 0xffff)
        assert user.SendMessageW(app.notice, 0x84, 0, hit_point) == -1, 'Popup consumes hit testing'
        work = work_area(hwnd=background)
        assert work[0] <= bounds[0] < bounds[2] <= work[2], 'Popup exceeds work-area width'
        assert work[1] <= bounds[1] < bounds[3] <= work[3], 'Popup exceeds work-area height'
        assert work[2] - bounds[2] == work[3] - bounds[3] == 12, 'Popup is not anchored bottom-right'
        assert '1. A' in app.notice_text and '6' in app.notice_text, 'Answer snapshot lost sample content'
        report.update(foreground_preserved=True, hit_test_transparent=True,
                      topmost_above_fullscreen=True, bounds=list(bounds), work_area=list(work),
                      monitor_bounds=list(monitor_box), answer_lines=list(app.notice_lines))

        # Production excludes the card from screen capture. Clear that flag only
        # on this synthetic instance so the verifier can inspect rendered pixels.
        assert user.SetWindowDisplayAffinity(app.notice, 0), 'Could not enable synthetic preview capture'
        preview_path = destination / 'completion-popup-preview.png'
        # Affinity/visibility changes reach the compositor asynchronously.
        # CAPTUREBLT is also required to include this WS_EX_LAYERED card.
        try:
            flush = C.WinDLL('dwmapi').DwmFlush
            flush.argtypes, flush.restype = [], C.c_long
        except (OSError, AttributeError):
            flush = None
        app.show_completion()
        popup_image, contrast, attempts = None, 0, 0
        try:
            deadline = time.monotonic() + 0.75
            while time.monotonic() < deadline:
                pump()
                user.InvalidateRect(app.notice, None, False)
                user.UpdateWindow(app.notice)
                if flush is not None:
                    report['composition_flushed'] = flush() == 0
                assert user.GetForegroundWindow() == foreground_before and above(app.notice, background)
                if popup_image is not None:
                    popup_image.close()
                popup_image = ImageGrab.grab(bbox=bounds, all_screens=True,
                                            include_layered_windows=True).convert('RGB')
                attempts += 1
                low, high = popup_image.convert('L').getextrema()
                contrast = high - low
                if contrast > 30:
                    break
                time.sleep(0.03)
            report.update(preview_capture_attempts=attempts, preview_pixel_contrast=contrast)
            assert popup_image is not None and contrast > 30, 'Rendered popup appears blank'
            popup_image.save(preview_path)
            report.update(rendered_pixels_nonblank=True, preview=str(preview_path.resolve()))
        finally:
            if popup_image is not None:
                popup_image.close()
            user.SetWindowDisplayAffinity(app.notice, 0x11)

        # Restart the configured interval independently of preview capture time.
        show('1. A\n2. B\n3. C')
        started = time.monotonic()
        while user.IsWindowVisible(app.notice) and time.monotonic() - started < POPUP_SECONDS + 0.5:
            pump()
            app.update_notice()
            time.sleep(0.01)
        elapsed = time.monotonic() - started
        assert not user.IsWindowVisible(app.notice), 'Popup did not expire'
        assert POPUP_SECONDS - 0.15 <= elapsed <= POPUP_SECONDS + 0.4, 'Popup duration differs from configured interval'
        assert user.GetForegroundWindow() == foreground_before, 'Expiry changed foreground focus'
        report.update(expired=True, visible_seconds=round(elapsed, 3))

        for flag in ('busy', 'capture_pending', 'region_pending', 'self_test', 'exiting'):
            show('1. D')
            setattr(app, flag, True)
            app.update_notice()
            assert not user.IsWindowVisible(app.notice), 'Popup stayed visible during ' + flag
            setattr(app, flag, False)
            report['hidden_during_' + flag] = True

        # Track an owned synthetic network menu over the fullscreen sample.
        # The timer runs on this same UI thread inside TrackPopupMenu's modal
        # loop, inspects only its owned menu windows, then dismisses the menu.
        owner = user.CreateWindowExW(0, 'STATIC', 'SYNTHETIC MENU OWNER',
            0x80000000, box.left + 40, box.top + 40, 1, 1,
            None, None, instance, None)
        assert owner, 'Could not create hidden synthetic menu owner'
        app.hwnd = owner
        assert not user.IsWindowVisible(owner) and not getter(owner, -20) & 0x8
        menu = user.CreatePopupMenu()
        assert menu, 'Could not create synthetic F3 network menu'
        assert user.AppendMenuW(menu, 0, 501, 'F3 test: synthetic Wi-Fi choice')
        assert user.AppendMenuW(menu, 0, 502, 'F3 test: synthetic LAN choice')
        assert user.AppendMenuW(menu, 1, 503, 'Synthetic only: no adapter changes')
        kernel.GetCurrentThreadId.argtypes, kernel.GetCurrentThreadId.restype = [], W.DWORD
        thread = kernel.GetCurrentThreadId()
        enum_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        user.EnumThreadWindows.argtypes = [W.DWORD, enum_type, W.LPARAM]
        user.EnumThreadWindows.restype = W.BOOL
        user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        user.GetClassNameW.restype = C.c_int
        user.EndMenu.argtypes, user.EndMenu.restype = [], W.BOOL
        timer_type = C.WINFUNCTYPE(None, W.HWND, W.UINT, C.c_size_t, W.DWORD)
        user.SetTimer.argtypes = [W.HWND, C.c_size_t, W.UINT, timer_type]
        menu_checked, menu_errors = [], []

        def inspect_menu(hwnd, message, timer_id, ticks):
            user.KillTimer(None, timer_id)
            try:
                visible_menus = []
                def collect(window, param):
                    name = C.create_unicode_buffer(64)
                    user.GetClassNameW(window, name, len(name))
                    if name.value == '#32768' and user.IsWindowVisible(window):
                        visible_menus.append(window)
                    return True
                enum_callback = enum_type(collect)
                user.EnumThreadWindows(thread, enum_callback, 0)
                assert visible_menus, 'Native synthetic network menu was not visible'
                assert any(above(window, background) for window in visible_menus), 'Native menu was below fullscreen sample'
                assert app.menu_tracking, 'Native menu did not enter tracking state'
                assert not user.IsWindowVisible(app.notice), 'Answer popup covered the native network menu'
                assert getter(owner, -20) & 0x8, 'Native menu owner was not raised topmost'
                assert not user.IsWindowVisible(owner), 'Hidden menu owner became visible'
                menu_checked.append(True)
            except Exception as exc:
                menu_errors.append(str(exc))
            finally:
                user.EndMenu()

        timer_callback = timer_type(inspect_menu)
        show('1. A')
        menu_timer = user.SetTimer(None, 0, 180, timer_callback)
        assert menu_timer, 'Could not arm synthetic menu inspection timer'
        try:
            command = app.track_popup_menu(menu, 0x100 | 0x2, work[2] - 24, work[3] - 24)
        finally:
            user.KillTimer(None, menu_timer)
            menu_timer = None
        assert menu_checked and not menu_errors, 'Native network menu verification failed: ' + '; '.join(menu_errors)
        assert command == 0, 'Synthetic menu unexpectedly selected an action'
        assert not getter(owner, -20) & 0x8, 'Native menu owner remained topmost after tracking'
        assert not user.IsWindowVisible(owner), 'Native menu owner remained visible after tracking'
        assert not app.menu_tracking, 'Native menu tracking state was not restored'
        report.update(native_network_menu_visible=True, native_network_menu_above_fullscreen=True,
                      notice_hidden_during_menu=True, native_menu_owner_restored_hidden=True,
                      native_menu_owner_restored_nontopmost=True)
        report['ok'] = True
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if menu_timer:
            user.KillTimer(None, menu_timer)
        if menu:
            user.DestroyMenu(menu)
        if owner:
            user.DestroyWindow(owner)
        if app is not None:
            if getattr(app, 'notice', None):
                user.DestroyWindow(app.notice)
            if getattr(app, 'notice_font', None):
                app.gdi.DeleteObject(app.notice_font)
        if background:
            user.DestroyWindow(background)
        if previous_foreground:
            user.SetForegroundWindow(previous_foreground)
        if set_dpi is not None and old_dpi:
            set_dpi(old_dpi)
        report['owned_windows_destroyed'] = True
        (destination / 'completion-popup-verification.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['ok']:
        raise RuntimeError('Synthetic completion popup verification failed: ' + report.get('error', 'unknown'))
    return report
