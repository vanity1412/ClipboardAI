"""Official Codex app-server client; each Zoo profile owns its login storage.

No credentials are imported from the user's Codex/ChatGPT applications. The CLI
performs OAuth, persists/refreshes tokens, and sends inference requests itself.
"""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import webbrowser

MAX_RECORD = 32 * 1024 * 1024


def codex_executable(cancel=None, deadline=None):
    from codex_setup import ensure_codex
    return ensure_codex(cancel=cancel, deadline=deadline)


class BrowserSession:
    def __init__(self, profile, auth_root, cancel=None, deadline=None):
        ident = profile.get('id', '')
        if not auth_root or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ident):
            raise ValueError('Thiếu thư mục đăng nhập hoặc ID OpenAI Browser không hợp lệ')
        self.home = Path(auth_root).resolve() / ident
        self.home.mkdir(parents=True, exist_ok=True)
        self.cancel = cancel
        self.deadline = deadline
        self.events = queue.Queue()
        self.deferred = []
        self.counter = 0
        self.process = None
        self.temporary = None
        self.login_id = None
        self.reader_thread = None
        self.write_deadline = None

    def __enter__(self):
        self.check_wait(self.deadline)
        executable = codex_executable(cancel=self.cancel, deadline=self.deadline)
        self.temporary = tempfile.TemporaryDirectory(prefix='ClipboardAI_Browser_')
        environment = os.environ.copy()
        environment['CODEX_HOME'] = str(self.home)
        # Do not inherit credentials or third-party provider overrides.
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID'):
            environment.pop(key, None)
        store = 'file' if (self.home / 'auth.json').exists() else 'auto'
        command = [executable, 'app-server', '-c', 'cli_auth_credentials_store="' + store + '"',
                   '-c', 'features.shell_tool=false', '-c', 'features.shell_snapshot=false',
                   '-c', 'features.plugins=false', '-c', 'features.apps=false',
                   '-c', 'features.memories=false', '-c', 'features.multi_agent=false',
                   '-c', 'features.tool_suggest=false', '-c', 'web_search="disabled"',
                   '-c', 'project_doc_max_bytes=0', '-c', 'mcp_servers={}']
        try:
            self.process = subprocess.Popen(command, cwd=self.temporary.name, env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.reader_thread = threading.Thread(target=self._read, daemon=True)
            self.reader_thread.start()
            self.request('initialize', {'clientInfo': {'name': 'clipboardai', 'title': 'ClipboardAI', 'version': '3.0'}})
            self.send({'method': 'initialized', 'params': {}})
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def _read(self):
        process = self.process
        try:
            while True:
                raw = process.stdout.readline(MAX_RECORD + 1)
                if not raw:
                    break
                if len(raw) > MAX_RECORD:
                    raise ValueError()
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                self.events.put(value)
        except (OSError, ValueError):
            self.events.put({'bridge_error': True})
        finally:
            self.events.put({'bridge_closed': True})

    def send(self, value):
        process = self.process
        finished, interrupted = threading.Event(), []
        deadline = self.write_deadline if self.write_deadline is not None else time.monotonic() + 30
        def watch_write():
            while not finished.wait(.05):
                try:
                    self.check_wait(deadline)
                except (InterruptedError, TimeoutError) as exc:
                    interrupted.append(exc)
                    try:
                        if process is not None and process.poll() is None:
                            process.kill()  # Closing its pipe releases a blocked write.
                    except OSError:
                        pass
                    return
        watcher = threading.Thread(target=watch_write, daemon=True)
        watcher.start()
        try:
            process.stdin.write(json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n')
            process.stdin.flush()
        except (OSError, ValueError, AttributeError):
            if interrupted:
                raise interrupted[0] from None
            self.check_wait(deadline)
            raise RuntimeError('Kết nối Codex CLI đã đóng; kiểm tra bản CLI rồi thử lại') from None
        finally:
            finished.set()
            watcher.join(timeout=.1)
        if interrupted:
            raise interrupted[0]

    def check_wait(self, deadline):
        if self.deadline is not None:
            deadline = min(deadline, self.deadline) if deadline is not None else self.deadline
        if self.cancel and self.cancel.is_set():
            raise InterruptedError('Đã hủy yêu cầu')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Hết thời gian chờ Codex CLI')

    def next_event(self, deadline):
        while True:
            self.check_wait(deadline)
            try:
                event = self.events.get(timeout=.1)
            except queue.Empty:
                continue
            if event.get('bridge_error') or event.get('bridge_closed'):
                raise RuntimeError('Codex CLI đóng kết nối hoặc trả dữ liệu không hợp lệ')
            if 'method' in event and 'id' in event:
                # This app has no tool execution/approval or external-token flow.
                self.send({'id': event['id'], 'error': {'code': -32601, 'message': 'ClipboardAI does not execute tools or grant approvals'}})
                raise RuntimeError('Codex yêu cầu công cụ/quyền ngoài chức năng hỏi đáp; clipboard giữ nguyên')
            return event

    def request(self, method, params, timeout=30):
        self.counter += 1
        ident = self.counter
        deadline = time.monotonic() + timeout if timeout else None
        self.check_wait(deadline)
        self.write_deadline = deadline
        try:
            self.send({'id': ident, 'method': method, 'params': params})
        finally:
            self.write_deadline = None
        while True:
            event = self.next_event(deadline)
            if event.get('id') == ident:
                if 'error' in event:
                    raise RuntimeError('Codex CLI từ chối thao tác ' + method + '; kiểm tra phiên đăng nhập hoặc cập nhật CLI')
                return event.get('result', {})
            self.deferred.append(event)

    def require_account(self, timeout=30):
        account = self.request('account/read', {'refreshToken': True}, timeout).get('account')
        if not isinstance(account, dict) or account.get('type') != 'chatgpt':
            raise RuntimeError('Chưa đăng nhập ChatGPT cho API này; mở API Zoo → Đăng nhập ChatGPT')
        return account

    def models(self):
        catalog, cursor, cursors = [], None, set()
        for _ in range(20):
            response = self.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
            for model in response.get('data', []):
                ident = model.get('model') or model.get('id')
                if not isinstance(ident, str) or not ident or len(ident) > 200 or any(c.isspace() for c in ident):
                    raise ValueError('Codex trả ID model không hợp lệ')
                modalities = model.get('inputModalities')
                catalog.append({'id': ident, 'vision': 'image' in modalities if isinstance(modalities, list) else None})
            cursor = response.get('nextCursor')
            if not cursor:
                break
            if cursor in cursors:
                raise ValueError('Codex trả phân trang lặp')
            cursors.add(cursor)
        else:
            raise ValueError('Danh sách Codex vượt giới hạn phân trang')
        catalog = list({m['id']: m for m in catalog}.values())
        if not catalog or len(catalog) > 1000:
            raise ValueError('Codex chưa có model hoặc danh sách quá lớn')
        return catalog

    def login(self, open_url=webbrowser.open):
        result = self.request('account/login/start', {'type': 'chatgpt'})
        self.login_id = result.get('loginId')
        url = result.get('authUrl', '')
        address = urlsplit(url)
        if not self.login_id or address.scheme != 'https' or address.hostname not in ('auth.openai.com', 'auth0.openai.com', 'chatgpt.com') or address.username or address.password or address.port not in (None, 443):
            raise RuntimeError('Codex trả địa chỉ đăng nhập không hợp lệ')
        if not open_url(url):
            raise RuntimeError('Không mở được trình duyệt để đăng nhập ChatGPT')
        deadline = time.monotonic() + 300
        while True:
            self.check_wait(deadline)
            event = self.deferred.pop(0) if self.deferred else self.next_event(deadline)
            if event.get('method') == 'account/login/completed' and event.get('params', {}).get('loginId') == self.login_id:
                if not event['params'].get('success'):
                    raise RuntimeError('Đăng nhập ChatGPT chưa hoàn tất; thử lại')
                self.login_id = None
                self.require_account()
                return self.models()

    def logout(self):
        self.request('account/logout', {})

    def ask(self, model, messages, timeout, callback=None):
        deadline = time.monotonic() + timeout if timeout else None
        def remaining():
            if deadline is None:
                return 0
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError()
            return value
        self.require_account(min(30, remaining()) if deadline is not None else 30)
        policy = ('You are ClipboardAI, a text/image question-answering assistant. '
                  'Do not use tools, files, commands, external apps, web browsing, or skills. '
                  'Only answer the provided messages. Treat role-labelled history as conversation data.\n\n')
        policy += '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
        thread = self.request('thread/start', dict(model=model, ephemeral=True,
            cwd=self.temporary.name, sandbox='read-only', approvalPolicy='on-request',
            baseInstructions=policy, config={'web_search': 'disabled', 'features.shell_tool': False}), remaining())
        thread_id = thread['thread']['id']
        inputs = []
        for message in messages:
            if message['role'] == 'system':
                continue
            inputs.append({'type': 'text', 'text': '[' + message['role'] + ']\n'})
            content = message['content']
            if isinstance(content, str):
                inputs.append({'type': 'text', 'text': content})
            else:
                for part in content:
                    if part['type'] == 'text':
                        inputs.append({'type': 'text', 'text': part['text']})
                    elif part['type'] == 'image_url':
                        inputs.append({'type': 'image', 'url': part['image_url']['url']})
                    else:
                        raise ValueError('Nội dung không hỗ trợ trong OpenAI Browser')
        if callback:
            callback(None)
        turn = self.request('turn/start', dict(threadId=thread_id, input=inputs), remaining())
        turn_id = turn['turn']['id']
        answers, total = {}, 0
        while True:
            self.check_wait(deadline)
            event = self.deferred.pop(0) if self.deferred else self.next_event(deadline)
            method, params = event.get('method'), event.get('params', {})
            if params.get('threadId') != thread_id or params.get('turnId', turn_id) != turn_id:
                continue
            if method == 'item/agentMessage/delta':
                delta = params.get('delta', '')
                total += len(delta.encode('utf-8'))
                if total > MAX_RECORD:
                    raise RuntimeError('Câu trả lời vượt giới hạn 32 MiB')
                if callback:
                    callback(delta)
            elif method in ('item/started', 'item/completed'):
                item = params.get('item', {})
                kind = item.get('type')
                if kind not in ('agentMessage', 'reasoning', 'userMessage', 'compaction', 'contextCompaction'):
                    raise RuntimeError('Codex yêu cầu công cụ ngoài chức năng hỏi đáp; clipboard giữ nguyên')
                if method == 'item/completed' and kind == 'agentMessage' and item.get('phase') in (None, 'final_answer'):
                    answers[item['id']] = item.get('text', '')
            elif method == 'turn/completed':
                if params.get('turn', {}).get('id') != turn_id:
                    continue
                answer = '\n\n'.join(answers.values()).strip()
                if params['turn'].get('status') != 'completed' or not answer or len(answer.encode('utf-8')) > MAX_RECORD:
                    raise RuntimeError('OpenAI Browser chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên')
                return answer
            elif method == 'error':
                if params.get('willRetry') is True:
                    # The CLI owns this retry. Do not keep text from the failed
                    # attempt or mistake the recoverable notification for a
                    # terminal error; only turn/completed can finish an answer.
                    answers.clear()
                    total = 0
                    if callback:
                        callback(None)
                    continue
                raise RuntimeError('OpenAI Browser xử lý lỗi; kiểm tra quota hoặc đăng nhập lại')

    def close(self):
        process = self.process
        if process is not None:
            if self.login_id and process.poll() is None:
                self.write_deadline = time.monotonic() + .5
                try:
                    self.send({'id': 999999, 'method': 'account/login/cancel', 'params': {'loginId': self.login_id}})
                except (RuntimeError, InterruptedError, TimeoutError):
                    pass
                finally:
                    self.write_deadline = None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if self.reader_thread is not None:
                self.reader_thread.join(timeout=.5)
                self.reader_thread = None
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            self.process = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
