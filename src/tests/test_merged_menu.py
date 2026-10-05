import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from chat_modes import CHAT, ANALYSIS, CODING, reply_config, supports_reasoning_control
from prompt_profiles import merged_preferences, prompt_preferences, instruction_for
from runtime_settings import save_preferences, load_runtime_settings, validated_preferences
from tests import test_selected_fixes as fixtures


class MergedMenuTests(unittest.TestCase):
    def test_active_analysis_prompt_wins_and_both_originals_survive_restart(self):
        old = {'PROMPT_MODES': {'3': 'custom', '4': 'custom'},
               'PROMPT_CUSTOM': {'3': 'OLD CHAT', '4': 'OLD ANALYSIS'}}
        values = merged_preferences(old, ANALYSIS)
        new = dict(old, **values)
        self.assertEqual(instruction_for(CHAT, new), 'OLD ANALYSIS')
        self.assertEqual(instruction_for(ANALYSIS, new), 'OLD ANALYSIS')
        self.assertEqual({p['text'] for p in new['SAVED_PROMPTS']}, {'OLD CHAT', 'OLD ANALYSIS'})
        self.assertEqual(reply_config(new, CHAT)['REPLY_MODE'], ANALYSIS)
        self.assertEqual(merged_preferences(new, ANALYSIS), {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preferences.json'
            save_preferences(path, new)
            restored = {}
            self.assertEqual(load_runtime_settings(restored, path), [])
            self.assertEqual(instruction_for(CHAT, restored), 'OLD ANALYSIS')
            self.assertEqual(restored['SAVED_PROMPTS'], new['SAVED_PROMPTS'])

    def test_fast_and_careful_never_change_prompt_and_code_has_independent_setting(self):
        config = merged_preferences({}, CHAT)
        config.update(prompt_preferences(CHAT, 'custom', 'ONLY ANSWERS', config))
        for value, api_mode in (('fast', CHAT), ('careful', ANALYSIS)):
            config['REASONING_MODES']['3'] = value
            self.assertEqual(instruction_for(CHAT, config), 'ONLY ANSWERS')
            self.assertEqual(reply_config(config, CHAT)['REPLY_MODE'], api_mode)
            self.assertEqual(reply_config(config, CODING)['REPLY_MODE'], CODING)
        config['REASONING_MODES']['0'] = 'fast'
        self.assertEqual(reply_config(config, CODING)['REPLY_MODE'], CHAT)

    def test_saved_prompt_selection_keeps_other_prompt_and_does_not_duplicate_library(self):
        config = merged_preferences({'PROMPT_MODES': {'4': 'custom'},
                                     'PROMPT_CUSTOM': {'4': 'KEEP ME'}}, CHAT)
        config.update(prompt_preferences(CODING, 'custom', 'CODE ONLY', config))
        count = len(config['SAVED_PROMPTS'])
        config.update(prompt_preferences(CHAT, 'custom', 'KEEP ME', config))
        self.assertEqual(len(config['SAVED_PROMPTS']), count)
        self.assertEqual(instruction_for(CHAT, config), 'KEEP ME')
        self.assertEqual(instruction_for(CODING, config), 'CODE ONLY')

    def test_dormant_custom_prompt_is_not_lost(self):
        old = {'PROMPT_MODES': {'3': 'free'}, 'PROMPT_CUSTOM': {'3': 'DORMANT'}}
        new = dict(old, **merged_preferences(old, CHAT))
        self.assertIn('DORMANT', [p['text'] for p in new['SAVED_PROMPTS']])
        self.assertEqual(instruction_for(CHAT, new), '')

    def test_invalid_library_and_reasoning_preferences_are_rejected(self):
        for data in ({'REASONING_MODES': {'3': 'guess'}}, {'REPLY_MENU_VERSION': True},
                     {'SAVED_PROMPTS': [{'id': 'a', 'name': 'b', 'style': 'custom', 'text': ''}]}):
            self.assertTrue(validated_preferences(data)[1])

    def test_unknown_api_does_not_claim_reasoning_control(self):
        self.assertFalse(supports_reasoning_control({'BACKEND': 'DeepSeek', 'SELECTED_MODEL': 'claude-example'}))
        self.assertTrue(supports_reasoning_control({'BACKEND': 'DeepSeek', 'SELECTED_MODEL': 'deepseek-flash'}))
        self.assertTrue(supports_reasoning_control({'OLLAMA_MODEL': 'qwen3:8b'}))

    def test_failed_reasoning_save_keeps_running_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            app = fixtures.SelectedFixTests().app(directory)
            app.config.update(merged_preferences(app.config, CHAT))
            before = dict(app.config['REASONING_MODES'])
            with patch('windows_native.ROOT', Path(directory)), patch('windows_native.save_preferences', side_effect=OSError):
                with self.assertRaises(OSError):
                    app.set_reasoning('careful')
            self.assertEqual(app.config['REASONING_MODES'], before)

    def test_menu_has_two_purposes_and_one_prompt_submenu(self):
        with tempfile.TemporaryDirectory() as directory:
            app = fixtures.SelectedFixTests().app(directory)
            app.config.update(merged_preferences(app.config, CHAT))
            app.enabled = True
            app.hide_tray_result = Mock()
            app.network_busy = False
            app.user.CreatePopupMenu.side_effect = range(10, 60)
            app.user.TrackPopupMenu.return_value = 0
            with patch('windows_native.ROOT', Path(directory)):
                app.menu()
            labels = [call.args[3] for call in app.user.AppendMenuW.call_args_list if len(call.args) > 3]
            self.assertEqual(labels.count('Hỏi đáp / phân tích'), 1)
            self.assertEqual(labels.count('Lập trình'), 1)
            self.assertEqual(sum(isinstance(label, str) and label.startswith('Prompt đang dùng') for label in labels), 1)
            self.assertNotIn('Phân tích', labels)
            self.assertFalse(any(isinstance(label, str) and label.startswith('Dùng chế độ') for label in labels))
