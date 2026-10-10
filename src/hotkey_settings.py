"""Stable action IDs with editable shortcuts; no keyboard hooks."""
import re

DEFAULTS = {201: 'F8', 202: 'F9', 203: 'F10', 204: 'F4', 207: 'F6',
            209: 'F3', 213: 'Shift+F10', 214: 'Shift+F9', 215: 'F7', 217: 'Shift+F7', 218: 'F2'}
LABELS = {201: 'Gửi chữ: câu hỏi mới', 202: 'Gửi chữ bổ sung', 203: 'Hủy yêu cầu / chọn vùng',
          204: 'Chụp câu hỏi mới', 207: 'Quản lý phiên', 209: 'Chọn mạng (nhấn hai lần)',
          213: 'Gửi lại yêu cầu', 214: 'Chụp ảnh bổ sung', 215: 'Copy đáp án gần nhất', 217: 'Copy đáp án phiên đang chọn',
          218: 'Agent tự làm trắc nghiệm (Cua MCP)'}
MODIFIERS = {'ctrl': ('Ctrl', 2), 'alt': ('Alt', 1), 'shift': ('Shift', 4), 'win': ('Win', 8)}

def parse_shortcut(text):
    if not isinstance(text, str) or len(text) > 60:
        raise ValueError('Phím tắt không hợp lệ')
    parts = [p.strip().lower() for p in text.split('+')]
    key = parts.pop() if parts else ''
    if len(set(parts)) != len(parts) or any(p not in MODIFIERS for p in parts):
        raise ValueError('Dùng Ctrl, Alt, Shift, Win và một phím')
    if re.fullmatch(r'f(?:[1-9]|1[0-9]|2[0-4])', key):
        vk, key = 0x6F + int(key[1:]), key.upper()
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk, key = ord(key.upper()), key.upper()
        if not parts:
            raise ValueError('Phím chữ/số cần Ctrl, Alt hoặc Win')
        if set(parts) <= {'shift'}:
            raise ValueError('Không chiếm phím gõ chữ/số')
    else:
        raise ValueError('Dùng F1–F24 hoặc chữ/số kèm Ctrl, Alt, Win')
    if key == 'F12' or (key == 'F4' and 'alt' in parts) or (key == 'L' and 'win' in parts):
        raise ValueError('Tổ hợp này dành cho Windows/đóng cửa sổ; chọn phím khác')
    order = [m for m in MODIFIERS if m in parts]
    canonical = '+'.join([MODIFIERS[m][0] for m in order] + [key])
    return canonical, sum(MODIFIERS[m][1] for m in order), vk

def normalized_shortcuts(overrides=None):
    overrides = {} if overrides is None else overrides
    # Ignore the removed chat shortcut in preferences saved by older versions.
    if not isinstance(overrides, dict) or not set(overrides) <= {str(i) for i in DEFAULTS} | {'212'}:
        raise ValueError('Danh sách chức năng phím tắt không hợp lệ')
    selected = {str(i): parse_shortcut(overrides.get(str(i), default))[0] for i, default in DEFAULTS.items()}
    # Preserve an older custom F2 binding when adding the new agent action.
    if '218' not in overrides and selected['218'] in {v for k, v in selected.items() if k != '218'}:
        used = {v for k, v in selected.items() if k != '218'}
        selected['218'] = next(name for name in ('Ctrl+Alt+F2', 'Ctrl+Shift+F2', 'Alt+Shift+F2',
            'Ctrl+Alt+Shift+F2', 'Ctrl+Alt+F1', 'Ctrl+Alt+F5', 'Ctrl+Alt+F11', 'Ctrl+Alt+F13',
            'Ctrl+Alt+F14', 'Ctrl+Alt+F15', 'Ctrl+Alt+F16') if name not in used)
    if selected['215'] == 'Shift+F8':
        selected['215'] = 'F7'  # Upgrade the previous default; custom bindings stay.
    return selected


def disabled_actions(disabled=None):
    disabled = [] if disabled is None else disabled
    if (not isinstance(disabled, list) or
            any(not isinstance(i, str) or i not in {str(i) for i in DEFAULTS} | {'212'} for i in disabled)):
        raise ValueError('Danh sách phím tắt đã tắt không hợp lệ')
    return [str(i) for i in DEFAULTS if str(i) in disabled]


