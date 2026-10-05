import unittest
from unittest.mock import patch
from session_images import SessionImages


class ImageCacheTests(unittest.TestCase):
    def test_isolation_dedup_and_clear_generation(self):
        cache = SessionImages()
        cache.add('a', b'one')
        cache.add('a', b'one')
        cache.add('b', b'two')
        self.assertEqual(cache.get('a'), [b'one'])
        cache.clear('a')
        self.assertEqual(cache.add('a', b'late', 0), [])
        cache.add('a', b'new', cache.generation('a'))
        self.assertEqual(cache.get('a'), [b'new'])
        self.assertEqual(cache.get('b'), [b'two'])

    def test_limits_preserve_first_and_recent_images(self):
        cache = SessionImages()
        with patch('session_images.MAX_IMAGES', 3):
            for i in range(5):
                cache.add('a', str(i).encode())
        self.assertEqual(cache.get('a'), [b'0', b'3', b'4'])
        with patch('session_images.MAX_CACHE_BYTES', 3):
            cache.add('b', b'new')
        self.assertEqual(cache.get('a'), [])
        self.assertEqual(cache.get('b'), [b'new'])

    def test_invalid_images_are_rejected(self):
        cache = SessionImages()
        for png in (b'', None, 'text'):
            with self.assertRaises(ValueError):
                cache.add('a', png)
