# SKILL — Page Watcher (Theo dõi trang tự động)

## Mô tả
Tính năng **Theo dõi Trang** cho phép tự động kiểm tra một URL định kỳ (VD: mỗi 6 tiếng), nếu có bài viết mới xuất hiện thì tự động chạy pipeline Cào → AI Biên tập → Đăng lên WordPress (kèm thumbnail + category).

## Khi nào dùng
- User nói "theo dõi", "monitor", "watch", "giám sát" một trang web
- User muốn "tự động đăng bài mới" từ một nguồn lên website
- User hỏi "đang theo dõi trang nào?", "danh sách watch"
- User muốn "dừng theo dõi", "xoá watch"
- User muốn "chỉnh sửa", "cập nhật", "thay đổi" cấu hình watch (instruction, interval, category...)

## Cách kích hoạt (AI OUTPUT JSON)

### 1. Bắt đầu theo dõi một trang:
Khi user yêu cầu theo dõi / giám sát / monitor một URL. Bạn trích xuất URL, khoảng cách thời gian (mặc định 6h), tên website đích, lệnh xử lý nội dung, và tên category nếu user chỉ định.
```json
{
  "action": "watch_page",
  "url": "https://vnexpress.net/the-gioi",
  "interval_hours": 6,
  "target_site": "miniaturefood",
  "instruction": "dịch sang tiếng anh",
  "max_articles_per_check": 5,
  "category_name": "World News"
}
```

### 2. Chỉnh sửa / cập nhật watch:
Khi user muốn thay đổi cấu hình watch đang chạy (ví dụ: đổi instruction, interval, category). Chỉ cần gửi URL và các field muốn thay đổi.
```json
{
  "action": "update_watch",
  "url": "https://vnexpress.net/the-gioi",
  "instruction": "biên tập lại bằng tiếng anh, giữ nguyên ý chính"
}
```
Các field có thể cập nhật: `instruction`, `interval_hours`, `target_site`, `max_articles_per_check`, `category_name`.

### 3. Dừng/xoá theo dõi:
```json
{"action": "unwatch_page", "url": "https://vnexpress.net/the-gioi"}
```

### 4. Xem danh sách đang theo dõi:
```json
{"action": "list_watches"}
```

## Lưu ý
- `interval_hours` mặc định 6 nếu user không nói rõ. Nếu user nói "mỗi 12 tiếng" → interval_hours = 12.
- `max_articles_per_check` mặc định 5 — giới hạn số bài xử lý mỗi lần check.
- `category_name` là TÊN category trên WordPress (VD: "World News", "Tin thế giới"). Nếu chưa tồn tại sẽ TỰ ĐỘNG tạo mới. Category ID sẽ được cache lại cho các bài tiếp theo.
- Lần check đầu tiên sẽ ghi nhận danh sách bài hiện tại (snapshot), chỉ từ lần sau mới phát hiện bài MỚI.
- **Thumbnail**: Hệ thống tự động lấy ảnh đầu tiên trong bài viết nguồn và upload làm Featured Image trên WordPress.
- Kết quả sẽ tự động thông báo về Telegram sau mỗi lần check.
