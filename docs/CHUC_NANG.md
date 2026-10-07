# Chức năng ClipboardAI / ClipboardAI_Region_Test

Hai tên EXE dùng cùng mã nguồn trong `src`. Bảng dưới là bộ phím mặc định; sau khi đổi phím, menu hiển thị tổ hợp mới. F7 không được đăng ký để mở/ẩn chat. Chat vẫn mở được chủ động qua menu tray hoặc danh sách hội thoại.

## Phím và thao tác

| Phím/thao tác | Chức năng |
|---|---|
| F8 | Gửi chữ clipboard thành câu hỏi mới, không dùng lịch sử trước |
| F4 | Kéo chọn vùng ảnh thành câu hỏi mới; có thể đổi sang chụp cả cửa sổ |
| F9 | Gửi chữ clipboard để hỏi tiếp/bổ sung vào phiên hiện tại |
| Shift+F9 | Chọn vùng ảnh bổ sung vào phiên hiện tại |
| F10 | Hủy yêu cầu AI hoặc chọn vùng; không copy đáp án dở |
| Shift+F10 | Gửi lại yêu cầu đã lưu, không đọc câu hỏi mới từ clipboard |
| Shift+F8 | Copy đáp án hoàn tất gần nhất, không gọi AI lại |
| F6 | Mở danh sách hội thoại và thao tác quản lý phiên |
| F3 hai lần nhanh | Mở menu chọn card mạng/Wi-Fi |
| Esc hoặc chuột phải khi chọn vùng | Hủy chọn vùng, giữ clipboard và phiên |
| Enter trong ô chat | Gửi nội dung vào phiên hiện tại |
| Shift+Enter trong ô chat | Xuống dòng |
| Esc trong chat | Ẩn cửa sổ chat, không hủy AI |
| Rê chuột vào icon tray | Xem trạng thái, thời gian xử lý, đáp án hoặc lỗi |

## Menu chuột phải icon tray

```text
ClipboardAI
├─ Trạng thái hiện tại
├─ Mở chat
├─ Hội thoại / thao tác phiên — F6
├─ Hỏi tiếp từ clipboard — F9
├─ Copy đáp án của phiên đang chọn
├─ Copy đáp án gần nhất — Shift+F8
├─ Gửi lại — Shift+F10
├─ Mạng · Wi-Fi / LAN
│  ├─ Chọn mạng · Wi-Fi / LAN — F3×2
│  │  ├─ Card Wi-Fi
│  │  ├─ Card LAN / Ethernet
│  │  ├─ Chọn mạng Wi-Fi theo tên SSID
│  │  ├─ Thiết lập Wi-Fi trong Windows
│  │  └─ Mở card mạng để khôi phục thủ công
│  └─ Mở card mạng để khôi phục thủ công
├─ Chụp ảnh
│  ├─ Chụp câu hỏi mới — F4
│  ├─ Bổ sung ảnh vào phiên — Shift+F9
│  ├─ Kéo chọn vùng
│  ├─ Chụp cả cửa sổ
│  ├─ Dấu chọn: bốn góc nhạt
│  ├─ Dấu chọn: viền rõ hơn
│  ├─ Chỉ ảnh · không gửi clipboard
│  └─ Ảnh + clipboard
├─ Chế độ trả lời
│  ├─ Hỏi đáp / phân tích
│  ├─ Lập trình
│  ├─ Suy luận → Nhanh / Suy nghĩ kỹ
│  ├─ Prompt đang dùng → Mặc định / Theo yêu cầu / Prompt đã lưu
│  ├─ Sửa prompt hiện tại
│  └─ Tự copy câu trả lời
├─ Hủy yêu cầu — F10 [khi AI đang chạy]
├─ Model / API / cài đặt
│  ├─ Quản lý API Zoo
│  ├─ Chọn model → tên API → danh sách model
│  ├─ Tạm dừng / trả phím cho ứng dụng khác [hoặc Bật lại phím tắt]
│  ├─ Cài đặt / kiểm tra kết nối
│  └─ Cài đặt phím tắt…
└─ Thoát
```

Danh sách API, model, card mạng và Wi-Fi phụ thuộc cấu hình và máy. Nếu model không hỗ trợ điều khiển suy luận, menu hiện “Model quyết định” và khóa Nhanh/Suy nghĩ kỹ. Chụp ảnh cần API/model nhận ảnh.

## Hội thoại và mạng

