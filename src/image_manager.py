"""Explicit local image viewing/deletion. No clipboard, capture or API access."""
import hashlib
from io import BytesIO


def open_manager(cache, session_id, results, cancel):
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = None
    timer = None
    try:
        root = tk.Tk()
        root.title('Quản lý ảnh của phiên')
        root.geometry('800x570')
        root.minsize(680, 460)
        pane = ttk.Frame(root, padding=12)
        pane.pack(fill='both', expand=True)
        status = tk.StringVar(master=root)
        ttk.Label(pane, textvariable=status, wraplength=760).pack(fill='x')
        body = ttk.Frame(pane)
        body.pack(fill='both', expand=True, pady=10)
        listing = tk.Listbox(body, width=24, exportselection=False)
        listing.pack(side='left', fill='y')
        preview = ttk.Label(body, anchor='center', text='Chọn một ảnh để xem')
        preview.pack(side='right', fill='both', expand=True, padx=10)
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
                    image.thumbnail((520, 410))
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
        poll()
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
