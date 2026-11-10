# -*- coding: utf-8 -*-
import os
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
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

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost").replace("mysql://", "").replace("https://", "").replace("http://", "").replace("://", "").replace("http", ""),
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

# ----------------------------------------------------------------------------
# ĐẶT HÀNG & ĐÁNH GIÁ (KHÁCH HÀNG)
# ----------------------------------------------------------------------------
@app.route("/order/place", methods=["POST"])
def place_order():
    if "user" not in session:
        flash("Vui lòng đăng nhập để đặt hàng!")
        return redirect(url_for("login"))
    if session["user"].get("role") == "admin":
        flash("Tài khoản Admin không thể thực hiện đặt hàng!")
        return redirect(url_for("admin_panel"))

    vari_id = int(request.form.get("vari_id"))
    qty = int(request.form.get("quantity", 1))
    ship_id = int(request.form.get("shipping_method_id"))
    pay_method = request.form.get("payment_method")
    address = request.form.get("address")
    promo_code = request.form.get("promo_code", "").strip().upper()
    user_id = session["user"]["id"]

    try:
        with db() as (conn, cur):
            cur.execute("""
                SELECT v.vari_id, v.price, v.quantity, p.product_id, p.product_name, s.user_id AS seller_user
                FROM product_vari v
                JOIN products p ON p.product_id = v.product_id
                JOIN sellers s ON s.seller_id = p.seller_id
                WHERE v.vari_id = %s FOR UPDATE
            """, (vari_id,))
            v = cur.fetchone()

            if not v or qty > v["quantity"]:
                flash("Số lượng tồn kho không đủ!")
                return redirect(request.referrer)
            if v["seller_user"] == user_id:
                flash("Bạn không thể mua sản phẩm của chính shop mình!")
                return redirect(request.referrer)

            subtotal = v["price"] * qty

            # 1. Giảm giá từ Voucher
            promo_discount = Decimal("0")
            if promo_code:
                cur.execute("""
                    SELECT pr.promotion_id, pr.discount_percent, pr.discount_amount 
                    FROM promotion pr
                    JOIN promotion_product pp ON pp.promotion_id = pr.promotion_id
                    WHERE pr.code = %s AND pp.product_id = %s AND pr.is_active = TRUE AND NOW() BETWEEN pr.start_date AND pr.end_date
                """, (promo_code, v["product_id"]))
                pr = cur.fetchone()
                if pr:
                    if pr["discount_percent"] > 0:
                        promo_discount = subtotal * (pr["discount_percent"] / Decimal("100"))
                    else:
                        promo_discount = pr["discount_amount"]
                    promo_discount = min(promo_discount, subtotal)
                else:
                    flash("Mã giảm giá không hợp lệ hoặc không áp dụng cho sản phẩm này!")

            # 2. Giảm giá từ Hạng thành viên
            cur.execute("""
                SELECT COALESCE(r.discount_rate, 0) AS rate 
                FROM users u LEFT JOIN user_rank r ON r.rank_id = u.rank_id 
                WHERE u.user_id = %s
            """, (user_id,))
            rank_rate = cur.fetchone()["rate"]
            rank_discount = (subtotal - promo_discount) * (rank_rate / Decimal("100"))

            total_discount = promo_discount + rank_discount

            # 3. Phí vận chuyển & Tổng tiền
            cur.execute("SELECT base_cost FROM shipping_method WHERE shipping_method_id = %s", (ship_id,))
            ship_cost = cur.fetchone()["base_cost"]
            final_amount = max(Decimal("0"), subtotal - total_discount) + ship_cost

            # 4. Lưu đơn hàng
            cur.execute("""
                INSERT INTO orders (user_id, total_amount, discount_amount, final_amount, status, shipping_address)
                VALUES (%s, %s, %s, %s, 'Cho xac nhan', %s)
            """, (user_id, subtotal, total_discount, final_amount, address))
            order_id = cur.lastrowid

            cur.execute("""
                INSERT INTO order_details (order_id, vari_id, quantity, unit_price, subtotal)
                VALUES (%s, %s, %s, %s, %s)
            """, (order_id, vari_id, qty, v["price"], subtotal))

            cur.execute("UPDATE product_vari SET quantity = quantity - %s WHERE vari_id = %s", (qty, vari_id))
            cur.execute("INSERT INTO payment (order_id, payment_method, amount, payment_status) VALUES (%s, %s, %s, 'Cho thanh toan')", (order_id, pay_method, final_amount))
            cur.execute("INSERT INTO shipment (shipping_method_id, order_id, tracking_code, shipping_status) VALUES (%s, %s, %s, 'Cho lay hang')", (ship_id, order_id, f"TRK{order_id:08d}"))

        flash(f"Đặt hàng thành công! Mã đơn: #{order_id} (Đã giảm: {total_discount:,.0f}đ)")
        return redirect(url_for("my_orders"))
    except Exception as e:
        flash(f"Lỗi đặt hàng: {e}")
        return redirect(request.referrer)


