# -*- coding: utf-8 -*-
"""Mini Shop - sàn thương mại điện tử nhiều shop (Flask + MySQL)."""
import os
import re
import hmac
import hashlib
import json
import urllib.parse
import requests
import secrets
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

import mysql.connector
from mysql.connector import errors as dberr
from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


# ----------------------------------------------------------------------------
# CẤU HÌNH (đọc từ biến môi trường hoặc file .env cạnh app.py)
# ----------------------------------------------------------------------------
def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_FILE_SIZE = 5 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "ecommerce_db"),
    "charset": "utf8mb4",
}

PER_PAGE = 12
LOW_STOCK = 5
PAYMENT_METHODS = {
    "COD": "Thanh toán khi nhận hàng (COD)",
    "DEMO": "Thanh toán mô phỏng (DEMO)",
    "VNPAY": "VNPay (cổng thanh toán thật)",
    "MOMO": "MoMo (cổng thanh toán thật)",
    "BANK_QR": "Chuyển khoản ngân hàng (VietQR)",
}
# Luồng trạng thái đơn hàng mà người bán được phép chuyển
STATUS_FLOW = {
    "Cho xac nhan": {"Da xac nhan", "Da huy"},
    "Da xac nhan": {"Dang giao", "Da huy"},
    "Dang giao": {"Da giao"},
}


@contextmanager
def db():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True, buffered=True)
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ----------------------------------------------------------------------------
# TIỆN ÍCH
# ----------------------------------------------------------------------------
def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_dec(value, field="Giá trị", minv=Decimal("0"), maxv=None):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} không hợp lệ.")
    if not d.is_finite() or d < minv or (maxv is not None and d > maxv):
        raise ValueError(f"{field} không hợp lệ.")
    return d


def money(d):
    return Decimal(d).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def slugify(text):
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or uuid.uuid4().hex[:8]


def safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") else None


def back(default="index"):
    return redirect(safe_next(request.referrer and request.referrer.replace(request.host_url, "/", 1))
                    or url_for(default))


def validate_and_save_image(file_storage):
    """Kiểm tra định dạng, dung lượng, tính toàn vẹn rồi lưu ảnh. Trả về tên file hoặc None."""
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Định dạng tệp không được hỗ trợ (chỉ PNG, JPG, JPEG, WEBP, GIF).")

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > MAX_FILE_SIZE:
        raise ValueError("Dung lượng ảnh quá lớn! Vui lòng tải ảnh dưới 5MB.")

    try:
        from PIL import Image
        Image.open(file_storage).verify()
        file_storage.seek(0)
    except ImportError:
        header = file_storage.read(12)
        file_storage.seek(0)
        ok = (header.startswith(b"\xff\xd8\xff") or header.startswith(b"\x89PNG\r\n\x1a\n")
              or header[:6] in (b"GIF87a", b"GIF89a")
              or (header[:4] == b"RIFF" and header[8:12] == b"WEBP"))
        if not ok:
            raise ValueError("Tệp tải lên bị hỏng hoặc không phải là hình ảnh hợp lệ!")
    except Exception:
        raise ValueError("Tệp tải lên bị hỏng hoặc không phải là hình ảnh hợp lệ!")

    new_name = f"{uuid.uuid4().hex[:12]}.{ext}"
    file_storage.save(os.path.join(UPLOAD_FOLDER, new_name))
    return new_name


def remove_upload(filename):
    if not filename:
        return
    path = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
    try:
        os.remove(path)
    except OSError:
        pass


