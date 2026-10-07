"""Packaged Tk smoke check: no tray, clipboard, screenshots or API calls."""
import json
from pathlib import Path
import sys
import tempfile
from io import BytesIO


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
        # Check packaged SQLite/Pillow and storage with synthetic data only.
        from session_images import SessionImages
        from session_state import Session
        from PIL import Image, ImageTk
        with tempfile.TemporaryDirectory(prefix='ClipboardAI_StorageVerify_') as folder:
            image_path = Path(folder) / 'images.sqlite3'
            text_path = Path(folder) / 'session.json'
            session = Session(text_path)
            session.problem = 'Synthetic question'
            saved = session.commit_async([dict(role='user', content='Synthetic question'),
                dict(role='assistant', content='Synthetic answer')], 'Synthetic answer')
            assert saved.result(5)
            session.close()
            restored = Session(text_path)
            assert restored.last_answer == 'Synthetic answer'
            archive = SessionImages(image_path)
            with Image.new('RGB', (100, 60), 'blue') as image:
                encoded = BytesIO(); image.save(encoded, format='PNG')
                photo = ImageTk.PhotoImage(image, master=window)
                assert photo.width() == 100 and photo.height() == 60
            archive.add(restored.active_id, encoded.getvalue())
            reopened = SessionImages(image_path)
            assert reopened.get(restored.active_id) == [encoded.getvalue()]
            reopened.clear(restored.active_id)
            assert SessionImages(image_path).get(restored.active_id) == []
            report.update(history_storage=True, image_storage=True, image_preview=True)
            from activity_stats import ActivityStats
            from conversation_tools import backup_archive, search_records
            from image_redaction import redact_png
            import threading
            import queue
            from tools_panel import open_tools
            ram = Session(None)
            assert ram.new_problem('Synthetic private question')
            assert ram.path is None and ram.save_async().result()
            assert ram.rename(ram.active_id, 'Synthetic title')
            assert search_records(ram.archive_snapshot(), 'Synthetic title')
            backup_archive(restored.archive_snapshot(), reopened, Path(folder) / 'synthetic.zip')
            assert redact_png(encoded.getvalue(), [(1, 1, 20, 20)])
            cancelled = threading.Event(); cancelled.set()
            events = queue.Queue()
            open_tools(dict(archive=ram.archive_snapshot(), stats=ActivityStats().snapshot(),
                private=True, mask=True, profiles=[], model='synthetic', proxies={}), SessionImages(), events, cancelled)
            assert events.get_nowait() == ('tools_closed',)
            from image_manager import open_manager
            open_manager(SessionImages(), ram.active_id, events, cancelled, conversation=ram._snapshot())
            assert events.get_nowait() == ('images_closed',)
            report.update(private_session=True, management_panel=True, backup=True, redaction=True)
            report['image_chat_panel'] = True
    except Exception as exc:
        # Keep errors diagnostic; no user data is loaded by this smoke check.
        report.update(ok=False, error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if window is not None:
            window.destroy()
        (Path(output) / 'tk-verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='utf-8')
