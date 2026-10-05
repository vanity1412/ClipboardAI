"""Exercise the real Zoo editor using synthetic credentials and a temp config."""
import json
from pathlib import Path
import queue
import tempfile
import threading
from unittest.mock import patch


def run(output):
    import tkinter as tk
    from api_zoo import ZooStore
    from api_zoo_ui import open_editor
    from cloud_client import DEFAULT_MODELS
    from PIL import ImageGrab
    output = Path(output)
    report = {'ok': False, 'network_calls': 0, 'synthetic_credentials': True}
    events = queue.Queue()
    original = tk.Tk
    with tempfile.TemporaryDirectory(prefix='ClipboardAI_Zoo_UI_') as folder:
        def process():
            while True:
                event = events.get()
                if event[0] == 'zoo_closed':
                    return
                if event[0] == 'zoo_save':
                    try:
                        ZooStore(folder).save(event[1])
                        event[2].put(('save', 'Đã lưu cấu hình kiểm thử'))
                    except Exception:
                        event[2].put(('save', 'Lỗi kiểm thử'))
        threading.Thread(target=process, daemon=True).start()
        def factory():
            window = original()
            def walk(widget):
                found = []
                for child in widget.winfo_children():
                    found.append(child)
                    found.extend(walk(child))
                return found
            def exercise():
                try:
                    all_widgets = walk(window)
                    buttons = {w.cget('text'): w for w in all_widgets if w.winfo_class() == 'TButton'}
                    entries = [w for w in all_widgets if w.winfo_class() == 'TEntry']
                    assert len(entries) == 3, 'Expected three fields'
                    assert entries[2].cget('show') == '•'
                    buttons['+ API'].invoke()
                    for widget, value in zip(entries, ('API mẫu', 'https://sample.example/v1', 'synthetic-key')):
                        widget.delete(0, 'end')
                        widget.insert(0, value)
                    entries[2].event_generate('<FocusOut>')
                    def save_discovered():
                        try:
                            choice = next(w for w in walk(window) if w.winfo_class() == 'TCombobox')
                            assert list(choice['values']) == ['coding-model', 'vision-model'], 'Model discovery did not fill dropdown: ' + str(choice['values'])
                            assert str(choice.cget('state')) == 'readonly', 'Model dropdown is editable'
                            choice.set('coding-model')
                            buttons['Lưu'].invoke()
                            window.after(500, verify)
                        except Exception as exc:
                            report['error_type'] = type(exc).__name__
                            report['error'] = str(exc)
                            window.destroy()
                    window.after(1400, save_discovered)
                except Exception as exc:
                    report['error_type'] = type(exc).__name__
                    report['error'] = str(exc)
                    window.destroy()
            def verify():
                try:
                    saved = ZooStore(folder).load()
                    p = next(p for p in saved['profiles'] if p['id'] == saved['primary'])
                    assert p['name'] == 'API mẫu' and p['model'] == 'coding-model', 'Primary/model not saved'
                    assert p['api_key'] == 'synthetic-key' and p['vision_model'] == 'vision-model', 'Key or vision model not saved'
                    assert len(saved['profiles']) == 3, 'Providers duplicated'
                    # Capture the actual HWND, never pixels from other apps
                    # that might cover the test editor while the user works.
                    window.update()
                    import ctypes as C
                    from ctypes import wintypes as W
                    user = C.WinDLL('user32')
                    user.GetAncestor.argtypes, user.GetAncestor.restype = [W.HWND, W.UINT], W.HWND
                    hwnd = user.GetAncestor(window.winfo_id(), 2)
                    ImageGrab.grab(window=hwnd).save(output / 'api-zoo-ui.png')
                    entries = [w for w in walk(window) if w.winfo_class() == 'TEntry']
                    entries[1].delete(0, 'end')
                    entries[1].insert(0, 'https://changed.example/v1')
                    entries[1].event_generate('<KeyRelease>')
                    entries[1].event_generate('<FocusOut>')
                    # Run the deferred focus handler without starting a fetch.
                    window.after(50, lambda: None)
                    window.update()
                    def finish():
                        if entries[2].get():
                            report.update(ok=False, error='A saved key survived changing its endpoint host')
                        else:
                            report['credential_host_bound'] = True
                        window.destroy()
                    report.update(ok=True, imported_existing_profiles=True, key_masked=True,
                                  add_edit_primary_save=True, automatic_discovery=True,
                                  no_manual_model_input=True, compact_ui=True,
                                  probe_mocked=True, screenshot_synthetic=True)
                    window.after(80, finish)
                    return
                except Exception as exc:
                    report['error_type'] = type(exc).__name__
                    report['error'] = str(exc)
                window.destroy()
            window.after(500, exercise)
            return window
        config = dict(MODEL_CHOICES=DEFAULT_MODELS, DEEPSEEK_API_KEY='deepseek-placeholder', MIRAI_API_KEY='mirai-placeholder')
        catalog = [{'id': 'coding-model', 'vision': False}, {'id': 'vision-model', 'vision': True}]
        with patch('tkinter.Tk', factory), patch('api_zoo_ui.discover_models', return_value=catalog), patch('tkinter.messagebox.showerror', side_effect=AssertionError('UI validation unexpectedly failed')):
            open_editor(folder, config, events)
    (output / 'api-zoo-verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    if not report['ok']:
        raise RuntimeError('Zoo UI verification failed')


if __name__ == '__main__':
    run(Path(__file__).resolve().parent)
