# ClipboardAI

Ứng dụng Windows chạy nền: chụp/copy câu hỏi hoặc code, gửi AI, nhận đáp án tự vào clipboard để Ctrl+V. Rê chuột vào icon tray để xem trạng thái, số giây xử lý và đáp án gọn.

Bảng chức năng đầy đủ, menu tray, các cửa sổ cài đặt và vị trí mã nguồn: [docs/CHUC_NANG.md](docs/CHUC_NANG.md). Tài liệu này cũng được chép vào thư mục `dist` khi build.

## Bắt đầu

1. Mở `ClipboardAI.exe` trong thư mục có quyền ghi; không chạy cùng bản ClipboardAI cũ.
2. Chuột phải icon tray → Model / API / cài đặt → API Zoo. API Zoo là nơi thêm API, nhập key, chọn model và cấu hình API dự phòng. EXE không có key sẵn. Với ảnh, chọn model/API có hỗ trợ ảnh.
3. Chọn mục đích `Hỏi đáp / phân tích` hoặc `Lập trình`; chọn prompt và mức suy luận riêng. Prompt có thể sửa/lưu từ menu.
4. Copy câu hỏi → F8, hoặc đang xem câu hỏi/code → F4; chờ hoàn tất rồi Ctrl+V.

| Phím | Hành vi |
|---|---|
| F8 | Gửi chữ clipboard thành câu hỏi mới |
| F4 | Kéo chọn vùng trong cửa sổ hiện tại, thả chuột để gửi câu hỏi mới |
| F9 | Gửi chữ bổ sung cho phiên hiện tại |
| Shift+F9 | Chụp ảnh bổ sung cho phiên hiện tại |
| F10 | Hủy; giữ clipboard |
| Shift+F10 | Gửi lại yêu cầu đã lưu |
| Shift+F8 | Copy lại đáp án hoàn tất gần nhất |
| F6 | Chọn/quản lý phiên và đáp án |
| F3 hai lần | Mở menu chọn card mạng/Wi-Fi |

F4/F8 độc lập; F9/Shift+F9 dùng dữ kiện trước. Clipboard đổi trong lúc chạy sẽ được bảo vệ; dùng menu copy đáp án hoặc Shift+F8 để lấy lại kết quả. Không có cửa sổ chat tự bật khi trả lời. Menu `Tạm dừng` trả phím cho ứng dụng khác; không hủy yêu cầu đang chạy. Phím global có thể không bắt được trong app chạy quyền admin. Các phím đã đăng ký có thể chặn hành vi gốc.

F7 không còn là phím của ClipboardAI. Muốn mở chat, chọn **Mở chat** trong menu tray; Esc trong ô nhập sẽ ẩn chat. Bộ phím mặc định còn lại được giữ nguyên, không áp một bộ phím riêng cho Chrome.

## Cài đặt phím tắt

Chuột phải icon tray → **Model / API / cài đặt → Cài đặt phím tắt…**. Hoặc mở **Cài đặt / kiểm tra kết nối**, bấm nút **Cài đặt phím tắt**.

- Mỗi chức năng có ô **Bật** và ô nhập tổ hợp. Bỏ chọn Bật để trả phím đó cho ứng dụng khác; vẫn dùng chức năng qua menu tray.
- Bấm vào ô rồi nhấn tổ hợp, hoặc nhập trực tiếp như `Ctrl+Alt+Q`. Tổ hợp riêng vẫn được nhớ khi tắt chức năng rồi mở lại cài đặt.
- Bấm **Lưu**, sau đó đóng cửa sổ để áp dụng. Trong lúc sửa phím, các phím của tool tạm được trả cho ứng dụng khác.
- Phím trùng giữa các chức năng đang bật hoặc bị ứng dụng khác chiếm: không áp dụng thay đổi, giữ bộ phím trước.
- Kiểm tra phím bị chiếm vẫn chạy khi app đang Tạm dừng; Lưu hoặc đóng cửa sổ đổi phím không tự bật lại app.
- **Khôi phục mặc định** điền lại bộ phím ở bảng trên và bật các chức năng; cần bấm Lưu để áp dụng.
- Tùy chọn lưu trong `preferences.json`, gồm tổ hợp và các chức năng đã tắt. Cấu hình cũ có phím mở chat sẽ được bỏ qua; các phím tùy chỉnh khác được giữ.

## Chọn vùng chụp