@app.route("/orders")
def my_orders():
    if "user" not in session: return redirect(url_for("login"))
    if session["user"].get("role") == "admin":
        return redirect(url_for("admin_panel"))

    with db() as (conn, cur):
        cur.execute("""
            SELECT o.order_id, o.order_date, o.final_amount, o.status, o.shipping_address,
                   od.order_detail_id, p.product_name, pr.review_id
            FROM orders o
            JOIN order_details od ON od.order_id = o.order_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN products p ON p.product_id = v.product_id
            LEFT JOIN product_review pr ON pr.order_detail_id = od.order_detail_id
            WHERE o.user_id = %s ORDER BY o.order_id DESC
        """, (session["user"]["id"],))
        orders = cur.fetchall()
    return render_template("orders.html", orders=orders)


@app.route("/orders/cancel/<int:order_id>", methods=["POST"])
def cancel_order(order_id):
    if "user" not in session: return redirect(url_for("login"))
    user_id = session["user"]["id"]

    with db() as (conn, cur):
        cur.execute("SELECT status FROM orders WHERE order_id = %s AND user_id = %s FOR UPDATE", (order_id, user_id))
        o = cur.fetchone()
        if not o or o["status"] != "Cho xac nhan":
            flash("Đơn hàng không thể hủy!")
            return redirect(url_for("my_orders"))

        cur.execute("SELECT vari_id, quantity FROM order_details WHERE order_id = %s", (order_id,))
        for item in cur.fetchall():
            cur.execute("UPDATE product_vari SET quantity = quantity + %s WHERE vari_id = %s", (item["quantity"], item["vari_id"]))

        cur.execute("UPDATE orders SET status = 'Da huy' WHERE order_id = %s", (order_id,))
        cur.execute("UPDATE payment SET payment_status = 'Da huy' WHERE order_id = %s", (order_id,))
        cur.execute("UPDATE shipment SET shipping_status = 'Da huy' WHERE order_id = %s", (order_id,))

    flash(f"Đã hủy đơn hàng #{order_id} và hoàn tồn kho!")
    return redirect(url_for("my_orders"))


@app.route("/review/add", methods=["POST"])
def add_review():
    if "user" not in session: return redirect(url_for("login"))
    od_id = int(request.form["order_detail_id"])
    rating = int(request.form["rating"])
    comment = request.form.get("comment", "").strip()

    try:
        with db() as (conn, cur):
            cur.execute("""
                INSERT INTO product_review (order_detail_id, user_id, rating, comment)
                VALUES (%s, %s, %s, %s)
            """, (od_id, session["user"]["id"], rating, comment))
        flash("Cảm ơn bạn đã gửi đánh giá sản phẩm!")
    except Exception:
        flash("Bạn đã đánh giá sản phẩm này rồi!")
    return redirect(url_for("my_orders"))

