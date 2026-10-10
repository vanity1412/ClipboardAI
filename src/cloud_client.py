"""DeepSeek by default; optional Mirai models with the same session protocol."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from coding_prompt import CODE_PROMPT
from chat_modes import request_messages, limits
from deepseek_client import DeepSeekClient
from api_zoo import ZooStore, ZooRouter, apply_config, selected_profile

DEFAULT_MODELS = (
    ("deepseek-flash", "DeepSeek Flash — mặc định"),
    ("claude-fable-5.1", "Mirai: claude-fable-5.1"),
    ("claude-sonnet-5.5", "Mirai: claude-sonnet-5.5"),
    ("claude-opus-5.5", "Mirai: claude-opus-5.5"),
)


def _load_cloud_config(root, deepseek):
    config = dict(deepseek, BACKEND="DeepSeek", DEEPSEEK_MODEL="deepseek-flash",
                  SELECTED_MODEL="deepseek-flash", MODEL_CHOICES=DEFAULT_MODELS)
    path = Path(root) / "mirai_config.json"
    if not path.exists():
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError()
        env = data.get("env", {})
        if not isinstance(env, dict):
            raise ValueError()
        base = str(data.get("base_url", env.get("ANTHROPIC_BASE_URL", "https://api.miraiapi.com/v1"))).rstrip("/")
        # Do not forward the supplied credential to another host accidentally.
        address = urlsplit(base)
        if address.scheme != "https" or address.netloc != "api.miraiapi.com" or address.query or address.fragment or address.path not in ("", "/v1"):
            raise ValueError()
        timeout = float(data.get("timeout_seconds", int(env.get("API_TIMEOUT_MS", "3000000")) / 1000))
        if not (timeout == 0 or 30 <= timeout <= 3000):
            raise ValueError()
        configured_models = data.get("models")
        if configured_models is not None and (not isinstance(configured_models, list) or not configured_models or not all(isinstance(m, str) for m in configured_models) or len(configured_models) > 12):
            raise ValueError()
        models = tuple(configured_models) if configured_models is not None else tuple(str(env.get(slot, fallback)) for slot, fallback in (
            ("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-fable-5.1"),
            ("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5.5"),
            ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-opus-5.5")))
        if any(not model.startswith("claude-") or len(model) > 100 or any(c.isspace() for c in model) for model in models):
            raise ValueError()
        vision_model = str(data.get("vision_model", "claude-fable-5.1"))
        if vision_model not in models:
            raise ValueError()
        config.update(MIRAI_BASE_URL=base[:-3] if base.endswith("/v1") else base,
                      MIRAI_API_KEY=str(data.get("api_key", env.get("ANTHROPIC_AUTH_TOKEN", ""))),
                      MIRAI_TIMEOUT_S=str(int(timeout)), MIRAI_MAX_TOKENS="0",
                      MIRAI_VISION_MODEL=vision_model,
                      MODEL_CHOICES=(DEFAULT_MODELS[0],) + tuple((m, "Mirai: " + m) for m in dict.fromkeys(models)))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        config["MIRAI_CONFIG_ERROR"] = "Không đọc được mirai_config.json; kiểm tra cấu hình Mirai"
    return config


def load_cloud_config(root, deepseek):
    config = _load_cloud_config(root, deepseek)
    config['BROWSER_AUTH_ROOT'] = str(Path(root).resolve() / '.clipboardai-auth')
    try:
        store = ZooStore(root)
        config['ZOO_ONLY'] = store.path.exists()
        apply_config(config, store.load(), select_primary=True)
    except (OSError, ValueError, TypeError, RecursionError):
        config['ZOO_CONFIG_ERROR'] = 'Không đọc được api_zoo.json; cấu hình cũ vẫn dùng được'
    return config


class CloudClient(DeepSeekClient):
    def supports_vision(self):
        return True

    def read_problem(self, png, previous=""):
        if self.config.get('SELECTED_MODEL', '').startswith('zoo:'):
            return super().read_problem(png, previous)
        if self.config.get("SELECTED_MODEL", "deepseek-flash") == "deepseek-flash":
            return super().read_problem(png, previous)
        # A dedicated Mirai reader passed the synthetic image check; some
        # gateway aliases returned unreadable even though text requests worked.
        config = self.config
        self.config = dict(config, SELECTED_MODEL=config.get("MIRAI_VISION_MODEL", "claude-fable-5.1"))
        try:
            return super().read_problem(png, previous)
        finally:
            self.config = config

    def ask(self, text, history=None, instruction=None, json_output=False):
        vision_input = isinstance(text, list) and any(isinstance(x, dict) and x.get('type') == 'image_url' for x in text)
        model = self.config.get("SELECTED_MODEL", "deepseek-flash")
        if vision_input and not model.startswith('zoo:') and model != 'deepseek-flash':
            model = self.config.get('MIRAI_VISION_MODEL', model)
        if model.startswith('zoo:'):
            profile = selected_profile(self.config)
            if not profile:
                raise RuntimeError('API Zoo chưa chọn cấu hình hợp lệ')
            if not hasattr(self, 'zoo_router'):
                self.zoo_router = ZooRouter()
            config = self.config
            def call(candidate, vision):
                selected = candidate['vision_model'] if vision else candidate['model']
                from model_reasoning import effort_for
                effort = effort_for(candidate, selected)
                if candidate['provider'] == 'codex':
                    from browser_provider import BrowserSession
                    _, timeout = limits(config, candidate['max_tokens'], candidate['timeout'])
                    deadline = getattr(self, 'deadline', None)
                    if deadline is not None:
                        import time
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError()
                        timeout = min(timeout or remaining, remaining)
                    import time
                    started, error = time.monotonic(), None
                    try:
                        with BrowserSession(candidate, config.get('BROWSER_AUTH_ROOT'), self.cancel_event,
                                            deadline=deadline) as browser:
                            answer = browser.ask(selected, request_messages(text, history, instruction), timeout, getattr(self, 'on_stream', None), effort=effort)
                    except Exception as exc:
                        error = exc
                        raise
                    finally:
                        stats = getattr(self, 'activity', None)
                        if stats is not None:
                            stats.record(candidate['name'], selected, getattr(self, 'activity_phase', 'Trả lời'), started, error=error)
                    return answer, candidate['name'] + ': ' + selected
                if candidate['provider'] == 'deepseek' and selected in ('deepseek-flash', 'deepseek-v4-pro'):
                    self.config = dict(config, ZOO_REASONING_EFFORT=effort, DEEPSEEK_MODEL=selected, DEEPSEEK_API_KEY=candidate['api_key'],
                                       DEEPSEEK_MAX_TOKENS=str(candidate['max_tokens']), DEEPSEEK_TIMEOUT_S=str(candidate['timeout']))
                    try:
                        answer, _ = super(CloudClient, self).ask(text, history, instruction, json_output)
                    finally:
                        self.config = config
                else:
                    from provider_protocols import request_body, completed_response
                    tokens, timeout = limits(config, candidate['max_tokens'], candidate['timeout'])
                    protocol = candidate['provider']
                    body = request_body(protocol, selected, request_messages(text, history, instruction), tokens, self.cancel_event is not None)
                    from model_reasoning import apply_effort
                    apply_effort(body, protocol, selected, effort)
                    route = {'anthropic': '/messages', 'responses': '/responses'}.get(protocol, '/chat/completions')
                    options = {'protocol': protocol} if protocol in ('anthropic', 'responses') else {}
                    data = self.post(candidate['base_url'] + route, body, timeout, key=candidate['api_key'], **options)
                    try:
                        answer = completed_response(protocol, data)
                    except (KeyError, IndexError, TypeError, ValueError):
                        raise RuntimeError('API Zoo chưa trả câu trả lời hoàn chỉnh; clipboard giữ nguyên') from None
                return answer.strip(), candidate['name'] + ': ' + selected
            return self.zoo_router.run(config, profile, json_output or vision_input, call, self.cancel_event, getattr(self, 'zoo_notify', None))
        if model == "deepseek-flash":
            return super().ask(text, history, instruction, json_output)
        if model not in dict(self.config.get("MODEL_CHOICES", DEFAULT_MODELS)):
            raise RuntimeError("Model chưa có trong cấu hình; chọn lại trong tray")
        if self.config.get("MIRAI_CONFIG_ERROR"):
            raise RuntimeError(self.config["MIRAI_CONFIG_ERROR"])
        key = self.config.get("MIRAI_API_KEY")
        if not key:
            raise RuntimeError("Thiếu key Mirai; đặt mirai_config.json cạnh EXE rồi mở lại app")
        # Mirai documents an OpenAI-compatible route. No Claude Code process or
        # telemetry is launched; slot names map to the exact supplied model IDs.
        body = {"model": model, "stream": self.cancel_event is not None,
                "messages": request_messages(text, history, instruction)}
        max_tokens = int(self.config.get("MIRAI_MAX_TOKENS", "0"))
        max_tokens, timeout = limits(self.config, max_tokens, float(self.config.get("MIRAI_TIMEOUT_S", "3000")))
        if max_tokens:
            body["max_tokens"] = max_tokens
        # The screenshot instruction itself asks for JSON; don't assume that
        # this gateway supports OpenAI response_format for every Claude alias.
        data = self.post(self.config.get("MIRAI_BASE_URL", "https://api.miraiapi.com") + "/v1/chat/completions",
                         body, timeout, key=key)
        try:
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            answer = choice["message"].get("content")
            from provider_protocols import check_answer_message
            check_answer_message(choice["message"])
        except (KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("Mirai trả phản hồi sai định dạng; clipboard giữ nguyên") from None
        if finish == "length":
            raise RuntimeError("Mirai hết token trước khi trả lời xong; code dở dang không được copy")
        if finish != "stop":
            raise RuntimeError("Mirai chưa hoàn tất câu trả lời hoặc từ chối nội dung; clipboard giữ nguyên")
        if not isinstance(answer, str) or not answer.strip():
            raise RuntimeError("Mirai trả nội dung trống; chọn Gửi lại ở tray hoặc chọn model khác")
        return answer.strip(), "Mirai: " + model
