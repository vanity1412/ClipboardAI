# Nhà cung cấp, model và đăng nhập

Trong **Model / API / cài đặt → Quản lý API Zoo**, bấm **+ API**, chọn nhà cung cấp, nhập thông tin rồi **Lưu**. Ô model vừa có danh sách chọn vừa cho nhập ID riêng. Ô **Model đọc ảnh** dùng cho F4/Shift+F9; để trống khi API chỉ hỗ trợ chữ. Model ảnh có thể khác model trả lời.

| Mẫu | Kết nối | Gợi ý ban đầu |
|---|---|---|
| OpenAI · API key | OpenAI Responses; API key | gpt-6.1-sol, gpt-6-astra, gpt-6-luna, gpt-5.4 |
| OpenAI Browser · ChatGPT | Đăng nhập trình duyệt qua Codex CLI chính thức | Lấy từ Codex sau khi đăng nhập |
| Claude · Anthropic | Anthropic Messages; API key | claude-sonnet-5-5, claude-opus-5-5, claude-fable-5-1, claude-haiku-4-5-20251001 |
| DeepSeek | DeepSeek Chat Completions; API key | deepseek-flash, deepseek-v4-pro |
| Grok · xAI | OpenAI-compatible Chat Completions; API key | grok-4.7 |
| Gemini · Google | Gemini OpenAI-compatible; API key | gemini-3.8-flash |
| Custom · OpenAI-compatible | `/chat/completions`; Bearer key | Nhập ID hoặc lấy từ API |
| Custom · Anthropic-compatible | `/messages`; x-api-key và anthropic-version | Nhập ID hoặc lấy từ API |
| Custom · OpenAI Responses | `/responses`; Bearer key | Nhập ID hoặc lấy từ API |

Gợi ý được đối chiếu ngày **2026-10-05**, có thể thay đổi theo nhà cung cấp. **Lấy lại model** thay danh sách gợi ý bằng danh sách API hiện tại; Anthropic và Codex có phân trang. Model liệt kê không chứng minh quota hoặc quyền gọi. Có thể nhập ID riêng nếu gateway không có `/models`; API sẽ kiểm tra ID khi gửi yêu cầu. Thay host endpoint sẽ xóa key cũ để bạn nhập key cho host mới. Đổi mẫu luôn xóa key trong form.

Endpoint custom là base URL, ví dụ `https://gateway.example/v1`, không thêm `/messages`, `/responses` hay `/chat/completions` vào cuối. Chỉ nhận HTTPS cổng 443; không gửi key qua redirect.

## OpenAI Browser

1. Cài [Codex CLI chính thức](https://developers.openai.com/codex/cli), hoặc dùng CLI có sẵn trong ứng dụng Codex Windows. App tự tìm `codex.exe` trong PATH hoặc thư mục cài Codex trên Windows; không tự tải/cài CLI.
2. Chọn **OpenAI Browser · ChatGPT → Đăng nhập ChatGPT**. Hoàn tất đăng nhập trên trình duyệt trong tối đa 5 phút. Nút **Hủy**, đổi API hoặc đóng cửa sổ sẽ hủy thao tác đang chờ.
3. Chọn model lấy được sau đăng nhập, chọn model đọc ảnh nếu cần, rồi **Lưu**. F8/F4/F9 dùng phiên này như những API khác. Quyền dùng model và giới hạn do gói ChatGPT/Codex quyết định.
4. **Lấy lại model** kiểm tra phiên và tải model hiện tại; **Đăng xuất** đăng xuất phiên riêng của API đang chọn. Nếu CLI cũ không hỗ trợ app-server, cập nhật CLI theo hướng dẫn chính thức.

Codex CLI thực hiện OAuth, lưu và làm mới token. App không lấy cookie hoặc phiên từ trình duyệt, không nhập kho đăng nhập Codex hiện có. Mỗi API Browser có thư mục riêng `.clipboardai-auth/<id>` cạnh EXE; thư mục này chứa dữ liệu xác thực nhạy cảm và đã được `.gitignore` loại khỏi Git. App không đọc token để gọi endpoint nội bộ; CLI thực hiện yêu cầu AI qua app-server.

Mỗi yêu cầu tạo thread Codex tạm thời, đưa lịch sử ClipboardAI và ảnh vào yêu cầu, dùng thư mục làm việc trống và sandbox chỉ đọc. Shell, web search, apps và các tính năng agent được tắt trong cấu hình CLI; yêu cầu công cụ/phê duyệt không được app đáp ứng. Nếu CLI/model yêu cầu công cụ, app báo lỗi và giữ clipboard. Chỉ câu trả lời của turn đã hoàn tất mới được chấp nhận; các phần commentary không được copy thành đáp án. Giới hạn token của Browser do Codex/model quản lý; timeout và F10 được ClipboardAI theo dõi.

Claude, DeepSeek, Grok và Gemini trong bản này dùng **API key**. Nút **Mở trang API key** mở trang quản lý key để đăng nhập/tạo key; nó không biến phiên web của các hãng thành phiên API. OpenCode hiện tích hợp đăng nhập ChatGPT, còn đăng nhập Claude Pro/Max qua plugin đã không còn được tích hợp sẵn. Bản này dùng các giao thức chính thức, không sao chép plugin OAuth không được nhà cung cấp hỗ trợ.

## Phản hồi và dữ liệu

OpenAI-compatible, Anthropic Messages và Responses hỗ trợ chữ, ảnh và streaming. Thiếu sự kiện hoàn tất, lỗi mạng, hết token, từ chối hoặc yêu cầu công cụ đều không được xem là đáp án hoàn tất. Anthropic yêu cầu `max_tokens`; nếu không đặt giới hạn riêng, app dùng 8192. Responses dùng `max_output_tokens` khi có giới hạn và đặt `store=false`.

Tự chuyển API có thể gửi cùng nội dung đến nhà cung cấp dự phòng đã bật. Hủy yêu cầu dừng chuyển API. API key trong `api_zoo.json` và dữ liệu phiên đăng nhập cục bộ cần được giữ riêng khi chia sẻ thư mục app.

## Nguồn tham khảo

- [OpenCode Providers](https://opencode.ai/docs/providers/): bố cục provider/model, ChatGPT browser login và trạng thái tích hợp Claude.
- [Codex app-server](https://learn.chatgpt.com/docs/app-server): initialize, account/login/start, account/read, account/logout, model/list, thread/start, turn/start và thông báo hoàn tất.
- [OpenAI models](https://developers.openai.com/api/docs/models) và [Codex authentication](https://developers.openai.com/codex/auth).
- [Claude models](https://platform.claude.com/docs/en/models/overview), [Messages](https://platform.claude.com/docs/en/api/messages/create), [Models API](https://platform.claude.com/docs/en/api/models), [streaming](https://platform.claude.com/docs/en/build-with-claude/streaming).
- [DeepSeek Codex integration](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/): alias DeepSeek hiện dùng.
- [xAI models](https://docs.x.ai/developers/models) và [Chat Completions](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions).
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai).

Code được viết trong repo này, không sao chép mã nguồn OpenCode. Kiểm thử dùng HTTP localhost, dữ liệu giả, đăng nhập mô phỏng và CLI chưa đăng nhập với kho tạm. Không gọi AI trả phí hoặc dùng tài khoản thật trong các kiểm thử này.
