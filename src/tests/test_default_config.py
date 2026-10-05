import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from default_config import load_defaults


class PublicConfigTests(unittest.TestCase):
    def test_clean_install_has_no_embedded_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_defaults(folder)['DEEPSEEK_API_KEY'], '')

    def test_local_config_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, '.env').write_text('# example\nDEEPSEEK_API_KEY="local-test"\nMAX_CLIPBOARD_CHARS=123\nUNRELATED=value\n', encoding='utf-8')
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'environment-test'}, clear=True):
                values = load_defaults(folder)
            self.assertEqual(values['DEEPSEEK_API_KEY'], 'environment-test')
            self.assertEqual(values['MAX_CLIPBOARD_CHARS'], '123')
            self.assertNotIn('UNRELATED', values)
