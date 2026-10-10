"""Private stdio MCP connection to the official Cua Driver executable."""
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import uuid

MAX_RECORD = 32 * 1024 * 1024


class CuaRPCError(RuntimeError):
    def __init__(self, rpc_code):
        super().__init__('Cua Driver MCP từ chối yêu cầu')
        self.code = 'mcp_invalid_arguments' if rpc_code == -32602 else 'mcp_rpc_error'


def executable(root):
    for path in (Path(root) / 'cua-driver.exe',
                 Path(root).parent / 'build' / 'cua-runtime' / 'cua-driver.exe'):
        if path.is_file():
            return str(path.resolve())
    found = shutil.which('cua-driver')
    if found:
        return found
    raise RuntimeError('Thiếu Cua Driver MCP; chạy scripts/setup_cua.py rồi build lại.')


class CuaMCP:
    def __init__(self, root, cancel, timeout=40):
        self.root, self.cancel, self.timeout = root, cancel, timeout
        self.process = None
        self.events = queue.Queue()
        self.counter = 0
        self.tools = {}
        self.cursor_session = 'clipboardai-' + uuid.uuid4().hex[:12]
        self.arrow_overlay = None
        self.cursor_style = 'cua'
        self.cursor_snapshot = None
        self.cursor_target = None

    def __enter__(self):
        environment = os.environ.copy()
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'DEEPSEEK_API_KEY',
                     'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
            environment.pop(name, None)
        self.process = subprocess.Popen([executable(self.root), 'mcp'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request('initialize', {'protocolVersion': '2024-11-05',
                'capabilities': {}, 'clientInfo': {'name': 'ClipboardAI', 'version': '3.1'}})
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            cursor = None
            for _ in range(10):
                page = self.request('tools/list', {'cursor': cursor} if cursor else {})
                for tool in page.get('tools', []):
                    self.tools[tool['name']] = tool
                cursor = page.get('nextCursor')
                if not cursor:
                    break
            self._configure_cursor()
            return self
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_RECORD + 1)
                if not line:
                    break
                if len(line) > MAX_RECORD:
                    raise ValueError('MCP record too large')
                self.events.put(json.loads(line))
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self.events.put(None)

    def send(self, message):
        if self.cancel.is_set():
            raise InterruptedError('Đã dừng agent')
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8'))
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise RuntimeError('Cua Driver MCP đã đóng kết nối') from None

    def request(self, method, params, timeout=None):
        self.counter += 1
        ident = self.counter
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise InterruptedError('Đã dừng agent')
            try:
                event = self.events.get(timeout=.05)
            except queue.Empty:
                continue
            if event is None:
                raise RuntimeError('Cua Driver MCP ngắt kết nối')
            if event.get('id') != ident or 'method' in event:
                # No server-initiated sampling or elicitation is supported.
                if 'id' in event and 'method' in event:
                    self.send({'jsonrpc': '2.0', 'id': event['id'],
                               'error': {'code': -32601, 'message': 'Unsupported client method'}})
                continue
            if 'error' in event:
                error = event.get('error')
                raise CuaRPCError(error.get('code') if isinstance(error, dict) else None)
            result = event.get('result')
            if not isinstance(result, dict):
                raise RuntimeError('Cua Driver MCP trả dữ liệu không hợp lệ')
            return result
        # A timed-out action can already have reached the OS. Never replay it.
        raise TimeoutError('Cua MCP quá thời gian; đã dừng, kiểm tra trang trước khi F2 lại')

    def call(self, name, arguments):
        if name not in self.tools:
            raise RuntimeError('Cua Driver thiếu công cụ ' + name)
        arguments = dict(arguments)
        properties = self.tools[name].get('inputSchema', {}).get('properties', {})
        if 'session' in properties:
            arguments.setdefault('session', self.cursor_session)
        if name == 'get_window_state' and 'max_image_dimension' in properties:
            # Pinned Cua scroll coordinates use the native bitmap, so keep
            # observations at native size instead of a configured resize.
            arguments['max_image_dimension'] = 0
        point = None
        if self.arrow_overlay:
            try:
                point = self._cursor_point(arguments) if name in ('click', 'scroll', 'type_text') else None
                # Keep our visual out of all native input and captures. The
                # owner thread acknowledges that its HWND is hidden first.
                if self.arrow_overlay.hide(wait=True) is False:
                    raise RuntimeError('Native arrow did not hide')
            except Exception:
                # Pointer rendering is optional; never fail/replay OS input.
                arrow, self.arrow_overlay = self.arrow_overlay, None
                self.cursor_style = 'hidden'
                try:
                    arrow.close()
                except Exception:
                    pass
        result = self.request('tools/call', {'name': name, 'arguments': arguments})
        if point and self.arrow_overlay and not result.get('isError'):
            try:
                self.arrow_overlay.update(*point)
            except Exception:
                arrow, self.arrow_overlay = self.arrow_overlay, None
                self.cursor_style = 'hidden'
                try:
                    arrow.close()
                except Exception:
                    pass
        if name == 'get_window_state':
            self.cursor_snapshot = result.get('structuredContent') if not result.get('isError') else None
            self.cursor_target = (arguments.get('pid'), arguments.get('window_id'))
        return result

    def _configure_cursor(self):
        if os.name != 'nt' or not all(n in self.tools for n in (
                'start_session', 'set_agent_cursor_enabled', 'get_agent_cursor_state')):
            return
        started = self.call('start_session', {'session': self.cursor_session})
        if started.get('isError'):
            raise RuntimeError('Không tạo được phiên con trỏ agent')
        result = self.call('set_agent_cursor_enabled', {'session': self.cursor_session, 'enabled': False})
        if result.get('isError') or result.get('structuredContent', {}).get('enabled') is not False:
            raise RuntimeError('Không tắt được hiệu ứng con trỏ Cua')
        # The setter acknowledges queueing, not painting. Wait for the owning
        # Cua renderer to apply it before permitting any agent input.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = self.request('tools/call', {'name': 'get_agent_cursor_state',
                'arguments': {'session': self.cursor_session}},
                timeout=min(self.timeout, max(.01, deadline - time.monotonic())))
            if not state.get('isError') and state.get('structuredContent', {}).get('enabled') is False:
                break
            if self.cancel.wait(.03):
                raise InterruptedError('Đã dừng agent')
        else:
            raise RuntimeError('Cua chưa xác nhận tắt hiệu ứng con trỏ; thử F2 lại')
        self.cursor_style = 'hidden'
        arrow = None
        try:
            from agent_cursor_overlay import NativeArrowOverlay
            arrow = NativeArrowOverlay()
            if arrow.available:
                self.arrow_overlay = arrow
                self.cursor_style = 'windows-arrow'
            else:
                arrow.close()
        except Exception:
            # Cua effects remain disabled even if the native renderer fails.
            if arrow:
                try:
                    arrow.close()
                except Exception:
                    pass

    def _cursor_point(self, arguments):
        """Render the observed target, never the disabled driver's stale position."""
        data, target = self.cursor_snapshot, arguments.get('target') or {}
        if not isinstance(data, dict) or self.cursor_target != (target.get('pid'), target.get('window_id')):
            return None
        if arguments.get('capture_id') and arguments['capture_id'] != data.get('capture_id'):
            return None
        elements = data.get('elements') or []
        def rect(value):
            return (isinstance(value, dict) and all(type(value.get(k)) in (int, float)
                    and math.isfinite(value[k]) for k in ('x', 'y', 'w', 'h'))
                    and value['w'] > 0 and value['h'] > 0)
        token = arguments.get('element_token')
        if token:
            frame = next((node.get('frame') for node in elements if node.get('element_token') == token), None)
            if rect(frame):
                return frame['x'] + frame['w'] / 2, frame['y'] + frame['h'] / 2
            return None
        x, y = arguments.get('x'), arguments.get('y')
        width, height = data.get('screenshot_width'), data.get('screenshot_height')
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (x, y, width, height)) or not (
                0 <= x < width and 0 <= y < height):
            return None
        pairs = [(node['frame'], node['screenshot_frame']) for node in elements
                 if rect(node.get('frame')) and rect(node.get('screenshot_frame'))]
        if not pairs or data.get('screenshot_frame_valid') is False:
            return None
        # Cua crops DWM borders, so window_bounds alone has the wrong origin.
        # Paired desktop/screenshot frames carry the actual crop and resize.
        frame, picture = max(pairs, key=lambda pair: pair[1]['w'] * pair[1]['h'])
        sx, sy = frame['w'] / picture['w'], frame['h'] / picture['h']
        ox, oy = frame['x'] - picture['x'] * sx, frame['y'] - picture['y'] * sy
        if any(abs((f['x'] - ox) / sx - p['x']) > 1 or
               abs((f['y'] - oy) / sy - p['y']) > 1 or
               abs(f['w'] / sx - p['w']) > 1 or abs(f['h'] / sy - p['h']) > 1 for f, p in pairs):
            return None
        return ox + x * sx, oy + y * sy

    def interrupt(self):
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def close(self):
        if self.arrow_overlay:
            arrow, self.arrow_overlay = self.arrow_overlay, None
            try:
                arrow.close()
            except Exception:
                pass
        process = self.process
        if not process:
            return
        try:
            process.stdin.close()
            process.wait(timeout=1)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.interrupt()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        finally:
            process.stdout.close()

    def __exit__(self, *_):
        self.close()
