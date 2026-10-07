"""Hidden real Tk editors with delayed acknowledgements and synthetic discovery."""
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import api_zoo_ui
import prompt_editor
from api_zoo import ZooStore, validate
from chat_modes import CHAT, CODING, MENU_LABELS
from prompt_profiles import prompt_preferences


def widgets(root):
    result = []
    for child in root.winfo_children():
        result.append(child)
        result.extend(widgets(child))
    return result


class EditorCleanupTests(unittest.TestCase):
    def test_missing_tk_runtime_still_reports_editor_closed(self):
        import tkinter as tk
        for module, args, event in ((prompt_editor, ({}, CHAT), 'prompt_closed'),
                                    (api_zoo_ui, ('synthetic-root', {}), 'zoo_closed')):
            results = queue.Queue()
            with self.subTest(module=module.__name__), patch('tkinter.Tk', side_effect=tk.TclError('missing runtime')):
                with self.assertRaises(tk.TclError):
                    module.open_editor(*args, results)
                self.assertEqual(results.get_nowait(), (event,))

    def test_setup_failure_always_destroys_owner_and_reports_closed(self):
        for module, args, event in ((prompt_editor, ({}, CHAT), 'prompt_closed'),
                                    (api_zoo_ui, ('synthetic-root', {}), 'zoo_closed')):
            window, results = Mock(), queue.Queue()
            with self.subTest(module=module.__name__), patch('tkinter.Tk', return_value=window), \
                    patch.object(module, '_open_editor', side_effect=ValueError('synthetic setup failure')):
                with self.assertRaises(ValueError):
                    module.open_editor(*args, results)
                window.destroy.assert_called_once()
                self.assertEqual(results.get_nowait(), (event,))


class HiddenEditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.tk = tk
        try:
            root = tk.Tk(); root.withdraw(); root.destroy()
        except tk.TclError as exc:
            self.skipTest('Tk runtime unavailable: ' + str(exc))

    def run_editor(self, module, args, exercise):
        original, errors, completed = self.tk.Tk, [], []
        results = queue.Queue()
        def factory():
            window = original(); window.withdraw()
            watchdog = [None]
            def safe(action):
                def run():
                    try:
                        action()
                    except BaseException as exc:
                        errors.append(exc)
                        window.destroy()
                return run
            def done():
                completed.append(True)
                window.after_cancel(watchdog[0])
                window.destroy()
            def begin():
                exercise(window, results, lambda delay, action: window.after(delay, safe(action)), done)
            window.after(40, safe(begin))
            watchdog[0] = window.after(3000, window.destroy)
            return window
        # Simulate another editor's default interpreter. Every variable must
        # belong explicitly to this window, regardless of global Tk state.
        with patch.object(self.tk, '_default_root', object()), patch('tkinter.Tk', factory):
            module.open_editor(*args, results)
        if errors:
            raise errors[0]
        self.assertTrue(completed, 'Editor did not finish its scenario')
        return results

    def test_delayed_prompt_save_preserves_newer_text_and_switched_mode(self):
        config = {'REPLY_MENU_VERSION': 1}
        def exercise(window, results, later, done):
            all_widgets = widgets(window)
            combos = [item for item in all_widgets if item.winfo_class() == 'TCombobox']
            text = next(item for item in all_widgets if item.winfo_class() == 'Text')
            button = next(item for item in all_widgets if item.winfo_class() == 'TButton')
            text.delete('1.0', 'end'); text.insert('1.0', 'submitted version')
            button.invoke()
            results.get_nowait()  # prompt_opened
            first = results.get_nowait()
            self.assertEqual(first[1:4], (CHAT, 'custom', 'submitted version'))
            text.insert('end', ' with newer edits')
            saved = prompt_preferences(*first[1:4], config)
            first[4].put(('saved', 'Saved', saved, 'custom'))
            def after_first():
                self.assertTrue(text.edit_modified())
                self.assertEqual(combos[1].get(), 'Mặc định')
                button.invoke()
                second = results.get_nowait()
                self.assertEqual(second[1:4], (CHAT, 'custom', 'submitted version with newer edits'))
                combos[0].set(MENU_LABELS[CODING])
                combos[0].event_generate('<<ComboboxSelected>>')
                text.insert('end', '\nnew coding edits')
                second[4].put(('saved', 'Saved', prompt_preferences(*second[1:4], config), 'custom'))
                def after_second():
                    self.assertEqual(combos[0].get(), MENU_LABELS[CODING])
                    self.assertEqual(combos[1].get(), 'Mặc định')
                    self.assertTrue(text.edit_modified())
                    button.invoke()
                    third = results.get_nowait()
                    self.assertEqual(third[1:3], (CODING, 'custom'))
                    self.assertTrue(third[3].endswith('new coding edits'))
                    done()
                later(160, after_second)
            later(160, after_first)
        self.run_editor(prompt_editor, (config, CHAT), exercise)

    def test_repeated_discovery_is_serial_and_only_latest_pending_operation_runs(self):
        release, entered = threading.Event(), threading.Event()
        calls, active, peaks = [], [0], [0]
        lock = threading.Lock()
        catalog = [{'id': 'model', 'vision': None}]
        def discovery(profile, auth_root, cancel=None):
            with lock:
                calls.append(cancel); active[0] += 1; peaks[0] = max(peaks[0], active[0])
                first = len(calls) == 1
            try:
                if first:
                    entered.set(); release.wait(2)
                    if cancel.is_set():
                        raise InterruptedError()
                return catalog
            finally:
                with lock:
                    active[0] -= 1
        with tempfile.TemporaryDirectory() as folder:
            row = dict(id='synthetic', name='Synthetic', base_url='https://example.test/v1',
                       api_key='synthetic-key', model='model', vision_model='', models=catalog)
            ZooStore(folder).save(validate({'profiles': [row], 'primary': 'synthetic'}))
            def exercise(window, results, later, done):
                button = next(item for item in widgets(window)
                              if item.winfo_class() == 'TButton' and item.cget('text') == 'Lấy lại model')
                button.invoke()
                self.assertTrue(entered.wait(1))
                for _ in range(5):
                    button.invoke()
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0].is_set())
                release.set()

                def verify():
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(peaks[0], 1)
                    self.assertIsNotNone(calls[1])
                    self.assertFalse(calls[1].is_set())
                    all_widgets = widgets(window)
                    next(item for item in all_widgets if item.winfo_name() == 'model').set('')
                    next(item for item in all_widgets if item.winfo_class() == 'TEntry').event_generate('<FocusOut>')
                    done()
                later(420, verify)
            try:
                with patch('api_zoo_ui.discover_models', side_effect=discovery):
                    self.run_editor(api_zoo_ui, (folder, {}), exercise)
            finally:
                release.set()

    def test_saving_legacy_preset_preserves_its_style_on_a_second_save(self):
        config = {'REPLY_MENU_VERSION': 1, 'PROMPT_MODES': {'3': 'choices'},
                  'SAVED_PROMPTS': [{'id': 'legacy', 'name': 'Legacy', 'style': 'choices',
                                    'text': 'synthetic choice prompt'}]}
        def exercise(window, results, later, done):
            button = next(item for item in widgets(window) if item.winfo_class() == 'TButton')
            button.invoke(); results.get_nowait()
            event = results.get_nowait()
            self.assertEqual(event[2], 'choices')
            event[4].put(('saved', 'Saved', prompt_preferences(*event[1:4], config), 'choices'))
            def verify():
                button.invoke()
                self.assertEqual(results.get_nowait()[2], 'choices')
                done()
            later(160, verify)
        self.run_editor(prompt_editor, (config, CHAT), exercise)


if __name__ == '__main__':
    unittest.main()
