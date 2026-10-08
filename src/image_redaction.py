"""Review a screenshot and apply solid masks before storage or transmission."""
from io import BytesIO


def redact_png(png, boxes):
    from PIL import Image, ImageDraw
    with Image.open(BytesIO(png)) as source:
        image = source.convert('RGB')
    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2 in boxes:
        box = (max(0, min(x1, x2)), max(0, min(y1, y2)),
               min(image.width - 1, max(x1, x2)), min(image.height - 1, max(y1, y2)))
        if box[0] <= box[2] and box[1] <= box[3]:
            draw.rectangle(box, fill='black')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def review_png(png, cancel, settings=None):
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk
    root = tk.Tk()
    root.title('Che vùng ảnh trước khi gửi AI')
    timer, result, boxes = None, [], []
    try:
        with Image.open(BytesIO(png)) as source:
            size = source.size
            preview = source.convert('RGB')
        preview.thumbnail((1000, 650))
        sx, sy = size[0] / preview.width, size[1] / preview.height
        photo = ImageTk.PhotoImage(preview, master=root)
        ttk.Label(root, text='Kéo chuột để che vùng nhạy cảm. Chỉ ảnh đã che được lưu và gửi.').pack(padx=12, pady=8)
        canvas = tk.Canvas(root, width=preview.width, height=preview.height)
        canvas.pack()
        canvas.create_image(0, 0, anchor='nw', image=photo)
        start = []
        marker = []
        def press(event):
            start[:] = [event.x, event.y]
            marker[:] = [canvas.create_rectangle(event.x, event.y, event.x, event.y, fill='black')]
        def move(event):
            if marker:
                canvas.coords(marker[0], *start, event.x, event.y)
        def release(event):
            if start:
                boxes.append(tuple(round(v) for v in (start[0]*sx, start[1]*sy, event.x*sx, event.y*sy)))
                start.clear()
                marker.clear()
        canvas.bind('<ButtonPress-1>', press)
        canvas.bind('<B1-Motion>', move)
        canvas.bind('<ButtonRelease-1>', release)
        def send():
            if not cancel.is_set():
                result.append(redact_png(png, boxes))
            root.quit()
        def close():
            root.quit()
        bar = ttk.Frame(root)
        bar.pack(fill='x', padx=12, pady=10)
        ttk.Button(bar, text='Xác nhận ảnh và tiếp tục', command=send).pack(side='left')
        ttk.Button(bar, text='Hủy gửi', command=close).pack(side='right')
        root.protocol('WM_DELETE_WINDOW', close)
        from window_layout import place_tk
        place_tk(root, settings, keep_size=True)
        def poll():
            nonlocal timer
            if cancel.is_set():
                root.quit()
            else:
                timer = root.after(100, poll)
        timer = root.after(0, poll)
        root.mainloop()
        if not result or cancel.is_set():
            raise InterruptedError('Đã hủy ảnh')
        return result[0]
    finally:
        try:
            if timer is not None:
                root.after_cancel(timer)
            root.destroy()
        except tk.TclError:
            pass