F4 và Shift+F9 mặc định kéo chọn vùng trong cửa sổ đang xem. Ảnh được lấy trước khi bộ chọn mở; kéo chuột chỉ chọn phần cần gửi từ ảnh đó. Esc, chuột phải hoặc F10 hủy chọn vùng, giữ clipboard và phiên.

Menu **Chụp ảnh** cho chọn vùng/cả cửa sổ, bốn góc nhạt/viền rõ hơn và chỉ ảnh/ảnh kèm chữ clipboard. Shift+F9 chỉ bổ sung ảnh cho phiên trước, không kèm chữ clipboard. Chọn vùng đang giữ lại đáp án chờ copy; hủy chọn không tự ghi đè clipboard.

Trắc nghiệm giữ nhãn A/B/C/D nếu đề có nhãn; nếu không có, đáp án số là thứ tự phương án từ trên xuống (ví dụ `Câu 1: 2` = phương án thứ hai). Điền khuyết trả nội dung cần điền. Đáp án không xác định phải được báo thiếu dữ kiện; độ đúng còn phụ thuộc ảnh/đề/model. Prompt tùy chỉnh có thể thay đổi định dạng.

Chuyển card mạng có thể yêu cầu UAC; menu phân biệt card bật và đã kết nối. Khi mất mạng, mở phần khôi phục mạng trong menu và chọn lại card trong Windows. Các test tự động dùng mô phỏng, không chứng minh hoạt động trên mọi phần cứng.

## Build và tải EXE trên GitHub

Thư mục `clipboardAI` này là **gốc repository**. Đẩy nội dung thư mục lên GitHub để `.github/workflows` nằm ngay tại gốc, không lồng thêm một tầng clipboardAI trong repo.

Mỗi push/pull request chạy test rồi build trên Windows; cũng có thể vào **Actions → Build Windows EXE → Run workflow**. Khi job thành công, mở lần chạy → **Artifacts → ClipboardAI-Windows-x64**, tải và giải nén để lấy `ClipboardAI.exe`. Artifact lưu 30 ngày. Đây là artifact Actions, chưa phải GitHub Release và EXE chưa được ký số.

Build tại máy Windows, Python 3.14 x64:

```powershell
python -m pip install -r requirements-build.txt
python scripts/run_tests.py
./build.ps1
```

Kết quả ở `dist/ClipboardAI.exe`. Test giao diện có thể chạy thêm `python scripts/run_tests.py --desktop` tại desktop Windows. Chạy source: `python src/deepseek_flash_entry.py`; mở cấu hình trực tiếp: thêm `--open-zoo`.

Để đóng gói bản thử với đúng tên `ClipboardAI_Region_Test.exe`:

```powershell
./build.ps1 -Name ClipboardAI_Region_Test
```

File ở `dist/ClipboardAI_Region_Test.exe`; dùng cùng mã nguồn và cài đặt như bản chính. `SHA256.txt` ghi mã kiểm tra và tên EXE vừa build.

Build kiểm tra các tệp Tcl/Tk trước khi đóng gói. Nếu runtime bị thiếu hoặc môi trường build chặn quyền đọc, script dừng với lỗi thay vì tạo EXE thiếu cửa sổ chọn vùng/API Zoo/cài đặt phím. Dùng Python có Tcl/Tk và môi trường cho phép đọc các tệp runtime đó.

## Dữ liệu riêng

API key lưu trong `api_zoo.json` (hoặc `.env`/`mirai_config.json`) cạnh EXE, dạng plaintext, không mã hóa. Lịch sử hỏi đáp lưu trong `session.json`, tùy chọn/prompt lưu trong `preferences.json`, cũng dạng plaintext. Khi dùng AI, chữ/ảnh được gửi đến endpoint của API/model đã chọn và API dự phòng nếu bật; không đưa dữ liệu nhạy cảm vào yêu cầu nếu không muốn gửi nhà cung cấp đó.

Repository chỉ chứa code và cấu hình mẫu trống. `.gitignore` loại key, lịch sử, ảnh, log, EXE và build. Không ép thêm các file riêng bằng `git add -f`. Không đặt key trong source hoặc GitHub Actions; build không cần key.

## Cấu trúc

`src/`: ứng dụng và test. `scripts/run_tests.py`: chạy test không gọi API thật. `build.ps1`: đóng gói EXE kèm Tkinter cho API Zoo/prompt editor. `.github/workflows/build-windows.yml`: build tự động. `requirements*.txt`: dependency đã cố định phiên bản.
