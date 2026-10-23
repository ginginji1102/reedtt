# Novel Box – Tiện ích đọc trang đã hiển thị (v1.1.0)

Trang GitHub Pages không thể tự đọc DOM của website khác vì chính sách bảo mật trình duyệt (CORS/same-origin). Tiện ích này mở tab nền của Gia Tộc Vượng Tài, đợi trang tải JavaScript và đọc văn bản công khai trong `.reader-demo-body .chapter-paragraph`.

## Cài đặt trên Chrome / Cốc Cốc
1. Tải mã nguồn Novel Box ở https://github.com/yuu110299/NovelBox/archive/refs/heads/main.zip rồi giải nén.
2. Mở `chrome://extensions` (Cốc Cốc: trang quản lý tiện ích), bật **Chế độ dành cho nhà phát triển**.
3. Chọn **Tải tiện ích đã giải nén**, trỏ đến thư mục `extension`.
4. Nếu đã cài phiên bản cũ, bấm **Tải lại** tiện ích (hoặc gỡ và cài lại).
5. Tải lại https://yuu110299.github.io/NovelBox/ rồi dán link trang truyện.

Tiện ích mở từng chương trong tab nền, lấy nội dung đang hiển thị và đóng tab. Không vượt qua đăng nhập, chương khóa, CAPTCHA hoặc giới hạn truy cập của website. Nếu danh sách chương được phân trang/ẩn sau thao tác, có thể cần bổ sung xử lý riêng. Mỗi chương có thể mất vài giây.
