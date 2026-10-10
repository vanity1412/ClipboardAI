# ClipboardAI

Ứng dụng Windows chạy nền: chụp/copy câu hỏi hoặc code, gửi AI, nhận đáp án tự vào clipboard để Ctrl+V. Khi hoàn tất, đáp án gọn màu xám nhạt như tray (`#B8B8B8`) hiện ở góc dưới bên phải màn hình đang dùng trong khoảng 0,5 giây. Rê chuột vào icon tray để xem trạng thái, số giây xử lý và đáp án gọn.

Bảng chức năng đầy đủ, menu tray, các cửa sổ cài đặt và vị trí mã nguồn: [docs/CHUC_NANG.md](docs/CHUC_NANG.md). Tài liệu này cũng được chép vào thư mục `dist` khi build.

API Zoo có mẫu OpenAI, **OpenAI Browser/ChatGPT**, Claude, DeepSeek, Grok, Gemini và custom OpenAI-compatible/Anthropic-compatible/Responses; điền endpoint và gợi ý model, lấy model từ API hoặc nhập ID riêng. OpenAI Browser dùng Codex CLI chính thức để đăng nhập; các mẫu API còn lại dùng key. Hướng dẫn và nguồn tham khảo OpenCode: [docs/API_PROVIDERS.md](docs/API_PROVIDERS.md).

## Bắt đầu

Máy mới cần Windows 10/11 64-bit, quyền admin và Internet. Vào GitHub **Actions → Build Windows EXE → lượt build thành công mới nhất → Artifacts → ClipboardAI-Windows-x64**, tải ZIP rồi giải nén toàn bộ vào thư mục riêng. **Code → Download ZIP** chỉ tải mã nguồn. Giữ `ClipboardAI.exe`, `cua-driver.exe`, `cua-driver-uia.exe` và thư mục giấy phép cùng nhau. Python, Tcl/Tk, Pillow và SQLite đã được đóng gói; không cần cài Python hoặc Node.js.

Máy mới chưa có tài khoản/model cấu hình: vào API Zoo để đăng nhập ChatGPT hoặc nhập API key, chọn model chữ và model ảnh. F2/F4 cần model hỗ trợ ảnh; đổi card mạng cần card Wi-Fi/LAN vật lý và driver Windows hoạt động. Kết nối Wi-Fi mới chưa lưu mật khẩu thực hiện qua Windows. Không chuyển API key mã hóa từ máy khác; nhập lại key hoặc đăng nhập trên máy mới.

1. Mở `ClipboardAI.exe` trong thư mục có quyền ghi; không chạy cùng bản ClipboardAI cũ. EXE yêu cầu quyền admin ngay khi mở: chấp nhận UAC một lần cho mỗi lần khởi động để thao tác bật/tắt card mạng không hỏi lại trong phiên đó. Khi thiếu Codex CLI, EXE tự tải/cài nền bản chính thức vào `%LOCALAPPDATA%/ClipboardAI/codex-cli`, kiểm tra checksum và khả năng khởi động trước khi dùng; không cần Node.js. Cài CLI không tự đăng nhập ChatGPT.
2. Chuột phải icon tray → Model / API / cài đặt → API Zoo. API Zoo là nơi thêm API, nhập key, chọn model và cấu hình API dự phòng. EXE không có key sẵn. Với ảnh, chọn model/API có hỗ trợ ảnh.
3. Chọn mục đích `Hỏi đáp / phân tích` hoặc `Lập trình`; chọn prompt và mức suy luận riêng. Prompt có thể sửa/lưu từ menu.
4. Copy câu hỏi → F8, hoặc đang xem câu hỏi/code → F4; chờ hoàn tất rồi Ctrl+V.

| Phím | Hành vi |
|---|---|
| F2 | Agent tự đọc màn hình và chọn đáp án trắc nghiệm qua Cua Driver MCP; F10 dừng |
| F8 | Gửi chữ clipboard thành câu hỏi mới |
| F4 | Kéo chọn vùng trong cửa sổ hiện tại, thả chuột để gửi câu hỏi mới |
| F9 | Gửi chữ bổ sung cho phiên hiện tại |
| Shift+F9 | Chụp ảnh bổ sung cho phiên hiện tại |
| F10 | Hủy; giữ clipboard |
| Shift+F10 | Gửi lại yêu cầu đã lưu |
| F7 | Copy lại và hiện popup đáp án hoàn tất gần nhất trong khoảng 0,5 giây |
| Shift+F7 | Copy và hiện popup đáp án phiên đang chọn |
| F6 | Chọn/quản lý phiên và đáp án |
| F3 hai lần | Mở menu chọn card mạng/Wi-Fi |

