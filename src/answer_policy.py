"""Clipboard-oriented reply instructions; independent of provider and reasoning mode."""
from chat_modes import CODING
from coding_prompt import CODE_PROMPT
from compact_preview import compact_answer

STYLES = ('short', 'choices', 'free')
STYLE_LABELS = {'short': 'Tự nhận dạng câu',
                'choices': 'Chỉ đáp án trắc nghiệm', 'free': 'Theo yêu cầu · không thêm hướng dẫn'}

CHOICES = """Với câu hỏi trắc nghiệm, chỉ xuất đáp án, mỗi câu một dòng.
Giữ nguyên số câu và nhãn phương án có sẵn trong đề (A/B/C/D, a/b/c/d, 1/2/3/4...).
Nếu các phương án không có nhãn, dùng số thứ tự 1, 2, 3, 4, ... theo thứ tự xuất hiện
trong từng câu, từ trên xuống dưới; không tự gán A/B/C/D. Ví dụ: Câu 5: 2.
Nếu có nhiều đáp án đúng, liệt kê các nhãn/số tương ứng trên cùng dòng.
Không nhầm số câu với số phương án. Không thêm Markdown, lời mở đầu hay giải thích,
trừ khi người dùng yêu cầu. Câu không đủ dữ kiện hoặc không đọc rõ: ghi số câu
kèm 'Chưa xác định', không đoán hoặc tự bịa phương án."""
CHOICES += """\nNếu chưa xác định được, thêm lý do rất ngắn khi biết:
'Chưa xác định (ảnh bị cắt)', 'Chưa xác định (không đọc rõ)' hoặc
'Chưa xác định (thiếu dữ kiện)'. Không từ chối toàn bộ đề khi chỉ một câu thiếu:
trả lời những câu đủ dữ kiện và đánh dấu riêng câu còn thiếu."""

CHOICES += """\nĐầu ra ví dụ: 4. d\n5. b\n6. a. Không chép lại đề, mô tả ảnh,
phương án đầy đủ, trạng thái ô chọn hoặc mức độ chắc chắn. Đọc và kiểm tra các
phương án trước khi chọn; chỉ xuất nhãn đáp án đã kiểm tra. Với đề có cả trắc nghiệm
và điền khuyết, giữ số câu trên mọi dòng, ví dụ: 1. B; 2. 4; 3. lạnh
(mỗi đáp án một dòng). Chỉ một câu điền khuyết độc lập mới bỏ số câu."""

FILL_BLANKS = """Với câu hỏi điền khuyết, chỉ xuất phần cần điền, không chép lại câu hỏi
hay câu hoàn chỉnh, không thêm lời mở đầu, Markdown hoặc giải thích, trừ khi người dùng yêu cầu.
Nếu đề chỉ có một câu điền khuyết độc lập với một chỗ trống, xuất trực tiếp giá trị
cần điền, không thêm số thứ tự. Trong đề có nhiều câu hoặc nhiều loại câu, giữ số câu.
Nếu có nhiều chỗ trống, mỗi chỗ một dòng theo thứ tự xuất hiện trong đề; giữ số câu
có sẵn. Với nhiều chỗ trống trong cùng một câu, dùng 'Câu 2, ô 1: ...',
'Câu 2, ô 2: ...'. Nếu đề không có số câu, đánh số 1, 2, 3, ... theo thứ tự chỗ trống.
Giữ đúng ký hiệu, đơn vị và dạng từ cần điền. Chỗ thiếu dữ kiện hoặc không đọc rõ:
ghi 'Chưa xác định' tại đúng vị trí, không đoán và không bỏ qua chỗ trống đó."""


