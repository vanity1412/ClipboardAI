"""Packaged Tk smoke check: no tray, clipboard, screenshots or API calls."""
import json
from pathlib import Path
import sys


def run(output):
    report = {'ok': False, 'python': sys.version.split()[0], 'frozen': bool(getattr(sys, 'frozen', False))}
    window = None
    try:
        import tkinter as tk
        from tkinter import ttk
        window = tk.Tk()
        window.withdraw()
        frame = ttk.Frame(window)
        ttk.Label(frame, text='ClipboardAI Tk smoke check').pack()
        ttk.Entry(frame).pack()
        ttk.Combobox(frame, values=('synthetic',)).pack()
        frame.pack()
        window.update_idletasks()
        report.update(ok=True, tcl_version=window.tk.call('info', 'patchlevel'),
                      tk_version=window.tk.call('package', 'require', 'Tk'),
                      tcl_library=window.tk.call('info', 'library'),
                      tk_library=str(window.tk.getvar('tk_library')))
    except Exception as exc:
        # Keep errors diagnostic; no user data is loaded by this smoke check.
        report.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if window is not None:
            window.destroy()
        (Path(output) / 'tk-verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='utf-8')
