import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from compact_preview import compact_answer
from answer_policy import tray_result
from chat_modes import CHAT, ANALYSIS, CODING
from coding_prompt import CODE_PROMPT
from prompt_profiles import instruction_for, prompt_preferences, selected_prompt
from runtime_settings import validated_preferences, save_preferences, load_runtime_settings
from windows_native import WindowsApp


class CompactTests(unittest.TestCase):
    def test_verbose_selected_answers_ignore_question_and_footer(self):
        answer = '\n'.join([
            'Lời mở đầu và bối cảnh ảnh.',
            '**Câu 4.** *Một câu hỏi cần chọn phương án nào?*',
            '→ **Chọn d. Ban Tổ chức Trung ương**',
            'Giải thích dài, mức độ trung bình.',
            '**Câu 5.** *Câu hỏi tiếp theo?*',
            '→ **Chọn b. "Nêu gương"**',
            '**Câu 6.** *Câu hỏi khác?*',
            '→ **Chọn a.** *Nội dung rất dài*',
            '**Câu 7.** *Câu hỏi tiếp theo?*',
            '→ **Chọn a. Mặt trận Tổ quốc**',
            'Lưu ý: đáp án d của câu 7 bị cắt nên không đọc đủ.'])
        self.assertEqual(compact_answer(answer), '4. d\n5. b\n6. a\n7. a')
        self.assertEqual(tray_result('Đã copy kết quả và hướng dẫn dài', answer), '4. d\n5. b\n6. a\n7. a')

    def test_fill_blank_mixed_and_multiselect(self):
        self.assertEqual(compact_answer('Câu 1: B\nCâu 2: 4\nCâu 3: lạnh'), '1. B\n2. 4\n3. lạnh')
        self.assertEqual(compact_answer('Câu 5. Chọn nhiều đáp án đúng?\nChọn A, C.'), '5. A, C')
        self.assertEqual(compact_answer('12 kg'), '12 kg')

    def test_unselected_options_and_long_content_are_not_displayed(self):
        self.assertEqual(compact_answer('Câu 4. Câu hỏi nào đúng?\n1. A\n2. B'), 'Đã có kết quả.')
        self.assertEqual(compact_answer('SECRET ' * 150), 'Đã có kết quả.')
        self.assertEqual(compact_answer(chr(96)*3 + 'python\nprint(42)\n' + chr(96)*3), 'Đã có kết quả.')
        lines = compact_answer('\n'.join(f'{i}. A' for i in range(1, 61))).splitlines()
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[-1], '…')
        self.assertEqual(tray_result('Working', 'old', True, 'SECRET'), 'Đang xử lý…')

    def test_per_mode_selection_survives_reload_without_changing_other_modes(self):
        config = {'ANSWER_STYLE': 'short'}
        custom = 'Trả lời ngắn bằng tiếng Việt.'
        config.update(prompt_preferences(CHAT, 'custom', custom, config))
        config.update(prompt_preferences(ANALYSIS, 'choices', '', config))
        self.assertEqual(instruction_for(CHAT, config), custom)
        self.assertIn('trắc nghiệm', instruction_for(ANALYSIS, config))
        self.assertEqual(instruction_for(CODING, config), CODE_PROMPT)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'prefs.json'
            save_preferences(path, config)
            restored = {}
            self.assertEqual(load_runtime_settings(restored, path), [])
            self.assertEqual(instruction_for(CHAT, restored), custom)
        self.assertEqual(selected_prompt(CODING, {'PROMPT_MODES': {'0': 'short'}}), 'short')
        self.assertNotEqual(instruction_for(CODING, {'PROMPT_MODES': {'0': 'short'}}), CODE_PROMPT)

    def test_invalid_prompt_and_failed_save_keep_old_config(self):
        for data in ({'PROMPT_MODES': {'3': 'bad'}}, {'PROMPT_CUSTOM': {'3': 'x'*16001}},
                     {'PROMPT_MODES': {'5': 'short'}}):
            self.assertTrue(validated_preferences(data)[1])
        with self.assertRaises(ValueError):
            prompt_preferences(CHAT, 'custom', '', {})
        app = WindowsApp.__new__(WindowsApp)
        app.config = {}
        app.tooltip = Mock()
        with tempfile.TemporaryDirectory() as root, patch('windows_native.ROOT', Path(root)), \
                patch('windows_native.save_preferences', side_effect=OSError):
            with self.assertRaises(OSError):
                app.set_mode_prompt(CHAT, 'choices')
            self.assertEqual(app.config, {})
