import json
import threading
import unittest
from unittest.mock import patch
from api_zoo import apply_config
from cloud_client import CloudClient
from deepseek_client import VisionError


class ImageReaderTests(unittest.TestCase):
    def client(self):
        config = {}
        profiles = [dict(id='ds', name='DeepSeek', provider='deepseek', base_url='https://api.deepseek.com',
                         api_key='synthetic', model='deepseek-flash', vision_model='deepseek-flash'),
                    dict(id='mirai', name='Mirai', provider='compatible', base_url='https://example.com/v1',
                         api_key='synthetic', model='claude-text', vision_model='claude-image')]
        apply_config(config, dict(profiles=profiles, primary='mirai', auto=True), select_primary=True)
        config['API_ZOO']['profiles'][1]['question_reader'] = {'api': 'ds', 'model': 'deepseek-flash'}
        client = CloudClient(config)
        client.cancel_event = threading.Event()
        return client

    def image(self):
        return [{'type': 'text', 'text': 'Giải câu này'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA=='}}]

    def test_deepseek_reads_then_selected_text_model_solves_without_images(self):
        client = self.client()
        calls = []
        def ask(instance, text, history=None, instruction=None, json_output=False):
            calls.append((instance.config['SELECTED_MODEL'], text, history, instruction))
            return (json.dumps({'text': 'Câu 1: print(2 + 3)', 'missing': []}), 'DeepSeek') if len(calls) == 1 else ('5', 'Mirai')
        with patch.object(CloudClient, 'ask', ask):
            self.assertEqual(client.ask_question(self.image(), [{'role': 'user', 'content': 'old'}], 'Solve'), ('5', 'Mirai'))
        self.assertEqual(calls[0][0], 'zoo:ds')
        self.assertIsInstance(calls[0][1], list)
        self.assertEqual(calls[1][0], 'zoo:mirai')
        self.assertIsInstance(calls[1][1], str)
        self.assertIn('print(2 + 3)', calls[1][1])
        self.assertEqual(calls[1][3], 'Solve')
        self.assertEqual(client.config['SELECTED_MODEL'], 'zoo:mirai')

    def test_unreadable_or_malformed_image_never_reaches_solver(self):
        for answer in ('not json', json.dumps({'text': 'partial', 'missing': ['code bị cắt']})):
            with self.subTest(answer=answer):
                client = self.client()
                with patch.object(CloudClient, 'ask', return_value=(answer, 'DeepSeek')) as ask:
                    with self.assertRaises(VisionError):
                        client.ask_question(self.image())
                    self.assertEqual(ask.call_count, 1)

    def test_text_and_selected_deepseek_do_not_add_an_extra_request(self):
        client = self.client()
        with patch.object(CloudClient, 'ask', return_value=('OK', 'model')) as ask:
            client.ask_question('text')
            ask.assert_called_once_with('text', None, instruction=None)
        client.config['SELECTED_MODEL'] = 'zoo:ds'
        with patch.object(CloudClient, 'ask', return_value=('OK', 'model')) as ask:
            client.ask_question(self.image())
            self.assertEqual(ask.call_count, 1)

    def test_missing_reader_does_not_send_images_to_mirai(self):
        client = self.client()
        client.config['API_ZOO']['profiles'][0]['enabled'] = False
        with patch.object(CloudClient, 'ask') as ask:
            with self.assertRaises(VisionError):
                client.ask_question(self.image())
            ask.assert_not_called()

    def test_default_uses_current_api_image_model_without_deepseek(self):
        client = self.client()
        client.config['API_ZOO']['profiles'] = [client.config['API_ZOO']['profiles'][1]]
        client.config['API_ZOO']['profiles'][0].pop('question_reader')
        with patch.object(CloudClient, 'ask', return_value=('OK', 'model')) as ask:
            client.ask_question(self.image())
            ask.assert_called_once_with(self.image(), None, instruction=None)

    def test_any_api_can_read_using_its_own_credentials_and_custom_model(self):
        client = self.client()
        reader = client.config['API_ZOO']['profiles'][0]
        reader.update(provider='compatible', base_url='https://other.example/v1', api_key='other-key')
        client.config['API_ZOO']['profiles'][1]['question_reader']['model'] = 'custom-vision'
        calls = []
        def ask(instance, text, history=None, instruction=None, json_output=False):
            p = instance.config['API_ZOO']['profiles'][0]
            calls.append((p['api_key'], p['vision_model']))
            return (json.dumps({'text': 'Hello', 'missing': []}), 'reader') if len(calls) == 1 else ('OK', 'solver')
        with patch.object(CloudClient, 'ask', ask):
            client.ask_question(self.image())
        self.assertEqual(calls[0], ('other-key', 'custom-vision'))

    def test_reader_roundtrip_and_picker_support_all_apis(self):
        from api_zoo import validate
        from api_zoo_ui import image_options
        data = validate(self.client().config['API_ZOO'])
        self.assertEqual(data['profiles'][1]['question_reader'], {'api': 'ds', 'model': 'deepseek-flash'})
        options = image_options(data['profiles'])
        self.assertIn({'api': 'ds', 'model': 'deepseek-flash'}, options.values())
        self.assertIn({'api': 'mirai', 'model': 'claude-image'}, options.values())