F6 có **Hội thoại mới từ clipboard** và **Tạo phiên trống**. Mỗi phiên có **Chọn phiên**, **Mở chat**, **Gửi lại yêu cầu**, **Copy câu trả lời**, **Xóa ảnh đã thu thập**, **Lưu lại lịch sử**, **Xóa phiên**, **Quản lý / xem ảnh…**. Danh sách hiển thị tên và thời điểm cập nhật phiên. Menu tray cũng có quản lý ảnh của phiên hiện tại.

Danh sách Wi-Fi theo SSID hiện tên mạng, phần trăm tín hiệu, đã lưu/chưa lưu và trạng thái kết nối. Có **Làm mới danh sách** và **Thiết lập trong Windows**. Mạng chưa lưu chuyển sang Windows để nhập mật khẩu. EXE chạy bằng quyền người dùng thông thường; Windows chỉ hỏi UAC khi cần bật/tắt card mạng. Chọn card có thể tắt các card Wi-Fi/LAN khác. Đọc danh sách không nâng quyền. Có mục mở card mạng để khôi phục thủ công.

Nếu chuyển mạng lỗi, app khôi phục trạng thái bật/tắt và profile/SSID của các card Wi-Fi trước đó; Wi-Fi vốn chưa kết nối được đưa lại về trạng thái chưa kết nối. Nếu khôi phục không thành công, app giữ card mới bật và báo kiểm tra thủ công. Thoát trong lúc chuyển mạng gửi yêu cầu hủy và chờ khôi phục xong; tray tiếp tục hiện trạng thái để thao tác Windows không bị bỏ dở. Kiểm tra kết nối dùng HTTPS và proxy của người dùng, không gửi API key.

## Các cửa sổ mở chủ động

| Cửa sổ | Các thao tác |
|---|---|
| Chat — mở qua menu | Xem hội thoại, nhập câu hỏi, Gửi, Hủy, Copy kết quả, Phiên mới, chọn chế độ, tự copy, Menu |
| API Zoo | Mẫu OpenAI/Claude/DeepSeek/Grok/Gemini và custom; gợi ý/nhập model; model đọc ảnh; đăng nhập/hủy/đăng xuất ChatGPT qua Codex CLI; sửa API/key; lấy lại model; tự chuyển API; Lưu |
| Sửa prompt | Chọn mục đích, prompt mặc định/theo yêu cầu/đã lưu, sửa nội dung, lưu cho chế độ đó |
| Cài đặt | Giới hạn lịch sử; token/timeout khi cấu hình hỗ trợ; lưu cấu hình; kiểm tra kết nối; tạm dừng; xóa lịch sử; **Cài đặt phím tắt** |
| Cài đặt phím tắt | Bật/tắt từng chức năng, nhập/bấm tổ hợp mới, kiểm tra trùng/bị chiếm, khôi phục mặc định, Lưu |

Mở cài đặt phím bằng **chuột phải icon tray → Model / API / cài đặt → Cài đặt phím tắt…**, hoặc bấm **Cài đặt phím tắt** trong **Cài đặt / kiểm tra kết nối**. Tổ hợp hỗ trợ F1–F24 hoặc chữ/số kèm Ctrl, Alt, Win; các tổ hợp dành riêng được kiểm tra trước khi lưu. Có thể nhập trực tiếp `Ctrl+Alt+Q` hoặc `Win+Q`.

Khi cửa sổ đổi phím mở, app tạm trả phím để tránh gửi nhầm câu hỏi. Bấm **Lưu**, rồi đóng cửa sổ để dùng phím mới. Phím trùng giữa chức năng đang bật hoặc bị ứng dụng khác chiếm không được lưu; cấu hình trước được giữ lại. Việc kiểm tra phím bị chiếm áp dụng cả khi đang Tạm dừng. Đóng cửa sổ sẽ đăng ký lại phím nếu app đang bật; nếu trước đó Tạm dừng thì vẫn tạm dừng. **Tạm dừng không hủy yêu cầu AI đang chạy.**

Bỏ chọn **Bật** để trả riêng phím đó; tổ hợp đã nhập vẫn được nhớ. **Khôi phục mặc định** điền lại bộ phím và bật các chức năng, cần bấm Lưu. Phím chat cũ trong cấu hình được bỏ qua; các phím tùy chỉnh khác được giữ. Lựa chọn lưu trong `preferences.json` cạnh EXE.

## Chụp ảnh, clipboard và đáp án

