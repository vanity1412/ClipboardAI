"""In-memory counts and safe diagnostics; no prompts, keys or raw error bodies."""
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies


class ActivityStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []
        self.calls = self.input_tokens = self.output_tokens = self.unknown = 0

    def record(self, provider, model, phase, started, data=None, error=None):
        usage = data.get('usage', {}) if isinstance(data, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        incoming = usage.get('input_tokens', usage.get('prompt_tokens'))
        outgoing = usage.get('output_tokens', usage.get('completion_tokens'))
        if isinstance(data, dict):
            incoming = data.get('prompt_eval_count', incoming)
            outgoing = data.get('eval_count', outgoing)
        known = all(type(n) is int and n >= 0 for n in (incoming, outgoing))
        code = getattr(error, 'status', getattr(error, 'code', None))
        # Only status identifiers, never exception text which may contain secrets.
        if not (type(code) is int or isinstance(code, str) and code in
                ('response_format', 'response_size', 'response_length', 'response_unfinished', 'response_empty')):
            code = type(error).__name__ if error else ''
        row = dict(provider=provider, model=model, phase=phase,
                   seconds=round(time.monotonic() - started, 2), error=str(code),
                   input=incoming if known else None, output=outgoing if known else None)
        with self.lock:
            self.calls += 1
            self.unknown += not known
            if known:
                self.input_tokens += incoming
                self.output_tokens += outgoing
            self.rows.append(row)
            self.rows = self.rows[-200:]

    def snapshot(self):
        with self.lock:
            return dict(calls=self.calls, input=self.input_tokens, output=self.output_tokens,
                        unknown=self.unknown, rows=[dict(row) for row in self.rows])


def safe_proxies():
    result = {}
    for name, value in getproxies().items():
        if name not in ('http', 'https'):
            continue
        try:
            address = urlsplit(value if '://' in value else 'http://' + value)
            result[name] = (address.hostname or '?') + (':' + str(address.port) if address.port else '')
        except ValueError:
            result[name] = 'Cấu hình không hợp lệ'
    return result