F4/F8 độc lập; F9/Shift+F9 dùng dữ kiện trước. Clipboard đổi trong lúc chạy sẽ được bảo vệ; dùng menu copy đáp án hoặc F7 để lấy lại kết quả. Không có cửa sổ chat tự bật khi trả lời. Popup đáp án tự ẩn sau khoảng 0,5 giây, không lấy focus và cho chuột bấm xuyên xuống ứng dụng bên dưới. Popup dùng cửa sổ topmost để hiện trên web toàn màn hình F11 thông thường; không bảo đảm hiển thị trên desktop bảo mật hoặc phần mềm khóa màn hình. Popup tạm ẩn khi mở menu của tool, gồm menu chọn mạng bằng F3 hai lần, để không che menu. Đáp án dài có thể chỉ hiện bản xem gọn; F7/menu tray vẫn lấy được câu trả lời đầy đủ. Menu `Tạm dừng` trả phím cho ứng dụng khác; không hủy yêu cầu đang chạy. Phím global có thể không bắt được trong app chạy quyền admin. Các phím đã đăng ký có thể chặn hành vi gốc.

F7 copy đáp án hoàn tất gần nhất và hiện lại chính đáp án đó trong popup khoảng 0,5 giây, kể cả khi yêu cầu mới đang chạy; không gọi AI lại hoặc đổi trạng thái xử lý. Shift+F7 copy và hiện đáp án phiên đang chọn. Muốn mở chat, chọn **Mở chat** trong menu tray; Esc trong ô nhập sẽ ẩn chat. Bộ phím mặc định còn lại được giữ nguyên, không áp một bộ phím riêng cho Chrome.

## Cài đặt phím tắt

**F3 một lần:** sau khoảng nửa giây hiện menu nhỏ chọn **Model trả lời** hoặc **Model ảnh · F2/F4**. Chọn API/model rồi mức reasoning trong menu con; lựa chọn được lưu theo từng model, mặc định `medium`. **F3 hai lần trong 0,5 giây:** mở chọn card mạng như trước. Menu model không đổi cấu hình khi AI/mạng đang xử lý. Chọn `Mặc định nhà cung cấp` nếu model/gateway không hỗ trợ effort. API Zoo cũng có trường Reasoning mặc định; thay đổi trường đó sẽ thay thế các mức riêng từng model.

OpenAI-compatible gửi `reasoning_effort`, Responses gửi `reasoning.effort`, ChatGPT qua Codex gửi `effort`, Anthropic Messages gửi `output_config.effort`. Gateway cần hỗ trợ/thực hiện tham số này; thành công HTTP không chứng minh mức suy luận nội bộ đã được áp dụng. DeepSeek chỉ chọn `high`/`max`; mặc định `medium` được ánh xạ thành `high` theo giới hạn giao thức. Menu Nhanh/Suy nghĩ kỹ cũ vẫn dành cho chế độ legacy, còn API Zoo dùng mức riêng đã lưu.

**Desktop Agent / F2:** mở cửa sổ đề trắc nghiệm rồi nhấn F2. Agent lấy cây giao diện và ảnh cửa sổ qua Cua Driver MCP, dùng model đọc ảnh đang chọn trong API Zoo để suy luận từng bước, chọn đáp án và cuộn/chuyển câu. Mỗi thao tác đều được theo sau bởi quan sát mới. F10 dừng phát thêm thao tác; Tạm dừng/Thoát cũng dừng agent. Agent dừng trước nút nộp bài để bạn kiểm tra và tự nộp. Không tự copy hoặc lưu ảnh/đề của lượt agent vào lịch sử.

Giữ `cua-driver.exe` và `cua-driver-uia.exe` cạnh EXE. Build tải runtime chính thức phiên bản 0.34.1 từ wheel `cua-driver` và chép các file này cùng thông tin phiên bản/giấy phép. Khi chạy source, dùng `python scripts/setup_cua.py` một lần. F2 dùng một kết nối MCP stdio riêng; model trả quyết định JSON để app gọi tool MCP, không cần bật shell hay công cụ Codex trong luồng hỏi đáp. Chế độ này dùng model ảnh của API đang chọn, không tự chuyển API dự phòng; có giới hạn 360 bước/15 phút. Lỗi tạm thời được quan sát lại có giới hạn; trang đăng nhập hoặc lỗi kéo dài sẽ dừng và báo lý do.

