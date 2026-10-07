"""Protect API Zoo keys with Windows DPAPI; runtime profiles remain unchanged.

Legacy plaintext profiles are accepted and upgraded on their next successful
save. Protected keys belong to the Windows user/machine that saved them.
"""
import base64
import copy
import ctypes as C
import os


def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Key này cần Windows và tài khoản đã lưu để giải mã; nhập lại API key.')
    from ctypes import wintypes as W
    class Blob(C.Structure):
        _fields_ = [('size', W.DWORD), ('data', C.POINTER(C.c_ubyte))]
    buffer = C.create_string_buffer(value)
    source = Blob(len(value), C.cast(buffer, C.POINTER(C.c_ubyte)))
    target = Blob()
    crypt = C.WinDLL('crypt32', use_last_error=True)
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [C.POINTER(Blob), C.c_void_p, C.c_void_p,
                          C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(Blob)]
    operation.restype = W.BOOL
    if not operation(C.byref(source), None, None, None, None, 1, C.byref(target)):
        raise ValueError('Không bảo vệ/giải mã được API key; dùng đúng tài khoản Windows hoặc nhập lại key.')
    try:
        return C.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def encode_profiles(data):
    stored = copy.deepcopy(data)
    for row in stored.get('profiles', []):
        if os.name == 'nt' and row.get('api_key'):
            row['api_key_protected'] = 'dpapi:v1:' + base64.b64encode(_crypt(row['api_key'].encode('utf-8'))).decode('ascii')
            row['api_key'] = ''
    return stored


def decode_profiles(data):
    stored = copy.deepcopy(data)
    if not isinstance(stored, dict) or not isinstance(stored.get('profiles', []), list):
        return stored  # The normal configuration validator reports this.
    for row in stored.get('profiles', []):
        if not isinstance(row, dict) or 'api_key_protected' not in row:
            continue
        value = row.pop('api_key_protected')
        if row.get('api_key') or not isinstance(value, str) or not value.startswith('dpapi:v1:'):
            raise ValueError('Cấu hình API key được bảo vệ không hợp lệ.')
        try:
            raw = base64.b64decode(value[9:], validate=True)
            row['api_key'] = _crypt(raw, decrypt=True).decode('utf-8')
        except (ValueError, UnicodeError):
            raise ValueError('Không giải mã được API key; dùng đúng tài khoản Windows hoặc nhập lại key.') from None
    return stored