# ----------------------------------------------------------------------------
# PHÂN HỆ KÊNH NGƯỜI BÁN (SELLER)
# ----------------------------------------------------------------------------
@app.route("/seller", methods=["GET", "POST"])
def seller_channel():
    if "user" not in session: return redirect(url_for("login"))
    if session["user"].get("role") == "admin":
        return redirect(url_for("admin_panel"))

    user_id = session["user"]["id"]

    with db() as (conn, cur):
        cur.execute("SELECT * FROM sellers WHERE user_id = %s", (user_id,))
        seller = cur.fetchone()

        if request.method == "POST" and not seller:
            shop_name = request.form["shop_name"]
            tax_code = request.form["tax_code"]
            cccd = request.form["cccd"]
            address = request.form["address"]
            cur.execute("INSERT INTO sellers (user_id, shop_name, tax_code, cccd, address) VALUES (%s, %s, %s, %s, %s)",
                        (user_id, shop_name, tax_code, cccd, address))
            flash("Đã nộp hồ sơ đăng ký Shop!")
            return redirect(url_for("seller_channel"))

        products, variants, orders, categories, brands, promotions = [], [], [], [], [], []
        if seller and seller["approved_at"]:
            cur.execute("SELECT categories_id, categories_name FROM categories")
            categories = cur.fetchall()
            cur.execute("SELECT brand_id, brand_name FROM brands")
            brands = cur.fetchall()

            cur.execute("""
                SELECT p.product_id, p.product_name, p.approval_status, p.image_url, c.categories_name
                FROM products p JOIN categories c ON c.categories_id = p.category_id
                WHERE p.seller_id = %s ORDER BY p.product_id DESC
            """, (seller["seller_id"],))
            products = cur.fetchall()

            cur.execute("""
                SELECT v.vari_id, v.sku, v.size, v.color, v.price, v.quantity, p.product_name
                FROM product_vari v JOIN products p ON p.product_id = v.product_id
                WHERE p.seller_id = %s ORDER BY v.vari_id DESC
            """, (seller["seller_id"],))
            variants = cur.fetchall()

            cur.execute("""
                SELECT DISTINCT o.order_id, o.order_date, o.status, o.final_amount, u.full_name AS customer
                FROM orders o
                JOIN order_details od ON od.order_id = o.order_id
                JOIN product_vari v ON v.vari_id = od.vari_id
                JOIN products p ON p.product_id = v.product_id
                JOIN users u ON u.user_id = o.user_id
                WHERE p.seller_id = %s ORDER BY o.order_id DESC
            """, (seller["seller_id"],))
            orders = cur.fetchall()

            cur.execute("SELECT * FROM promotion WHERE seller_id = %s ORDER BY promotion_id DESC", (seller["seller_id"],))
            promotions = cur.fetchall()

    return render_template("seller.html", seller=seller, products=products, variants=variants, orders=orders, categories=categories, brands=brands, promotions=promotions)


@app.route("/seller/product/add", methods=["POST"])
def seller_add_product():
    if "user" not in session: return redirect(url_for("login"))
    try:
        filename = validate_and_save_image(request.files.get('image'))
    except ValueError as err:
        flash(f"Lỗi ảnh: {err}")
        return redirect(url_for("seller_channel"))

    with db() as (conn, cur):
        cur.execute("SELECT seller_id FROM sellers WHERE user_id = %s AND approved_at IS NOT NULL", (session["user"]["id"],))
        s = cur.fetchone()
        if not s: return redirect(url_for("seller_channel"))

        cat_id = request.form["category_id"]
        brand_id = request.form.get("brand_id") or None
        name = request.form["product_name"]
        desc = request.form.get("description")
        sku = request.form["sku"]
        size = request.form.get("size")
        color = request.form.get("color")
        price = Decimal(request.form["price"])
        qty = int(request.form["quantity"])

        cur.execute("""
            INSERT INTO products (category_id, seller_id, brand_id, product_name, description, image_url, approval_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'Cho duyet')
        """, (cat_id, s["seller_id"], brand_id, name, desc, filename))
        pid = cur.lastrowid

        cur.execute("""
            INSERT INTO product_vari (product_id, sku, size, color, price, quantity)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (pid, sku, size, color, price, qty))

    flash("Đã gửi sản phẩm và ảnh lên hệ thống! Đang chờ Admin duyệt.")
    return redirect(url_for("seller_channel"))


@app.route("/seller/variant/add", methods=["POST"])
def seller_add_variant():
    if "user" not in session: return redirect(url_for("login"))
    pid = int(request.form["product_id"])
    sku = request.form["sku"]
    size = request.form.get("size")
    color = request.form.get("color")
    price = Decimal(request.form["price"])
    qty = int(request.form["quantity"])

    with db() as (conn, cur):
        cur.execute("""
            INSERT INTO product_vari (product_id, sku, size, color, price, quantity)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (pid, sku, size, color, price, qty))
    flash(f"Đã thêm biến thể mới (SKU: {sku}) thành công!")
    return redirect(url_for("seller_channel"))


