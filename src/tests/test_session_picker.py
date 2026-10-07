import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from session_state import Session
from windows_native import WindowsApp


class SessionArchiveTests(unittest.TestCase):
    def test_failed_transition_keeps_previous_session_in_memory_and_on_disk(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root) / "session.json")
            session.new_problem("A. SUM")
            first = session.active_id
            session.commit([{"role": "user", "content": "A. SUM"},
                            {"role": "assistant", "content": "sum code"}], "sum code")
            session.new_problem("B. TREE")
            second = session.active_id
            session.commit([{"role": "user", "content": "B. TREE"},
                            {"role": "assistant", "content": "tree code"}], "tree code")
            for operation in (lambda: session.new_problem("C. NEW"),
                              lambda: session.select(first)):
                session = Session(session.path)
                original_replace = Path.replace
                def fail_transition_write(path, target):
                    # Unchanged saves may skip I/O; fail the actual archive
                    # selection change rather than an implementation write count.
                    if json.loads(path.read_text(encoding='utf-8'))['active_id'] != second:
                        raise PermissionError("synthetic disk failure")
                    return original_replace(path, target)
                with patch.object(Path, "replace", new=fail_transition_write):
                    self.assertFalse(operation())
                self.assertTrue(session.error)
                self.assertEqual((session.active_id, session.problem, session.last_answer),
                                 (second, "B. TREE", "tree code"))
                restored = Session(session.path)
                self.assertEqual(restored.active_id, second)
                self.assertEqual(len(restored.entries()), 2)
                self.assertTrue(restored.select(first))
                self.assertEqual(restored.last_answer, "sum code")
                self.assertTrue(restored.select(second))

    def test_long_legacy_history_keeps_original_problem_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            messages = [{"role": "user", "content": "original statement"}]
            messages.extend({"role": "assistant", "content": "code " + str(i)} for i in range(30))
            path.write_text(json.dumps(dict(problem="original statement", messages=messages,
                                            last_answer="code 29")), encoding="utf-8")
            session = Session(path)
            session.save()
            restored = Session(path)
            self.assertEqual(len(restored.messages), 31)
            self.assertEqual(restored.context()[0]["content"], "original statement")
            self.assertEqual(restored.context()[-1]["content"], "code 29")

    def test_switch_and_restart_restore_separate_problems_history_and_code(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            session = Session(path)
            session.new_problem("A. SUM\nInput a b")
            first = session.active_id
            session.commit([{"role": "user", "content": session.problem},
                            {"role": "assistant", "content": "sum code"}], "sum code")
            session.new_problem("B. TREE")
            second = session.active_id
            session.commit([{"role": "user", "content": session.problem},
                            {"role": "assistant", "content": "tree code"}], "tree code")
            self.assertEqual(len(session.entries()), 2)
            self.assertTrue(session.select(first))
            session.messages.append({"role": "user", "content": "WA test"})
            session.commit([{"role": "assistant", "content": "fixed sum"}], "fixed sum")
            restored = Session(path)
            self.assertEqual((restored.active_id, restored.problem, restored.last_answer),
                             (first, "A. SUM\nInput a b", "fixed sum"))
            self.assertIn("WA test", str(restored.context()))
            restored.select(second)
            self.assertEqual(restored.last_answer, "tree code")
            self.assertNotIn("WA test", str(restored.context()))

    def test_migrate_old_file_and_select_does_not_change_update_time(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.write_text(json.dumps(dict(problem="Old problem", last_answer="old code",
                                            messages=[{"role": "user", "content": "Old problem"}],
                                            mode=1, auto_copy=False)), encoding="utf-8")
            session = Session(path)
            first, updated = session.active_id, session.updated_at
            session.new_problem("New problem")
            session.select(first)
            self.assertEqual(session.updated_at, updated)
            self.assertEqual((session.mode, session.auto_copy, session.last_answer), (0, False, "old code"))
            self.assertEqual(len(Session(path).entries()), 2)

    def test_draft_and_failed_request_are_recoverable(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root) / "session.json")
            session.capture_text = "Draft image problem"
            session.capture_source, session.capture_pages = "window hash", 2
            session.capture_missing = ["constraints"]
            session.save()
            draft = session.active_id
            session.new_problem("Failed new problem")
            failed = session.active_id
            session.select(draft)
            self.assertEqual((session.capture_text, session.capture_pages, session.capture_missing),
                             ("Draft image problem", 2, ["constraints"]))
            session.select(failed)
            self.assertEqual(session.last_request, "Failed new problem")
            self.assertEqual(session.messages, [])

    def test_delete_only_selected_problem(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root) / "session.json")
            session.new_problem("Keep")
            keep = session.active_id
            session.new_problem("Delete")
            session.reset()
            restored = Session(session.path)
            self.assertEqual([entry["title"] for entry in restored.entries()], ["Keep"])
            self.assertTrue(restored.select(keep))

    def test_invalid_archive_never_partly_loads_or_overwrites(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            for records, active in (([{"id": "a", "problem": "A"}, {"id": "b", "mode": 100}], "a"),
                                    ([{"id": "a"}, {"id": "a"}], "a"),
                                    ([{"id": "a"}], "missing")):
                payload = json.dumps(dict(sessions=records, active_id=active))
                path.write_text(payload, encoding="utf-8")
                session = Session(path)
                self.assertTrue(session.error)
                self.assertEqual(session.problem, "")
                self.assertFalse(session.new_problem("must not replace"))
                self.assertEqual(path.read_text(encoding="utf-8"), payload)


class PickerFlowTests(unittest.TestCase):
    def app(self, root):
        app = WindowsApp.__new__(WindowsApp)
        app.session = Session(Path(root) / "session.json")
        app.session.new_problem("A. SUM & MORE\nBody")
        app.session.commit([{"role": "user", "content": app.session.problem},
                            {"role": "assistant", "content": "sum code"}], "sum code")
        app.enabled, app.busy, app.current_id = True, False, 1
        app.state = "Đang chờ"
        app.output_fingerprints = set()
        app.hwnd, app.user = 123, Mock()
        app.controls = {"mode": 124, "auto": 125}
        app.read_clipboard = Mock(return_value=("WA on test 4", 99))
        app.tooltip, app.refresh_panel, app.set_text = Mock(), Mock(), Mock()
        app.start_request = Mock()
        return app

    def test_f6_popup_restores_without_ai_or_clipboard_and_clears_pending_write(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            first = app.session.active_id
            app.session.new_problem("B. TREE")
            entries = app.session.entries()
            index = next(index for index, entry in enumerate(entries) if entry["id"] == first)
            app.pending_write = ("old pending", 20, None)
            app.user.TrackPopupMenu.return_value = 1001 + index
            def work_area(action, param, rect, flags):
                rect._obj.right, rect._obj.bottom = 1920, 1040
                return True
            app.user.SystemParametersInfoW.side_effect = work_area
            app.window_proc(app.hwnd, 0x0312, 207, 0)
            self.assertEqual(app.session.problem, "A. SUM & MORE\nBody")
            self.assertEqual(app.session.last_answer, "sum code")
            self.assertIsNone(app.pending_write)
            app.set_text.assert_any_call("answer", "sum code")
            app.start_request.assert_not_called()
            app.read_clipboard.assert_not_called()
            self.assertEqual(app.user.TrackPopupMenu.call_args.args[1:4], (0x128, 1904, 1024))
            self.assertTrue(any("SUM && MORE\t" in call.args[3] for call in app.user.AppendMenuW.call_args_list))
            app.user.DestroyMenu.assert_called_once()

    def test_escape_leaves_active_session_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            original = app.session.active_id
            app.user.TrackPopupMenu.return_value = 0
            app.session_menu()
            self.assertEqual(app.session.active_id, original)
            app.start_request.assert_not_called()
            app.set_text.assert_not_called()
            app.user.DestroyMenu.assert_called_once()

    def test_f9_sends_fresh_clipboard_feedback_to_selected_problem(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.session.mode = 0  # This check exercises a legacy coding session.
            app.window_proc(app.hwnd, 0x0312, 202, 0)
            self.assertIn("WA on test 4", app.start_request.call_args.args[0])
            self.assertEqual(app.start_request.call_args.kwargs["action"], "chat")
            self.assertEqual(app.session.problem, "A. SUM & MORE\nBody")
            app.start_request.reset_mock()
            for text in ("", "sum code"):
                app.read_clipboard.return_value = (text, 100)
                app.reply_clipboard()
            app.start_request.assert_not_called()

    def test_busy_switch_does_not_redirect_running_job(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            first = app.session.active_id
            app.session.new_problem("B. TREE")
            active = app.session.active_id
            app.busy = True
            app.select_session(first)
            self.assertEqual(app.session.active_id, active)
            self.assertIn("F10", app.state)

    def test_retry_stays_in_selected_session(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.session.new_problem("Failed request")
            app.resend_session()
            self.assertEqual(app.start_request.call_args.args[0], "Failed request")
            self.assertFalse(app.start_request.call_args.kwargs["new_session"])


if __name__ == "__main__":
    unittest.main()
