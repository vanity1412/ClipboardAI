"""Conservative credential checks that leave ordinary programming expressions alone.

This is a best-effort input guard, not a credential scanner. It never logs or
returns matched values, so callers only learn whether the request should stop.
"""
import re


_PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----')
_KEY = re.compile(r'\b(?:sk-[\w-]{12,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b')
_ASSIGNMENT = re.compile(
    r'''(?ix)(?<!\w)["']?(?P<name>password|passwd|pwd|secret|token|api[_ -]?key)["']?
    \s*[:=]\s*(?P<value>"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;}\r\n]+)''')
_IDENTIFIER = re.compile(r'[A-Za-z_]\w*\Z')
_PLACEHOLDER = re.compile(
    r'''(?ix)(?:placeholder|redacted|changeme|none|null|true|false|password|secret|
    dummy|example|sample|test|x+|\*+|
    (?:your|put|insert|replace|dummy|example|sample|test)[_-]?(?:api[_-]?)?(?:key|token|secret|password)(?:[_-]?here)?|
    sk-(?:your|dummy|example|sample|test)[_-]?(?:api[_-]?)?(?:key|token)(?:[_-]?here)?)\Z''')


def _placeholder(value):
    value = value.strip()
    return (not value or bool(_PLACEHOLDER.fullmatch(value)) or
            (value.startswith('<') and value.endswith('>')) or
            value.startswith(('$', '{{')) or
            (value.isdecimal() and len(value) < 6))


def _credential_literal(name, value):
    quoted = len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]
    value = value[1:-1] if quoted else value
    if _placeholder(value):
        return False
    if not quoted:
        # Calls, lookups and variable references are code, not exposed literals.
        if any(c in value for c in '()[]{}') or _IDENTIFIER.fullmatch(value) or value.isdecimal():
            return False
    if name.lower() in ('password', 'passwd', 'pwd'):
        return len(value) >= 6 if quoted else len(value) >= 8 and any(not c.isalnum() for c in value)
    # Unknown API providers often use unprefixed random keys. Avoid treating
    # simple words such as secret="answer" as keys based only on a variable name.
    classes = sum((any(c.islower() for c in value), any(c.isupper() for c in value),
                   any(c.isdigit() for c in value), any(not c.isalnum() for c in value)))
    return len(value) >= 16 and classes >= 2


def contains_secret(text):
    if not isinstance(text, str) or not text:
        return False
    if _PRIVATE_KEY.search(text):
        return True
    if any(not _placeholder(match.group()) for match in _KEY.finditer(text)):
        return True
    return any(_credential_literal(match['name'], match['value']) for match in _ASSIGNMENT.finditer(text))


class _SecretSearch:
    """Compatibility for existing boolean-only SECRET.search(...) call sites."""
    search = staticmethod(contains_secret)


SECRET = _SecretSearch()