@app.route("/seller/purchase_order", methods=["POST"])
def seller_purchase_order():
    if "user" not in session: return redirect(url_for("login"))
    with db() as (conn, cur):
        cur.execute("SELECT seller_id FROM sellers WHERE user_id = %s", (session["user"]["id"],))
        s = cur.fetchone()
        if not s: return redirect(url_for("seller_channel"))

        cur.execute("SELECT warehouses_id FROM warehouses WHERE seller_id = %s", (s["seller_id"],))
        wh = cur.fetchone()
        if not wh:
            cur.execute("INSERT INTO warehouses (seller_id, warehouse_name, address) VALUES (%s, %s, %s)",
                        (s["seller_id"], f"Kho {session['user']['name']}", "Địa chỉ Shop"))
            wh_id = cur.lastrowid
        else:
            wh_id = wh["warehouses_id"]

        vari_id = int(request.form["vari_id"])
        qty = int(request.form["quantity"])
        unit_cost = Decimal(request.form["unit_cost"])
        total_amount = qty * unit_cost

        cur.execute("INSERT INTO purchase_orders (seller_id, warehouse_id, total_amount, status) VALUES (%s, %s, %s, 'Da nhap kho')", (s["seller_id"], wh_id, total_amount))
        po_id = cur.lastrowid

        cur.execute("INSERT INTO purchase_order_details (po_id, vari_id, quantity, unit_cost) VALUES (%s, %s, %s, %s)", (po_id, vari_id, qty, unit_cost))
        cur.execute("UPDATE product_vari SET quantity = quantity + %s WHERE vari_id = %s", (qty, vari_id))

    flash(f"Đã nhập kho +{qty} sản phẩm (Đơn PO#{po_id})!")
    return redirect(url_for("seller_channel"))


@app.route("/seller/promotion/add", methods=["POST"])
def seller_add_promotion():
    if "user" not in session: return redirect(url_for("login"))
    with db() as (conn, cur):
        cur.execute("SELECT seller_id FROM sellers WHERE user_id = %s", (session["user"]["id"],))
        s = cur.fetchone()
        if not s: return redirect(url_for("seller_channel"))

        code = request.form["code"].strip().upper()
        pct = Decimal(request.form.get("discount_percent", 0))
        amt = Decimal(request.form.get("discount_amount", 0))
        product_id = int(request.form["product_id"])

        cur.execute("""
            INSERT INTO promotion (seller_id, code, discount_percent, discount_amount, start_date, end_date, is_active)
            VALUES (%s, %s, %s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 30 DAY), TRUE)
        """, (s["seller_id"], code, pct, amt))
        promo_id = cur.lastrowid

        cur.execute("INSERT INTO promotion_product (promotion_id, product_id) VALUES (%s, %s)", (promo_id, product_id))
    flash(f"Đã tạo Mã Voucher [{code}] thành công!")
    return redirect(url_for("seller_channel"))


