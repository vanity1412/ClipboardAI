"""Compact provider editor: endpoint/key → discovered model dropdown."""
import copy
import queue
import threading
import uuid
from urllib.parse import urlsplit
from api_zoo import ZooStore, seed_profiles, validate, consolidate, discover_models, update_catalog


def open_editor(root_path, config, results):
    import tkinter as tk
    from tkinter import ttk, messagebox
    window = tk.Tk()
    window.title('API Zoo')
    window.geometry('600x350')
    window.minsize(560, 350)
    local = queue.Queue()
    try:
        data = ZooStore(root_path).load()
    except (OSError, ValueError, TypeError):
        messagebox.showerror('API Zoo', 'File cấu hình lỗi; file cũ được giữ nguyên.', parent=window)
        window.destroy()
        results.put(('zoo_closed',))
        return
    if not data['profiles'] and not ZooStore(root_path).path.exists():
        data = seed_profiles(config)
    data = copy.deepcopy(data)
    selected, pending, timer, loading = [None], [0], [None], [False]
    credential_host = [None]
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(frame, columns=('api', 'models'), show='headings', height=4)
    tree.heading('api', text='API · ★ đang dùng')
    tree.heading('models', text='Model')
    tree.column('api', width=330)
    tree.column('models', width=210)
    tree.pack(fill='x')
    form = ttk.Frame(frame)
    form.pack(fill='x', pady=10)
    fields = {field: tk.StringVar() for field in ('name', 'base_url', 'api_key')}
    entries = {}
    for row, (field, label) in enumerate((('name', 'Tên'), ('base_url', 'Endpoint'), ('api_key', 'API key'))):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=3)
        entry = ttk.Entry(form, textvariable=fields[field], show='•' if field == 'api_key' else '')
        entry.grid(row=row, column=1, sticky='ew', padx=(10, 0), pady=3)
        entries[field] = entry
    form.columnconfigure(1, weight=1)
    ttk.Label(form, text='Model').grid(row=3, column=0, sticky='w', pady=3)
    model = tk.StringVar()
    chooser = ttk.Combobox(form, textvariable=model, state='readonly')
    chooser.grid(row=3, column=1, sticky='ew', padx=(10, 0), pady=3)
    auto = tk.BooleanVar(value=data['auto'])
    ttk.Checkbutton(frame, text='Tự chuyển API khi lỗi', variable=auto).pack(anchor='w')
    status = tk.StringVar(value='Nhập endpoint và key để tự lấy model từ API.')
    ttk.Label(frame, textvariable=status, wraplength=590).pack(anchor='w', pady=8)
    bar = ttk.Frame(frame)
    bar.pack(fill='x')

    def redraw():
        tree.delete(*tree.get_children())
        for p in sorted(data['profiles'], key=lambda p: p['priority']):
            tree.insert('', 'end', iid=p['id'], values=(('★ ' if p['id'] == data['primary'] else '') + p['name'], str(len(p['models'])) + ' model'))

    def current():
        old = next((p for p in data['profiles'] if p['id'] == selected[0]), {})
        p = dict(old, **{k: v.get().strip() for k, v in fields.items()})
        p['id'] = selected[0] or uuid.uuid4().hex
        host = urlsplit(p['base_url']).hostname or ''
        p['name'] = p['name'] or host
        p.setdefault('provider', 'deepseek' if host == 'api.deepseek.com' else 'compatible')
        p.setdefault('priority', len(data['profiles']))
        p.setdefault('timeout', 0)
        p.setdefault('max_tokens', 0)
        p.setdefault('vision_model', '')
        p.setdefault('models', [])
        p['enabled'] = True
        if p.get('api_key') != old.get('api_key') or p.get('base_url', '').rstrip('/') != old.get('base_url'):
            p.update(models=[], model='', vision_model='')
        else:
            p['model'] = model.get()
        p['provider'] = 'deepseek' if host == 'api.deepseek.com' else 'compatible'
        return validate({'profiles': [p]})['profiles'][0]

    def pick(_=None):
        if not tree.selection():
            return
        pending[0] += 1
        loading[0] = False
        p = next(p for p in data['profiles'] if p['id'] == tree.selection()[0])
        selected[0] = p['id']
        credential_host[0] = urlsplit(p['base_url']).hostname
        for k, variable in fields.items():
            variable.set(p[k])
        chooser['values'] = [m['id'] for m in p['models']]
        model.set(p['model'] if p['model'] in chooser['values'] else '')
        status.set('Chọn model rồi Lưu để dùng API này.' if p['models'] else 'Đang lấy danh sách model…')
        if not p['models']:
            window.after(100, fetch)

    def new():
        pending[0] += 1
        loading[0] = False
        selected[0] = None
        credential_host[0] = None
        tree.selection_remove(*tree.selection())
        for variable in fields.values():
            variable.set('')
        chooser['values'] = []
        model.set('')
        entries['base_url'].focus_set()
        status.set('Nhập endpoint HTTPS và key. Danh sách model sẽ tự tải.')

    def fetch():
        if timer[0]:
            window.after_cancel(timer[0])
            timer[0] = None
        try:
            p = current()
            if not p['api_key']:
                return
        except (ValueError, TypeError):
            return
        pending[0] += 1
        token = pending[0]
        loading[0] = True
        status.set('Đang lấy model từ API…')
        def run():
            try:
                local.put(('models', token, p, discover_models(p)))
            except Exception as exc:
                code = getattr(exc, 'code', getattr(exc, 'status', None))
                local.put(('error', token, 'Không lấy được model' + (f' (HTTP {code}).' if isinstance(code, int) else '; kiểm tra endpoint/key hoặc /models.')))
        threading.Thread(target=run, daemon=True).start()

    def edited(_=None):
        try:
            host = urlsplit(fields['base_url'].get().strip()).hostname
        except ValueError:
            host = None
        if host:
            if credential_host[0] and credential_host[0] != host:
                # A saved key belongs to its original host. A new endpoint
                # must receive its own key before automatic discovery runs.
                fields['api_key'].set('')
            credential_host[0] = host
        if timer[0]:
            window.after_cancel(timer[0])
        pending[0] += 1
        loading[0] = False
        chooser['values'] = []
        model.set('')
        timer[0] = window.after(900, fetch)
    for field in ('base_url', 'api_key'):
        entries[field].bind('<KeyRelease>', edited)
        entries[field].bind('<<Paste>>', lambda event: window.after(20, edited))
        entries[field].bind('<FocusOut>', lambda event: window.after(20, edited) if not model.get() else None)

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
            status.set('Đợi tải model xong rồi Lưu.')
            return
        try:
            p = current()
            if not p['models'] or model.get() not in {m['id'] for m in p['models']}:
                status.set('Lấy model từ API rồi chọn trong danh sách trước khi Lưu.')
                fetch()
                return
            p['model'] = model.get()
            selected[0] = p['id']
            data['profiles'] = [old for old in data['profiles'] if old['id'] != p['id']] + [p]
            data.update(primary=p['id'], auto=auto.get())
            data.update(consolidate(data))
            selected[0] = data['primary']
            results.put(('zoo_save', copy.deepcopy(validate(data)), local))
            redraw()
        except (ValueError, TypeError) as exc:
            status.set(str(exc))
    for label, command in (('+ API', new), ('Xóa', delete), ('Lấy lại model', fetch)):
        ttk.Button(bar, text=label, command=command).pack(side='left', padx=(0, 6))
    ttk.Button(bar, text='Lưu', command=save).pack(side='right')
    tree.bind('<<TreeviewSelect>>', pick)
    def poll():
        try:
            while True:
                event = local.get_nowait()
                if event[0] in ('models', 'error'):
                    if event[1] != pending[0]:
                        continue
                    loading[0] = False
                    if event[0] == 'error':
                        status.set(event[2])
                        continue
                    p = update_catalog(event[2], event[3])
                    selected[0] = p['id']
                    credential_host[0] = urlsplit(p['base_url']).hostname
                    data['profiles'] = [old for old in data['profiles'] if old['id'] != p['id']] + [p]
                    if not fields['name'].get():
                        fields['name'].set(p['name'])
                    chooser['values'] = [m['id'] for m in p['models']]
                    model.set(p['model'])
                    redraw()
                    status.set(f'Đã lấy {len(p["models"])} model. Chọn model rồi Lưu.')
                else:
                    status.set(event[1])
        except queue.Empty:
            pass
        window.after(150, poll)
    redraw()
    if data['profiles']:
        tree.selection_set(data['primary'] or data['profiles'][0]['id'])
        pick()
    poll()
    window.update_idletasks()
    results.put(('zoo_opened', window.winfo_id()))
    if frame.winfo_reqheight() + 24 > 350:
        window.geometry(f'600x{frame.winfo_reqheight() + 24}')
    try:
        window.mainloop()
    finally:
        results.put(('zoo_closed',))