def init_system():
    """Bổ sung bảng/cột còn thiếu và băm mật khẩu cho tài khoản demo (chỉ khi chưa đặt)."""
    try:
        with db() as (conn, cur):
            for ddl in (
                "ALTER TABLE products ADD COLUMN image_url VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE users ADD COLUMN avatar_url VARCHAR(255) NULL",
            ):
                try:
                    cur.execute(ddl)
                except dberr.Error:
                    pass  # cột đã tồn tại
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cart_items (
                    cart_item_id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    vari_id INT NOT NULL,
                    quantity INT NOT NULL DEFAULT 1,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_cart_user_vari UNIQUE (user_id, vari_id),
                    CONSTRAINT fk_cart_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    CONSTRAINT fk_cart_vari FOREIGN KEY (vari_id) REFERENCES product_vari(vari_id) ON DELETE CASCADE
                ) ENGINE=InnoDB""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wishlist (
                    user_id INT NOT NULL,
                    product_id INT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, product_id),
                    CONSTRAINT fk_wish_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    CONSTRAINT fk_wish_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
                ) ENGINE=InnoDB""")
            # Sự kiện bán hàng và danh sách sản phẩm tham gia.
            cur.execute("""CREATE TABLE IF NOT EXISTS events (
                event_id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(180) NOT NULL,
                slug VARCHAR(200) NOT NULL UNIQUE,
                description TEXT NULL,
                banner_url VARCHAR(255) NULL,
                start_at DATETIME NOT NULL,
                end_at DATETIME NOT NULL,
                status ENUM('Draft','Published','Cancelled') NOT NULL DEFAULT 'Draft',
                created_by INT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_event_dates (status,start_at,end_at),
                CONSTRAINT fk_event_admin FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS event_products (
                event_id INT NOT NULL,
                product_id INT NOT NULL,
                discount_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
                PRIMARY KEY (event_id, product_id),
                CONSTRAINT fk_event_product_event FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
                CONSTRAINT fk_event_product_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                CHECK (discount_percent BETWEEN 0 AND 90)
            ) ENGINE=InnoDB""")
            # Shop tự đề cử sản phẩm vào chiến dịch; Admin duyệt trước khi công khai.
            cur.execute("""CREATE TABLE IF NOT EXISTS event_nominations (
                nomination_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                event_id INT NOT NULL,
                product_id INT NOT NULL,
                seller_id INT NOT NULL,
                discount_percent DECIMAL(5,2) NOT NULL,
                committed_stock INT NOT NULL DEFAULT 0,
                status ENUM('Pending','Approved','Rejected','Withdrawn') NOT NULL DEFAULT 'Pending',
                seller_note VARCHAR(500) NULL,
                admin_note VARCHAR(500) NULL,
                reviewed_by INT NULL,
                reviewed_at DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_event_product_nomination (event_id, product_id),
                INDEX idx_nomination_review (status, created_at),
                CONSTRAINT fk_nom_event FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE,
                CONSTRAINT fk_nom_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE,
                CONSTRAINT fk_nom_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id) ON DELETE CASCADE,
                CONSTRAINT fk_nom_reviewer FOREIGN KEY (reviewed_by) REFERENCES users(user_id) ON DELETE SET NULL,
                CHECK (discount_percent BETWEEN 1 AND 90),
                CHECK (committed_stock >= 0)
            ) ENGINE=InnoDB""")
            # Cấu hình hoa hồng và sổ cái tài chính cho sàn nhiều người bán.
            for ddl in (
                "ALTER TABLE order_details ADD COLUMN commission_rate DECIMAL(5,2) NOT NULL DEFAULT 5.00",
                "ALTER TABLE order_details ADD COLUMN commission_amount DECIMAL(15,2) NOT NULL DEFAULT 0",
                "ALTER TABLE order_details ADD COLUMN seller_amount DECIMAL(15,2) NOT NULL DEFAULT 0",
            ):
                try:
                    cur.execute(ddl)
                except dberr.Error:
                    pass
            cur.execute("""CREATE TABLE IF NOT EXISTS platform_settings (
                setting_id TINYINT PRIMARY KEY, commission_rate DECIMAL(5,2) NOT NULL DEFAULT 5.00,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CHECK (commission_rate BETWEEN 0 AND 100)
            ) ENGINE=InnoDB""")
            cur.execute("INSERT IGNORE INTO platform_settings (setting_id, commission_rate) VALUES (1,5.00)")
            # Hoa hồng có thể ghi đè theo danh mục, shop hoặc sản phẩm (ưu tiên sản phẩm > shop > danh mục > mặc định).
            for ddl in (
                "ALTER TABLE categories ADD COLUMN commission_rate DECIMAL(5,2) NULL",
                "ALTER TABLE sellers ADD COLUMN commission_rate DECIMAL(5,2) NULL",
                "ALTER TABLE products ADD COLUMN commission_rate DECIMAL(5,2) NULL",
            ):
                try:
                    cur.execute(ddl)
                except dberr.Error:
                    pass
            cur.execute("""CREATE TABLE IF NOT EXISTS notifications (
                notification_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL, title VARCHAR(160) NOT NULL, message VARCHAR(500) NOT NULL,
                target_url VARCHAR(255) NULL, is_read BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_notification_user (user_id, is_read, created_at),
                CONSTRAINT fk_notification_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS return_requests (
                return_id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id INT NOT NULL,
                order_detail_id INT NOT NULL, user_id INT NOT NULL, seller_id INT NOT NULL,
                quantity INT NOT NULL, reason VARCHAR(500) NOT NULL, evidence_url VARCHAR(255) NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'Cho xu ly', seller_note VARCHAR(500) NULL,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, processed_at TIMESTAMP NULL,
                INDEX idx_return_seller (seller_id, status, requested_at),
                CONSTRAINT fk_return_order FOREIGN KEY (order_id) REFERENCES orders(order_id),
                CONSTRAINT fk_return_detail FOREIGN KEY (order_detail_id) REFERENCES order_details(order_detail_id),
                CONSTRAINT fk_return_user FOREIGN KEY (user_id) REFERENCES users(user_id),
                CONSTRAINT fk_return_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
            ) ENGINE=InnoDB""")
            for ddl in (
                "ALTER TABLE return_requests ADD COLUMN refund_amount DECIMAL(15,2) NULL",
                "ALTER TABLE return_requests ADD COLUMN refund_reference VARCHAR(150) NULL",
                "ALTER TABLE return_requests ADD COLUMN refunded_at TIMESTAMP NULL",
            ):
                try:
                    cur.execute(ddl)
                except dberr.Error:
                    pass
            cur.execute("""CREATE TABLE IF NOT EXISTS commission_refunds (
                refund_id BIGINT AUTO_INCREMENT PRIMARY KEY, return_id BIGINT NOT NULL UNIQUE,
                seller_id INT NOT NULL, commission_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
                seller_amount DECIMAL(15,2) NOT NULL DEFAULT 0, total_refund DECIMAL(15,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_comm_refund_return FOREIGN KEY (return_id) REFERENCES return_requests(return_id),
                CONSTRAINT fk_comm_refund_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS payment_events (
                event_id BIGINT AUTO_INCREMENT PRIMARY KEY, payment_id INT NOT NULL,
                provider VARCHAR(20) NOT NULL, provider_transaction_id VARCHAR(150) NULL,
                event_type VARCHAR(50) NOT NULL, payload JSON NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_payment_event_payment (payment_id, created_at),
                CONSTRAINT fk_payment_event_payment FOREIGN KEY (payment_id) REFERENCES payment(payment_id)
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS seller_wallet (
                seller_id INT PRIMARY KEY, balance DECIMAL(15,2) NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_wallet_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id) ON DELETE CASCADE
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS commission_records (
                commission_id BIGINT AUTO_INCREMENT PRIMARY KEY, order_detail_id INT NOT NULL UNIQUE,
                seller_id INT NOT NULL, order_id INT NOT NULL, commission_base DECIMAL(15,2) NOT NULL,
                commission_rate DECIMAL(5,2) NOT NULL, commission_amount DECIMAL(15,2) NOT NULL,
                seller_amount DECIMAL(15,2) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_comm_od FOREIGN KEY (order_detail_id) REFERENCES order_details(order_detail_id),
                CONSTRAINT fk_comm_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id),
                CONSTRAINT fk_comm_order FOREIGN KEY (order_id) REFERENCES orders(order_id)
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS wallet_transactions (
                transaction_id BIGINT AUTO_INCREMENT PRIMARY KEY, seller_id INT NOT NULL,
                order_detail_id INT NULL, withdrawal_id BIGINT NULL, transaction_type VARCHAR(30) NOT NULL,
                amount DECIMAL(15,2) NOT NULL, note VARCHAR(255), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_wallet_tx_seller (seller_id, created_at),
                CONSTRAINT fk_wtx_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
            ) ENGINE=InnoDB""")
            cur.execute("""CREATE TABLE IF NOT EXISTS withdrawal_requests (
                withdrawal_id BIGINT AUTO_INCREMENT PRIMARY KEY, seller_id INT NOT NULL,
                amount DECIMAL(15,2) NOT NULL, bank_name VARCHAR(120) NOT NULL,
                account_number VARCHAR(50) NOT NULL, account_name VARCHAR(120) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'Cho duyet', admin_note VARCHAR(255),
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, processed_at TIMESTAMP NULL,
                CONSTRAINT fk_withdraw_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
            ) ENGINE=InnoDB""")
            # Chuyển dữ liệu đơn đã giao từ phiên bản cũ sang sổ hoa hồng (chạy an toàn nhiều lần).
            cur.execute("""
                UPDATE order_details od
                JOIN orders o ON o.order_id=od.order_id
                JOIN platform_settings ps ON ps.setting_id=1
                SET od.commission_rate=ps.commission_rate,
                    od.commission_amount=ROUND(
                        (CASE WHEN o.total_amount>0
                         THEN od.subtotal*(o.total_amount-o.discount_amount)/o.total_amount
                         ELSE 0 END)*ps.commission_rate/100, 0),
                    od.seller_amount=(CASE WHEN o.total_amount>0
                         THEN ROUND(od.subtotal*(o.total_amount-o.discount_amount)/o.total_amount,0)
                         ELSE 0 END) -
                         ROUND((CASE WHEN o.total_amount>0
                         THEN od.subtotal*(o.total_amount-o.discount_amount)/o.total_amount
                         ELSE 0 END)*ps.commission_rate/100, 0)
                WHERE o.status='Da giao'
                  AND NOT EXISTS (SELECT 1 FROM commission_records cr WHERE cr.order_detail_id=od.order_detail_id)
            """)
            cur.execute("""
                INSERT IGNORE INTO commission_records
                (order_detail_id, seller_id, order_id, commission_base, commission_rate, commission_amount, seller_amount)
                SELECT od.order_detail_id, p.seller_id, od.order_id,
                       od.commission_amount+od.seller_amount, od.commission_rate,
                       od.commission_amount, od.seller_amount
                FROM order_details od
                JOIN product_vari v ON v.vari_id=od.vari_id
                JOIN products p ON p.product_id=v.product_id
                JOIN orders o ON o.order_id=od.order_id
                WHERE o.status='Da giao'
            """)
            # Chỉ cộng ví cho các bản ghi hoa hồng vừa được chuyển đổi; ví demo có thể đã được seed.
            cur.execute("""
                INSERT IGNORE INTO seller_wallet (seller_id, balance)
                SELECT cr.seller_id, SUM(cr.seller_amount)
                FROM commission_records cr
                LEFT JOIN seller_wallet sw ON sw.seller_id=cr.seller_id
                GROUP BY cr.seller_id
            """)
            cur.execute("UPDATE admin_user SET password_hash=%s WHERE user_admin='admin' AND password_hash='CHANGE_ME_HASH'",
                        (generate_password_hash("admin123"),))
            cur.execute("UPDATE users SET pass_hash=%s WHERE pass_hash='CHANGE_ME_HASH'",
                        (generate_password_hash("123456"),))
        print("=> [OK] Đã khởi tạo hệ thống và đồng bộ CSDL.")
    except Exception as e:
        print(f"=> [Lỗi khởi tạo CSDL]: {e}")


# ----------------------------------------------------------------------------
# BẢO MẬT: CSRF, PHÂN QUYỀN
# ----------------------------------------------------------------------------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    if request.endpoint == "momo_ipn":
        return None
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRFToken") or ""
        expected = session.get("_csrf", "")
        if not expected or not secrets.compare_digest(sent, expected):
            flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "warning")
            return redirect(url_for("index"))


@app.errorhandler(413)
def too_large(_e):
    flash("Tệp tải lên quá lớn (tối đa 5MB cho ảnh).", "danger")
    return back()


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, message="Không tìm thấy trang bạn cần."), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500, message="Hệ thống đang gặp sự cố, vui lòng thử lại sau."), 500


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if "user" not in session:
            flash("Vui lòng đăng nhập để tiếp tục!", "warning")
            nxt = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("login", next=nxt) if nxt else url_for("login"))
        return view(*a, **kw)
    return wrapper


def customer_required(view):
    @wraps(view)
    @login_required
    def wrapper(*a, **kw):
        if session["user"].get("role") == "admin":
            flash("Tài khoản Admin không dùng được chức năng này!", "warning")
            return redirect(url_for("admin_panel"))
        return view(*a, **kw)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if session.get("user", {}).get("role") != "admin":
            flash("Bạn không có quyền truy cập trang Admin!", "danger")
            return redirect(url_for("index"))
        return view(*a, **kw)
    return wrapper


def seller_required(view):
    """Chỉ cho shop đã được duyệt; gắn thông tin shop vào g.seller."""
    @wraps(view)
    @customer_required
    def wrapper(*a, **kw):
        with db() as (conn, cur):
            cur.execute("SELECT * FROM sellers WHERE user_id=%s AND approved_at IS NOT NULL",
                        (session["user"]["id"],))
            g.seller = cur.fetchone()
        if not g.seller:
            flash("Bạn cần có shop đã được duyệt để thực hiện thao tác này.", "warning")
            return redirect(url_for("seller_channel"))
        return view(*a, **kw)
    return wrapper


@app.context_processor
def inject_globals():
    cart_count = 0
    u = session.get("user")
    if u and u.get("role") == "user":
        try:
            with db() as (conn, cur):
                cur.execute("SELECT COALESCE(SUM(quantity),0) AS n FROM cart_items WHERE user_id=%s", (u["id"],))
                cart_count = int(cur.fetchone()["n"])
        except Exception:
            pass
    unread_notifications = 0
    if u and u.get("role") == "user":
        try:
            with db() as (conn, cur):
                cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND is_read=FALSE", (u["id"],))
                unread_notifications = int(cur.fetchone()["n"])
        except Exception:
            pass
    return {"cart_count": cart_count, "unread_notifications": unread_notifications}


@app.template_filter("vnd")
def vnd(value):
    try:
        return "{:,.0f}".format(value or 0).replace(",", ".") + " đ"
    except (ValueError, TypeError):
        return "0 đ"


# ----------------------------------------------------------------------------
# NGHIỆP VỤ DÙNG CHUNG
# ----------------------------------------------------------------------------
def refresh_rank(cur, user_id):
    """Tự nâng/hạ hạng thành viên theo tổng chi tiêu của các đơn đã giao."""
    cur.execute("SELECT COALESCE(SUM(final_amount),0) AS spent FROM orders WHERE user_id=%s AND status='Da giao'",
                (user_id,))
    spent = cur.fetchone()["spent"]
    cur.execute("SELECT rank_id FROM user_rank WHERE min_spent <= %s ORDER BY min_spent DESC LIMIT 1", (spent,))
    r = cur.fetchone()
    if r:
        cur.execute("UPDATE users SET rank_id=%s WHERE user_id=%s", (r["rank_id"], user_id))


def restore_stock(cur, order_id):
    cur.execute("SELECT vari_id, quantity FROM order_details WHERE order_id=%s", (order_id,))
    for item in cur.fetchall():
        cur.execute("UPDATE product_vari SET quantity = quantity + %s WHERE vari_id=%s",
                    (item["quantity"], item["vari_id"]))


def settle_order_commissions(cur, order_id):
    """Ghi nhận hoa hồng đúng một lần khi đơn giao thành công; cộng phần shop nhận vào ví."""
    cur.execute("""
        SELECT od.order_detail_id, (od.commission_amount + od.seller_amount) AS net_amount, od.commission_rate, od.commission_amount,
               od.seller_amount, p.seller_id
        FROM order_details od
        JOIN product_vari v ON v.vari_id=od.vari_id
        JOIN products p ON p.product_id=v.product_id
        WHERE od.order_id=%s
    """, (order_id,))
    for row in cur.fetchall():
        cur.execute("""INSERT IGNORE INTO commission_records
            (order_detail_id, seller_id, order_id, commission_base, commission_rate, commission_amount, seller_amount)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (row["order_detail_id"], row["seller_id"], order_id, row["net_amount"],
             row["commission_rate"], row["commission_amount"], row["seller_amount"]))
        if cur.rowcount == 1:
            cur.execute("""INSERT INTO seller_wallet (seller_id, balance) VALUES (%s,%s)
                           ON DUPLICATE KEY UPDATE balance=balance+VALUES(balance)""",
                        (row["seller_id"], row["seller_amount"]))
            cur.execute("""INSERT INTO wallet_transactions
                (seller_id, order_detail_id, transaction_type, amount, note)
                VALUES (%s,%s,'Cong doanh thu',%s,%s)""",
                (row["seller_id"], row["order_detail_id"], row["seller_amount"],
                 f"Doanh thu đơn hàng #{order_id} sau khi trừ hoa hồng"))


def notify_user(cur, user_id, title, message, target_url=None):
    cur.execute("INSERT INTO notifications (user_id,title,message,target_url) VALUES (%s,%s,%s,%s)",
                (user_id, title[:160], message[:500], target_url))


def effective_commission(cur, product_id, seller_id, category_id, default_rate):
    cur.execute("SELECT commission_rate FROM products WHERE product_id=%s", (product_id,))
    product = cur.fetchone()
    if product and product.get("commission_rate") is not None:
        return product["commission_rate"]
    cur.execute("SELECT commission_rate FROM sellers WHERE seller_id=%s", (seller_id,))
    seller = cur.fetchone()
    if seller and seller.get("commission_rate") is not None:
        return seller["commission_rate"]
    cur.execute("SELECT commission_rate FROM categories WHERE categories_id=%s", (category_id,))
    category = cur.fetchone()
    if category and category.get("commission_rate") is not None:
        return category["commission_rate"]
    return default_rate


def vnpay_signature(params, secret):
    clean = {k: v for k, v in params.items() if k not in ("vnp_SecureHash", "vnp_SecureHashType") and v is not None}
    query = urllib.parse.urlencode(sorted(clean.items()), quote_via=urllib.parse.quote_plus)
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha512).hexdigest()


def verify_vnpay(params):
    secret = os.getenv("VNPAY_HASH_SECRET", "")
    supplied = params.get("vnp_SecureHash", "")
    if not secret or not supplied:
        return False
    return hmac.compare_digest(vnpay_signature(params, secret).lower(), supplied.lower())


def build_vnpay_url(order_id, amount, order_info):
    tmn_code = os.getenv("VNPAY_TMN_CODE", "")
    secret = os.getenv("VNPAY_HASH_SECRET", "")
    base_url = os.getenv("VNPAY_URL", "https://sandbox.vnpayment.vn/paymentv2/vpcpay.html")
    if not tmn_code or not secret:
        raise ValueError("Chưa cấu hình VNPAY_TMN_CODE và VNPAY_HASH_SECRET trong file .env.")
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    params = {
        "vnp_Version": "2.1.0", "vnp_Command": "pay", "vnp_TmnCode": tmn_code,
        "vnp_Amount": str(int(Decimal(str(amount)) * 100)), "vnp_CurrCode": "VND",
        "vnp_TxnRef": str(order_id), "vnp_OrderInfo": order_info[:255],
        "vnp_OrderType": "other", "vnp_Locale": "vn",
        "vnp_ReturnUrl": os.getenv("VNPAY_RETURN_URL", ""),
        "vnp_IpAddr": request.remote_addr or "127.0.0.1", "vnp_CreateDate": now,
    }
    if not params["vnp_ReturnUrl"]:
        raise ValueError("Chưa cấu hình VNPAY_RETURN_URL (URL public/HTTPS của website).")
    params["vnp_SecureHash"] = vnpay_signature(params, secret)
    return base_url + "?" + urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote_plus)


def create_momo_payment(order_id, amount, order_info):
    endpoint = os.getenv("MOMO_ENDPOINT", "https://test-payment.momo.vn/v2/gateway/api/create")
    partner_code = os.getenv("MOMO_PARTNER_CODE", "")
    access_key = os.getenv("MOMO_ACCESS_KEY", "")
    secret_key = os.getenv("MOMO_SECRET_KEY", "")
    redirect_url = os.getenv("MOMO_REDIRECT_URL", "")
    ipn_url = os.getenv("MOMO_IPN_URL", "")
    if not all((partner_code, access_key, secret_key, redirect_url, ipn_url)):
        raise ValueError("Chưa cấu hình đầy đủ MOMO_PARTNER_CODE, MOMO_ACCESS_KEY, MOMO_SECRET_KEY, MOMO_REDIRECT_URL và MOMO_IPN_URL.")
    request_id = uuid.uuid4().hex
    order_id_momo = f"MINISHOP_{order_id}_{request_id[:8]}"
    amount_int = str(int(Decimal(str(amount))))
    request_type = os.getenv("MOMO_REQUEST_TYPE", "captureWallet")
    extra_data = ""
    raw = (f"accessKey={access_key}&amount={amount_int}&extraData={extra_data}&ipnUrl={ipn_url}"
           f"&orderId={order_id_momo}&orderInfo={order_info}& partnerCode={partner_code}")
    # MoMo yêu cầu chuỗi raw chính xác, không có khoảng trắng thừa trước partnerCode.
    raw = raw.replace("& partnerCode=", "&partnerCode=")
    raw += f"&redirectUrl={redirect_url}&requestId={request_id}&requestType={request_type}"
    signature = hmac.new(secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()
    payload = {"partnerCode": partner_code, "accessKey": access_key, "requestId": request_id,
               "amount": amount_int, "orderId": order_id_momo, "orderInfo": order_info,
               "redirectUrl": redirect_url, "ipnUrl": ipn_url, "extraData": extra_data,
               "requestType": request_type, "signature": signature, "lang": "vi"}
    response = requests.post(endpoint, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    response_raw_keys = ("accessKey", "amount", "extraData", "message", "orderId", "orderInfo",
                         "partnerCode", "payUrl", "requestId", "responseTime", "resultCode", "transId")
    response_raw = "&".join(f"{key}={data.get(key, '')}" for key in response_raw_keys)
    expected_response_sig = hmac.new(secret_key.encode(), response_raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_response_sig, str(data.get("signature", ""))):
        raise ValueError("Chữ ký phản hồi MoMo không hợp lệ.")
    if data.get("resultCode") != 0 or not data.get("payUrl"):
        raise ValueError(data.get("message") or "MoMo không tạo được giao dịch.")
    return data, order_id_momo


def create_orders(cur, user_id, items, ship_id, pay_method, address, promo_code):
    """Tạo đơn hàng từ danh sách [(vari_id, qty)]. Tách thành 1 đơn cho mỗi shop.
    Ném ValueError với thông báo tiếng Việt nếu không hợp lệ."""
    if pay_method not in PAYMENT_METHODS:
        raise ValueError("Phương thức thanh toán không hợp lệ.")
    if not address or len(address.strip()) < 5:
        raise ValueError("Vui lòng nhập địa chỉ nhận hàng.")
    address = address.strip()[:255]

    wanted = {}
    for vid, qty in items:
        if not vid or not qty or qty < 1:
            raise ValueError("Số lượng không hợp lệ.")
        wanted[vid] = wanted.get(vid, 0) + qty
    if not wanted:
        raise ValueError("Chưa có sản phẩm nào để đặt.")

    ids = sorted(wanted)
    marks = ",".join(["%s"] * len(ids))
    cur.execute(f"""
        SELECT v.vari_id,
               ROUND(v.price * (1 - COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                   JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                   WHERE ep.product_id=p.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)/100),0) AS price,
               v.quantity, v.size, v.color,
               p.product_id, p.category_id, p.product_name, p.seller_id, s.user_id AS seller_user
        FROM product_vari v
        JOIN products p ON p.product_id = v.product_id
        JOIN sellers s ON s.seller_id = p.seller_id
        WHERE v.vari_id IN ({marks}) AND p.approval_status = 'Da duyet'
        ORDER BY v.vari_id FOR UPDATE""", ids)
    rows = {r["vari_id"]: r for r in cur.fetchall()}

    by_seller = {}
    for vid in ids:
        v = rows.get(vid)
        if not v:
            raise ValueError("Có sản phẩm không còn được bán.")
        if wanted[vid] > v["quantity"]:
            raise ValueError(f"'{v['product_name']}' chỉ còn {v['quantity']} sản phẩm trong kho.")
        if v["seller_user"] == user_id:
            raise ValueError("Bạn không thể mua sản phẩm của chính shop mình!")
        by_seller.setdefault(v["seller_id"], []).append(v)

    cur.execute("SELECT base_cost FROM shipping_method WHERE shipping_method_id=%s AND is_active=TRUE", (ship_id,))
    ship = cur.fetchone()
    if not ship:
        raise ValueError("Đơn vị vận chuyển không hợp lệ.")
    cur.execute("""SELECT COALESCE(r.discount_rate,0) AS rate FROM users u
                   LEFT JOIN user_rank r ON r.rank_id=u.rank_id WHERE u.user_id=%s""", (user_id,))
    rank_rate = cur.fetchone()["rate"]

    cur.execute("SELECT commission_rate FROM platform_settings WHERE setting_id=1")
    setting = cur.fetchone()
    commission_rate = setting["commission_rate"] if setting else Decimal("5.00")

    promo_used = False
    results = []
    for seller_id, vs in by_seller.items():
        subtotal = sum(v["price"] * wanted[v["vari_id"]] for v in vs)

        promo_discount = Decimal("0")
        promo_product_ids = set()
        promo_base = Decimal("0")
        if promo_code:
            cur.execute("""SELECT promotion_id, discount_percent, discount_amount FROM promotion
                           WHERE seller_id=%s AND code=%s AND is_active=TRUE
                             AND NOW() BETWEEN start_date AND end_date""", (seller_id, promo_code))
            pr = cur.fetchone()
            if pr:
                cur.execute("SELECT product_id FROM promotion_product WHERE promotion_id=%s", (pr["promotion_id"],))
                promo_product_ids = {r["product_id"] for r in cur.fetchall()}
                promo_base = sum(v["price"] * wanted[v["vari_id"]] for v in vs
                                 if v["product_id"] in promo_product_ids)
                if promo_base > 0:
                    if pr["discount_percent"] > 0:
                        promo_discount = promo_base * pr["discount_percent"] / Decimal("100")
                    else:
                        promo_discount = pr["discount_amount"]
                    promo_discount = min(money(promo_discount), promo_base)
                    promo_used = True
        rank_discount = money((subtotal - promo_discount) * rank_rate / Decimal("100"))
        total_discount = promo_discount + rank_discount
        final_amount = max(Decimal("0"), subtotal - total_discount) + ship["base_cost"]

        cur.execute("""INSERT INTO orders (user_id, total_amount, discount_amount, final_amount, status, shipping_address)
                       VALUES (%s,%s,%s,%s,'Cho xac nhan',%s)""",
                    (user_id, subtotal, total_discount, final_amount, address))
        order_id = cur.lastrowid
        for v in vs:
            q = wanted[v["vari_id"]]
            gross_line = money(v["price"] * q)
            # Phân bổ voucher đúng các sản phẩm đủ điều kiện, sau đó phân bổ giảm hạng thành viên.
            line_promo_discount = Decimal("0")
            if v["product_id"] in promo_product_ids and promo_base > 0:
                line_promo_discount = money(promo_discount * gross_line / promo_base)
                line_promo_discount = min(line_promo_discount, gross_line)
            after_promo = gross_line - line_promo_discount
            rank_base = subtotal - promo_discount
            line_rank_discount = money(rank_discount * after_promo / rank_base) if rank_base > 0 else Decimal("0")
            net_line = max(Decimal("0"), after_promo - line_rank_discount)
            # Không tính hoa hồng trên phí vận chuyển.
            item_rate = effective_commission(cur, v["product_id"], seller_id, v["category_id"], commission_rate)
            commission_amount = money(net_line * item_rate / Decimal("100"))
            seller_amount = net_line - commission_amount
            cur.execute("""INSERT INTO order_details
                           (order_id, vari_id, quantity, unit_price, subtotal, commission_rate, commission_amount, seller_amount)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (order_id, v["vari_id"], q, v["price"], gross_line,
                         item_rate, commission_amount, seller_amount))
            cur.execute("UPDATE product_vari SET quantity = quantity - %s WHERE vari_id=%s", (q, v["vari_id"]))
        cur.execute("""INSERT INTO payment (order_id, payment_method, amount, payment_status)
                       VALUES (%s,%s,%s,'Cho thanh toan')""", (order_id, pay_method, final_amount))
        cur.execute("""INSERT INTO shipment (shipping_method_id, order_id, tracking_code, shipping_status)
                       VALUES (%s,%s,%s,'Cho lay hang')""", (ship_id, order_id, f"TRK{order_id:08d}"))
        notify_user(cur, user_id, "Đặt hàng thành công", f"Đơn hàng #{order_id} đã được tạo và đang chờ xác nhận.", f"/orders/{order_id}")
        notify_user(cur, vs[0]["seller_user"], "Có đơn hàng mới", f"Shop của bạn vừa nhận đơn hàng #{order_id}.", "/seller")
        results.append({"order_id": order_id, "discount": total_discount, "final": final_amount})

    if promo_code and not promo_used:
        raise ValueError("Mã giảm giá không hợp lệ hoặc không áp dụng cho sản phẩm đã chọn.")
    return results


# ----------------------------------------------------------------------------
# SỰ KIỆN MUA SẮM
# ----------------------------------------------------------------------------
@app.route("/events")
def event_list():
    with db() as (conn, cur):
        cur.execute("""SELECT e.*, COUNT(ep.product_id) AS product_count,
                       CASE WHEN NOW() BETWEEN e.start_at AND e.end_at THEN 'Đang diễn ra'
                            WHEN NOW() < e.start_at THEN 'Sắp diễn ra' ELSE 'Đã kết thúc' END AS time_status
                       FROM events e LEFT JOIN event_products ep ON ep.event_id=e.event_id
                       WHERE e.status='Published' GROUP BY e.event_id
                       ORDER BY (NOW() BETWEEN e.start_at AND e.end_at) DESC,
                                (e.start_at > NOW()) DESC,e.start_at DESC""")
        events = cur.fetchall()
    return render_template("events.html", events=events)


@app.route("/events/<int:event_id>")
def event_detail(event_id):
    with db() as (conn, cur):
        cur.execute("SELECT * FROM events WHERE event_id=%s AND status='Published'", (event_id,))
        event = cur.fetchone()
        if not event:
            flash("Sự kiện không tồn tại hoặc chưa được công bố.", "warning")
            return redirect(url_for("event_list"))
        cur.execute("""SELECT p.product_id,p.product_name,p.image_url,s.shop_name,
                       MIN(ROUND(v.price*(1-ep.discount_percent/100))) AS sale_price,
                       MIN(v.price) AS original_price,MAX(ep.discount_percent) AS discount_percent,
                       SUM(v.quantity) AS stock
                       FROM event_products ep JOIN products p ON p.product_id=ep.product_id
                       JOIN product_vari v ON v.product_id=p.product_id
                       JOIN sellers s ON s.seller_id=p.seller_id
                       WHERE ep.event_id=%s AND p.approval_status='Da duyet'
                       GROUP BY p.product_id,p.product_name,p.image_url,s.shop_name
                       ORDER BY ep.discount_percent DESC,p.product_id DESC""", (event_id,))
        products = cur.fetchall()
    return render_template("event_detail.html", event=event, products=products)


@app.route("/admin/events", methods=["GET"])
@admin_required
def admin_events():
    with db() as (conn, cur):
        cur.execute("""SELECT e.*,COUNT(ep.product_id) AS product_count FROM events e
                       LEFT JOIN event_products ep ON ep.event_id=e.event_id
                       GROUP BY e.event_id ORDER BY e.start_at DESC""")
        events = cur.fetchall()
        cur.execute("""SELECT n.*,e.title AS event_title,p.product_name,s.shop_name,
                       u.full_name AS seller_name,
                       (SELECT MIN(v.price) FROM product_vari v WHERE v.product_id=p.product_id) AS original_price
                       FROM event_nominations n JOIN events e ON e.event_id=n.event_id
                       JOIN products p ON p.product_id=n.product_id
                       JOIN sellers s ON s.seller_id=n.seller_id JOIN users u ON u.user_id=s.user_id
                       WHERE n.status='Pending' ORDER BY n.created_at""")
        nominations = cur.fetchall()
    return render_template("admin_events.html", events=events, nominations=nominations)


@app.route("/admin/events/create", methods=["POST"])
@admin_required
def admin_event_create():
    try:
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        start_at = datetime.strptime(request.form.get("start_at", ""), "%Y-%m-%dT%H:%M")
        end_at = datetime.strptime(request.form.get("end_at", ""), "%Y-%m-%dT%H:%M")
        if not title or len(title) > 180 or end_at <= start_at:
            raise ValueError("Tên sự kiện và thời gian hợp lệ là bắt buộc.")
        banner = validate_and_save_image(request.files.get("banner")) if request.files.get("banner") and request.files.get("banner").filename else None
        slug = slugify(title) + "-" + uuid.uuid4().hex[:6]
        status = "Published" if request.form.get("publish") == "1" else "Draft"
        with db() as (conn, cur):
            cur.execute("""INSERT INTO events
                (title,slug,description,banner_url,start_at,end_at,status,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (title,slug,description,banner,start_at,end_at,status,session["user"]["id"]))
        flash("Đã tạo sự kiện. Các shop có thể tự đề cử sản phẩm; chỉ sản phẩm được duyệt mới xuất hiện.", "success")
    except (ValueError, TypeError) as err:
        flash(str(err), "danger")
    except Exception as err:
        flash(f"Không thể tạo sự kiện: {err}", "danger")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/nomination/<int:nomination_id>/<action>", methods=["POST"])
@admin_required
def admin_event_nomination_review(nomination_id, action):
    if action not in ("approve", "reject"):
        flash("Thao tác không hợp lệ.", "danger")
        return redirect(url_for("admin_events"))
    note = request.form.get("admin_note", "").strip()[:500]
    with db() as (conn, cur):
        cur.execute("""SELECT n.*,e.status AS event_status,e.end_at,p.approval_status,s.user_id AS seller_user_id
                       FROM event_nominations n JOIN events e ON e.event_id=n.event_id
                       JOIN products p ON p.product_id=n.product_id JOIN sellers s ON s.seller_id=n.seller_id
                       WHERE n.nomination_id=%s FOR UPDATE""", (nomination_id,))
        n = cur.fetchone()
        if not n or n["status"] != "Pending":
            flash("Đề cử không còn ở trạng thái chờ duyệt.", "warning")
            return redirect(url_for("admin_events"))
        if action == "approve":
            if n["event_status"] != "Published" or n["end_at"] <= datetime.now() or n["approval_status"] != "Da duyet":
                flash("Không thể duyệt: sự kiện đã đóng hoặc sản phẩm chưa được duyệt bán.", "danger")
                return redirect(url_for("admin_events"))
            cur.execute("""INSERT INTO event_products (event_id,product_id,discount_percent)
                           VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE discount_percent=VALUES(discount_percent)""",
                        (n["event_id"],n["product_id"],n["discount_percent"]))
            cur.execute("""UPDATE event_nominations SET status='Approved',admin_note=%s,
                           reviewed_by=%s,reviewed_at=NOW() WHERE nomination_id=%s""",
                        (note,session["user"]["id"],nomination_id))
            notify_user(cur, n["seller_user_id"], "Sản phẩm được duyệt tham gia sự kiện",
                        f"Sản phẩm #{n['product_id']} đã được duyệt tham gia chương trình.", "/seller/events")
            flash("Đã duyệt sản phẩm tham gia sự kiện.", "success")
        else:
            cur.execute("""UPDATE event_nominations SET status='Rejected',admin_note=%s,
                           reviewed_by=%s,reviewed_at=NOW() WHERE nomination_id=%s""",
                        (note,session["user"]["id"],nomination_id))
            notify_user(cur, n["seller_user_id"], "Đề cử sự kiện chưa được duyệt",
                        f"Sản phẩm #{n['product_id']} chưa được duyệt. Vui lòng xem ghi chú của quản trị viên.", "/seller/events")
            flash("Đã từ chối đề cử.", "info")
    return redirect(url_for("admin_events"))


@app.route("/seller/events")
@customer_required
def seller_events():
    user_id = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("SELECT * FROM sellers WHERE user_id=%s AND approved_at IS NOT NULL", (user_id,))
        seller = cur.fetchone()
        if not seller:
            flash("Bạn cần có shop đã được duyệt mới có thể đăng ký sự kiện.", "warning")
            return redirect(url_for("seller_channel"))
        cur.execute("""SELECT e.*,COUNT(ep.product_id) AS product_count,
                       CASE WHEN NOW() BETWEEN e.start_at AND e.end_at THEN 'Đang diễn ra' ELSE 'Sắp diễn ra' END AS time_status
                       FROM events e LEFT JOIN event_products ep ON ep.event_id=e.event_id
                       WHERE e.status='Published' AND e.end_at>NOW()
                       GROUP BY e.event_id ORDER BY e.start_at""")
        events = cur.fetchall()
        cur.execute("""SELECT product_id,product_name FROM products
                       WHERE seller_id=%s AND approval_status='Da duyet' ORDER BY product_name""", (seller["seller_id"],))
        products = cur.fetchall()
        cur.execute("""SELECT n.*,e.title AS event_title,p.product_name FROM event_nominations n
                       JOIN events e ON e.event_id=n.event_id JOIN products p ON p.product_id=n.product_id
                       WHERE n.seller_id=%s ORDER BY n.created_at DESC""", (seller["seller_id"],))
        nominations = cur.fetchall()
    return render_template("seller_events.html", seller=seller, events=events,
                           products=products, nominations=nominations)


@app.route("/seller/events/nominate", methods=["POST"])
@customer_required
def seller_event_nominate():
    user_id = session["user"]["id"]
    try:
        event_id = int(request.form.get("event_id", "0"))
        product_id = int(request.form.get("product_id", "0"))
        discount = to_dec(request.form.get("discount_percent", "0"), "Mức giảm", Decimal("1"), Decimal("90"))
        stock = int(request.form.get("committed_stock", "0"))
        note = request.form.get("seller_note", "").strip()[:500]
        if stock < 1:
            raise ValueError("Số lượng sản phẩm cam kết phải từ 1 trở lên.")
        with db() as (conn, cur):
            cur.execute("SELECT seller_id FROM sellers WHERE user_id=%s AND approved_at IS NOT NULL", (user_id,))
            seller = cur.fetchone()
            if not seller:
                raise ValueError("Shop chưa được duyệt.")
            cur.execute("""SELECT event_id FROM events WHERE event_id=%s AND status='Published'
                           AND end_at>NOW() FOR UPDATE""", (event_id,))
            if not cur.fetchone():
                raise ValueError("Sự kiện không còn nhận đăng ký.")
            cur.execute("""SELECT p.product_id,(SELECT COALESCE(SUM(v.quantity),0) FROM product_vari v
                           WHERE v.product_id=p.product_id) AS available_stock
                           FROM products p WHERE p.product_id=%s AND p.seller_id=%s
                           AND p.approval_status='Da duyet'""", (product_id,seller["seller_id"]))
            owned_product = cur.fetchone()
            if not owned_product:
                raise ValueError("Bạn chỉ được đăng ký sản phẩm đã được duyệt thuộc shop của mình.")
            if stock > owned_product["available_stock"]:
                raise ValueError(f"Số lượng cam kết vượt tồn kho hiện tại ({owned_product['available_stock']}).")
            cur.execute("SELECT status FROM event_nominations WHERE event_id=%s AND product_id=%s", (event_id,product_id))
            previous = cur.fetchone()
            if previous and previous["status"] == "Approved":
                raise ValueError("Sản phẩm đã được duyệt vào sự kiện. Liên hệ quản trị viên nếu cần điều chỉnh.")
            cur.execute("""INSERT INTO event_nominations
                (event_id,product_id,seller_id,discount_percent,committed_stock,seller_note,status)
                VALUES (%s,%s,%s,%s,%s,%s,'Pending')
                ON DUPLICATE KEY UPDATE seller_id=VALUES(seller_id),discount_percent=VALUES(discount_percent),
                committed_stock=VALUES(committed_stock),seller_note=VALUES(seller_note),status='Pending',
                admin_note=NULL,reviewed_by=NULL,reviewed_at=NULL""",
                (event_id,product_id,seller["seller_id"],discount,stock,note))
        flash("Đã gửi đề cử. Sản phẩm chỉ được giảm giá khi quản trị viên duyệt.", "success")
    except (ValueError, TypeError) as err:
        flash(str(err), "danger")
    return redirect(url_for("seller_events"))


@app.route("/seller/events/nomination/<int:nomination_id>/withdraw", methods=["POST"])
@customer_required
def seller_event_nomination_withdraw(nomination_id):
    with db() as (conn, cur):
        cur.execute("""UPDATE event_nominations n JOIN sellers s ON s.seller_id=n.seller_id
                       SET n.status='Withdrawn' WHERE n.nomination_id=%s AND s.user_id=%s
                       AND n.status='Pending'""", (nomination_id,session["user"]["id"]))
    flash("Đã rút đề cử đang chờ duyệt.", "success")
    return redirect(url_for("seller_events"))


@app.route("/admin/events/<int:event_id>/status", methods=["POST"])
@admin_required
def admin_event_status(event_id):
    status = request.form.get("status")
    if status not in ("Draft", "Published", "Cancelled"):
        flash("Trạng thái sự kiện không hợp lệ.", "danger")
    else:
        with db() as (conn, cur):
            cur.execute("UPDATE events SET status=%s WHERE event_id=%s", (status,event_id))
        flash("Đã cập nhật trạng thái sự kiện.", "success")
    return redirect(url_for("admin_events"))


@app.route("/admin/events/<int:event_id>/delete", methods=["POST"])
@admin_required
def admin_event_delete(event_id):
    with db() as (conn, cur):
        cur.execute("DELETE FROM events WHERE event_id=%s", (event_id,))
    flash("Đã xóa sự kiện.", "success")
    return redirect(url_for("admin_events"))


# ----------------------------------------------------------------------------
# TRANG CHỦ & CHI TIẾT SẢN PHẨM
# ----------------------------------------------------------------------------
SORTS = {
    "new": "p.product_id DESC",
    "price_asc": "min_price ASC",
    "price_desc": "min_price DESC",
    "rating": "avg_star DESC, p.product_id DESC",
}


@app.route("/")
def index():
    kw = request.args.get("kw", "").strip()
    cat_id = request.args.get("cat", type=int)
    brand_id = request.args.get("brand", type=int)
    sort = request.args.get("sort", "new")
    if sort not in SORTS:
        sort = "new"
    page = max(request.args.get("page", 1, type=int), 1)

    where, params = ["p.approval_status = 'Da duyet'"], []
    if kw:
        where.append("p.product_name LIKE %s")
        params.append(f"%{kw}%")
    if cat_id:
        where.append("p.category_id = %s")
        params.append(cat_id)
    if brand_id:
        where.append("p.brand_id = %s")
        params.append(brand_id)
    where_sql = " AND ".join(where)

    base = f"""
        FROM products p
        JOIN categories c ON c.categories_id = p.category_id
        JOIN sellers s ON s.seller_id = p.seller_id
        LEFT JOIN brands b ON b.brand_id = p.brand_id
        JOIN product_vari v ON v.product_id = p.product_id
        WHERE {where_sql}
        GROUP BY p.product_id, p.product_name, p.image_url, c.categories_name, s.shop_name, b.brand_name"""

    with db() as (conn, cur):
        cur.execute(f"SELECT COUNT(*) AS n FROM (SELECT p.product_id {base}) t", params)
        total = cur.fetchone()["n"]
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        page = min(page, pages)

        cur.execute(f"""
            SELECT p.product_id, p.product_name, p.image_url, c.categories_name, s.shop_name, b.brand_name,
                   MIN(ROUND(v.price * (1 - COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                       JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                       WHERE ep.product_id=p.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)/100),0)) AS min_price,
                   MAX(ROUND(v.price * (1 - COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                       JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                       WHERE ep.product_id=p.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)/100),0)) AS max_price,
                   MIN(v.price) AS original_min_price,
                   MAX(COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                       JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                       WHERE ep.product_id=p.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)) AS event_discount_percent,
                   SUM(v.quantity) AS stock,
                   (SELECT AVG(r.rating) FROM product_review r
                      JOIN order_details od ON od.order_detail_id = r.order_detail_id
                      JOIN product_vari vv ON vv.vari_id = od.vari_id
                     WHERE vv.product_id = p.product_id) AS avg_star
            {base}
            ORDER BY {SORTS[sort]} LIMIT %s OFFSET %s""", params + [PER_PAGE, (page - 1) * PER_PAGE])
        products = cur.fetchall()

        cur.execute("SELECT categories_id, categories_name FROM categories ORDER BY categories_name")
        categories = cur.fetchall()
        cur.execute("SELECT brand_id, brand_name FROM brands ORDER BY brand_name")
        brands = cur.fetchall()
        # Chiến dịch nổi bật hiển thị ngay trên trang chủ: đang diễn ra trước, kế tiếp sau.
        cur.execute("""SELECT e.*,COUNT(ep.product_id) AS product_count,
                       CASE WHEN NOW() BETWEEN e.start_at AND e.end_at THEN 'Đang diễn ra' ELSE 'Sắp diễn ra' END AS time_status
                       FROM events e LEFT JOIN event_products ep ON ep.event_id=e.event_id
                       WHERE e.status='Published' AND e.end_at>NOW()
                       GROUP BY e.event_id
                       ORDER BY (e.start_at<=NOW() AND e.end_at>=NOW()) DESC,e.start_at ASC LIMIT 4""")
        home_events = cur.fetchall()

        wished = set()
        u = session.get("user")
        if u and u.get("role") == "user" and products:
            cur.execute("SELECT product_id FROM wishlist WHERE user_id=%s", (u["id"],))
            wished = {r["product_id"] for r in cur.fetchall()}

    args = {k: v for k, v in {"kw": kw, "cat": cat_id, "brand": brand_id,
                              "sort": sort if sort != "new" else None}.items() if v}
    return render_template("index.html", products=products, categories=categories, brands=brands,
                           kw=kw, cat_id=cat_id, brand_id=brand_id, sort=sort, page=page, pages=pages,
                           total=total, args=args, wished=wished, home_events=home_events)


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    with db() as (conn, cur):
        cur.execute("""
            SELECT p.product_id, p.product_name, p.description, p.image_url, p.category_id,
                   c.categories_name, s.shop_name, s.seller_id, s.user_id AS seller_user, b.brand_name
            FROM products p
            JOIN categories c ON c.categories_id = p.category_id
            JOIN sellers s ON s.seller_id = p.seller_id
            LEFT JOIN brands b ON b.brand_id = p.brand_id
            WHERE p.product_id = %s AND p.approval_status = 'Da duyet'""", (product_id,))
        product = cur.fetchone()
        if not product:
            flash("Sản phẩm không tồn tại hoặc chưa được duyệt!", "warning")
            return redirect(url_for("index"))

        cur.execute("""SELECT v.vari_id,v.sku,v.size,v.color,v.price,v.quantity,
                       ROUND(v.price*(1-COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                         JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                         WHERE ep.product_id=v.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)/100),0) AS sale_price
                       FROM product_vari v WHERE v.product_id=%s ORDER BY v.price""", (product_id,))
        variants = cur.fetchall()

        cur.execute("""
            SELECT r.rating, r.comment, u.full_name, r.created_at
            FROM product_review r
            JOIN order_details od ON od.order_detail_id = r.order_detail_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN users u ON u.user_id = r.user_id
            WHERE v.product_id = %s ORDER BY r.created_at DESC""", (product_id,))
        reviews = cur.fetchall()

        cur.execute("""
            SELECT AVG(r.rating) AS avg_star, COUNT(*) AS count_star
            FROM product_review r
            JOIN order_details od ON od.order_detail_id = r.order_detail_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            WHERE v.product_id = %s""", (product_id,))
        star_stats = cur.fetchone()

        cur.execute("SELECT shipping_method_id, provider_name, base_cost FROM shipping_method WHERE is_active=TRUE")
        shipping_methods = cur.fetchall()

        cur.execute("""
            SELECT pr.code, pr.discount_percent, pr.discount_amount, pr.end_date
            FROM promotion pr JOIN promotion_product pp ON pp.promotion_id = pr.promotion_id
            WHERE pp.product_id = %s AND pr.is_active = TRUE AND NOW() BETWEEN pr.start_date AND pr.end_date""",
                    (product_id,))
        promotions = cur.fetchall()

        cur.execute("""
            SELECT p.product_id, p.product_name, p.image_url, MIN(v.price) AS min_price
            FROM products p JOIN product_vari v ON v.product_id = p.product_id
            WHERE p.approval_status='Da duyet' AND p.category_id=%s AND p.product_id<>%s
            GROUP BY p.product_id, p.product_name, p.image_url
            ORDER BY p.product_id DESC LIMIT 4""", (product["category_id"], product_id))
        related = cur.fetchall()

        in_wishlist, default_address = False, ""
        u = session.get("user")
        if u and u.get("role") == "user":
            cur.execute("SELECT 1 FROM wishlist WHERE user_id=%s AND product_id=%s", (u["id"], product_id))
            in_wishlist = cur.fetchone() is not None
            cur.execute("SELECT address FROM users WHERE user_id=%s", (u["id"],))
            default_address = (cur.fetchone() or {}).get("address") or ""

    return render_template("product_detail.html", product=product, variants=variants, reviews=reviews,
                           star_stats=star_stats, shipping_methods=shipping_methods, promotions=promotions,
                           related=related, in_wishlist=in_wishlist, default_address=default_address,
                           payment_methods=PAYMENT_METHODS)


# ----------------------------------------------------------------------------
# YÊU THÍCH
# ----------------------------------------------------------------------------
@app.route("/wishlist")
@customer_required
def wishlist():
    with db() as (conn, cur):
        cur.execute("""
            SELECT p.product_id, p.product_name, p.image_url, s.shop_name, c.categories_name,
                   MIN(v.price) AS min_price, MAX(v.price) AS max_price, SUM(v.quantity) AS stock
            FROM wishlist w
            JOIN products p ON p.product_id = w.product_id AND p.approval_status='Da duyet'
            JOIN categories c ON c.categories_id = p.category_id
            JOIN sellers s ON s.seller_id = p.seller_id
            JOIN product_vari v ON v.product_id = p.product_id
            WHERE w.user_id=%s
            GROUP BY p.product_id, p.product_name, p.image_url, s.shop_name, c.categories_name, w.created_at
            ORDER BY w.created_at DESC""", (session["user"]["id"],))
        products = cur.fetchall()
    return render_template("wishlist.html", products=products)


@app.route("/wishlist/toggle/<int:product_id>", methods=["POST"])
@customer_required
def wishlist_toggle(product_id):
    uid = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("SELECT 1 FROM products WHERE product_id=%s", (product_id,))
        if not cur.fetchone():
            flash("Sản phẩm không tồn tại.", "warning")
            return back()
        cur.execute("DELETE FROM wishlist WHERE user_id=%s AND product_id=%s", (uid, product_id))
        if cur.rowcount:
            flash("Đã bỏ khỏi danh sách yêu thích.", "info")
        else:
            cur.execute("INSERT INTO wishlist (user_id, product_id) VALUES (%s,%s)", (uid, product_id))
            flash("Đã thêm vào danh sách yêu thích.", "success")
    return back()


# ----------------------------------------------------------------------------
# GIỎ HÀNG
# ----------------------------------------------------------------------------
@app.route("/cart")
@customer_required
def cart():
    uid = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("""
            SELECT ci.cart_item_id, ci.quantity, v.vari_id, v.size, v.color,
                   ROUND(v.price * (1 - COALESCE((SELECT MAX(ep.discount_percent) FROM event_products ep
                       JOIN events ev ON ev.event_id=ep.event_id AND ev.status='Published'
                       WHERE ep.product_id=p.product_id AND NOW() BETWEEN ev.start_at AND ev.end_at),0)/100),0) AS price,
                   v.quantity AS stock, p.product_id, p.product_name, p.image_url, s.shop_name, s.seller_id
            FROM cart_items ci
            JOIN product_vari v ON v.vari_id = ci.vari_id
            JOIN products p ON p.product_id = v.product_id AND p.approval_status='Da duyet'
            JOIN sellers s ON s.seller_id = p.seller_id
            WHERE ci.user_id=%s ORDER BY s.shop_name, ci.added_at DESC""", (uid,))
        items = cur.fetchall()
        cur.execute("SELECT shipping_method_id, provider_name, base_cost FROM shipping_method WHERE is_active=TRUE")
        shipping_methods = cur.fetchall()
        cur.execute("""SELECT u.address, COALESCE(r.discount_rate,0) AS rate, r.rank_name
                       FROM users u LEFT JOIN user_rank r ON r.rank_id=u.rank_id WHERE u.user_id=%s""", (uid,))
        me = cur.fetchone()
    subtotal = sum(i["price"] * i["quantity"] for i in items if i["stock"] > 0)
    return render_template("cart.html", items=items, shipping_methods=shipping_methods, me=me,
                           subtotal=subtotal, payment_methods=PAYMENT_METHODS)


@app.route("/cart/add", methods=["POST"])
@customer_required
def cart_add():
    vari_id = to_int(request.form.get("vari_id"))
    qty = max(to_int(request.form.get("quantity"), 1), 1)
    uid = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("""
            SELECT v.quantity AS stock, s.user_id AS seller_user
            FROM product_vari v JOIN products p ON p.product_id=v.product_id
            JOIN sellers s ON s.seller_id=p.seller_id
            WHERE v.vari_id=%s AND p.approval_status='Da duyet'""", (vari_id,))
        v = cur.fetchone()
        if not v:
            flash("Sản phẩm không tồn tại hoặc đã ngừng bán.", "warning")
            return back()
        if v["seller_user"] == uid:
            flash("Bạn không thể mua sản phẩm của chính shop mình!", "warning")
            return back()
        if v["stock"] <= 0:
            flash("Phân loại này đã hết hàng.", "warning")
            return back()
        cur.execute("SELECT quantity FROM cart_items WHERE user_id=%s AND vari_id=%s", (uid, vari_id))
        existing = cur.fetchone()
        new_qty = min((existing["quantity"] if existing else 0) + qty, v["stock"])
        if existing:
            cur.execute("UPDATE cart_items SET quantity=%s WHERE user_id=%s AND vari_id=%s", (new_qty, uid, vari_id))
        else:
            cur.execute("INSERT INTO cart_items (user_id, vari_id, quantity) VALUES (%s,%s,%s)", (uid, vari_id, new_qty))
    flash("Đã thêm vào giỏ hàng.", "success")
    return back()


@app.route("/cart/update", methods=["POST"])
@customer_required
def cart_update():
    item_id = to_int(request.form.get("cart_item_id"))
    qty = to_int(request.form.get("quantity"), 1)
    uid = session["user"]["id"]
    with db() as (conn, cur):
        if qty < 1:
            cur.execute("DELETE FROM cart_items WHERE cart_item_id=%s AND user_id=%s", (item_id, uid))
        else:
            cur.execute("""SELECT v.quantity AS stock FROM cart_items ci JOIN product_vari v ON v.vari_id=ci.vari_id
                           WHERE ci.cart_item_id=%s AND ci.user_id=%s""", (item_id, uid))
            row = cur.fetchone()
            if row:
                if qty > row["stock"]:
                    qty = max(row["stock"], 1)
                    flash(f"Chỉ còn {row['stock']} sản phẩm trong kho.", "warning")
                cur.execute("UPDATE cart_items SET quantity=%s WHERE cart_item_id=%s AND user_id=%s", (qty, item_id, uid))
    return redirect(url_for("cart"))


@app.route("/cart/remove/<int:item_id>", methods=["POST"])
@customer_required
def cart_remove(item_id):
    with db() as (conn, cur):
        cur.execute("DELETE FROM cart_items WHERE cart_item_id=%s AND user_id=%s", (item_id, session["user"]["id"]))
    flash("Đã xóa sản phẩm khỏi giỏ hàng.", "info")
    return redirect(url_for("cart"))


@app.route("/cart/checkout", methods=["POST"])
@customer_required
def cart_checkout():
    uid = session["user"]["id"]
    selected = [to_int(x) for x in request.form.getlist("selected")]
    selected = [x for x in selected if x]
    if not selected:
        flash("Hãy chọn ít nhất một sản phẩm để thanh toán.", "warning")
        return redirect(url_for("cart"))
    try:
        with db() as (conn, cur):
            marks = ",".join(["%s"] * len(selected))
            cur.execute(f"SELECT cart_item_id, vari_id, quantity FROM cart_items WHERE user_id=%s AND cart_item_id IN ({marks})",
                        [uid] + selected)
            rows = cur.fetchall()
            results = create_orders(
                cur, uid, [(r["vari_id"], r["quantity"]) for r in rows],
                to_int(request.form.get("shipping_method_id")),
                request.form.get("payment_method"),
                request.form.get("address", ""),
                request.form.get("promo_code", "").strip().upper())
            cur.execute(f"DELETE FROM cart_items WHERE user_id=%s AND cart_item_id IN ({marks})", [uid] + selected)
    except ValueError as err:
        flash(str(err), "danger")
        return redirect(url_for("cart"))
    except dberr.Error as err:
        flash(f"Lỗi hệ thống khi đặt hàng: {err}", "danger")
        return redirect(url_for("cart"))

    codes = ", ".join(f"#{r['order_id']}" for r in results)
    saved = sum(r["discount"] for r in results)
    flash(f"Đặt hàng thành công! Mã đơn: {codes}" + (f" (đã giảm {saved:,.0f}đ)" if saved else ""), "success")
    return redirect(url_for("my_orders"))


# ----------------------------------------------------------------------------
# ĐẶT HÀNG NGAY, ĐƠN HÀNG & ĐÁNH GIÁ
# ----------------------------------------------------------------------------
@app.route("/order/place", methods=["POST"])
@customer_required
def place_order():
    uid = session["user"]["id"]
    try:
        with db() as (conn, cur):
            results = create_orders(
                cur, uid,
                [(to_int(request.form.get("vari_id")), to_int(request.form.get("quantity"), 1))],
                to_int(request.form.get("shipping_method_id")),
                request.form.get("payment_method"),
                request.form.get("address", ""),
                request.form.get("promo_code", "").strip().upper())
    except ValueError as err:
        flash(str(err), "danger")
        return back()
    except dberr.Error as err:
        flash(f"Lỗi hệ thống khi đặt hàng: {err}", "danger")
        return back()
    r = results[0]
    flash(f"Đặt hàng thành công! Mã đơn: #{r['order_id']} (đã giảm {r['discount']:,.0f}đ)", "success")
    return redirect(url_for("order_detail", order_id=r["order_id"]))


@app.route("/notifications")
@login_required
def notifications_page():
    if session.get("user", {}).get("role") != "user":
        return redirect(url_for("admin_panel"))
    with db() as (conn, cur):
        cur.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY notification_id DESC LIMIT 100",
                    (session["user"]["id"],))
        rows = cur.fetchall()
        cur.execute("UPDATE notifications SET is_read=TRUE WHERE user_id=%s", (session["user"]["id"],))
    return render_template("notifications.html", notifications=rows)


def vietqr_image_url(order_id, amount):
    """Tạo URL ảnh VietQR; thông tin tài khoản được cấu hình trong .env."""
    bank_id = os.getenv("BANK_ID", "").strip()
    account_no = os.getenv("BANK_ACCOUNT_NO", "").strip()
    template = os.getenv("BANK_QR_TEMPLATE", "compact2").strip() or "compact2"
    if not bank_id or not account_no:
        return None
    query = urllib.parse.urlencode({
        "amount": int(Decimal(str(amount)).quantize(Decimal("1"))),
        "addInfo": f"MINISHOP{order_id}",
        "accountName": os.getenv("BANK_ACCOUNT_NAME", "")
    })
    return f"https://img.vietqr.io/image/{urllib.parse.quote(bank_id)}-{urllib.parse.quote(account_no)}-{urllib.parse.quote(template)}.png?{query}"


@app.route("/payment/<int:order_id>/start", methods=["POST"])
@customer_required
def payment_start(order_id):
    uid = session["user"]["id"]
    try:
        with db() as (conn, cur):
            cur.execute("""SELECT o.order_id,o.final_amount,o.status,p.payment_id,p.payment_method,p.payment_status
                           FROM orders o JOIN payment p ON p.order_id=o.order_id
                           WHERE o.order_id=%s AND o.user_id=%s FOR UPDATE""", (order_id, uid))
            row = cur.fetchone()
            if not row:
                flash("Không tìm thấy đơn hàng.", "danger")
                return redirect(url_for("my_orders"))
            if row["payment_status"] == "Da thanh toan":
                flash("Đơn hàng đã được thanh toán.", "info")
                return redirect(url_for("order_detail", order_id=order_id))
            if row["status"] == "Da huy":
                flash("Đơn hàng đã hủy, không thể thanh toán.", "warning")
                return redirect(url_for("order_detail", order_id=order_id))
            method = row["payment_method"]
            if method == "DEMO":
                # Chỉ mô phỏng trong môi trường demo; không dùng cho giao dịch thật.
                cur.execute("UPDATE payment SET payment_status='Da thanh toan', transaction_code=%s, paid_at=NOW() WHERE payment_id=%s",
                            (f"DEMO-{uuid.uuid4().hex[:12]}", row["payment_id"]))
                cur.execute("INSERT INTO payment_events (payment_id,provider,event_type,payload) VALUES (%s,'DEMO','SIMULATED_SUCCESS',%s)",
                            (row["payment_id"], json.dumps({"environment": "demo", "order_id": order_id}, ensure_ascii=False)))
                notify_user(cur, uid, "Thanh toán mô phỏng thành công", f"Đơn hàng #{order_id} đã được đánh dấu thanh toán trong chế độ demo.", f"/orders/{order_id}")
                flash("Thanh toán DEMO thành công. Đây không phải giao dịch tiền thật.", "success")
                return redirect(url_for("order_detail", order_id=order_id))
            if method == "VNPAY":
                pay_url = build_vnpay_url(order_id, row["final_amount"], f"Thanh toan don hang {order_id}")
                cur.execute("INSERT INTO payment_events (payment_id,provider,event_type,payload) VALUES (%s,'VNPAY','PAYMENT_URL_CREATED',%s)",
                            (row["payment_id"], json.dumps({"order_id": order_id}, ensure_ascii=False)))
                return redirect(pay_url)
            if method == "MOMO":
                data, momo_ref = create_momo_payment(order_id, row["final_amount"], f"Thanh toan don hang {order_id}")
                cur.execute("UPDATE payment SET transaction_code=%s WHERE payment_id=%s", (momo_ref, row["payment_id"]))
                cur.execute("INSERT INTO payment_events (payment_id,provider,provider_transaction_id,event_type,payload) VALUES (%s,'MOMO',%s,'PAYMENT_URL_CREATED',%s)",
                            (row["payment_id"], momo_ref, json.dumps(data, ensure_ascii=False)))
                return redirect(data["payUrl"])
            if method == "BANK_QR":
                if not os.getenv("BANK_ID") or not os.getenv("BANK_ACCOUNT_NO"):
                    flash("Chưa cấu hình tài khoản nhận tiền. Vui lòng liên hệ quản trị viên.", "warning")
                else:
                    cur.execute("INSERT INTO payment_events (payment_id,provider,event_type,payload) VALUES (%s,'VIETQR','QR_DISPLAYED',%s)",
                                (row["payment_id"], json.dumps({"order_id": order_id}, ensure_ascii=False)))
                return redirect(url_for("order_detail", order_id=order_id))
            flash("COD không cần thanh toán trực tuyến.", "info")
            return redirect(url_for("order_detail", order_id=order_id))
    except (ValueError, requests.RequestException, mysql.connector.Error) as err:
        flash(f"Không thể khởi tạo thanh toán: {err}", "danger")
        return redirect(url_for("order_detail", order_id=order_id))


@app.route("/payment/bank/webhook", methods=["POST"])
def bank_transfer_webhook():
    """Webhook đối soát VietQR/SePay. Không bật xác nhận nếu chưa cấu hình token."""
    token = os.getenv("BANK_WEBHOOK_TOKEN", "")
    if not token:
        return {"message": "Bank webhook is not configured"}, 503
    auth = request.headers.get("Authorization", "")
    supplied = auth.replace("Bearer ", "", 1).replace("Apikey ", "", 1).strip()
    supplied = supplied or request.headers.get("X-Webhook-Token", "")
    if not hmac.compare_digest(token, supplied):
        return {"message": "Unauthorized"}, 401
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or data.get("description") or data.get("transferContent") or "")
    match = re.search(r"MINISHOP\s*(\d+)", content, re.IGNORECASE)
    if not match:
        return {"message": "Order reference not found"}, 400
    order_id = int(match.group(1))
    try:
        amount = int(Decimal(str(data.get("transferAmount", data.get("amount", 0)))))
    except Exception:
        return {"message": "Invalid transfer amount"}, 400
    transaction_id = str(data.get("id") or data.get("referenceCode") or data.get("transactionId") or "")
    transfer_type = str(data.get("transferType") or data.get("transfer_type") or "").lower()
    if amount <= 0 or not transaction_id or transfer_type != "in":
        return {"message": "Missing or invalid incoming transaction data"}, 400
    configured_account = os.getenv("BANK_ACCOUNT_NO", "").strip()
    received_account = str(data.get("accountNumber") or data.get("account_number") or "").strip()
    if not configured_account or not received_account or received_account != configured_account:
        return {"message": "Bank account mismatch or not configured"}, 400
    with db() as (conn, cur):
        cur.execute("""SELECT p.payment_id,p.amount,p.payment_status,o.user_id,o.status
                       FROM payment p JOIN orders o ON o.order_id=p.order_id
                       WHERE p.order_id=%s AND p.payment_method='BANK_QR' FOR UPDATE""", (order_id,))
        pay = cur.fetchone()
        if not pay or pay["status"] == "Da huy" or amount != int(Decimal(str(pay["amount"]))):
            return {"message": "Order or amount mismatch"}, 400
        cur.execute("SELECT event_id FROM payment_events WHERE provider='VIETQR' AND provider_transaction_id=%s LIMIT 1", (transaction_id,))
        if cur.fetchone():
            return {"success": True, "duplicate": True}, 200
        cur.execute("""UPDATE payment SET payment_status='Da thanh toan',transaction_code=%s,paid_at=NOW()
                       WHERE payment_id=%s AND payment_status<>'Da thanh toan'""", (transaction_id, pay["payment_id"]))
        cur.execute("""INSERT INTO payment_events (payment_id,provider,provider_transaction_id,event_type,payload)
                       VALUES (%s,'VIETQR',%s,'BANK_TRANSFER_RECEIVED',%s)""",
                    (pay["payment_id"], transaction_id, json.dumps(data, ensure_ascii=False)))
        if cur.rowcount and pay["payment_status"] != "Da thanh toan":
            notify_user(cur, pay["user_id"], "Đã nhận thanh toán ngân hàng", f"Đơn hàng #{order_id} đã được xác nhận chuyển khoản.", f"/orders/{order_id}")
    # SePay yêu cầu HTTP 200/201 và JSON success=true để ghi nhận webhook thành công.
    return {"success": True}, 200


@app.route("/payment/vnpay/return", methods=["GET"])
def vnpay_return():
    params = request.args.to_dict(flat=True)
    if not verify_vnpay(params):
        flash("Chữ ký phản hồi VNPay không hợp lệ. Không ghi nhận thanh toán.", "danger")
        return redirect(url_for("my_orders"))
    order_id = to_int(params.get("vnp_TxnRef"))
    success = params.get("vnp_ResponseCode") == "00" and params.get("vnp_TransactionStatus") == "00"
    with db() as (conn, cur):
        cur.execute("""SELECT p.payment_id,p.amount,o.user_id,o.status FROM payment p JOIN orders o ON o.order_id=p.order_id
                       WHERE p.order_id=%s AND p.payment_method='VNPAY' FOR UPDATE""", (order_id,))
        payment = cur.fetchone()
        if not payment or payment["status"] == "Da huy" or int(params.get("vnp_Amount", "0")) != int(Decimal(str(payment["amount"])) * 100):
            flash("Thông tin giao dịch VNPay không khớp đơn hàng.", "danger")
            return redirect(url_for("my_orders"))
        if success:
            cur.execute("UPDATE payment SET payment_status='Da thanh toan', transaction_code=%s, paid_at=NOW() WHERE payment_id=%s AND payment_status<>'Da thanh toan'",
                        (params.get("vnp_TransactionNo", ""), payment["payment_id"]))
            if cur.rowcount == 1:
                notify_user(cur, payment["user_id"], "Thanh toán thành công", f"Đơn hàng #{order_id} đã thanh toán qua VNPay.", f"/orders/{order_id}")
        cur.execute("INSERT INTO payment_events (payment_id,provider,provider_transaction_id,event_type,payload) VALUES (%s,'VNPAY',%s,%s,%s)",
                    (payment["payment_id"], params.get("vnp_TransactionNo"), "RETURN_SUCCESS" if success else "RETURN_FAILED", json.dumps(params, ensure_ascii=False)))
    flash("Thanh toán VNPay thành công." if success else "Giao dịch VNPay chưa thành công.", "success" if success else "warning")
    return redirect(url_for("order_detail", order_id=order_id))


@app.route("/payment/momo/return", methods=["GET"])
def momo_return():
    # Redirect URL chỉ để hiển thị kết quả; trạng thái đáng tin cậy được cập nhật từ IPN có chữ ký.
    order_ref = request.args.get("orderId", "")
    match = re.match(r"MINISHOP_(\d+)_", order_ref)
    if match:
        return redirect(url_for("order_detail", order_id=int(match.group(1))))
    flash("Không xác định được đơn hàng MoMo.", "warning")
    return redirect(url_for("my_orders"))


@app.route("/payment/momo/ipn", methods=["POST"])
def momo_ipn():
    data = request.get_json(silent=True) or {}
    secret = os.getenv("MOMO_SECRET_KEY", "")
    access_key = os.getenv("MOMO_ACCESS_KEY", "")
    if not secret or not access_key:
        return {"message": "MoMo chưa được cấu hình"}, 503
    raw_keys = ("accessKey", "amount", "extraData", "message", "orderId", "orderInfo", "orderType",
                "partnerCode", "payType", "requestId", "responseTime", "resultCode", "transId")
    raw = "&".join(f"{k}={data.get(k, '')}" for k in raw_keys)
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if (str(data.get("accessKey", "")) != access_key
            or not hmac.compare_digest(expected, str(data.get("signature", "")))):
        return {"message": "Invalid signature"}, 400
    match = re.match(r"MINISHOP_(\d+)_", str(data.get("orderId", "")))
    if not match:
        return {"message": "Invalid order"}, 400
    order_id = int(match.group(1))
    with db() as (conn, cur):
        cur.execute("""SELECT p.payment_id,p.amount,o.user_id,o.status FROM payment p JOIN orders o ON o.order_id=p.order_id
                       WHERE p.order_id=%s AND p.payment_method='MOMO' FOR UPDATE""", (order_id,))
        payment = cur.fetchone()
        if (not payment or payment["status"] == "Da huy"
                or str(data.get("partnerCode", "")) != os.getenv("MOMO_PARTNER_CODE", "")
                or int(data.get("amount", 0)) != int(payment["amount"])):
            return {"message": "Amount mismatch"}, 400
        success = int(data.get("resultCode", -1)) == 0
        if success:
            cur.execute("UPDATE payment SET payment_status='Da thanh toan', transaction_code=%s, paid_at=NOW() WHERE payment_id=%s AND payment_status<>'Da thanh toan'",
                        (str(data.get("transId", "")), payment["payment_id"]))
            if cur.rowcount == 1:
                notify_user(cur, payment["user_id"], "Thanh toán thành công", f"Đơn hàng #{order_id} đã thanh toán qua MoMo.", f"/orders/{order_id}")
        cur.execute("INSERT INTO payment_events (payment_id,provider,provider_transaction_id,event_type,payload) VALUES (%s,'MOMO',%s,%s,%s)",
                    (payment["payment_id"], str(data.get("transId", "")), "IPN_SUCCESS" if success else "IPN_FAILED", json.dumps(data, ensure_ascii=False)))
    return {"message": "ok"}, 200


@app.route("/orders")
@customer_required
def my_orders():
    status = request.args.get("status", "")
    uid = session["user"]["id"]
    with db() as (conn, cur):
        sql = """SELECT o.order_id, o.order_date, o.total_amount, o.discount_amount, o.final_amount, o.status,
                        o.shipping_address, pay.payment_method, pay.payment_status
                 FROM orders o LEFT JOIN payment pay ON pay.order_id = o.order_id WHERE o.user_id=%s"""
        params = [uid]
        if status:
            sql += " AND o.status=%s"
            params.append(status)
        cur.execute(sql + " ORDER BY o.order_id DESC", params)
        orders = cur.fetchall()
        if orders:
            marks = ",".join(["%s"] * len(orders))
            cur.execute(f"""
                SELECT od.order_id, od.order_detail_id, od.quantity, od.unit_price, p.product_id, p.product_name,
                       p.image_url, v.size, v.color, pr.review_id
                FROM order_details od
                JOIN product_vari v ON v.vari_id = od.vari_id
                JOIN products p ON p.product_id = v.product_id
                LEFT JOIN product_review pr ON pr.order_detail_id = od.order_detail_id
                WHERE od.order_id IN ({marks})""", [o["order_id"] for o in orders])
            by_order = {}
            for it in cur.fetchall():
                by_order.setdefault(it["order_id"], []).append(it)
            for o in orders:
                o["items"] = by_order.get(o["order_id"], [])
    return render_template("orders.html", orders=orders, status=status)


def can_view_order(cur, order_id):
    """Trả về (order, vai_trò) nếu người dùng hiện tại được xem đơn này."""
    u = session["user"]
    cur.execute("SELECT * FROM orders WHERE order_id=%s", (order_id,))
    order = cur.fetchone()
    if not order:
        return None, None
    if u.get("role") == "admin":
        return order, "admin"
    if order["user_id"] == u["id"]:
        return order, "buyer"
    cur.execute("""SELECT 1 FROM order_details od JOIN product_vari v ON v.vari_id=od.vari_id
                   JOIN products p ON p.product_id=v.product_id JOIN sellers s ON s.seller_id=p.seller_id
                   WHERE od.order_id=%s AND s.user_id=%s LIMIT 1""", (order_id, u["id"]))
    if cur.fetchone():
        return order, "seller"
    return None, None


@app.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    with db() as (conn, cur):
        order, role = can_view_order(cur, order_id)
        if not order:
            flash("Không tìm thấy đơn hàng.", "warning")
            return redirect(url_for("index"))
        cur.execute("""
            SELECT od.order_detail_id, od.quantity, od.unit_price, od.subtotal, v.sku, v.size, v.color,
                   p.product_id, p.product_name, p.image_url, s.shop_name, pr.review_id
            FROM order_details od
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN products p ON p.product_id = v.product_id
            JOIN sellers s ON s.seller_id = p.seller_id
            LEFT JOIN product_review pr ON pr.order_detail_id = od.order_detail_id
            WHERE od.order_id=%s""", (order_id,))
        items = cur.fetchall()
        cur.execute("SELECT * FROM payment WHERE order_id=%s", (order_id,))
        payment = cur.fetchone()
        cur.execute("""SELECT sh.*, sm.provider_name, sm.base_cost FROM shipment sh
                       JOIN shipping_method sm ON sm.shipping_method_id=sh.shipping_method_id
                       WHERE sh.order_id=%s""", (order_id,))
        shipment = cur.fetchone()
        cur.execute("SELECT full_name, phone, email FROM users WHERE user_id=%s", (order["user_id"],))
        customer = cur.fetchone()
    bank_qr_url = vietqr_image_url(order_id, payment["amount"]) if payment and payment["payment_method"] == "BANK_QR" else None
    return render_template("order_detail.html", order=order, items=items, payment=payment,
                           shipment=shipment, customer=customer, viewer=role, bank_qr_url=bank_qr_url,
                           bank_account_no=os.getenv("BANK_ACCOUNT_NO", ""), bank_account_name=os.getenv("BANK_ACCOUNT_NAME", ""),
                           bank_name=os.getenv("BANK_DISPLAY_NAME", os.getenv("BANK_ID", "")))


@app.route("/orders/cancel/<int:order_id>", methods=["POST"])
@customer_required
def cancel_order(order_id):
    uid = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("SELECT o.status,p.payment_status FROM orders o LEFT JOIN payment p ON p.order_id=o.order_id WHERE o.order_id=%s AND o.user_id=%s FOR UPDATE", (order_id, uid))
        o = cur.fetchone()
        if not o or o["status"] != "Cho xac nhan":
            flash("Đơn hàng không thể hủy!", "warning")
            return back("my_orders")
        if o["payment_status"] == "Da thanh toan":
            flash("Đơn đã thanh toán. Vui lòng gửi yêu cầu hỗ trợ/hoàn tiền thay vì tự hủy đơn.", "warning")
            return back("my_orders")
        restore_stock(cur, order_id)
        cur.execute("UPDATE orders SET status='Da huy' WHERE order_id=%s", (order_id,))
        cur.execute("UPDATE payment SET payment_status='Da huy' WHERE order_id=%s", (order_id,))
        cur.execute("UPDATE shipment SET shipping_status='Da huy' WHERE order_id=%s", (order_id,))
    flash(f"Đã hủy đơn hàng #{order_id} và hoàn lại tồn kho.", "success")
    return back("my_orders")


@app.route("/orders/reorder/<int:order_id>", methods=["POST"])
@customer_required
def reorder(order_id):
    """Mua lại: đưa các sản phẩm của đơn cũ vào giỏ hàng (bỏ qua mục hết hàng)."""
    uid = session["user"]["id"]
    added = skipped = 0
    with db() as (conn, cur):
        cur.execute("SELECT 1 FROM orders WHERE order_id=%s AND user_id=%s", (order_id, uid))
        if not cur.fetchone():
            flash("Không tìm thấy đơn hàng.", "warning")
            return redirect(url_for("my_orders"))
        cur.execute("""SELECT od.vari_id, od.quantity, v.quantity AS stock, s.user_id AS seller_user
                       FROM order_details od JOIN product_vari v ON v.vari_id=od.vari_id
                       JOIN products p ON p.product_id=v.product_id AND p.approval_status='Da duyet'
                       JOIN sellers s ON s.seller_id=p.seller_id WHERE od.order_id=%s""", (order_id,))
        for it in cur.fetchall():
            if it["stock"] <= 0 or it["seller_user"] == uid:
                skipped += 1
                continue
            cur.execute("SELECT quantity FROM cart_items WHERE user_id=%s AND vari_id=%s", (uid, it["vari_id"]))
            ex = cur.fetchone()
            qty = min(it["quantity"] + (ex["quantity"] if ex else 0), it["stock"])
            if ex:
                cur.execute("UPDATE cart_items SET quantity=%s WHERE user_id=%s AND vari_id=%s", (qty, uid, it["vari_id"]))
            else:
                cur.execute("INSERT INTO cart_items (user_id, vari_id, quantity) VALUES (%s,%s,%s)", (uid, it["vari_id"], qty))
            added += 1
    msg = f"Đã thêm {added} sản phẩm vào giỏ hàng." + (f" Bỏ qua {skipped} sản phẩm đã hết hàng." if skipped else "")
    flash(msg, "success" if added else "warning")
    return redirect(url_for("cart") if added else url_for("my_orders"))


@app.route("/review/add", methods=["POST"])
@customer_required
def add_review():
    od_id = to_int(request.form.get("order_detail_id"))
    rating = to_int(request.form.get("rating"))
    comment = request.form.get("comment", "").strip()[:1000]
    if not od_id or rating not in (1, 2, 3, 4, 5):
        flash("Dữ liệu đánh giá không hợp lệ.", "danger")
        return redirect(url_for("my_orders"))
    uid = session["user"]["id"]
    try:
        with db() as (conn, cur):
            cur.execute("""SELECT o.status FROM order_details od JOIN orders o ON o.order_id=od.order_id
                           WHERE od.order_detail_id=%s AND o.user_id=%s""", (od_id, uid))
            row = cur.fetchone()
            if not row or row["status"] != "Da giao":
                flash("Bạn chỉ có thể đánh giá sản phẩm của đơn hàng đã giao.", "warning")
                return redirect(url_for("my_orders"))
            cur.execute("INSERT INTO product_review (order_detail_id, user_id, rating, comment) VALUES (%s,%s,%s,%s)",
                        (od_id, uid, rating, comment))
        flash("Cảm ơn bạn đã gửi đánh giá sản phẩm!", "success")
    except dberr.IntegrityError:
        flash("Bạn đã đánh giá sản phẩm này rồi!", "warning")
    return back("my_orders")


# ----------------------------------------------------------------------------
# TÀI KHOẢN: HỒ SƠ, ẢNH ĐẠI DIỆN, ĐỔI MẬT KHẨU
# ----------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^0\d{9}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")


@app.route("/profile")
@login_required
def profile():
    u = session["user"]
    stats = {}
    with db() as (conn, cur):
        if u["role"] == "admin":
            cur.execute("""SELECT a.admin_id, a.user_admin AS username, a.full_name, a.phone, a.email,
                                  a.created_at, r.roles_name FROM admin_user a
                           JOIN admin_roles r ON r.roles_id=a.roles_id WHERE a.admin_id=%s""", (u["id"],))
            p = cur.fetchone()
            return render_template("profile.html", profile=p, stats=stats, rank=None, next_rank=None)

        cur.execute("""SELECT u.*, r.rank_name, r.discount_rate, r.min_spent FROM users u
                       LEFT JOIN user_rank r ON r.rank_id=u.rank_id WHERE u.user_id=%s""", (u["id"],))
        p = cur.fetchone()
        cur.execute("""SELECT COUNT(*) AS orders, COALESCE(SUM(CASE WHEN status='Da giao' THEN final_amount END),0) AS spent
                       FROM orders WHERE user_id=%s""", (u["id"],))
        stats = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM wishlist WHERE user_id=%s", (u["id"],))
        stats["wishlist"] = cur.fetchone()["n"]
        cur.execute("SELECT * FROM user_rank WHERE min_spent > %s ORDER BY min_spent LIMIT 1", (stats["spent"],))
        next_rank = cur.fetchone()
        cur.execute("SELECT shop_name, approved_at FROM sellers WHERE user_id=%s", (u["id"],))
        shop = cur.fetchone()
    return render_template("profile.html", profile=p, stats=stats, next_rank=next_rank, shop=shop)


@app.route("/profile/update", methods=["POST"])
@customer_required
def profile_update():
    full_name = request.form.get("full_name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    address = request.form.get("address", "").strip()
    if len(full_name) < 2:
        flash("Họ tên phải có ít nhất 2 ký tự.", "danger")
    elif not PHONE_RE.match(phone):
        flash("Số điện thoại gồm 10 chữ số, bắt đầu bằng 0.", "danger")
    elif not EMAIL_RE.match(email):
        flash("Email không hợp lệ.", "danger")
    elif not address:
        flash("Vui lòng nhập địa chỉ.", "danger")
    else:
        try:
            with db() as (conn, cur):
                cur.execute("UPDATE users SET full_name=%s, phone=%s, email=%s, address=%s WHERE user_id=%s",
                            (full_name, phone, email, address, session["user"]["id"]))
            session["user"] = {**session["user"], "name": full_name}
            flash("Đã cập nhật thông tin cá nhân.", "success")
        except dberr.IntegrityError:
            flash("Email này đã được dùng bởi tài khoản khác.", "danger")
    return redirect(url_for("profile"))


@app.route("/profile/avatar", methods=["POST"])
@customer_required
def profile_avatar():
    uid = session["user"]["id"]
    if request.form.get("remove"):
        with db() as (conn, cur):
            cur.execute("SELECT avatar_url FROM users WHERE user_id=%s", (uid,))
            old = (cur.fetchone() or {}).get("avatar_url")
            cur.execute("UPDATE users SET avatar_url=NULL WHERE user_id=%s", (uid,))
        remove_upload(old)
        session["user"] = {**session["user"], "avatar": None}
        flash("Đã xóa ảnh đại diện.", "info")
        return redirect(url_for("profile"))
    try:
        filename = validate_and_save_image(request.files.get("avatar"))
    except ValueError as err:
        flash(f"Lỗi ảnh: {err}", "danger")
        return redirect(url_for("profile"))
    if not filename:
        flash("Hãy chọn một ảnh để tải lên.", "warning")
        return redirect(url_for("profile"))
    with db() as (conn, cur):
        cur.execute("SELECT avatar_url FROM users WHERE user_id=%s", (uid,))
        old = (cur.fetchone() or {}).get("avatar_url")
        cur.execute("UPDATE users SET avatar_url=%s WHERE user_id=%s", (filename, uid))
    remove_upload(old)
    session["user"] = {**session["user"], "avatar": filename}
    flash("Đã cập nhật ảnh đại diện.", "success")
    return redirect(url_for("profile"))


@app.route("/profile/password", methods=["POST"])
@login_required
def profile_password():
    u = session["user"]
    old = request.form.get("old_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    table, id_col, hash_col = (("admin_user", "admin_id", "password_hash") if u["role"] == "admin"
                               else ("users", "user_id", "pass_hash"))
    if len(new) < 6:
        flash("Mật khẩu mới tối thiểu 6 ký tự.", "danger")
    elif new != confirm:
        flash("Mật khẩu xác nhận không khớp.", "danger")
    else:
        with db() as (conn, cur):
            cur.execute(f"SELECT {hash_col} AS h FROM {table} WHERE {id_col}=%s", (u["id"],))
            row = cur.fetchone()
            if not row or not check_password_hash(row["h"], old):
                flash("Mật khẩu hiện tại không đúng.", "danger")
            else:
                cur.execute(f"UPDATE {table} SET {hash_col}=%s WHERE {id_col}=%s",
                            (generate_password_hash(new), u["id"]))
                flash("Đã đổi mật khẩu thành công.", "success")
    return redirect(url_for("profile"))


# ----------------------------------------------------------------------------
# KÊNH NGƯỜI BÁN
# ----------------------------------------------------------------------------
@app.route("/seller", methods=["GET", "POST"])
@customer_required
def seller_channel():
    user_id = session["user"]["id"]
    with db() as (conn, cur):
        cur.execute("SELECT * FROM sellers WHERE user_id=%s", (user_id,))
        seller = cur.fetchone()

        if request.method == "POST" and not seller:
            shop_name = request.form.get("shop_name", "").strip()
            tax_code = request.form.get("tax_code", "").strip()
            cccd = request.form.get("cccd", "").strip()
            address = request.form.get("address", "").strip()
            if not (shop_name and tax_code and cccd and address):
                flash("Vui lòng điền đầy đủ hồ sơ shop.", "danger")
            elif not re.fullmatch(r"\d{9}|\d{12}", cccd):
                flash("Số CCCD gồm 9 hoặc 12 chữ số.", "danger")
            else:
                cur.execute("INSERT INTO sellers (user_id, shop_name, tax_code, cccd, address) VALUES (%s,%s,%s,%s,%s)",
                            (user_id, shop_name, tax_code, cccd, address))
                flash("Đã nộp hồ sơ đăng ký Shop! Vui lòng chờ Admin duyệt.", "success")
            return redirect(url_for("seller_channel"))

        ctx = dict(products=[], variants=[], orders=[], categories=[], brands=[], promotions=[], stats={}, low_stock=[],
                   wallet_balance=Decimal("0"), commission_total=Decimal("0"), withdrawals=[], wallet_transactions=[])
        if seller and seller["approved_at"]:
            sid = seller["seller_id"]
            cur.execute("SELECT categories_id, categories_name FROM categories ORDER BY categories_name")
            ctx["categories"] = cur.fetchall()
            cur.execute("SELECT brand_id, brand_name FROM brands ORDER BY brand_name")
            ctx["brands"] = cur.fetchall()

            cur.execute("""
                SELECT p.product_id, p.product_name, p.description, p.approval_status, p.image_url,
                       p.category_id, p.brand_id, c.categories_name,
                       (SELECT COUNT(*) FROM product_vari v WHERE v.product_id=p.product_id) AS variant_count
                FROM products p JOIN categories c ON c.categories_id = p.category_id
                WHERE p.seller_id=%s ORDER BY p.product_id DESC""", (sid,))
            ctx["products"] = cur.fetchall()

            cur.execute("""
                SELECT v.vari_id, v.product_id, v.sku, v.size, v.color, v.price, v.quantity, p.product_name
                FROM product_vari v JOIN products p ON p.product_id = v.product_id
                WHERE p.seller_id=%s ORDER BY p.product_id DESC, v.vari_id""", (sid,))
            ctx["variants"] = cur.fetchall()
            ctx["low_stock"] = [v for v in ctx["variants"] if v["quantity"] <= LOW_STOCK]

            status = request.args.get("status", "")
            sql = """
                SELECT o.order_id, o.order_date, o.status, o.final_amount, u.full_name AS customer,
                       GROUP_CONCAT(CONCAT(p.product_name, ' x', od.quantity) SEPARATOR ', ') AS items
                FROM orders o
                JOIN order_details od ON od.order_id = o.order_id
                JOIN product_vari v ON v.vari_id = od.vari_id
                JOIN products p ON p.product_id = v.product_id
                JOIN users u ON u.user_id = o.user_id
                WHERE p.seller_id=%s"""
            params = [sid]
            if status:
                sql += " AND o.status=%s"
                params.append(status)
            cur.execute(sql + " GROUP BY o.order_id, o.order_date, o.status, o.final_amount, u.full_name ORDER BY o.order_id DESC",
                        params)
            ctx["orders"] = cur.fetchall()
            ctx["order_status"] = status

            cur.execute("""
                SELECT pr.*, GROUP_CONCAT(p.product_name SEPARATOR ', ') AS products
                FROM promotion pr
                LEFT JOIN promotion_product pp ON pp.promotion_id = pr.promotion_id
                LEFT JOIN products p ON p.product_id = pp.product_id
                WHERE pr.seller_id=%s GROUP BY pr.promotion_id ORDER BY pr.promotion_id DESC""", (sid,))
            ctx["promotions"] = cur.fetchall()

            cur.execute("""
                SELECT COALESCE(SUM(CASE WHEN o.status='Da giao' THEN od.subtotal END),0) AS revenue,
                       COUNT(DISTINCT o.order_id) AS orders,
                       COUNT(DISTINCT CASE WHEN o.status='Cho xac nhan' THEN o.order_id END) AS pending,
                       COALESCE(SUM(CASE WHEN o.status='Da giao' THEN od.quantity END),0) AS sold
                FROM orders o JOIN order_details od ON od.order_id=o.order_id
                JOIN product_vari v ON v.vari_id=od.vari_id JOIN products p ON p.product_id=v.product_id
                WHERE p.seller_id=%s""", (sid,))
            ctx["stats"] = cur.fetchone()
            ctx["stats"]["products"] = len(ctx["products"])

            cur.execute("""
                SELECT p.product_name, SUM(od.quantity) AS qty, SUM(od.subtotal) AS revenue
                FROM order_details od JOIN orders o ON o.order_id=od.order_id AND o.status='Da giao'
                JOIN product_vari v ON v.vari_id=od.vari_id JOIN products p ON p.product_id=v.product_id
                WHERE p.seller_id=%s GROUP BY p.product_id, p.product_name ORDER BY revenue DESC LIMIT 5""", (sid,))
            ctx["top_products"] = cur.fetchall()
            cur.execute("SELECT balance FROM seller_wallet WHERE seller_id=%s", (sid,))
            wallet = cur.fetchone()
            ctx["wallet_balance"] = wallet["balance"] if wallet else Decimal("0")
            cur.execute("SELECT COALESCE(SUM(commission_amount),0) AS total FROM commission_records WHERE seller_id=%s", (sid,))
            ctx["commission_total"] = cur.fetchone()["total"]
            cur.execute("SELECT * FROM withdrawal_requests WHERE seller_id=%s ORDER BY withdrawal_id DESC LIMIT 10", (sid,))
            ctx["withdrawals"] = cur.fetchall()
            cur.execute("SELECT * FROM wallet_transactions WHERE seller_id=%s ORDER BY transaction_id DESC LIMIT 30", (sid,))
            ctx["wallet_transactions"] = cur.fetchall()
            cur.execute("""SELECT rr.*,u.full_name,o.order_id FROM return_requests rr
                           JOIN users u ON u.user_id=rr.user_id JOIN orders o ON o.order_id=rr.order_id
                           WHERE rr.seller_id=%s ORDER BY rr.return_id DESC LIMIT 50""", (sid,))
            ctx["return_requests"] = cur.fetchall()

    return render_template("seller.html", seller=seller, flow=STATUS_FLOW, **ctx)


def seller_owns_product(cur, product_id):
    cur.execute("SELECT * FROM products WHERE product_id=%s AND seller_id=%s", (product_id, g.seller["seller_id"]))
    return cur.fetchone()


def seller_owns_variant(cur, vari_id):
    cur.execute("""SELECT v.* FROM product_vari v JOIN products p ON p.product_id=v.product_id
                   WHERE v.vari_id=%s AND p.seller_id=%s""", (vari_id, g.seller["seller_id"]))
    return cur.fetchone()


def seller_redirect(tab=None):
    return redirect(url_for("seller_channel") + (f"#{tab}" if tab else ""))


@app.route("/seller/product/add", methods=["POST"])
@seller_required
def seller_add_product():
    try:
        filename = validate_and_save_image(request.files.get("image"))
        name = request.form.get("product_name", "").strip()
        if not name:
            raise ValueError("Vui lòng nhập tên sản phẩm.")
        sku = request.form.get("sku", "").strip()
        if not sku:
            raise ValueError("Vui lòng nhập mã SKU.")
        price = to_dec(request.form.get("price"), "Giá bán")
        qty = to_int(request.form.get("quantity"))
        if qty is None or qty < 0:
            raise ValueError("Số lượng tồn kho không hợp lệ.")
        cat_id = to_int(request.form.get("category_id"))
        brand_id = to_int(request.form.get("brand_id"))
        with db() as (conn, cur):
            cur.execute("""INSERT INTO products (category_id, seller_id, brand_id, product_name, description, image_url, approval_status)
                           VALUES (%s,%s,%s,%s,%s,%s,'Cho duyet')""",
                        (cat_id, g.seller["seller_id"], brand_id, name, request.form.get("description", "").strip(), filename))
            cur.execute("INSERT INTO product_vari (product_id, sku, size, color, price, quantity) VALUES (%s,%s,%s,%s,%s,%s)",
                        (cur.lastrowid, sku, request.form.get("size", "").strip() or None,
                         request.form.get("color", "").strip() or None, price, qty))
        flash("Đã gửi sản phẩm lên hệ thống! Đang chờ Admin duyệt.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    except dberr.IntegrityError:
        remove_upload(locals().get("filename"))
        flash("Mã SKU đã tồn tại hoặc danh mục/thương hiệu không hợp lệ.", "danger")
    return seller_redirect()


@app.route("/seller/product/<int:product_id>/edit", methods=["POST"])
@seller_required
def seller_edit_product(product_id):
    new_file = None
    try:
        with db() as (conn, cur):
            prod = seller_owns_product(cur, product_id)
            if not prod:
                flash("Không tìm thấy sản phẩm của shop bạn.", "warning")
                return seller_redirect()
            name = request.form.get("product_name", "").strip()
            if not name:
                raise ValueError("Tên sản phẩm không được để trống.")
            new_file = validate_and_save_image(request.files.get("image"))
            cur.execute("""UPDATE products SET product_name=%s, description=%s, category_id=%s, brand_id=%s,
                                  image_url=%s, approval_status='Cho duyet', approved_by=NULL, approved_at=NULL
                           WHERE product_id=%s""",
                        (name, request.form.get("description", "").strip(), to_int(request.form.get("category_id")),
                         to_int(request.form.get("brand_id")), new_file or prod["image_url"], product_id))
        if new_file:
            remove_upload(prod["image_url"])
        flash("Đã lưu thay đổi. Sản phẩm sẽ được Admin duyệt lại trước khi hiển thị.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    except dberr.IntegrityError:
        remove_upload(new_file)
        flash("Danh mục hoặc thương hiệu không hợp lệ.", "danger")
    return seller_redirect()


@app.route("/seller/product/<int:product_id>/delete", methods=["POST"])
@seller_required
def seller_delete_product(product_id):
    try:
        with db() as (conn, cur):
            prod = seller_owns_product(cur, product_id)
            if not prod:
                flash("Không tìm thấy sản phẩm của shop bạn.", "warning")
                return seller_redirect()
            cur.execute("DELETE FROM products WHERE product_id=%s", (product_id,))
        remove_upload(prod["image_url"])
        flash("Đã xóa sản phẩm.", "success")
    except dberr.IntegrityError:
        flash("Sản phẩm đã có đơn hàng nên không thể xóa. Hãy đặt tồn kho về 0 để ngừng bán.", "warning")
    return seller_redirect()


@app.route("/seller/variant/add", methods=["POST"])
@seller_required
def seller_add_variant():
    try:
        pid = to_int(request.form.get("product_id"))
        sku = request.form.get("sku", "").strip()
        if not sku:
            raise ValueError("Vui lòng nhập mã SKU.")
        price = to_dec(request.form.get("price"), "Giá bán")
        qty = to_int(request.form.get("quantity"))
        if qty is None or qty < 0:
            raise ValueError("Số lượng tồn kho không hợp lệ.")
        with db() as (conn, cur):
            if not seller_owns_product(cur, pid):
                flash("Sản phẩm không thuộc shop của bạn.", "warning")
                return seller_redirect()
            cur.execute("INSERT INTO product_vari (product_id, sku, size, color, price, quantity) VALUES (%s,%s,%s,%s,%s,%s)",
                        (pid, sku, request.form.get("size", "").strip() or None,
                         request.form.get("color", "").strip() or None, price, qty))
        flash(f"Đã thêm biến thể mới (SKU: {sku})!", "success")
    except ValueError as err:
        flash(str(err), "danger")
    except dberr.IntegrityError:
        flash("Mã SKU đã tồn tại.", "danger")
    return seller_redirect()


@app.route("/seller/variant/<int:vari_id>/edit", methods=["POST"])
@seller_required
def seller_edit_variant(vari_id):
    try:
        price = to_dec(request.form.get("price"), "Giá bán")
        qty = to_int(request.form.get("quantity"))
        if qty is None or qty < 0:
            raise ValueError("Số lượng tồn kho không hợp lệ.")
        with db() as (conn, cur):
            if not seller_owns_variant(cur, vari_id):
                flash("Biến thể không thuộc shop của bạn.", "warning")
                return seller_redirect()
            cur.execute("UPDATE product_vari SET size=%s, color=%s, price=%s, quantity=%s WHERE vari_id=%s",
                        (request.form.get("size", "").strip() or None, request.form.get("color", "").strip() or None,
                         price, qty, vari_id))
        flash("Đã cập nhật biến thể.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return seller_redirect()


@app.route("/seller/variant/<int:vari_id>/delete", methods=["POST"])
@seller_required
def seller_delete_variant(vari_id):
    try:
        with db() as (conn, cur):
            if not seller_owns_variant(cur, vari_id):
                flash("Biến thể không thuộc shop của bạn.", "warning")
                return seller_redirect()
            cur.execute("DELETE FROM product_vari WHERE vari_id=%s", (vari_id,))
        flash("Đã xóa biến thể.", "success")
    except dberr.IntegrityError:
        flash("Biến thể đã có đơn hàng hoặc phiếu nhập nên không thể xóa. Hãy đặt tồn kho về 0.", "warning")
    return seller_redirect()


@app.route("/seller/purchase_order", methods=["POST"])
@seller_required
def seller_purchase_order():
    try:
        vari_id = to_int(request.form.get("vari_id"))
        qty = to_int(request.form.get("quantity"))
        if not qty or qty < 1:
            raise ValueError("Số lượng nhập phải lớn hơn 0.")
        unit_cost = to_dec(request.form.get("unit_cost"), "Giá vốn")
        with db() as (conn, cur):
            if not seller_owns_variant(cur, vari_id):
                flash("Biến thể không thuộc shop của bạn.", "warning")
                return seller_redirect()
            cur.execute("SELECT warehouses_id FROM warehouses WHERE seller_id=%s LIMIT 1", (g.seller["seller_id"],))
            wh = cur.fetchone()
            if wh:
                wh_id = wh["warehouses_id"]
            else:
                cur.execute("INSERT INTO warehouses (seller_id, warehouse_name, address) VALUES (%s,%s,%s)",
                            (g.seller["seller_id"], f"Kho {g.seller['shop_name']}", g.seller["address"] or "Địa chỉ shop"))
                wh_id = cur.lastrowid
            cur.execute("INSERT INTO purchase_orders (seller_id, warehouse_id, total_amount, status) VALUES (%s,%s,%s,'Da nhap kho')",
                        (g.seller["seller_id"], wh_id, qty * unit_cost))
            po_id = cur.lastrowid
            cur.execute("INSERT INTO purchase_order_details (po_id, vari_id, quantity, unit_cost) VALUES (%s,%s,%s,%s)",
                        (po_id, vari_id, qty, unit_cost))
            cur.execute("UPDATE product_vari SET quantity = quantity + %s WHERE vari_id=%s", (qty, vari_id))
        flash(f"Đã nhập kho +{qty} sản phẩm (phiếu PO#{po_id}).", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return seller_redirect()


@app.route("/seller/promotion/add", methods=["POST"])
@seller_required
def seller_add_promotion():
    try:
        code = re.sub(r"\s+", "", request.form.get("code", "").upper())
        if not re.fullmatch(r"[A-Z0-9_-]{3,40}", code):
            raise ValueError("Mã voucher gồm 3-40 ký tự chữ/số, không dấu.")
        pct = to_dec(request.form.get("discount_percent") or 0, "Phần trăm giảm", maxv=Decimal("100"))
        amt = to_dec(request.form.get("discount_amount") or 0, "Số tiền giảm")
        if pct == 0 and amt == 0:
            raise ValueError("Hãy nhập phần trăm giảm hoặc số tiền giảm.")
        if pct > 0 and amt > 0:
            raise ValueError("Chỉ chọn một: giảm theo % hoặc giảm theo số tiền.")
        days = min(max(to_int(request.form.get("days"), 30), 1), 365)
        pid = to_int(request.form.get("product_id"))
        with db() as (conn, cur):
            if not seller_owns_product(cur, pid):
                flash("Sản phẩm không thuộc shop của bạn.", "warning")
                return seller_redirect()
            cur.execute("""INSERT INTO promotion (seller_id, code, discount_percent, discount_amount, start_date, end_date, is_active)
                           VALUES (%s,%s,%s,%s,NOW(),DATE_ADD(NOW(), INTERVAL %s DAY),TRUE)""",
                        (g.seller["seller_id"], code, pct, amt, days))
            cur.execute("INSERT INTO promotion_product (promotion_id, product_id) VALUES (%s,%s)", (cur.lastrowid, pid))
        flash(f"Đã tạo voucher [{code}] có hiệu lực {days} ngày.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    except dberr.IntegrityError:
        flash("Mã voucher này đã tồn tại, hãy chọn mã khác.", "danger")
    return seller_redirect()


@app.route("/seller/promotion/<int:promo_id>/toggle", methods=["POST"])
@seller_required
def seller_toggle_promotion(promo_id):
    with db() as (conn, cur):
        cur.execute("UPDATE promotion SET is_active = NOT is_active WHERE promotion_id=%s AND seller_id=%s",
                    (promo_id, g.seller["seller_id"]))
        flash("Đã đổi trạng thái voucher." if cur.rowcount else "Không tìm thấy voucher.", "info")
    return seller_redirect()


@app.route("/seller/order/update/<int:order_id>", methods=["POST"])
@seller_required
def seller_update_order(order_id):
    new_status = request.form.get("status", "")
    with db() as (conn, cur):
        cur.execute("""SELECT o.status, o.user_id FROM orders o
                       WHERE o.order_id=%s AND EXISTS (
                           SELECT 1 FROM order_details od JOIN product_vari v ON v.vari_id=od.vari_id
                           JOIN products p ON p.product_id=v.product_id
                           WHERE od.order_id=o.order_id AND p.seller_id=%s) FOR UPDATE""",
                    (order_id, g.seller["seller_id"]))
        o = cur.fetchone()
        if not o:
            flash("Không tìm thấy đơn hàng của shop bạn.", "warning")
            return seller_redirect()
        if new_status not in STATUS_FLOW.get(o["status"], set()):
            flash("Không thể chuyển đơn từ trạng thái hiện tại sang trạng thái này.", "warning")
            return seller_redirect()
        if new_status == "Da huy":
            cur.execute("SELECT payment_status FROM payment WHERE order_id=%s FOR UPDATE", (order_id,))
            paid_check = cur.fetchone()
            if paid_check and paid_check["payment_status"] == "Da thanh toan":
                flash("Đơn đã thanh toán. Không thể hủy trực tiếp; cần xử lý hoàn tiền qua Admin.", "warning")
                return seller_redirect()

        if new_status == "Da huy":
            restore_stock(cur, order_id)
            cur.execute("UPDATE payment SET payment_status='Da huy' WHERE order_id=%s", (order_id,))
            cur.execute("UPDATE shipment SET shipping_status='Da huy' WHERE order_id=%s", (order_id,))
        elif new_status == "Dang giao":
            cur.execute("UPDATE shipment SET shipping_status='Dang giao', shipped_date=NOW() WHERE order_id=%s", (order_id,))
        elif new_status == "Da giao":
            cur.execute("SELECT payment_method,payment_status FROM payment WHERE order_id=%s FOR UPDATE", (order_id,))
            pay = cur.fetchone()
            if pay and pay["payment_method"] in ("VNPAY", "MOMO", "DEMO", "BANK_QR") and pay["payment_status"] != "Da thanh toan":
                flash("Đơn thanh toán trực tuyến chưa được xác nhận thanh toán.", "warning")
                return seller_redirect()
            cur.execute("UPDATE shipment SET shipping_status='Da giao', delivered_date=NOW() WHERE order_id=%s", (order_id,))
            if pay and pay["payment_method"] == "COD":
                cur.execute("UPDATE payment SET payment_status='Da thanh toan', paid_at=NOW() WHERE order_id=%s", (order_id,))
            settle_order_commissions(cur, order_id)

        cur.execute("UPDATE orders SET status=%s WHERE order_id=%s", (new_status, order_id))
        status_text = {"Da xac nhan": "đã được shop xác nhận", "Dang giao": "đang được vận chuyển",
                       "Da giao": "đã giao thành công", "Da huy": "đã bị hủy"}.get(new_status, new_status)
        notify_user(cur, o["user_id"], "Cập nhật đơn hàng", f"Đơn hàng #{order_id} {status_text}.", f"/orders/{order_id}")
        if new_status == "Da giao":
            refresh_rank(cur, o["user_id"])
    flash(f"Đã cập nhật đơn #{order_id} ➔ '{new_status}'", "success")
    return seller_redirect()


@app.route("/returns/request", methods=["POST"])
@customer_required
def request_return():
    uid = session["user"]["id"]
    detail_id = to_int(request.form.get("order_detail_id"))
    qty = to_int(request.form.get("quantity"), 0)
    reason = request.form.get("reason", "").strip()
    if not detail_id or qty < 1 or len(reason) < 10:
        flash("Vui lòng chọn số lượng hợp lệ và mô tả lý do ít nhất 10 ký tự.", "warning")
        return back("my_orders")
    with db() as (conn, cur):
        cur.execute("""SELECT od.order_id,od.quantity,o.status,p.seller_id
                       FROM order_details od JOIN orders o ON o.order_id=od.order_id
                       JOIN product_vari v ON v.vari_id=od.vari_id JOIN products p ON p.product_id=v.product_id
                       WHERE od.order_detail_id=%s AND o.user_id=%s FOR UPDATE""", (detail_id, uid))
        row = cur.fetchone()
        if not row or row["status"] != "Da giao" or qty > row["quantity"]:
            flash("Chỉ có thể yêu cầu đổi trả sản phẩm thuộc đơn đã giao và số lượng không vượt quá số đã mua.", "warning")
            return back("my_orders")
        cur.execute("SELECT COUNT(*) AS n FROM return_requests WHERE order_detail_id=%s AND status IN ('Cho xu ly','Chap nhan')", (detail_id,))
        if cur.fetchone()["n"]:
            flash("Sản phẩm này đã có yêu cầu đổi trả đang xử lý.", "warning")
            return back("my_orders")
        cur.execute("""INSERT INTO return_requests (order_id,order_detail_id,user_id,seller_id,quantity,reason)
                       VALUES (%s,%s,%s,%s,%s,%s)""", (row["order_id"], detail_id, uid, row["seller_id"], qty, reason[:500]))
        cur.execute("SELECT user_id FROM sellers WHERE seller_id=%s", (row["seller_id"],))
        seller_user = cur.fetchone()
        if seller_user:
            notify_user(cur, seller_user["user_id"], "Có yêu cầu đổi trả mới", f"Khách hàng gửi yêu cầu đổi trả cho đơn #{row['order_id']}.", "/seller#returns")
    flash("Đã gửi yêu cầu đổi trả. Shop sẽ phản hồi sau khi kiểm tra.", "success")
    return back("my_orders")


@app.route("/seller/return/<int:return_id>/<action>", methods=["POST"])
@seller_required
def seller_process_return(return_id, action):
    if action not in ("accept", "reject"):
        return seller_redirect("returns")
    with db() as (conn, cur):
        cur.execute("SELECT * FROM return_requests WHERE return_id=%s AND seller_id=%s FOR UPDATE",
                    (return_id, g.seller["seller_id"]))
        req = cur.fetchone()
        if not req or req["status"] != "Cho xu ly":
            flash("Yêu cầu không tồn tại hoặc đã được xử lý.", "warning")
            return seller_redirect("returns")
        status = "Chap nhan" if action == "accept" else "Tu choi"
        note = request.form.get("seller_note", "").strip()[:500]
        cur.execute("UPDATE return_requests SET status=%s,seller_note=%s,processed_at=NOW() WHERE return_id=%s",
                    (status, note, return_id))
        notify_user(cur, req["user_id"], "Cập nhật yêu cầu đổi trả", f"Yêu cầu đổi trả #{return_id}: {status}. {note}", f"/orders/{req['order_id']}")
    flash("Đã cập nhật yêu cầu đổi trả. Nếu chấp nhận, cần thực hiện kiểm tra hàng và hoàn tiền theo quy trình.", "success")
    return seller_redirect("returns")


@app.route("/seller/withdraw", methods=["POST"])
@seller_required
def seller_withdraw():
    amount = to_dec(request.form.get("amount"), "Số tiền rút", minv=Decimal("1"))
    bank = request.form.get("bank_name", "").strip()[:120]
    account = request.form.get("account_number", "").strip()[:50]
    owner = request.form.get("account_name", "").strip()[:120]
    if not (bank and account and owner):
        flash("Vui lòng nhập đầy đủ thông tin ngân hàng.", "danger")
        return seller_redirect()
    with db() as (conn, cur):
        sid = g.seller["seller_id"]
        cur.execute("SELECT balance FROM seller_wallet WHERE seller_id=%s FOR UPDATE", (sid,))
        wallet = cur.fetchone()
        if not wallet or amount > wallet["balance"]:
            flash("Số dư ví không đủ để rút số tiền này.", "warning")
            return seller_redirect()
        cur.execute("UPDATE seller_wallet SET balance=balance-%s WHERE seller_id=%s", (amount, sid))
        cur.execute("""INSERT INTO withdrawal_requests
            (seller_id, amount, bank_name, account_number, account_name)
            VALUES (%s,%s,%s,%s,%s)""", (sid, amount, bank, account, owner))
        wid = cur.lastrowid
        cur.execute("""INSERT INTO wallet_transactions
            (seller_id, withdrawal_id, transaction_type, amount, note)
            VALUES (%s,%s,'Giu tien rut',%s,%s)""", (sid, wid, -amount, "Giữ tiền trong khi chờ Admin duyệt rút"))
    flash("Đã gửi yêu cầu rút tiền. Số dư đã được tạm giữ trong thời gian chờ duyệt.", "success")
    return seller_redirect()


@app.route("/admin/return/<int:return_id>/refund", methods=["POST"])
@admin_required
def admin_refund_return(return_id):
    reference = request.form.get("refund_reference", "").strip()[:150]
    if not reference:
        flash("Nhập mã giao dịch hoàn tiền sau khi đã chuyển tiền/hoàn qua cổng thanh toán.", "warning")
        return redirect(url_for("admin_panel"))
    with db() as (conn, cur):
        cur.execute("""SELECT rr.*,od.quantity AS ordered_qty,od.commission_amount AS line_commission,
                              od.seller_amount AS line_seller,od.subtotal,o.status AS order_status,
                              p.payment_method,p.payment_status
                       FROM return_requests rr
                       JOIN order_details od ON od.order_detail_id=rr.order_detail_id
                       JOIN orders o ON o.order_id=rr.order_id
                       JOIN payment p ON p.order_id=o.order_id
                       WHERE rr.return_id=%s FOR UPDATE""", (return_id,))
        rr = cur.fetchone()
        if not rr or rr["status"] != "Chap nhan":
            flash("Yêu cầu chưa được shop chấp nhận hoặc đã được xử lý.", "warning")
            return redirect(url_for("admin_panel"))
        if rr["quantity"] > rr["ordered_qty"]:
            flash("Số lượng yêu cầu đổi trả không hợp lệ.", "danger")
            return redirect(url_for("admin_panel"))
        total_refund = money((rr["line_commission"] + rr["line_seller"]) * rr["quantity"] / rr["ordered_qty"])
        seller_refund = money(rr["line_seller"] * rr["quantity"] / rr["ordered_qty"])
        commission_refund = total_refund - seller_refund
        cur.execute("SELECT commission_id FROM commission_records WHERE order_detail_id=%s", (rr["order_detail_id"],))
        commission_exists = cur.fetchone()
        if commission_exists:
            cur.execute("INSERT INTO commission_refunds (return_id,seller_id,commission_amount,seller_amount,total_refund) VALUES (%s,%s,%s,%s,%s)",
                        (return_id, rr["seller_id"], commission_refund, seller_refund, total_refund))
            cur.execute("UPDATE seller_wallet SET balance=balance-%s WHERE seller_id=%s", (seller_refund, rr["seller_id"]))
            cur.execute("INSERT INTO wallet_transactions (seller_id,transaction_type,amount,note) VALUES (%s,'Hoan tien doi tra',%s,%s)",
                        (rr["seller_id"], -seller_refund, f"Điều chỉnh hoàn tiền yêu cầu #{return_id}"))
        cur.execute("UPDATE return_requests SET status='Da hoan tien',refund_amount=%s,refund_reference=%s,refunded_at=NOW(),processed_at=NOW() WHERE return_id=%s",
                    (total_refund, reference, return_id))
        notify_user(cur, rr["user_id"], "Đã xử lý hoàn tiền", f"Yêu cầu đổi trả #{return_id} đã hoàn {total_refund:,.0f}đ. Mã tham chiếu: {reference}.", f"/orders/{rr['order_id']}")
    flash(f"Đã ghi nhận hoàn tiền {total_refund:,.0f}đ. Hãy bảo đảm giao dịch bên ngoài đã thực sự hoàn tất.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/commission/update", methods=["POST"])
@admin_required
def admin_update_commission():
    try:
        rate = to_dec(request.form.get("commission_rate"), "Tỷ lệ hoa hồng", maxv=Decimal("100"))
        with db() as (conn, cur):
            cur.execute("UPDATE platform_settings SET commission_rate=%s WHERE setting_id=1", (rate,))
        flash(f"Đã cập nhật hoa hồng sàn thành {rate}%. Áp dụng cho đơn đặt hàng mới.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/category/commission/<int:category_id>", methods=["POST"])
@admin_required
def admin_category_commission(category_id):
    raw = request.form.get("commission_rate", "").strip()
    try:
        rate = None if raw == "" else to_dec(raw, "Tỷ lệ hoa hồng", maxv=Decimal("100"))
        with db() as (conn, cur):
            cur.execute("SELECT categories_id FROM categories WHERE categories_id=%s", (category_id,))
            exists = cur.fetchone()
            if not exists:
                flash("Không tìm thấy danh mục.", "warning")
            else:
                cur.execute("UPDATE categories SET commission_rate=%s WHERE categories_id=%s", (rate, category_id))
                flash("Đã cập nhật tỷ lệ hoa hồng danh mục. Đơn mới sẽ áp dụng mức này nếu shop/sản phẩm không ghi đè.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/seller/commission/<int:seller_id>", methods=["POST"])
@admin_required
def admin_seller_commission(seller_id):
    raw = request.form.get("commission_rate", "").strip()
    try:
        rate = None if raw == "" else to_dec(raw, "Tỷ lệ hoa hồng", maxv=Decimal("100"))
        with db() as (conn, cur):
            cur.execute("SELECT seller_id FROM sellers WHERE seller_id=%s", (seller_id,))
            if not cur.fetchone():
                flash("Không tìm thấy shop.", "warning")
            else:
                cur.execute("UPDATE sellers SET commission_rate=%s WHERE seller_id=%s", (rate, seller_id))
                flash("Đã cập nhật hoa hồng riêng cho shop. Áp dụng cho đơn mới nếu sản phẩm không ghi đè.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/product/commission/<int:product_id>", methods=["POST"])
@admin_required
def admin_product_commission(product_id):
    raw = request.form.get("commission_rate", "").strip()
    try:
        rate = None if raw == "" else to_dec(raw, "Tỷ lệ hoa hồng", maxv=Decimal("100"))
        with db() as (conn, cur):
            cur.execute("SELECT product_id FROM products WHERE product_id=%s", (product_id,))
            exists = cur.fetchone()
            if not exists:
                flash("Không tìm thấy sản phẩm.", "warning")
            else:
                cur.execute("UPDATE products SET commission_rate=%s WHERE product_id=%s", (rate, product_id))
                flash("Đã cập nhật hoa hồng riêng cho sản phẩm.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/withdrawal/<int:withdrawal_id>/<action>", methods=["POST"])
@admin_required
def admin_process_withdrawal(withdrawal_id, action):
    if action not in ("approve", "reject"):
        return redirect(url_for("admin_panel"))
    with db() as (conn, cur):
        cur.execute("SELECT * FROM withdrawal_requests WHERE withdrawal_id=%s FOR UPDATE", (withdrawal_id,))
        req = cur.fetchone()
        if not req or req["status"] != "Cho duyet":
            flash("Yêu cầu rút tiền không tồn tại hoặc đã được xử lý.", "warning")
            return redirect(url_for("admin_panel"))
        if action == "reject":
            cur.execute("UPDATE seller_wallet SET balance=balance+%s WHERE seller_id=%s",
                        (req["amount"], req["seller_id"]))
            cur.execute("""INSERT INTO wallet_transactions
                (seller_id, withdrawal_id, transaction_type, amount, note)
                VALUES (%s,%s,'Hoan tien rut',%s,'Hoàn tiền do yêu cầu rút bị từ chối')""",
                (req["seller_id"], withdrawal_id, req["amount"]))
            status = "Tu choi"
        else:
            status = "Da duyet"
        cur.execute("UPDATE withdrawal_requests SET status=%s, processed_at=NOW() WHERE withdrawal_id=%s",
                    (status, withdrawal_id))
    flash("Đã duyệt yêu cầu rút tiền. Hãy thực hiện chuyển khoản ngoài hệ thống." if action == "approve"
          else "Đã từ chối và hoàn số dư vào ví shop.", "success")
    return redirect(url_for("admin_panel"))


# ----------------------------------------------------------------------------
# QUẢN TRỊ VIÊN
# ----------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_panel():
    status = request.args.get("status", "")
    with db() as (conn, cur):
        cur.execute("""SELECT s.seller_id, u.full_name, s.shop_name, s.tax_code, s.cccd, s.registered_at
                       FROM sellers s JOIN users u ON u.user_id = s.user_id WHERE s.approved_at IS NULL""")
        pending_sellers = cur.fetchall()
        cur.execute("""SELECT p.product_id, p.product_name, p.image_url, s.shop_name
                       FROM products p JOIN sellers s ON s.seller_id = p.seller_id
                       WHERE p.approval_status = 'Cho duyet'""")
        pending_products = cur.fetchall()
        cur.execute("""SELECT c.*, (SELECT COUNT(*) FROM products p WHERE p.category_id=c.categories_id) AS n
                       FROM categories c ORDER BY c.categories_id DESC""")
        categories = cur.fetchall()
        cur.execute("""SELECT b.*, (SELECT COUNT(*) FROM products p WHERE p.brand_id=b.brand_id) AS n
                       FROM brands b ORDER BY b.brand_id DESC""")
        brands = cur.fetchall()
        cur.execute("""SELECT u.user_id, u.username, u.full_name, u.email, u.status, r.rank_name
                       FROM users u LEFT JOIN user_rank r ON r.rank_id=u.rank_id ORDER BY u.user_id DESC""")
        users = cur.fetchall()

        sql = """
            SELECT o.order_id, o.order_date, o.final_amount, o.status, o.shipping_address,
                   u.full_name AS customer_name, u.phone AS customer_phone,
                   GROUP_CONCAT(DISTINCT s.shop_name SEPARATOR ', ') AS shop_name,
                   GROUP_CONCAT(CONCAT(p.product_name, ' x', od.quantity) SEPARATOR ', ') AS items
            FROM orders o
            JOIN users u ON u.user_id = o.user_id
            JOIN order_details od ON od.order_id = o.order_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN products p ON p.product_id = v.product_id
            JOIN sellers s ON s.seller_id = p.seller_id"""
        params = []
        if status:
            sql += " WHERE o.status=%s"
            params.append(status)
        cur.execute(sql + """ GROUP BY o.order_id, o.order_date, o.final_amount, o.status, o.shipping_address,
                              u.full_name, u.phone ORDER BY o.order_id DESC LIMIT 200""", params)
        all_orders = cur.fetchall()

        cur.execute("""SELECT (SELECT COUNT(*) FROM users) AS users,
                              (SELECT COUNT(*) FROM sellers WHERE approved_at IS NOT NULL) AS shops,
                              (SELECT COUNT(*) FROM products WHERE approval_status='Da duyet') AS products,
                              (SELECT COUNT(*) FROM orders) AS orders,
                              (SELECT COALESCE(SUM(final_amount),0) FROM orders WHERE status='Da giao') AS revenue""")
        stats = cur.fetchone()
        cur.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
        by_status = {r["status"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT commission_rate FROM platform_settings WHERE setting_id=1")
        setting = cur.fetchone() or {"commission_rate": Decimal("5.00")}
        cur.execute("""SELECT wr.*, s.shop_name FROM withdrawal_requests wr
                       JOIN sellers s ON s.seller_id=wr.seller_id
                       WHERE wr.status='Cho duyet' ORDER BY wr.requested_at""")
        pending_withdrawals = cur.fetchall()
        cur.execute("""SELECT GREATEST(0,COALESCE((SELECT SUM(commission_amount) FROM commission_records),0)
                       -COALESCE((SELECT SUM(commission_amount) FROM commission_refunds),0)) AS total""")
        commission_stats = cur.fetchone()
        cur.execute("""SELECT p.product_id,p.product_name,p.commission_rate,s.shop_name
                       FROM products p JOIN sellers s ON s.seller_id=p.seller_id
                       WHERE p.approval_status='Da duyet' ORDER BY p.product_id DESC LIMIT 100""")
        commission_products = cur.fetchall()
        cur.execute("SELECT s.seller_id,s.shop_name,s.commission_rate FROM sellers s ORDER BY s.seller_id DESC LIMIT 100")
        commission_sellers = cur.fetchall()
        cur.execute("""SELECT rr.return_id,rr.order_id,rr.quantity,rr.reason,rr.seller_note,
                              u.full_name,s.shop_name,od.quantity AS ordered_qty,
                              od.commission_amount+od.seller_amount AS line_net
                       FROM return_requests rr JOIN users u ON u.user_id=rr.user_id
                       JOIN sellers s ON s.seller_id=rr.seller_id
                       JOIN order_details od ON od.order_detail_id=rr.order_detail_id
                       WHERE rr.status='Chap nhan' ORDER BY rr.return_id DESC""")
        accepted_returns = cur.fetchall()

    return render_template("admin.html", sellers=pending_sellers, products=pending_products,
                           categories=categories, brands=brands, users=users, all_orders=all_orders,
                           stats=stats, by_status=by_status, order_status=status,
                           commission_rate=setting["commission_rate"], pending_withdrawals=pending_withdrawals,
                           commission_stats=commission_stats, commission_products=commission_products, commission_sellers=commission_sellers,
                           accepted_returns=accepted_returns)


@app.route("/admin/approve_seller/<int:seller_id>", methods=["POST"])
@admin_required
def approve_seller(seller_id):
    with db() as (conn, cur):
        cur.execute("UPDATE sellers SET approved_by=%s, approved_at=NOW() WHERE seller_id=%s AND approved_at IS NULL",
                    (session["user"]["id"], seller_id))
        cur.execute("SELECT user_id,shop_name FROM sellers WHERE seller_id=%s", (seller_id,))
        seller_row = cur.fetchone()
        if seller_row:
            notify_user(cur, seller_row["user_id"], "Shop đã được duyệt", f"Shop {seller_row['shop_name']} đã được Admin phê duyệt.", "/seller")
    flash(f"Đã duyệt người bán #{seller_id}", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/reject_seller/<int:seller_id>", methods=["POST"])
@admin_required
def reject_seller(seller_id):
    with db() as (conn, cur):
        cur.execute("DELETE FROM sellers WHERE seller_id=%s AND approved_at IS NULL", (seller_id,))
    flash(f"Đã từ chối hồ sơ shop #{seller_id}", "info")
    return redirect(url_for("admin_panel"))


@app.route("/admin/approve_product/<int:product_id>", methods=["POST"])
@admin_required
def approve_product(product_id):
    with db() as (conn, cur):
        cur.execute("UPDATE products SET approval_status='Da duyet', approved_by=%s, approved_at=NOW() WHERE product_id=%s",
                    (session["user"]["id"], product_id))
    flash(f"Đã duyệt sản phẩm #{product_id}", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/reject_product/<int:product_id>", methods=["POST"])
@admin_required
def reject_product(product_id):
    with db() as (conn, cur):
        cur.execute("UPDATE products SET approval_status='Tu choi', approved_by=%s, approved_at=NOW() WHERE product_id=%s",
                    (session["user"]["id"], product_id))
    flash(f"Đã từ chối sản phẩm #{product_id}", "info")
    return redirect(url_for("admin_panel"))


@app.route("/admin/category/add", methods=["POST"])
@admin_required
def admin_add_category():
    name = request.form.get("categories_name", "").strip()
    if not name:
        flash("Tên danh mục không được trống.", "danger")
        return redirect(url_for("admin_panel"))
    try:
        with db() as (conn, cur):
            cur.execute("INSERT INTO categories (categories_name, slug) VALUES (%s,%s)", (name, slugify(name)))
        flash(f"Đã thêm danh mục: {name}", "success")
    except dberr.IntegrityError:
        flash("Danh mục này đã tồn tại.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/category/delete/<int:cat_id>", methods=["POST"])
@admin_required
def admin_delete_category(cat_id):
    try:
        with db() as (conn, cur):
            cur.execute("DELETE FROM categories WHERE categories_id=%s", (cat_id,))
        flash("Đã xóa danh mục.", "success")
    except dberr.IntegrityError:
        flash("Danh mục đang có sản phẩm nên không thể xóa.", "warning")
    return redirect(url_for("admin_panel"))


@app.route("/admin/brand/add", methods=["POST"])
@admin_required
def admin_add_brand():
    name = request.form.get("brand_name", "").strip()
    if not name:
        flash("Tên thương hiệu không được trống.", "danger")
        return redirect(url_for("admin_panel"))
    try:
        with db() as (conn, cur):
            cur.execute("INSERT INTO brands (brand_name, company_name) VALUES (%s,%s)",
                        (name, request.form.get("company_name", "").strip()))
        flash(f"Đã thêm thương hiệu: {name}", "success")
    except dberr.IntegrityError:
        flash("Thương hiệu này đã tồn tại.", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/brand/delete/<int:brand_id>", methods=["POST"])
@admin_required
def admin_delete_brand(brand_id):
    with db() as (conn, cur):
        cur.execute("DELETE FROM brands WHERE brand_id=%s", (brand_id,))  # sản phẩm giữ lại, brand_id -> NULL
    flash("Đã xóa thương hiệu.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/toggle/<int:user_id>", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    with db() as (conn, cur):
        cur.execute("SELECT status FROM users WHERE user_id=%s", (user_id,))
        u = cur.fetchone()
        if u:
            new_st = "Khoa" if u["status"] == "Hoat dong" else "Hoat dong"
            cur.execute("UPDATE users SET status=%s WHERE user_id=%s", (new_st, user_id))
            flash(f"Đã chuyển trạng thái tài khoản #{user_id} ➔ {new_st}", "success")
    return redirect(url_for("admin_panel"))


# ----------------------------------------------------------------------------
# ĐĂNG NHẬP / ĐĂNG KÝ
# ----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        with db() as (conn, cur):
            cur.execute("SELECT admin_id, full_name, password_hash, status FROM admin_user WHERE user_admin=%s", (username,))
            admin = cur.fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                if admin["status"] != "Hoat dong":
                    flash("Tài khoản quản trị đã bị khóa!", "danger")
                    return redirect(url_for("login"))
                session.clear()
                session["user"] = {"id": admin["admin_id"], "name": admin["full_name"], "role": "admin"}
                flash("Đăng nhập Admin thành công!", "success")
                return redirect(url_for("admin_panel"))

            cur.execute("SELECT user_id, full_name, pass_hash, status, avatar_url FROM users WHERE username=%s", (username,))
            user = cur.fetchone()
            if user and check_password_hash(user["pass_hash"], password):
                if user["status"] != "Hoat dong":
                    flash("Tài khoản của bạn đang bị tạm khóa!", "danger")
                    return redirect(url_for("login"))
                session.clear()
                session["user"] = {"id": user["user_id"], "name": user["full_name"], "role": "user",
                                   "avatar": user["avatar_url"]}
                flash(f"Đăng nhập thành công! Chào {user['full_name']}", "success")
                return redirect(nxt or url_for("index"))
        flash("Sai tên đăng nhập hoặc mật khẩu!", "danger")
    return render_template("login.html", next=nxt)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("username", "password", "full_name", "phone", "email", "address")}
        error = None
        if not USERNAME_RE.match(f["username"]):
            error = "Tên đăng nhập gồm 3-30 ký tự: chữ, số, dấu chấm hoặc gạch dưới."
        elif len(f["password"]) < 6:
            error = "Mật khẩu tối thiểu 6 ký tự."
        elif len(f["full_name"]) < 2:
            error = "Vui lòng nhập họ tên."
        elif not PHONE_RE.match(f["phone"]):
            error = "Số điện thoại gồm 10 chữ số, bắt đầu bằng 0."
        elif not EMAIL_RE.match(f["email"]):
            error = "Email không hợp lệ."
        elif not f["address"]:
            error = "Vui lòng nhập địa chỉ."
        if error:
            flash(error, "danger")
            return render_template("register.html", form=f)
        try:
            with db() as (conn, cur):
                cur.execute("SELECT 1 FROM admin_user WHERE user_admin=%s", (f["username"],))
                if cur.fetchone():
                    raise dberr.IntegrityError("duplicate")
                cur.execute("""INSERT INTO users (username, pass_hash, full_name, phone, email, address, rank_id)
                               VALUES (%s,%s,%s,%s,%s,%s,1)""",
                            (f["username"], generate_password_hash(f["password"]), f["full_name"], f["phone"],
                             f["email"], f["address"]))
            flash("Đăng ký thành công! Hãy đăng nhập ngay.", "success")
            return redirect(url_for("login"))
        except dberr.IntegrityError:
            flash("Đăng ký thất bại: Username hoặc Email đã tồn tại!", "danger")
            return render_template("register.html", form=f)
    return render_template("register.html", form={})


@app.route("/logout")
def logout():
    session.clear()
    flash("Đã đăng xuất.", "info")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_system()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")),
            debug=os.getenv("FLASK_DEBUG", "0") == "1")
