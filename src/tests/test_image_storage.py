"""Image/data persistence regressions, using only temporary local databases."""
from pathlib import Path
from contextlib import closing
import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from session_images import SessionImages


class ImageStorageTests(unittest.TestCase):
    def test_restart_isolation_dedup_delete_and_stale_capture_generation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'images.sqlite3'
            first = SessionImages(path)
            first.add('a', b'one')
            first.add('a', b'one')
            first.add('b', b'two')
            reopened = SessionImages(path)
            self.assertEqual(reopened.get('a'), [b'one'])
            self.assertEqual(reopened.get('b'), [b'two'])
            self.assertTrue(reopened.remove('a', hashlib.sha256(b'one').hexdigest()))
            self.assertEqual(first.get('a'), [])
            self.assertEqual(first.add('a', b'late', 0), [])
            reopened.add('a', b'new', reopened.generation('a'))
            reopened.clear('b')
            final = SessionImages(path)
            self.assertEqual(final.get('a'), [b'new'])
            self.assertEqual(final.get('b'), [])

    def test_ram_eviction_restores_from_disk_without_false_missing_notice(self):
        with tempfile.TemporaryDirectory() as root, patch('session_images.MAX_CACHE_BYTES', 4):
            cache = SessionImages(Path(root) / 'images.sqlite3')
            cache.add('a', b'one')
            cache.add('b', b'two')
            self.assertNotIn('a', cache.frames)
            self.assertEqual(cache.get('a'), [b'one'])
            self.assertEqual(cache.warning('a'), '')
            self.assertEqual(cache.get('b'), [b'two'])
            self.assertLessEqual(sum(len(p) for rows in cache.frames.values() for _, p in rows), 4)

    def test_image_limits_and_dropped_counts_survive_restart(self):
        with tempfile.TemporaryDirectory() as root, patch('session_images.MAX_IMAGES', 3):
            path = Path(root) / 'images.sqlite3'
            cache = SessionImages(path)
            for index in range(5):
                cache.add('a', str(index).encode())
            cache = SessionImages(path)
            self.assertEqual(cache.get('a'), [b'0', b'3', b'4'])
            self.assertIn('2 ảnh', cache.warning('a'))
            cache.clear('a')
            cache = SessionImages(path)
            self.assertEqual(cache.warning('a'), '')
            self.assertEqual(cache.get('a'), [])

    def test_disk_limit_invalidates_cached_evicted_sessions_and_records_warning(self):
        with tempfile.TemporaryDirectory() as root, patch('image_storage.MAX_DISK_BYTES', 5):
            path = Path(root) / 'images.sqlite3'
            cache = SessionImages(path)
            cache.add('a', b'old')
            cache.add('b', b'new')
            self.assertEqual(cache.get('a'), [])
            self.assertIn('1 ảnh', cache.warning('a'))
            self.assertEqual(cache.get('b'), [b'new'])
            self.assertEqual(SessionImages(path).get('a'), [])
            with closing(sqlite3.connect(path)) as db, db:
                self.assertLessEqual(db.execute('SELECT SUM(length(png)) FROM images').fetchone()[0], 5)

    def test_corrupt_image_detected_without_affecting_other_images(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'images.sqlite3'
            cache = SessionImages(path)
            cache.add('a', b'first')
            cache.add('a', b'second')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE images SET png=? WHERE digest=?',
                           (b'corrupted', hashlib.sha256(b'first').hexdigest()))
            cache = SessionImages(path)
            self.assertEqual(cache.get('a'), [b'second'])
            self.assertIn('1 ảnh', cache.warning('a'))

    def test_corrupt_database_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'images.sqlite3'
            original = b'not a database'
            path.write_bytes(original)
            cache = SessionImages(path)
            self.assertIn('Không đọc', cache.warning('a'))
            self.assertEqual(cache.get('a'), [])
            with self.assertRaises(ValueError):
                cache.add('a', b'new')
            with self.assertRaises(ValueError):
                cache.clear('a')
            self.assertEqual(path.read_bytes(), original)

    def test_transaction_failure_rolls_back_image_and_evictions(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'images.sqlite3'
            cache = SessionImages(path)
            cache.add('a', b'original')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TRIGGER deny_image BEFORE INSERT ON images BEGIN SELECT RAISE(ABORT,'synthetic'); END")
            with self.assertRaises(ValueError):
                cache.add('a', b'new')
            self.assertEqual(SessionImages(path).get('a'), [b'original'])


if __name__ == '__main__':
    unittest.main()