Với PDF chỉ đọc, F2 giải các câu nhìn đủ trên màn hình và hiển thị đáp án; tài liệu không có ô chọn thì không thể tự điền. Cuộn đến phần tiếp theo rồi F2 lại. F7 copy và hiện lại đáp án.

F2 khóa vào cửa sổ đang chọn và quan sát lại sau từng bước; có thể đổi trang/web/tab trong cửa sổ đó. Khi đổi website, agent bỏ ngữ cảnh trang cũ. Nếu AI trả sai cấu trúc, phần tử đã thay đổi hoặc UIA không hỗ trợ, agent quan sát mới và thử sửa tối đa hai lần; không click khi thiếu đích, không phát lại thao tác có kết quả chưa rõ. Agent theo dõi cả nhóm radio ngoài vùng ảnh để tìm câu bị bỏ sót; trước khi báo hoàn tất phải kiểm tra nội dung phía dưới. Cuộn từng phần và chờ giao diện ổn định rồi mới đọc lại. Cửa sổ bị thu nhỏ sẽ chờ tối đa 30 giây để bạn mở lại, F10 vẫn dừng ngay. Nhật ký ghi số nhóm đã chọn/còn thiếu, mã lỗi và các lượt khôi phục. Con trỏ agent chỉ dùng mũi tên Windows hiện tại, không có vòng sáng, biểu tượng hay nhãn Cua; không thay đổi con trỏ thật hoặc lấy focus. Muốn chuyển sang cửa sổ khác, F10 rồi chọn cửa sổ mới và F2.

Kiểm tra đường F2 và điều khiển với trang mẫu: `ClipboardAI.exe --verify-agent` dùng model giả lập; `ClipboardAI.exe --verify-agent-live` dùng model đã cấu hình thật (tối đa 8 lượt/150 giây). Cả hai dùng Chrome profile tạm riêng và kiểm tra đúng hai đáp án, chuyển câu và dừng trước Submit. Nhật ký `status.log` ghi các bước agent và loại lỗi, không ghi ảnh/đề hoặc khóa. Độ đúng khi giải và khả năng nhận diện giao diện thực tế vẫn phụ thuộc model và website.

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

Chuyển card mạng yêu cầu UAC nếu app chưa có quyền admin; đọc card/danh sách Wi-Fi không cần nâng quyền. Menu phân biệt card bật và đã kết nối. Nếu chuyển thất bại, app khôi phục trạng thái card và profile/SSID Wi-Fi cũ; nếu không khôi phục được, card mới được giữ bật để còn đường kết nối và app báo kiểm tra thủ công. Khi mất mạng, mở phần khôi phục mạng trong menu và chọn lại card trong Windows. Thoát trong lúc chuyển mạng sẽ yêu cầu hủy và chờ Windows hoàn tất khôi phục; tray còn hoạt động trong thời gian này. Các test tự động dùng mô phỏng, không chứng minh hoạt động trên mọi phần cứng.

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

Để chạy bản portable ở ổ E và giữ mã nguồn trong thư mục repo hiện tại, build sang thư mục staging rồi chép EXE/runtime/tài liệu sang `E:\ClipboardAI-Windows-x64`; giữ nguyên `api_zoo.json`, `preferences.json`, lịch sử, kho ảnh và thư mục đăng nhập ở E. Đóng bản đang chạy rồi mở lại EXE sau cập nhật. Không chép cấu hình mẫu đè lên cấu hình riêng.

Chẩn đoán API đã lưu ở E: `python scripts/diagnose_providers.py E:/ClipboardAI-Windows-x64`. Script gửi câu OK và ảnh màu giả lập nhỏ, không chụp màn hình hay thao tác desktop, không sửa cấu hình và không in key. Mỗi lượt có timeout 60 giây; báo cáo riêng ở `build/provider-diagnostics.json`. Thêm `--agent --profile TênAPI` để kiểm tra JSON quyết định F2 mà không thực thi thao tác. Lấy được danh sách model không chứng minh endpoint sinh câu trả lời/ảnh đang hoạt động.

Để đóng gói bản thử với đúng tên `ClipboardAI_Region_Test.exe`:

```powershell
./build.ps1 -Name ClipboardAI_Region_Test
```

File ở `dist/ClipboardAI_Region_Test.exe`; dùng cùng mã nguồn và cài đặt như bản chính. `SHA256.txt` ghi mã kiểm tra và tên EXE vừa build.

Nếu EXE cũ đang chạy và bị Windows khóa, build sang thư mục riêng: `./build.ps1 -Name ClipboardAI_Region_Test -OutputDirectory dist/providers`. Đóng bản cũ trước khi mở bản mới; chuyển cấu hình riêng cục bộ nếu muốn dùng lại. Thư mục mới không có sẵn key hoặc phiên đăng nhập.

