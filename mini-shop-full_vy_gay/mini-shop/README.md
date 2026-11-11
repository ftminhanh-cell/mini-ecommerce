# Mini Shop — Multi-vendor E-commerce

Ứng dụng thương mại điện tử nhiều người bán, xây dựng bằng Flask + MySQL 8 + HTML/CSS/JavaScript/Bootstrap.

## 1. Chức năng hiện có trong source

### Khách hàng
- Đăng ký, đăng nhập, đăng xuất; hồ sơ, đổi mật khẩu, avatar.
- Tìm kiếm, lọc/sắp xếp, xem chi tiết sản phẩm và biến thể.
- Giỏ hàng, yêu thích, voucher, đặt hàng nhiều shop (tách đơn theo shop).
- Theo dõi đơn hàng, hủy đơn khi chưa thanh toán, mua lại, đánh giá sau giao hàng.
- Trung tâm thông báo và yêu cầu đổi trả sau khi đơn giao.
- Thanh toán COD, mô phỏng DEMO, VNPay, MoMo.

### Người bán
- Đăng ký shop và chờ Admin duyệt.
- Quản lý sản phẩm, ảnh, SKU/biến thể, tồn kho và nhập kho.
- Quản lý voucher, đơn hàng, trạng thái vận chuyển.
- Dashboard doanh thu, ví shop, lịch sử giao dịch, yêu cầu rút tiền.
- Nhận và phản hồi yêu cầu đổi trả.

### Admin
- Dashboard và quản lý người dùng, shop, sản phẩm, danh mục, thương hiệu, đơn hàng.
- Cấu hình hoa hồng mặc định; ghi đè theo danh mục, shop hoặc sản phẩm.
- Theo dõi hoa hồng, ví shop và duyệt/từ chối yêu cầu rút tiền.
- Duyệt bước ghi nhận hoàn tiền đổi trả sau khi kiểm tra giao dịch ngân hàng/cổng thanh toán.

### Sổ cái hoa hồng
- Mức ưu tiên: sản phẩm → shop → danh mục → mặc định toàn sàn.
- Tỷ lệ được snapshot vào từng `order_details` tại thời điểm đặt hàng.
- Hoa hồng tính trên giá trị hàng sau giảm giá, không tính phí vận chuyển.
- Chỉ ghi nhận tiền shop khi đơn giao thành công; `order_detail_id` duy nhất chống ghi nhận hai lần.
- Yêu cầu rút tiền giữ số dư khả dụng; từ chối sẽ hoàn lại bằng giao dịch sổ cái.

## 2. Cài đặt lần đầu

Yêu cầu Python 3.10+ và MySQL 8.0.16+.

1. Giải nén source.
2. Mở MySQL Workbench hoặc terminal MySQL và chạy `schema.sql` **trên database mới**. File SQL không còn lệnh `DROP DATABASE`; tuy nhiên các `CREATE TABLE` vẫn dành cho lần khởi tạo đầu tiên.
3. Sao chép `.env.example` thành `.env`, cập nhật `MYSQL_PASSWORD` và `SECRET_KEY`.
4. Cài thư viện:

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

5. Chạy:

```bash
python app.py
```

6. Mở `http://127.0.0.1:5000`.

### Tài khoản dữ liệu mẫu
- Admin: `admin` / `admin123`
- Khách hàng: `khachhang01` / `123456`
- Chủ shop: `nguoiban01` / `123456`

Đổi mật khẩu mẫu trước khi triển khai. Nếu dùng database cũ, sao lưu trước rồi chạy app để `init_system()` bổ sung các cột/bảng còn thiếu; không chạy lại `schema.sql` lên database đang có dữ liệu.

## 3. Thanh toán DEMO và thanh toán thật

### DEMO
Chọn `Thanh toán mô phỏng (DEMO)` ở bước checkout. Mở chi tiết đơn và bấm `Mô phỏng thanh toán`. Hệ thống ghi sự kiện DEMO và đánh dấu payment paid. Đây không phải giao dịch tiền thật.

### VNPay
Điền các biến sau trong `.env` bằng thông tin merchant/sandbox do VNPay cấp:

- `VNPAY_TMN_CODE`
- `VNPAY_HASH_SECRET`
- `VNPAY_URL` (sandbox mặc định)
- `VNPAY_RETURN_URL`

`VNPAY_RETURN_URL` phải là URL callback public có thể truy cập từ Internet, ví dụ `https://ten-mien-cua-ban/payment/vnpay/return`. Không thể nhận callback thật chỉ bằng IP LAN `192.168.x.x`.

### MoMo
Điền `MOMO_PARTNER_CODE`, `MOMO_ACCESS_KEY`, `MOMO_SECRET_KEY`, `MOMO_REDIRECT_URL`, `MOMO_IPN_URL`. IPN URL phải public và truy cập được từ hệ thống MoMo. Dùng endpoint test mặc định cho tài khoản thử nghiệm.

**Lưu ý:** Tích hợp gateway cần merchant credentials hợp lệ, URL callback được đăng ký/cho phép và kiểm thử sandbox theo tài liệu mới nhất của nhà cung cấp. Chưa có thông tin merchant nên source chỉ cung cấp luồng tích hợp và không thể xác nhận giao dịch thật ngay trên máy local. Không đưa secret vào Git hoặc gửi secret qua chat.

## 4. Chạy trong mạng LAN

