import unittest
from hover_layout import PAGE_SECONDS, MAX_HOVER_ROWS, answer_pages, page_index
from request_display import RequestDisplay


def measure(text):
    return len(text) * 7


class HoverLayoutTests(unittest.TestCase):
    def display(self, answer, copy='copied'):
        display = RequestDisplay()
        display.finish('done', 34, answer, copy=copy)
        return display

    def test_ten_choices_fit_one_page_three_columns_with_original_numbers(self):
        answer = '\n'.join(f'{i}. {chr(65 + i % 4)}' for i in range(4, 14))
        display = self.display(answer)
        pages = display.hover_pages(measure, 330)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].columns, 3)
        self.assertEqual(len(pages[0].rows), 5)
        value = '\n'.join(pages[0].rows)
        for row in answer.splitlines():
            self.assertIn(row, value)
        self.assertEqual(display.answer, answer)
        self.assertTrue(all(measure(row) <= 318 for row in pages[0].rows))

    def test_unknown_choice_keeps_its_cell_and_global_warning(self):
        answer = '\n'.join(f'{i}. B' if i != 5 else '5. Chưa xác định' for i in range(1, 11))
        pages = self.display(answer).hover_pages(measure, 330)
        self.assertEqual(len(pages), 1)
        self.assertIn('Chưa rõ: 5', pages[0].rows[0])
        self.assertIn('5. ?', '\n'.join(pages[0].rows))
        self.assertIn('10. B', '\n'.join(pages[0].rows))

    def test_ten_short_fill_answers_fit_two_columns_without_cutting_words(self):
        answer = '\n'.join(f'{i}. Machine' for i in range(1, 11))
        page, = self.display(answer).hover_pages(measure, 330)
        self.assertEqual(page.columns, 2)
        self.assertEqual(len(page.rows), 6)
        self.assertIn('10. Machine', page.rows[-1])

    def test_long_fill_answers_are_all_reachable_without_ellipsis(self):
        answer = '\n'.join(f'{i}. ' + 'Generative Pre-trained Transformer ' * 3 for i in range(1, 11))
        pages = self.display(answer).hover_pages(measure, 330)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(page.columns == 1 for page in pages))
        bodies = '\n'.join('\n'.join(page.rows[1:]) for page in pages)
        self.assertEqual(''.join(bodies.split()), ''.join(answer.split()))
        self.assertNotIn('…', bodies)
        self.assertTrue(all(len(page.rows) <= MAX_HOVER_ROWS for page in pages))
        self.assertTrue(all(measure(row) <= 318 for page in pages for row in page.rows))
        self.assertTrue(pages[-1].rows[0].startswith(f'{len(pages)}/{len(pages)}'))

    def test_copy_and_unknown_status_survive_each_page(self):
        answer = '\n'.join(f'{i}. Machine' if i != 7 else '7. Chưa xác định (ảnh bị cắt)' for i in range(1, 25))
        pages = self.display(answer, 'changed').hover_pages(measure, 330)
        for page in pages:
            value = '\n'.join(page.rows)
            self.assertIn('clipboard đã đổi', value)
            self.assertIn('Chưa rõ: 7', value)
            self.assertIn('Shift+F8', value)
            self.assertLessEqual(len(page.rows), MAX_HOVER_ROWS)

    def test_single_long_word_is_not_discarded(self):
        answer = '1. ' + 'á😀' * 100
        pages = self.display(answer).hover_pages(measure, 330)
        bodies = ''.join(''.join(page.rows[1:]) for page in pages)
        self.assertEqual(''.join(bodies.split()), ''.join(answer.split()))

    def test_numbered_blanks_keep_question_and_blank_identifiers(self):
        answer = '\n'.join(f'Câu 2, ô {i}: lạnh' for i in range(1, 11))
        pages = self.display(answer).hover_pages(measure, 330)
        value = '\n'.join(row for page in pages for row in page.rows)
        for i in range(1, 11):
            self.assertIn(f'2, ô {i}. lạnh', value)

    def test_unknown_answers_are_not_invented_from_option_lists(self):
        pages = self.display('Câu 4. Câu hỏi nào đúng?\n1. A\n2. B').hover_pages(measure, 330)
        self.assertNotIn('1. A', '\n'.join(pages[0].rows))

    def test_page_timer_is_stable_clamps_negative_time_and_cycles(self):
        self.assertEqual(page_index(100, 100, 3), 0)
        self.assertEqual(page_index(104.9, 100, 3), 0)
        self.assertEqual(page_index(100 + PAGE_SECONDS, 100, 3), 1)
        self.assertEqual(page_index(115, 100, 3), 0)
        self.assertEqual(page_index(99, 100, 3), 0)
        self.assertEqual(page_index(500, 100, 1), 0)
