"""Session/cursor diagnostics without launching a driver or Windows UI."""
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock, patch

import agent_cursor_overlay
from cua_mcp import CuaMCP


class AgentCursorTests(unittest.TestCase):
    def connection(self):
        mcp = CuaMCP('.', threading.Event())
        for name in ('start_session', 'set_agent_cursor_enabled', 'get_agent_cursor_state', 'click', 'scroll',
                     'type_text', 'press_key'):
            mcp.tools[name] = {'name': name, 'inputSchema': {'properties': {'session': {}}}}
        mcp.tools['get_window_state'] = {'name': 'get_window_state', 'inputSchema': {
            'properties': {'session': {}, 'max_image_dimension': {}}}}
        return mcp

    def snapshot(self):
        return {'capture_id': 'current-image', 'screenshot_width': 800,
            'screenshot_height': 600, 'screenshot_frame_valid': True,
            'window_bounds': {'x': 100, 'y': 50, 'width': 802, 'height': 602},
            'elements': [
                {'element_token': 'document', 'role': 'Document',
                 'frame': {'x': 101, 'y': 51, 'w': 800, 'h': 600},
                 'screenshot_frame': {'x': 0, 'y': 0, 'w': 800, 'h': 600}},
                {'element_token': 'answer', 'role': 'RadioButton',
                 'frame': {'x': 201, 'y': 251, 'w': 100, 'h': 30},
                 'screenshot_frame': {'x': 100, 'y': 200, 'w': 100, 'h': 30}}]}

    def bind_snapshot(self, mcp, data=None):
        mcp.cursor_snapshot = self.snapshot() if data is None else data
        mcp.cursor_target = (11, 12)
        return {'target': {'kind': 'window', 'pid': 11, 'window_id': 12},
                'capture_id': 'current-image', 'x': 10, 'y': 20}

    def test_unique_session_is_shared_by_setup_and_actions_without_mutating_caller_arguments(self):
        mcp = self.connection()
        self.assertNotEqual(mcp.cursor_session, self.connection().cursor_session)
        def respond(method, params, **kwargs):
            self.assertEqual(method, 'tools/call')
            if params['name'] in ('set_agent_cursor_enabled', 'get_agent_cursor_state'):
                return {'structuredContent': {'enabled': False, 'visible': False}}
            return {'structuredContent': {'effect': 'confirmed'}}
        mcp.request = Mock(side_effect=respond)
        renderer = Mock(available=False)
        with patch('cua_mcp.os.name', 'nt'), patch.object(
                agent_cursor_overlay, 'NativeArrowOverlay', return_value=renderer):
            mcp._configure_cursor()
        for name in ('click', 'scroll', 'type_text', 'press_key'):
            arguments = {'target': {'kind': 'window', 'pid': 11, 'window_id': 12}}
            original = deepcopy(arguments)
            mcp.call(name, arguments)
            self.assertEqual(arguments, original)
        requests = [call.args[1] for call in mcp.request.call_args_list]
        self.assertEqual([item['name'] for item in requests], ['start_session',
            'set_agent_cursor_enabled', 'get_agent_cursor_state', 'click', 'scroll', 'type_text', 'press_key'])
        self.assertTrue(all(item['arguments']['session'] == mcp.cursor_session for item in requests))
        self.assertIs(requests[1]['arguments']['enabled'], False)
        self.assertEqual(mcp.cursor_style, 'hidden')
        renderer.close.assert_called_once()

    def test_observation_uses_native_bitmap_without_mutating_caller_resize_option(self):
        mcp = self.connection()
        data = self.snapshot()
        result = {'structuredContent': data}
        mcp.request = Mock(return_value=result)
        arguments = {'pid': 11, 'window_id': 12, 'max_image_dimension': 1024}
        self.assertIs(mcp.call('get_window_state', arguments), result)
        sent = mcp.request.call_args.args[1]['arguments']
        self.assertEqual(sent['max_image_dimension'], 0)
        self.assertEqual(arguments['max_image_dimension'], 1024)
        self.assertEqual(mcp.cursor_target, (11, 12))
        self.assertIs(mcp.cursor_snapshot, data)

    def test_pixel_mapping_uses_paired_frame_crop_instead_of_dwm_window_border(self):
        mcp = self.connection()
        arguments = self.bind_snapshot(mcp)
        self.assertEqual(mcp._cursor_point(arguments), (111, 71))
        # Token frames already use desktop coordinates; they need no scaling.
        self.assertEqual(mcp._cursor_point(dict(arguments, element_token='answer')), (251, 266))

    def test_scaled_mapping_supports_monitor_with_negative_desktop_coordinates(self):
        mcp, data = self.connection(), self.snapshot()
        data['window_bounds'] = {'x': -1890, 'y': -170, 'width': 1220, 'height': 920}
        data['elements'][0]['frame'] = {'x': -1880, 'y': -160, 'w': 1200, 'h': 900}
        data['elements'][1]['frame'] = {'x': -1730, 'y': 140, 'w': 150, 'h': 45}
        arguments = self.bind_snapshot(mcp, data)
        arguments.update(x=200, y=100)
        self.assertEqual(mcp._cursor_point(arguments), (-1580, -10))

    def test_foreign_target_or_capture_has_no_cursor_display_and_no_extra_rpc(self):
        for mismatch in ('pid', 'window_id', 'capture_id'):
            with self.subTest(mismatch=mismatch):
                mcp = self.connection()
                arguments = self.bind_snapshot(mcp)
                if mismatch == 'capture_id':
                    arguments['capture_id'] = 'old-image'
                else:
                    arguments['target'][mismatch] += 1
                renderer = mcp.arrow_overlay = Mock()
                result = {'structuredContent': {'effect': 'confirmed'}}
                mcp.request = Mock(return_value=result)
                self.assertIs(mcp.call('click', arguments), result)
                renderer.update.assert_not_called()
                renderer.hide.assert_called_once()
                mcp.request.assert_called_once()

    def test_invalid_or_inconsistent_mapping_does_not_guess_cursor_position(self):
        for invalid in ('invalid_flag', 'missing_pairs', 'inconsistent_pair', 'outside', 'nan', 'boolean'):
            with self.subTest(invalid=invalid):
                mcp, data = self.connection(), self.snapshot()
                arguments = self.bind_snapshot(mcp, data)
                if invalid == 'invalid_flag':
                    data['screenshot_frame_valid'] = False
                elif invalid == 'missing_pairs':
                    data['elements'] = []
                elif invalid == 'inconsistent_pair':
                    data['elements'][1]['frame']['x'] += 50
                elif invalid == 'outside':
                    arguments['x'] = data['screenshot_width']
                elif invalid == 'nan':
                    arguments['x'] = float('nan')
                elif invalid == 'boolean':
                    arguments['x'] = True
                self.assertIsNone(mcp._cursor_point(arguments))

    def test_native_render_failure_does_not_fail_or_replay_successful_mcp_action(self):
        for failure in (RuntimeError('render'), ValueError('render'), TypeError('render')):
            with self.subTest(error=type(failure).__name__):
                mcp = self.connection()
                arguments = self.bind_snapshot(mcp)
                renderer = mcp.arrow_overlay = Mock()
                renderer.update.side_effect = failure
                result = {'structuredContent': {'effect': 'confirmed'}}
                mcp.request = Mock(return_value=result)
                self.assertIs(mcp.call('click', arguments), result)
                mcp.request.assert_called_once()
                self.assertEqual(mcp.request.call_args.args[1]['name'], 'click')
                renderer.close.assert_called_once()
                self.assertIsNone(mcp.arrow_overlay)
                self.assertEqual(mcp.cursor_style, 'hidden')

    def test_renderer_unavailable_or_constructor_failure_keeps_cua_effects_disabled(self):
        for constructor_failure in (False, True):
            with self.subTest(constructor_failure=constructor_failure):
                mcp = self.connection()
                mcp.request = Mock(side_effect=[{}, {'structuredContent': {'enabled': False}},
                                               {'structuredContent': {'enabled': False, 'visible': False}}])
                renderer = Mock(available=False)
                factory = Mock(side_effect=ValueError('native unavailable')) if constructor_failure else Mock(return_value=renderer)
                with patch('cua_mcp.os.name', 'nt'), patch.object(
                        agent_cursor_overlay, 'NativeArrowOverlay', factory):
                    mcp._configure_cursor()
                self.assertEqual(mcp.cursor_style, 'hidden')
                self.assertIsNone(mcp.arrow_overlay)
                self.assertEqual(mcp.request.call_count, 3)
                disabled = mcp.request.call_args_list[1].args[1]
                self.assertEqual(disabled['name'], 'set_agent_cursor_enabled')
                self.assertEqual(disabled['arguments'], {'session': mcp.cursor_session, 'enabled': False})

    def test_native_renderer_is_created_only_after_pending_cua_state_acknowledges_disabled(self):
        mcp, events = self.connection(), []
        states = iter([{'enabled': True, 'visible': True}, {'enabled': False, 'visible': False}])
        def request(method, params, **kwargs):
            events.append(params['name'])
            self.assertEqual(params['arguments']['session'], mcp.cursor_session)
            if params['name'] == 'set_agent_cursor_enabled':
                # Setter acceptance is not proof that its renderer queue ran.
                return {'structuredContent': {'enabled': False}}
            if params['name'] == 'get_agent_cursor_state':
                self.assertGreater(kwargs['timeout'], 0)
                self.assertLessEqual(kwargs['timeout'], 2)
                return {'structuredContent': next(states)}
            return {}
        mcp.request = Mock(side_effect=request)
        renderer = Mock(available=True)
        def create_renderer():
            events.append('native-renderer')
            return renderer
        with patch('cua_mcp.os.name', 'nt'), patch.object(mcp.cancel, 'wait', return_value=False), \
                patch.object(agent_cursor_overlay, 'NativeArrowOverlay', side_effect=create_renderer):
            mcp._configure_cursor()
        self.assertEqual(events, ['start_session', 'set_agent_cursor_enabled',
                                  'get_agent_cursor_state', 'get_agent_cursor_state', 'native-renderer'])
        self.assertIs(mcp.arrow_overlay, renderer)
        self.assertEqual(mcp.cursor_style, 'windows-arrow')
        self.assertFalse(any(call.args[1]['name'] in ('click', 'scroll', 'type_text', 'press_key')
                             for call in mcp.request.call_args_list))

    def test_cancellation_during_pending_cursor_ack_stops_before_renderer_or_input(self):
        mcp = self.connection()
        mcp.request = Mock(side_effect=[{}, {'structuredContent': {'enabled': False}},
                                       {'structuredContent': {'enabled': True, 'visible': True}}])
        def cancel_during_wait(*args, **kwargs):
            mcp.cancel.set()
            return True
        with patch('cua_mcp.os.name', 'nt'), patch.object(mcp.cancel, 'wait', side_effect=cancel_during_wait), \
                patch.object(agent_cursor_overlay, 'NativeArrowOverlay') as factory:
            with self.assertRaises(InterruptedError):
                mcp._configure_cursor()
        factory.assert_not_called()
        self.assertIsNone(mcp.arrow_overlay)
        self.assertEqual([call.args[1]['name'] for call in mcp.request.call_args_list],
                         ['start_session', 'set_agent_cursor_enabled', 'get_agent_cursor_state'])

    def test_session_setup_cancellation_propagates_before_native_renderer_is_created(self):
        for stage in ('start', 'disable'):
            with self.subTest(stage=stage):
                mcp = self.connection()
                error = InterruptedError('cancelled')
                mcp.request = Mock(side_effect=[error] if stage == 'start' else [{}, error])
                with patch('cua_mcp.os.name', 'nt'), patch.object(
                        agent_cursor_overlay, 'NativeArrowOverlay') as factory:
                    with self.assertRaises(InterruptedError):
                        mcp._configure_cursor()
                factory.assert_not_called()
                self.assertIsNone(mcp.arrow_overlay)

    def test_renderer_close_failure_still_closes_driver_pipes_and_waits_for_exit(self):
        mcp = self.connection()
        renderer = mcp.arrow_overlay = Mock()
        renderer.close.side_effect = ValueError('render cleanup')
        process = mcp.process = Mock()
        process.wait.return_value = 0
        mcp.close()
        renderer.close.assert_called_once()
        self.assertIsNone(mcp.arrow_overlay)
        process.stdin.close.assert_called_once()
        process.wait.assert_called_once_with(timeout=1)
        process.stdout.close.assert_called_once()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
