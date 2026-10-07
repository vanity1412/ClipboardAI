"""Request/copy status for compact UI. Never infers correctness of an answer."""
from dataclasses import dataclass, field
import re
import time
from compact_preview import answer_rows, uncertain_answers


def failure_hint(message):
    lower = message.lower()
    if 'đang chuyển/đọc mạng' in lower:
        return 'F4 chưa gửi · chờ chuyển mạng xong'
    if 'tạm dừng' in lower:
        return 'Phím tắt tạm dừng · bật lại trong menu'
    if 'chưa có câu hỏi' in lower:
        return 'Chưa có câu hỏi · gửi F4/F8 trước'
    for code, hint in ((401, 'Key sai · mở API Zoo'), (402, 'Hết số dư · mở API Zoo'),
                       (403, 'Không có quyền model · API Zoo'), (404, 'Sai model/endpoint · API Zoo'),
                       (429, 'API giới hạn lượt · gửi lại sau')):
        if re.search(rf'\b{code}\b', lower):
            return f'Lỗi {code}: {hint}'
    if 'toàn đen' in lower:
        return 'Ảnh đen · chưa gửi AI · F4 chụp lại'
    if 'thu nhỏ' in lower:
        return 'Cửa sổ thu nhỏ · mở đề rồi F4'
    if 'cửa sổ đã đổi' in lower:
        return 'Cửa sổ đã đổi · F4 chụp lại'
    if 'chụp' in lower:
        return 'Không chụp được · mở đề rồi F4'
    if 'model đọc ảnh' in lower or 'model ảnh' in lower:
        return 'Thiếu model đọc ảnh · API Zoo'
    if 'timeout' in lower or 'hết thời gian' in lower:
        return 'Hết thời gian · Shift+F10 gửi lại'
    if 'trống' in lower:
        return 'AI trả rỗng · Shift+F10 gửi lại'
    if 'token' in lower and ('hết' in lower or 'dở' in lower):
        return 'Đáp án dở · tăng token / gửi lại'
    if 'từ chối' in lower:
        return 'API từ chối · đổi model / gửi lại'
    if 'hoàn chỉnh' in lower or 'hoàn tất' in lower or 'stream' in lower or 'định dạng' in lower:
        return 'Phản hồi chưa đủ · Shift+F10 gửi lại'
    if 'mạng' in lower or 'kết nối' in lower:
        return 'Lỗi mạng · Shift+F10 gửi lại'
    if 'key' in lower or 'cấu hình' in lower or 'model' in lower:
        return 'Lỗi cấu hình · mở API Zoo'
    return 'AI xử lý lỗi · Shift+F10 gửi lại'


