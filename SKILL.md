# SKILL.md — Web Crawler Extension

## Mô tả
Extension **Web Crawler** cho phép bóc tách thông tin (Text & ẢNh) từ các bài báo, dùng AI để biên tập (dịch, viết lại) và tự động đăng tải lên Website WordPress.

## Khi nào dùng
- User gửi yêu cầu "crawl website", "lấy nội dung trang web", "cào dữ liệu".
- User yêu cầu "dịch bài [URL] sang tiếng anh và upload lên website [Tên Website]".
- User muốn lấy bài viết từ một đường link URL báo chí, xử lý và bắn lên WordPress.

## Cách kích hoạt (AI OUTPUT JSON)

### 1. Chỉ cào dữ liệu thông thường:
```json
{"action": "crawl_website", "url": "https://example.com"}
```

### 2. Tự động hóa: Cào dữ liệu -> AI Biên tập -> Publish WordPress
Nếu User yêu cầu cả việc xử lý nội dung (dịch/rewrite) VÀ đăng bài lên website đích. Bạn TÓM TẮT yêu cầu xử lý (ví dụ "dịch sang tiếng anh") vào "instruction", URL vào "url" và từ khoá tên website đích (ví dụ "miniaturefood.net") vào "target_site".
```json
{
  "action": "crawl_and_publish", 
  "url": "https://vnexpress.net/...",
  "instruction": "dịch sang tiếng anh",
  "target_site": "miniaturefood"
}
```

### 3. Thêm cấu hình WordPress Site:
Nếu người dùng cung cấp thông tin đăng nhập website WordPress (URL, username, app password) để lưu cấu hình cho việc đăng bài tự động sau này:
```json
{
  "action": "add_wp_site",
  "name": "Tên gợi nhớ (tuỳ chọn)",
  "url": "https://example.com",
  "user": "admin",
  "pass": "xxxx yyyy zzzz aaaa"
}
```

## Lưu ý Quan trọng
Trong lệnh `crawl_and_publish`, bạn KHÔNG tự làm công việc dịch thuật!. Bạn là người điều phối (Orchestrator). Nhiệm vụ của bạn chỉ là output JSON với action `crawl_and_publish` và truyền dải `instruction` (lệnh) xuống cho Backend tự giải quyết. Backend sẽ sử dụng API của Web Crawler để gọi một model phụ bên dưới để dịch bài! Nếu Backend trả về lỗi do chưa có thông tin WordPress, hãy hướng dẫn người dùng cung cấp tài khoản ngay trong chat.
