"""Shared compact panel placement inside the monitor's usable work area."""
import ctypes
from ctypes import wintypes
import os

DEFAULT_WIDTH = 420
DEFAULT_HEIGHT = 560


def panel_bounds(settings, work, outer_size=None):
    left, top, right, bottom = work
    available_width, available_height = max(1, right-left), max(1, bottom-top)
    width = min(int(settings.get('PANEL_WIDTH', DEFAULT_WIDTH)), available_width)
    height = min(int(settings.get('PANEL_HEIGHT', DEFAULT_HEIGHT)), available_height)
    if outer_size is not None:
        width, height = min(outer_size[0], available_width), min(outer_size[1], available_height)
    margin = min(12, max(0, (available_width-width)//2), max(0, (available_height-height)//2))
    if settings.get('PANEL_POSITION', 'bottom_right') == 'center':
        x, y = left + (available_width-width)//2, top + (available_height-height)//2
    else:
        x, y = right-width-margin, bottom-height-margin
    return x, y, width, height


def work_area(fallback=(0, 0, 1920, 1040)):
    if os.name != 'nt':
        return fallback
    try:
        user = ctypes.WinDLL('user32', use_last_error=True)
        class Info(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        point = wintypes.POINT()
        user.GetCursorPos(ctypes.byref(point))
        user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user.MonitorFromPoint.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Info)]
        info = Info(size=ctypes.sizeof(Info))
        if user.GetMonitorInfoW(user.MonitorFromPoint(point, 2), ctypes.byref(info)):
            rect = info.work
            return rect.left, rect.top, rect.right, rect.bottom
    except (OSError, AttributeError):
        pass
    return fallback


def place_native(user, hwnd, settings):
    x, y, width, height = panel_bounds(settings, work_area())
    user.SetWindowPos(hwnd, None, x, y, width, height, 0x14)


def place_tk(root, settings=None):
    settings = settings or {}
    work = work_area((0, 0, root.winfo_screenwidth(), root.winfo_screenheight()))
    x, y, width, height = panel_bounds(settings, work)
    root.minsize(min(360, width), min(480, height))
    root.geometry(f'{width}x{height}{x:+d}{y:+d}')
    root.resizable(True, True)
    root.update_idletasks()
    if os.name == 'nt':
        # Set exact coordinates for monitors left of the primary display;
        # Tk interprets negative geometry coordinates relative to screen edges.
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        hwnd = user.GetAncestor(root.winfo_id(), 2)
        rect = wintypes.RECT()
        if user.GetWindowRect(hwnd, ctypes.byref(rect)):
            x, y, _, _ = panel_bounds(settings, work, (rect.right-rect.left, rect.bottom-rect.top))
            user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x15)
