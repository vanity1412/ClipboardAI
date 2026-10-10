import json
import base64
from io import BytesIO
import queue
import threading
import unittest
from unittest.mock import Mock

from cua_mcp import CuaMCP, CuaRPCError
from desktop_agent import DesktopAgent, action_arguments, decision, document_origin, model_snapshot, model_tool
from hotkey_settings import bindings


class FakeMCP:
    def __init__(self, *args):
        self.calls = []
        self.tools = {'click': {'name': 'click', 'inputSchema': {
            'properties': {'element_token': {'type': 'string'}}}}}
        self.snapshots = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'list_windows':
            data = {'windows': [{'window_id': 12, 'title': 'Quiz'}]}
        elif name == 'get_window_state':
            self.snapshots += 1
            data = {'elements': [{'element_token': 's12345678:1', 'label': 'B. 4'}],
                    'selected': self.snapshots > 1}
        else:
            data = {'verified': True, 'effect': 'confirmed'}
        return {'structuredContent': data}


class SemanticQuizMCP(FakeMCP):
    """Interactive page with independent answer/page/scroll state."""
    def __init__(self, *, answered=False, next_page=False):
        super().__init__()
        self.answered = answered
        self.next_page = next_page
        self.tools['scroll'] = {'name': 'scroll', 'inputSchema': {'properties': {
            key: {} for key in ('x', 'y', 'direction', 'amount', 'by', 'element_token')}}}
        self.tools['press_key'] = {'name': 'press_key', 'inputSchema': {'properties': {'key': {}}}}
        self.tools['click']['inputSchema']['properties'].update(button={}, count={})
        self.radio_page = True
        self.observation_refusals = []
        from PIL import Image
        buffer = BytesIO()
        Image.new('RGB', (800, 600), 'white').save(buffer, format='PNG')
        self.picture = base64.b64encode(buffer.getvalue()).decode('ascii')

    def page(self):
        elements = [{'element_token': 'page', 'role': 'Document',
                     'value': 'https://quiz.example/attempt', 'element_index': 0,
                     'depth': 0, 'frame': {'x': 0, 'y': 0, 'w': 800, 'h': 600},
                     'screenshot_frame': {'x': 0, 'y': 0, 'w': 800, 'h': 600}}]
        if self.radio_page:
            elements.append({'element_token': 'question', 'role': 'Group', 'label': 'Question 1',
                             'element_index': 1, 'parent_index': 0, 'depth': 1,
                             'screenshot_frame': {'x': 40, 'y': 80, 'w': 650, 'h': 220}})
            for index, label in enumerate(('A. 3', 'B. 4', 'C. 5')):
                elements.append({'element_token': 'answer:' + str(index), 'role': 'RadioButton',
                                 'label': label, 'selected': self.answered and index == 1,
                                 'actions': ['select'], 'element_index': index + 2,
                                 'parent_index': 1, 'depth': 2,
                                 'screenshot_frame': {'x': 50, 'y': 120 + 50 * index, 'w': 180, 'h': 30}})
        if self.next_page:
            elements.append({'element_token': 'next', 'label': 'Next question', 'role': 'Button',
                             'actions': ['invoke'], 'enabled': True,
                             'screenshot_frame': {'x': 50, 'y': 350, 'w': 180, 'h': 40}})
        elements.append({'element_token': 'submit', 'label': 'Submit quiz', 'role': 'Button',
                         'actions': ['invoke'], 'enabled': True,
                         'screenshot_frame': {'x': 50, 'y': 440, 'w': 180, 'h': 40}})
        return {'elements': elements, 'screenshot_width': 800, 'screenshot_height': 600,
                'window_bounds': {'x': 0, 'y': 0, 'width': 800, 'height': 600},
                'capture_id': 'current-capture'}

    def call(self, name, args):
        self.calls.append((name, args))
        if name == 'list_windows':
            return {'structuredContent': {'windows': [{'window_id': 12, 'title': 'Quiz'}]}}
        if name == 'get_window_state':
            self.snapshots += 1
            if self.observation_refusals:
                return self.observation_refusals.pop(0)
            return {'structuredContent': self.page(),
                    'content': [{'type': 'image', 'mimeType': 'image/png', 'data': self.picture}]}
        if name == 'click':
            if args.get('element_token') == 'answer:1':
                self.answered = True
            elif args.get('element_token') == 'next':
                self.next_page = False
            elif args.get('element_token') == 'submit':
                raise AssertionError('The completion guard must never submit the quiz')
        return {'structuredContent': {'effect': 'confirmed'}}