def bindings(overrides=None, disabled=None):
    selected = normalized_shortcuts(overrides)
    disabled = disabled_actions(disabled)
    found, result = set(), {}
    for ident in DEFAULTS:
        if str(ident) in disabled:
            continue
        name, modifiers, vk = parse_shortcut(selected[str(ident)])
        if (modifiers, vk) in found:
            raise ValueError('Phím bị trùng: ' + name)
        found.add((modifiers, vk))
        result[ident] = (name, modifiers, vk)
    return result

def open_editor(config, results):
    import tkinter as tk
    from tkinter import ttk
    import queue
    root = tk.Tk()
    root.title('Phím tắt ClipboardAI')
    frame = ttk.Frame(root, padding=14)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Bấm ô rồi nhấn tổ hợp mới, hoặc nhập Ctrl+Alt+Q. Esc luôn hủy chọn vùng.').grid(columnspan=2, sticky='w')
    values, enabled, fields = {}, {}, []
    selected = normalized_shortcuts(config.get('HOTKEYS'))
    disabled = disabled_actions(config.get('HOTKEYS_DISABLED'))
    for row, ident in enumerate(DEFAULTS, 1):
        ttk.Label(frame, text=LABELS[ident]).grid(row=row, column=0, sticky='w', pady=4)
        variable = tk.StringVar(master=root, value=selected[str(ident)])
        field = ttk.Entry(frame, textvariable=variable, width=24)
        field.grid(row=row, column=1, padx=(20, 0), pady=4)
        def record(event, value=variable):
            key = event.keysym.upper()
            if key.startswith('F') and key[1:].isdigit() or len(key) == 1 and key.isalnum() and event.state & (0x4 | 0x20000):
                modifiers = (['Ctrl'] if event.state & 0x4 else []) + (['Alt'] if event.state & 0x20000 else []) + (['Shift'] if event.state & 0x1 else [])
                value.set('+'.join(modifiers + [key]))
                return 'break'
        field.bind('<KeyPress>', record)
        active = tk.BooleanVar(master=root, value=str(ident) not in disabled)
        def toggle(entry=field, value=active):
            entry.state(['!disabled'] if value.get() else ['disabled'])
        ttk.Checkbutton(frame, text='Bật', variable=active, command=toggle).grid(row=row, column=2, sticky='w')
        toggle()
        fields.append(field)
        values[str(ident)] = variable
        enabled[str(ident)] = active
    message = tk.StringVar(master=root, value='Đang mở: tạm trả phím cho app khác; đóng cửa sổ để bật lại. Phím đăng ký sẽ chặn thao tác gốc.')
    ttk.Label(frame, textvariable=message, wraplength=570).grid(row=12, columnspan=3, sticky='w', pady=12)
    replies = queue.Queue()
    def save():
        try:
            selected = {key: value.get() for key, value in values.items()}
            disabled = [key for key, value in enabled.items() if not value.get()]
            bindings(selected, disabled)
            results.put(('hotkey_save', normalized_shortcuts(selected), replies, disabled))
            button.state(['disabled'])
        except ValueError as exc:
            message.set(str(exc))
    def poll():
        try:
            reply = replies.get_nowait()
            message.set(reply)
            button.state(['!disabled'])
        except queue.Empty:
            pass
        root.after(80, poll)
    def reset():
        for key, variable in values.items():
            variable.set(DEFAULTS[int(key)])
            enabled[key].set(True)
        for field in fields:
            field.state(['!disabled'])
        message.set('Đã điền phím mặc định; bấm Lưu để áp dụng.')
    ttk.Button(frame, text='Khôi phục mặc định', command=reset).grid(row=13, column=0, sticky='w')
    button = ttk.Button(frame, text='Lưu', command=save)
    button.grid(row=13, column=1, sticky='e')
    try:
        from window_layout import place_tk
        place_tk(root, config, keep_size=True)
        poll()
        root.mainloop()
    finally:
        try:
            for timer in root.tk.call('after', 'info'):
                root.after_cancel(timer)
        except tk.TclError:
            pass
        results.put(('hotkey_closed',))