@app.route("/seller/order/update/<int:order_id>", methods=["POST"])
def seller_update_order(order_id):
    if "user" not in session: return redirect(url_for("login"))
    new_status = request.form["status"]

    with db() as (conn, cur):
        if new_status == "Da huy":
            cur.execute("SELECT vari_id, quantity FROM order_details WHERE order_id = %s", (order_id,))
            for item in cur.fetchall():
                cur.execute("UPDATE product_vari SET quantity = quantity + %s WHERE vari_id = %s", (item["quantity"], item["vari_id"]))
            cur.execute("UPDATE payment SET payment_status = 'Da huy' WHERE order_id = %s", (order_id,))
            cur.execute("UPDATE shipment SET shipping_status = 'Da huy' WHERE order_id = %s", (order_id,))
        elif new_status == "Dang giao":
            cur.execute("UPDATE shipment SET shipping_status = 'Dang giao', shipped_date = NOW() WHERE order_id = %s", (order_id,))
        elif new_status == "Da giao":
            cur.execute("UPDATE shipment SET shipping_status = 'Da giao', delivered_date = NOW() WHERE order_id = %s", (order_id,))
            cur.execute("UPDATE payment SET payment_status = 'Da thanh toan', paid_at = NOW() WHERE order_id = %s", (order_id,))

        cur.execute("UPDATE orders SET status = %s WHERE order_id = %s", (new_status, order_id))

    flash(f"Đã cập nhật đơn #{order_id} ➔ '{new_status}'")
    return redirect(url_for("seller_channel"))

# ----------------------------------------------------------------------------
# PHÂN HỆ QUẢN TRỊ VIÊN (ADMIN PANEL)
# ----------------------------------------------------------------------------
@app.route("/admin")
def admin_panel():
    if "user" not in session or session["user"].get("role") != "admin":
        flash("Bạn không có quyền truy cập trang Admin!")
        return redirect(url_for("index"))

    with db() as (conn, cur):
        cur.execute("SELECT s.seller_id, u.full_name, s.shop_name, s.registered_at FROM sellers s JOIN users u ON u.user_id = s.user_id WHERE s.approved_at IS NULL")
        pending_sellers = cur.fetchall()

        # Lấy thêm cột image_url để Admin xem trước ảnh sản phẩm cần duyệt
        cur.execute("SELECT p.product_id, p.product_name, p.image_url, s.shop_name FROM products p JOIN sellers s ON s.seller_id = p.seller_id WHERE p.approval_status = 'Cho duyet'")
        pending_products = cur.fetchall()

        cur.execute("SELECT * FROM categories ORDER BY categories_id DESC")
        categories = cur.fetchall()
        cur.execute("SELECT * FROM brands ORDER BY brand_id DESC")
        brands = cur.fetchall()

        cur.execute("SELECT user_id, username, full_name, email, status FROM users ORDER BY user_id DESC")
        users = cur.fetchall()

        # TOÀN BỘ ĐƠN HÀNG HỆ THỐNG DÀNH CHO ADMIN
        cur.execute("""
            SELECT o.order_id, o.order_date, o.final_amount, o.status, o.shipping_address,
                   u.full_name AS customer_name, u.phone AS customer_phone,
                   p.product_name, od.quantity, s.shop_name
            FROM orders o
            JOIN users u ON u.user_id = o.user_id
            JOIN order_details od ON od.order_id = o.order_id
            JOIN product_vari v ON v.vari_id = od.vari_id
            JOIN products p ON p.product_id = v.product_id
            JOIN sellers s ON s.seller_id = p.seller_id
            ORDER BY o.order_id DESC
        """)
        all_orders = cur.fetchall()

    return render_template("admin.html", 
                           sellers=pending_sellers, 
                           products=pending_products, 
                           categories=categories, 
                           brands=brands, 
                           users=users,
                           all_orders=all_orders)


@app.route("/admin/approve_seller/<int:seller_id>")
def approve_seller(seller_id):
    if session.get("user", {}).get("role") == "admin":
        with db() as (conn, cur):
            cur.execute("UPDATE sellers SET approved_by=%s, approved_at=NOW() WHERE seller_id=%s", (session["user"]["id"], seller_id))
        flash(f"Đã duyệt người bán #{seller_id}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/approve_product/<int:product_id>")
def approve_product(product_id):
    if session.get("user", {}).get("role") == "admin":
        with db() as (conn, cur):
            cur.execute("UPDATE products SET approval_status='Da duyet', approved_by=%s, approved_at=NOW() WHERE product_id=%s", (session["user"]["id"], product_id))
        flash(f"Đã duyệt sản phẩm #{product_id}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/reject_product/<int:product_id>")
