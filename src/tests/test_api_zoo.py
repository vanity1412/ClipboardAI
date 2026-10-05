import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from api_zoo import (APIHTTPError, ZooStore, ZooRouter, validate, apply_config,
                     seed_profiles, retry_delay, consolidate, discover_models, update_catalog)
from cloud_client import CloudClient, load_cloud_config, DEFAULT_MODELS
from windows_native import AIClient


def profile(ident='a', **updates):
    p = dict(id=ident, name=ident, base_url='https://' + ident + '.example/v1',
             api_key='key-' + ident, model='coder', vision_model='reader', priority=0)
    p.update(updates)
    return validate({'profiles': [p]})['profiles'][0]


class ZooTests(unittest.TestCase):
    def config(self, profiles=None, **updates):
        data = validate(dict(profiles=profiles or [profile(), profile('b', priority=1)], **updates))
        config = dict(MODEL_CHOICES=DEFAULT_MODELS, SELECTED_MODEL='zoo:a')
        apply_config(config, data)
        return config

    def client(self, **updates):
        c = CloudClient(self.config(**updates))
        c.cancel_event = threading.Event()
        c.post = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': 'complete'}}]})
        return c

    def test_store_primary_roundtrip_and_legacy_import_keeps_credentials_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ZooStore(folder)
            self.assertEqual(store.load()['profiles'], [])
            data = store.save({'profiles': [profile()], 'primary': 'a'})
            self.assertEqual(store.load(), data)
            loaded = load_cloud_config(folder, {'DEEPSEEK_API_KEY': 'original'})
            self.assertEqual(loaded['SELECTED_MODEL'], 'zoo:a')
            self.assertEqual(loaded['DEEPSEEK_API_KEY'], 'original')

    def test_malformed_zoo_is_preserved_and_legacy_deepseek_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'api_zoo.json'
            path.write_text('{bad', encoding='utf-8')
            config = load_cloud_config(folder, {})
            self.assertIn('ZOO_CONFIG_ERROR', config)
            self.assertEqual(config['SELECTED_MODEL'], 'deepseek-flash')
            self.assertEqual(path.read_text(), '{bad')

    def test_unsafe_endpoints_and_duplicate_ids_rejected(self):
        for url in ('http://a.example/v1', 'https://key@a.example/v1', 'https://a.example?key=x', 'https://a.example/v1#x', 'https://a.example:444/v1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                profile(base_url=url)
        with self.assertRaises(ValueError):
            validate({'profiles': [profile(), profile()]})

    def test_seed_all_legacy_models_without_sending_request(self):
        config = dict(MODEL_CHOICES=DEFAULT_MODELS, DEEPSEEK_API_KEY='deep', MIRAI_API_KEY='mirai')
        seeded = seed_profiles(config)
        self.assertEqual(len(seeded['profiles']), 2)
        self.assertEqual(seeded['profiles'][0]['api_key'], 'deep')
        self.assertEqual(seeded['profiles'][1]['api_key'], 'mirai')

    def test_migration_groups_models_under_one_api_and_preserves_primary(self):
        a = profile('a', model='first')
        b = profile('b', base_url=a['base_url'], api_key=a['api_key'], model='second')
        data = consolidate({'profiles': [a, b], 'primary': 'b'})
        self.assertEqual(len(data['profiles']), 1)
        self.assertEqual(data['primary'], 'a')
        self.assertEqual(data['profiles'][0]['model'], 'second')

    def test_discovery_gets_every_unique_model_and_vision_metadata(self):
        p = profile(model='', vision_model='')
        response = {'data': [{'id': 'new-coder', 'input_modalities': ['text']},
                             {'id': 'new-reader', 'capabilities': {'vision': True}},
                             {'id': 'new-coder'}, {'id': 'unknown'}]}
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            models = discover_models(p)
        self.assertEqual([m['id'] for m in models], ['new-coder', 'new-reader', 'unknown'])
        refreshed = update_catalog(p, models)
        self.assertEqual(refreshed['model'], 'new-coder')
        self.assertEqual(refreshed['vision_model'], 'new-reader')

    def test_zoo_choices_have_no_legacy_duplicates(self):
        c = self.config()
        self.assertEqual([m for m, label in c['MODEL_CHOICES']], ['zoo:a', 'zoo:b'])

    def test_empty_api_list_does_not_restore_fixed_models(self):
        c = {'ZOO_ONLY': True, 'MODEL_CHOICES': DEFAULT_MODELS, 'SELECTED_MODEL': 'deepseek-flash'}
        apply_config(c, {'profiles': []})
        self.assertEqual(c['MODEL_CHOICES'], ())

    def test_new_discovered_deepseek_model_is_not_restricted_to_fixed_aliases(self):
        c = self.client(profiles=[profile(provider='deepseek', base_url='https://api.deepseek.com', model='server-new-model')])
        self.assertEqual(c.ask('problem')[0], 'complete')
        self.assertEqual(c.post.call_args.args[1]['model'], 'server-new-model')

    def test_503_fallback_preserves_exact_input_history_and_separate_keys(self):
        c = self.client()
        c.post.side_effect = [APIHTTPError(503), {'choices': [{'finish_reason': 'stop', 'message': {'content': 'backup'}}]}]
        history = [{'role': 'user', 'content': 'problem'}, {'role': 'assistant', 'content': 'code'}]
        before = copy.deepcopy(history)
        self.assertEqual(c.ask('fix', history), ('backup', 'b: coder'))
        calls = c.post.call_args_list
        self.assertEqual([v.kwargs['key'] for v in calls], ['key-a', 'key-b'])
        self.assertEqual(calls[0].args[1], calls[1].args[1])
        self.assertEqual(history, before)
        self.assertEqual(c.config['SELECTED_MODEL'], 'zoo:a')

    def test_401_disables_only_failed_profile_until_reload(self):
        c = self.client()
        c.post.side_effect = [APIHTTPError(401), {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}]
        c.ask('one')
        c.post.reset_mock(side_effect=True)
        c.post.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}
        c.ask('two')
        self.assertEqual(c.post.call_args.kwargs['key'], 'key-b')

    def test_429_retry_after_cooldown_is_honored(self):
        router = ZooRouter()
        config = self.config()
        call = Mock(side_effect=[APIHTTPError(429, '120'), 'ok'])
        with patch('api_zoo.time.monotonic', return_value=100):
            self.assertEqual(router.run(config, config['API_ZOO']['profiles'][0], False, call), 'ok')
        self.assertEqual(next(iter(router.cooldowns.values())), 220)

    def test_same_key_cannot_bypass_401_or_429_by_changing_model(self):
        for status in (401, 429):
            config = self.config(profiles=[profile(), profile('b', base_url='https://a.example/v1', api_key='key-a', model='other'), profile('c')])
            call = Mock(side_effect=[APIHTTPError(status, '90'), 'ok'])
            router = ZooRouter()
            self.assertEqual(router.run(config, config['API_ZOO']['profiles'][0], False, call), 'ok')
            self.assertEqual([v.args[0]['id'] for v in call.call_args_list], ['a', 'c'])

    def test_manual_mode_and_400_never_fallback(self):
        for auto, status in ((False, 503), (True, 400), (True, 404)):
            c = self.client(auto=auto)
            c.post.side_effect = APIHTTPError(status)
            with self.assertRaises(APIHTTPError):
                c.ask('problem')
            self.assertEqual(c.post.call_count, 1)

    def test_at_most_three_attempts_even_with_many_profiles(self):
        c = self.client(profiles=[profile(x) for x in 'abcd'])
        c.post.side_effect = APIHTTPError(503)
        with self.assertRaises(RuntimeError):
            c.ask('problem')
        self.assertEqual(c.post.call_count, 3)

    def test_cancel_prevents_all_fallback_and_backoff(self):
        c = self.client()
        def fail(*args, **kwargs):
            c.cancel_event.set()
            raise APIHTTPError(503)
        c.post.side_effect = fail
        with self.assertRaises(InterruptedError):
            c.ask('problem')
        self.assertEqual(c.post.call_count, 1)
        c.post.reset_mock()
        with self.assertRaises(InterruptedError):
            c.ask('another')
        c.post.assert_not_called()

    def test_cancellable_single_profile_backoff(self):
        router = ZooRouter()
        config = self.config(profiles=[profile()])
        event = Mock()
        event.is_set.return_value = False
        event.wait.return_value = True
        call = Mock(side_effect=APIHTTPError(503))
        with self.assertRaises(InterruptedError):
            router.run(config, config['API_ZOO']['profiles'][0], False, call, event)
        self.assertEqual(call.call_count, 1)
        event.wait.assert_called_once()

    def test_f4_fallback_skips_text_only_provider_and_uses_reader_model(self):
        c = self.client(profiles=[profile(vision_model=''), profile('b', vision_model='vision')])
        c.post.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'text': 'SUM', 'complete': True})}}]}
        result = c.read_problem(b'fake-image')
        self.assertEqual(result['text'], 'SUM')
        self.assertEqual(c.post.call_args.args[1]['model'], 'vision')
        self.assertEqual(c.post.call_args.kwargs['key'], 'key-b')
        self.assertNotIn('thinking', c.post.call_args.args[1])

    def test_f4_no_vision_provider_does_not_send(self):
        c = self.client(profiles=[profile(vision_model='')])
        with self.assertRaises(RuntimeError):
            c.read_problem(b'fake-image')
        c.post.assert_not_called()

    def test_deepseek_fallback_uses_its_own_protocol_and_restores_config(self):
        c = self.client(profiles=[profile(), profile('b', provider='deepseek', base_url='https://api.deepseek.com', model='deepseek-flash', vision_model='deepseek-flash', max_tokens=4096)])
        c.post.side_effect = [APIHTTPError(503), {'choices': [{'finish_reason': 'stop', 'message': {'content': 'code'}}]}]
        original = copy.deepcopy(c.config)
        c.ask('problem')
        request = c.post.call_args
        self.assertEqual(request.args[0], 'https://api.deepseek.com/chat/completions')
        self.assertIn('thinking', request.args[1])
        self.assertEqual(request.kwargs['key'], 'key-b')
        self.assertEqual(c.config, original)

    def test_incomplete_or_refused_response_does_not_silently_switch_models(self):
        for finish in ('length', 'content_filter', None):
            c = self.client()
            c.post.return_value = {'choices': [{'finish_reason': finish, 'message': {'content': 'partial'}}]}
            with self.assertRaises(RuntimeError):
                c.ask('problem')
            self.assertEqual(c.post.call_count, 1)

    def test_failed_stream_discarded_before_fallback(self):
        c = self.client()
        c.post.side_effect = [ConnectionError('partial'), {'choices': [{'finish_reason': 'stop', 'message': {'content': 'complete'}}]}]
        self.assertEqual(c.ask('problem')[0], 'complete')

    def test_http_transport_preserves_status_and_retry_after(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(429)
                self.send_header('Retry-After', '90')
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for stream in (False, True):
                c = AIClient({})
                c.cancel_event = threading.Event()
                with self.assertRaises(APIHTTPError) as caught:
                    c.post(f'http://127.0.0.1:{server.server_port}/chat', {'stream': stream}, 3, key='test')
                self.assertEqual(caught.exception.status, 429)
                self.assertEqual(caught.exception.retry_after, '90')
        finally:
            server.shutdown()
            server.server_close()

    def test_redirect_does_not_forward_credential(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(307)
                self.send_header('Location', '/stolen')
                self.end_headers()
            def do_GET(self):
                hits.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.end_headers()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(APIHTTPError):
                AIClient({}).post(f'http://127.0.0.1:{server.server_port}/api', {}, 3, key='test')
            self.assertEqual(hits, [])
        finally:
            server.shutdown()
            server.server_close()

    def test_retry_after_date_and_invalid_values(self):
        self.assertEqual(retry_delay('90'), 90)
        self.assertEqual(retry_delay('nan'), 30)
        self.assertEqual(retry_delay('not-a-date'), 30)


if __name__ == '__main__':
    unittest.main()
