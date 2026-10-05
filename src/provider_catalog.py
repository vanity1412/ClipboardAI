"""Provider defaults, not an entitlement list. Live discovery takes precedence."""
from urllib.parse import urlsplit

# Checked against provider documentation on 2026-10-05; see docs/API_PROVIDERS.md.
PRESETS = {
    'OpenAI · API key': ('responses', 'https://api.openai.com/v1',
        ('gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-luna', 'gpt-5.4'), 'https://platform.openai.com/api-keys'),
    'OpenAI Browser · ChatGPT': ('codex', 'https://chatgpt.com',
        (), 'https://developers.openai.com/codex/auth'),
    'Claude · Anthropic': ('anthropic', 'https://api.anthropic.com/v1',
        ('claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1', 'claude-haiku-4-5-20251001'), 'https://platform.claude.com/settings/keys'),
    'DeepSeek': ('deepseek', 'https://api.deepseek.com',
        ('deepseek-flash', 'deepseek-v4-pro'), 'https://platform.deepseek.com/api_keys'),
    'Grok · xAI': ('compatible', 'https://api.x.ai/v1',
        ('grok-4.7',), 'https://console.x.ai'),
    'Gemini · Google': ('compatible', 'https://generativelanguage.googleapis.com/v1beta/openai',
        ('gemini-3.8-flash',), 'https://aistudio.google.com/api-keys'),
    'Custom · OpenAI-compatible': ('compatible', '', (), ''),
    'Custom · Anthropic-compatible': ('anthropic', '', (), ''),
    'Custom · OpenAI Responses': ('responses', '', (), ''),
}


def preset_for(profile):
    for name, (protocol, endpoint, _, _) in PRESETS.items():
        if endpoint and profile.get('provider', 'compatible') == protocol and profile.get('base_url', '').rstrip('/') == endpoint:
            return name
    return {'anthropic': 'Custom · Anthropic-compatible', 'responses': 'Custom · OpenAI Responses'}.get(profile.get('provider'), 'Custom · OpenAI-compatible')


def suggestions(name):
    models = PRESETS[name][2]
    return [{'id': m, 'vision': None if name.startswith('Custom') or m == 'deepseek-v4-pro' else True} for m in models]


def api_key_page(profile):
    host = urlsplit(profile.get('base_url', '')).hostname
    return next((values[3] for values in PRESETS.values() if values[1] and urlsplit(values[1]).hostname == host), '')
