# Mini Shop - bản bổ sung hoa hồng sàn

## Công nghệ
- Python Flask
- MySQL 8.0.16+
- HTML, CSS, Bootstrap, JavaScript

## Chạy lần đầu
1. Tạo database `ecommerce_db` trong MySQL.
2. Chạy toàn bộ file `schema.sql` trên database vừa tạo.
3. Sao chép `.env.example` thành `.env`, cập nhật mật khẩu MySQL và `SECRET_KEY`.
4. Cài thư viện: `pip install -r requirements.txt`.
5. Chạy: `python app.py`.
6. Mở `http://127.0.0.1:5000`. Trong mạng LAN, mở `http://IP_MAY_CHU:5000`.

## Tài khoản dữ liệu mẫu
- Admin: `admin` / `admin123`
- Khách hàng: `khachhang01` / `123456`
- Chủ shop: `nguoiban01` / `123456`

Đổi mật khẩu mẫu ngay khi triển khai.

## Cơ chế hoa hồng
- Tỷ lệ mặc định: 5%, có thể thay đổi tại trang Admin.
- Tỷ lệ được chụp (snapshot) vào từng dòng `order_details` khi đặt hàng; thay đổi cấu hình không tác động đơn cũ.
- Hoa hồng tính trên giá trị sản phẩm sau khi phân bổ giảm giá, không tính phí vận chuyển.
- Chỉ khi đơn chuyển sang `Da giao`, hệ thống mới tạo `commission_records` và cộng tiền shop vào `seller_wallet`.
- `commission_records.order_detail_id` là UNIQUE, ngăn ghi nhận hoa hồng hai lần.
- Khi shop gửi yêu cầu rút, số tiền được giữ khỏi số dư khả dụng. Admin duyệt hoặc từ chối; từ chối sẽ hoàn tiền vào ví.
- Bản demo chưa tích hợp API ngân hàng. Admin cần thực hiện chuyển khoản ngoài hệ thống sau khi duyệt.

## Nâng cấp từ database cũ
File `app.py` có hàm `init_system()` để bổ sung các bảng/cột hoa hồng khi chạy trực tiếp bằng `python app.py`. Với database mới, ưu tiên chạy `schema.sql`.
Nếu đang chạy bằng Gunicorn, cần chạy migration một lần trước khi khởi động worker vì `init_system()` hiện được gọi trong khối chạy trực tiếp.

## Lưu ý trước khi triển khai thật
- Đặt `FLASK_DEBUG=0`, dùng `SECRET_KEY` ngẫu nhiên dài.
- Dùng HTTPS, tài khoản MySQL riêng với quyền tối thiểu.
- Bổ sung quy trình hoàn tiền/đổi trả và đối soát ngân hàng.
- Không xem việc Admin duyệt yêu cầu rút là bằng chứng chuyển khoản đã hoàn tất; cần lưu mã giao dịch ngân hàng trong phiên bản vận hành thật.
