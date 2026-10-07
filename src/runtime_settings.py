"""Validate portable preferences without rewriting the user's files at startup."""
import json
import math
from pathlib import Path

DEEPSEEK_TOKEN_CEILING = 393216
DEEPSEEK_DEFAULT_MAX_TOKENS = DEEPSEEK_TOKEN_CEILING
DEEPSEEK_DEFAULT_TIMEOUT_S = 0

NUMERIC_SETTINGS = {
    "OLLAMA_NUM_CTX": (1024, 65536, False),
    "OLLAMA_NUM_PREDICT": (128, 32768, False),
    "OLLAMA_TIMEOUT_S": (30, 2147483647, True),
    "DEEPSEEK_MAX_TOKENS": (1, DEEPSEEK_TOKEN_CEILING, True),
    "DEEPSEEK_TIMEOUT_S": (30, 2147483647, True),
    "SESSION_MAX_CHARS": (1024, 196608, False),
    "MIRAI_MAX_TOKENS": (1, 32768, True),
    "MIRAI_TIMEOUT_S": (30, 2147483647, True),
}


def validated_preferences(data):
    if not isinstance(data, dict):
        return {}, ["preferences_file"]
    valid, invalid = {}, []
    for name, value in data.items():
        if name == 'HOTKEYS':
            from hotkey_settings import bindings, normalized_shortcuts
            try:
                if not isinstance(value, dict):
                    raise ValueError('Invalid shortcuts')
                bindings(value, data.get('HOTKEYS_DISABLED'))
                valid[name] = normalized_shortcuts(value)
            except ValueError:
                invalid.append(name)
        elif name == 'HOTKEYS_DISABLED':
            from hotkey_settings import disabled_actions, bindings
            try:
                valid[name] = disabled_actions(value)
                bindings(data.get('HOTKEYS'), valid[name])
            except ValueError:
                valid.pop(name, None)
                invalid.append(name)
        elif name in ('F4_CAPTURE', 'REGION_CUE'):
            if value in (('region', 'window') if name == 'F4_CAPTURE' else ('light', 'clear')):
                valid[name] = value
            else:
                invalid.append(name)
        elif name == 'REPLY_MENU_VERSION':
            if type(value) is int and value == 1:
                valid[name] = value
            else:
                invalid.append(name)
        elif name == 'REASONING_MODES':
            if isinstance(value, dict) and set(value) <= {'0', '3'} and all(v in ('fast', 'careful') for v in value.values()):
                valid[name] = dict(value)
            else:
                invalid.append(name)
        elif name == 'SAVED_PROMPTS':
            if (isinstance(value, list) and len(value) <= 64 and
                    all(isinstance(p, dict) and set(p) == {'id', 'name', 'style', 'text'} and
                        isinstance(p['id'], str) and 0 < len(p['id']) <= 80 and
                        isinstance(p['name'], str) and 0 < len(p['name']) <= 100 and
                        p['style'] in ('short', 'choices', 'code', 'free', 'custom') and
                        isinstance(p['text'], str) and len(p['text']) <= 16000 and '\x00' not in p['text'] and
                        (p['style'] != 'custom' or p['text'].strip()) for p in value) and
                    len({p['id'] for p in value}) == len(value)):
                valid[name] = [dict(p) for p in value]
            else:
                invalid.append(name)
        elif name in ('PROMPT_MODES', 'PROMPT_CUSTOM'):
            allowed = {'0', '3', '4'}
            if (isinstance(value, dict) and set(value) <= allowed and
                    all(isinstance(v, str) and (
                        v in ('short', 'choices', 'code', 'free', 'custom') if name == 'PROMPT_MODES'
                        else len(v) <= 16000 and '\x00' not in v) for v in value.values())):
                valid[name] = dict(value)
            else:
                invalid.append(name)
        elif name == "ANSWER_STYLE":
            if value in ('short', 'choices', 'free'):
                valid[name] = value
            else:
                invalid.append(name)
        elif name == "F4_INPUT":
            if value in ("image", "image_clipboard"):
                valid[name] = value
            else:
                invalid.append(name)
        elif name == "OLLAMA_MODEL":
            if isinstance(value, str) and value.strip() and len(value) <= 200 and not any(c.isspace() for c in value.strip()):
                valid[name] = value.strip()
            else:
                invalid.append(name)
        elif name in NUMERIC_SETTINGS:
            low, high, allow_zero = NUMERIC_SETTINGS[name]
            try:
                if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 64:
                    raise ValueError()
                number = float(value)
                if not math.isfinite(number) or not number.is_integer():
                    raise ValueError()
                number = int(number)
                if not (low <= number <= high or allow_zero and number == 0):
                    raise ValueError()
                valid[name] = str(number)
            except (ValueError, TypeError, OverflowError):
                invalid.append(name)
    return valid, invalid


def load_runtime_settings(config, path):
    valid_base, issues = validated_preferences(config)
    # A rejected structured preference can come from the string-only .env
    # loader. Remove it before consumers call dict()/get() on that value.
    for name in issues:
        config.pop(name, None)
    model = valid_base.get("OLLAMA_MODEL", "auto")
    thinking = model.startswith(("qwen3:", "qwen3.5:"))
    defaults = {
        "F4_CAPTURE": "region",
        "REGION_CUE": "light",
        "F4_INPUT": "image",
        "ANSWER_STYLE": "short",
        "OLLAMA_MODEL": model,
        "OLLAMA_NUM_CTX": "8192" if thinking else "4096",
        "OLLAMA_NUM_PREDICT": "8192" if thinking else "2048",
        "OLLAMA_TIMEOUT_S": "900" if thinking else "300",
        "DEEPSEEK_MAX_TOKENS": str(DEEPSEEK_DEFAULT_MAX_TOKENS),
        "DEEPSEEK_TIMEOUT_S": str(DEEPSEEK_DEFAULT_TIMEOUT_S),
        "SESSION_MAX_CHARS": "48000",
        "MIRAI_MAX_TOKENS": "0", "MIRAI_TIMEOUT_S": "3000",
    }
    config.update(defaults)
    config.update(valid_base)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        valid, invalid = validated_preferences(data)
        config.update(valid)
        issues.extend(invalid)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, RecursionError):
        issues.append("preferences_file")
    return sorted(set(issues))


def save_preferences(path, preferences):
    path = Path(path)
    valid, invalid = validated_preferences(preferences)
    if invalid:
        raise ValueError("Invalid preferences")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(valid), encoding="utf-8")
    temporary.replace(path)
