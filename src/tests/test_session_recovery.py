import json
from pathlib import Path
import tempfile
import unittest

from session_state import Session


class SessionRecoveryTests(unittest.TestCase):
    def test_non_object_json_never_crashes_or_overwrites_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            for payload in ("[]", "null", "42", '"text"', "true"):
                path.write_text(payload, encoding="utf-8")
                session = Session(path)
                self.assertTrue(session.error)
                self.assertEqual(session.messages, [])
                session.save()
                self.assertEqual(path.read_text(encoding="utf-8"), payload)

    def test_invalid_fields_do_not_leave_partly_loaded_history(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            fields = (("capture_pages", float("inf")), ("capture_pages", -1),
                      ("capture_pages", "bad"), ("mode", float("nan")),
                      ("mode", None), ("auto_copy", "false"), ("problem", {}),
                      ("capture_missing", [None]), ("last_answer", "\ud800"))
            for name, value in fields:
                payload = json.dumps({"messages": [{"role": "user", "content": "old problem"}], name: value})
                path.write_text(payload, encoding="utf-8")
                session = Session(path)
                self.assertTrue(session.error, name)
                self.assertEqual(session.messages, [], name)
                self.assertEqual(session.problem, "")
                session.save()
                self.assertEqual(path.read_text(encoding="utf-8"), payload)

    def test_valid_existing_session_and_optional_defaults_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.write_text(json.dumps({"messages": [{"role": "user", "content": "problem"}],
                                       "problem": "problem", "mode": 2, "auto_copy": False,
                                       "capture_pages": 1, "capture_missing": ["constraints"]}), encoding="utf-8")
            session = Session(path)
            self.assertFalse(session.error)
            self.assertEqual(session.problem, "problem")
            self.assertEqual(session.mode, 0)
            self.assertFalse(session.auto_copy)
            self.assertEqual(session.capture_missing, ["constraints"])

    def test_reset_preserves_all_previous_corrupt_backups(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.with_suffix(".unreadable.json").write_text("earlier backup", encoding="utf-8")
            path.write_text("[]", encoding="utf-8")
            session = Session(path)
            session.reset()
            self.assertEqual(path.with_suffix(".unreadable.json").read_text(), "earlier backup")
            self.assertEqual(path.with_suffix(".unreadable.1.json").read_text(), "[]")
            self.assertFalse(Session(path).error)

    def test_excessively_nested_json_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.write_text("[" * 2000 + "0" + "]" * 2000, encoding="utf-8")
            self.assertTrue(Session(path).error)