Build kiểm tra các tệp Tcl/Tk trước khi đóng gói. Nếu runtime bị thiếu hoặc môi trường build chặn quyền đọc, script dừng với lỗi thay vì tạo EXE thiếu cửa sổ chọn vùng/API Zoo/cài đặt phím. Dùng Python có Tcl/Tk và môi trường cho phép đọc các tệp runtime đó.

EXE được kiểm tra manifest `requireAdministrator`, yêu cầu quyền admin ngay khi mở. Helper PowerShell chuyển card mạng kế thừa quyền admin của app để không hỏi UAC thêm trong cùng phiên; mã helper được khóa chống sửa/thay thế cho tới khi thao tác và khôi phục kết thúc.

Kiểm tra chấp nhận cả tệp Tcl/Tk riêng và Tcl/Tk 9 nhúng dữ liệu trong DLL (`//zipfs:/`), nên không yêu cầu danh sách `data_files` phải khác rỗng trong trường hợp nhúng. Workflow kiểm tra thêm EXE bằng `--verify-tk`: khởi tạo Tk/ttk ẩn, xuất phiên bản và đường dẫn thư viện; không mở tray, gửi API hoặc thay clipboard.

## Dữ liệu riêng

API key được bảo vệ bằng Windows DPAPI cho tài khoản và máy hiện tại khi bạn **Lưu** trong API Zoo. File API Zoo cũ chứa key plaintext được chuyển sang dạng bảo vệ ở lần lưu thành công tiếp theo; đọc file không tự sửa cấu hình. Nếu chuyển máy/tài khoản và không giải mã được, giữ bản gốc, đổi tên `api_zoo.json` rồi tạo lại API và nhập key qua API Zoo. Cấu hình cũ trong `.env`/`mirai_config.json` vẫn có thể chứa key dạng plaintext. Lịch sử hỏi đáp trong `session.json` và tùy chọn/prompt trong `preferences.json` vẫn là plaintext. Khi dùng AI, chữ/ảnh được gửi đến endpoint của API/model đã chọn và API dự phòng nếu bật; không đưa dữ liệu nhạy cảm vào yêu cầu nếu không muốn gửi nhà cung cấp đó.

Lưu lịch sử chạy nền theo thứ tự để giao diện tiếp tục phản hồi; file được ghi tạm, đồng bộ xuống đĩa rồi thay thế, và dữ liệu không đổi không bị ghi lại. Khi không ghi được file, đáp án hoàn tất vẫn có thể copy và app báo lỗi lưu; chọn **F6 → Lưu lại lịch sử** sau khi khôi phục quyền ghi/dung lượng. Thoát sẽ chờ lưu xong; nếu lưu thất bại, app giữ mở để bạn lưu lại hoặc copy kết quả.

Giới hạn lịch sử API dùng đúng số ký tự cấu hình trong `SESSION_MAX_CHARS`; câu hỏi hiện tại được trừ khỏi phần dành cho lịch sử. Ảnh lưu theo phiên trong `session-images.sqlite3`, tự khôi phục khi mở lại app. Mở **Ảnh & nội dung chat của phiên…** từ tray hoặc F6 để xem, xóa từng ảnh hoặc toàn bộ ảnh. Giới hạn: 8 ảnh/32 MiB mỗi phiên, cache RAM 64 MiB, dữ liệu ảnh 256 MiB trên đĩa. Ảnh ra khỏi cache RAM có thể nạp lại; ảnh bị loại khỏi kho do giới hạn có cảnh báo để chụp lại. `status.log` xoay vòng khi đạt khoảng 2 MiB và giữ một bản `status.previous.log`; log không chứa câu hỏi, đáp án hay API key.

Để sao lưu, đóng app rồi chép `session.json`, `session-images.sqlite3` và `preferences.json`; giữ chúng cùng thư mục EXE khi phục hồi. Kho ảnh là dữ liệu riêng trên máy, không được mã hóa; chỉ ảnh/chữ cần cho câu hỏi mới được gửi đến API đã chọn. Ảnh từng bị mất khi thoát các bản cũ không thể khôi phục tự động.

Repository chỉ chứa code và cấu hình mẫu trống. `.gitignore` loại key, lịch sử, ảnh, log, EXE và build. Không ép thêm các file riêng bằng `git add -f`. Không đặt key trong source hoặc GitHub Actions; build không cần key.

