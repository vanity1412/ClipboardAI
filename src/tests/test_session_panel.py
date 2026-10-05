import ctypes
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from session_state import Session, GENERATE
from windows_native import WindowsApp


class SessionTests(unittest.TestCase):
    def test_capture_draft_survives_restart_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            session = Session(path)
            session.capture_text = "part one"
            session.capture_source = "hash"
            session.capture_pages = 2
            session.capture_missing = ["output"]
            session.save()
            restored = Session(path)
            self.assertEqual((restored.capture_text, restored.capture_source, restored.capture_pages), ("part one", "hash", 2))
            self.assertEqual(restored.capture_missing, ["output"])
            restored.reset()
            self.assertEqual(Session(path).capture_text, "")

    def test_restart_retains_problem_answer_and_mode(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            session = Session(path)
            session.mode = 1
            session.commit([{"role": "user", "content": "đề"}, {"role": "assistant", "content": "code"}], "code", "đề")
            restored = Session(path)
            self.assertEqual((restored.problem, restored.last_answer, restored.mode), ("đề", "code", 0))
            self.assertEqual(restored.context(), session.messages)
            restored.reset()
            self.assertEqual(Session(path).messages, [])

    def test_corrupt_history_is_preserved_until_reset(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.write_text("not json", encoding="utf-8")
            session = Session(path)
            session.save()
            self.assertEqual(path.read_text(), "not json")
            session.reset()
            self.assertTrue(path.with_suffix(".unreadable.json").exists())
            self.assertEqual(Session(path).messages, [])

    def test_context_keeps_problem_and_recent_answer(self):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root) / "session.json")
            session.mode = 0  # Legacy repair context; chat keeps full history.
            session.messages = [{"role": "user", "content": "original"}] + [
                {"role": "assistant", "content": "x" * 3000},
                {"role": "user", "content": "repair"},
                {"role": "assistant", "content": "latest"}]
            self.assertEqual(session.context(1024), session.messages)


@unittest.skipUnless(os.name == "nt", "Native Windows UI")
class PanelTests(unittest.TestCase):
    def test_real_panel_image_clipboard_modes_and_conversation(self):
        import windows_native as native
        from verify_chat_flow import run
        run(native)
        report = json.loads((native.ROOT / 'chat-verification.json').read_text(encoding='utf-8'))
        self.assertTrue(report['ok'])
        self.assertTrue(report['f4_image_and_clipboard_immediate'])
        self.assertTrue(report['programming_code_and_explanation'])