class DesktopAgentTests(unittest.TestCase):
    def test_f2_is_registered_without_repeat_at_native_registration(self):
        self.assertEqual(bindings()[218], ('F2', 0, 0x71))
        with self.assertRaises(ValueError):
            bindings({'201': 'F2', '218': 'F2'})
        migrated = bindings({'201': 'F2'})
        self.assertEqual(migrated[201][0], 'F2')
        self.assertEqual(migrated[218][0], 'Ctrl+Alt+F2')

    def test_observe_act_observe_done_target_locked(self):
        mcp = FakeMCP()
        client = Mock()
        client.ask.side_effect = [
            (json.dumps({'tool': 'click', 'arguments': {'element_token': 's12345678:1'}}), 'test'),
            (json.dumps({'done': True, 'summary': 'Câu 1: B'}), 'test')]
        agent = DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp)
        self.assertEqual(agent.run(11, 'Quiz'), 'Câu 1: B')
        self.assertEqual([n for n, _ in mcp.calls],
                         ['list_windows', 'get_window_state', 'click', 'get_window_state'])
        args = mcp.calls[2][1]
        self.assertEqual(args['target'], {'kind': 'window', 'pid': 11, 'window_id': 12})
        self.assertEqual(args['delivery_mode'], 'background')

    def test_cancel_after_model_returns_never_dispatches_input(self):
        mcp, cancel = FakeMCP(), threading.Event()
        client = Mock()
        def answer(*args, **kwargs):
            cancel.set()
            return json.dumps({'tool': 'click', 'arguments': {'element_token': 's12345678:1'}}), 'test'
        client.ask.side_effect = answer
        agent = DesktopAgent('.', client, cancel, Mock(), lambda *a: mcp)
        with self.assertRaises(InterruptedError):
            agent.run(11, 'Quiz')
        self.assertNotIn('click', [n for n, _ in mcp.calls])

    def test_stale_or_foreign_token_and_target_override_never_dispatch(self):
        snapshot = FakeMCP().call('get_window_state', {})
        tool = FakeMCP().tools['click']
        for args in ({'element_token': 'old'}, {'target': {'kind': 'desktop'}}, {'scope': 'desktop'}):
            with self.assertRaises(RuntimeError):
                action_arguments({'tool': 'click', 'arguments': args}, snapshot, tool,
                                 {'pid': 11, 'window_id': 12})

    def test_submit_and_enter_are_blocked(self):
        snapshot = {'structuredContent': {'elements': [{'element_token': 's12345678:1', 'label': 'Nộp bài'}]}}
        with self.assertRaises(RuntimeError):
            action_arguments({'tool': 'click', 'arguments': {'element_token': 's12345678:1'}},
                             snapshot, FakeMCP().tools['click'], {'pid': 11, 'window_id': 12})
        for key in ('return', 'space', 'Tab'):
            with self.assertRaises(RuntimeError):
                action_arguments({'tool': 'press_key', 'arguments': {'key': key}},
                                 snapshot, {'inputSchema': {'properties': {'key': {}}}}, {})

    def test_model_cannot_request_other_mcp_tools(self):
        for text in ('[]', 'bad', '{"tool":"kill_app","arguments":{}}'):
            with self.assertRaises(RuntimeError):
                decision(text)

    def test_provider_single_action_formats_are_normalized(self):
        for value in (
            {'action': 'click', 'x': 10, 'y': 20},
            {'name': 'click', 'args': {'x': 10, 'y': 20}},
            {'tool': 'click', 'arguments': '{"x":10,"y":20}'}):
            parsed = decision(json.dumps(value))
            self.assertEqual(parsed['tool'], 'click')
            self.assertEqual(parsed['arguments'], {'x': 10, 'y': 20})
        mixed = decision('{"tool":"click","element_token":"fresh","arguments":{"button":"left"}}')
        self.assertEqual(mixed['arguments'], {'element_token': 'fresh', 'button': 'left'})
        prose = decision('Action:\n{"tool":"click","arguments":{"element_token":"fresh"}}\nDone describing.')
        self.assertEqual(prose['arguments']['element_token'], 'fresh')
        with self.assertRaises(RuntimeError):
            decision('{"tool":"click","x":10,"arguments":{"x":20,"y":10}}')
        with self.assertRaises(RuntimeError):
            decision('{"actions":[{"tool":"click"},{"tool":"click"}]}')

    def test_click_without_grounded_target_never_reaches_driver(self):
        with self.assertRaises(RuntimeError) as caught:
            action_arguments({'tool': 'click', 'arguments': {}}, FakeMCP().call('get_window_state', {}),
                             FakeMCP().tools['click'], {'pid': 11, 'window_id': 12})
        self.assertEqual(caught.exception.code, 'action_target_missing')
        choice = decision('{"tool":"click","target":{"kind":"desktop"},"arguments":{"element_token":"fresh"}}')
        with self.assertRaises(RuntimeError):
            action_arguments(choice, FakeMCP().call('get_window_state', {}),
                             FakeMCP().tools['click'], {'pid': 11, 'window_id': 12})

    def test_bad_model_response_reobserves_before_any_action(self):
        mcp, client = FakeMCP(), Mock()
        client.ask.side_effect = [('invalid', 'test'),
            ('{"tool":"click","arguments":{"element_token":"s12345678:1"}}', 'test'),
            ('{"done":true}', 'test')]
        DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz')
        self.assertEqual([n for n, _ in mcp.calls], ['list_windows', 'get_window_state',
            'get_window_state', 'click', 'get_window_state'])
        self.assertIn('decision_json', client.ask.call_args_list[1].args[0][0]['text'])

    def test_persistent_bad_decision_is_bounded_without_actions(self):
        mcp, client = FakeMCP(), Mock(ask=Mock(return_value=('invalid', 'test')))
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz')
        self.assertEqual(caught.exception.code, 'decision_json')
        self.assertEqual(client.ask.call_count, 3)
        self.assertNotIn('click', [n for n, _ in mcp.calls])

    def test_rpc_invalid_arguments_reobserves_but_ambiguous_error_is_not_replayed(self):
        for rpc_code, expected in ((-32602, ['background', 'background']), (-32603, ['background'])):
            with self.subTest(rpc_code=rpc_code):
                mcp, attempts = FakeMCP(), []
                original = mcp.call
                def call(name, args):
                    if name == 'click':
                        attempts.append(args['delivery_mode'])
                        if len(attempts) == 1:
                            mcp.calls.append((name, args))
                            raise CuaRPCError(rpc_code)
                    return original(name, args)
                mcp.call = call
                click = '{"tool":"click","arguments":{"element_token":"s12345678:1"}}'
                client = Mock(ask=Mock(side_effect=[(click, 'test'), (click, 'test'), ('{"done":true}', 'test')]))
                agent = DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp)
                if rpc_code == -32602:
                    agent.run(11, 'Quiz')
                    self.assertEqual(mcp.snapshots, 3)
                else:
                    with self.assertRaises(CuaRPCError):
                        agent.run(11, 'Quiz')
                self.assertEqual(attempts, expected)

    def test_origin_can_change_inside_locked_window(self):
        origin = ('https', 'quiz.example')
        self.assertEqual(document_origin({'elements': [
            {'role': 'Document', 'value': 'https://second.example/questions'}]}, origin, allow_change=True),
            ('https', 'second.example'))
        with self.assertRaises(RuntimeError):
            document_origin({'elements': [{'role': 'Document', 'value': 'https://second.example/login'}]},
                            origin, allow_change=True)

    def test_compact_snapshot_preserves_selected_state_and_geometry_without_duplicate_tree(self):
        data = {'elements': [{'element_token': 's12345678:1', 'label': 'B', 'selected': True,
                 'frame': {'x': 100, 'y': 200, 'w': 20, 'h': 20}}], 'tree_markdown': 'duplicate tree',
                 'window_bounds': {'x': 0, 'y': 100, 'width': 800, 'height': 600}}
        parsed = json.loads(model_snapshot(data))
        self.assertNotIn('tree_markdown', parsed)
        self.assertTrue(parsed['elements'][0]['selected'])
        self.assertEqual(parsed['window_bounds'], data['window_bounds'])

    def test_mcp_cancel_interrupts_wait_without_waiting_timeout(self):
        cancel = threading.Event()
        mcp = CuaMCP('.', cancel)
        mcp.send = Mock(side_effect=lambda _: cancel.set())
        with self.assertRaises(InterruptedError):
            mcp.request('tools/call', {})

    def test_login_or_different_origin_stops_before_model_and_input(self):
        first = {'elements': [{'role': 'Document', 'value': 'https://quiz.example/questions/1'}]}
        origin = document_origin(first)
        for url in ('edge://sync-confirmation-dialog/', 'https://quiz.example/login',
                    'https://other.example/questions'):
            with self.assertRaises(RuntimeError):
                document_origin({'elements': [{'role': 'Document', 'value': url}]}, origin)

    def test_chrome_footer_and_small_login_frame_do_not_replace_main_quiz(self):
        main = {'role': 'Document', 'value': 'https://utexlms.hcmute.edu.vn/mod/quiz/attempt.php',
                'frame': {'w': 1921, 'h': 875}, 'depth': 7}
        footer = {'role': 'Document', 'value': 'chrome://newtab-footer/',
                  'frame': {'w': 1921, 'h': 73}, 'depth': 7}
        login = {'role': 'Document', 'value': 'https://auth.example/login',
                 'frame': {'w': 200, 'h': 100}, 'depth': 8}
        expected = ('https', 'utexlms.hcmute.edu.vn')
        for documents in ([main, footer], [footer, main], [login, main, footer]):
            with self.subTest(documents=documents):
                self.assertEqual(document_origin({'elements': documents}), expected)
                self.assertEqual(document_origin({'elements': documents}, expected), expected)
        with self.assertRaises(RuntimeError):
            document_origin({'elements': [dict(main, value='https://auth.example/login'), login]}, expected)

    def test_offscreen_settings_document_does_not_replace_visible_page(self):
        self.assertEqual(document_origin({'elements': [
            {'role': 'Document', 'value': 'chrome://settings/', 'offscreen': True,
             'frame': {'w': 2000, 'h': 2000}},
            {'role': 'Document', 'value': 'https://quiz.example/attempt', 'frame': {'w': 800, 'h': 600}}
        ]}), ('https', 'quiz.example'))

    def test_background_refusal_uses_fresh_snapshot_before_foreground_retry(self):
        mcp = FakeMCP()
        original = mcp.call
        attempts = []
        def call(name, args):
            if name == 'click':
                attempts.append(args['delivery_mode'])
                if len(attempts) == 1:
                    mcp.calls.append((name, args))
                    return {'isError': True, 'structuredContent': {'code': 'background_unavailable'}}
            return original(name, args)
        mcp.call = call
        client = Mock()
        click = json.dumps({'tool': 'click', 'arguments': {'element_token': 's12345678:1'}})
        client.ask.side_effect = [(click, 'test'), (click, 'test'), ('{"done":true}', 'test')]
        DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz')
        self.assertEqual(attempts, ['background', 'foreground'])
        self.assertEqual([n for n, _ in mcp.calls], ['list_windows', 'get_window_state',
                         'click', 'get_window_state', 'click', 'get_window_state'])

    def test_f2_dispatch_and_f10_stops_even_when_paused(self):
        import tempfile
        from tests import test_selected_fixes as fixtures
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.start_desktop_agent = Mock()
            app.window_proc(app.hwnd, 0x0312, 218, 0)
            app.start_desktop_agent.assert_called_once()
            app.desktop_agent = Mock()
            app.enabled = False
            app.cancel_request = Mock()
            app.window_proc(app.hwnd, 0x0312, 203, 0)
            app.cancel_request.assert_called_once()

    def test_large_observation_remains_valid_bounded_json(self):
        data = {'elements': [{'element_token': 's12345678:' + str(i), 'label': 'X' * 2000}
                             for i in range(500)], 'capture_id': 'capture'}
        content = model_snapshot(data)
        self.assertLessEqual(len(content), 65000)
        parsed = json.loads(content)
        self.assertTrue(parsed['truncated'])
        self.assertEqual(parsed['capture_id'], 'capture')
        self.assertTrue(parsed['elements'])

    def test_model_schema_omits_host_managed_target_and_delivery(self):
        tool = {'name': 'click', 'inputSchema': {'properties': {
            'target': {}, 'scope': {}, 'pid': {}, 'delivery_mode': {}, 'capture_id': {},
            'x': {'type': 'number'}, 'y': {'type': 'number'}, 'button': {}, 'count': {}},
            'required': ['target', 'x', 'y']}}
        schema = model_tool(tool)['inputSchema']
        self.assertEqual(set(schema['properties']), {'x', 'y', 'button', 'count'})
        self.assertEqual(schema['required'], ['x', 'y'])
        self.assertEqual(schema['properties']['button']['enum'], ['left'])
        self.assertFalse(schema['additionalProperties'])

    def test_busy_f2_updates_visible_feedback(self):
        import tempfile
        from tests import test_selected_fixes as fixtures
        with tempfile.TemporaryDirectory() as folder:
            app = fixtures.SelectedFixTests().app(folder)
            app.busy = True
            app.start_desktop_agent()
            self.assertIn('F10', app.display_state().feedback)
            self.assertEqual(app.state, app.display_state().feedback)

    def test_real_driver_nested_foreground_escalation_reobserves_before_retry(self):
        for feedback in (
                {'error': {'code': 'background_unavailable'}, 'effect': 'refused'},
                {'effect': 'suspected_noop', 'escalation': {'target': 'foreground', 'reason': 'suspected_noop'}},
                {'effect': 'refused', 'escalation': {'target': 'foreground', 'reason': 'route_unavailable'}}):
            with self.subTest(feedback=feedback):
                mcp = FakeMCP()
                original = mcp.call
                attempts = []
                def call(name, args):
                    if name == 'click':
                        attempts.append(args['delivery_mode'])
                        if len(attempts) == 1:
                            mcp.calls.append((name, args))
                            return {'isError': True, 'structuredContent': feedback}
                    return original(name, args)
                mcp.call = call
                click = json.dumps({'tool': 'click', 'arguments': {'element_token': 's12345678:1'}})
                client = Mock(ask=Mock(side_effect=[(click, 'test'), (click, 'test'), ('{"done":true}', 'test')]))
                DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz', 12)
                self.assertEqual(attempts, ['background', 'foreground'])
                self.assertEqual([n for n, _ in mcp.calls], ['list_windows', 'get_window_state',
                    'click', 'get_window_state', 'click', 'get_window_state'])

    def test_projection_keeps_question_hierarchy_but_omits_content_outside_viewport(self):
        data = SemanticQuizMCP().page()
        data['elements'].extend([
            {'element_token': 'below', 'role': 'RadioButton', 'label': 'A. Below fold',
             'screenshot_frame': {'x': 50, 'y': 800, 'w': 200, 'h': 30}},
            {'element_token': 'above', 'role': 'RadioButton', 'label': 'A. Above fold',
             'screenshot_frame': {'x': 50, 'y': -100, 'w': 200, 'h': 30}}])
        projected = json.loads(model_snapshot(data))
        tokens = {element['element_token']: element for element in projected['elements']}
        self.assertNotIn('below', tokens)
        self.assertNotIn('above', tokens)
        self.assertEqual(tokens['answer:1']['parent_index'], 1)
        self.assertEqual(tokens['answer:1']['element_index'], 3)
        self.assertEqual(tokens['answer:1']['depth'], 2)

    def test_radio_groups_count_question_answers_instead_of_unselected_alternatives(self):
        from desktop_agent import visible_radio_groups, quiz_progress
        data = SemanticQuizMCP(answered=True).page()
        groups = visible_radio_groups(data)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]['answered'])
        self.assertEqual(len(groups[0]['elements']), 3)
        progress = quiz_progress(data)
        self.assertEqual(progress['visible_groups'], 1)
        self.assertEqual(progress['answered_groups'], 1)
        self.assertEqual(progress['unanswered_groups'], [])
        self.assertTrue(progress['end_visible'])

    def test_repeated_answer_prefixes_form_separate_questions_without_parent_groups(self):
        from desktop_agent import visible_radio_groups
        data = SemanticQuizMCP().page()
        radios = [element for element in data['elements'] if element.get('role') == 'RadioButton']
        elements = []
        for question in range(2):
            for index, radio in enumerate(radios):
                item = dict(radio, element_token='question:' + str(question) + ':' + str(index),
                            selected=question == 0 and index == 1)
                for field in ('parent_index', 'element_index', 'depth'):
                    item.pop(field, None)
                item['screenshot_frame'] = dict(item['screenshot_frame'], y=100 + question * 200 + index * 30)
                elements.append(item)
        data['elements'] = [data['elements'][0], *elements]
        groups = visible_radio_groups(data)
        self.assertEqual(len(groups), 2)
        self.assertEqual([group['answered'] for group in groups], [True, False])

    def test_question_number_prefixes_and_unique_option_parents_still_form_one_answer_group(self):
        from desktop_agent import visible_radio_groups, quiz_progress
        data = SemanticQuizMCP(answered=True).page()
        for index, radio in enumerate(element for element in data['elements']
                                      if element.get('role') == 'RadioButton'):
            radio['label'] = 'Question 4: ' + radio['label']
            radio['parent_index'] = 50 + index
        groups = visible_radio_groups(data, visible_only=False)
        self.assertEqual(len(groups), 1)
        self.assertEqual([element['label'] for element in groups[0]['elements']],
                         ['Question 4: A. 3', 'Question 4: B. 4', 'Question 4: C. 5'])
        self.assertTrue(groups[0]['answered'])
        progress = quiz_progress(data)
        self.assertEqual(progress['known_groups'], 1)
        self.assertEqual(progress['known_answered_groups'], 1)
        self.assertEqual(progress['known_unanswered_count'], 0)

    def test_offscreen_answered_group_counts_once_despite_unselected_alternatives(self):
        from desktop_agent import quiz_progress
        data = SemanticQuizMCP(answered=True).page()
        radios = [element for element in data['elements'] if element.get('role') == 'RadioButton']
        for index, radio in enumerate(radios):
            duplicate = dict(radio, element_token='offscreen:' + str(index),
                             element_index=20 + index, parent_index=19, offscreen=True)
            duplicate['screenshot_frame'] = dict(radio['screenshot_frame'], y=900 + index * 50)
            data['elements'].append(duplicate)
        progress = quiz_progress(data)
        self.assertEqual(progress['visible_groups'], 1)
        self.assertEqual(progress['answered_groups'], 1)
        self.assertEqual(progress['known_groups'], 2)
        self.assertEqual(progress['known_answered_groups'], 2)
        self.assertEqual(progress['known_unanswered_count'], 0)
        self.assertEqual(progress['unanswered_offscreen'], [])

    def test_offscreen_unanswered_group_blocks_repeated_done_without_dispatching_input(self):
        from desktop_agent import quiz_progress
        mcp, trace = SemanticQuizMCP(answered=True), Mock()
        page = mcp.page
        def data_with_missing_question():
            data = page()
            radios = [element for element in data['elements'] if element.get('role') == 'RadioButton']
            for index, radio in enumerate(radios):
                missing = dict(radio, element_token='missing:' + str(index),
                               element_index=20 + index, parent_index=19,
                               selected=False, offscreen=True)
                missing['screenshot_frame'] = dict(radio['screenshot_frame'], y=900 + index * 50)
                data['elements'].append(missing)
            return data
        mcp.page = data_with_missing_question
        progress = quiz_progress(mcp.page())
        self.assertEqual(progress['known_unanswered_count'], 1)
        self.assertEqual(progress['unanswered_offscreen'], ['A. 3'])
        client = Mock(ask=Mock(return_value=('{"done":true,"summary":"Hoàn tất"}', 'test')))
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                         trace=trace).run(11, 'Quiz', 12)
        self.assertEqual(caught.exception.code, 'premature_done')
        self.assertEqual(client.ask.call_count, 3)
        self.assertEqual(mcp.snapshots, 3)
        self.assertFalse(any(name in ('click', 'scroll', 'press_key') for name, _ in mcp.calls))
        self.assertIn('known_unanswered_count', client.ask.call_args_list[0].args[0][0]['text'])
        self.assertTrue(any(call.kwargs.get('error_code') == 'premature_done' for call in trace.call_args_list))

    def test_current_tree_token_outside_screenshot_never_reaches_driver(self):
        mcp = SemanticQuizMCP()
        page = mcp.page
        def clipped_page():
            data = page()
            for element in data['elements']:
                if element.get('element_token') == 'answer:1':
                    element['screenshot_frame'] = dict(element['screenshot_frame'], y=900)
            return data
        mcp.page = clipped_page
        client = Mock(ask=Mock(return_value=(
            '{"tool":"click","arguments":{"element_token":"answer:1"}}', 'test')))
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz', 12)
        self.assertEqual(caught.exception.code, 'element_out_of_view')
        self.assertEqual(client.ask.call_count, 3)
        self.assertNotIn('click', [name for name, _ in mcp.calls])

    def test_visible_answer_cannot_be_selected_when_other_options_are_below_fold(self):
        mcp = SemanticQuizMCP()
        page = mcp.page
        def clipped_page():
            data = page()
            for index, element in enumerate(node for node in data['elements']
                                           if node.get('role') == 'RadioButton'):
                if index > 0:
                    element['screenshot_frame'] = dict(element['screenshot_frame'], y=800 + 50 * index)
            return data
        mcp.page = clipped_page
        client = Mock(ask=Mock(return_value=(
            '{"tool":"click","arguments":{"element_token":"answer:0"}}', 'test')))
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz', 12)
        self.assertEqual(caught.exception.code, 'question_cutoff')
        self.assertEqual(client.ask.call_count, 3)
        self.assertNotIn('click', [name for name, _ in mcp.calls])

    def test_scroll_distance_is_limited_to_keep_intermediate_questions_observable(self):
        mcp = SemanticQuizMCP()
        snapshot = {'structuredContent': mcp.page()}
        for by, requested, expected in (('page', 99, 1), ('line', 99, 3),
                                        ('page', 0, 1), ('line', -5, 1), ('line', 2, 2)):
            with self.subTest(by=by, requested=requested):
                args = action_arguments({'tool': 'scroll', 'arguments': {
                    'direction': 'down', 'amount': requested, 'by': by}}, snapshot,
                    mcp.tools['scroll'], {'pid': 11, 'window_id': 12})
                self.assertEqual(args['amount'], expected)
                self.assertEqual(args['by'], by)
                self.assertEqual(args['target'], {'kind': 'window', 'pid': 11, 'window_id': 12})

    def test_partially_clipped_last_radio_blocks_selecting_any_answer_in_that_question(self):
        mcp = SemanticQuizMCP()
        data = mcp.page()
        next(element for element in data['elements'] if element.get('element_token') == 'answer:2')[
            'screenshot_frame'] = {'x': 50, 'y': 585, 'w': 180, 'h': 30}
        with self.assertRaises(RuntimeError) as caught:
            action_arguments({'tool': 'click', 'arguments': {'element_token': 'answer:1'}},
                             {'structuredContent': data}, mcp.tools['click'], {'pid': 11, 'window_id': 12})
        self.assertEqual(caught.exception.code, 'question_cutoff')

    def test_pixel_radio_or_its_label_cannot_bypass_clipped_question_guard(self):
        mcp = SemanticQuizMCP()
        tool = {'inputSchema': {'properties': dict(
            mcp.tools['click']['inputSchema']['properties'], x={}, y={})}}
        for click_label in (False, True):
            with self.subTest(click_label=click_label):
                data = mcp.page()
                for index, element in enumerate(node for node in data['elements']
                                               if node.get('role') == 'RadioButton'):
                    if index > 0:
                        element['screenshot_frame'] = dict(element['screenshot_frame'], y=800 + 50 * index)
                    elif click_label:
                        element['screenshot_frame'] = dict(element['screenshot_frame'], w=18)
                if click_label:
                    data['elements'].append({'element_token': 'label-a', 'role': 'Text',
                        'label': 'A. 3', 'screenshot_frame': {'x': 85, 'y': 120, 'w': 150, 'h': 30}})
                snapshot = {'structuredContent': data, 'content': [
                    {'type': 'image', 'mimeType': 'image/png', 'data': mcp.picture}]}
                with self.assertRaises(RuntimeError) as caught:
                    action_arguments({'tool': 'click', 'arguments': {'x': 100 if click_label else 60, 'y': 130}},
                                     snapshot, tool, {'pid': 11, 'window_id': 12})
                self.assertEqual(caught.exception.code, 'question_cutoff')

    def test_scroll_over_submit_button_is_allowed_but_click_at_same_point_is_blocked(self):
        mcp = SemanticQuizMCP(answered=True)
        snapshot = {'structuredContent': mcp.page(), 'content': [
            {'type': 'image', 'mimeType': 'image/png', 'data': mcp.picture}]}
        scroll = action_arguments({'tool': 'scroll', 'arguments': {
            'x': 100, 'y': 450, 'direction': 'down', 'amount': 1, 'by': 'page'}},
            snapshot, mcp.tools['scroll'], {'pid': 11, 'window_id': 12})
        self.assertEqual(scroll['target'], {'kind': 'window', 'pid': 11, 'window_id': 12})
        self.assertEqual(scroll['amount'], 1)
        tool = {'inputSchema': {'properties': dict(
            mcp.tools['click']['inputSchema']['properties'], x={}, y={})}}
        with self.assertRaisesRegex(RuntimeError, 'nộp bài'):
            action_arguments({'tool': 'click', 'arguments': {'x': 100, 'y': 450}},
                             snapshot, tool, {'pid': 11, 'window_id': 12})

    def test_capture_refusal_during_scroll_settling_requires_fresh_observation_before_model(self):
        mcp, client, trace = SemanticQuizMCP(answered=True), Mock(), Mock()
        events, original = [], mcp.call
        def call(name, args):
            events.append(name)
            result = original(name, args)
            if name == 'get_window_state' and mcp.snapshots == 3:
                events.append('capture_refused')
                return {'isError': True, 'structuredContent': {
                    'effect': 'refused', 'error': {'code': 'capture_action_refused'}}}
            return result
        mcp.call = call
        def done(*args, **kwargs):
            events.append('model')
            return '{"done":true,"summary":"Câu 1: B"}', 'test'
        client.ask.side_effect = done
        self.assertEqual(DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                         trace=trace).run(11, 'Quiz', 12), 'Câu 1: B')
        refusal = events.index('capture_refused')
        self.assertEqual(events[refusal + 1], 'get_window_state')
        self.assertEqual(client.ask.call_count, 2)
        self.assertGreaterEqual(mcp.snapshots, 4)
        self.assertEqual([name for name, _ in mcp.calls if name in ('click', 'scroll', 'press_key')], ['scroll'])
        self.assertTrue(any(call.kwargs.get('error_code') == 'capture_action_refused' for call in trace.call_args_list))

    def test_visible_unanswered_question_rejects_premature_done_then_finishes_after_scroll_probe(self):
        mcp, trace, client = SemanticQuizMCP(), Mock(), Mock()
        decisions = iter([
            {'done': True, 'summary': 'Premature'},
            {'tool': 'click', 'arguments': {'element_token': 'answer:1'}}])
        client.ask.side_effect = lambda *a, **k: (
            json.dumps(next(decisions, {'done': True, 'summary': 'Câu 1: B'})), 'test')
        result = DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                              trace=trace).run(11, 'Quiz', 12)
        self.assertEqual(result, 'Câu 1: B')
        self.assertTrue(mcp.answered)
        actions = [(name, args) for name, args in mcp.calls if name in ('click', 'scroll', 'press_key')]
        self.assertEqual([args['element_token'] for name, args in actions if name == 'click'], ['answer:1'])
        self.assertTrue(any(name == 'scroll' for name, _ in actions))
        self.assertTrue(any(call.kwargs.get('error_code') == 'premature_done' for call in trace.call_args_list))
        self.assertGreaterEqual(mcp.snapshots, 4)
        for _, args in actions:
            self.assertEqual(args['target'], {'kind': 'window', 'pid': 11, 'window_id': 12})

    def test_actionable_next_page_rejects_premature_done_without_radio_groups(self):
        mcp, client, trace = SemanticQuizMCP(next_page=True), Mock(), Mock()
        mcp.radio_page = False
        client.ask.side_effect = [('{"done":true}', 'test'),
            ('{"tool":"click","arguments":{"element_token":"next"}}', 'test'),
            ('{"done":true,"summary":"Hoàn tất"}', 'test')]
        self.assertEqual(DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                         trace=trace).run(11, 'Quiz', 12), 'Hoàn tất')
        self.assertFalse(mcp.next_page)
        self.assertEqual([args['element_token'] for name, args in mcp.calls if name == 'click'], ['next'])
        self.assertTrue(any(call.kwargs.get('error_code') == 'premature_done' for call in trace.call_args_list))

    def test_already_answered_interactive_page_requires_scroll_before_done(self):
        mcp = SemanticQuizMCP(answered=True)
        client = Mock(ask=Mock(return_value=('{"done":true,"summary":"Câu 1: B"}', 'test')))
        self.assertEqual(DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(
            11, 'Quiz', 12), 'Câu 1: B')
        names = [name for name, _ in mcp.calls]
        self.assertIn('scroll', names)
        self.assertGreaterEqual(mcp.snapshots, 2)
        self.assertNotIn('click', names)

    def test_repeated_selected_answer_never_dispatches_click_and_has_bounded_recovery(self):
        mcp, trace = SemanticQuizMCP(answered=True), Mock()
        client = Mock(ask=Mock(return_value=(
            '{"tool":"click","arguments":{"element_token":"answer:1"}}', 'test')))
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                         trace=trace).run(11, 'Quiz', 12)
        self.assertEqual(caught.exception.code, 'already_selected')
        self.assertEqual(client.ask.call_count, 3)
        self.assertNotIn('click', [name for name, _ in mcp.calls])
        self.assertIn('already_selected', client.ask.call_args_list[1].args[0][0]['text'])
        self.assertTrue(any(call.kwargs.get('error_code') == 'already_selected' for call in trace.call_args_list))

    def test_transient_capture_refusal_reobserves_before_model_and_never_replays_input(self):
        mcp, client, trace = SemanticQuizMCP(), Mock(), Mock()
        mcp.radio_page = False
        mcp.observation_refusals = [{'isError': True, 'structuredContent': {
            'effect': 'refused', 'error': {'code': 'capture_action_refused'}}}]
        client.ask.return_value = ('{"done":true,"summary":"Đã đọc"}', 'test')
        self.assertEqual(DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp,
                         trace=trace).run(11, 'Quiz', 12), 'Đã đọc')
        self.assertEqual(client.ask.call_count, 1)
        self.assertEqual(mcp.snapshots, 2)
        self.assertFalse(any(name in ('click', 'scroll', 'press_key') for name, _ in mcp.calls))
        self.assertTrue(any(call.kwargs.get('error_code') == 'capture_action_refused' for call in trace.call_args_list))

    def test_persistent_capture_refusal_is_bounded_before_model_or_input(self):
        mcp, client = SemanticQuizMCP(), Mock()
        mcp.observation_refusals = [{'isError': True, 'structuredContent': {
            'effect': 'refused', 'error': {'code': 'capture_action_refused'}}} for _ in range(10)]
        with self.assertRaises(RuntimeError) as caught:
            DesktopAgent('.', client, threading.Event(), Mock(), lambda *a: mcp).run(11, 'Quiz', 12)
        self.assertEqual(caught.exception.code, 'capture_action_refused')
        self.assertEqual(mcp.snapshots, 3)
        client.ask.assert_not_called()
        self.assertFalse(any(name in ('click', 'scroll', 'press_key') for name, _ in mcp.calls))

    def test_wait_for_minimized_window_preserves_hwnd_and_never_sends_input(self):
        mcp, cancel = SemanticQuizMCP(), threading.Event()
        mcp.observation_refusals = [{'isError': True, 'structuredContent': {
            'effect': 'refused', 'error': {'code': 'window_minimized'}}}]
        agent = DesktopAgent('.', Mock(), cancel, Mock(), lambda *a: mcp)
        agent.wait_for_window(mcp, {'pid': 11, 'window_id': 12})
        self.assertGreaterEqual(mcp.snapshots, 2)
        self.assertTrue(all(name == 'get_window_state' for name, _ in mcp.calls))
        for _, args in mcp.calls:
            self.assertEqual(args['pid'], 11)
            self.assertEqual(args['window_id'], 12)

    def test_wait_for_minimized_window_obeys_cancellation_without_retargeting(self):
        mcp, cancel = SemanticQuizMCP(), threading.Event()
        original = mcp.call
        def call(name, args):
            result = original(name, args)
            cancel.set()
            return result
        mcp.call = call
        mcp.observation_refusals = [{'isError': True, 'structuredContent': {
            'effect': 'refused', 'error': {'code': 'window_minimized'}}}]
        agent = DesktopAgent('.', Mock(), cancel, Mock(), lambda *a: mcp)
        with self.assertRaises(InterruptedError):
            agent.wait_for_window(mcp, {'pid': 11, 'window_id': 12})
        self.assertEqual([name for name, _ in mcp.calls], ['get_window_state'])

    def test_mcp_ignores_notification_but_rejects_rpc_errors(self):
        mcp = CuaMCP('.', threading.Event())
        mcp.send = Mock()
        mcp.events.put({'method': 'notifications/message', 'params': {}})
        mcp.events.put({'id': 1, 'result': {'tools': []}})
        self.assertEqual(mcp.request('tools/list', {}), {'tools': []})
        mcp.events.put({'id': 2, 'error': {'message': 'private driver body'}})
        with self.assertRaisesRegex(RuntimeError, '^Cua Driver MCP từ chối yêu cầu$'):
            mcp.request('tools/call', {})
