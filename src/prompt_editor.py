"""Manual prompt editor. Saves are applied by the tray application's queue."""
import queue
from chat_modes import MENU_MODES, MENU_LABELS, purpose_mode
from prompt_profiles import selected_prompt, instruction_for, default_prompt, merged_preferences


def open_editor(config, mode, results):
    import tkinter as tk
    from tkinter import ttk
    window = tk.Tk()
    window.title('Sửa prompt hiện tại')
    window.geometry('660x460')
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    config.update(merged_preferences(config, mode))
    selected_mode = tk.StringVar(value=MENU_LABELS[purpose_mode(mode)])
    chosen = tk.StringVar()
    selectors = []
    for label, variable, choices in (
        ('Áp dụng cho', selected_mode, [MENU_LABELS[m] for m in MENU_MODES]),
        ('Prompt', chosen, [])):
        ttk.Label(frame, text=label).pack(anchor='w')
        field = ttk.Combobox(frame, textvariable=variable, values=choices, state='readonly')
        field.pack(fill='x', pady=(2, 8))
        field.bind('<<ComboboxSelected>>', lambda event: load(event.widget))
        selectors.append(field)
    ttk.Label(frame, text='Sửa nội dung rồi Lưu để dùng prompt riêng. Áp dụng từ yêu cầu tiếp theo.').pack(anchor='w')
    text = tk.Text(frame, wrap='word', height=12, undo=True)
    text.pack(fill='both', expand=True, pady=8)
    status = tk.StringVar()
    ttk.Label(frame, textvariable=status, wraplength=620).pack(anchor='w')
    local = queue.Queue()

    def current_mode():
        return next(m for m in MENU_MODES if MENU_LABELS[m] == selected_mode.get())

    def current_style():
        return selections[chosen.get()][0]

    selections = {}

    def refresh_choices():
        selections.clear()
        m = current_mode()
        selections.update({'Mặc định': (default_prompt(m), None),
                           'Theo yêu cầu': ('free', None), 'Prompt riêng': ('custom', None)})
        for item in config.get('SAVED_PROMPTS', []):
            selections['Đã lưu · ' + item['name'] + ' [' + item['id'][:8] + ']'] = (item['style'], item['text'])
        selectors[1]['values'] = list(selections)

    def load(widget=None):
        m = current_mode()
        if widget is None or widget.cget('textvariable') == str(selected_mode):
            refresh_choices()
            active = selected_prompt(m, config)
            label = 'Mặc định' if active == default_prompt(m) else 'Theo yêu cầu' if active == 'free' else 'Prompt riêng'
            if active not in (default_prompt(m), 'free', 'custom'):
                actual = instruction_for(m, config)
                label = next((name for name, pair in selections.items() if pair == (active, actual)), 'Prompt hiện tại')
                if label == 'Prompt hiện tại':
                    selections[label] = (active, actual)
                    selectors[1]['values'] = list(selections)
            chosen.set(label)
        style = current_style()
        style, saved_text = selections[chosen.get()]
        sample = dict(config, PROMPT_MODES={str(m): style})
        text.delete('1.0', 'end')
        text.insert('1.0', instruction_for(m, sample) if saved_text is None else saved_text)
        text.edit_modified(False)
        status.set('Mẫu có sẵn; có thể sửa và lưu thành prompt riêng.')

    def save():
        style = 'custom' if text.edit_modified() else current_style()
        content = text.get('1.0', 'end-1c')
        results.put(('prompt_save', current_mode(), style, content, local))
        status.set('Đang lưu…')

    def poll():
        try:
            while True:
                event = local.get_nowait()
                status.set(event[1])
                if event[0] == 'saved':
                    config.update(event[2])
                    refresh_choices()
                    chosen.set('Mặc định' if event[3] == default_prompt(current_mode()) else 'Theo yêu cầu' if event[3] == 'free' else 'Prompt riêng')
                    text.edit_modified(False)
        except queue.Empty:
            pass
        window.after(100, poll)

    ttk.Button(frame, text='Lưu cho chế độ này', command=save).pack(anchor='e', pady=(8, 0))
    load()
    poll()
    window.update_idletasks()
    results.put(('prompt_opened', window.winfo_id()))
    try:
        window.mainloop()
    finally:
        results.put(('prompt_closed',))