Luồng dùng: **copy/chọn ảnh → gửi → chờ hoàn tất → Ctrl+V**. F4/F8 bắt đầu câu hỏi mới; F9/Shift+F9 dùng dữ kiện trước. Shift+F9 chỉ bổ sung ảnh, không kèm chữ clipboard.

Chọn vùng dùng ảnh cửa sổ được lấy trước khi bộ chọn mở. Kéo chuột và thả để gửi; Esc, chuột phải hoặc phím Hủy đã cấu hình hủy chọn. Clipboard đổi trong lúc chọn hoặc chờ AI được bảo vệ; dùng **Shift+F8** hoặc menu Copy để lấy đáp án hoàn tất. Không có cửa sổ chat tự bật khi trả lời.

Gửi lại một lượt hỏi tiếp bị hủy/lỗi giữ lịch sử đã hoàn tất trước đó, kể cả khi nội dung hỏi tiếp trùng câu cũ. Gửi lại lượt đã hoàn tất thay đáp án của lượt đó khi thành công. Phản hồi thiếu kết thúc, bị cắt do token, từ chối hoặc yêu cầu công cụ không được coi là đáp án hoàn tất để tự copy; phần thông báo đang xử lý của model cũng không được ghép vào đáp án cuối.

Trắc nghiệm giữ nhãn của đề. Nếu đề không có nhãn, số đáp án là thứ tự phương án từ trên xuống; `Câu 1: 2` nghĩa là phương án thứ hai. Điền khuyết trả phần cần điền. Câu thiếu dữ kiện hoặc không đọc rõ được đánh dấu “Chưa xác định”. Prompt riêng có thể thay đổi cách trả lời.

Ô rê chuột hiện đáp án gọn, câu chưa xác định và lỗi. Nhiều đáp án được chia cột/trang, tự chuyển trang sau khoảng **5 giây khi đang rê chuột**. Clipboard giữ nội dung câu trả lời đầy đủ.

Lịch sử chữ lưu trong `session.json`. Việc ghi file chạy nền theo thứ tự, đồng bộ xuống đĩa rồi thay thế file đích; dữ liệu không đổi không bị ghi lại. Nếu lưu thất bại, app giữ đáp án hoàn tất để copy và báo lỗi. Dùng **F6 → Lưu lại lịch sử** khi thư mục có thể ghi lại. Thoát chờ lưu xong; nếu chưa lưu được, app giữ mở để tránh mất kết quả.

Giới hạn lịch sử API dùng đúng số ký tự đặt trong cài đặt/`SESSION_MAX_CHARS`, trừ phần câu hỏi hiện tại trước khi lấy hoặc rút gọn lịch sử. Giới hạn này không phải số token của nhà cung cấp và không xóa lịch sử đã lưu trên đĩa. `status.log` xoay vòng ở khoảng 2 MiB, giữ một bản trước trong `status.previous.log`; log không ghi câu hỏi, đáp án hoặc API key.

Ảnh lưu riêng theo phiên trong `session-images.sqlite3`, bằng transaction SQLite và đồng bộ xuống đĩa trước khi gửi AI. Mở lại app hoặc chuyển phiên tự nạp lại đúng ảnh. Kho ảnh giữ tối đa 8 ảnh/32 MiB mỗi phiên và 256 MiB dữ liệu ảnh tổng; cache RAM tối đa 64 MiB. Cache có thể nạp lại từ đĩa. Nếu kho đầy, ảnh ở các phiên cũ bị loại và thông báo số ảnh thiếu được giữ qua restart; câu hỏi tiếp có thông tin thiếu ảnh để model không suy đoán.

**Quản lý / xem ảnh…** có xem trước, xóa ảnh đang chọn và xóa tất cả ảnh của phiên; xóa cần xác nhận, không xóa lịch sử chữ. Đóng cửa sổ quản lý trước khi gửi/chụp câu hỏi mới. Kho ảnh hỏng hoặc không ghi được sẽ báo lỗi và giữ file cũ, không tự ghi đè. Đóng app rồi sao lưu `session.json`, `session-images.sqlite3` và `preferences.json` cùng nhau. Kho ảnh không mã hóa; `.gitignore` loại database và file journal khỏi source.

## Mã nguồn theo chức năng

