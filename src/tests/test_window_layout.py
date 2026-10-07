from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import queue
import threading
from runtime_settings import validated_preferences, save_preferences, load_runtime_settings
from window_layout import panel_bounds


class PanelPlacementTests(unittest.TestCase):
    def test_default_bottom_right_leaves_taskbar_work_area_and_margin(self):
        self.assertEqual(panel_bounds({}, (0, 0, 1920, 1040)), (1488, 468, 420, 560))

    def test_negative_monitor_coordinates_and_center(self):
        self.assertEqual(panel_bounds({}, (-1920, -100, 0, 940)), (-432, 368, 420, 560))
        self.assertEqual(panel_bounds({'PANEL_POSITION': 'center'}, (-1920, 0, 0, 1080)), (-1170, 260, 420, 560))

    def test_small_work_area_clamps_window_inside_screen(self):
        x, y, width, height = panel_bounds({'PANEL_WIDTH': '1600', 'PANEL_HEIGHT': '1400'}, (0, 0, 800, 600))
        self.assertEqual((x, y, width, height), (0, 0, 800, 600))

    def test_settings_survive_restart_and_invalid_values_do_not_override(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            save_preferences(path, {'PANEL_WIDTH': '520', 'PANEL_HEIGHT': '680', 'PANEL_POSITION': 'center'})
            config = {}
            self.assertEqual(load_runtime_settings(config, path), [])
            self.assertEqual(config['PANEL_WIDTH'], '520')
            self.assertEqual(config['PANEL_HEIGHT'], '680')
            self.assertEqual(config['PANEL_POSITION'], 'center')
        valid, invalid = validated_preferences({'PANEL_WIDTH': 'NaN', 'PANEL_HEIGHT': True, 'PANEL_POSITION': 'offscreen'})
        self.assertEqual(valid, {})
        self.assertEqual(len(invalid), 3)


class HiddenAppearanceSettingsTests(unittest.TestCase):
    def test_compact_panel_saves_custom_dimensions(self):
        import tkinter as tk
        from tools_panel import open_tools
        from activity_stats import ActivityStats
        from session_state import Session
        from session_images import SessionImages
        try:
            probe = tk.Tk(); probe.withdraw(); probe.destroy()
        except tk.TclError:
            self.skipTest('Tk unavailable')
        original, events, errors = tk.Tk, queue.Queue(), []
        def widgets(root):
            return [item for child in root.winfo_children() for item in [child, *widgets(child)]]
        def factory():
            root = original(); root.withdraw()
            def exercise():
                try:
                    self.assertEqual(root.winfo_width(), 420)
                    self.assertEqual(root.winfo_height(), 560)
                    fields = [w for w in widgets(root) if w.winfo_class() == 'TSpinbox']
                    self.assertEqual(len(fields), 2)
                    for field, value in zip(fields, ('540', '640')):
                        field.delete(0, 'end'); field.insert(0, value)
                    save = next(w for w in widgets(root) if w.winfo_class() == 'TButton' and w.cget('text') == 'Lưu kích thước và vị trí')
                    save.invoke()
                except BaseException as exc:
                    errors.append(exc)
                    root.quit()
            root.after(80, exercise)
            root.after(3000, root.quit)
            return root
        ram = Session(None)
        snapshot = dict(archive=ram.archive_snapshot(), stats=ActivityStats().snapshot(),
                        private=False, mask=False, profiles=[], model='synthetic', proxies={})
        with patch('tkinter.Tk', factory), patch('tkinter._default_root', object()):
            open_tools(snapshot, SessionImages(), events, threading.Event())
        if errors:
            raise errors[0]
        self.assertEqual(events.get_nowait(), ('tools_action', 'window_settings',
                         {'PANEL_WIDTH': '540', 'PANEL_HEIGHT': '640', 'PANEL_POSITION': 'bottom_right'}))
        self.assertEqual(events.get_nowait(), ('tools_closed',))
