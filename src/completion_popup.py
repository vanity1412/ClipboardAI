"""Bounded answer preview and corner placement for the brief answer popup."""
from compact_preview import answer_rows, clean, uncertain_answers
from hover_layout import answer_pages

POPUP_SECONDS = 0.5
POPUP_TEXT_COLOR = 0xB8B8B8
POPUP_MAX_WIDTH = 480
POPUP_LINE_HEIGHT = 22
POPUP_MAX_CHARS = 4096


def popup_rows(answer, measure, width):
    # This runs on the UI thread. Measure only a bounded preview, even when
    # the complete response contains megabytes of prose/code on a single line.
    clipped = len(answer) > POPUP_MAX_CHARS
    answer = answer[:POPUP_MAX_CHARS]
    if clipped and '\n' in answer:
        answer = answer.rsplit('\n', 1)[0]
    rows = answer_rows(answer)
    if not rows:
        rows = [clean(line) for line in answer.splitlines()
                if line.strip() and not line.strip().startswith('```')]
    pages = answer_pages('Đáp án', rows, uncertain_answers(answer), '', '',
                         measure, max(1, width - 12))
    preview = list(pages[0].rows)
    if clipped or len(pages) > 1:
        preview.append('… Xem đầy đủ trong menu tray / F7 để copy')
    return preview


def popup_bounds(work, width, rows):
    left, top, right, bottom = work
    available_width, available_height = max(1, right - left), max(1, bottom - top)
    margin = min(12, (available_width - 1) // 2, (available_height - 1) // 2)
    width = min(max(1, width), available_width - 2 * margin)
    height = min(POPUP_LINE_HEIGHT * rows + 20, available_height - 2 * margin)
    return right - width - margin, bottom - height - margin, width, height
