"""Native F2 + real Cua MCP on an isolated Chrome quiz fixture.

The default model is synthetic. ``run(output, model='live', config_root='dist')``
uses the configured CloudClient and sends only this generated fixture. Paging
is the default; ``layout='long'`` independently verifies answering and scrolling
all the way through one long page. Both paths have bounded model calls and time,
and neither accesses the clipboard or the user's conversation archive.
"""
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import queue
import re
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

from cua_mcp import CuaMCP
from desktop_agent import DesktopAgent, structured, tokens_in


# Only exact, fixed host validation messages may enter a live diagnostic report.
# Arbitrary provider/MCP exception bodies are never recorded.
HOST_FAILURE_MESSAGES = {
    'Agent trả sai JSON; chưa thực hiện thao tác': 'decision_json',
    'Agent trả quyết định không hợp lệ': 'decision_shape',
    'Agent yêu cầu thao tác không được hỗ trợ': 'decision_tool',
    'Agent yêu cầu đổi cửa sổ đích; đã dừng': 'target_override',
    'Agent trả tham số ngoài schema Cua MCP': 'arguments_schema',
    'Chế độ trắc nghiệm chỉ dùng thao tác đơn không kèm phím bổ trợ': 'arguments_modifiers',
    'Phần tử không thuộc ảnh mới nhất; đã dừng': 'stale_element',
    'Đã đến nút nộp bài; bạn kiểm tra và tự nộp': 'submit_guard',
    'Agent yêu cầu thao tác ngoài nội dung đề': 'content_scope',
    'Phím agent yêu cầu không được dùng cho chế độ trắc nghiệm': 'key_guard',
    'Nội dung điền đáp án không hợp lệ': 'text_guard',
    'Click cần tọa độ trong ảnh mới nhất': 'coordinate_input',
    'Tọa độ nằm ngoài cửa sổ': 'coordinate_bounds',
    'Trang chuyển sang đăng nhập/cài đặt; đã dừng agent': 'login_guard',
    'Website đã đổi; agent dừng, chọn lại đề rồi F2': 'origin_guard',
    'Không xác định duy nhất cửa sổ F2; chọn lại cửa sổ rồi thử': 'target_resolution',
    'Cua MCP không cung cấp công cụ thao tác': 'tools_missing',
    'Cua MCP chưa đọc được cửa sổ': 'snapshot_empty',
    'Cua MCP thiếu công cụ agent chọn': 'tool_missing',
    'Cua Driver từ chối thao tác; đã dừng agent': 'mcp_action_rejected',
    'Cua Driver không hoàn tất thao tác; đã dừng agent': 'mcp_action_failed',
    'Agent đạt giới hạn 120 bước; kiểm tra trang rồi F2 lại': 'step_limit',
    'Agent đã đạt giới hạn 15 phút; kiểm tra trang rồi F2 lại': 'time_limit',
}


def fixture_decision_diagnostic(answer):
    """Bounded model action diagnostics from the generated fixture only."""
    import desktop_agent
    try:
        value = desktop_agent.decision(answer)
    except (RuntimeError, TypeError, ValueError, AttributeError):
        return {'parse_failed': True}
    result = {'tool': str(value.get('tool', ''))[:64], 'done': value.get('done') is True}
    arguments = value.get('arguments', {})
    if not isinstance(arguments, dict):
        return result
    result['arguments_keys'] = [str(key)[:64] for key in list(arguments)[:24]]
    scalars, omitted = {}, {}
    for key, item in list(arguments.items())[:24]:
        name = str(key)[:64]
        if any(secret in name.casefold().replace('_', '')
               for secret in ('apikey', 'password', 'credential', 'authorization', 'accesstoken', 'refreshtoken')):
            continue
        if item is None or isinstance(item, bool):
            scalars[name] = item
        elif isinstance(item, (int, float)) and abs(item) <= 1e12 and math.isfinite(item):
            scalars[name] = item
        elif isinstance(item, str):
            scalars[name] = item[:180]
        else:
            omitted[name] = type(item).__name__
    result['arguments'] = scalars
    if omitted:
        result['omitted_argument_types'] = omitted
    return result

