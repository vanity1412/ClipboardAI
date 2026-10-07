import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from usb_setup import child_path, install_payload


class PayloadTests(unittest.TestCase):
    def package(self, root, corrupt=False):
        payload = root / 'payload'
        payload.mkdir()
        (payload / 'part1').write_bytes(b'first')
        (payload / 'part2').write_bytes(b'second')
        item = {'path': 'models/blobs/test', 'size': 11, 'parts': ['part1', 'part2'],
                'sha256': hashlib.sha256(b'firstsecond').hexdigest()}
        if corrupt:
            (payload / 'part2').write_bytes(b'broken')
        (payload / 'manifest.json').write_text(json.dumps({'files': [item]}))

    def test_split_file_reassembled_and_second_launch_uses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root)
            result = install_payload(root, root / 'installed')
            self.assertEqual((result / 'models/blobs/test').read_bytes(), b'firstsecond')
            # Cache can still be used even when USB parts are no longer readable.
            (root / 'payload/part1').unlink()
            self.assertEqual(install_payload(root, root / 'installed'), result)

    def test_corruption_rejected_without_completed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root, corrupt=True)
            with self.assertRaises(RuntimeError):
                install_payload(root, root / 'installed')
            self.assertEqual(list((root / 'installed').rglob('installed.json')), [])

    def test_same_size_cached_corruption_is_repaired_before_runtime_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root)
            result = install_payload(root, root / 'installed')
            cached = result / 'models/blobs/test'
            cached.write_bytes(b'corruptbyte')
            self.assertEqual(cached.stat().st_size, 11)
            self.assertEqual(install_payload(root, root / 'installed'), result)
            self.assertEqual(cached.read_bytes(), b'firstsecond')

    def test_failed_cache_repair_does_not_keep_completed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.package(root)
            result = install_payload(root, root / 'installed')
            (result / 'models/blobs/test').write_bytes(b'corruptbyte')
            (root / 'payload/part2').write_bytes(b'broken')
            with self.assertRaises(RuntimeError):
                install_payload(root, root / 'installed')
            self.assertFalse((result / 'installed.json').exists())

    def test_cannot_write_outside_package_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                child_path(Path(directory), '../outside')


if __name__ == '__main__':
    unittest.main()
