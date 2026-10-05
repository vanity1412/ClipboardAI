from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from api_zoo import APIHTTPError, ZooRouter, apply_config, update_catalog, validate
from cloud_client import CloudClient, DEFAULT_MODELS
from deepseek_client import DeepSeekClient
from provider_protocols import completed_response, EventStream, stream_error
from windows_native import AIClient


def profile(ident='a', **changes):
    item = dict(id=ident, name=ident, provider='compatible',
                base_url='https://example.com/v1', api_key='synthetic-' + ident,
                model='answer', vision_model='reader')
    item.update(changes)
    return validate({'profiles': [item]})['profiles'][0]


def response_message(text='final', **changes):
    item = dict(type='message', role='assistant', status='completed',
                content=[dict(type='output_text', text=text)])
    item.update(changes)
    return item


class CompletionGuardTests(unittest.TestCase):
    def test_responses_excludes_commentary_and_uses_final_phase(self):
        data = dict(status='completed', output=[response_message('working', phase='commentary'),
                                               response_message('final', phase='final_answer')])
        self.assertEqual(completed_response('responses', data), 'final')
        with self.assertRaises(RuntimeError):
            completed_response('responses', dict(data, output=data['output'][:1]))
        self.assertEqual(completed_response('responses', dict(status='completed', output=[response_message()])), 'final')

    def test_responses_refuses_incomplete_items_or_conflicting_failure_fields(self):
        good = dict(status='completed', output=[response_message()])
        for changes in ({'status': 'incomplete'}, {'status': 'in_progress'}, {'role': 'user'}, {'phase': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                completed_response('responses', dict(good, output=[response_message('partial', **changes)]))
        for changes in ({'error': {'code': 'server_error'}}, {'incomplete_details': {'reason': 'max_output_tokens'}}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                completed_response('responses', dict(good, **changes))
        refused = response_message(phase='commentary', content=[dict(type='refusal', refusal='blocked')])
        with self.assertRaises(RuntimeError):
            completed_response('responses', dict(good, output=[refused, response_message(phase='final_answer')]))

    def test_chat_completions_refuses_tool_or_refusal_metadata_despite_stop(self):
        for field, value in (('refusal', 'blocked'), ('tool_calls', [{'id': 'call'}]), ('function_call', {'name': 'tool'})):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                completed_response('compatible', {'choices': [{'finish_reason': 'stop', 'message': dict(content='interim', **{field: value})}]})
        self.assertEqual(completed_response('compatible', {'choices': [{'finish_reason': 'stop', 'message': dict(content='final', refusal=None, tool_calls=[])}]}), 'final')

    def test_legacy_deepseek_and_mirai_share_refusal_and_tool_guard(self):
        for field, value in (('refusal', 'blocked'), ('tool_calls', [{'id': 'call'}]), ('function_call', {'name': 'tool'})):
            data = {'choices': [{'finish_reason': 'stop', 'message': dict(content='interim', **{field: value})}]}
            deep = DeepSeekClient(dict(DEEPSEEK_MODEL='deepseek-flash', DEEPSEEK_API_KEY='synthetic'))
            mirai = CloudClient(dict(SELECTED_MODEL='claude-fable-5.1', MODEL_CHOICES=DEFAULT_MODELS,
                                     MIRAI_API_KEY='synthetic', MIRAI_BASE_URL='https://api.miraiapi.com'))
            for client in (deep, mirai):
                client.post = Mock(return_value=data)
                with self.subTest(field=field, client=type(client).__name__), self.assertRaises(RuntimeError):
                    client.ask('synthetic question')


class VisionCapabilityTests(unittest.TestCase):
    def test_refresh_replaces_explicitly_text_only_reader_or_disables_images(self):
        item = profile(model='text', vision_model='text')
        updated = update_catalog(item, [dict(id='text', vision=False), dict(id='vision', vision=True)])
        self.assertEqual(updated['vision_model'], 'vision')
        self.assertEqual(update_catalog(item, [dict(id='text', vision=False)])['vision_model'], '')

    def test_router_skips_known_text_only_reader_but_allows_manual_unknown(self):
        known_text = profile(vision_model='text', models=[dict(id='text', vision=False)])
        manual = profile('b', vision_model='my-unknown-reader', priority=1)
        config = {}
        apply_config(config, dict(profiles=[known_text, manual], primary='a'))
        call = Mock(return_value='answer')
        self.assertEqual(ZooRouter().run(config, known_text, True, call), 'answer')
        self.assertEqual(call.call_args.args[0]['id'], 'b')
        self.assertEqual(call.call_count, 1)
        config['API_ZOO']['auto'] = False
        with self.assertRaises(RuntimeError):
            ZooRouter().run(config, known_text, True, call)
        self.assertEqual(call.call_count, 1)

    def test_image_model_permission_failure_does_not_cool_down_text_model(self):
        first, backup = profile(), profile('b', priority=1)
        config = {}
        apply_config(config, dict(profiles=[first, backup], primary='a'))
        router = ZooRouter()
        call = Mock(side_effect=[APIHTTPError(403), 'backup'])
        self.assertEqual(router.run(config, first, True, call), 'backup')
        call = Mock(return_value='text answer')
        self.assertEqual(router.run(config, first, False, call), 'text answer')
        self.assertEqual(call.call_args.args[0]['id'], 'a')


class NativeStreamTests(unittest.TestCase):
    def test_anthropic_requires_closed_blocks_and_terminal_reason(self):
        sequences = [
            [{'type': 'message_stop'}],
            [{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'partial'}}],
            [{'type': 'message_start'}, {'type': 'message_stop'}],
            [{'type': 'message_start'}, {'type': 'content_block_start', 'index': 0,
              'content_block': {'type': 'text', 'text': 'partial'}}, {'type': 'message_stop'}],
            [{'type': 'message_start'}, {'type': 'content_block_start', 'index': 0,
              'content_block': {'type': 'text', 'text': 'partial'}},
             {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}}],
        ]
        for events in sequences:
            stream = EventStream('anthropic')
            with self.subTest(events=events), self.assertRaises(RuntimeError):
                for event in events:
                    stream.feed(event)

    def test_anthropic_usage_delta_does_not_erase_terminal_reason(self):
        stream = EventStream('anthropic')
        result = None
        for event in [
            {'type': 'message_start'}, {'type': 'ping'},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'final'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}},
            {'type': 'message_delta', 'usage': {'output_tokens': 3}}, {'type': 'message_stop'}
        ]:
            _, result = stream.feed(event)
        self.assertEqual(completed_response('anthropic', result), 'final')

    def test_documented_stream_errors_keep_retry_status_without_raw_text(self):
        for protocol, event, status in (
            ('anthropic', {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private raw detail'}}, 529),
            ('responses', {'type': 'response.failed', 'response': {'error': {'code': 'server_error', 'message': 'private raw detail'}}}, 500),
            ('responses', {'type': 'error', 'code': 'rate_limit_exceeded', 'message': 'private raw detail'}, 429),
        ):
            with self.subTest(protocol=protocol, status=status), self.assertRaises(APIHTTPError) as caught:
                stream_error(protocol, event)
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn('private', str(caught.exception))

    def test_real_http_preserves_refusal_tools_and_midstream_retry_errors(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                if body.get('overload'):
                    events = [{'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'private raw detail'}}]
                else:
                    field = body['unsupported_field']
                    events = [{'choices': [{'index': 0, 'delta': dict(content='interim', **{field: True}), 'finish_reason': None}]},
                              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
                for event in events:
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/synthetic'
            for field in ('refusal', 'tool_calls', 'function_call'):
                client = AIClient({})
                client.cancel_event = threading.Event()
                data = client.post(url, dict(stream=True, unsupported_field=field), 3, key='synthetic')
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    completed_response('compatible', data)
            client = AIClient({})
            client.cancel_event = threading.Event()
            with self.assertRaises(APIHTTPError) as caught:
                client.post(url, dict(stream=True, overload=True), 3, key='synthetic', protocol='anthropic')
            self.assertEqual(caught.exception.status, 529)
            config = {}
            first, second = profile(), profile('b', priority=1)
            apply_config(config, dict(profiles=[first, second], primary='a'))
            call = Mock(side_effect=[caught.exception, 'fresh backup answer'])
            self.assertEqual(ZooRouter().run(config, first, False, call), 'fresh backup answer')
            self.assertEqual(call.call_count, 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_chat_stream_rejects_conflicting_finish_or_extra_choice(self):
        cases = [
            [{'choices': [{'index': 0, 'delta': {'content': 'partial'}, 'finish_reason': 'length'}]},
             {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}],
            [{'choices': [{'index': 0, 'delta': {'content': 'final'}, 'finish_reason': 'stop'}]},
             {'choices': [{'index': 0, 'delta': {'content': 'extra'}, 'finish_reason': None}]}],
            [{'choices': [{'index': 0, 'delta': {'content': 'one'}, 'finish_reason': 'stop'},
                          {'index': 1, 'delta': {'content': 'two'}, 'finish_reason': 'stop'}]}]
        ]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.end_headers()
                for event in cases[body['case']]:
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for index in range(len(cases)):
                client = AIClient({})
                client.cancel_event = threading.Event()
                with self.subTest(case=index), self.assertRaises(ValueError):
                    client.post(f'http://127.0.0.1:{server.server_port}/synthetic',
                                dict(stream=True, case=index), 3, key='synthetic')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
