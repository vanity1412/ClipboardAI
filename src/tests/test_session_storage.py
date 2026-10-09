import json
from pathlib import Path
import queue
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from session_state import Session, format_session_time, session_time_key
from windows_native import WindowsApp


class StorageRecoveryTests(unittest.TestCase):
    def test_failed_commit_keeps_answer_and_recovers_without_reset(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            session = Session(path)
            self.assertTrue(session.new_problem("Original problem"))
            original_id = session.active_id
            turns = [{"role": "user", "content": "Original problem"},
                     {"role": "assistant", "content": "new answer"}]
            with patch.object(Path, "replace", side_effect=PermissionError("synthetic disk error")):
                self.assertFalse(session.commit(turns, "new answer"))
            self.assertTrue(session.error)
            self.assertEqual(session.last_answer, "new answer")
            self.assertEqual(Session(path).last_answer, "")
            self.assertTrue(session.save())
            self.assertFalse(session.error)
            restored = Session(path)
            self.assertEqual((restored.active_id, restored.last_answer, restored.messages),
                             (original_id, "new answer", turns))
            self.assertFalse(path.with_suffix(".unreadable.json").exists())

    def test_new_problem_retries_unsaved_answer_before_archiving(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root) / "session.json")
            session.new_problem("First problem")
            first = session.active_id
            with patch.object(Path, "replace", side_effect=OSError("synthetic full disk")):
                self.assertFalse(session.commit([], "first answer"))
                self.assertFalse(session.new_problem("must not replace first"))
            self.assertEqual((session.active_id, session.problem, session.last_answer),
                             (first, "First problem", "first answer"))
            self.assertTrue(session.new_problem("Second problem"))
            self.assertTrue(session.select(first))
            self.assertEqual(session.last_answer, "first answer")
            self.assertEqual(len(Session(session.path).entries()), 2)

    def test_corrupt_input_stays_protected_even_when_storage_is_writable(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            payload = '{"sessions":[],"active_id":"missing"}'
            path.write_text(payload, encoding="utf-8")
            session = Session(path)
            self.assertFalse(session.save())
            self.assertFalse(session.new_problem("do not overwrite"))
            self.assertEqual(path.read_text(encoding="utf-8"), payload)
            self.assertTrue(session.reset())
            self.assertEqual(path.with_suffix(".unreadable.json").read_text(encoding="utf-8"), payload)
            self.assertFalse(session.error)


class DateMetadataTests(unittest.TestCase):
    def test_early_dates_and_naive_dates_do_not_use_windows_mktime(self):
        self.assertEqual(format_session_time("0001-01-01T00:00:00"), "01/01/0001 07:00:00")
        self.assertEqual(format_session_time("1960-01-01T00:00:00"), "01/01/1960 07:00:00")
        self.assertEqual(format_session_time("2026-10-04T03:22:00+07:00"), "04/10/2026 03:22:00")
        self.assertEqual(format_session_time("2026-10-03T20:22:00Z"), "04/10/2026 03:22:00")
        self.assertLess(session_time_key("1960-01-01"), session_time_key("2026-10-04"))

    def test_bad_metadata_does_not_invalidate_otherwise_healthy_archive(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            for stamp in ("not a date", "9999-12-31T23:59:59+00:00",
                          "0001-01-01T00:00:00+07:00", None, 123, {}, "x" * 500):
                with self.subTest(stamp=stamp):
                    data = dict(active_id="good", sessions=[
                        dict(id="good", problem="Healthy problem", last_answer="healthy code",
                             updated_at="2026-10-04T00:00:00+00:00"),
                        dict(id="bad-date", problem="Another problem", last_answer="another code",
                             updated_at=stamp)])
                    path.write_text(json.dumps(data), encoding="utf-8")
                    original = path.read_bytes()
                    session = Session(path)
                    self.assertFalse(session.error)
                    self.assertEqual(len(session.entries()), 2)
                    self.assertEqual(session.entries()[0]["id"], "good")
                    self.assertEqual(path.read_bytes(), original, "Loading must not rewrite user data")
                    self.assertTrue(session.select("bad-date"))
                    self.assertEqual(session.last_answer, "another code")
                    self.assertEqual(format_session_time(session.updated_at), "Không rõ thời gian")
                    self.assertFalse(Session(path).error)


class StorageStatusTests(unittest.TestCase):
    def app(self, root):
        app = WindowsApp.__new__(WindowsApp)
        app.session = Session(Path(root) / "session.json")
        app.session.new_problem("Synthetic problem")
        app.pending_read = app.self_test = False
        app.results = queue.Queue()
        app.current_id, app.busy = 1, True
        app.started = time.monotonic()
        app.pending_write = None
        app.enabled = False
        app.output_fingerprints = set()
        app.config = {}
        app.nid = SimpleNamespace(szTip="")
        app.last_tooltip = ""
        app.shell = Mock()
        app.set_text = app.refresh_panel = app.update_notice = app.show_completion = Mock()
        return app

    def test_done_warns_when_answer_is_only_in_memory(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            job = dict(id=1, action="problem", text="Synthetic problem", sequence=1, digest=None)
            turns = [{"role": "user", "content": "Synthetic problem"},
                     {"role": "assistant", "content": "new answer"}]
            app.results.put(("done", job, "new answer", turns))
            with patch.object(Path, "replace", side_effect=PermissionError("synthetic failure")), \
                 patch("windows_native.log_event") as log:
                # Tooltip uses ctypes.byref, which is unnecessary in this mock.
                with patch("windows_native.C.byref", side_effect=lambda value: value):
                    app.tick()
            self.assertIn("chưa lưu", app.state)
            self.assertNotIn("kết quả đã lưu", app.state)
            self.assertTrue(app.nid.szTip.startswith("ClipboardAI: Chưa lưu"))
            self.assertEqual(app.session.last_answer, "new answer")
            self.assertEqual(Session(app.session.path).last_answer, "")
            log.assert_any_call("request_done", request_id=1, elapsed=0, session_saved=False)
            post = Mock()
            app.client = SimpleNamespace(post=post)
            with patch("windows_native.C.byref", side_effect=lambda value: value):
                app.command(208)
            post.assert_not_called()
            self.assertFalse(app.session.error)
            self.assertIn("Đã lưu lại", app.state)
            self.assertEqual(Session(app.session.path).last_answer, "new answer")

    def test_copy_message_cannot_hide_unsaved_warning(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = False
            app.session.error = "Chưa lưu được lịch sử; kết quả chỉ ở bộ nhớ."
            with patch("windows_native.C.byref", side_effect=lambda value: value):
                app.tooltip("Đã copy kết quả")
            self.assertIn("Chưa lưu được lịch sử", app.nid.szTip)

    def test_f6_can_display_out_of_range_date_without_opening_ai(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.busy = False
            app.hwnd, app.user = 123, Mock()
            app.user.GetForegroundWindow.return_value = 456
            app.user.GetWindowLongPtrW.return_value = 0
            app.user.GetWindowLongW.return_value = 0
            app.session.updated_at = "9999-12-31T23:59:59+00:00"
            app.user.TrackPopupMenu.return_value = 0
            app.start_request = Mock()
            with patch('window_layout.work_area', return_value=(0, 0, 1920, 1040)) as work:
                app.session_menu()
            work.assert_called_once_with(hwnd=456)
            self.assertTrue(any("Không rõ thời gian" in call.args[3]
                                for call in app.user.AppendMenuW.call_args_list))
            app.start_request.assert_not_called()
            app.user.DestroyMenu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