@dataclass
class RequestDisplay:
    phase: str = 'idle'
    stage: str = 'Đang xử lý'
    started: float | None = None
    duration: int = 0
    copy: str = 'manual'
    answer: str = ''
    error: str = ''
    image: bool = False
    notice: str = ''
    feedback: str = ''
    shortcut_names: dict = field(default_factory=dict)
    status: str = ''

    def hint(self, text):
        return re.sub(r'Shift\+F(?:10|9|8)|F(?:10|9|8|7|6|4|3)\b',
                      lambda match: self.shortcut_names.get(match.group(), match.group()), text)

    def show_feedback(self, message):
        self.feedback = message
    _cached_answer: str | None = None
    _unknown: list = field(default_factory=list)
    _answer_rows: list = field(default_factory=list)
    _all_answer_rows: list = field(default_factory=list)
    _uncertain_reason: str = ''

    def answer_details(self):
        if self._cached_answer != self.answer:
            self._cached_answer = self.answer
            self._unknown = uncertain_answers(self.answer)
            self._uncertain_reason = ''
            for line in self.answer.splitlines():
                if 'chưa xác định' not in line.lower():
                    continue
                for reason in ('ảnh bị cắt', 'không đọc rõ', 'thiếu dữ kiện', 'không thấy câu hỏi'):
                    if reason in line.lower():
                        self._uncertain_reason = reason
                        break
                if self._uncertain_reason:
                    break
            self._all_answer_rows = answer_rows(self.answer)
            self._answer_rows = [
                line[:160] for line in self._all_answer_rows
                if not re.match(r'^(?:\d+\s*[.:)]\s*)?Chưa xác định\b', line, re.I)]
        return self._unknown, self._answer_rows

    def hover_pages(self, measure, width):
        from hover_layout import HoverPage, answer_pages, wrap_line, MAX_HOVER_ROWS
        feedback = self.feedback
        if feedback:
            return [HoverPage(wrap_line(self.hint(feedback), measure, max(1, width - 12))[:MAX_HOVER_ROWS])]
        if self.phase != 'done':
            return [HoverPage(self.preview().splitlines())]
        unknown, _ = self.answer_details()
        hint = self.hint('Shift+F8 để copy') if self.copy in ('changed', 'error', 'manual') else ''
        return answer_pages(self.header(), self._all_answer_rows, unknown,
                            self._uncertain_reason, hint, measure, width, notice=self.notice)

    def begin(self, started=None, image=False):
        self.phase, self.started = 'running', started
        self.stage = 'Đang chụp ảnh' if image else 'Đang chuẩn bị gửi AI'
        self.duration, self.answer, self.error, self.notice = 0, '', '', ''
        self.feedback = ''
        self.status = ''
        self.copy, self.image = 'manual', image

    def begin_model(self, started):
        self.started, self.stage = started, 'Model đang xử lý'

    def seconds(self, now=None):
        if self.phase == 'running' and self.started is not None:
            return max(0, int((time.monotonic() if now is None else now) - self.started))
        return self.duration

    def finish(self, phase, duration, answer='', error='', copy='manual'):
        self.phase, self.duration, self.answer, self.error, self.copy = phase, duration, answer, error, copy
        self.notice = ''
        self.feedback = ''
        self.status = ''

    def header(self, now=None):
        feedback = self.feedback
        if feedback:
            return self.hint(feedback)
        if self.status:
            return self.hint(self.status)
        seconds = self.seconds(now)
        if self.phase == 'running':
            return f'{self.stage} · {seconds}s' if self.started is not None else self.stage
        if self.phase == 'failed':
            return self.hint(failure_hint(self.error)) + f' · {seconds}s'
        if self.phase == 'cancelled':
            return f'Đã hủy · {seconds}s'
        if self.phase == 'done':
            label = {'copied': 'Đã copy', 'pending': 'Đang copy',
                     'changed': 'Có kết quả · clipboard đã đổi', 'error': 'Có kết quả · copy lỗi',
                     'manual': 'Có kết quả · chưa copy'}[self.copy]
            return f'{label} · {seconds}s'
        return self.notice or self.hint('Đang chờ · F4 ảnh / F8 chữ')

    def preview(self, now=None):
        rows = [self.header(now)]
        if self.feedback:
            return rows[0]
        if self.phase == 'done':
            unknown, answer_rows = self.answer_details()
            warning = ''
            # Always reveal unresolved question IDs, even below the normal cutoff.
            if unknown:
                warning = ('Chưa xác định' if unknown == ['cần bổ sung'] else
                           'Chưa rõ: ' + ', '.join(item.removeprefix('câu ') for item in unknown[:3]) +
                           ('…' if len(unknown) > 3 else ''))
                if self._uncertain_reason:
                    warning += ' · ' + self._uncertain_reason
            room = 4 - bool(warning) - bool(self.notice)
            hint = ('Shift+F8 để copy' if self.copy in ('changed', 'error', 'manual') else
                    'F9 chữ / Shift+F9 ảnh' if unknown and not answer_rows else '')
            if hint:
                room -= 1
            rows.extend(answer_rows[:room])
            if len(answer_rows) > room and rows:
                rows[-1] += ' …'
            if warning:
                rows.append(warning)
            if self.notice:
                rows.append(self.notice)
            if hint:
                rows.append(self.hint(hint))
        elif self.phase == 'running':
            rows.append(self.hint(self.notice or ('F10 hủy · chờ để nhận kết quả' if self.image else 'F10 để hủy')))
        elif self.phase == 'failed' and self.image and 'model' not in self.error.lower():
            rows.append(self.hint('Shift+F10 gửi ảnh cũ · F4 chụp mới'))
        return '\n'.join(rows[:5])
