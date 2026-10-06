"""Regression checks using private sessions and simulated transports only."""
import tempfile
import unittest

from request_display import RequestDisplay
from tests import test_selected_fixes as fixtures


class RuntimeReliabilityTests(unittest.TestCase):
    def app(self, root):
        return fixtures.SelectedFixTests().app(root)

    def test_rejected_f8_reports_current_reason_without_old_answer(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.request_display = RequestDisplay()
            app.request_display.finish('done', 2, 'OLD ANSWER', copy='copied')
            app.read_clipboard.return_value = None
            app.send_clipboard()
            self.assertIn('Clipboard đang bận', app.result_text())
            self.assertNotIn('OLD ANSWER', app.result_text())
            self.assertIn('Clipboard đang bận', app.request_display.header(10000))
            self.assertTrue(app.jobs.empty())

    def test_new_request_and_completion_clear_old_feedback(self):
        display = RequestDisplay()
        display.show_feedback('Clipboard đang bận')
        display.begin(101)
        self.assertNotIn('Clipboard', display.header(102))
        display.show_feedback('F9 chưa gửi')
        display.finish('done', 3, 'NEW ANSWER', copy='copied')
        self.assertIn('NEW ANSWER', display.preview(103))
        self.assertNotIn('chưa gửi', display.preview(103))

    def test_long_rejection_message_wraps_without_mixing_old_answers(self):
        display = RequestDisplay()
        display.finish('done', 2, 'OLD ANSWER')
        message = 'Clipboard đang bận; copy nội dung rồi nhấn F8 lại để gửi câu hỏi.'
        display.show_feedback(message)
        pages = display.hover_pages(lambda value: len(value) * 7, 150)
        self.assertGreater(len(pages[0].rows), 1)
        self.assertEqual(''.join(''.join(pages[0].rows).split()), ''.join(message.split()))
        self.assertNotIn('OLD ANSWER', ''.join(pages[0].rows))
