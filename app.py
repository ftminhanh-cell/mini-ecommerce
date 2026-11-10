# -*- coding: utf-8 -*-
import os
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "ecommerce_full_features_secret_key"

# ----------------------------------------------------------------------------
# CẤU HÌNH UPLOAD & KIỂM DUYỆT ẢNH
# ----------------------------------------------------------------------------
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # Tối đa 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Lọc và xử lý sạch sẽ chuỗi MYSQL_HOST từ biến môi trường
raw_host = os.getenv("MYSQL_HOST", "localhost").strip()
if "://" not in raw_host:
    cleaned_host = urlparse(f"mysql://{raw_host}").hostname or raw_host
else:
    cleaned_host = urlparse(raw_host).hostname or raw_host

DB_CONFIG = {
    "host": cleaned_host,
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "minhyeuanh2k7?"),
    "database": os.getenv("MYSQL_DATABASE", "ecommerce_db"),
    "charset": "utf8mb4",
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

def validate_and_save_image(file_storage):
    """Kiểm duyệt định dạng, dung lượng và tính hợp lệ của tệp ảnh tải lên"""
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Định dạng tệp không được hỗ trợ (Chỉ chấp nhận PNG, JPG, JPEG, WEBP, GIF).")

    # Kiểm tra dung lượng tệp
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > MAX_FILE_SIZE:
        raise ValueError("Dung lượng ảnh quá lớn! Vui lòng tải ảnh dưới 5MB.")

    # Kiểm tra cấu trúc tệp ảnh (Image Integrity Check)
    try:
        from PIL import Image
        img = Image.open(file_storage)
        img.verify()
        file_storage.seek(0)
    except ImportError:
        # Nếu chưa cài Pillow, kiểm tra header cơ bản
        header = file_storage.read(10)
        file_storage.seek(0)
        valid_headers = [b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'RIFF']
        if not any(header.startswith(vh) for vh in valid_headers):
            raise ValueError("Tệp tải lên bị hỏng hoặc không phải là hình ảnh hợp lệ!")
    except Exception:
        raise ValueError("Tệp tải lên bị hỏng hoặc không phải là hình ảnh hợp lệ!")

    new_filename = f"{int(datetime.now().timestamp())}_{filename}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)
    file_storage.save(save_path)
    return new_filename

def init_system():
    """Tự động đồng bộ CSDL và Hash mật khẩu tài khoản Demo"""
    try:
        with db() as (conn, cur):
            try:
                cur.execute("ALTER TABLE products ADD COLUMN image_url VARCHAR(255) DEFAULT NULL;")
            except Exception:
                pass
            
            cur.execute("UPDATE admin_user SET password_hash=%s WHERE user_admin='admin'", 
                        (generate_password_hash("admin123"),))
            cur.execute("UPDATE users SET pass_hash=%s WHERE username IN ('khachhang01', 'nguoiban01', 'khachhang02')", 
                        (generate_password_hash("123456"),))
        print("=> [OK] Đã khởi tạo hệ thống và đồng bộ CSDL thành công!")
    except Exception as e:
        print(f"=> [Lỗi khởi tạo CSDL]: {e}")

# Tự động chạy khởi tạo hệ thống khi ứng dụng khởi chạy độc lập
try:
    init_system()
except Exception as e:
    print(f"=> [Cảnh báo lỗi khởi tạo tuần tự]: {e}")

# ----------------------------------------------------------------------------
# TRANG CHỦ & CHI TIẾT SẢN PHẨM
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    kw = request.args.get("kw", "").strip()
    cat_id = request.args.get("cat", type=int)
    brand_id = request.args.get("brand", type=int)

    sql = """
        SELECT p.product_id, p.product_name, p.image_url, c.categories_name, s.shop_name, b.brand_name,
               MIN(v.price) AS min_price, MAX(v.price) AS max_price, SUM(v.quantity) AS stock
        FROM products p
        JOIN categories c ON c.categories_id = p.category_id
        JOIN sellers s ON s.seller_id = p.seller_id
        LEFT JOIN brands b ON b.brand_id = p.brand_id
        JOIN product_vari v ON v.product_id = p.product_id
        WHERE p.approval_status = 'Da duyet'
    """
    params = []
    if kw:
        sql += " AND p.product_name LIKE %s"
        params.append(f"%{kw}%")
    if cat_id:
        sql += " AND p.category_id = %s"
        params.append(cat_id)
    if brand_id:
        sql += " AND p.brand_id = %s"
        params.append(brand_id)

    sql += " GROUP BY p.product_id, p.product_name, p.image_url, c.categories_name, s.shop_name, b.brand_name ORDER BY p.product_id DESC"

    with db() as (conn, cur):
        cur.execute(sql, params)
        products = cur.fetchall()

        cur.execute("SELECT categories_id, categories_name FROM categories")
        categories = cur.fetchall()

        cur.execute("SELECT brand_id, brand_name FROM brands")
        brands = cur.fetchall()

    return render_template("index.html", products=products, categories=categories, brands=brands, kw=kw, cat_id=cat_id, brand_id=brand_id)


@app.route("/product/<int:product_id>")
def product_detail(product_id):
    with db() as (conn, cur):
        cur.execute("""
            SELECT p.product_id, p.product_name, p.description, p.image_url, c.categories_name, s.shop_name, s.seller_id, b.brand_name
            FROM products p
            JOIN categories c ON c.categories_id = p.category_id
            JOIN sellers s ON s.seller_id = p.seller_id
            LEFT JOIN brands b ON b.brand_id = p.brand_id
            WHERE p.product_id = %s AND p.approval_status = 'Da duyet'
        """, (product_id,))
        product = cur.fetchone()

        if not product:
            flash("Sản phẩm không tồn tại hoặc chưa được duyệt!")
            return redirect(url_for("index"))

        cur.execute("SELECT vari_id, sku, size, color, price, quantity FROM product_vari WHERE product_id = %s", (product_id,))
        variants = cur.fetchall()

        cur.execute("""
            SELECT r.rating, r.comment, u.full_name, r.created_at
            FROM product_review r
            JOIN order_details od ON od.order_detail_id = r.order_detail_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN users u ON u.user_id = r.user_id
            WHERE v.product_id = %s ORDER BY r.created_at DESC
        """, (product_id,))
        reviews = cur.fetchall()

        cur.execute("""
            SELECT AVG(r.rating) AS avg_star, COUNT(*) AS count_star
            FROM product_review r
            JOIN order_details od ON od.order_detail_id = r.order_detail_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            WHERE v.product_id = %s
        """, (product_id,))
        star_stats = cur.fetchone()

        cur.execute("SELECT shipping_method_id, provider_name, base_cost FROM shipping_method WHERE is_active=TRUE")
        shipping_methods = cur.fetchall()

        cur.execute("""
            SELECT pr.code, pr.discount_percent, pr.discount_amount, pr.end_date
            FROM promotion pr JOIN promotion_product pp ON pp.promotion_id = pr.promotion_id
            WHERE pp.product_id = %s AND pr.is_active = TRUE AND NOW() BETWEEN pr.start_date AND pr.end_date
        """, (product_id,))
        promotions = cur.fetchall()

    return render_template("product_detail.html", product=product, variants=variants, reviews=reviews, star_stats=star_stats, shipping_methods=shipping_methods, promotions=promotions)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
