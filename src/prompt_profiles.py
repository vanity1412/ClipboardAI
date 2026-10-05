"""Per-mode prompt preferences, independent of the selected API."""
from answer_policy import answer_instruction
from chat_modes import CHAT, ANALYSIS, CODING, MODE_ORDER, normalize_mode, purpose_mode
from coding_prompt import CODE_PROMPT

PRESETS = ('short', 'choices', 'code', 'free', 'custom')
LABELS = {'short': 'Ngắn gọn · tự nhận dạng câu hỏi',
          'choices': 'Trắc nghiệm / điền khuyết · chỉ đáp án',
          'code': 'Lập trình', 'free': 'Theo yêu cầu · không thêm prompt',
          'custom': 'Prompt riêng'}
MAX_PROMPT_CHARS = 16000


def prompt_mode(mode, config):
    return purpose_mode(mode) if config.get('REPLY_MENU_VERSION') == 1 else normalize_mode(mode)


def default_prompt(mode):
    return 'code' if purpose_mode(mode) == CODING else 'short'


def merged_preferences(config, active_mode):
    if config.get('REPLY_MENU_VERSION') == 1:
        return {}
    library = []
    for mode, title in ((CHAT, 'Hỏi đáp cũ'), (ANALYSIS, 'Phân tích cũ'), (CODING, 'Lập trình cũ')):
        if str(mode) in config.get('PROMPT_MODES', {}) or config.get('PROMPT_CUSTOM', {}).get(str(mode)):
            style = selected_prompt(mode, config)
            library.append(dict(id='legacy-' + str(mode), name=title, style=style,
                                text=instruction_for(mode, config)))
            dormant = config.get('PROMPT_CUSTOM', {}).get(str(mode), '')
            if dormant and style != 'custom':
                library.append(dict(id='legacy-custom-' + str(mode), name=title + ' · prompt riêng',
                                    style='custom', text=dormant))
    source = ANALYSIS if active_mode == ANALYSIS else CHAT
    if str(source) not in config.get('PROMPT_MODES', {}) and str(source) not in config.get('PROMPT_CUSTOM', {}):
        source = CHAT if str(CHAT) in config.get('PROMPT_MODES', {}) else ANALYSIS if str(ANALYSIS) in config.get('PROMPT_MODES', {}) else source
    modes = dict(config.get('PROMPT_MODES', {}))
    custom = dict(config.get('PROMPT_CUSTOM', {}))
    modes[str(CHAT)] = selected_prompt(source, config)
    if custom.get(str(source)):
        custom[str(CHAT)] = custom[str(source)]
    return dict(REPLY_MENU_VERSION=1, PROMPT_MODES=modes, PROMPT_CUSTOM=custom,
                SAVED_PROMPTS=library, REASONING_MODES={
                    '3': 'careful' if active_mode == ANALYSIS else 'fast', '0': 'careful'})


def selected_prompt(mode, config):
    mode = prompt_mode(mode, config)
    return config.get('PROMPT_MODES', {}).get(str(mode),
        'code' if mode == CODING else config.get('ANSWER_STYLE', 'short'))


def instruction_for(mode, config):
    style = selected_prompt(mode, config)
    if style == 'custom':
        return config.get('PROMPT_CUSTOM', {}).get(str(prompt_mode(mode, config)), '')
    if style == 'code':
        return CODE_PROMPT
    return answer_instruction(CHAT, style)


def prompt_preferences(mode, style, text, config):
    mode = prompt_mode(mode, config)
    if mode not in MODE_ORDER or style not in PRESETS:
        raise ValueError('Chế độ/prompt không hợp lệ')
    if not isinstance(text, str) or len(text) > MAX_PROMPT_CHARS or '\x00' in text:
        raise ValueError('Prompt tối đa 16.000 ký tự')
    if style == 'custom' and not text.strip():
        raise ValueError('Nhập prompt riêng trước khi lưu')
    modes = dict(config.get('PROMPT_MODES', {}))
    custom = dict(config.get('PROMPT_CUSTOM', {}))
    modes[str(mode)] = style
    if style == 'custom':
        custom[str(mode)] = text.strip()
    result = dict(PROMPT_MODES=modes, PROMPT_CUSTOM=custom)
    if config.get('REPLY_MENU_VERSION') == 1:
        library = [dict(item) for item in config.get('SAVED_PROMPTS', [])]
        if style == 'custom' and not any(item['text'] == text.strip() and item['style'] == 'custom' for item in library):
            if len(library) >= 64:
                raise ValueError('Đã đủ 64 prompt đã lưu; giữ các bản cũ')
            import uuid
            library.append(dict(id=str(uuid.uuid4()), name='Prompt riêng ' + str(len(library) + 1),
                                style='custom', text=text.strip()))
        result['SAVED_PROMPTS'] = library
    return result