Trong `.env` giữ `PORT=5000`, chạy `python app.py` (Flask bind `0.0.0.0`). Trên máy chủ Windows chạy `ipconfig` để lấy IPv4, máy khác truy cập `http://IP_MAY_CHU:5000`. Cho phép Python/port 5000 qua Windows Firewall trên mạng Private. Không mở Flask development server trực tiếp ra Internet.

## 5. Kiểm tra source

Chạy:

```bash
python smoke_check.py
python -m unittest discover -s tests -v
```

`smoke_check.py` kiểm tra cú pháp Python, cú pháp template Jinja, các route khai báo và một số điều kiện an toàn tĩnh. Unit test cần cài dependencies từ `requirements.txt`. Kiểm thử tích hợp MySQL và callback sandbox cần database/gateway credentials thật.

## 6. Giới hạn cần cấu hình/hoàn thiện trước vận hành thương mại

- Chuyển khoản rút tiền vẫn cần Admin thực hiện ở ngân hàng và lưu mã giao dịch; chưa có API payout ngân hàng.
- Yêu cầu đổi trả có luồng gửi/duyệt và Admin ghi nhận mã giao dịch hoàn. Việc chuyển tiền thật vẫn phải thực hiện trên ngân hàng/cổng thanh toán; hệ thống không tự gọi API hoàn tiền. Khi ghi nhận hoàn, sổ cái điều chỉnh ngược phần shop và hoa hồng tương ứng.
- Phí vận chuyển hiện là phí cấu hình tĩnh, chưa kết nối hãng vận chuyển.
- Cần HTTPS, reverse proxy, backup database, giám sát lỗi, giới hạn tần suất đăng nhập và rà soát bảo mật độc lập trước khi mở cho khách hàng thật.


## Thanh toán ngân hàng VietQR

Mini Shop hỗ trợ đồng thời VNPay và chuyển khoản VietQR. Thêm các biến sau vào `.env`:

```env
BANK_ID=970436
BANK_DISPLAY_NAME=Vietcombank
BANK_ACCOUNT_NO=SO_TAI_KHOAN_NHAN_TIEN
BANK_ACCOUNT_NAME=TEN_CHU_TAI_KHOAN
BANK_QR_TEMPLATE=compact2
BANK_WEBHOOK_TOKEN=CHUOI_BI_MAT_DAI_NGAU_NHIEN
```

`BANK_ID` là mã BIN ngân hàng theo VietQR; thay bằng ngân hàng thực tế. QR được tạo theo số tiền của đơn và nội dung `MINISHOP{ma_don}`. Không dùng số tài khoản mẫu.

Để tự động xác nhận tiền vào, cần đăng ký webhook tại một dịch vụ đối soát ngân hàng như SePay và cấu hình URL công khai HTTPS:

`https://TEN_MIEN/payment/bank/webhook`

Cấu hình webhook gửi `Authorization: Apikey <BANK_WEBHOOK_TOKEN>` và định dạng JSON. Payload theo SePay gồm `content`, `transferAmount`, `id`, `transferType=in`, `accountNumber`. Endpoint phản hồi `HTTP 200` với `{"success": true}`. Nội dung chuyển khoản phải chứa `MINISHOP` kèm mã đơn, số tiền phải khớp tuyệt đối và giao dịch phải là tiền vào đúng tài khoản nhận. Tài liệu: https://docs.sepay.vn/tich-hop-webhooks.html. Nếu chưa cấu hình webhook, khách vẫn quét QR và chuyển khoản được nhưng trạng thái sẽ không tự chuyển sang đã thanh toán. Không tự xác nhận bằng ảnh chụp giao dịch.

## Sự kiện và chiến dịch bán hàng

Mini Shop sử dụng quy trình gần với Kênh Người Bán của các sàn lớn:

1. Admin tạo chiến dịch (10/10, 11/11, Black Friday, Tết...), nhập banner, thời gian, mô tả và thể lệ; có thể lưu nháp hoặc công bố mở đăng ký.
2. Shop đã được duyệt vào **Kênh người bán > Chiến dịch của Shop** để xem chiến dịch đang mở.
3. Shop tự chọn sản phẩm thuộc shop mình, đề xuất mức giảm 1–90%, số lượng cam kết và ghi chú. Gửi đề cử không làm thay đổi giá sản phẩm.
4. Admin xem danh sách đề cử tại **Quản lý sự kiện**, duyệt hoặc từ chối kèm ghi chú.
5. Chỉ đề cử được duyệt mới được ghi vào `event_products`, hiển thị trong trang sự kiện và áp dụng giá ưu đãi trong thời gian chương trình.

Khách hàng thấy tối đa 4 chiến dịch đang diễn ra/sắp diễn ra ngay trên trang chủ, có đường dẫn tới danh sách `/events` và trang chi tiết từng chiến dịch. Sản phẩm phải được Admin duyệt bán trước khi Shop có thể đề cử.

## Lưu ý vận hành

- VietQR tạo mã chuyển khoản; tự động đối soát phụ thuộc webhook từ ngân hàng/dịch vụ trung gian và token bí mật. Đây không phải API ngân hàng được cấp sẵn.
- VNPay vẫn cần merchant credentials và callback HTTPS công khai.
- Chạy `python smoke_check.py` để kiểm tra cú pháp, template và sự hiện diện các luồng. Kiểm thử tích hợp vẫn cần MySQL và tài khoản sandbox/đối soát thực tế.
