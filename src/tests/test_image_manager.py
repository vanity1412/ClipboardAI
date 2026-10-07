"""Hidden Tk image manager with generated pixels, never screen/clipboard/API."""
from io import BytesIO
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import image_manager
from session_images import SessionImages


class ManagerCleanupTests(unittest.TestCase):
    def test_tk_failure_still_reports_closed(self):
        import tkinter as tk
        results = queue.Queue()
        with patch('tkinter.Tk', side_effect=tk.TclError('synthetic')):
            with self.assertRaises(tk.TclError):
                image_manager.open_manager(Mock(), 'a', results, threading.Event())
        self.assertEqual(results.get_nowait(), ('images_closed',))


class HiddenImageManagerTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.tk = tk
        try:
            root = tk.Tk(); root.withdraw(); root.destroy()
        except tk.TclError as exc:
            self.skipTest('Tk runtime unavailable: ' + str(exc))

    def test_preview_delete_and_restart_preserve_only_other_session(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'images.sqlite3'
            cache = SessionImages(path)
            with Image.new('RGB', (100, 60), 'blue') as photo:
                data = BytesIO(); photo.save(data, format='PNG'); png = data.getvalue()
            cache.add('a', png); cache.add('b', png)
            original, errors, results = self.tk.Tk, [], queue.Queue()
            def descendants(root):
                return [child for widget in root.winfo_children() for child in [widget, *descendants(widget)]]
            def factory():
                root = original(); root.withdraw()
                def exercise():
                    try:
                        items = descendants(root)
                        preview = next(w for w in items if w.winfo_class() == 'TLabel' and w.cget('image'))
                        self.assertTrue(preview.cget('image'))
                        delete = next(w for w in items if w.winfo_class() == 'TButton' and w.cget('text') == 'Xóa ảnh đang chọn')
                        delete.invoke()
                        self.assertEqual(cache.get('a'), [])
                        self.assertEqual(cache.get('b'), [png])
                        root.destroy()
                    except BaseException as exc:
                        errors.append(exc); root.destroy()
                root.after(80, exercise)
                root.after(3000, root.destroy)
                return root
            with patch('tkinter.Tk', factory), patch('tkinter._default_root', object()), \
                    patch('tkinter.messagebox.askyesno', return_value=True):
                image_manager.open_manager(cache, 'a', results, threading.Event())
            if errors:
                raise errors[0]
            self.assertEqual(SessionImages(path).get('a'), [])
            self.assertEqual(SessionImages(path).get('b'), [png])
            self.assertEqual(results.get_nowait(), ('images_changed', 'a'))
            self.assertEqual(results.get_nowait(), ('images_closed',))

    def test_exit_cancel_closes_manager(self):
        original, results, cancel = self.tk.Tk, queue.Queue(), threading.Event()
        def factory():
            root = original(); root.withdraw(); root.after(80, cancel.set)
            return root
        with patch('tkinter.Tk', factory):
            image_manager.open_manager(SessionImages(), 'empty', results, cancel)
        self.assertEqual(results.get_nowait(), ('images_closed',))


if __name__ == '__main__':
    unittest.main()
