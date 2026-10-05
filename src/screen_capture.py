"""Capture only the visible foreground window, in memory, on explicit F4."""
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO


class CaptureError(RuntimeError):
    """Safe local capture error suitable for the compact status display."""


def capture_foreground_image(hwnd=None):
    from PIL import ImageGrab
    user = C.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = W.HWND
    user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.IsIconic.argtypes = [W.HWND]
    # Win 7/8 do not export this API. The manifest sets process DPI awareness
    # there; on recent Windows temporarily use per-monitor thread coordinates.
    set_dpi = getattr(user, "SetThreadDpiAwarenessContext", None)
    previous = None
    if set_dpi is not None:
        set_dpi.argtypes = [C.c_void_p]
        set_dpi.restype = C.c_void_p
        previous = set_dpi(C.c_void_p(-4))
    try:
        foreground = user.GetForegroundWindow()
        if hwnd is not None and hwnd != foreground:
            raise CaptureError("Cửa sổ đã đổi trước khi chụp; nhấn F4 lại")
        hwnd = hwnd or foreground
        rect = W.RECT()
        if not hwnd or user.IsIconic(hwnd) or not user.GetWindowRect(hwnd, C.byref(rect)):
            raise CaptureError("Không chụp được cửa sổ đang xem hoặc đã thu nhỏ; mở đề rồi F4")
        x, y = user.GetSystemMetrics(76), user.GetSystemMetrics(77)
        right = x + user.GetSystemMetrics(78)
        bottom = y + user.GetSystemMetrics(79)
        box = (max(x, rect.left), max(y, rect.top), min(right, rect.right), min(bottom, rect.bottom))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise CaptureError("Cửa sổ không nằm trên màn hình; mở đề rồi F4")
        image = ImageGrab.grab(bbox=box, all_screens=True)
        if all(high <= 3 for low, high in image.convert('RGB').getextrema()):
            raise CaptureError("Ảnh chụp toàn đen; chưa gửi AI. Cửa sổ có thể được bảo vệ hoặc đang trống; mở đề rồi F4.")
        return image, box
    finally:
        if previous:
            set_dpi(previous)


def encode_png(image):
    if all(high <= 3 for low, high in image.convert('RGB').getextrema()):
        raise CaptureError('Vùng chọn toàn đen; chưa gửi AI. Chọn lại vùng có nội dung.')
    data = BytesIO()
    image.save(data, format='PNG')
    return data.getvalue()


def capture_foreground_png(hwnd=None):
    return encode_png(capture_foreground_image(hwnd)[0])
