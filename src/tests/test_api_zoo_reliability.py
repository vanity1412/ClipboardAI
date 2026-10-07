"""Billing recovery, cancellation and protected storage without real accounts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from api_zoo import APIHTTPError, ZooRouter, ZooStore, consolidate, discover_models, validate
from credential_storage import decode_profiles, encode_profiles


def profile(**changes):
    row = dict(id='primary', name='Synthetic', base_url='https://example.test/v1',
               api_key='synthetic-key', provider='compatible', model='model', vision_model='')
    row.update(changes)
    return validate({'profiles': [row]})['profiles'][0]


class RouterRecoveryTests(unittest.TestCase):
    def test_topped_up_account_recovers_on_explicit_retry_or_after_cooldown(self):
        selected = profile()
        config = {'API_ZOO': validate({'profiles': [selected]})}
        for explicit in (False, True):
            router = ZooRouter()
            with patch('api_zoo.time.monotonic', return_value=100):
                with self.assertRaises(RuntimeError):
                    router.run(config, selected, False, Mock(side_effect=APIHTTPError(402)))
                restored = Mock(return_value='restored')
                with self.assertRaises(RuntimeError):
                    router.run(config, selected, False, restored)
                restored.assert_not_called()
                if explicit:
                    router.retry(selected)
                    self.assertEqual(router.run(config, selected, False, restored), 'restored')
            if not explicit:
                with patch('api_zoo.time.monotonic', return_value=131):
                    self.assertEqual(router.run(config, selected, False, restored), 'restored')

    def test_retry_does_not_bypass_invalid_credentials(self):
        selected = profile()
        config = {'API_ZOO': validate({'profiles': [selected]})}
        router = ZooRouter()
        with self.assertRaises(RuntimeError):
            router.run(config, selected, False, Mock(side_effect=APIHTTPError(401)))
        router.retry(selected)
        call = Mock(return_value='unsafe retry')
        with self.assertRaises(RuntimeError):
            router.run(config, selected, False, call)
        call.assert_not_called()

    def test_old_billing_failure_does_not_allow_bypassing_later_auth_or_rate_limits(self):
        selected = profile()
        config = {'API_ZOO': validate({'profiles': [selected]})}
        for status in (401, 429):
            router = ZooRouter()
            with patch('api_zoo.time.monotonic', return_value=100):
                with self.assertRaises(RuntimeError):
                    router.run(config, selected, False, Mock(side_effect=APIHTTPError(402)))
            with patch('api_zoo.time.monotonic', return_value=131):
                with self.assertRaises(RuntimeError):
                    router.run(config, selected, False, Mock(side_effect=APIHTTPError(status)))
                router.retry(selected)
                call = Mock(return_value='unexpected retry')
                with self.assertRaises(RuntimeError):
                    router.run(config, selected, False, call)
                call.assert_not_called()

    def test_primary_duplicate_keeps_enabled_status_and_request_limits(self):
        old = profile(enabled=False, priority=0, timeout=3000, max_tokens=1)
        primary = dict(old, id='active', enabled=True, priority=1, timeout=60, max_tokens=4096)
        result = consolidate({'profiles': [old, primary], 'primary': 'active'})
        self.assertEqual(len(result['profiles']), 1)
        self.assertTrue(result['profiles'][0]['enabled'])
        self.assertEqual(result['primary'], old['id'])
        self.assertEqual((result['profiles'][0]['timeout'], result['profiles'][0]['max_tokens']), (60, 4096))


class DiscoveryCancellationTests(unittest.TestCase):
    def test_browser_discovery_receives_cancel_and_shared_deadline(self):
        item = profile(provider='codex', base_url='https://chatgpt.com', api_key='')
        cancel = threading.Event()
        with patch('browser_provider.BrowserSession') as bridge, patch('api_zoo.time.monotonic', return_value=100):
            bridge.return_value.__enter__.return_value.models.return_value = [{'id': 'model', 'vision': True}]
            self.assertEqual(discover_models(item, 'synthetic-auth', cancel, timeout=20)[0]['id'], 'model')
            bridge.assert_called_once_with(item, 'synthetic-auth', cancel, deadline=120)

    def test_already_canceled_discovery_never_opens_transport(self):
        cancel = threading.Event(); cancel.set()
        with patch('http_transport.request_json') as request:
            with self.assertRaises(InterruptedError):
                discover_models(profile(), cancel=cancel)
        request.assert_not_called()

    def test_aggregate_catalog_limit_counts_raw_bytes_across_pages(self):
        item = profile(provider='anthropic')
        page = {'data': [{'id': 'one'}], 'has_more': True, 'last_id': 'one'}
        with patch('http_transport.request_json', side_effect=[(page, 3 * 1024 * 1024),
                  ({'data': [{'id': 'two'}]}, 2 * 1024 * 1024)]) as request:
            with self.assertRaises(ValueError):
                discover_models(item)
            self.assertEqual(request.call_args_list[1].kwargs['max_bytes'], 1024 * 1024)

    def test_stalled_http_discovery_really_cancels_and_has_an_overall_deadline(self):
        started, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                started.set()
                release.wait(2)
                try:
                    self.send_response(200); self.end_headers()
                    self.wfile.write(b'{"data":[{"id":"model"}]}')
                except OSError:
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        item = dict(profile(), base_url=f'http://127.0.0.1:{server.server_port}')
        try:
            with patch('http_transport.getproxies', return_value={}):
                cancel = threading.Event()
                timer = threading.Timer(.1, cancel.set); timer.start()
                before = time.monotonic()
                with self.assertRaises(InterruptedError):
                    discover_models(item, cancel=cancel)
                self.assertLess(time.monotonic() - before, .8)
                timer.join()
                before = time.monotonic()
                with self.assertRaises(TimeoutError):
                    discover_models(item, timeout=.1)
                self.assertLess(time.monotonic() - before, .8)
        finally:
            release.set(); server.shutdown(); server.server_close()


class ProtectedStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_legacy_key_migrates_on_save_without_changing_runtime_profiles(self):
        data = validate({'profiles': [profile()], 'primary': 'primary'})
        with tempfile.TemporaryDirectory() as folder:
            store = ZooStore(folder)
            store.path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(store.load(), data)
            self.assertEqual(store.save(store.load()), data)
            saved = store.path.read_text(encoding='utf-8')
            self.assertNotIn('synthetic-key', saved)
            self.assertIn('api_key_protected', saved)
            self.assertEqual(store.load(), data)

    def test_corrupt_protected_key_fails_without_exposing_value(self):
        secret = 'private-corrupt-secret'
        data = {'profiles': [{'api_key': '', 'api_key_protected': 'dpapi:v1:' + secret}]}
        with self.assertRaises(ValueError) as raised:
            decode_profiles(data)
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn('api_key_protected', data['profiles'][0])

    def test_unsupported_os_keeps_legacy_save_format(self):
        data = {'profiles': [{'api_key': 'synthetic-key'}]}
        with patch('credential_storage.os.name', 'posix'):
            self.assertEqual(encode_profiles(data), data)


if __name__ == '__main__':
    unittest.main()
