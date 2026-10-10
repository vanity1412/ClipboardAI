import copy
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from api_zoo import validate, apply_config, ZooStore
from cloud_client import CloudClient
from model_reasoning import apply_effort, effort_for, select_model
from browser_provider import BrowserSession


def catalog():
    return validate({'profiles': [dict(id='test', name='test', base_url='https://example.com/v1',
        api_key='synthetic', model='text', vision_model='image',
        models=[{'id': 'text', 'vision': False}, {'id': 'image', 'vision': True}])], 'primary': 'test'})


class ReasoningTests(unittest.TestCase):
    def test_old_config_defaults_to_medium_without_mutation(self):
        raw = catalog()
        raw['profiles'][0].pop('reasoning_effort')
        old = copy.deepcopy(raw)
        self.assertEqual(validate(raw)['profiles'][0]['reasoning_effort'], 'medium')
        self.assertEqual(raw, old)

    def test_invalid_efforts_are_rejected_before_save(self):
        for value in ('ultra-unknown', None, [], 8):
            data = catalog()
            data['profiles'][0]['reasoning_effort'] = value
            with self.assertRaises(ValueError):
                validate(data)

    def test_picker_keeps_text_and_vision_separate_and_persists_effort(self):
        data = catalog()
        updated = select_model(data, 'test', 'image', 'high', vision=True)
        self.assertEqual(updated['profiles'][0]['model'], 'text')
        self.assertEqual(effort_for(updated['profiles'][0], 'image'), 'high')
        self.assertEqual(effort_for(updated['profiles'][0], 'text'), 'medium')
        self.assertEqual(data['profiles'][0]['model_efforts'], {})
        with tempfile.TemporaryDirectory() as root:
            store = ZooStore(root)
            store.save(updated)
            self.assertEqual(effort_for(store.load()['profiles'][0], 'image'), 'high')

    def test_picker_rejects_text_only_vision_and_unknown_model(self):
        for model, vision in (('text', True), ('missing', False)):
            with self.assertRaises(ValueError):
                select_model(catalog(), 'test', model, 'medium', vision)

    def test_protocols_send_correct_fields_and_default_omits_them(self):
        for protocol, field in (('compatible', 'reasoning_effort'), ('responses', 'reasoning'), ('anthropic', 'output_config')):
            body = apply_effort({}, protocol, 'model', 'medium')
            self.assertEqual(body[field], 'medium' if protocol == 'compatible' else {'effort': 'medium'})
            self.assertEqual(apply_effort({}, protocol, 'model', 'default'), {})

    def test_real_client_routes_image_model_and_its_effort(self):
        data = select_model(catalog(), 'test', 'image', 'high', True)
        config = {}
        apply_config(config, data, select_primary=True)
        client = CloudClient(config)
        client.cancel_event = threading.Event()
        client.post = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': 'done'}}]})
        client.ask([{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA=='}}], json_output=True)
        body = client.post.call_args.args[1]
        self.assertEqual(body['model'], 'image')
        self.assertEqual(body['reasoning_effort'], 'high')
        client.ask('question')
        self.assertEqual(client.post.call_args.args[1]['reasoning_effort'], 'medium')

    def test_browser_turn_sends_explicit_effort_or_omits_default(self):
        for effort in ('medium', 'high', 'default'):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as root:
                session = BrowserSession({'id': 'test'}, root)
                session.temporary = Mock(name='temporary')
                session.request = Mock(side_effect=[{'account': {'type': 'chatgpt'}}, {'thread': {'id': 'thread'}}, {'turn': {'id': 'turn'}}])
                session.events.put({'method': 'item/completed', 'params': {'threadId': 'thread', 'turnId': 'turn', 'item': {'type': 'agentMessage', 'id': 'answer', 'phase': 'final_answer', 'text': 'done'}}})
                session.events.put({'method': 'turn/completed', 'params': {'threadId': 'thread', 'turnId': 'turn', 'turn': {'id': 'turn', 'status': 'completed'}}})
                self.assertEqual(session.ask('model', [{'role': 'user', 'content': 'question'}], 10, effort=effort), 'done')
                params = session.request.call_args.args[1]
                if effort == 'default':
                    self.assertNotIn('effort', params)
                else:
                    self.assertEqual(params['effort'], effort)

    def test_native_picker_saves_selected_image_effort(self):
        from windows_native import WindowsApp
        app = WindowsApp.__new__(WindowsApp)
        app.busy = False
        app.enabled = True
        app.config = {}
        apply_config(app.config, catalog(), select_primary=True)
        app.user = Mock()
        app.user.CreatePopupMenu.side_effect = range(1, 100)
        app.client = Mock()
        app.set_text, app.tooltip, app.refresh_panel, app.model_name = Mock(), Mock(), Mock(), Mock(return_value='test')
        def choose(*args):
            high = [c.args[2] for c in app.user.AppendMenuW.call_args_list if c.args[-1] == 'high']
            return high[-1]  # Image model, high effort.
        app.track_popup_menu = Mock(side_effect=choose)
        with tempfile.TemporaryDirectory() as root, patch('windows_native.ROOT', root):
            app.quick_model_menu()
            stored = ZooStore(root).load()['profiles'][0]
        self.assertEqual(stored['model'], 'text')
        self.assertEqual(stored['vision_model'], 'image')
        self.assertEqual(effort_for(stored, 'image'), 'high')
        self.assertEqual(effort_for(app.config['API_ZOO']['profiles'][0], 'image'), 'high')
        app.user.DestroyMenu.assert_called_once()

    def test_codex_metadata_restricts_picker_effort(self):
        from model_reasoning import choices
        data = catalog()
        data['profiles'][0]['models'][0]['reasoning_efforts'] = ['low', 'medium']
        self.assertEqual(choices(validate(data)['profiles'][0], 'text'), ('default', 'low', 'medium'))
