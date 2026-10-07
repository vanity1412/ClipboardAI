from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from hotkey_settings import bindings, normalized_shortcuts
from tests import test_selected_fixes as fixtures


class CopySessionMenuTests(unittest.TestCase):
    def test_f7_defaults_and_upgrade_preserve_custom_shortcuts(self):
        self.assertEqual(bindings()[215], ('F7', 0, 0x76))
        self.assertEqual(bindings()[217], ('Shift+F7', 4, 0x76))
        self.assertEqual(bindings()[201][0], 'F8')
        self.assertEqual(normalized_shortcuts({'215': 'Shift+F8'})['215'], 'F7')
        self.assertEqual(normalized_shortcuts({'215': 'Ctrl+Alt+C'})['215'], 'Ctrl+Alt+C')

    def test_selected_copy_shortcut_does_not_copy_latest_other_session(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.last_completed_answer = 'Latest from another session'
            app.copy_last_answer = Mock()
            app.command(217)
            app.copy_last_answer.assert_called_once_with(current_session=True)

    def test_choose_session_selects_and_copies_without_api_request(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            original = app.session.active_id
            app.session.new_problem('Another question')
            app.select_session = Mock(side_effect=lambda ident: app.session.select(ident))
            app.copy_last_answer = Mock()
            app.copy_session_answer(original)
            app.select_session.assert_called_once_with(original)
            app.copy_last_answer.assert_called_once_with(current_session=True)
            self.assertTrue(app.jobs.empty())

    def test_failed_selection_does_not_copy_wrong_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.select_session = Mock()
            app.copy_last_answer = Mock()
            app.copy_session_answer('missing')
            app.copy_last_answer.assert_not_called()

    def test_menu_places_tools_under_settings_and_copy_sessions_under_arrow(self):
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.hide_tray_result = Mock()
            app.user.CreatePopupMenu.side_effect = range(10, 100)
            app.user.TrackPopupMenu.return_value = 0
            app.menu()
            calls = [c.args for c in app.user.AppendMenuW.call_args_list]
            settings = next(c[2] for c in calls if c[3] == 'Model / API / cài đặt')
            management = next(c for c in calls if c[2] == 224)
            self.assertEqual(management[0], settings)
            copy_menu = next(c for c in calls if c[3].startswith('Copy đáp án theo phiên'))
            self.assertEqual(copy_menu[1], 0x10)
            self.assertTrue(any(c[0] == copy_menu[2] and c[2] >= 60000 for c in calls))
