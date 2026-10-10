"""Provider presets, editable model suggestions, discovery and native login."""
import copy
from pathlib import Path
import queue
import threading
import uuid
from urllib.parse import urlsplit
import webbrowser
from api_zoo import ZooStore, seed_profiles, validate, consolidate, discover_models, update_catalog
from provider_catalog import PRESETS, preset_for, suggestions, api_key_page
from browser_provider import BrowserSession


def model_choice(value, label):
    value = value.strip()
    if len(value) > 200 or any(character.isspace() for character in value):
        raise ValueError(label + ' không hợp lệ: tối đa 200 ký tự, không chứa khoảng trắng.')
    return value


def catalog_result(event, pending_token, selected_id, choices, edited_choices=(), preserve_empty_vision=False):
    """Keep valid metadata separate from a form the user may still be editing."""
    if event[0] != 'models' or event[1] != pending_token or event[2]['id'] != selected_id:
        return None
    profile = update_catalog(event[2], event[3])
    form_choices = {field: profile[field] for field in ('model', 'vision_model')}
    capabilities = {item['id']: item['vision'] for item in event[3]}
    for field in ('model', 'vision_model'):
        chosen = choices[field].strip()
        preserve = bool(chosen) or field in edited_choices or field == 'vision_model' and preserve_empty_vision
        if preserve and not (field == 'vision_model' and capabilities.get(chosen) is False):
            form_choices[field] = chosen
            try:
                profile[field] = model_choice(chosen, field)
            except ValueError:
                # A partial/invalid entry stays in the widget and is reported on
                # Save; it must not poison another profile's metadata or save.
                pass
    return profile, form_choices


def open_editor(root_path, config, results):
    import tkinter as tk
    window = None
    try:
        window = tk.Tk()
        _open_editor(window, root_path, config, results)
    finally:
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
        results.put(('zoo_closed',))


