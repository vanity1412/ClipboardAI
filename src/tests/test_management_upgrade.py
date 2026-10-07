import json
from io import BytesIO
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from activity_stats import ActivityStats, safe_proxies
from conversation_tools import search_records, export_record, backup_archive
from image_redaction import redact_png
from session_state import Session
from session_images import SessionImages
from api_zoo import ZooRouter
import windows_native as native


class ManagementTests(unittest.TestCase):
    def test_private_session_never_writes_and_supports_transitions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.json'
            original = Session(path)
            original.commit([dict(role='user', content='durable')], 'answer')
            before = path.read_bytes()
            private = Session(None)
            self.assertTrue(private.new_problem('secret'))
            self.assertTrue(private.commit_async([dict(role='user', content='RAM secret')], 'RAM answer').result())
            ident = private.active_id
            self.assertTrue(private.new_problem('second'))
            self.assertTrue(private.select(ident))
            self.assertEqual(private.last_answer, 'RAM answer')
            self.assertTrue(private.reset())
            self.assertTrue(private.close())
            self.assertEqual(path.read_bytes(), before)

    def test_rename_nonactive_search_export_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder) / 'session.json')
            session.new_problem('old question')
            session.commit([dict(role='assistant', content='needle in answer')], 'needle in answer')
            first = session.active_id
            session.new_problem('new question')
            self.assertTrue(session.rename(first, 'Custom title'))
            restored = Session(session.path)
            archive = restored.archive_snapshot()
            record = search_records(archive, 'NEEDLE')[0]
            self.assertEqual(record['title'], 'Custom title')
            self.assertEqual(next(e for e in restored.entries() if e['id'] == first)['title'], 'Custom title')
            export_record(record, Path(folder) / 'export.json')
            self.assertEqual(json.loads((Path(folder) / 'export.json').read_text(encoding='utf-8'))['title'], 'Custom title')

    def test_backup_restores_images_and_history(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = Session(root / 'session.json')
            session.new_problem('backup question')
            images = SessionImages(root / 'session-images.sqlite3')
            output = BytesIO()
            Image.new('RGB', (10, 10), 'blue').save(output, format='PNG')
            png = output.getvalue()
            images.add(session.active_id, png)
            backup_archive(session.archive_snapshot(), images, root / 'backup.zip')
            restore = root / 'restored'
            with zipfile.ZipFile(root / 'backup.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'session.json', 'session-images.sqlite3', 'HUONG_DAN.txt'})
                archive.extractall(restore)
            recovered = Session(restore / 'session.json')
            self.assertEqual(recovered.problem, 'backup question')
            self.assertEqual(SessionImages(restore / 'session-images.sqlite3').get(recovered.active_id), [png])

    def test_redaction_changes_only_requested_pixels(self):
        from PIL import Image
        output = BytesIO()
        Image.new('RGB', (20, 20), 'white').save(output, format='PNG')
        png = output.getvalue()
        with Image.open(BytesIO(redact_png(png, [(9, 9, 2, 2)]))) as image:
            self.assertEqual(image.getpixel((5, 5)), (0, 0, 0))
            self.assertEqual(image.getpixel((15, 15)), (255, 255, 255))
        with Image.open(BytesIO(png)) as original:
            self.assertEqual(original.getpixel((5, 5)), (255, 255, 255))

    def test_counts_failed_fallback_summary_and_unknown_without_secret(self):
        stats = ActivityStats()
        client = native.AIClient({})
        client.activity = stats
        client.activity_phase = 'Tóm tắt'
        body = {'model': 'model'}
        with patch.object(client, '_post', side_effect=[RuntimeError('secret-key'),
                {'usage': {'input_tokens': 12, 'output_tokens': 3}}]):
            with self.assertRaises(RuntimeError):
                client.post('https://provider.test/v1', body, 30)
            client.post('https://provider.test/v1', body, 30)
        result = stats.snapshot()
        self.assertEqual((result['calls'], result['input'], result['output'], result['unknown']), (2, 12, 3, 1))
        self.assertEqual(result['rows'][0]['phase'], 'Tóm tắt')
        self.assertNotIn('secret-key', str(result))
        with patch('activity_stats.getproxies', return_value={'https': 'http://user:password@proxy.test:8080', 'no': 'secret'}):
            self.assertEqual(safe_proxies(), {'https': 'proxy.test:8080'})

    def test_native_private_toggle_restores_durable_and_removes_cached_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            app = native.WindowsApp.__new__(native.WindowsApp)
            app.busy = False
            app.private_mode = False
            app.session = Session(Path(folder) / 'session.json')
            app.session.commit([dict(role='user', content='normal')], 'normal answer')
            durable = app.session
            before = durable.path.read_bytes()
            app.images = SessionImages(Path(folder) / 'images.sqlite3')
            app.current_id = 1
            app.set_text = Mock()
            app.change_privacy(True)
            self.assertTrue(app.private_mode)
            self.assertFalse(app.images.persistent)
            self.assertFalse(app.session.auto_copy)
            app.session.commit([dict(role='user', content='sensitive')], 'private answer')
            previous_ram = app.session
            app.change_privacy(True, clear=True)
            self.assertEqual(app.session.messages, [])
            self.assertEqual(previous_ram.messages, [])
            self.assertEqual(previous_ram.last_answer, '')
            app.change_privacy(False)
            self.assertIs(app.session, durable)
            self.assertEqual(durable.path.read_bytes(), before)
            self.assertEqual(app.last_completed_answer, '')

    def test_explicit_provider_retry_only_clears_selected_credential(self):
        router = ZooRouter()
        p = dict(id='one', provider='compatible', base_url='https://a', api_key='key')
        credential = ('key', 'https://a', 'key', '')
        router.cooldowns[credential] = float('inf')
        router.cooldowns[('other',)] = float('inf')
        self.assertIn('khóa', router.profile_state(p))
        router.retry_manual(p)
        self.assertEqual(router.profile_state(p), 'Sẵn sàng')
        self.assertIn(('other',), router.cooldowns)


class ToolsPanelTests(unittest.TestCase):
    def test_hidden_panel_search_and_private_action(self):
        import tkinter as tk
        from tools_panel import open_tools
        try:
            probe = tk.Tk(); probe.withdraw(); probe.destroy()
        except tk.TclError:
            self.skipTest('Tk unavailable')
        original, results, errors = tk.Tk, queue.Queue(), []
        session = Session(None)
        session.new_problem('synthetic question')
        def descendants(root):
            return [child for widget in root.winfo_children() for child in [widget, *descendants(widget)]]
        def factory():
            root = original(); root.withdraw()
            def exercise():
                try:
                    widgets = descendants(root)
                    listing = next(w for w in widgets if w.winfo_class() == 'Listbox')
                    self.assertEqual(listing.size(), 1)
                    button = next(w for w in widgets if w.winfo_class() == 'TButton' and w.cget('text').startswith('Bật riêng tư'))
                    button.invoke()
                except BaseException as exc:
                    errors.append(exc)
                    root.quit()
            root.after(80, exercise)
            root.after(3000, root.quit)
            return root
        snapshot = dict(archive=session.archive_snapshot(), stats=ActivityStats().snapshot(),
                        private=False, mask=False, profiles=[], model='synthetic', proxies={})
        with patch('tkinter.Tk', factory), patch('tkinter._default_root', object()):
            open_tools(snapshot, SessionImages(), results, threading.Event())
        if errors:
            raise errors[0]
        self.assertEqual(results.get_nowait(), ('tools_action', 'privacy', True))
        self.assertEqual(results.get_nowait(), ('tools_closed',))

    def test_hidden_mask_review_cancel_never_returns_image(self):
        import tkinter as tk
        from image_redaction import review_png
        from PIL import Image
        try:
            probe = tk.Tk(); probe.withdraw(); probe.destroy()
        except tk.TclError:
            self.skipTest('Tk unavailable')
        original = tk.Tk
        def factory():
            root = original(); root.withdraw()
            root.after(60, root.quit)
            return root
        encoded = BytesIO()
        Image.new('RGB', (10, 10), 'white').save(encoded, format='PNG')
        with patch('tkinter.Tk', factory), patch('tkinter._default_root', object()):
            with self.assertRaises(InterruptedError):
                review_png(encoded.getvalue(), threading.Event())

    def test_setup_failure_reports_closed(self):
        import tkinter as tk
        from tools_panel import open_tools
        results = queue.Queue()
        with patch('tkinter.Tk', side_effect=tk.TclError('synthetic')):
            with self.assertRaises(tk.TclError):
                open_tools({}, Mock(), results, threading.Event())
        self.assertEqual(results.get_nowait(), ('tools_closed',))
