import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from windows_native import AIClient, AIResponseError


class StreamTests(unittest.TestCase):
    def test_deepseek_sse_stream_preserves_finish_and_ignores_reasoning(self):
        auth = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                auth.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                items = [{"choices": [{"delta": {"reasoning_content": "private reasoning"}, "finish_reason": None}]},
                         {"choices": [{"delta": {"content": "int main(){}"}, "finish_reason": None}]},
                         {"choices": [{"delta": {}, "finish_reason": "length"}],
                          "usage": {"prompt_tokens": 20, "completion_tokens": 100,
                                    "completion_tokens_details": {"reasoning_tokens": 90}}}]
                payload = b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in items) + b"data: [DONE]\n\n"
                for piece in (payload[:3], payload[3:31], payload[31:]):
                    self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = AIClient({})
            client.cancel_event = threading.Event()
            previews = []
            client.on_stream = previews.append
            data = client.post(f"http://127.0.0.1:{server.server_port}/sse", {"stream": True}, 5, key="test")
            self.assertEqual(auth, ["Bearer test"])
            self.assertEqual(data["choices"][0]["finish_reason"], "length")
            self.assertEqual(data["choices"][0]["message"]["content"], "int main(){}")
            self.assertEqual(data["usage"]["completion_tokens"], 100)
            self.assertEqual(data["usage"]["completion_tokens_details"]["reasoning_tokens"], 90)
            self.assertNotIn("private reasoning", json.dumps(data))
            self.assertIsNone(previews[0])
            self.assertEqual(''.join(previews[1:]), 'int main(){}')
        finally:
            server.shutdown()
            server.server_close()

    def test_stream_reassembles_answer_and_cancel_disconnects_waiting_request(self):
        waiting = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                if body["messages"][-1]["content"] != "wait":
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                if body["messages"][-1]["content"] == "wait":
                    self.wfile.write(b'{"message":{"thinking":"..."},"done":false}\n')
                    self.wfile.flush()
                    waiting.set()
                    release.wait(3)
                    return
                payload = b"".join(json.dumps(item).encode() + b"\n" for item in (
                    {"message": {"content": "int "}, "done": False},
                    {"message": {"content": "main(){}"}, "done": True, "done_reason": "stop"}))
                for piece in (payload[:7], payload[7:]):
                    self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = AIClient({"OLLAMA_MODEL": "qwen2.5-coder:7b", "OLLAMA_TIMEOUT_S": "0", "OLLAMA_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
            client.cancel_event = threading.Event()
            self.assertEqual(client.ask("code"), ("int main(){}", "Ollama"))
            finished = threading.Event()
            errors = []
            def request():
                try:
                    client.ask("wait")
                except Exception as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            threading.Thread(target=request, daemon=True).start()
            self.assertTrue(waiting.wait(2))
            client.cancel()
            self.assertTrue(finished.wait(2), "Cancel must interrupt socket reads")
            self.assertTrue(errors)
        finally:
            release.set()
            server.shutdown()
            server.server_close()


class LargeStreamTests(unittest.TestCase):
    def request(self, payload, chunked=False, key="test"):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if key else "application/x-ndjson")
                self.send_header("Connection", "close")
                self.send_header("Transfer-Encoding" if chunked else "Content-Length", "chunked" if chunked else str(len(payload)))
                self.end_headers()
                try:
                    for offset in range(0, len(payload), 16387):
                        piece = payload[offset:offset + 16387]
                        if chunked:
                            self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                        else:
                            self.wfile.write(piece)
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client = AIClient({})
        client.cancel_event = threading.Event()
        try:
            return client.post(f"http://127.0.0.1:{server.server_port}/test", {"stream": True}, 5, key=key)
        finally:
            server.shutdown()
            server.server_close()
            self.assertIsNone(client.active_loop)
            self.assertIsNone(client.active_task)

    def sse(self, answer, done=True):
        items = [{"choices": [{"delta": {"content": answer}, "finish_reason": None}]}]
        if done:
            items.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        payload = b"".join(b"data: " + json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\r\n\r\n" for item in items)
        return payload + (b"data: [DONE]\r\n\r\n" if done else b"")

    def test_plain_sse_record_larger_than_one_megabyte(self):
        answer = "x" * (1024 * 1024 + 7)
        data = self.request(self.sse(answer))
        self.assertEqual(data["choices"][0]["message"]["content"], answer)
        self.assertEqual(data["choices"][0]["finish_reason"], "stop")

    def test_plain_and_chunked_unicode_records_keep_exact_bytes(self):
        answer = "// bài mẫu — dữ liệu UTF-8\n" * 8000
        for chunked in (False, True):
            data = self.request(self.sse(answer), chunked)
            self.assertEqual(data["choices"][0]["message"]["content"], answer)

    def test_large_plain_ollama_ndjson_is_supported(self):
        answer = "int f() { return 0; }\n" * 8000
        payload = json.dumps({"message": {"content": answer}, "done": True, "done_reason": "stop"}).encode() + b"\n"
        data = self.request(payload, key=None)
        self.assertEqual(data["message"]["content"], answer)

    def test_large_but_incomplete_record_is_not_accepted(self):
        with self.assertRaises(RuntimeError):
            self.request(self.sse("partial" * 20000, done=False))

    def test_oversized_transport_record_is_rejected_safely(self):
        for chunked in (False, True):
            with patch("windows_native.MAX_STREAM_RECORD_BYTES", 256), self.assertRaises(AIResponseError) as raised:
                self.request(self.sse("x" * 1000), chunked)
            self.assertEqual(raised.exception.code, "stream_size")

    def test_cancel_remains_responsive_during_large_unterminated_record(self):
        waiting, release, finished = threading.Event(), threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"unfinished":"' + b"x" * 100000)
                self.wfile.flush()
                waiting.set()
                release.wait(3)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        client, errors = AIClient({}), []
        client.cancel_event = threading.Event()
        def ask():
            try:
                client.post(f"http://127.0.0.1:{server.server_port}/test", {"stream": True}, 0, key="test")
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()
        try:
            threading.Thread(target=ask, daemon=True).start()
            self.assertTrue(waiting.wait(2))
            client.cancel()
            self.assertTrue(finished.wait(2))
            self.assertIsInstance(errors[0], InterruptedError)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
