import unittest

from secret_policy import SECRET, contains_secret


class SecretPolicyTests(unittest.TestCase):
    def test_numeric_settings_expressions_and_placeholders_are_allowed(self):
        for text in ('token=4096', 'password=len(s)', 'secret=0', 'api_key=PLACEHOLDER',
                     'password = user_password', 'secret = values[0]',
                     'token = "YOUR_API_KEY"', 'api_key = "<api-key>"',
                     'password = "changeme"', 'api_key = os.getenv("API_KEY")',
                     '{"api_key": "PLACEHOLDER", "token": 4096}',
                     'secret = "answer"', 'api_key = "test-key"',
                     'sk-your-api-key-here'):
            with self.subTest(text=text):
                self.assertFalse(contains_secret(text))

    def test_clear_api_keys_and_private_key_headers_are_blocked(self):
        for text in ('sk-' + 'aB19xZ' * 5, 'ghp_' + 'aB19xZ' * 6,
                     'github_pat_' + 'aB19xZ' * 8,
                     '-----BEGIN PRIVATE KEY-----', '-----BEGIN RSA PRIVATE KEY-----',
                     'token = "aB19xZaB19xZaB19xZaB19xZ"',
                     '{"api_key": "aB19xZaB19xZaB19xZaB19xZ"}'):
            with self.subTest(text=text[:25]):
                self.assertTrue(contains_secret(text))

    def test_plausible_literal_passwords_remain_blocked(self):
        for text in ('password = "MyLongP@ss2026"', "password='hunter2'",
                     '"password": "123456"', 'PASSWORD=MyLongP@ss2026'):
            with self.subTest(text=text.split('=')[0]):
                self.assertTrue(contains_secret(text))

    def test_compatibility_exposes_only_boolean_and_handles_empty_input(self):
        for text in ('token=4096', 'password="MyLongP@ss2026"'):
            self.assertIs(type(SECRET.search(text)), bool)
            self.assertEqual(SECRET.search(text), contains_secret(text))
        for text in ('', None, 4096):
            self.assertFalse(contains_secret(text))


if __name__ == '__main__':
    unittest.main()
