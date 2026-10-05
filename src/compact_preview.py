"""Extract explicitly stated answers for display; never solve or change clipboard."""
import re

MAX_PREVIEW_ROWS = 5
MAX_PREVIEW_COLUMNS = 32
HEAD = re.compile(r'^Câu\s+(\d+(?:\s*,\s*ô\s+\d+)?)\s*([.:)])\s*(.*)$', re.I)
ROW = re.compile(r'^(\d+(?:\s*,\s*ô\s+\d+)?)\s*[.:)]\s*(.+)$', re.I)
LABELS = re.compile(r'^([A-Za-z]|\d{1,3})(?:\s*[,/&]\s*(?:[A-Za-z]|\d{1,3}))*$')
CHOICE = re.compile(r'^(?:→\s*)?(?:Chọn|Đáp án(?:\s+đúng)?\s*[:：]|Answer\s*:)\s*[:：]?\s*(.+)$', re.I)


def bounded(rows):
    if len(rows) > MAX_PREVIEW_ROWS:
        rows = rows[:MAX_PREVIEW_ROWS - 1] + ['…']
    return '\n'.join(line if len(line) <= MAX_PREVIEW_COLUMNS else
                     line[:MAX_PREVIEW_COLUMNS - 1] + '…' for line in rows)


def clean(line):
    return line.strip().replace('**', '').replace(chr(96), '').strip()


def selected_label(value):
    match = re.match(r'^([A-Za-z]|\d{1,3})(?:\s*[,/&]\s*(?:[A-Za-z]|\d{1,3}))*(?=\s*[.):]\s*|\s*$)', value)
    return match.group(0).strip() if match else None


def uncertain_answers(answer):
    """Detect explicit unresolved answers, before preview truncation."""
    current, values = None, []
    for raw in answer.splitlines():
        line = clean(raw).lstrip('* ').strip()
        head = re.match(r'^(?:Câu\s+)?(\d+(?:\s*,\s*ô\s+\d+)?)\s*[.:)]\s*(.*)$', line, re.I)
        if head:
            current = head.group(1)
            line = head.group(2).lstrip('* ').strip()
        if re.match(r'^(?:(?:Đáp án|Chọn)\s*:\s*)?Chưa xác định\b', line, re.I):
            label = 'câu ' + current if current else 'cần bổ sung'
            if label not in values:
                values.append(label)
    return values


def answer_rows(answer):
    """Extract rows before limiting the hover size, including long fill answers."""
    lines = [clean(line) for line in answer.splitlines() if line.strip()]
    if not lines:
        return ['Chưa có đáp án']
    if chr(96) * 3 in answer:
        return []
    # Length controls display truncation, not whether an answer exists.
    numbered = [ROW.match(line) for line in lines]
    if all(numbered):
        return [f'{row.group(1)}. {row.group(2)}' for row in numbered]
    current, found, concise = None, {}, []
    question_headings = False
    for line in lines:
        head = HEAD.match(line)
        if head:
            current, separator, rest = head.groups()
            if (LABELS.fullmatch(rest) or rest.lower().startswith('chưa xác định') or
                    separator == ':' and len(rest) <= MAX_PREVIEW_COLUMNS and
                    '?' not in rest and not CHOICE.match(rest)):
                found[current] = rest
            else:
                question_headings = True
                choice = CHOICE.match(rest)
                label = selected_label(choice.group(1)) if choice else None
                if label:
                    found[current] = label
            continue
        choice = CHOICE.match(line)
        if current and line.lower().startswith('chưa xác định'):
            found[current] = 'Chưa xác định'
            continue
        if choice and current:
            label = selected_label(choice.group(1))
            if label:
                found[current] = label
            continue
        row = ROW.match(line)
        if row:
            concise.append(f'{row.group(1)}. {row.group(2)}')
    if found:
        rows = [f'{number}. {value}' for number, value in found.items()]
        return rows + (concise if not question_headings else [])
    if question_headings:
        return []
    if concise and len(concise) == len(lines):
        return concise
    if len(lines) <= MAX_PREVIEW_ROWS and all(len(line) <= MAX_PREVIEW_COLUMNS for line in lines):
        return lines
    return []


def compact_answer(answer):
    return bounded(answer_rows(answer)) or 'Đã có kết quả.'
