"""A click-through agent pointer using the user's current Windows arrow cursor.

This window never moves the system pointer or activates an application.  Its
thread owns every GDI/window resource, so callers can update or close it from
the MCP worker without sharing a window message loop.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import math
import os
import queue
import threading


class NativeArrowOverlay:
    """Display only IDC_ARROW at desktop coordinates; unavailable OSes no-op.

    ``update(x, y)`` and ``hide()`` are asynchronous. ``hide(wait=True)`` waits
    up to 250 ms for the owner thread to hide its window and returns whether
    that happened. ``close()`` is idempotent and joins the owner thread. A
    rendering failure closes the overlay and sets ``error_code`` instead of
    failing an otherwise valid agent action.
    """

    def __init__(self):
        self._commands = queue.SimpleQueue()
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._hwnd = None
        self._thread_id = None
        self.error_code = None
        self._cursor_size = None
        self._hotspot = None
        self._pixels_bgra = None
        if os.name == 'nt':
            try:
                self._thread = threading.Thread(target=self._run,
                    name='ClipboardAI native arrow', daemon=True)
                self._thread.start()
            except Exception:
                self._thread = None
                self.error_code = 'native_cursor_start_failed'
                self._closed.set()
                self._ready.set()
                return
            if not self._ready.wait(3):
                self.error_code = 'native_cursor_start_timeout'
                self.close()
        else:
            self._ready.set()

    @property
    def available(self):
        return bool(self._ready.is_set() and self._hwnd
            and not self.error_code and not self._closed.is_set())

    def update(self, x, y):
        if self._closed.is_set() or self.error_code or os.name != 'nt':
            return False
        if (isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or abs(x) > 2**30 or abs(y) > 2**30
                or not math.isfinite(x) or not math.isfinite(y)):
            return False
        self._commands.put(('update', round(x), round(y)))
        self._wake()
        return True

    def hide(self, wait=False):
        if self._closed.is_set() or os.name != 'nt':
            return True if wait else None
        acknowledged = threading.Event() if wait else None
        self._commands.put(('hide', acknowledged))
        self._wake()
        if acknowledged:
            return acknowledged.wait(.25) or self._closed.is_set()

    def close(self):
        self._closed.set()
        self._commands.put(('close',))
        self._wake()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(3)

    def _wake(self):
        user = getattr(self, '_user', None)
        if user and self._hwnd:
            if not user.PostMessageW(self._hwnd, self._wake_message, 0, 0):
                if self._closed.is_set() and self._thread_id:
                    user.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)

    def _run(self):
        try:
            self._run_windows()
        except Exception:
            # Failures while loading APIs/signatures also release the caller's
            # startup wait; no exception can escape the daemon owner thread.
            self.error_code = self.error_code or 'native_cursor_initialization'
        finally:
            self._closed.set()
            self._ready.set()

    def _run_windows(self):
        user = C.WinDLL('user32', use_last_error=True)
        gdi = C.WinDLL('gdi32', use_last_error=True)
        kernel = C.WinDLL('kernel32', use_last_error=True)
        self._user = user
        self._wake_message = 0x8001
        LRESULT = C.c_ssize_t
        WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

        class WNDCLASSEX(C.Structure):
            _fields_ = [('cbSize', W.UINT), ('style', W.UINT),
                ('lpfnWndProc', WNDPROC), ('cbClsExtra', C.c_int),
                ('cbWndExtra', C.c_int), ('hInstance', W.HINSTANCE),
                ('hIcon', W.HICON), ('hCursor', W.HANDLE),
                ('hbrBackground', W.HBRUSH), ('lpszMenuName', W.LPCWSTR),
                ('lpszClassName', W.LPCWSTR), ('hIconSm', W.HICON)]

        class ICONINFO(C.Structure):
            _fields_ = [('fIcon', W.BOOL), ('xHotspot', W.DWORD),
                ('yHotspot', W.DWORD), ('hbmMask', W.HBITMAP),
                ('hbmColor', W.HBITMAP)]

        class BITMAP(C.Structure):
            _fields_ = [('bmType', W.LONG), ('bmWidth', W.LONG),
                ('bmHeight', W.LONG), ('bmWidthBytes', W.LONG),
                ('bmPlanes', W.WORD), ('bmBitsPixel', W.WORD),
                ('bmBits', C.c_void_p)]

        class BITMAPINFOHEADER(C.Structure):
            _fields_ = [('biSize', W.DWORD), ('biWidth', W.LONG),
                ('biHeight', W.LONG), ('biPlanes', W.WORD),
                ('biBitCount', W.WORD), ('biCompression', W.DWORD),
                ('biSizeImage', W.DWORD), ('biXPelsPerMeter', W.LONG),
                ('biYPelsPerMeter', W.LONG), ('biClrUsed', W.DWORD),
                ('biClrImportant', W.DWORD)]

        class BITMAPINFO(C.Structure):
            _fields_ = [('bmiHeader', BITMAPINFOHEADER),
                ('bmiColors', W.DWORD * 1)]

        class BLENDFUNCTION(C.Structure):
            _fields_ = [('BlendOp', W.BYTE), ('BlendFlags', W.BYTE),
                ('SourceConstantAlpha', W.BYTE), ('AlphaFormat', W.BYTE)]

        # Declare pointer-sized signatures explicitly; default ctypes integers
        # would truncate handles on 64-bit Windows.
        signatures = {
            'GetIconInfo': ([W.HANDLE, C.POINTER(ICONINFO)], W.BOOL),
            'LoadCursorW': ([W.HINSTANCE, C.c_void_p], W.HANDLE),
            'RegisterClassExW': ([C.POINTER(WNDCLASSEX)], W.ATOM),
            'UnregisterClassW': ([W.LPCWSTR, W.HINSTANCE], W.BOOL),
            'CreateWindowExW': ([W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU,
                W.HINSTANCE, C.c_void_p], W.HWND),
            'DefWindowProcW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], LRESULT),
            'DestroyWindow': ([W.HWND], W.BOOL),
            'ShowWindow': ([W.HWND, C.c_int], W.BOOL),
            'SetWindowPos': ([W.HWND, W.HWND, C.c_int, C.c_int,
                C.c_int, C.c_int, W.UINT], W.BOOL),
            'UpdateLayeredWindow': ([W.HWND, W.HDC, C.POINTER(W.POINT),
                C.POINTER(W.SIZE), W.HDC, C.POINTER(W.POINT), W.DWORD,
                C.POINTER(BLENDFUNCTION), W.DWORD], W.BOOL),
            'GetDC': ([W.HWND], W.HDC),
            'ReleaseDC': ([W.HWND, W.HDC], C.c_int),
            'DrawIconEx': ([W.HDC, C.c_int, C.c_int, W.HANDLE,
                C.c_int, C.c_int, W.UINT, W.HBRUSH, W.UINT], W.BOOL),
            'PostMessageW': ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            'PostThreadMessageW': ([W.DWORD, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            'GetMessageW': ([C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL),
            'TranslateMessage': ([C.POINTER(W.MSG)], W.BOOL),
            'DispatchMessageW': ([C.POINTER(W.MSG)], LRESULT),
            'PostQuitMessage': ([C.c_int], None),
        }
        for name, (args, result) in signatures.items():
            method = getattr(user, name)
            method.argtypes, method.restype = args, result
        for name, args, result in (
            ('CreateCompatibleDC', [W.HDC], W.HDC),
            ('DeleteDC', [W.HDC], W.BOOL),
            ('DeleteObject', [W.HGDIOBJ], W.BOOL),
            ('SelectObject', [W.HDC, W.HGDIOBJ], W.HGDIOBJ),
            ('GetObjectW', [W.HGDIOBJ, C.c_int, C.c_void_p], C.c_int),
            ('GetDIBits', [W.HDC, W.HBITMAP, W.UINT, W.UINT,
                C.c_void_p, C.POINTER(BITMAPINFO), W.UINT], C.c_int),
            ('CreateDIBSection', [W.HDC, C.POINTER(BITMAPINFO), W.UINT,
                C.POINTER(C.c_void_p), W.HANDLE, W.DWORD], W.HBITMAP),
            ('GdiFlush', [], W.BOOL)):
            method = getattr(gdi, name)
            method.argtypes, method.restype = args, result
        kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        kernel.GetModuleHandleW.restype = W.HMODULE
        kernel.GetCurrentThreadId.argtypes = []
        kernel.GetCurrentThreadId.restype = W.DWORD
        self._thread_id = kernel.GetCurrentThreadId()
        module = kernel.GetModuleHandleW(None)
        class_name = 'ClipboardAI.NativeArrow.%s.%s' % (os.getpid(), id(self))
        registered = False
        screen_dc = memory_dc = bitmap = old_bitmap = None
        icon = ICONINFO()
        dpi_previous = None

        def check(value, code):
            if not value:
                self.error_code = 'native_cursor_' + code
                raise OSError(C.get_last_error(), code)
            return value

        def info_for(width, height):
            info = BITMAPINFO()
            info.bmiHeader = BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER),
                width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
            return info

        def drain():
            latest = None
            while True:
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    break
                if command[0] == 'hide':
                    # Hide is a barrier, rather than an update that can be
                    # discarded by coalescing. Acknowledge every waiting caller
                    # after ShowWindow has completed on the owning thread.
                    latest = None
                    user.ShowWindow(self._hwnd, 0)
                    if command[1]:
                        command[1].set()
                else:
                    latest = command
            if self._closed.is_set() or latest and latest[0] == 'close':
                user.PostQuitMessage(0)
            elif latest:
                x, y = latest[1:]
                destination = W.POINT(x - self._hotspot[0], y - self._hotspot[1])
                size = W.SIZE(*self._cursor_size)
                source = W.POINT(0, 0)
                blend = BLENDFUNCTION(0, 0, 255, 1)
                check(user.UpdateLayeredWindow(self._hwnd, screen_dc,
                    C.byref(destination), C.byref(size), memory_dc,
                    C.byref(source), 0, C.byref(blend), 2), 'layered_update')
                check(user.SetWindowPos(self._hwnd, W.HWND(-1), 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0010 | 0x0040), 'show')

        @WNDPROC
        def window_proc(hwnd, message, wparam, lparam):
            try:
                if message == self._wake_message:
                    drain()
                    return 0
                if message == 0x0084:  # WM_NCHITTEST: pass clicks through.
                    return -1
                if message == 0x0021:  # WM_MOUSEACTIVATE: never activate.
                    return 3
                if message == 0x0014:  # No background or border to draw.
                    return 1
            except Exception:
                self.error_code = self.error_code or 'native_cursor_message'
                user.ShowWindow(hwnd, 0)
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(hwnd, message, wparam, lparam)

        self._window_proc = window_proc  # Keep the native callback alive.
        try:
            if hasattr(user, 'SetThreadDpiAwarenessContext'):
                user.SetThreadDpiAwarenessContext.argtypes = [C.c_void_p]
                user.SetThreadDpiAwarenessContext.restype = C.c_void_p
                dpi_previous = user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
            if self._closed.is_set():
                return
            cursor = check(user.LoadCursorW(None, C.c_void_p(32512)), 'load_arrow')
            check(user.GetIconInfo(cursor, C.byref(icon)), 'icon_info')
            source_bitmap = BITMAP()
            check(gdi.GetObjectW(icon.hbmColor or icon.hbmMask,
                C.sizeof(source_bitmap), C.byref(source_bitmap)), 'cursor_size')
            width = source_bitmap.bmWidth
            height = source_bitmap.bmHeight if icon.hbmColor else source_bitmap.bmHeight // 2
            if not 1 <= width <= 512 or not 1 <= height <= 512:
                self.error_code = 'native_cursor_invalid_size'
                raise ValueError('cursor size')
            self._cursor_size = (width, height)
            self._hotspot = (icon.xHotspot, icon.yHotspot)
            screen_dc = check(user.GetDC(None), 'screen_dc')
            memory_dc = check(gdi.CreateCompatibleDC(screen_dc), 'memory_dc')
            bits = C.c_void_p()
            info = info_for(width, height)
            bitmap = check(gdi.CreateDIBSection(screen_dc, C.byref(info),
                0, C.byref(bits), None, 0), 'dib')
            old_bitmap = check(gdi.SelectObject(memory_dc, bitmap), 'select_dib')
            byte_count = width * height * 4
            C.memset(bits, 0, byte_count)
            check(user.DrawIconEx(memory_dc, 0, 0, cursor, width, height,
                0, None, 3), 'draw_arrow')
            check(gdi.GdiFlush(), 'flush_arrow')
            pixels = bytearray(C.string_at(bits, byte_count))
            if not any(pixels[3::4]):
                # Legacy system cursor themes use an AND mask instead of alpha.
                mask_bitmap = BITMAP()
                check(gdi.GetObjectW(icon.hbmMask, C.sizeof(mask_bitmap),
                    C.byref(mask_bitmap)), 'mask_size')
                mask_height = mask_bitmap.bmHeight
                if mask_bitmap.bmWidth != width or mask_height not in (height, height * 2):
                    self.error_code = 'native_cursor_invalid_mask'
                    raise ValueError('cursor mask')
                mask_info = info_for(width, mask_height)
                mask = (C.c_ubyte * (width * mask_height * 4))()
                check(gdi.GetDIBits(screen_dc, icon.hbmMask, 0, mask_height,
                    mask, C.byref(mask_info), 0), 'cursor_mask')
                for offset in range(0, byte_count, 4):
                    pixels[offset + 3] = 255 if mask[offset] < 128 else 0
            # UpdateLayeredWindow expects premultiplied BGRA. DrawIconEx already
            # supplies it for alpha cursors; normalize legacy transparent pixels.
            for offset in range(0, byte_count, 4):
                alpha = pixels[offset + 3]
                if not alpha:
                    pixels[offset:offset + 3] = b'\0\0\0'
                elif any(value > alpha for value in pixels[offset:offset + 3]):
                    for channel in range(3):
                        pixels[offset + channel] = round(pixels[offset + channel] * alpha / 255)
            C.memmove(bits, bytes(pixels), byte_count)
            self._pixels_bgra = bytes(pixels)
            window_class = WNDCLASSEX()
            window_class.cbSize = C.sizeof(WNDCLASSEX)
            window_class.lpfnWndProc = window_proc
            window_class.hInstance = module
            window_class.lpszClassName = class_name
            check(user.RegisterClassExW(C.byref(window_class)), 'register_window')
            registered = True
            self._hwnd = check(user.CreateWindowExW(
                0x00080000 | 0x00000020 | 0x00000080 | 0x08000000,
                class_name, '', 0x80000000, 0, 0, width, height,
                None, None, module, None), 'create_window')
            self._ready.set()
            drain()
            message = W.MSG()
            while not self._closed.is_set():
                status = user.GetMessageW(C.byref(message), None, 0, 0)
                if status == 0:
                    break
                if status == -1:
                    check(False, 'message_loop')
                user.TranslateMessage(C.byref(message))
                user.DispatchMessageW(C.byref(message))
        except Exception:
            self.error_code = self.error_code or 'native_cursor_initialization'
        finally:
            self._closed.set()
            self._ready.set()
            if self._hwnd:
                user.DestroyWindow(self._hwnd)
                self._hwnd = None
            if registered:
                user.UnregisterClassW(class_name, module)
            if old_bitmap and memory_dc:
                gdi.SelectObject(memory_dc, old_bitmap)
            if bitmap:
                gdi.DeleteObject(bitmap)
            if memory_dc:
                gdi.DeleteDC(memory_dc)
            if screen_dc:
                user.ReleaseDC(None, screen_dc)
            if icon.hbmColor:
                gdi.DeleteObject(icon.hbmColor)
            if icon.hbmMask:
                gdi.DeleteObject(icon.hbmMask)
            if dpi_previous:
                user.SetThreadDpiAwarenessContext(dpi_previous)
