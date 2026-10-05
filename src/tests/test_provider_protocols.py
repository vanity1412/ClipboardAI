import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from api_zoo import validate, consolidate, discover_models, apply_config, APIConnectionError
from browser_provider import BrowserSession
from cloud_client import CloudClient
from provider_catalog import PRESETS, preset_for, suggestions
from provider_protocols import request_body, completed_response, EventStream
from windows_native import AIClient


def profile(protocol='anthropic', ident='test', **updates):
    p = dict(id=ident, name='Example', provider=protocol, base_url='https://example.com/v1',
             api_key='synthetic-key', model='answer', vision_model='vision')
    if protocol == 'codex':
        p.update(base_url='https://chatgpt.com', api_key='')
    p.update(updates)
    return validate({'profiles': [p]})['profiles'][0]


class ProviderTests(unittest.TestCase):
    def test_presets_cover_all_requested_providers_and_two_custom_protocols(self):
        self.assertEqual(PRESETS['Custom · OpenAI-compatible'][0], 'compatible')
        self.assertEqual(PRESETS['Custom · Anthropic-compatible'][0], 'anthropic')
        for name, (protocol, endpoint, _, _) in PRESETS.items():
            if endpoint:
                p = profile(protocol, base_url=endpoint)
                self.assertEqual(preset_for(p), name)
                self.assertTrue(suggestions(name) or protocol == 'codex')

    def test_browser_ids_and_credentials_cannot_escape_login_directory(self):
        for changes in ({'id': '../outside'}, {'id': 'a/b'}, {'base_url': 'https://evil.example'}, {'api_key': 'key'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                profile('codex', **changes)

    def test_browser_profiles_do_not_merge_empty_credentials(self):
        data = consolidate({'profiles': [profile('codex', 'one'), profile('codex', 'two')], 'primary': 'two'})
        self.assertEqual(len(data['profiles']), 2)
        self.assertEqual(data['primary'], 'two')

    def test_anthropic_text_images_history_and_required_token_cap(self):
        messages = [{'role': 'system', 'content': 'instructions'}, {'role': 'user', 'content': 'old'},
                    {'role': 'assistant', 'content': 'answer'}, {'role': 'user', 'content': [
                        {'type': 'text', 'text': 'new'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]}]
        before = copy.deepcopy(messages)
        body = request_body('anthropic', 'claude', messages, 0, True)
        self.assertEqual(body['system'], 'instructions')
        self.assertEqual(body['max_tokens'], 8192)
        self.assertEqual(body['messages'][-1]['content'][-1]['source']['data'], 'YQ==')
        self.assertEqual(body['messages'][1]['role'], 'assistant')
        self.assertEqual(messages, before)

    def test_responses_image_and_assistant_history_schema(self):
        messages = [{'role': 'system', 'content': 'instructions'}, {'role': 'assistant', 'content': 'old'},
                    {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]}]
        body = request_body('responses', 'gpt', messages, 4000, False)
        self.assertEqual(body['input'][0]['content'][0]['type'], 'input_text')
        self.assertEqual(body['input'][1]['content'], 'old')
        self.assertEqual(body['input'][2]['content'][0]['type'], 'input_image')
        self.assertEqual(body['max_output_tokens'], 4000)
        self.assertFalse(body['store'])
        self.assertNotIn('messages', body)

    def test_native_response_only_accepts_complete_text(self):
        good = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'complete'}]}]}
        self.assertEqual(completed_response('responses', good), 'complete')
        for status in ('incomplete', 'failed', 'in_progress'):
            with self.assertRaises(RuntimeError):
                completed_response('responses', dict(good, status=status))
        for kind in ('function_call', 'web_search_call'):
            with self.assertRaises(RuntimeError):
                completed_response('responses', dict(good, output=[{'type': kind}]))
        with self.assertRaises(RuntimeError):
            completed_response('responses', dict(good, output=[{'type': 'message', 'content': [{'type': 'refusal'}]}]))

    def test_anthropic_stop_reason_drops_truncation_tools_refusal(self):
        good = {'stop_reason': 'end_turn', 'content': [{'type': 'thinking', 'thinking': 'private'}, {'type': 'text', 'text': 'answer'}]}
        self.assertEqual(completed_response('anthropic', good), 'answer')
        for stop in ('max_tokens', 'tool_use', 'refusal', 'pause_turn', None):
            with self.assertRaises(RuntimeError):
                completed_response('anthropic', dict(good, stop_reason=stop))
        with self.assertRaises(RuntimeError):
            completed_response('anthropic', dict(good, content=[{'type': 'tool_use'}]))

    def test_anthropic_discovery_uses_version_header_and_paginates(self):
        pages = [{'data': [{'id': 'a'}], 'has_more': True, 'last_id': 'a'}, {'data': [{'id': 'b'}], 'has_more': False}]
        with patch('urllib.request.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.side_effect = [json.dumps(page).encode() for page in pages]
            self.assertEqual([m['id'] for m in discover_models(profile())], ['a', 'b'])
            calls = opener.return_value.open.call_args_list
            req = calls[0].args[0]
            self.assertEqual(req.get_header('X-api-key'), 'synthetic-key')
            self.assertEqual(req.get_header('Anthropic-version'), '2023-06-01')
            self.assertIsNone(req.get_header('Authorization'))
            self.assertTrue(calls[1].args[0].full_url.endswith('?after_id=a'))

    def test_discovery_repeated_page_is_rejected(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({'data': [], 'has_more': True, 'last_id': 'a'}).encode()
            with self.assertRaises(ValueError):
                discover_models(profile())

    def test_cloud_routes_each_protocol_and_keeps_legacy_call_signature(self):
        for protocol, route, data in (
            ('anthropic', '/messages', {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'complete'}]}),
            ('responses', '/responses', {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'complete'}]}]}),
            ('compatible', '/chat/completions', {'choices': [{'finish_reason': 'stop', 'message': {'content': 'complete'}}]})):
            config = {'SELECTED_MODEL': 'zoo:test'}
            apply_config(config, {'profiles': [profile(protocol)]})
            client = CloudClient(config)
            client.post = Mock(return_value=data)
            self.assertEqual(client.ask('question')[0], 'complete')
            self.assertTrue(client.post.call_args.args[0].endswith(route))
            self.assertEqual(client.post.call_args.kwargs['key'], 'synthetic-key')
            self.assertEqual(client.post.call_args.kwargs.get('protocol'), None if protocol == 'compatible' else protocol)

    def test_browser_router_works_without_api_key_and_keeps_history(self):
        config = {'SELECTED_MODEL': 'zoo:test', 'BROWSER_AUTH_ROOT': 'build/test-auth'}
        apply_config(config, {'profiles': [profile('codex')]})
        history = [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'reply'}]
        with patch('browser_provider.BrowserSession') as bridge:
            bridge.return_value.__enter__.return_value.ask.return_value = 'complete'
            client = CloudClient(config)
            self.assertEqual(client.ask('follow-up', history)[0], 'complete')
            messages = bridge.return_value.__enter__.return_value.ask.call_args.args[1]
            self.assertTrue(any(m['content'] == 'first' for m in messages))

    def test_real_http_native_stream_headers_completion_disconnect_and_cancel(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                hits.append(dict(self.headers))
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                protocol = 'anthropic' if self.path == '/messages' else 'responses'
                events = ([{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                           {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'complete'}},
                           {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}, {'type': 'message_stop'}]
                          if protocol == 'anthropic' else [{'type': 'response.output_text.delta', 'delta': 'complete'},
                           {'type': 'response.completed', 'response': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'complete'}]}]}}])
                if body.get('partial'):
                    events = events[:-1]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                try:
                    for event in events:
                        self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for protocol, route in (('anthropic', '/messages'), ('responses', '/responses')):
                client = AIClient({})
                client.cancel_event = threading.Event()
                client.on_stream = Mock()
                url = f'http://127.0.0.1:{server.server_port}' + route
                data = client.post(url, {'stream': True}, 3, key='synthetic-key', protocol=protocol)
                self.assertEqual(completed_response(protocol, data), 'complete')
                header = {k.lower(): v for k, v in hits[-1].items()}
                if protocol == 'anthropic':
                    self.assertEqual(header['anthropic-version'], '2023-06-01')
                    self.assertEqual(header['x-api-key'], 'synthetic-key')
                    self.assertNotIn('authorization', header)
                else:
                    self.assertEqual(header['authorization'], 'Bearer synthetic-key')
                with self.assertRaises(APIConnectionError):
                    client.post(url, {'stream': True, 'partial': True}, 3, key='synthetic-key', protocol=protocol)
                client.cancel_event.set()
                with self.assertRaises(InterruptedError):
                    client.post(url, {'stream': True}, 3, key='synthetic-key', protocol=protocol)
        finally:
            server.shutdown()
            server.server_close()


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.session = BrowserSession(profile('codex'), self.folder.name, threading.Event())
        self.sent = []
        self.session.send = self.sent.append

    def test_child_cli_uses_isolated_home_and_no_inherited_api_keys(self):
        process = Mock()
        process.poll.return_value = 0
        self.session.request = Mock(return_value={})
        with patch.dict(os.environ, {'CODEX_HOME': 'another-app-home', 'OPENAI_API_KEY': 'private-key', 'CODEX_API_KEY': 'private-codex-key'}), patch('browser_provider.codex_executable', return_value='official-codex'), patch('browser_provider.subprocess.Popen', return_value=process) as popen, patch.object(self.session, '_read'):
            with self.session:
                environment = popen.call_args.kwargs['env']
                self.assertNotEqual(environment['CODEX_HOME'], 'another-app-home')
                self.assertTrue(environment['CODEX_HOME'].endswith('test'))
                self.assertNotIn('OPENAI_API_KEY', environment)
                self.assertNotIn('CODEX_API_KEY', environment)
                command = popen.call_args.args[0]
                self.assertIn('features.shell_tool=false', command)
                self.assertIn('web_search="disabled"', command)
                self.assertNotEqual(popen.call_args.kwargs['cwd'], str(self.session.home))
                self.assertEqual(os.environ['CODEX_HOME'], 'another-app-home')

    def test_protocol_request_defers_early_notifications(self):
        notice = {'method': 'account/updated', 'params': {'authMode': 'chatgpt'}}
        self.session.events.put(notice)
        self.session.events.put({'id': 1, 'result': {'account': {'type': 'chatgpt'}}})
        self.assertEqual(self.session.require_account()['type'], 'chatgpt')
        self.assertEqual(self.session.deferred, [notice])
        self.assertEqual(self.sent[0]['method'], 'account/read')

    def test_native_login_opens_official_url_and_checks_account_before_models(self):
        self.session.request = Mock(side_effect=[{'loginId': 'login', 'authUrl': 'https://auth.openai.com/oauth/authorize?state=test'},
                                                 {'account': {'type': 'chatgpt'}}, {'data': [{'model': 'gpt', 'inputModalities': ['text', 'image']}]}])
        self.session.events.put({'method': 'account/login/completed', 'params': {'loginId': 'login', 'success': True}})
        opener = Mock(return_value=True)
        self.assertEqual(self.session.login(opener), [{'id': 'gpt', 'vision': True}])
        self.assertEqual(opener.call_count, 1)
        self.assertIsNone(self.session.login_id)

    def test_invalid_auth_url_cannot_open_arbitrary_site(self):
        for url in ('https://evil.example/oauth', 'http://auth.openai.com/oauth', 'https://secret@auth.openai.com/oauth'):
            self.session.request = Mock(return_value={'loginId': 'login', 'authUrl': url})
            opener = Mock()
            with self.assertRaises(RuntimeError):
                self.session.login(opener)
            opener.assert_not_called()

    def test_cancel_and_timeout_are_bounded_without_fallback(self):
        self.session.cancel.set()
        with self.assertRaises(InterruptedError):
            self.session.next_event(None)
        self.session.cancel.clear()
        with self.assertRaises(TimeoutError):
            self.session.next_event(0)

    def test_no_account_is_not_reported_as_signed_in(self):
        self.session.request = Mock(return_value={'account': None})
        with self.assertRaises(RuntimeError):
            self.session.require_account()

    def test_model_pagination_and_image_modalities(self):
        self.session.request = Mock(side_effect=[{'data': [{'model': 'text', 'inputModalities': ['text']}], 'nextCursor': 'next'},
                                                {'data': [{'model': 'image', 'inputModalities': ['text', 'image']}]}])
        self.assertEqual(self.session.models(), [{'id': 'text', 'vision': False}, {'id': 'image', 'vision': True}])
        self.assertEqual(self.session.request.call_args.args[1]['cursor'], 'next')

    def test_server_tool_or_approval_request_is_rejected(self):
        self.session.events.put({'id': 88, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        with self.assertRaises(RuntimeError):
            self.session.next_event(None)
        self.assertEqual(self.sent[-1]['error']['code'], -32601)

    def test_browser_answer_requires_completed_turn_and_excludes_commentary(self):
        for status in ('completed', 'failed', 'interrupted'):
            self.session.temporary = Mock(name='isolated-cwd')
            self.session.temporary.name = 'empty-temp-folder'
            self.session.request = Mock(side_effect=[{'account': {'type': 'chatgpt'}}, {'thread': {'id': 'thread'}}, {'turn': {'id': 'turn'}}])
            for event in (
                {'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn', 'item': {'type': 'agentMessage', 'id': 'c', 'phase': 'commentary', 'text': 'working'}}},
                {'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn', 'item': {'type': 'agentMessage', 'id': 'a', 'phase': 'final_answer', 'text': 'complete'}}},
                {'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn', 'status': status}}}):
                self.session.events.put(event)
            if status == 'completed':
                self.assertEqual(self.session.ask('gpt', [{'role': 'user', 'content': 'question'}], 3), 'complete')
            else:
                with self.assertRaises(RuntimeError):
                    self.session.ask('gpt', [{'role': 'user', 'content': 'question'}], 3)
            thread_options = self.session.request.call_args_list[1].args[1]
            self.assertTrue(thread_options['ephemeral'])
            self.assertEqual(thread_options['sandbox'], 'read-only')
            self.assertEqual(thread_options['approvalPolicy'], 'untrusted')


if __name__ == '__main__':
    unittest.main()
