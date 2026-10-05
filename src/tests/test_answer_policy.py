import unittest

from answer_policy import CHOICES, FILL_BLANKS, answer_instruction
from chat_modes import CHAT, ANALYSIS, CODING
from coding_prompt import CODE_PROMPT


class AnswerPolicyTests(unittest.TestCase):
    def test_clipboard_styles_cover_choices_and_single_or_multiple_blanks(self):
        for mode in (CHAT, ANALYSIS):
            for style in ('short', 'choices'):
                with self.subTest(mode=mode, style=style):
                    instruction = answer_instruction(mode, style)
                    self.assertIn(CHOICES, instruction)
                    self.assertIn(FILL_BLANKS, instruction)
                    self.assertIn('một chỗ trống', instruction)
                    self.assertIn('không thêm số thứ tự', instruction)
                    self.assertIn('thứ tự xuất hiện', instruction)
                    self.assertIn('không bỏ qua chỗ trống', instruction)

    def test_programming_and_explicit_free_style_remain_unchanged(self):
        for style in ('short', 'choices', 'free'):
            self.assertEqual(answer_instruction(CODING, style), CODE_PROMPT)
        for mode in (CHAT, ANALYSIS):
            self.assertEqual(answer_instruction(mode, 'free'), '')


if __name__ == '__main__':
    unittest.main()
