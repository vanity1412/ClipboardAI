"""Reply modes are independent of provider/model and legacy coding sessions."""
from coding_prompt import CODE_PROMPT

CODING, CHAT, ANALYSIS = 0, 3, 4
MODE_ORDER = (CHAT, ANALYSIS, CODING)
MENU_MODES = (CHAT, CODING)
VALID_MODES = (0, 1, 2, CHAT, ANALYSIS)
MODE_LABELS = {CHAT: "Hỏi đáp", ANALYSIS: "Phân tích", CODING: "Lập trình"}
MENU_LABELS = {CHAT: "Hỏi đáp / phân tích", CODING: "Lập trình"}


def purpose_mode(mode):
    return CHAT if normalize_mode(mode) == ANALYSIS else normalize_mode(mode)


def reasoning_for(config, mode):
    purpose = purpose_mode(mode)
    return config.get('REASONING_MODES', {}).get(str(purpose),
        'careful' if mode == ANALYSIS or purpose == CODING else 'fast')


def supports_reasoning_control(config):
    selected = config.get('SELECTED_MODEL', config.get('DEEPSEEK_MODEL', ''))
    if selected.startswith('zoo:'):
        profile = next((p for p in config.get('API_ZOO', {}).get('profiles', [])
                        if p['id'] == selected[4:]), None)
        return bool(profile and profile.get('provider') == 'deepseek' and
                    profile.get('model') in ('deepseek-flash', 'deepseek-v4-pro'))
    if config.get('BACKEND') == 'DeepSeek':
        return selected in ('deepseek-flash', 'deepseek-v4-pro')
    return config.get('OLLAMA_MODEL', '').startswith(('qwen3:', 'qwen3.5:'))


def normalize_mode(mode):
    return {1: CODING, 2: CODING}.get(mode, mode)


def clean_legacy_text(text):
    # Strip only wrappers inserted by old releases, never a user's C++ request.
    marker = '\nPhản hồi người dùng:\n'
    if text.startswith('Tiếp tục đúng bài trong session, dùng đề và code gần nhất đã lưu.') and marker in text:
        return text.split(marker, 1)[1]
    if text.startswith('Dựa trên đề và phân tích trong hội thoại, tự kiểm tra lại lập luận rồi viết code GNU C++17') and text.endswith('Không Markdown.'):
        return 'Tiếp tục.'
    return text


def is_chat(mode):
    return mode in VALID_MODES


def request_messages(text, history=None, instruction=None):
    history = [dict(m, content=clean_legacy_text(m['content'])) if m.get('role') == 'user' and isinstance(m.get('content'), str) else dict(m) for m in (history or [])]
    return ([{"role": "system", "content": instruction}] if instruction else []) + history + [{"role": "user", "content": text}]


def reply_config(config, mode):
    mode = normalize_mode(mode)
    if config.get('REPLY_MENU_VERSION') == 1:
        purpose = purpose_mode(mode)
        mode = CHAT if reasoning_for(config, mode) == 'fast' else CODING if purpose == CODING else ANALYSIS
    result = dict(config, REPLY_MODE=mode)
    if is_chat(mode):
        result["REPLY_MAX_TOKENS"] = 8192 if mode == CHAT else 32768
        result["REPLY_TIMEOUT_S"] = 180 if mode == CHAT else 600
    return result


def limits(config, tokens, timeout):
    if "REPLY_MAX_TOKENS" in config:
        tokens = min(tokens or config["REPLY_MAX_TOKENS"], config["REPLY_MAX_TOKENS"])
        timeout = min(timeout or config["REPLY_TIMEOUT_S"], config["REPLY_TIMEOUT_S"])
    return tokens, timeout