def _open_editor(window, root_path, config, results):
    import tkinter as tk
    from tkinter import ttk, messagebox
    window.title('API Zoo')
    window.geometry('740x620')
    window.minsize(650, 610)
    from window_layout import place_tk
    place_tk(window, config, keep_size=True)
    local = queue.Queue()
    auth_root = str(Path(root_path).resolve() / '.clipboardai-auth')
    try:
        data = ZooStore(root_path).load()
    except (OSError, ValueError, TypeError):
        messagebox.showerror('API Zoo', 'Không đọc/giải mã được cấu hình. Khôi phục bản sao bằng đúng tài khoản Windows, hoặc đổi tên api_zoo.json rồi thêm API lại. File cũ được giữ nguyên.', parent=window)
        window.destroy()
        return
    if not data['profiles'] and not ZooStore(root_path).path.exists():
        data = seed_profiles(config)
    data = copy.deepcopy(data)
    selected, pending, timer, loading = [None], [0], [None], [False]
    credential_host, operation_cancel = [None], [None]
    worker, waiting, closed, poll_timer = [None], [None], [False], [None]
    deferred_edits = set()
    browser_ready = set()
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(frame, columns=('api', 'models'), show='headings', height=4)
    tree.heading('api', text='API · ★ đang dùng')
    tree.heading('models', text='Model')
    tree.column('api', width=380)
    tree.column('models', width=260)
    tree.pack(fill='x')
    form = ttk.Frame(frame)
    form.pack(fill='x', pady=10)
    preset = tk.StringVar(master=window, value='Custom · OpenAI-compatible')
    protocol = ['compatible']
    ttk.Label(form, text='Nhà cung cấp').grid(row=0, column=0, sticky='w')
    providers = ttk.Combobox(form, name='provider', textvariable=preset, state='readonly', values=list(PRESETS))
    providers.grid(row=0, column=1, sticky='ew', padx=(10, 0), pady=3)
    fields = {field: tk.StringVar(master=window) for field in ('name', 'base_url', 'api_key')}
    entries = {}
    for row, (field, label) in enumerate((('name', 'Tên'), ('base_url', 'Endpoint'), ('api_key', 'API key')), 1):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=3)
        entry = ttk.Entry(form, textvariable=fields[field], show='•' if field == 'api_key' else '')
        entry.grid(row=row, column=1, sticky='ew', padx=(10, 0), pady=3)
        entries[field] = entry
    form.columnconfigure(1, weight=1)
    model, vision = tk.StringVar(master=window), tk.StringVar(master=window)
    edited_choices, updating_choices = set(), [False]
    def choice_changed(field):
        if not updating_choices[0]:
            edited_choices.add(field)
    model.trace_add('write', lambda *_: choice_changed('model'))
    vision.trace_add('write', lambda *_: choice_changed('vision_model'))
    ttk.Label(form, text='Model trả lời').grid(row=4, column=0, sticky='w', pady=3)
    chooser = ttk.Combobox(form, name='model', textvariable=model, state='normal')
    chooser.grid(row=4, column=1, sticky='ew', padx=(10, 0), pady=3)
    ttk.Label(form, text='Model đọc ảnh').grid(row=5, column=0, sticky='w', pady=3)
    image_chooser = ttk.Combobox(form, name='vision', textvariable=vision, state='normal')
    image_chooser.grid(row=5, column=1, sticky='ew', padx=(10, 0), pady=3)
    effort = tk.StringVar(master=window, value='medium')
    ttk.Label(form, text='Reasoning mặc định').grid(row=6, column=0, sticky='w', pady=3)
    from model_reasoning import LEVELS
    ttk.Combobox(form, name='effort', textvariable=effort, state='readonly', values=LEVELS).grid(row=6, column=1, sticky='ew', padx=(10, 0), pady=3)
    ttk.Label(frame, text='Chọn gợi ý hoặc nhập ID model. Để trống model đọc ảnh nếu API chỉ hỗ trợ chữ.', wraplength=690).pack(anchor='w')
    auth_bar = ttk.Frame(frame)
    auth_bar.pack(fill='x', pady=8)
    auto = tk.BooleanVar(master=window, value=data['auto'])
    ttk.Checkbutton(frame, text='Tự chuyển API khi lỗi', variable=auto).pack(anchor='w')
    status = tk.StringVar(master=window, value='Chọn nhà cung cấp để điền endpoint và gợi ý model.')
    ttk.Label(frame, textvariable=status, wraplength=690).pack(anchor='w', pady=8)
    bar = ttk.Frame(frame)
    bar.pack(fill='x', side='bottom')

    def invalidate():
        pending[0] += 1
        loading[0] = False
        if operation_cancel[0]:
            operation_cancel[0].set()
            operation_cancel[0] = None
        if timer[0]:
            try:
                window.after_cancel(timer[0])
            except tk.TclError:
                pass
            timer[0] = None
        waiting[0] = None
        for ident in tuple(deferred_edits):
            try:
                window.after_cancel(ident)
            except tk.TclError:
                pass
        deferred_edits.clear()

    def redraw():
        tree.delete(*tree.get_children())
        for p in sorted(data['profiles'], key=lambda p: p['priority']):
            tree.insert('', 'end', iid=p['id'], values=(('★ ' if p['id'] == data['primary'] else '') + p['name'], p['model'] or 'chưa chọn'))

    def current():
        old = next((p for p in data['profiles'] if p['id'] == selected[0]), {})
        p = dict(old, **{k: v.get().strip() for k, v in fields.items()})
        if not selected[0]:
            selected[0] = uuid.uuid4().hex
        p['id'] = selected[0]
        p['name'] = p['name'] or urlsplit(p['base_url']).hostname or 'API'
        p.update(provider=protocol[0], model=model_choice(model.get(), 'Model trả lời'),
                 vision_model=model_choice(vision.get(), 'Model đọc ảnh'), enabled=True, reasoning_effort=effort.get())
        if effort.get() != old.get('reasoning_effort', 'medium'):
            p['model_efforts'] = {}  # A changed profile default replaces quick-menu overrides.
        p.setdefault('priority', len(data['profiles']))
        p.setdefault('timeout', 0)
        p.setdefault('max_tokens', 0)
        if p.get('api_key') != old.get('api_key') or p.get('base_url', '').rstrip('/') != old.get('base_url') or p['provider'] != old.get('provider'):
            p['models'] = []
        p.setdefault('models', [])
        return validate({'profiles': [p]})['profiles'][0]

    def auth_controls():
        browser = protocol[0] == 'codex'
        entries['api_key'].configure(state='disabled' if browser else 'normal')
        entries['base_url'].configure(state='disabled' if browser else 'normal')
        auth_button.configure(text='Đăng nhập ChatGPT' if browser else 'Mở trang API key')
        logout_button.configure(state='normal' if browser else 'disabled')

    def set_catalog(catalog, selected_model='', selected_vision=''):
        chooser['values'] = [m['id'] for m in catalog]
        image_chooser['values'] = [m['id'] for m in catalog if m['vision'] is not False]
        updating_choices[0] = True
        try:
            model.set(selected_model)
            vision.set(selected_vision)
        finally:
            updating_choices[0] = False
        edited_choices.clear()

    def choose_preset(_=None):
        invalidate()
        effort.set('medium')
        label = preset.get()
        protocol[0], endpoint, _, _ = PRESETS[label]
        fields['name'].set(label)
        fields['base_url'].set(endpoint)
        fields['api_key'].set('')
        credential_host[0] = urlsplit(endpoint).hostname
        catalog = suggestions(label)
        chosen = catalog[0]['id'] if catalog else ''
        set_catalog(catalog, chosen, chosen if catalog and catalog[0]['vision'] is True else '')
        auth_controls()
        status.set('Đăng nhập ChatGPT bằng Codex CLI chính thức; không cần API key. Model sẽ được lấy sau khi đăng nhập.' if protocol[0] == 'codex' else 'Đây là gợi ý model, không xác nhận quyền sử dụng. Nhập key và Lấy lại model để ưu tiên danh sách API.')

    def pick(_=None):
        if not tree.selection():
            return
        invalidate()
        p = next(p for p in data['profiles'] if p['id'] == tree.selection()[0])
        selected[0] = p['id']
        credential_host[0] = urlsplit(p['base_url']).hostname
        protocol[0] = p['provider']
        effort.set(p.get('reasoning_effort', 'medium'))
        preset.set(preset_for(p))
        for k, variable in fields.items():
            variable.set(p[k])
        set_catalog(p['models'] or suggestions(preset.get()), p['model'], p['vision_model'])
        auth_controls()
        status.set('Chọn/nhập model rồi Lưu. Dùng Lấy lại model để kiểm tra danh sách hiện tại.')
        if protocol[0] == 'codex' or not p['models']:
            timer[0] = window.after(100, fetch)

    def new():
        invalidate()
        selected[0] = None
        credential_host[0] = None
        tree.selection_remove(*tree.selection())
        preset.set('Custom · OpenAI-compatible')
        choose_preset()
        entries['base_url'].focus_set()

    def start(operation):
        try:
            p = current()
            if protocol[0] != 'codex' and not p['api_key']:
                status.set('Nhập API key của nhà cung cấp này.')
                return
        except (ValueError, TypeError) as exc:
            status.set(str(exc))
            return
        invalidate()
        token = pending[0]
        cancel = threading.Event()
        operation_cancel[0] = cancel
        loading[0] = True
        status.set('Hoàn tất đăng nhập trong trình duyệt (tối đa 5 phút); có thể bấm Hủy.' if operation == 'login' else 'Đang kiểm tra phiên/lấy model…')
        if worker[0] is not None and worker[0].is_alive():
            waiting[0] = (p, token, cancel, operation)
            status.set('Đang hủy thao tác trước; sẽ kiểm tra cấu hình mới ngay sau đó…')
        else:
            launch(p, token, cancel, operation)

    def launch(p, token, cancel, operation):
        def run():
            try:
                if operation in ('login', 'logout'):
                    with BrowserSession(p, auth_root, cancel) as session:
                        if operation == 'logout':
                            session.logout()
                            local.put(('logout', token, p))
                            return
                        catalog = session.login()
                else:
                    catalog = discover_models(p, auth_root, cancel=cancel)
                local.put(('models', token, p, catalog))
            except InterruptedError:
                local.put(('error', token, 'Đã hủy đăng nhập/thao tác.'))
            except Exception as exc:
                code = getattr(exc, 'code', getattr(exc, 'status', None))
                message = str(exc) if isinstance(exc, RuntimeError) and p['provider'] == 'codex' else ('Không lấy được model' + (f' (HTTP {code}).' if isinstance(code, int) else '; kiểm tra endpoint/key hoặc nhập ID model thủ công.'))
                local.put(('error', token, message))
        worker[0] = threading.Thread(target=run, daemon=True)
        worker[0].start()

    def fetch():
        if timer[0]:
            window.after_cancel(timer[0])
            timer[0] = None
        start('models')

    def authenticate():
        if protocol[0] == 'codex':
            start('login')
        else:
            try:
                page = api_key_page(current())
                if page:
                    webbrowser.open(page)
                else:
                    status.set('Dùng trang quản lý key của nhà cung cấp gateway riêng.')
            except ValueError as exc:
                status.set(str(exc))

    def cancel_operation():
        invalidate()
        status.set('Đã hủy thao tác; cấu hình đã lưu được giữ nguyên.')

    def edited(_=None):
        try:
            host = urlsplit(fields['base_url'].get().strip()).hostname
        except ValueError:
            host = None
        if host:
            if credential_host[0] and credential_host[0] != host:
                fields['api_key'].set('')
            credential_host[0] = host
        invalidate()
        chooser['values'] = []
        image_chooser['values'] = []
        timer[0] = window.after(900, fetch)

    def queue_edit():
        if closed[0]:
            return
        ident = [None]
        def apply():
            deferred_edits.discard(ident[0])
            if not closed[0]:
                edited()
        ident[0] = window.after(20, apply)
        deferred_edits.add(ident[0])
    for field in ('base_url', 'api_key'):
        entries[field].bind('<KeyRelease>', edited)
        entries[field].bind('<<Paste>>', lambda event: queue_edit())
        entries[field].bind('<FocusOut>', lambda event: queue_edit() if not model.get() else None)

    def delete():
        if selected[0] is None:
            return
        data['profiles'] = [p for p in data['profiles'] if p['id'] != selected[0]]
        if data['primary'] == selected[0]:
            data['primary'] = ''
        data['auto'] = auto.get()
        results.put(('zoo_save', copy.deepcopy(data), local))
        new()
        redraw()

    def save():
        if loading[0]:
            status.set('Đợi thao tác xong hoặc Hủy trước khi Lưu.')
            return
        try:
            p = current()
            if not p['model'] or (not p['api_key'] and p['provider'] != 'codex'):
                status.set('Cần chọn/nhập model và API key (hoặc đăng nhập ChatGPT).')
                return
            if p['provider'] == 'codex' and p['id'] not in browser_ready:
                status.set('Đăng nhập ChatGPT hoặc Lấy lại model để kiểm tra phiên trước khi Lưu.')
                return
            if not p['models']:
                p['models'] = suggestions(preset.get())
            known = {m['id'] for m in p['models']}
            for ident in (p['model'], p['vision_model']):
                if ident and ident not in known:
                    p['models'].append({'id': ident, 'vision': None})
                    known.add(ident)
            data['profiles'] = [old for old in data['profiles'] if old['id'] != p['id']] + [p]
            data.update(primary=p['id'], auto=auto.get())
            data.update(consolidate(data))
            selected[0] = data['primary']
            results.put(('zoo_save', copy.deepcopy(validate(data)), local))
            redraw()
        except (ValueError, TypeError) as exc:
            status.set(str(exc))
    auth_button = ttk.Button(auth_bar, text='Mở trang API key', command=authenticate)
    auth_button.pack(side='left', padx=(0, 6))
    logout_button = ttk.Button(auth_bar, text='Đăng xuất', command=lambda: start('logout'), state='disabled')
    logout_button.pack(side='left', padx=(0, 6))
    ttk.Button(auth_bar, text='Hủy', command=cancel_operation).pack(side='left')
    for label, command in (('+ API', new), ('Xóa', delete), ('Lấy lại model', fetch)):
        ttk.Button(bar, text=label, command=command).pack(side='left', padx=(0, 6))
    ttk.Button(bar, text='Lưu', command=save).pack(side='right')
    providers.bind('<<ComboboxSelected>>', choose_preset)
    tree.bind('<<TreeviewSelect>>', pick)

    def poll():
        if closed[0]:
            return
        if worker[0] is not None and not worker[0].is_alive():
            worker[0] = None
            next_operation, waiting[0] = waiting[0], None
            if next_operation and next_operation[1] == pending[0] and not next_operation[2].is_set():
                launch(*next_operation)
        try:
            while True:
                event = local.get_nowait()
                if event[0] in ('models', 'error', 'logout'):
                    if event[1] != pending[0]:
                        continue
                    loading[0] = False
                    operation_cancel[0] = None
                    if event[0] == 'error':
                        status.set(event[2])
                        continue
                    if event[0] == 'logout':
                        browser_ready.discard(event[2]['id'])
                        status.set('Đã đăng xuất phiên ChatGPT của API này. Đăng nhập lại trước khi gửi.')
                        continue
                    applied = catalog_result(event, pending[0], selected[0],
                        {'model': model.get(), 'vision_model': vision.get()}, edited_choices,
                        preserve_empty_vision=any(old['id'] == event[2]['id'] for old in data['profiles']))
                    if applied is None:
                        continue
                    p, form_choices = applied
                    if p['provider'] == 'codex':
                        browser_ready.add(p['id'])
                    selected[0] = p['id']
                    credential_host[0] = urlsplit(p['base_url']).hostname
                    data['profiles'] = [old for old in data['profiles'] if old['id'] != p['id']] + [p]
                    if not fields['name'].get():
                        fields['name'].set(p['name'])
                    set_catalog(p['models'], form_choices['model'], form_choices['vision_model'])
                    redraw()
                    status.set(f'Đã lấy {len(p["models"])} model. Danh sách không bảo đảm quota/quyền gọi; chọn model rồi Lưu.')
                else:
                    status.set(event[1])
        except queue.Empty:
            pass
        poll_timer[0] = window.after(150, poll)

    def close():
        closed[0] = True
        invalidate()
        if poll_timer[0]:
            window.after_cancel(poll_timer[0])
            poll_timer[0] = None
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    redraw()
    if data['profiles']:
        tree.selection_set(data['primary'] or data['profiles'][0]['id'])
        pick()
    poll()
    window.update_idletasks()
    results.put(('zoo_opened', window.winfo_id()))
    if frame.winfo_reqheight() + 24 > 580:
        window.geometry(f'740x{frame.winfo_reqheight() + 24}')
    place_tk(window, config, keep_size=True)
    try:
        window.mainloop()
    finally:
        closed[0] = True
        invalidate()
        if poll_timer[0]:
            try:
                window.after_cancel(poll_timer[0])
            except tk.TclError:
                window.tk.call('after', 'cancel', poll_timer[0])
            poll_timer[0] = None
