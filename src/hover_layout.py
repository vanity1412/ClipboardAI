"""Arrange explicit answers using actual font widths, without changing clipboard."""
from dataclasses import dataclass
import re
from compact_preview import LABELS, ROW

PAGE_SECONDS = 5
MAX_HOVER_ROWS = 7
MAX_HOVER_HEIGHT = 134
UNKNOWN = re.compile(r'^Chưa xác định\b', re.I)


@dataclass
class HoverPage:
    rows: list[str]
    columns: int = 1


def wrap_line(line, measure, width):
    """Wrap all content, including long single words; never ellipsize answers."""
    result = []
    line = line.strip()
    while line:
        if measure(line) <= width:
            result.append(line)
            break
        lo, hi = 1, len(line)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if measure(line[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        stop = line.rfind(' ', 0, lo + 1)
        stop = stop if stop > 0 else lo
        result.append(line[:stop].rstrip())
        line = line[stop:].lstrip()
    return result


def fit_label(line, measure, width):
    if measure(line) <= width:
        return line
    while line and measure(line + '…') > width:
        line = line[:-1]
    return line + '…'


def grid_lines(cells, columns, measure, width):
    gap = '  |  '
    cell_width = (width - measure(gap) * (columns - 1)) // columns
    space = max(1, measure(' '))
    rows = []
    for start in range(0, len(cells), columns):
        group = cells[start:start + columns]
        parts = [cell + ' ' * max(0, int((cell_width - measure(cell)) // space))
                 for cell in group[:-1]]
        rows.append(gap.join(parts + group[-1:]))
    return rows


def answer_pages(header, raw_rows, unknown, reason, copy_hint, measure, width):
    """Grid short lists; paginate complete long answers while hovered."""
    usable = max(1, width - 12)
    warning = ''
    if unknown:
        warning = ('Chưa xác định' if unknown == ['cần bổ sung'] else
                   'Chưa rõ: ' + ', '.join(value.removeprefix('câu ') for value in unknown[:3]) +
                   ('…' if len(unknown) > 3 else ''))
        if reason:
            warning += ' · ' + reason
    numbered = [ROW.match(row) for row in raw_rows]
    choices = bool(raw_rows) and all(
        row and (LABELS.fullmatch(row.group(2)) or UNKNOWN.match(row.group(2)))
        for row in numbered)
    cells = ([f'{row.group(1)}. ' + ('?' if UNKNOWN.match(row.group(2)) else row.group(2))
              for row in numbered] if choices else
             [row for row in raw_rows if not re.match(r'^(?:\d+\s*[.:)]\s*)?Chưa xác định\b', row, re.I)])
    columns = 1
    if len(cells) >= 5:
        for candidate in ([3, 2] if choices else [2]):
            cell_width = (usable - measure('  |  ') * (candidate - 1)) // candidate
            if all(measure(cell) <= cell_width for cell in cells):
                columns = candidate
                break
    if columns > 1 and warning and measure(header + ' · ' + warning) <= usable:
        header += ' · ' + warning
        warning = ''
    footer = [value for value in (warning, copy_hint) if value]
    if unknown and not cells and not copy_hint:
        footer.append('F9 chữ / Shift+F9 ảnh')
    body_limit = min(5 if columns == 2 else 4, MAX_HOVER_ROWS - 1 - len(footer))
    if columns > 1:
        chunks = [cells[start:start + columns * body_limit]
                  for start in range(0, len(cells), columns * body_limit)] or [[]]
        bodies = [grid_lines(chunk, columns, measure, usable) for chunk in chunks]
    else:
        lines = [part for cell in cells for part in wrap_line(cell, measure, usable)]
        bodies = [lines[start:start + body_limit]
                  for start in range(0, len(lines), body_limit)] or [[]]
    pages = []
    for index, body in enumerate(bodies):
        # Put the page marker first so a long copy status cannot conceal it.
        title = (f'{index + 1}/{len(bodies)} · ' if len(bodies) > 1 else '') + header
        pages.append(HoverPage([fit_label(title, measure, usable)] + body +
                              [fit_label(value, measure, usable) for value in footer], columns))
    return pages


def page_index(now, started, count):
    return int(max(0, now - started) // PAGE_SECONDS) % max(1, count)
