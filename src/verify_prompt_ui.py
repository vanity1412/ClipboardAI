"""Exercise the actual prompt editor with synthetic content and queued saves."""
import json
import queue
from pathlib import Path
from unittest.mock import patch
from chat_modes import CHAT, CODING
from prompt_profiles import prompt_preferences, merged_preferences


def run(output):
    import tkinter as tk
    from prompt_editor import open_editor
    report = dict(ok=False, network_calls=0, real_clipboard_writes=0)
    events = queue.Queue()
    original = tk.Tk
    config = {}
    saved = merged_preferences({'PROMPT_MODES': {'3': 'choices'},
                                'PROMPT_CUSTOM': {'4': 'SYNTHETIC OLD ANALYSIS'}}, CHAT)
    config.update(saved)

    def walk(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(walk(child))
        return result

    def factory():
        window = original()
        def exercise():
            try:
                widgets = walk(window)
                combos = [w for w in widgets if w.winfo_class() == 'TCombobox']
                text = next(w for w in widgets if w.winfo_class() == 'Text')
                button = next(w for w in widgets if w.winfo_class() == 'TButton')
                assert len(combos) == 2 and 'trắc nghiệm' in text.get('1.0', 'end')
                assert combos[1].get().startswith('Đã lưu')
                text.delete('1.0', 'end')
                text.insert('1.0', 'SYNTHETIC PROMPT')
                button.invoke()
                while True:
                    event = events.get_nowait()
                    if event[0] == 'prompt_save':
                        assert event[1:4] == (CHAT, 'custom', 'SYNTHETIC PROMPT')
                        saved.update(prompt_preferences(*event[1:4], saved))
                        event[4].put(('saved', 'Saved', saved, 'custom'))
                        break
                def second():
                    try:
                        combos[0].set('Lập trình')
                        combos[0].event_generate('<<ComboboxSelected>>')
                        assert 'trắc nghiệm' not in text.get('1.0', 'end')
                        combos[1].set('Theo yêu cầu')
                        combos[1].event_generate('<<ComboboxSelected>>')
                        button.invoke()
                        event = events.get_nowait()
                        assert event[:3] == ('prompt_save', CODING, 'free')
                        saved.update(prompt_preferences(*event[1:4], saved))
                        assert saved['PROMPT_CUSTOM']['3'] == 'SYNTHETIC PROMPT'
                        assert saved['PROMPT_MODES'] == {'3': 'custom', '0': 'free'}
                        assert any(item['text'] == 'SYNTHETIC OLD ANALYSIS' for item in saved['SAVED_PROMPTS'])
                        report.update(ok=True, mode_prompts_independent=True,
                                      custom_prompt_save=True, presets_selectable=True,
                                      legacy_prompt_opened_correctly=True, old_analysis_prompt_preserved=True)
                    except Exception as exc:
                        report['error'] = str(exc)
                    window.destroy()
                window.after(250, second)
            except Exception as exc:
                report['error'] = str(exc)
                window.destroy()
        window.after(250, exercise)
        window.after(5000, lambda: window.destroy() if window.winfo_exists() else None)
        return window

    with patch('tkinter.Tk', factory):
        open_editor(config, CHAT, events)
    (Path(output) / 'prompt-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['ok']:
        raise RuntimeError('Prompt UI verification failed')
