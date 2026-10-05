"""Owned synthetic-window UI checks; no live API, clipboard or network writes."""
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO
import json
from pathlib import Path
import threading
from unittest.mock import patch


def run(output):
    from PIL import Image, ImageDraw
    import region_capture as region
    import tkinter as tk
    from windows_native import setup_winapi
    user, kernel, shell = setup_winapi()
    user.SetThreadDpiAwarenessContext.argtypes = [C.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = C.c_void_p
    old_dpi = user.SetThreadDpiAwarenessContext(C.c_void_p(-4))
    previous = user.GetForegroundWindow()
    bounds = (80, 80, 560, 380)
    background = tk.Tk()
    background.overrideredirect(True)
    background.attributes('-topmost', True)
    background.geometry('480x300+80+80')
    tk.Label(background, text='SYNTHETIC REGION TEST\nSelect a question or code.\nNo private content.\n1. A   2. B   3. C',
             bg='white', fg='black', anchor='nw', justify='left').pack(fill='both', expand=True)
    background.update()
    user.GetAncestor.argtypes, user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
    window = user.GetAncestor(background.winfo_id(), 2)
    actual = W.RECT()
    user.GetWindowRect(window, C.byref(actual))
    bounds = (actual.left, actual.top, actual.right, actual.bottom)
    sample = Image.new('RGB', (bounds[2] - bounds[0], bounds[3] - bounds[1]), 'white')
    ImageDraw.Draw(sample).text((40, 50), 'SYNTHETIC QUESTION\n1. A   2. B   3. C', fill='black')
    report = {'ok': False, 'live_api_calls': 0, 'clipboard_writes': 0, 'physical_network_changes': 0,
              'native_overlay_geometry_tested': True, 'screen_pixels_tested': False, 'synthetic_image_only': True}
    try:
        for action in ('light', 'clear', 'escape', 'cancel'):
            user.SetForegroundWindow(window)
            cancel = threading.Event()
            cursor = [None]
            def opened(root, input_hwnd, box):
                rect = W.RECT()
                user.GetWindowRect(input_hwnd, C.byref(rect))
                assert (rect.left, rect.top, rect.right, rect.bottom) == box, 'Overlay physical coordinates differ from capture'
                cursor[0] = (box[0] + 35, box[1] + 40)
                def simulate():
                    if action == 'escape':
                        root.event_generate('<Escape>')
                        return
                    if action == 'cancel':
                        cancel.set()
                        return
                    root.event_generate('<ButtonPress-1>', x=35, y=40)
                    cursor[0] = (box[0] + 255, box[1] + 180)
                    root.event_generate('<Motion>', x=255, y=180, state=0x100)
                    root.update_idletasks()
                    marks = root.winfo_children()[0]
                    canvas = marks.winfo_children()[0]
                    assert len(canvas.find_all()) == (4 if action == 'light' else 5)
                    root.event_generate('<ButtonRelease-1>', x=255, y=180)
                root.after(120, simulate)
                root.after(2500, cancel.set)  # Fail boundedly if an event does not arrive.
            with patch.object(region, 'cursor_position', side_effect=lambda _: cursor[0]), \
                    patch.object(region, 'capture_foreground_image', side_effect=lambda _: (sample.copy(), bounds)):
                png = region.select_region(window, cancel, 'clear' if action == 'clear' else 'light', opened)
            if action in ('escape', 'cancel'):
                assert png is None and cancel.is_set()
            else:
                assert not cancel.is_set() and Image.open(BytesIO(png)).size == (220, 140)
                cropped = Image.open(BytesIO(png)).convert('RGB')
                expected = sample.crop((35, 40, 255, 180))
                assert cropped.tobytes() == expected.tobytes(), 'Selected pixels differ from original snapshot'
            report[action] = True
        report['ok'] = True
        verify_hotkey_editor(report)
        Path(output, 'region-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report
    finally:
        background.destroy()
        if previous:
            user.SetForegroundWindow(previous)
        if old_dpi:
            user.SetThreadDpiAwarenessContext(old_dpi)


def verify_hotkey_editor(report):
    import tkinter as tk
    import queue
    from hotkey_settings import open_editor
    actual_factory = tk.Tk
    messages = queue.Queue()
    completed, errors = [], []
    def factory():
        window = actual_factory()
        def check():
            try:
                frame = window.winfo_children()[0]
                fields = [w for w in frame.winfo_children() if w.winfo_class() == 'TEntry']
                buttons = {w.cget('text'): w for w in frame.winfo_children() if w.winfo_class() == 'TButton'}
                checks = [w for w in frame.winfo_children() if w.winfo_class() == 'TCheckbutton']
                assert len(fields) == len(checks) == 9
                fields[0].event_generate('<KeyPress-F5>')
                # Typing a manual combination remains available for Win keys.
                fields[0].delete(0, 'end')
                fields[0].insert(0, 'Ctrl+Alt+Q')
                checks[0].invoke()
                assert 'disabled' in fields[0].state()
                buttons['Lưu'].invoke()
                result = messages.get_nowait()
                assert result[0] == 'hotkey_save' and result[1]['201'] == 'Ctrl+Alt+Q'
                assert result[3] == ['201']
                result[2].put('Đã lưu mẫu; đóng cửa sổ để bật phím')
                def reset():
                    try:
                        buttons['Khôi phục mặc định'].invoke()
                        assert fields[0].get() == 'F8'
                        assert 'disabled' not in fields[0].state()
                        completed.append(True)
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                    finally:
                        window.quit()
                        window.destroy()
                window.after(120, reset)
            except Exception as exc:
                errors.append(type(exc).__name__)
                window.quit()
                window.destroy()
        window.after(120, check)
        window.after(2500, window.destroy)
        return window
    with patch.object(tk, 'Tk', side_effect=factory):
        open_editor({}, messages)
    assert completed and not errors, 'Hotkey editor synthetic UI check failed: ' + str(errors)
    assert messages.get_nowait()[0] == 'hotkey_closed'
    report['hotkey_editor_save_reset_close'] = True
