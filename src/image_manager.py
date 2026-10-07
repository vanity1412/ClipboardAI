"""Explicit local image viewing/deletion. No clipboard, capture or API access."""
import hashlib
from io import BytesIO


def open_manager(cache, session_id, results, cancel, conversation=None, settings=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = None
    timer = None
    try:
        root = tk.Tk()
        root.title('Ảnh & nội dung chat của phiên')
        tabs = ttk.Notebook(root)
        tabs.pack(fill='both', expand=True)
        pane = ttk.Frame(tabs, padding=12)
        tabs.add(pane, text='Ảnh đính kèm')
        chat = ttk.Frame(tabs, padding=12)
        tabs.add(chat, text='Nội dung chat của phiên')
        conversation = conversation or {}
        ttk.Label(chat, text=conversation.get('title') or 'Hội thoại cùng phiên với các ảnh này', wraplength=340).pack(anchor='w', pady=6)
        from tkinter.scrolledtext import ScrolledText
        history = ScrolledText(chat, wrap='word', font=('Segoe UI', 10))
        history.pack(fill='both', expand=True)
        transcript = '\n\n'.join(('Bạn' if m['role'] == 'user' else 'AI') + ':\n' + m['content']
                                 for m in conversation.get('messages', []))
        if not transcript:
            transcript = conversation.get('problem', '') + '\n\n' + conversation.get('last_answer', '')
        if len(transcript) > 180000:
            transcript = '[Hiển thị phần cuối hội thoại; xuất hội thoại trong Cài đặt để xem toàn bộ.]\n\n' + transcript[-180000:]
        history.insert('1.0', transcript.strip() or 'Phiên này chưa có nội dung chat.')
        history.configure(state='disabled')
        def open_chat():
            results.put(('images_open_chat', session_id))
            close()
        ttk.Button(chat, text='Mở chat để hỏi tiếp', command=open_chat).pack(anchor='e', pady=8)
        status = tk.StringVar(master=root)
        ttk.Label(pane, textvariable=status, wraplength=340).pack(fill='x')
        body = ttk.Frame(pane)
        body.pack(fill='both', expand=True, pady=10)
        listing = tk.Listbox(body, height=4, exportselection=False)
        listing.pack(side='top', fill='x')
        preview = ttk.Label(body, anchor='center', text='Chọn một ảnh để xem')
        preview.pack(side='top', fill='both', expand=True, pady=8)
        photos = []
        frames = []

        def cancel_timer():
            nonlocal timer
            if timer is not None:
                try:
                    root.after_cancel(timer)
                except tk.TclError:
                    root.tk.call('after', 'cancel', timer)
                timer = None

        def close():
            cancel_timer()
            root.quit()
            root.destroy()

        root.protocol('WM_DELETE_WINDOW', close)
        root.bind('<Destroy>', lambda event: cancel_timer() if event.widget is root else None, add='+')

        def show(_event=None):
            selected = listing.curselection()
            photos.clear()
            preview.configure(image='', text='Chọn một ảnh để xem')
            if not selected:
                return
            try:
                from PIL import Image, ImageTk
                with Image.open(BytesIO(frames[selected[0]])) as image:
                    width = max(72, preview.winfo_width() if preview.winfo_width() > 1 else root.winfo_width()-40)
                    height = max(72, preview.winfo_height() if preview.winfo_height() > 1 else 300)
                    image.thumbnail((width, height))
                    photo = ImageTk.PhotoImage(image, master=root)
                photos.append(photo)
                preview.configure(image=photo, text='')
            except Exception:
                preview.configure(image='', text='Ảnh không đọc được; có thể xóa ảnh này.')

        def refresh(selected=0):
            frames[:] = cache.get(session_id)
            listing.delete(0, 'end')
            for index, png in enumerate(frames):
                listing.insert('end', f'Trang {index + 1}/{len(frames)} · {len(png) / 1048576:.2f} MiB')
            if frames:
                listing.selection_set(min(selected, len(frames) - 1))
            status.set(cache.warning(session_id) or
                       f'{len(frames)} trang ảnh · {sum(map(len, frames)) / 1048576:.2f} MiB · ' +
                       ('Đã lưu theo phiên' if cache.persistent else 'Chỉ trong RAM'))
            show()

        def remove(all_images=False):
            selected = listing.curselection()
            if not frames or not all_images and not selected:
                return
            if not messagebox.askyesno('Xóa ảnh', 'Xóa toàn bộ ảnh của phiên này?' if all_images else
                                      'Xóa ảnh đang chọn? Lịch sử chữ được giữ nguyên.', parent=root):
                return
            try:
                if all_images:
                    cache.clear(session_id)
                else:
                    cache.remove(session_id, hashlib.sha256(frames[selected[0]]).hexdigest())
                results.put(('images_changed', session_id))
                refresh(selected[0] if selected else 0)
            except ValueError as exc:
                status.set(str(exc))

        listing.bind('<<ListboxSelect>>', show)
        preview.bind('<Configure>', show)
        buttons = ttk.Frame(pane)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Xóa ảnh đang chọn', command=remove).pack(side='left')
        ttk.Button(buttons, text='Xóa tất cả ảnh', command=lambda: remove(True)).pack(side='left', padx=8)
        ttk.Button(buttons, text='Đóng', command=close).pack(side='right')
        def poll():
            nonlocal timer
            if cancel.is_set():
                close()
                return
            timer = root.after(100, poll)
        refresh()
        from window_layout import place_tk
        place_tk(root, settings)
        timer = root.after(0, poll)
        root.mainloop()
    finally:
        if root is not None:
            try:
                if timer is not None:
                    root.after_cancel(timer)
                root.destroy()
            except tk.TclError:
                pass
        results.put(('images_closed',))