Đăng nhập OpenAI Browser dùng kho thông tin xác thực do Codex CLI quản lý, ưu tiên keyring của hệ điều hành khi khả dụng và có thể dùng file dự phòng. Các phiên cũ dùng file vẫn được giữ tương thích trong `.clipboardai-auth/<id>` cạnh EXE, đã loại khỏi Git; không chia sẻ thư mục này. ClipboardAI không nhập cookie hoặc kho đăng nhập Codex đang có.

## Quản lý hội thoại, chẩn đoán và riêng tư

Mở tray → **Model / API / cài đặt → Quản lý hội thoại · Chẩn đoán · Riêng tư…**. Tab Hội thoại tìm trong toàn bộ nội dung, mở phiên, đổi tên, xuất Markdown/JSON và sao lưu ZIP gồm lịch sử + kho ảnh. ZIP không gồm khóa API/đăng nhập; đóng app trước khi khôi phục hai file cạnh EXE. File xuất và ZIP chưa mã hóa.

Tab Chẩn đoán hiển thị provider/model thực dùng, trạng thái lỗi an toàn, proxy hệ thống đã bỏ thông tin đăng nhập, số lần gọi và token provider báo từ lúc mở app. Đếm cả lần thất bại, tóm tắt và dự phòng; giữ 200 dòng gần nhất. Lần không có usage được ghi “không báo”, không ước lượng thành token thật. OpenAI Browser hiện chỉ đếm lần gọi, chưa lấy số token. Nút bỏ khóa tạm cho phép thử provider đã chọn ở lần gửi sau; không tự gửi hay thay đổi key. Proxy hiển thị là cấu hình hệ thống; kết nối vẫn áp dụng quy tắc bỏ qua proxy như trước.

Tab Riêng tư mở phiên trống chỉ trong RAM và mặc định tắt tự copy. Ảnh mới được xem/che bằng kéo chuột trước khi lưu RAM và gửi; có thể bật/tắt che ảnh riêng. Bật riêng tư không xóa lịch sử cũ. Tắt sẽ bỏ phiên riêng tư và trở về lịch sử đã lưu; nút dọn RAM mở phiên riêng tư trống mới. Phiên riêng tư không được khôi phục sau khi thoát; khi mở app lại, lịch sử thường vẫn còn. Xuất nội dung riêng tư ra file cần xác nhận riêng, sao lưu ZIP bị tắt trong chế độ này. App vẫn có thể lưu cấu hình, khóa đăng nhập và log trạng thái không chứa câu hỏi/ảnh. Đây không phải chế độ ẩn danh của Windows hay của nhà cung cấp AI: không bảo đảm xóa vật lý mọi bản sao trong RAM/pagefile, clipboard hoặc dữ liệu đã gửi nhà cung cấp.

## Cấu trúc

Chat và các bảng ảnh/hội thoại mặc định 420 × 560 px, mở ở góc dưới bên phải màn hình đang có con trỏ, trong vùng làm việc để tránh thanh tác vụ. Kéo cạnh để đổi kích thước. Vào **Cài đặt → Hội thoại / Riêng tư → Giao diện** để chỉnh chiều ngang/cao, đổi vị trí sang giữa màn hình, hoặc **Lấy kích thước đang kéo → Lưu kích thước và vị trí**. Lựa chọn lưu trong `preferences.json`, dùng khi mở cửa sổ lần sau và sau khi khởi động lại. Chiều ngang 360–1600 px, chiều cao 480–1400 px; cửa sổ được giới hạn theo diện tích màn hình.

`src/`: ứng dụng và test. `scripts/run_tests.py`: chạy test không gọi API thật. `build.ps1`: đóng gói EXE kèm Tkinter cho API Zoo/prompt editor. `.github/workflows/build-windows.yml`: build tự động. `requirements*.txt`: dependency đã cố định phiên bản.

Cửa sổ **Ảnh & nội dung chat của phiên…** có tab ảnh và tab nội dung hội thoại cùng phiên, kèm nút mở chat để hỏi tiếp. Menu copy theo phiên có mũi tên; chọn tên phiên sẽ chuyển phiên và copy đáp án cuối của phiên đó, không gọi AI. F7 copy đáp án gần nhất, Shift+F7 copy phiên đang chọn; F8 vẫn gửi bài mới.

Thử cuộn trang dài bằng `ClipboardAI.exe --verify-agent-long-controlled`: sáu câu trên một trang nhiều màn hình, kiểm tra độc lập tất cả đáp án, đã cuộn đến cuối và chưa nộp. Chế độ này dùng model giả lập; thử model thật từ source bằng `verify_desktop_agent.run(..., model="live", layout="long", questions=6)`.
