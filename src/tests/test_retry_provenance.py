"""Retry identity regressions; no live AI, clipboard or desktop access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from session_state import Session
from tests import test_selected_fixes as fixtures


class RetryProvenanceTests(unittest.TestCase):
    def completed_app(self, root):
        app = fixtures.SelectedFixTests().app(root)
        app.session.messages = [dict(role='user', content='Repeat this task'),
                                dict(role='assistant', content='First completed answer')]
        app.session.last_request = 'Repeat this task'
        app.session.last_action = 'chat'
        app.session.commit([], 'First completed answer')
        app.read_clipboard.return_value = ('Repeat this task', 13)
        app.write_clipboard = Mock(return_value=True)
        return app

    def complete(self, app, job, answer):
        content = job['text'] + ('\n[Ảnh đính kèm]' if job.get('image_png') is not None else '')
        app.results.put(('done', job, answer, [dict(role='user', content=content),
                                             dict(role='assistant', content=answer)]))
        with patch('windows_native.log_event'):
            app.tick()

    def test_same_text_cancel_retry_keeps_previous_completed_turn_and_ignores_late_result(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.completed_app(root)
            previous = list(app.session.messages)
            app.reply_clipboard()
            cancelled = app.jobs.get_nowait()
            self.assertEqual(cancelled['memory_messages'], previous)
            self.assertEqual(app.session.last_request_state, 'pending')
            app.cancel_request()
            self.complete(app, cancelled, 'Late cancelled answer')
            self.assertEqual(app.session.messages, previous)
            app.write_clipboard.assert_not_called()

            # Provenance survives restart; repeated text is not reclassified.
            app.session = Session(app.session.path)
            self.assertEqual(app.session.last_request_state, 'pending')
            app.read_clipboard.return_value = ('Unrelated clipboard question', 99)
            app.resend_session()
            retry = app.jobs.get_nowait()
            self.assertEqual(retry['text'], 'Repeat this task')
            self.assertEqual(retry['memory_messages'], previous)
            self.assertFalse(retry['replace_last'])
            self.complete(app, retry, 'Second completed answer')
            self.assertEqual(app.session.messages[:2], previous)
            self.assertEqual(len(app.session.messages), 4)
            self.assertEqual(app.session.last_answer, 'Second completed answer')
            self.assertEqual(app.session.last_request_state, 'completed')
            self.assertEqual(Session(app.session.path).messages, app.session.messages)
            app.write_clipboard.assert_called_once()

    def test_cancelled_regeneration_keeps_original_context_across_retries(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.completed_app(root)
            previous = list(app.session.messages)
            app.resend_session()
            first = app.jobs.get_nowait()
            self.assertTrue(first['replace_last'])
            self.assertEqual(first['memory_messages'], [])
            self.assertEqual(app.session.last_request_state, 'retrying_completed')
            app.cancel_request()
            self.assertEqual(app.session.messages, previous)
            app.session = Session(app.session.path)
            self.assertEqual(app.session.last_request_state, 'retrying_completed')

            app.resend_session()
            second = app.jobs.get_nowait()
            self.assertTrue(second['replace_last'])
            self.assertEqual(second['memory_messages'], first['memory_messages'])
            self.complete(app, second, 'Regenerated answer')
            self.assertEqual(app.session.messages, [dict(role='user', content='Repeat this task'),
                                                  dict(role='assistant', content='Regenerated answer')])
            self.assertEqual(app.session.last_request_state, 'completed')

    def test_failed_same_text_followup_is_retried_without_replacing_completed_history(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.completed_app(root)
            previous = list(app.session.messages)
            app.reply_clipboard()
            failed = app.jobs.get_nowait()
            app.results.put(('failed', failed, 'Synthetic network failure'))
            app.text = Mock(return_value='')
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.session.last_request_state, 'pending')
            app.resend_session()
            retry = app.jobs.get_nowait()
            self.assertFalse(retry['replace_last'])
            self.assertEqual(retry['memory_messages'], previous)

    def test_failed_regeneration_does_not_add_its_old_answer_to_the_next_retry_context(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.completed_app(root)
            app.resend_session()
            failed = app.jobs.get_nowait()
            app.results.put(('failed', failed, 'Synthetic provider failure'))
            app.text = Mock(return_value='')
            with patch('windows_native.log_event'):
                app.tick()
            self.assertEqual(app.session.last_request_state, 'retrying_completed')
            app.resend_session()
            retry = app.jobs.get_nowait()
            self.assertTrue(retry['replace_last'])
            self.assertEqual(retry['memory_messages'], failed['memory_messages'])

    def test_image_regeneration_preserves_prior_turn_and_collected_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.completed_app(root)
            prior = list(app.session.messages)
            app.session.last_request = 'Describe these images'
            app.session.last_action = 'image'
            app.session.commit([dict(role='user', content='Describe these images\n[Ảnh đính kèm]'),
                                dict(role='assistant', content='Old image answer')], 'Old image answer')
            ident = app.session.active_id
            app.images.add(ident, b'first collected image')
            app.images.add(ident, b'latest collected image')
            app.last_image = dict(session_id=ident, png=b'latest collected image')
            app.read_clipboard.return_value = ('Different content from clipboard', 77)
            app.resend_session()
            retry = app.jobs.get_nowait()
            self.assertEqual(retry['text'], 'Describe these images')
            self.assertEqual(retry['image_png'], b'latest collected image')
            self.assertEqual(retry['memory_messages'], prior)
            self.assertEqual(app.images.get(ident), [b'first collected image', b'latest collected image'])
            self.assertEqual(retry['mode'], app.session.mode)
            self.assertEqual(app.session.active_id, ident)
            self.complete(app, retry, 'New image answer')
            self.assertEqual(app.session.messages[:2], prior)
            self.assertEqual(len(app.session.messages), 4)
            self.assertEqual(app.images.get(ident), [b'first collected image', b'latest collected image'])


class RetryArchiveTests(unittest.TestCase):
    def test_legacy_completed_pair_migrates_without_rewriting_or_dropping_history(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            messages = [dict(role='user', content='Describe image\n[Ảnh đính kèm]'),
                        dict(role='assistant', content='Saved answer')]
            payload = json.dumps(dict(version=4, mode=3, last_request='Describe image',
                                      last_action='image', messages=messages, last_answer='Saved answer'))
            path.write_text(payload, encoding='utf-8')
            session = Session(path)
            self.assertFalse(session.error)
            self.assertEqual(session.last_request_state, 'completed')
            self.assertEqual(session.messages, messages)
            self.assertEqual(path.read_text(encoding='utf-8'), payload)
            self.assertTrue(session.save())
            self.assertEqual(Session(path).messages, messages)
            self.assertEqual(Session(path).last_request_state, 'completed')

    def test_explicit_pending_provenance_is_retained_even_when_saved_text_matches(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            messages = [dict(role='user', content='Same request'),
                        dict(role='assistant', content='Earlier answer')]
            path.write_text(json.dumps(dict(version=5, mode=3, last_request='Same request',
                last_action='chat', last_request_state='pending', messages=messages)), encoding='utf-8')
            session = Session(path)
            self.assertFalse(session.error)
            self.assertEqual(session.last_request_state, 'pending')
            self.assertTrue(session.save())
            self.assertEqual(Session(path).last_request_state, 'pending')
            self.assertEqual(Session(path).messages, messages)

    def test_legacy_pending_request_and_new_or_reset_sessions_remain_pending(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            path.write_text(json.dumps(dict(version=4, mode=3, last_request='Unanswered request')),
                            encoding='utf-8')
            session = Session(path)
            self.assertEqual(session.last_request_state, 'pending')
            self.assertTrue(session.new_problem('New request'))
            self.assertEqual(session.last_request_state, 'pending')
            session.commit([dict(role='user', content='New request'),
                            dict(role='assistant', content='Answer')], 'Answer')
            self.assertEqual(session.last_request_state, 'completed')
            self.assertTrue(session.reset())
            self.assertEqual(Session(path).last_request_state, 'pending')

    def test_invalid_provenance_protects_the_original_archive(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'session.json'
            payload = json.dumps(dict(version=5, last_request_state='unknown'))
            path.write_text(payload, encoding='utf-8')
            session = Session(path)
            self.assertTrue(session.error)
            self.assertFalse(session.save())
            self.assertEqual(path.read_text(encoding='utf-8'), payload)


if __name__ == '__main__':
    unittest.main()
