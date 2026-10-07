"""Reject malformed Unicode before it reaches storage, Win32 or the clipboard."""


def validate_unicode(value):
    """Validate strings in API JSON without changing valid text or exposing it."""
    if isinstance(value, str):
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('Phản hồi chứa Unicode không hợp lệ; clipboard giữ nguyên.') from None
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_unicode(key)
            validate_unicode(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_unicode(item)
    return value
