# Autonomous Web Crawler Extension for TubeCLI

Trích xuất tự động dữ liệu từ bất cứ website nào và tự động đăng tải (Auto-publish) kết quả lên blog / WordPress thông qua API.

## 🌟 Tính năng chính
- Thu thập dữ liệu thông minh không giới hạn qua script.
- Hỗ trợ Auto-Publisher cho WordPress (viết blog, gắn tag, up ảnh cover).
- Quản lý danh sách website cần crawl theo luồng của Agent.

## 🚀 Hướng dẫn cài đặt

Hệ thống yêu cầu phải cài đặt core [TubeCLI](https://github.com/tubecreate/tubecli) trước.

### Cách 1: Cài đặt trực tiếp (Khuyên dùng)
Bạn có thể tự động cài thông qua CLI có sẵn:
`ash
tubecli ext install https://github.com/tubecreate/tubecli-ext-web-crawler.git
`

### Cách 2: Clone thủ công dành cho Developer
`ash
# 1. Di chuyển vào thư mục lưu trữ
cd path/to/tubecli/data/extensions_external

# 2. Clone repository bằng git
git clone https://github.com/tubecreate/tubecli-ext-web-crawler.git web_crawler

# 3. Kích hoạt extension để nạp core
tubecli ext enable web_crawler
`

## 📖 Cách hoạt động
Cấu hình URL mục tiêu trong Dashboard hoặc gửi link cho Bot, hệ thống sẽ tự đọc, phân tích nội dung (LLM) và đăng lên WordPress tùy biến.

---
*Phát triển bởi đội ngũ TubeCreate.*
