"""Explicit provider profiles and bounded, cancellable per-stage failover."""
import copy
import json
import math
import re
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlencode


class APIHTTPError(RuntimeError):
    def __init__(self, status, retry_after=None):
        self.status = int(status)
        self.retry_after = retry_after
        super().__init__('AI HTTP ' + str(self.status))


class APIConnectionError(ConnectionError, RuntimeError):
    """Transport interruption; retains the legacy RuntimeError contract."""


def retry_delay(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 30
    return max(1, seconds) if math.isfinite(seconds) else 30


def validate(data):
    if not isinstance(data, dict) or type(data.get('auto', True)) is not bool:
        raise ValueError('Cấu hình API Zoo không hợp lệ')
    profiles = data.get('profiles', [])
    if not isinstance(profiles, list) or len(profiles) > 24:
        raise ValueError('API Zoo hỗ trợ tối đa 24 cấu hình')
    result, ids = [], set()
    for p in profiles:
        if not isinstance(p, dict):
            raise ValueError('Cấu hình API không hợp lệ')
        row = {k: p.get(k, '') for k in ('id', 'name', 'base_url', 'api_key', 'model', 'vision_model')}
        if not all(isinstance(v, str) for v in row.values()):
            raise ValueError('Các trường API phải là văn bản')
        if not row['id'] or len(row['id']) > 64 or row['id'] in ids:
            raise ValueError('ID API bị trùng hoặc không hợp lệ')
        ids.add(row['id'])
        if not row['name'].strip() or len(row['name']) > 80:
            raise ValueError('Tên API cần từ 1 đến 80 ký tự')
        row['base_url'] = row['base_url'].strip().rstrip('/')
        u = urlsplit(row['base_url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('Endpoint phải là HTTPS, không chứa key, query hoặc thông tin đăng nhập')
        if any(c.isspace() for c in row['base_url']) or not u.port in (None, 443):
            raise ValueError('Endpoint HTTPS cần dùng cổng 443')
        for field in ('model', 'vision_model'):
            value = row[field]
            if len(value) > 200 or any(c.isspace() for c in value):
                raise ValueError('Model không hợp lệ')
        if any(ord(c) < 32 or ord(c) > 126 for c in row['api_key']):
            raise ValueError('Key không hợp lệ')
        provider = p.get('provider', 'compatible')
        if provider not in ('compatible', 'deepseek', 'anthropic', 'responses', 'codex'):
            raise ValueError('Giao thức API không hợp lệ')
        if provider == 'codex' and (row['base_url'] != 'https://chatgpt.com' or row['api_key'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', row['id'])):
            raise ValueError('OpenAI Browser cần endpoint ChatGPT, không dùng API key và cần ID an toàn')
        if provider == 'deepseek' and u.hostname != 'api.deepseek.com':
            raise ValueError('Giao thức DeepSeek cần endpoint DeepSeek chính thức')
        models = p.get('models', [])
        if not isinstance(models, list) or len(models) > 1000:
            raise ValueError('Danh sách model không hợp lệ')
        catalog = []
        for m in models:
            if isinstance(m, str):
                m = {'id': m, 'vision': None}
            if not isinstance(m, dict) or not isinstance(m.get('id'), str) or not m['id'] or len(m['id']) > 200 or any(c.isspace() for c in m['id']):
                raise ValueError('ID model từ API không hợp lệ')
            vision = m.get('vision')
            if vision is not None and type(vision) is not bool:
                raise ValueError('Thông tin model ảnh không hợp lệ')
            if m['id'] not in {old['id'] for old in catalog}:
                catalog.append({'id': m['id'], 'vision': vision})
                if 'reasoning_efforts' in m:
                    from model_reasoning import LEVELS
                    values = m['reasoning_efforts']
                    if not isinstance(values, list) or any(v not in LEVELS or v == 'default' for v in values):
                        raise ValueError('Danh sách reasoning của model không hợp lệ')
                    catalog[-1]['reasoning_efforts'] = list(dict.fromkeys(values))
        timeout = p.get('timeout', 3000)
        tokens = p.get('max_tokens', 0)
        priority = p.get('priority', len(result))
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not (timeout == 0 or 30 <= timeout <= 3000):
            raise ValueError('Timeout: 0 hoặc 30–3000 giây')
        if type(tokens) is not int or not 0 <= tokens <= 393216 or type(priority) is not int or not 0 <= priority <= 999:
            raise ValueError('Token hoặc thứ tự ưu tiên không hợp lệ')
        if type(p.get('enabled', True)) is not bool:
            raise ValueError('Trạng thái API không hợp lệ')
        from model_reasoning import LEVELS
        effort = p.get('reasoning_effort', 'medium')
        efforts = p.get('model_efforts', {})
        if effort not in LEVELS or not isinstance(efforts, dict) or len(efforts) > 1000 or any(
                not isinstance(k, str) or not k or len(k) > 200 or any(c.isspace() for c in k) or v not in LEVELS
                for k, v in efforts.items()):
            raise ValueError('Reasoning effort không hợp lệ')
        row.update(provider=provider, timeout=timeout, max_tokens=tokens,
                   priority=priority, enabled=p.get('enabled', True), models=catalog,
                   reasoning_effort=effort, model_efforts=dict(efforts))
        reader = p.get('question_reader')
        if reader is not None:
            if not isinstance(reader, dict) or set(reader) != {'api', 'model'} or not all(isinstance(v, str) and v for v in reader.values()) or len(reader['api']) > 64 or len(reader['model']) > 200 or any(c.isspace() for c in reader['model']):
                raise ValueError('Model đọc ảnh riêng không hợp lệ')
            row['question_reader'] = dict(reader)
        result.append(row)
    primary = data.get('primary', '')
    if not isinstance(primary, str) or primary and primary not in ids:
        raise ValueError('API chính không tồn tại')
    if primary and not next(p for p in result if p['id'] == primary)['enabled']:
        raise ValueError('API chính phải được bật; chọn API chính khác trước khi tắt')
    return dict(version=3, auto=data.get('auto', True), primary=primary, profiles=result)


def consolidate(data):
    """One row per endpoint/credential, preserving the primary selection."""
    data = validate(data)
    groups, profiles, primary = {}, [], data['primary']
    remapped = {}
    for p in sorted(data['profiles'], key=lambda p: p['priority']):
        identity = (p['base_url'], p['api_key'], p['provider'], p['id'] if p['provider'] == 'codex' else '')
        if identity not in groups:
            groups[identity] = copy.deepcopy(p)
            if p['name'] == 'DeepSeek Flash':
                groups[identity]['name'] = 'DeepSeek'
            elif p['name'].startswith('Mirai:') and urlsplit(p['base_url']).hostname == 'api.miraiapi.com':
                groups[identity]['name'] = 'Mirai'
            profiles.append(groups[identity])
        target = groups[identity]
        remapped[p['id']] = target['id']
        target['enabled'] = target['enabled'] or p['enabled']
        if p['id'] == data['primary']:
            primary = target['id']
            target['model'], target['vision_model'] = p['model'], p['vision_model']
            target['timeout'], target['max_tokens'] = p['timeout'], p['max_tokens']
            target['reasoning_effort'] = p['reasoning_effort']
            if 'question_reader' in p:
                target['question_reader'] = copy.deepcopy(p['question_reader'])
            else:
                target.pop('question_reader', None)
        target['model_efforts'].update(p['model_efforts'])
        known = {m['id'] for m in target['models']}
        for m in p['models']:
            if m['id'] not in known:
                target['models'].append(m)
                known.add(m['id'])
    for p in profiles:
        if 'question_reader' in p:
            ident = p['question_reader']['api']
            p['question_reader']['api'] = remapped.get(ident, ident)
    return validate(dict(data, profiles=profiles, primary=primary))


class ZooStore:
    def __init__(self, root):
        self.path = Path(root) / 'api_zoo.json'

    def load(self):
        if not self.path.exists():
            return validate({})
        from credential_storage import decode_profiles
        return consolidate(decode_profiles(json.loads(self.path.read_text(encoding='utf-8-sig'))))

    def save(self, data):
        data = consolidate(data)
        from credential_storage import encode_profiles
        stored = encode_profiles(data)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return data


def seed_profiles(config):
    profiles = []
    for model, label in config.get('MODEL_CHOICES', []):
        if model.startswith('zoo:'):
            continue
        deep = model == 'deepseek-flash'
        key = config.get('DEEPSEEK_API_KEY' if deep else 'MIRAI_API_KEY', '')
        if not key:
            continue
        profiles.append(dict(id=uuid.uuid4().hex, name=label.replace(' — mặc định', ''),
            provider='deepseek' if deep else 'compatible',
            base_url='https://api.deepseek.com' if deep else config.get('MIRAI_BASE_URL', 'https://api.miraiapi.com') + '/v1',
            api_key=key, model=model, vision_model=model if deep else config.get('MIRAI_VISION_MODEL', ''),
            timeout=int(config.get('DEEPSEEK_TIMEOUT_S' if deep else 'MIRAI_TIMEOUT_S', 3000)),
            max_tokens=int(config.get('DEEPSEEK_MAX_TOKENS' if deep else 'MIRAI_MAX_TOKENS', 0)),
            priority=len(profiles), enabled=True))
    return consolidate(dict(profiles=profiles))


def apply_config(config, data, select_primary=False):
    data = validate(data)
    config['API_ZOO'] = copy.deepcopy(data)
    config['MODEL_CHOICES'] = tuple(('zoo:' + p['id'], p['name'] + (' · ' + p['model'] if p['model'] else ' · chưa tải model')) for p in data['profiles'] if p['enabled']) if data['profiles'] or config.get('ZOO_ONLY') else config.get('MODEL_CHOICES', ())
    config.pop('ZOO_CONFIG_ERROR', None)
    selected = config.get('SELECTED_MODEL', 'deepseek-flash')
    primary = next((p for p in data['profiles'] if p['id'] == data['primary'] and p['enabled']), None)
    if primary and select_primary:
        config['SELECTED_MODEL'] = 'zoo:' + primary['id']
    elif selected not in dict(config['MODEL_CHOICES']):
        config['SELECTED_MODEL'] = next(iter(dict(config['MODEL_CHOICES'])), '')


def selected_profile(config):
    ident = config.get('SELECTED_MODEL', '')
    return next((p for p in config.get('API_ZOO', {}).get('profiles', []) if 'zoo:' + p['id'] == ident and p['enabled']), None)


def discover_models(profile, auth_root=None, cancel=None, timeout=60):
    deadline = time.monotonic() + timeout
    def remaining():
        if cancel and cancel.is_set():
            raise InterruptedError('Đã hủy lấy model')
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('Hết thời gian lấy model')
        return seconds
    remaining()
    if profile.get('provider') == 'codex':
        from browser_provider import BrowserSession
        with BrowserSession(profile, auth_root, cancel, deadline=deadline) as session:
            session.require_account()
            return session.models()
    from http_transport import request_json
    headers = {'Accept': 'application/json', 'User-Agent': 'ClipboardAI/3.0'}
    if profile.get('provider') == 'anthropic':
        headers.update({'x-api-key': profile['api_key'], 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + profile['api_key']
    rows, cursor, cursors, total_bytes = [], None, set(), 0
    for _ in range(20):
        query = '?' + urlencode({'after_id': cursor}) if cursor else ''
        data, received = request_json(profile['base_url'] + '/models' + query,
                            headers=headers, timeout=min(20, remaining()),
                            cancel=cancel, max_bytes=4 * 1024 * 1024 - total_bytes,
                            return_size=True)
        total_bytes += received
        if total_bytes > 4 * 1024 * 1024:
            raise ValueError('Danh sách model vượt giới hạn')
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise ValueError('API không trả danh sách model hợp lệ')
        rows.extend(data['data'])
        if len(rows) > 1000:
            raise ValueError('Danh sách model vượt giới hạn')
        if profile.get('provider') != 'anthropic' or not data.get('has_more'):
            break
        cursor = data.get('last_id')
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError('Phân trang model không hợp lệ')
        cursors.add(cursor)
    else:
        raise ValueError('Danh sách model vượt giới hạn phân trang')
    catalog = []
    for m in rows:
        if not isinstance(m, dict) or not m.get('id'):
            continue
        capabilities = m.get('capabilities', {})
        capabilities = capabilities if isinstance(capabilities, dict) else {}
        architecture = m.get('architecture', {})
        architecture = architecture if isinstance(architecture, dict) else {}
        modalities = m.get('input_modalities', capabilities.get('input_modalities', architecture.get('input_modalities')))
        vision = capabilities.get('vision', m.get('supports_vision'))
        if isinstance(modalities, list):
            vision = 'image' in modalities
        if type(vision) is not bool:
            vision = None
        catalog.append({'id': m['id'], 'vision': vision})
    catalog = validate({'profiles': [dict(profile, models=catalog)]})['profiles'][0]['models']
    if not catalog:
        raise ValueError('API chưa liệt kê model nào cho key này')
    return sorted(catalog, key=lambda m: m['id'].lower())


def update_catalog(profile, catalog):
    p = copy.deepcopy(profile)
    p['models'] = catalog
    ids = {m['id'] for m in catalog}
    if p['model'] not in ids:
        p['model'] = catalog[0]['id'] if catalog else ''
    if p['vision_model'] not in ids or not model_supports_vision(p, p['vision_model']):
        p['vision_model'] = next((m['id'] for m in catalog if m['vision'] is True), '')
        if not p['vision_model']:
            p['vision_model'] = next((m['id'] for m in catalog if m['id'] == p['model'] and m['vision'] is None), '')
    return validate({'profiles': [p]})['profiles'][0]


def model_supports_vision(profile, model):
    """Unknown/manual model capabilities stay usable; explicit false is final."""
    return bool(model) and not any(m['id'] == model and m.get('vision') is False
                                   for m in profile.get('models', []))


def probe_profile(profile, auth_root=None):
    catalog = discover_models(profile, auth_root)
    return profile['model'] in {m['id'] for m in catalog}, len(catalog)


class ZooRouter:
    def __init__(self):
        self.cooldowns = {}
        self.billing_cooldowns = set()

    def retry(self, profile):
        """An explicit retry may recheck a topped-up account, never invalid keys."""
        credential = ('key', profile['base_url'], profile['api_key'], profile['id'] if profile['provider'] == 'codex' else '')
        if credential in self.billing_cooldowns:
            self.cooldowns.pop(credential, None)
            self.billing_cooldowns.discard(credential)

    def retry_manual(self, profile):
        credential = ('key', profile['base_url'], profile['api_key'], profile['id'] if profile['provider'] == 'codex' else '')
        self.cooldowns.pop(credential, None)
        self.billing_cooldowns.discard(credential)
        for identity in list(self.cooldowns):
            if identity[:3] == (profile['id'], profile['base_url'], profile['api_key']):
                self.cooldowns.pop(identity, None)

    def profile_state(self, profile):
        credential = ('key', profile['base_url'], profile['api_key'], profile['id'] if profile['provider'] == 'codex' else '')
        until = max([self.cooldowns.get(credential, 0)] + [value for identity, value in self.cooldowns.items()
                    if identity[:3] == (profile['id'], profile['base_url'], profile['api_key'])])
        return 'Bị khóa; kiểm tra key/quyền' if until == float('inf') else 'Đang tạm nghỉ' if until > time.monotonic() else 'Sẵn sàng'

    def run(self, config, selected, vision, call, cancel=None, notify=None):
        data = config['API_ZOO']
        profiles = sorted((p for p in data['profiles'] if p['enabled'] and (p['api_key'] or p['provider'] == 'codex') and p['model'] and (not vision or model_supports_vision(p, p['vision_model']))),
                          key=lambda p: (p['id'] != selected['id'], p['model'] != selected['model'], p['priority']))
        if not data['auto']:
            profiles = [p for p in profiles if p['id'] == selected['id']]
        if not profiles:
            raise RuntimeError('API Zoo thiếu key hoặc model đọc ảnh; kiểm tra cấu hình')
        attempts, last = 0, None
        for profile in profiles:
            active_model = profile['vision_model'] if vision else profile['model']
            identity = (profile['id'], profile['base_url'], profile['api_key'], active_model)
            credential = ('key', profile['base_url'], profile['api_key'], profile['id'] if profile['provider'] == 'codex' else '')
            if max(self.cooldowns.get(identity, 0), self.cooldowns.get(credential, 0)) > time.monotonic():
                continue
            while attempts < 3:
                if cancel and cancel.is_set():
                    raise InterruptedError('Đã hủy yêu cầu')
                self.billing_cooldowns.discard(credential)
                attempts += 1
                if notify:
                    notify('API Zoo: ' + profile['name'] + ' — lần ' + str(attempts) + '/3')
                try:
                    return call(copy.deepcopy(profile), vision)
                except InterruptedError:
                    raise
                except Exception as exc:
                    if cancel and cancel.is_set():
                        raise InterruptedError('Đã hủy yêu cầu') from None
                    status = getattr(exc, 'status', getattr(exc, 'code', None))
                    # HttpError has an integer .code; AIResponseError uses strings.
                    retryable = status in (401, 402, 403, 429) or isinstance(status, int) and 500 <= status <= 599
                    retryable = retryable or isinstance(exc, (TimeoutError, ConnectionError, OSError)) and not isinstance(status, int)
                    if not retryable or not data['auto']:
                        raise
                    last = exc
                    if status in (401, 403):
                        self.cooldowns[identity if status == 403 else credential] = float('inf')
                        break
                    if status == 402:
                        self.cooldowns[credential] = time.monotonic() + 30
                        self.billing_cooldowns.add(credential)
                        break
                    cooldown = retry_delay(getattr(exc, 'retry_after', None)) if status == 429 else 5
                    self.cooldowns[credential if status == 429 else identity] = time.monotonic() + cooldown
                    if len(profiles) > 1 or attempts == 3 or status == 429:
                        break
                    if cancel:
                        if cancel.wait(2 ** attempts):
                            raise InterruptedError('Đã hủy yêu cầu')
                    else:
                        time.sleep(2 ** attempts)
            if attempts == 3:
                break
        raise RuntimeError('API Zoo: các API dự phòng đã lỗi hoặc đang tạm nghỉ; clipboard và lịch sử giữ nguyên') from last
