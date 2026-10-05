"""Official CLI event regressions; no login, network or child process."""
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from browser_provider import BrowserSession


class BrowserCompletionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.session = BrowserSession({'id': 'browser-test'}, folder.name, threading.Event())
        self.session.temporary = SimpleNamespace(name=folder.name)
        self.session.request = Mock(side_effect=[
            {'account': {'type': 'chatgpt'}}, {'thread': {'id': 'thread'}}, {'turn': {'id': 'turn'}}])

    def event(self, method, **params):
        return {'method': method, 'params': dict(threadId='thread', turnId='turn', **params)}

    def message(self, text, ident='answer', phase='final_answer'):
        return self.event('item/completed', item={
            'type': 'agentMessage', 'id': ident, 'phase': phase, 'text': text})

    def completed(self, status='completed'):
        return self.event('turn/completed', turn={'id': 'turn', 'status': status})

    def ask(self, *events, callback=None):
        for event in events:
            self.session.events.put(event)
        return self.session.ask('model', [{'role': 'user', 'content': 'question'}], 3, callback)

    def test_recoverable_error_waits_for_final_turn_and_discards_failed_attempt(self):
        callback = Mock()
        answer = self.ask(
            self.event('item/agentMessage/delta', delta='failed attempt'),
            self.message('failed attempt', ident='old'),
            self.event('error', willRetry=True, error={'message': 'private provider error'}),
            self.message('verified final', ident='new'), self.completed(), callback=callback)
        self.assertEqual(answer, 'verified final')
        self.assertEqual([call.args[0] for call in callback.call_args_list], [None, 'failed attempt', None])

    def test_terminal_error_keeps_raw_provider_message_private(self):
        with self.assertRaises(RuntimeError) as raised:
            self.ask(self.event('error', willRetry=False, error={'message': 'private provider error'}))
        self.assertNotIn('private', str(raised.exception))

    def test_retry_does_not_accept_a_failed_turn_or_commentary_only(self):
        with self.assertRaises(RuntimeError):
            self.ask(self.event('error', willRetry=True), self.message('partial'), self.completed('failed'))

    def test_commentary_only_is_never_a_completed_answer(self):
        with self.assertRaises(RuntimeError):
            self.ask(self.message('working', phase='commentary'), self.completed())

    def test_current_context_compaction_notification_is_not_a_tool(self):
        self.assertEqual(self.ask(
            self.event('item/completed', item={'type': 'contextCompaction', 'id': 'compact'}),
            self.message('final'), self.completed()), 'final')

    def test_account_refresh_uses_remaining_request_deadline(self):
        with patch('browser_provider.time.monotonic', return_value=100):
            self.assertEqual(self.ask(self.message('final'), self.completed()), 'final')
        account_request = self.session.request.call_args_list[0]
        self.assertEqual(account_request.args, ('account/read', {'refreshToken': True}, 3))
        thread_request = self.session.request.call_args_list[1]
        self.assertEqual(thread_request.args[1]['approvalPolicy'], 'on-request')

    def test_cancel_still_applies_when_completion_notifications_are_deferred(self):
        self.session.deferred = [self.message('final'), self.completed()]
        self.session.cancel.set()
        with self.assertRaises(InterruptedError):
            self.session.ask('model', [{'role': 'user', 'content': 'question'}], 3)


if __name__ == '__main__':
    unittest.main()