HTML = '''<!doctype html><html><head><title>ClipboardAI Cua Quiz Fixture</title>
<style>body{font:22px Segoe UI;padding:30px}label{display:block;padding:12px}
button{font:22px Segoe UI;margin:20px;padding:12px}</style></head>
<body><h1>ClipboardAI Cua Quiz Fixture</h1><h2 id="q"></h2><div id="answers"></div>
<button id="next" onclick="next()">Next question</button>
<button onclick="fetch('/state',{method:'POST',body:JSON.stringify({submit:true})})">Submit quiz</button>
<script>let page=1;const total=__TOTAL__; function report(answer){fetch('/state',{method:'POST',body:JSON.stringify({page,answer})})}
function draw(){document.getElementById('q').textContent='Question '+page+' of '+total+': '+(page%2?'2 + 2 = ?':'6 * 7 = ?');
document.getElementById('answers').innerHTML=(page%2?['A. 3','B. 4','C. 5']:['A. 40','B. 42','C. 43'])
.map(x=>`<label><input type="radio" name="answer" aria-label="${x}" onchange="report('${x}')">${x}</label>`).join('');
document.getElementById('next').style.display=page<total?'inline-block':'none';
requestAnimationFrame(()=>{let points={}; for(let e of document.querySelectorAll('label,button')){
let r=e.getBoundingClientRect();points[e.textContent]={x:r.x+r.width/2,y:r.y+r.height/2};}
fetch('/state',{method:'POST',body:JSON.stringify({layout:points,scale:devicePixelRatio,
top:outerHeight-innerHeight})})});}
function next(){page++;draw();report(null)} draw();</script></body></html>'''


LONG_HTML = '''<!doctype html><html><head><title>ClipboardAI Cua Quiz Fixture</title>
<style>body{font:22px Segoe UI;margin:0;padding:30px;box-sizing:border-box}
h1{font-size:27px}h2{font-size:24px}section{border-top:1px solid #ccc;padding-top:20px}
section:not(:last-of-type){min-height:88vh}label{display:block;padding:12px}
button{font:22px Segoe UI;padding:12px;margin-top:25px}#end{margin:20px 0}</style></head>
<body><h1>Quiz: __TOTAL__ questions on this page</h1><div id="questions"></div>
<button onclick="fetch('/state',{method:'POST',body:JSON.stringify({submit:true})})">Submit quiz</button>
<p id="end">End of quiz: __TOTAL__ questions</p>
<script>const total=__TOTAL__;
document.getElementById('questions').innerHTML=Array.from({length:total},(_,i)=>{
let n=i+1,choices=n%2?['A. 3','B. 4','C. 5']:['A. 40','B. 42','C. 43'];
return `<section><h2>Question ${n} of ${total}: ${n%2?'2 + 2 = ?':'6 * 7 = ?'}</h2>`+
choices.map(x=>`<label><input type="radio" name="question${n}" aria-label="Question ${n}: ${x}" onchange="answer(${n},'${x}')">${x}</label>`).join('')+'</section>'}).join('');
function answer(page,answer){fetch('/state',{method:'POST',body:JSON.stringify({page,answer})})}
function reportScroll(event){let maxScroll=Math.max(0,document.documentElement.scrollHeight-innerHeight);
fetch('/state',{method:'POST',body:JSON.stringify({scroll:true,event,scrollY,maxScroll,
viewportHeight:innerHeight,documentHeight:document.documentElement.scrollHeight})})}
let pending=false;addEventListener('scroll',()=>{if(!pending){pending=true;
requestAnimationFrame(()=>{pending=false;reportScroll(true)})}});
requestAnimationFrame(()=>reportScroll(false));</script></body></html>'''