def reject_product(product_id):
    """Admin từ chối sản phẩm không hợp lệ / ảnh vi phạm"""
    if session.get("user", {}).get("role") == "admin":
        with db() as (conn, cur):
            cur.execute("UPDATE products SET approval_status='Tu choi', approved_by=%s, approved_at=NOW() WHERE product_id=%s", (session["user"]["id"], product_id))
        flash(f"Đã từ chối duyệt sản phẩm #{product_id}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/category/add", methods=["POST"])
def admin_add_category():
    if session.get("user", {}).get("role") == "admin":
        name = request.form["categories_name"].strip()
        slug = name.lower().replace(" ", "-")
        with db() as (conn, cur):
            cur.execute("INSERT INTO categories (categories_name, slug) VALUES (%s, %s)", (name, slug))
        flash(f"Đã thêm Danh mục: {name}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/brand/add", methods=["POST"])
def admin_add_brand():
    if session.get("user", {}).get("role") == "admin":
        name = request.form["brand_name"].strip()
        company = request.form.get("company_name", "").strip()
        with db() as (conn, cur):
            cur.execute("INSERT INTO brands (brand_name, company_name) VALUES (%s, %s)", (name, company))
        flash(f"Đã thêm Thương hiệu: {name}")
    return redirect(url_for("admin_panel"))


@app.route("/admin/user/toggle/<int:user_id>")
def admin_toggle_user(user_id):
    if session.get("user", {}).get("role") == "admin":
        with db() as (conn, cur):
            cur.execute("SELECT status FROM users WHERE user_id = %s", (user_id,))
            u = cur.fetchone()
            if u:
                new_st = "Khoa" if u["status"] == "Hoat dong" else "Hoat dong"
                cur.execute("UPDATE users SET status = %s WHERE user_id = %s", (new_st, user_id))
                flash(f"Đã chuyển trạng thái tài khoản #{user_id} ➔ {new_st}")
    return redirect(url_for("admin_panel"))

# ----------------------------------------------------------------------------
# ĐĂNG NHẬP / ĐĂNG KÝ
# ----------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        with db() as (conn, cur):
            cur.execute("SELECT admin_id, full_name, password_hash, status FROM admin_user WHERE user_admin = %s", (username,))
            admin = cur.fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                if admin["status"] != "Hoat dong":
                    flash("Tài khoản quản trị đã bị khóa!")
                    return redirect(url_for("login"))
                session["user"] = {"id": admin["admin_id"], "name": admin["full_name"], "role": "admin"}
                flash("Đăng nhập Admin thành công!")
                return redirect(url_for("admin_panel"))

            cur.execute("SELECT user_id, full_name, pass_hash, status FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            if user and check_password_hash(user["pass_hash"], password):
                if user["status"] != "Hoat dong":
                    flash("Tài khoản của bạn đang bị Tạm khóa!")
                    return redirect(url_for("login"))
                session["user"] = {"id": user["user_id"], "name": user["full_name"], "role": "user"}
                flash(f"Đăng nhập thành công! Chào {user['full_name']}")
                return redirect(url_for("index"))

        flash("Sai tên đăng nhập hoặc mật khẩu!")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        full_name = request.form["full_name"].strip()
        phone = request.form["phone"].strip()
        email = request.form["email"].strip()
        address = request.form["address"].strip()

        try:
            with db() as (conn, cur):
                cur.execute("""
                    INSERT INTO users (username, pass_hash, full_name, phone, email, address, rank_id)
                    VALUES (%s, %s, %s, %s, %s, %s, 1)
                """, (username, generate_password_hash(password), full_name, phone, email, address))
            flash("Đăng ký thành công! Hãy đăng nhập ngay.")
            return redirect(url_for("login"))
        except Exception:
            flash("Đăng ký thất bại: Username hoặc Email đã tồn tại!")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Đã đăng xuất.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_system()
    app.run(host="0.0.0.0", port=5000, debug=True)
