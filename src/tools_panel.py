"""Local management panel. Mutations are queued to the native owner thread."""
from conversation_tools import search_records, export_record, backup_archive


def open_tools(snapshot, images, results, cancel):
    import tkinter as tk
    from tkinter import ttk, filedialog, simpledialog, messagebox
    root, timer, query, status, trace = None, None, None, None, None
    width = height = position = None
    try:
        root = tk.Tk()
        root.title('Hội thoại · Chẩn đoán · Riêng tư')
        tabs = ttk.Notebook(root)
        tabs.pack(fill='both', expand=True, padx=10, pady=10)
        conversations, diagnostics, privacy, appearance = [ttk.Frame(tabs, padding=10) for _ in range(4)]
        for pane, title in zip((conversations, diagnostics, privacy, appearance), ('Hội thoại', 'Chẩn đoán', 'Riêng tư', 'Giao diện')):
            tabs.add(pane, text=title)
        status = tk.StringVar(master=root)
        ttk.Label(root, textvariable=status, wraplength=360).pack(fill='x', padx=12)
        query = tk.StringVar(master=root)
        ttk.Label(conversations, text='Tìm theo tên và toàn bộ nội dung:').pack(anchor='w')
        ttk.Entry(conversations, textvariable=query).pack(fill='x', pady=6)
        listing = tk.Listbox(conversations, exportselection=False)
        listing.pack(fill='both', expand=True)
        records = []
        def refresh(*_):
            records[:] = search_records(snapshot['archive'], query.get())
            listing.delete(0, 'end')
            for record in records:
                title = record.get('title') or record.get('problem') or record.get('last_request') or 'Hội thoại trống'
                listing.insert('end', title.replace('\n', ' ')[:120])
        trace = query.trace_add('write', refresh)
        refresh()
        def selected():
            return records[listing.curselection()[0]] if listing.curselection() else None
        def action(name, value=None):
            results.put(('tools_action', name, value))
            root.quit()
        def choose():
            record = selected()
            if record:
                action('select', record['id'])
        def rename():
            record = selected()
            if record:
                title = simpledialog.askstring('Đổi tên', 'Tên hội thoại (1–120 ký tự):',
                                               initialvalue=record.get('title', ''), parent=root)
                if title:
                    action('rename', (record['id'], title))
        def export():
            record = selected()
            if not record:
                return
            if snapshot['private'] and not messagebox.askyesno('Xuất dữ liệu riêng tư',
                    'Xuất sẽ ghi nội dung riêng tư ra ổ đĩa. Tiếp tục?', parent=root):
                return
            path = filedialog.asksaveasfilename(parent=root, defaultextension='.md',
                    filetypes=[('Markdown', '*.md'), ('JSON', '*.json')])
            if path:
                try:
                    export_record(record, path)
                    status.set('Đã xuất hội thoại')
                except (OSError, ValueError):
                    status.set('Không ghi được file; kiểm tra vị trí/quyền ghi')
        def backup():
            path = filedialog.asksaveasfilename(parent=root, defaultextension='.zip', filetypes=[('ZIP', '*.zip')])
            if path:
                try:
                    backup_archive(snapshot['archive'], images, path)
                    status.set('Đã sao lưu hội thoại và ảnh; không gồm khóa API')
                except Exception:
                    status.set('Sao lưu thất bại; kiểm tra ổ đĩa và kho ảnh')
        bar = ttk.Frame(conversations)
        bar.pack(fill='x', pady=8)
        for column in (0, 1):
            bar.columnconfigure(column, weight=1)
        for index, (label, command) in enumerate((('Mở phiên', choose), ('Đổi tên', rename), ('Xuất MD / JSON', export))):
            ttk.Button(bar, text=label, command=command).grid(row=index//2, column=index%2, sticky='ew', padx=3, pady=3)
        ttk.Button(bar, text='Sao lưu ZIP', command=backup,
                   state='disabled' if snapshot['private'] else 'normal').grid(row=1, column=1, sticky='ew', padx=3, pady=3)
        text = tk.Text(diagnostics, wrap='word', height=22)
        text.pack(fill='both', expand=True)
        stats = snapshot['stats']
        content = (f"Thống kê từ lúc mở app: {stats['calls']} lần gọi (gồm thất bại, tóm tắt, dự phòng).\n"
                   f"Token đã được provider báo: vào {stats['input']:,} · ra {stats['output']:,}.\n"
                   f"{stats['unknown']} lần không báo đủ token; không tính các lần này thành 0.\n"
                   f"Model đang chọn: {snapshot['model']}\nProvider/model lần hoàn tất: {snapshot.get('last_provider') or 'Chưa có'}\n"
                   f"Trạng thái yêu cầu: {snapshot.get('last_status', '')}\n"
                   f"Proxy hệ thống (đã bỏ thông tin đăng nhập): {snapshot['proxies'] or 'Không cấu hình'}\n"
                   'Danh sách 200 lần gọi gần nhất, mới nhất ở cuối:\n\n')
        for row in stats['rows']:
            content += (f"{row['phase']} | {row['provider']} | {row['model']} | {row['seconds']}s | "
                        f"token {row['input'] if row['input'] is not None else '?'} / {row['output'] if row['output'] is not None else '?'} | "
                        f"{('Lỗi: ' + row['error']) if row['error'] else 'Đã nhận phản hồi'}\n")
        text.insert('1.0', content)
        text.configure(state='disabled')
        profiles = snapshot['profiles']
        provider = ttk.Combobox(diagnostics, state='readonly', values=[p['name'] + ' · ' + p['state'] for p in profiles])
        provider.pack(fill='x', pady=6)
        if profiles:
            provider.current(0)
        def retry():
            if provider.current() >= 0:
                action('retry_provider', profiles[provider.current()]['id'])
        ttk.Button(diagnostics, text='Thử lại provider đã chọn', command=retry).pack(anchor='w')
        ttk.Label(diagnostics, text='Bỏ khóa tạm; lần gửi sau sẽ thử lại.', wraplength=340).pack(anchor='w')
        ttk.Label(privacy, wraplength=340, text=
                  'Phiên riêng tư chỉ giữ câu hỏi và ảnh trong RAM, không ghi lịch sử/ảnh xuống ổ đĩa. '
                  'Bật sẽ mở phiên trống. Tắt sẽ bỏ phiên riêng tư và trở về lịch sử đã lưu. '
                  'Chế độ này không xóa dữ liệu đã lưu trước đó và không ngăn nhà cung cấp AI nhận nội dung bạn gửi. '
                  'Dọn RAM bỏ tham chiếu nội dung trong app; không bảo đảm xóa vật lý mọi bản sao trong RAM, pagefile hay clipboard.').pack(anchor='w', pady=8)
        ttk.Label(privacy, text='Hiện tại: ' + ('RIÊNG TƯ · chỉ RAM' if snapshot['private'] else 'LƯU LỊCH SỬ')).pack(anchor='w', pady=8)
        ttk.Button(privacy, text='Tắt riêng tư và bỏ phiên RAM' if snapshot['private'] else 'Bật riêng tư · mở phiên RAM',
                   command=lambda: action('privacy', not snapshot['private'])).pack(anchor='w', pady=8)
        ttk.Button(privacy, text='Che vùng ảnh: ' + ('đang bật' if snapshot['mask'] else 'đang tắt') + ' · bấm để đổi',
                   command=lambda: action('mask', not snapshot['mask'])).pack(anchor='w', pady=8)
        ttk.Button(privacy, text='Dọn nội dung phiên riêng tư trong RAM', state='normal' if snapshot['private'] else 'disabled',
                   command=lambda: action('clear_private')).pack(anchor='w', pady=8)
        settings = snapshot.get('window_settings', {})
        width = tk.StringVar(master=root, value=str(settings.get('PANEL_WIDTH', '420')))
        height = tk.StringVar(master=root, value=str(settings.get('PANEL_HEIGHT', '560')))
        position = tk.StringVar(master=root, value='Giữa màn hình' if settings.get('PANEL_POSITION') == 'center' else 'Góc dưới bên phải')
        ttk.Label(appearance, text='Kích thước mặc định của cửa sổ', wraplength=340).pack(anchor='w', pady=8)
        for label, variable, low, high in (('Chiều ngang (px)', width, 360, 1600), ('Chiều cao (px)', height, 480, 1400)):
            ttk.Label(appearance, text=label).pack(anchor='w', pady=4)
            ttk.Spinbox(appearance, from_=low, to=high, textvariable=variable, width=12).pack(anchor='w')
        ttk.Label(appearance, text='Vị trí khi mở').pack(anchor='w', pady=8)
        ttk.Combobox(appearance, state='readonly', textvariable=position,
                     values=('Góc dưới bên phải', 'Giữa màn hình')).pack(fill='x')
        def save_window():
            from runtime_settings import validated_preferences
            values = dict(PANEL_WIDTH=width.get(), PANEL_HEIGHT=height.get(),
                          PANEL_POSITION='center' if position.get() == 'Giữa màn hình' else 'bottom_right')
            valid, invalid = validated_preferences(values)
            if invalid:
                status.set('Ngang 360–1600 px; cao 480–1400 px.')
                return
            action('window_settings', valid)
        def use_current():
            width.set(str(root.winfo_width()))
            height.set(str(root.winfo_height()))
        ttk.Button(appearance, text='Lấy kích thước đang kéo', command=use_current).pack(anchor='w', pady=8)
        ttk.Button(appearance, text='Lưu kích thước và vị trí', command=save_window).pack(anchor='w', pady=8)
        ttk.Label(appearance, text='Mặc định 420 × 560 px, góc dưới bên phải, tránh thanh tác vụ. Có thể kéo cạnh cửa sổ để đổi kích thước.', wraplength=340).pack(anchor='w', pady=8)
        from window_layout import place_tk
        place_tk(root, settings)
        root.protocol('WM_DELETE_WINDOW', root.quit)
        def poll():
            nonlocal timer
            if cancel.is_set():
                root.quit()
            else:
                timer = root.after(100, poll)
        timer = root.after(0, poll)
        root.mainloop()
    finally:
        if root is not None:
            try:
                if query is not None and trace is not None:
                    query.trace_remove('write', trace)
                if timer is not None:
                    root.after_cancel(timer)
                root.destroy()
            except tk.TclError:
                pass
            # Variables can outlive this function through callback closures.
            # The interpreter has been destroyed on its owner thread already.
            for variable in (query, status, width, height, position):
                if variable is not None:
                    variable._tk = None
        results.put(('tools_closed',))