| Tệp trong `src` | Trách nhiệm |
|---|---|
| `deepseek_flash_entry.py`, `default_config.py` | Khởi chạy bản công khai và nạp cấu hình/key cục bộ |
| `windows_native.py` | Tray, chat, cài đặt, phím global, clipboard, điều phối yêu cầu, trạng thái và menu |
| `hotkey_settings.py`, `runtime_settings.py` | Cửa sổ đổi phím, kiểm tra tổ hợp, bật/tắt từng phím, lưu/đọc tùy chọn |
| `region_capture.py`, `screen_capture.py` | Chọn vùng, tọa độ màn hình, chụp cửa sổ, PNG trong bộ nhớ |
| `session_state.py`, `session_images.py`, `conversation_memory.py` | Hội thoại, lưu/khôi phục, ảnh theo phiên và rút gọn ngữ cảnh gửi API |
| `api_zoo.py`, `api_zoo_ui.py` | Quản lý API, lấy model, chọn API/model và chuyển API khi lỗi |
| `provider_catalog.py`, `provider_protocols.py`, `browser_provider.py` | Mẫu/gợi ý model, Anthropic/Responses, đăng nhập ChatGPT qua Codex app-server |
| `cloud_client.py`, `deepseek_client.py` | Gửi chữ/ảnh tới API và kiểm tra phản hồi hoàn chỉnh |
| `http_transport.py`, `credential_storage.py` | Kết nối HTTP/streaming có proxy, timeout/hủy và bảo vệ key API Zoo bằng Windows DPAPI |
| `chat_modes.py`, `coding_prompt.py`, `prompt_profiles.py`, `prompt_editor.py` | Mục đích trả lời, suy luận và prompt theo chế độ |
| `answer_policy.py`, `compact_preview.py`, `hover_layout.py`, `request_display.py` | Chính sách đáp án, trạng thái, bản xem gọn và chuyển trang khi rê chuột |
| `network_switch.py`, `wifi_networks.py` | Card mạng, SSID, kết nối và khôi phục mạng |
| `secret_policy.py`, `usb_setup.py` | Kiểm tra dữ liệu cấu hình và tiện ích thiết lập portable |
| `tests/`, `verify_*.py` | Kiểm thử hồi quy và công cụ kiểm tra giao diện/luồng |

Build bản chính: `./build.ps1`. Build đúng tên bản thử: `./build.ps1 -Name ClipboardAI_Region_Test`. Kết quả nằm trong `dist` cùng README, tài liệu này, cấu hình mẫu trống và SHA256. Repo chứa toàn bộ mã công khai, test và workflow build; không chứa API key, lịch sử cá nhân hoặc EXE build cục bộ.

Chi tiết provider, giao thức custom và đăng nhập: [API_PROVIDERS.md](API_PROVIDERS.md). F7 đã bỏ; mở chat từ tray. Các provider API dùng key; riêng OpenAI Browser có luồng đăng nhập ChatGPT tích hợp qua Codex CLI chính thức.
# Nâng cấp quản lý và riêng tư

Tray → **Hội thoại · Chẩn đoán · Riêng tư…**:

- Hội thoại: tìm toàn bộ nội dung, đổi tên, mở phiên, xuất MD/JSON, sao lưu ZIP gồm lịch sử và ảnh (không gồm key). Đóng app trước khi giải nén hai file dữ liệu cạnh EXE để khôi phục.
- Chẩn đoán: provider/model thực dùng, lỗi theo mã an toàn, proxy không chứa mật khẩu; đếm lần gọi gồm tóm tắt, lỗi và dự phòng; token chỉ cộng khi provider báo đủ. Thống kê trong RAM từ lúc mở app, tối đa 200 dòng; OpenAI Browser chưa báo token.
- Riêng tư: mở phiên mới chỉ RAM, mặc định không tự copy; tắt/dọn sẽ bỏ phiên RAM. Không xóa lịch sử cũ. Khi khởi động lại, app mở lịch sử thường.
- Che ảnh: kéo vùng đen trước khi gửi; chỉ bản đã che được lưu/gửi. Đóng cửa sổ hoặc Hủy gửi sẽ hủy yêu cầu.
- Quản lý ảnh ghi số trang trong danh sách ảnh đang giữ, cho xem trước và xóa; cảnh báo ảnh bị loại vẫn giữ.

Nội dung xuất và ZIP chưa mã hóa. Riêng tư không xóa clipboard, lịch sử cũ, thông tin đăng nhập hoặc dữ liệu đã gửi AI; dọn bộ nhớ của app không bảo đảm xóa vật lý mọi bản sao RAM/pagefile.