def run(output, model='fixture', config_root=None, require_foreground=True, questions=2,
        layout='paging', max_seconds=150):
    if model not in ('fixture', 'live'):
        raise ValueError("model must be 'fixture' or 'live'")
    if type(questions) is not int or not 2 <= questions <= 6:
        raise ValueError('Fixture questions must be between two and six')
    if layout not in ('paging', 'long'):
        raise ValueError("layout must be 'paging' or 'long'")
    if type(max_seconds) not in (int, float) or not 30 <= max_seconds <= 300:
        raise ValueError('Fixture max_seconds must be between 30 and 300')
    import windows_native as native
    import cloud_client
    import desktop_agent
    from api_zoo import selected_profile
    from default_config import load_defaults
    from request_display import RequestDisplay
    from runtime_settings import load_runtime_settings
    from session_state import Session

    output = Path(output).resolve()
    configuration_root = Path(config_root or output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + max_seconds
    cancel = threading.Event()
    state = {'page': 1, 'answers': {}, 'submitted': False, 'scroll_events': 0,
             'scroll_y': 0, 'max_scroll_y': 0, 'max_scroll': 0, 'reached_bottom': False}
    report = {'ok': False, 'real_api_calls': 0, 'clipboard_access': False,
              'private_browser_profile': False, 'driver': 'Cua Driver MCP 0.34.1',
              'model': model, 'model_calls': 0,
              'max_model_calls': min(40, 5 * questions + 8) if layout == 'long' else 2 * questions + 4,
              'questions': questions, 'layout': layout, 'scroll_tool_calls': 0,
              'max_seconds': max_seconds, 'native_hotkey': False,
              'native_start_desktop_agent': False, 'user_session_access': False,
              'model_decisions': []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            document = LONG_HTML if layout == 'long' else HTML
            self.wfile.write(document.replace('__TOTAL__', str(questions)).encode())

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if value.get('layout'):
                state.update(layout=value['layout'], scale=value['scale'], top=value['top'])
            elif value.get('scroll'):
                position, maximum = float(value['scrollY']), float(value['maxScroll'])
                state['scroll_y'] = position
                state['max_scroll_y'] = max(state['max_scroll_y'], position)
                state['max_scroll'] = maximum
                state['scroll_events'] += value.get('event') is True
                state['reached_bottom'] |= maximum > 0 and position >= maximum - 2
                state.update(viewport_height=value['viewportHeight'], document_height=value['documentHeight'])
            elif value.get('submit'):
                state['submitted'] = True
            else:
                state['page'] = value['page']
                if value.get('answer'):
                    state['answers'][str(value['page'])] = value['answer']
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser, app, owner, fixture_hwnd, session = None, None, None, None, None
    registered = False
    user, kernel, shell = native.setup_winapi()
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                 wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user.PeekMessageW.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    previous = user.GetForegroundWindow()
    folder = tempfile.TemporaryDirectory(prefix='ClipboardAI_CuaFixture_')
    captured_events, callback_events = [], []
    hotkey_callbacks = []
    original_client = cloud_client.CloudClient
    original_agent = desktop_agent.DesktopAgent

    def pump():
        message = wintypes.MSG()
        while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            if message.message != 0x12:
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))

    def trace(event, **fields):
        # Store event names/types only; provider messages/configuration can
        # contain credentials and are deliberately excluded from this report.
        captured_events.append({'event': event, **{
            key: value for key, value in fields.items()
            if key in ('error_type', 'error_code', 'attempt', 'phase', 'request_id', 'key', 'step', 'tool')
            and isinstance(value, (str, int, bool))}})

    def interrupt():
        cancel.set()
        agent = getattr(app, 'desktop_agent', None) if app else None
        if agent:
            agent.interrupt()

    watchdog = threading.Timer(max_seconds, interrupt)
    watchdog.daemon = True
    watchdog.start()
    try:
        config = cloud_client.load_cloud_config(configuration_root, load_defaults(configuration_root))
        load_runtime_settings(config, configuration_root / 'preferences.json')
        profile = selected_profile(config)
        if profile:
            report.update(provider=profile['provider'], configured_model=profile['model'],
                          configured_vision_model=profile['vision_model'])
        if model == 'live' and profile is None:
            raise RuntimeError('Live fixture requires a configured API Zoo profile')
        chrome = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not chrome.exists():
            raise RuntimeError('Chrome chưa cài; không chạy được fixture browser')
        browser = subprocess.Popen([str(chrome), '--user-data-dir=' + folder.name,
            '--no-first-run', '--no-default-browser-check', '--force-renderer-accessibility',
            '--disable-background-mode', '--disable-sync', '--disable-features=SigninPromo',
            '--app=http://127.0.0.1:' + str(server.server_port),
            '--window-size=850,700'], creationflags=subprocess.CREATE_NO_WINDOW)
        report['private_browser_profile'] = True
        title = 'ClipboardAI Cua Quiz Fixture'
        with CuaMCP(configuration_root, cancel) as mcp:
            browser_deadline = min(deadline, time.monotonic() + 30)
            while time.monotonic() < browser_deadline:
                windows = structured(mcp.call('list_windows', {})).get('windows', [])
                chosen = [w for w in windows if title in str(w.get('title', ''))]
                if len(chosen) == 1:
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Không tìm được cửa sổ Chrome fixture')
            window = chosen[0]
            pid = window.get('pid', window.get('owner_pid'))
            if not pid:
                raise RuntimeError('Cua list_windows không trả pid fixture')
            fixture_hwnd = window['window_id']
            if not isinstance(fixture_hwnd, int):
                raise RuntimeError('Fixture window_id is not a native HWND')
            actual_pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(fixture_hwnd, ctypes.byref(actual_pid))
            if actual_pid.value != pid:
                raise RuntimeError('Fixture HWND does not belong to its reported Chrome pid')
            capture = mcp.call('get_window_state', {'pid': pid, 'window_id': window['window_id'],
                'include_accessibility_tree': True, 'include_screenshot': True, 'timeout_ms': 5000})
            (output / 'agent-fixture-state.json').write_text(
                json.dumps(structured(capture), ensure_ascii=False, indent=2), encoding='utf-8')
            user.SetForegroundWindow(fixture_hwnd)
            if user.GetForegroundWindow() != fixture_hwnd:
                # This harmless click is constrained to the owned fixture's
                # heading, and gives the native F2 handler a real foreground HWND.
                args = {'target': {'kind': 'window', 'pid': pid, 'window_id': fixture_hwnd},
                        'delivery_mode': 'foreground', 'x': 45, 'y': 145}
                properties = mcp.tools['click'].get('inputSchema', {}).get('properties', {})
                capture_id = structured(capture).get('capture_id')
                if capture_id and 'capture_id' in properties:
                    args['capture_id'] = capture_id
                point = mcp._cursor_point(args)
                user.WindowFromPoint.argtypes, user.WindowFromPoint.restype = [wintypes.POINT], wintypes.HWND
                user.GetAncestor.argtypes, user.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
                hit = user.WindowFromPoint(wintypes.POINT(round(point[0]), round(point[1]))) if point else None
                if hit and user.GetAncestor(hit, 2) == fixture_hwnd:
                    activation = mcp.call('click', args)
                    report['fixture_activation_result'] = {
                        key: value for key, value in structured(activation, allow_background_refusal=True).items()
                        if key in ('code', 'error_code', 'effect', 'verified')}
                else:
                    report['fixture_activation_skipped_covered'] = True
            focus_deadline = min(deadline, time.monotonic() + 1)
            while user.GetForegroundWindow() != fixture_hwnd and time.monotonic() < focus_deadline:
                pump()
                time.sleep(.02)
            if user.GetForegroundWindow() != fixture_hwnd:
                # A background-launched test may lack foreground activation
                # rights. Temporarily share the input queue, focus only our own
                # Chrome HWND, and immediately detach the thread queues again.
                kernel.GetCurrentThreadId.argtypes, kernel.GetCurrentThreadId.restype = [], wintypes.DWORD
                user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
                user.AttachThreadInput.restype = wintypes.BOOL
                current_thread = kernel.GetCurrentThreadId()
                foreground_thread = user.GetWindowThreadProcessId(user.GetForegroundWindow(), None)
                attached = foreground_thread != current_thread and bool(
                    user.AttachThreadInput(current_thread, foreground_thread, True))
                try:
                    user.SetForegroundWindow(fixture_hwnd)
                    pump()
                finally:
                    if attached:
                        user.AttachThreadInput(current_thread, foreground_thread, False)
                report['fixture_focus_input_queue_attached'] = attached
            if user.GetForegroundWindow() != fixture_hwnd and require_foreground:
                raise RuntimeError('Owned fixture could not become the foreground F2 target')
            report['fixture_foreground_hwnd'] = fixture_hwnd
            report['foreground_read_stubbed'] = user.GetForegroundWindow() != fixture_hwnd
        class FixtureModel:
            def cancel(self):
                pass

            def long_decision(self, snapshot):
                # Controlled decisions use only the same observation delivered
                # to the live model, never the server's independent answer state.
                elements = list(tokens_in(snapshot).values())
                documents = [e for e in elements if e.get('role') == 'Document'
                             and e.get('screenshot_frame')]
                if not documents:
                    raise RuntimeError('Long fixture has no visible Document viewport')
                document = max(documents, key=lambda e: e['screenshot_frame'].get('w', 0)
                               * e['screenshot_frame'].get('h', 0))
                viewport = document['screenshot_frame']
                def visible(element):
                    frame = element.get('screenshot_frame', {})
                    return (frame.get('w', 0) > 0 and frame.get('h', 0) > 0
                            and frame.get('x', -1) >= viewport['x']
                            and frame.get('y', -1) >= viewport['y']
                            and frame.get('x', -1) + frame.get('w', 0) <= viewport['x'] + viewport['w']
                            and frame.get('y', -1) + frame.get('h', 0) <= viewport['y'] + viewport['h'])
                for element in elements:
                    label = str(element.get('label', element.get('name', '')))
                    match = re.fullmatch(r'Question (\d+): B\. (4|42)', label)
                    if (match and element.get('role') == 'RadioButton'
                            and element.get('selected') is not True and visible(element)):
                        expected = '4' if int(match[1]) % 2 else '42'
                        if match[2] != expected:
                            raise RuntimeError('Controlled fixture answer does not match its arithmetic question')
                        if any(action in element.get('actions', []) for action in ('invoke', 'select', 'toggle')):
                            args = {'element_token': element['element_token']}
                        else:
                            frame = element['screenshot_frame']
                            args = {'x': frame['x'] + frame['w'] / 2, 'y': frame['y'] + frame['h'] / 2}
                        return {'tool': 'click', 'arguments': args,
                                'status': 'Fixture chọn ' + label}
                if any(str(e.get('label', e.get('name', ''))).startswith('End of quiz:')
                       and visible(e) for e in elements):
                    return {'done': True, 'summary': '; '.join('Câu ' + str(n) + ': B'
                            for n in range(1, questions + 1))}
                return {'tool': 'scroll', 'arguments': {'direction': 'down', 'by': 'page', 'amount': 1,
                        'x': viewport['x'] + viewport['w'] / 2,
                        'y': viewport['y'] + viewport['h'] / 2}, 'status': 'Fixture cuộn đến câu tiếp theo'}

            def ask(self, content, **kwargs):
                text = content[0]['text'].split('QUAN SÁT MỚI NHẤT:\n', 1)[1].split('\nCÔNG CỤ:', 1)[0]
                snapshot = json.loads(text)
                (output / 'agent-fixture-decision.json').write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
                if layout == 'long':
                    return json.dumps(self.long_decision(snapshot)), 'fixture'
                page = state['page']
                if str(page) not in state['answers']:
                    label = 'B. 4' if page % 2 else 'B. 42'
                elif page < questions:
                    label = 'Next question'
                else:
                    return json.dumps({'done': True, 'summary': '; '.join('Câu ' + str(n) + ': B'
                        for n in range(1, questions + 1))}), 'fixture'
                elements = [e for e in tokens_in(snapshot).values()
                            if e.get('label', e.get('name')) == label and 'invoke' in e.get('actions', [])]
                if len(elements) != 1:
                    point = state.get('layout', {}).get(label)
                    if not point:
                        raise RuntimeError('Fixture chưa hiển thị ' + label)
                    args = {'x': point['x'] * state['scale'],
                            'y': (point['y'] + state['top']) * state['scale']}
                else:
                    args = {'element_token': elements[0]['element_token']}
                return json.dumps({'tool': 'click', 'arguments': args,
                                   'status': 'Fixture chọn ' + label}), 'fixture'
        class BoundedModel:
            def __init__(self, configuration):
                self.delegate = original_client(configuration) if model == 'live' else FixtureModel()
                self.cancel_event, self.deadline = None, deadline

            def cancel(self):
                self.delegate.cancel()

            def ask(self, content, **kwargs):
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError('Synthetic fixture reached its configured time limit')
                if report['model_calls'] >= report['max_model_calls']:
                    raise RuntimeError('Synthetic fixture reached its model-call limit')
                report['model_calls'] += 1
                report['real_api_calls'] += model == 'live'
                self.delegate.cancel_event = self.cancel_event
                self.delegate.deadline = min(self.deadline, deadline)
                answer, provider = self.delegate.ask(content, **kwargs)
                report['model_decisions'].append(fixture_decision_diagnostic(answer))
                observed = getattr(self, 'fixture_observations', [])
                observed.append({'page': state['page'], 'answers': dict(state['answers']),
                                 'scroll_y': state['scroll_y'],
                                 'snapshot': json.loads(content[0]['text'].split('QUAN SÁT MỚI NHẤT:\n', 1)[1].split('\nCÔNG CỤ:', 1)[0])})
                self.fixture_observations = observed
                (output / 'fixture-model-observations.json').write_text(
                    json.dumps(observed, ensure_ascii=False), encoding='utf-8')
                if model == 'live':
                    # Only model final messages for this generated, confined
                    # fixture; never upstream exception bodies or user pages.
                    finals = getattr(self, 'fixture_finals', [])
                    finals.append(answer[:2000])
                    self.fixture_finals = finals
                    (output / 'fixture-model-finals.json').write_text(
                        json.dumps(finals, ensure_ascii=False, indent=2), encoding='utf-8')
                return answer, provider

        def guarded_mcp(root, agent_cancel):
            connection = CuaMCP(root, agent_cancel, timeout=min(40, max(.1, deadline - time.monotonic())))
            call = connection.call
            def confined_call(name, arguments):
                if name in ('list_windows', 'get_window_state'):
                    assert arguments.get('pid') == pid, 'Agent observed a process outside fixture'
                    if name == 'get_window_state':
                        assert arguments.get('window_id') == fixture_hwnd, 'Agent observed a window outside fixture'
                elif name in ('click', 'scroll', 'type_text', 'press_key'):
                    target = arguments.get('target', {})
                    assert target.get('pid') == pid and target.get('window_id') == fixture_hwnd, 'Agent targeted a window outside fixture'
                    if name == 'scroll':
                        report['scroll_tool_calls'] += 1
                elif name in ('start_session', 'set_agent_cursor_enabled', 'get_agent_cursor_state'):
                    assert arguments.get('session') == connection.cursor_session, 'Cursor setting targeted a different session'
                else:
                    raise RuntimeError('Fixture does not authorize this MCP tool')
                if name in ('click', 'scroll', 'type_text', 'press_key') and arguments.get('delivery_mode') == 'foreground':
                    if connection.arrow_overlay:
                        connection.arrow_overlay.hide(wait=True)
                    if name == 'scroll' and 'x' not in arguments:
                        bounds = (connection.cursor_snapshot or {}).get('window_bounds') or {}
                        point = (bounds.get('x', 0) + bounds.get('width', 0) / 2,
                                 bounds.get('y', 0) + bounds.get('height', 0) / 2)
                    else:
                        point = connection._cursor_point(arguments)
                    if point:
                        user.WindowFromPoint.argtypes, user.WindowFromPoint.restype = [wintypes.POINT], wintypes.HWND
                        user.GetAncestor.argtypes, user.GetAncestor.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
                        under_pointer = user.WindowFromPoint(wintypes.POINT(round(point[0]), round(point[1])))
                        covered = user.GetAncestor(under_pointer, 2) != fixture_hwnd
                    else:
                        covered = user.GetForegroundWindow() != fixture_hwnd
                    report['fixture_input_target_covered'] = covered
                    if covered:
                        raise RuntimeError('Owned fixture is covered; global input test stopped before dispatch')
                result = call(name, arguments)
                report['cursor_style'] = connection.cursor_style
                if name == 'set_agent_cursor_enabled':
                    report['cua_cursor_enabled'] = result.get('structuredContent', {}).get('enabled')
                if name == 'get_window_state':
                    raw = structured(result)
                    # The initial Chrome capture may precede content loading;
                    # preserve the last complete agent read for UIA diagnostics.
                    (output / 'fixture-last-raw-state.json').write_text(
                        json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
                    report['fixture_raw_radio_count'] = sum(
                        e.get('role') == 'RadioButton' for e in tokens_in(raw).values())
                if name in ('click', 'scroll', 'type_text', 'press_key'):
                    # Generated-fixture diagnostics only; preserve provider-
                    # independent route/effect and the observed target label.
                    feedback = structured(result, allow_background_refusal=True)
                    action = {'tool': name, 'delivery_mode': arguments.get('delivery_mode'),
                              'effect': feedback.get('effect'), 'route': feedback.get('route'),
                              'error_code': (feedback.get('error') or {}).get('code'),
                              'scroll_y_after_return': state['scroll_y'],
                              'answers_after_return': dict(state['answers'])}
                    for key in ('element_token', 'x', 'y', 'direction', 'by', 'amount'):
                        if key in arguments:
                            action[key] = arguments[key]
                    report.setdefault('fixture_actions', []).append(action)
                    arrow = connection.arrow_overlay
                    if arrow:
                        report['native_arrow_error'] = arrow.error_code
                        report['native_arrow_available'] = arrow.available
                        point = connection._cursor_point(arguments)
                        action['native_arrow_target'] = list(point) if point else None
                return result
            connection.call = confined_call
            return connection

        def make_agent(root, client, agent_cancel, progress, **kwargs):
            assert Path(root).resolve() == configuration_root
            def checked_progress(status):
                callback_events.append('progress')
                progress(status)
            agent = original_agent(root, client, agent_cancel, checked_progress,
                                   mcp_factory=guarded_mcp, **kwargs)
            agent.deadline = deadline
            report['native_start_desktop_agent'] = True
            return agent

        app = native.WindowsApp.__new__(native.WindowsApp)
        class SelectedFixtureUser:
            # Optional controlled selection for testing the agent loop when
            # Windows denies foreground activation to a background test runner.
            # All window APIs and Cua input still use the real owned fixture.
            def GetForegroundWindow(self):
                return fixture_hwnd
            def __getattr__(self, name):
                return getattr(user, name)
        app.user = SelectedFixtureUser() if report['foreground_read_stubbed'] else user
        app.kernel, app.shell = kernel, shell
        app.config = config
        app.results = queue.Queue()
        app.request_display = RequestDisplay()
        app.enabled = True
        app.self_test = True
        app.busy = app.network_busy = app.region_pending = app.exiting = False
        app.hotkey_open = False
        app.current_id = 0
        app.start_desktop_agent = lambda: native.WindowsApp.start_desktop_agent(app, client_factory=BoundedModel)
        app.notice = None
        app.notice_until = 0
        app.pending_write = None
        app.started = None
        app.tooltip = lambda status: callback_events.append('tooltip')
        session = app.session = Session(Path(folder.name) / 'synthetic-session.json')
        owner = user.CreateWindowExW(0, 'STATIC', 'ClipboardAI synthetic F2 owner',
            0x80000000, 0, 0, 1, 1, None, None, kernel.GetModuleHandleW(None), None)
        if not owner:
            raise RuntimeError('Could not create native fixture hotkey owner')
        app.hwnd = owner
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                          wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        old_proc = None
        def owner_proc(hwnd, message, wp, lp):
            if message == 0x312:
                hotkey_callbacks.append(int(wp))
                return app.window_proc(hwnd, message, wp, lp)
            return user.CallWindowProcW(ctypes.c_void_p(old_proc), hwnd, message, wp, lp)
        native_callback = callback_type(owner_proc)
        setter = native.window_long_setter(user)
        setter.argtypes, setter.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
        old_proc = setter(owner, -4, ctypes.cast(native_callback, ctypes.c_void_p).value)
        if not old_proc:
            raise RuntimeError('Could not attach native fixture hotkey callback')

        with patch.object(native, 'ROOT', configuration_root), \
                patch.object(native, 'log_event', side_effect=trace), \
                patch.object(desktop_agent, 'DesktopAgent', side_effect=make_agent):
            registered = bool(user.RegisterHotKey(owner, 218, 0x4000, 0x71))
            report['f2_register_available'] = registered
            if not registered:
                report['f2_register_win32_error'] = ctypes.get_last_error()
            # Send a real native WM_HOTKEY through the owned WndProc. This does
            # not inject F2 into an unrelated foreground app if F2 is already held.
            if not user.PostMessageW(owner, 0x312, 218, 0x71 << 16):
                raise RuntimeError('Could not dispatch the native F2 fixture message')
            worker_deadline = min(deadline, time.monotonic() + 5)
            while time.monotonic() < worker_deadline and not getattr(app, 'agent_thread', None):
                pump()
                time.sleep(.01)
            report['native_hotkey'] = hotkey_callbacks == [218]
            report['native_hotkey_message'] = 'WM_HOTKEY'
            report['native_hotkey_id'] = 218
            worker = getattr(app, 'agent_thread', None)
            if not worker:
                raise RuntimeError('Native F2 did not start the desktop-agent worker')
            terminal, stopped, summary = None, False, ''
            while time.monotonic() < deadline:
                pump()
                try:
                    result = app.results.get(timeout=.05)
                except queue.Empty:
                    continue
                kind = result[0]
                callback_events.append(kind)
                if result[1] != app.current_id:
                    raise RuntimeError('Native agent callback used a different request id')
                if kind in ('agent_done', 'agent_failed', 'agent_cancelled'):
                    terminal = kind
                    if kind == 'agent_done':
                        summary = result[2]
                        report['summary'] = summary[:4000]
                    elif kind == 'agent_failed':
                        category = HOST_FAILURE_MESSAGES.get(result[2])
                        report['native_failure_category'] = category or 'other_or_provider_error'
                        if category:
                            report['native_failure_message'] = result[2]
                if kind == 'agent_stopped':
                    stopped = True
                    break
            worker.join(timeout=2)
            report.update(native_terminal_event=terminal, native_stopped_event=stopped,
                          native_progress_callbacks=callback_events.count('agent_progress'))
            if terminal != 'agent_done' or not stopped or worker.is_alive():
                raise RuntimeError('Native F2 worker did not finish the isolated quiz successfully')
            if not report['native_hotkey'] or not report['native_progress_callbacks']:
                raise RuntimeError('Native F2 dispatch/progress callbacks were not observed')
        assert state['answers'] == {str(n): 'B. 4' if n % 2 else 'B. 42'
                                   for n in range(1, questions + 1)}, state
        assert not state['submitted'], 'Fixture unexpectedly submitted'
        if layout == 'long':
            assert state['max_scroll'] > state.get('viewport_height', 0), 'Long fixture did not create a long page'
            assert report['scroll_tool_calls'] > 0, 'Agent did not request an actual scroll tool action'
            assert state['scroll_events'] > 0, 'Browser did not report an actual scroll event'
            assert state['reached_bottom'], 'Agent did not scroll to the end of the long quiz'
        report.update(ok=True, real_mcp=True, selected_answers=state['answers'],
                      next_question=layout == 'paging', stopped_before_submit=True, summary=summary,
                      long_page_scrolled_to_bottom=layout == 'long' and state['reached_bottom'])
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        # Report only our fixed failure categories for live runs, never an
        # upstream exception body or credential-bearing configuration.
        report['error'] = str(exc) if model == 'fixture' else 'Live native F2 fixture did not complete; inspect event types in this report'
        raise
    finally:
        watchdog.cancel()
        interrupt()
        if app and getattr(app, 'agent_thread', None):
            app.agent_thread.join(timeout=3)
        if registered and owner:
            user.UnregisterHotKey(owner, 218)
        if owner:
            user.DestroyWindow(owner)
        if session:
            session.close()
        if browser:
            # Only the private browser created above is closed.
            if fixture_hwnd:
                user.PostMessageW(fixture_hwnd, 0x10, 0, 0)
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.terminate()
                try:
                    browser.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=3)
        server.shutdown()
        if previous:
            user.SetForegroundWindow(previous)
        try:
            folder.cleanup()
        except OSError:
            report['profile_cleanup_pending'] = True
        report.update(elapsed_seconds=round(time.monotonic() - started, 2),
                      native_events=captured_events, callback_events=callback_events,
                      fixture_answers=dict(state['answers']), fixture_page=state['page'],
                      fixture_submitted=state['submitted'],
                      fixture_scroll_events=state['scroll_events'], fixture_scroll_y=state['scroll_y'],
                      fixture_max_scroll_y=state['max_scroll_y'], fixture_max_scroll=state['max_scroll'],
                      fixture_reached_bottom=state['reached_bottom'],
                      fixture_browser_closed=browser is None or browser.poll() is not None)
        (output / 'agent-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