AUTO_QUESTION = """Tự nhận dạng từng câu từ nội dung chữ hoặc ảnh, kể cả đề có nhiều dạng câu.
Không cần hỏi người dùng chọn dạng; không in nhãn phân loại hoặc chép lại đề.
Phân biệt câu ĐỌC HIỂU CODE (hỏi kết quả chạy, lỗi, độ phức tạp, ý nghĩa)
với câu VIẾT CODE (yêu cầu tạo chương trình/hàm). Có code trong đề không có
nghĩa là phải viết lại chương trình. Nếu có lựa chọn, ưu tiên dạng trắc nghiệm.
Trắc nghiệm và điền khuyết: áp dụng quy tắc đáp án dưới đây.
Đọc hiểu code không có lựa chọn: trả chính xác kết quả hoặc giải thích ngắn
theo câu hỏi; giữ dấu cách/xuống dòng của output khi cần. Phân biệt lỗi biên dịch,
lỗi lúc chạy và hành vi không xác định; không bịa output khi không xác định được.
Tính toán/viết đáp số: trả kết quả theo định dạng đề, giữ đơn vị và yêu cầu làm
tròn. Nếu chỉ yêu cầu đáp số, chỉ trả đáp số; nếu yêu cầu cách làm, thêm các bước
kiểm chứng ngắn. Không tự thêm đơn vị vào ô chỉ nhận số.
Viết code: trả code đầy đủ, đúng ngôn ngữ/phiên bản đề yêu cầu, đúng chữ ký hàm
hoặc input/output. Không mặc định C++ khi đề dùng ngôn ngữ khác. Kiểm tra logic
với ví dụ và trường hợp biên trước khi trả; chỉ nói đã chạy test khi thực sự có
kết quả chạy. Không rút code thành đáp án một dòng. Nếu thiếu ngôn ngữ hoặc
điều kiện quan trọng, nêu ngắn phần cần bổ sung, không tự bịa yêu cầu.
Với đề hỗn hợp, giữ số câu và xử lý mỗi câu theo dạng riêng; phần code nằm dưới
số câu tương ứng. Quy tắc chỉ một dòng áp dụng cho đáp án ngắn, không áp dụng
cho câu viết code. Câu bị cắt/mờ/thiếu dữ kiện: đánh dấu riêng Chưa xác định
và nêu phần cần bổ sung; vẫn giải các câu khác đủ dữ kiện.
Theo yêu cầu định dạng rõ ràng của người dùng khi có."""


def answer_instruction(mode, style='short'):
    if mode == CODING:
        return CODE_PROMPT
    if style == 'free':
        return ''
    if style == 'choices':
        return CHOICES + '\n' + FILL_BLANKS + '\nVới câu hỏi khác, trả lời trực tiếp và ngắn gọn.'
    return ('Trả lời để người dùng copy và dán ngay. Với câu hỏi thông thường, trả lời '
            'trực tiếp, ngắn gọn, đủ ý; không có lời mở đầu, không lặp câu hỏi. '
            'Chỉ giải thích dài hoặc trình bày định dạng khác khi người dùng yêu cầu.\n' +
            AUTO_QUESTION + '\n' + CHOICES + '\n' + FILL_BLANKS)


def tray_result(status, answer='', busy=False, preview='', error=''):
    """Small, discreet display only. Original clipboard answer is untouched."""
    if busy:
        return 'Đang xử lý…'
    if error:
        return 'Chưa lưu được lịch sử' if 'lưu' in error.lower() else 'Có lỗi · xem menu'
    lower = status.lower()
    if lower.startswith('đã hủy'):
        return 'Đã hủy'
    if lower.startswith(('lỗi', 'hết thời gian', 'api ', 'ai http', 'kết nối/api', 'key không')):
        return 'Lỗi API · Shift+F10 gửi lại'
    if answer:
        value = compact_answer(answer)
        if 'clipboard đã đổi' in lower or 'không copy được' in lower:
            value = '\n'.join(value.splitlines()[:4]) + '\nShift+F8 để copy'
        return value
    return ' '.join(status.split())[:32] or 'Đang chờ'


def short_tooltip(value):
    # NOTIFYICONDATA.szTip is 128 UTF-16 code units, including the terminator.
    encoded = value.encode('utf-16-le', errors='replace')
    if len(encoded) <= 254:
        return value
    return encoded[:252].decode('utf-16-le', errors='ignore') + '…'
