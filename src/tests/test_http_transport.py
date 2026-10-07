"""Loopback transport regressions: no external APIs, proxies or credentials."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socketserver
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from http_transport import request_json
from windows_native import AIClient, AIResponseError


class ProxyParityTests(unittest.TestCase):
    def test_model_discovery_and_streaming_take_the_same_configured_proxy(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                requests.append((self.command, self.path, self.headers.get('Authorization')))
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"data":[{"id":"synthetic-model"}]}')
            def do_POST(self):
                requests.append((self.command, self.path, self.headers.get('Authorization')))
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200); self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy = {'http': 'http://127.0.0.1:' + str(server.server_port)}
        try:
            with patch('http_transport.getproxies', return_value=proxy), patch('http_transport.proxy_bypass', return_value=False):
                result = request_json('http://synthetic.invalid/models', {'Authorization': 'Bearer synthetic'})
                client = AIClient({}); client.cancel_event = threading.Event()
                answer = client.post('http://synthetic.invalid/chat', {'stream': True}, 3, key='synthetic')
            self.assertEqual(result['data'][0]['id'], 'synthetic-model')
            self.assertEqual(answer['choices'][0]['message']['content'], 'answer')
            self.assertEqual(requests, [('GET', 'http://synthetic.invalid/models', 'Bearer synthetic'),
                                        ('POST', 'http://synthetic.invalid/chat', 'Bearer synthetic')])
        finally:
            server.shutdown(); server.server_close()

    def test_https_tunnel_keeps_api_key_out_of_connect_and_proxy_auth_out_of_origin(self):
        requests = []
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                def read_headers():
                    lines = []
                    while True:
                        line = self.rfile.readline()
                        if not line or line == b'\r\n':
                            return b''.join(lines)
                        lines.append(line)
                requests.append(read_headers())
                self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n'); self.wfile.flush()
                requests.append(read_headers())
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}'); self.wfile.flush()
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy = {'https': 'http://proxy-user:proxy-password@127.0.0.1:' + str(server.server_address[1])}
        try:
            # Exercise real CONNECT/socket framing; TLS upgrade itself is a
            # mock so the test needs no certificate or trust-store changes.
            with patch('http_transport.getproxies', return_value=proxy), patch('http_transport.proxy_bypass', return_value=False), \
                    patch.object(asyncio.StreamWriter, 'start_tls', new_callable=AsyncMock) as tls:
                self.assertEqual(request_json('https://synthetic.invalid/models', {'Authorization': 'Bearer synthetic-api'}), {'ok': True})
            self.assertIn(b'CONNECT synthetic.invalid:443', requests[0])
            self.assertIn(b'Proxy-Authorization: Basic ', requests[0])
            self.assertNotIn(b'synthetic-api', requests[0])
            self.assertIn(b'GET /models', requests[1])
            self.assertIn(b'Authorization: Bearer synthetic-api', requests[1])
            self.assertNotIn(b'Proxy-Authorization', requests[1])
            self.assertEqual(tls.call_args.kwargs['server_hostname'], 'synthetic.invalid')
        finally:
            server.shutdown(); server.server_close()


class BoundedResponseTests(unittest.TestCase):
    def test_cancellation_and_deadline_interrupt_stalled_nonstream_body(self):
        entered, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200); self.send_header('Content-Length', '100'); self.end_headers()
                self.wfile.flush(); entered.set(); release.wait(2)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        errors, done = [], threading.Event()
        client = AIClient({}); client.cancel_event = threading.Event()
        url = 'http://127.0.0.1:' + str(server.server_port) + '/test'
        def ask():
            try:
                client.post(url, {'stream': False}, 0)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        try:
            with patch('http_transport.getproxies', return_value={}):
                threading.Thread(target=ask, daemon=True).start()
                self.assertTrue(entered.wait(1)); client.cancel()
                self.assertTrue(done.wait(1), 'Cancel did not close a nonstream request')
                self.assertIsInstance(errors[0], InterruptedError)
                before = time.monotonic()
                with self.assertRaises(TimeoutError):
                    request_json(url, timeout=.1, method='POST', payload=b'{}')
                self.assertLess(time.monotonic() - before, .8)
        finally:
            release.set(); server.shutdown(); server.server_close()

    def test_nonstream_size_and_invalid_unicode_are_rejected(self):
        for raw, code in ((b' ' * 65, 'response_size'),
                          (b'{"answer":"\\ud800"}', 'response_format')):
            client = AIClient({})
            with self.subTest(code=code), patch('windows_native.MAX_STREAM_RECORD_BYTES', 64), \
                    patch('windows_native.build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
                with self.assertRaises(AIResponseError) as raised:
                    client.post('https://synthetic.invalid', {}, 1)
                self.assertEqual(raised.exception.code, code)
                opener.return_value.open.return_value.__enter__.return_value.read.assert_called_once_with(65)

    def test_already_canceled_request_does_not_connect(self):
        client = AIClient({}); client.cancel_event = threading.Event(); client.cancel_event.set()
        with patch('windows_native.build_opener') as open_url:
            with self.assertRaises(InterruptedError):
                client.post('https://synthetic.invalid', {'stream': False}, 0)
            open_url.assert_not_called()


if __name__ == '__main__':
    unittest.main()
