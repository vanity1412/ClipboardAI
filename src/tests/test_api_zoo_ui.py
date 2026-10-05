"""Discovery results must not undo choices made while the API is responding."""
import unittest
import tempfile
from unittest.mock import patch

from api_zoo import ZooStore, validate
from api_zoo_ui import catalog_result, model_choice


def profile(**changes):
    return dict(dict(id='selected', name='Synthetic API', base_url='https://example.test/v1',
                     api_key='synthetic-key', provider='compatible', model='first',
                     vision_model='reader', models=[]), **changes)


class CatalogChoiceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [{'id': 'first', 'vision': False}, {'id': 'second', 'vision': False},
                        {'id': 'reader', 'vision': True}, {'id': 'other-reader', 'vision': True}]
        self.event = ('models', 7, profile(), self.catalog)

    def result(self, model='first', vision='reader', **options):
        return catalog_result(self.event, 7, 'selected',
                              {'model': model, 'vision_model': vision}, **options)[0]

    def test_delayed_discovery_keeps_newly_selected_model_and_vision(self):
        result = self.result('second', 'other-reader')
        self.assertEqual((result['model'], result['vision_model']), ('second', 'other-reader'))
        self.assertEqual(result['models'], self.catalog)
        self.assertEqual(self.event[2]['model'], 'first')

    def test_manually_entered_models_need_not_appear_in_discovery(self):
        result = self.result('private-model', 'private-reader')
        self.assertEqual((result['model'], result['vision_model']), ('private-model', 'private-reader'))

    def test_clearing_vision_during_discovery_does_not_reenable_images(self):
        result = self.result(vision='', edited_choices={'vision_model'})
        self.assertEqual(result['vision_model'], '')

    def test_existing_profile_with_images_disabled_stays_disabled(self):
        self.event = ('models', 7, dict(profile(), vision_model=''), self.catalog)
        result = self.result(vision='', preserve_empty_vision=True)
        self.assertEqual(result['vision_model'], '')

    def test_clearing_model_during_discovery_stays_empty_for_save_validation(self):
        result = self.result(model='', edited_choices={'model'})
        self.assertEqual(result['model'], '')

    def test_new_unedited_form_can_still_get_initial_defaults(self):
        self.event = ('models', 7, dict(profile(), model='', vision_model=''), self.catalog)
        result = self.result(model='', vision='')
        self.assertEqual((result['model'], result['vision_model']), ('first', 'reader'))

    def test_stale_request_cannot_change_current_form(self):
        with patch('api_zoo_ui.update_catalog') as update:
            result = catalog_result(self.event, 8, 'selected', {'model': 'new', 'vision_model': ''})
        self.assertIsNone(result)
        update.assert_not_called()

    def test_result_for_another_profile_cannot_change_current_form(self):
        with patch('api_zoo_ui.update_catalog') as update:
            result = catalog_result(self.event, 7, 'other-api', {'model': 'new', 'vision_model': ''})
        self.assertIsNone(result)
        update.assert_not_called()

    def test_confirmed_text_only_model_does_not_override_validated_image_choice(self):
        validated = dict(profile(), models=self.catalog, vision_model='reader')
        with patch('api_zoo_ui.update_catalog', return_value=validated):
            result = self.result(vision='second')
        self.assertEqual(result['vision_model'], 'reader')

    def test_invalid_buffer_does_not_poison_metadata_or_another_profile_save(self):
        stored, displayed = catalog_result(self.event, 7, 'selected',
            {'model': 'unfinished model', 'vision_model': 'unfinished image model'})
        self.assertEqual(displayed, {'model': 'unfinished model', 'vision_model': 'unfinished image model'})
        self.assertEqual((stored['model'], stored['vision_model']), ('first', 'reader'))
        other = profile(id='other-api', name='Other API', api_key='different-synthetic-key', model='other-model')
        data = validate({'profiles': [stored, other], 'primary': other['id']})
        with tempfile.TemporaryDirectory() as folder:
            ZooStore(folder).save(data)
            saved = ZooStore(folder).load()
        self.assertEqual(saved['primary'], 'other-api')
        self.assertEqual(next(p for p in saved['profiles'] if p['id'] == 'other-api')['model'], 'other-model')

    def test_overlong_buffer_stays_visible_without_entering_metadata(self):
        stored, displayed = catalog_result(self.event, 7, 'selected',
            {'model': 'm' * 201, 'vision_model': 'v' * 201})
        self.assertEqual((stored['model'], stored['vision_model']), ('first', 'reader'))
        self.assertEqual(len(displayed['model']), 201)
        self.assertEqual(len(displayed['vision_model']), 201)

    def test_save_validation_identifies_invalid_field(self):
        for label in ('Model trả lời', 'Model đọc ảnh'):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, label + ' không hợp lệ'):
                model_choice('unfinished model', label)


if __name__ == '__main__':
    unittest.main()
