"""Opt-in clean-runtime smoke: download and initialize; no login or inference."""
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading

from codex_setup import VERSION, ensure_codex


def run(root):
    root = Path(root) / 'codex-setup-verification'
    executable = ensure_codex(root=root / 'runtime')
    with tempfile.TemporaryDirectory(prefix='auth-', dir=root) as home:
        environment = os.environ.copy()
        environment['CODEX_HOME'] = home
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID'):
            environment.pop(key, None)
        request = {'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'clipboardai_setup_test', 'version': '1.0'}}}
        process = subprocess.Popen([executable, 'app-server'], cwd=home, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        try:
            reader.start()
            process.stdin.write((json.dumps(request) + '\n').encode())
            process.stdin.flush()
            response = json.loads(lines.get(timeout=20))
            if response.get('id') != 1 or 'result' not in response or 'error' in response:
                raise RuntimeError('CLI app-server initialization failed')
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            reader.join(timeout=1)
            process.stdin.close()
            process.stdout.close()
    report = {'ok': True, 'version': VERSION, 'app_server_initialized': True,
              'login_requests': 0, 'inference_requests': 0, 'admin_required': False}
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
