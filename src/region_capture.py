"""Frozen foreground pixels, physical-coordinate selection, no disk/clipboard writes."""
import ctypes as C
from ctypes import wintypes as W
from screen_capture import CaptureError, capture_foreground_image, encode_png


def crop_bounds(start, end, bounds):
    left, top, right, bottom = bounds
    x1, x2 = sorted((start[0], end[0]))
    y1, y2 = sorted((start[1], end[1]))
    box = max(left, x1), max(top, y1), min(right, x2), min(bottom, y2)
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        raise CaptureError('Vùng quá nhỏ; kéo chọn lại. Chưa gửi AI.')
    return tuple(v - (left if i % 2 == 0 else top) for i, v in enumerate(box))


def cursor_position(user):
    point = W.POINT()
    if not user.GetCursorPos(C.byref(point)):
        raise CaptureError('Không đọc được vị trí chuột; chọn lại vùng.')
    return point.x, point.y


def select_region(hwnd, cancel, cue='light', on_open=None):
    import tkinter as tk
    user = C.WinDLL('user32', use_last_error=True)
    set_dpi = getattr(user, 'SetThreadDpiAwarenessContext', None)
    if set_dpi:
        set_dpi.argtypes, set_dpi.restype = [C.c_void_p], C.c_void_p
    previous = set_dpi(C.c_void_p(-4)) if set_dpi else None
    root = None
    try:
        if cancel.is_set():
            return None
        image, bounds = capture_foreground_image(hwnd)
        if cancel.is_set():
            return None
        x, y, right, bottom = bounds
        width, height = right - x, bottom - y
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg='white', cursor='crosshair')
        # A virtually transparent input surface catches the drag. A separate
        # color-keyed, click-through layer draws corners at readable opacity.
        root.attributes('-topmost', True, '-alpha', 0.008)
        root.geometry(f'{width}x{height}+0+0')
        marks = tk.Toplevel(root)
        marks.withdraw()
        marks.overrideredirect(True)
        marks.attributes('-topmost', True, '-transparentcolor', '#ff00ff')
        marks.geometry(f'{width}x{height}+0+0')
        canvas = tk.Canvas(marks, bg='#ff00ff', highlightthickness=0, bd=0)
        canvas.pack(fill='both', expand=True)
        user.GetAncestor.argtypes, user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
        user.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        user.GetCursorPos.argtypes = [C.POINTER(W.POINT)]
        user.GetForegroundWindow.restype = W.HWND
        user.SetForegroundWindow.argtypes, user.SetForegroundWindow.restype = [W.HWND], W.BOOL
        get_style = user.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user.GetWindowLongW
        set_style = user.SetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user.SetWindowLongW
        get_style.argtypes, get_style.restype = [W.HWND, C.c_int], C.c_ssize_t
        set_style.argtypes, set_style.restype = [W.HWND, C.c_int, C.c_ssize_t], C.c_ssize_t
        root.update_idletasks()
        marks.update_idletasks()
        input_hwnd = user.GetAncestor(root.winfo_id(), 2)
        marks_hwnd = user.GetAncestor(marks.winfo_id(), 2)
        set_style(marks_hwnd, -20, get_style(marks_hwnd, -20) | 0x20 | 0x08000000 | 0x80)
        # SetWindowPos avoids Tk's interpretation of negative monitor geometry.
        root.deiconify()
        marks.deiconify()
        root.update()
        user.SetWindowPos(input_hwnd, C.c_void_p(-1), x, y, width, height, 0)
        user.SetWindowPos(marks_hwnd, C.c_void_p(-1), x, y, width, height, 0x10)
        root.focus_force()
        start, chosen = [], []
        def position():
            px, py = cursor_position(user)
            return max(x, min(right, px)), max(y, min(bottom, py))
        def press(event):
            start[:] = [position()]
        def motion(event):
            if not start:
                return
            end = position()
            a, b = sorted((start[0][0] - x, end[0] - x))
            c, d = sorted((start[0][1] - y, end[1] - y))
            canvas.delete('all')
            color = '#c2c2c2' if cue == 'light' else '#707070'
            length = min(9, max(1, (b - a) / 2), max(1, (d - c) / 2))
            for px, py, dx, dy in ((a, c, 1, 1), (b - 1, c, -1, 1), (a, d - 1, 1, -1), (b - 1, d - 1, -1, -1)):
                canvas.create_line(px + length * dx, py, px, py, px, py + length * dy, fill=color, width=1)
            if cue == 'clear':
                canvas.create_rectangle(a, c, b - 1, d - 1, outline=color, width=1)
        def release(event):
            if start:
                chosen[:] = [(start[0], position())]
                root.quit()
        def stop(event=None):
            cancel.set()
            root.quit()
        def poll():
            if cancel.is_set():
                root.quit()
            else:
                root.after(25, poll)
        root.bind('<ButtonPress-1>', press)
        root.bind('<B1-Motion>', motion)
        root.bind('<ButtonRelease-1>', release)
        root.bind('<Escape>', stop)
        root.bind('<ButtonPress-3>', stop)
        # Capture keeps release events when the pointer leaves the window.
        root.grab_set_global()
        if on_open:
            on_open(root, input_hwnd, bounds)
        poll()
        root.mainloop()
        root.grab_release()
        restore_focus = user.GetForegroundWindow() in (input_hwnd, marks_hwnd)
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()
        root = None
        if restore_focus:
            user.SetForegroundWindow(hwnd)
        if cancel.is_set() or not chosen:
            return None
        crop = image.crop(crop_bounds(*chosen[0], bounds))
        image.close()
        return encode_png(crop)
    finally:
        if root is not None:
            try:
                root.grab_release()
                for timer in root.tk.call('after', 'info'):
                    root.after_cancel(timer)
                root.destroy()
            except tk.TclError:
                pass
        if previous and set_dpi:
            set_dpi(previous)
