"""Bootstrap failure/cancellation/atomic install regressions; no network/login."""
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

import codex_setup as setup


class CodexSetupTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.deadline = time.monotonic() + 30

    def test_existing_cli_does_not_download_or_probe(self):
        with patch.object(setup, 'find_existing', return_value='installed.exe'), patch.object(setup, '_download') as download:
            self.assertEqual(setup.ensure_codex(), 'installed.exe')
            download.assert_not_called()

    def test_cancel_before_setup_does_not_touch_network(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(setup, '_download') as download:
            with self.assertRaises(InterruptedError):
                setup.ensure_codex(cancel=cancel)
            download.assert_not_called()

    def test_expired_deadline_does_not_even_detect_cli(self):
        with patch.object(setup, 'find_existing') as detect:
            with self.assertRaises(TimeoutError):
                setup.ensure_codex(deadline=0)
            detect.assert_not_called()

    def test_checksum_failure_never_extracts_or_publishes(self):
        with patch.object(setup, 'find_existing', return_value=None), patch.object(setup, '_download', side_effect=RuntimeError('checksum')), patch.object(setup, '_extract') as extract:
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                setup.ensure_codex(root=self.root)
            extract.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_download_validates_hash(self):
        response = io.BytesIO(b'official archive')
        with patch.object(setup, 'urlopen', return_value=response), patch.object(setup, 'SHA256', hashlib.sha256(b'official archive').hexdigest()):
            setup._download(self.root / 'archive', None, self.deadline)
        self.assertEqual((self.root / 'archive').read_bytes(), b'official archive')

    def test_corrupt_download_rejected(self):
        with patch.object(setup, 'urlopen', return_value=io.BytesIO(b'corrupt')):
            with self.assertRaisesRegex(RuntimeError, 'Checksum'):
                setup._download(self.root / 'archive', None, self.deadline)

    def test_download_size_limit(self):
        with patch.object(setup, 'urlopen', return_value=io.BytesIO(b'oversize')), patch.object(setup, 'MAX_DOWNLOAD', 2):
            with self.assertRaisesRegex(RuntimeError, 'dung lượng'):
                setup._download(self.root / 'archive', None, self.deadline)

    def archive(self, names):
        archive = self.root / 'test.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for name in names:
                output.writestr(name, b'fixture')
        return archive

    def test_extract_preserves_companions_and_renames_cli(self):
        archive = self.archive([setup.ASSET.removesuffix('.zip'), 'codex-resources/helper.exe'])
        target = self.root / 'runtime'
        target.mkdir()
        setup._extract(archive, target, None, self.deadline)
        self.assertTrue((target / 'codex.exe').is_file())
        self.assertTrue((target / 'codex-resources/helper.exe').is_file())

    def test_extract_rejects_escape_paths(self):
        for name in ('../outside.exe', '/outside.exe', 'C:/outside.exe'):
            with self.subTest(name=name):
                archive = self.archive([name])
                with self.assertRaisesRegex(RuntimeError, 'Đường dẫn'):
                    setup._extract(archive, self.root / 'runtime', None, self.deadline)

    def test_extract_rejects_raw_windows_separator(self):
        archive = Mock()
        archive.infolist.return_value = [Mock(filename='folder\\outside.exe', file_size=1)]
        with patch.object(setup.zipfile, 'ZipFile') as opened:
            opened.return_value.__enter__.return_value = archive
            with self.assertRaisesRegex(RuntimeError, 'Đường dẫn'):
                setup._extract(self.root / 'test.zip', self.root / 'runtime', None, self.deadline)
        archive.open.assert_not_called()

    def test_probe_failure_does_not_leave_installed_executable(self):
        def extract(_, destination, *args):
            (destination / 'codex.exe').write_bytes(b'fixture')
        with patch.object(setup, 'find_existing', return_value=None), patch.object(setup, '_download'), patch.object(setup, '_extract', side_effect=extract), patch.object(setup, '_probe', side_effect=RuntimeError('bad binary')):
            with self.assertRaisesRegex(RuntimeError, 'bad binary'):
                setup.ensure_codex(root=self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_success_publishes_complete_runtime_after_probe(self):
        def extract(_, destination, *args):
            (destination / 'codex.exe').write_bytes(b'fixture')
        with patch.object(setup, 'find_existing', return_value=None), patch.object(setup, '_download'), patch.object(setup, '_extract', side_effect=extract), patch.object(setup, '_probe') as probe:
            result = setup.ensure_codex(root=self.root)
        self.assertEqual(result, str(self.root / setup.VERSION / 'codex.exe'))
        self.assertTrue((self.root / setup.VERSION / '.complete').is_file())
        self.assertEqual([p.name for p in self.root.iterdir()], [setup.VERSION])
        probe.assert_called_once()

    def test_cancel_waiting_for_parallel_setup(self):
        setup._lock.acquire()
        cancel = threading.Event()
        threading.Timer(.02, cancel.set).start()
        try:
            with patch.object(setup, 'find_existing', return_value=None):
                with self.assertRaises(InterruptedError):
                    setup.ensure_codex(cancel=cancel, root=self.root)
        finally:
            setup._lock.release()

    def test_background_failure_is_logged_without_crashing_app(self):
        log = Mock()
        with patch.object(setup, 'ensure_codex', side_effect=RuntimeError('private error')):
            setup.start_background(log).join(2)
        self.assertEqual(log.call_args.args, ('codex_setup_failed',))
        self.assertEqual(log.call_args.kwargs, {'error_type': 'RuntimeError'})
