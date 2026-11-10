-- =========================================================
-- MINI E-COMMERCE - CSDL HOÀN CHỈNH & IN ĐẦY ĐỦ THÔNG TIN
-- MySQL 8.0.16+
-- =========================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP DATABASE IF EXISTS ecommerce_db;
CREATE DATABASE ecommerce_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ecommerce_db;

-- =========================================================
-- 1. TẠO BẢNG DỮ LIỆU
-- =========================================================

CREATE TABLE admin_roles (
    roles_id INT AUTO_INCREMENT PRIMARY KEY,
    roles_name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255)
) ENGINE=InnoDB;

CREATE TABLE admin_user (
    admin_id INT AUTO_INCREMENT PRIMARY KEY,
    user_admin VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    phone VARCHAR(20),
    email VARCHAR(150) UNIQUE,
    roles_id INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'Hoat dong',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_admin_role FOREIGN KEY (roles_id) REFERENCES admin_roles(roles_id)
) ENGINE=InnoDB;

CREATE TABLE admin_permission (
    permission_id INT AUTO_INCREMENT PRIMARY KEY,
    permission_name VARCHAR(100) NOT NULL UNIQUE,
    description VARCHAR(255)
) ENGINE=InnoDB;

CREATE TABLE admin_role_permission (
    roles_id INT NOT NULL,
    permission_id INT NOT NULL,
    PRIMARY KEY (roles_id, permission_id),
    CONSTRAINT fk_arp_role FOREIGN KEY (roles_id) REFERENCES admin_roles(roles_id) ON DELETE CASCADE,
    CONSTRAINT fk_arp_permission FOREIGN KEY (permission_id) REFERENCES admin_permission(permission_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE user_rank (
    rank_id INT AUTO_INCREMENT PRIMARY KEY,
    rank_name VARCHAR(50) NOT NULL UNIQUE,
    min_spent DECIMAL(15,2) NOT NULL DEFAULT 0,
    discount_rate DECIMAL(5,2) NOT NULL DEFAULT 0,
    CONSTRAINT chk_rank_min_spent CHECK (min_spent >= 0),
    CONSTRAINT chk_rank_discount CHECK (discount_rate BETWEEN 0 AND 100)
) ENGINE=InnoDB;

CREATE TABLE users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    pass_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    phone VARCHAR(20),
    email VARCHAR(150) NOT NULL UNIQUE,
    address VARCHAR(255),
    rank_id INT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'Hoat dong',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_users_rank FOREIGN KEY (rank_id) REFERENCES user_rank(rank_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE sellers (
    seller_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    shop_name VARCHAR(150) NOT NULL,
    approved_by INT NULL,
    tax_code VARCHAR(50),
    cccd VARCHAR(20),
    address VARCHAR(255),
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP NULL,
    CONSTRAINT fk_seller_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_seller_admin FOREIGN KEY (approved_by) REFERENCES admin_user(admin_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE warehouses (
    warehouses_id INT AUTO_INCREMENT PRIMARY KEY,
    seller_id INT NOT NULL,
    warehouse_name VARCHAR(150) NOT NULL,
    address VARCHAR(255) NOT NULL,
    CONSTRAINT fk_warehouse_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE categories (
    categories_id INT AUTO_INCREMENT PRIMARY KEY,
    parent_id INT NULL,
    categories_name VARCHAR(150) NOT NULL,
    slug VARCHAR(180) NOT NULL UNIQUE,
    CONSTRAINT fk_category_parent FOREIGN KEY (parent_id) REFERENCES categories(categories_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE brands (
    brand_id INT AUTO_INCREMENT PRIMARY KEY,
    brand_name VARCHAR(150) NOT NULL UNIQUE,
    company_name VARCHAR(150),
    contact_name VARCHAR(100)
) ENGINE=InnoDB;

CREATE TABLE products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    category_id INT NOT NULL,
    approved_by INT NULL,
    seller_id INT NOT NULL,
    brand_id INT NULL,
    product_name VARCHAR(255) NOT NULL,
    description TEXT,
    approval_status VARCHAR(30) NOT NULL DEFAULT 'Cho duyet',
    approved_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_product_category FOREIGN KEY (category_id) REFERENCES categories(categories_id),
    CONSTRAINT fk_product_admin FOREIGN KEY (approved_by) REFERENCES admin_user(admin_id) ON DELETE SET NULL,
    CONSTRAINT fk_product_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id),
    CONSTRAINT fk_product_brand FOREIGN KEY (brand_id) REFERENCES brands(brand_id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE product_vari (
    vari_id INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL,
    sku VARCHAR(100) NOT NULL UNIQUE,
    size VARCHAR(50),
    color VARCHAR(50),
    price DECIMAL(15,2) NOT NULL,
    quantity INT NOT NULL DEFAULT 0,
    CONSTRAINT chk_vari_price CHECK (price >= 0),
    CONSTRAINT chk_vari_quantity CHECK (quantity >= 0),
    CONSTRAINT fk_vari_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE promotion (
    promotion_id INT AUTO_INCREMENT PRIMARY KEY,
    seller_id INT NOT NULL,
    code VARCHAR(50) NOT NULL UNIQUE,
    discount_percent DECIMAL(5,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
    start_date DATETIME NOT NULL,
    end_date DATETIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_promotion_percent CHECK (discount_percent BETWEEN 0 AND 100),
    CONSTRAINT chk_promotion_amount CHECK (discount_amount >= 0),
    CONSTRAINT chk_promotion_dates CHECK (end_date >= start_date),
    CONSTRAINT fk_promotion_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE promotion_product (
    promotion_id INT NOT NULL,
    product_id INT NOT NULL,
    PRIMARY KEY (promotion_id, product_id),
    CONSTRAINT fk_pp_promotion FOREIGN KEY (promotion_id) REFERENCES promotion(promotion_id) ON DELETE CASCADE,
    CONSTRAINT fk_pp_product FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE purchase_orders (
    po_id INT AUTO_INCREMENT PRIMARY KEY,
    seller_id INT NOT NULL,
    warehouse_id INT NOT NULL,
    order_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    total_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'Da nhap kho',
    CONSTRAINT chk_po_total CHECK (total_amount >= 0),
    CONSTRAINT fk_po_seller FOREIGN KEY (seller_id) REFERENCES sellers(seller_id),
    CONSTRAINT fk_po_warehouse FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouses_id)
) ENGINE=InnoDB;

CREATE TABLE purchase_order_details (
    po_id INT NOT NULL,
    vari_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_cost DECIMAL(15,2) NOT NULL,
    PRIMARY KEY (po_id, vari_id),
    CONSTRAINT chk_pod_quantity CHECK (quantity > 0),
    CONSTRAINT chk_pod_cost CHECK (unit_cost >= 0),
    CONSTRAINT fk_pod_po FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
    CONSTRAINT fk_pod_vari FOREIGN KEY (vari_id) REFERENCES product_vari(vari_id)
) ENGINE=InnoDB;

CREATE TABLE orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
    final_amount DECIMAL(15,2) NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'Cho xac nhan',
    shipping_address VARCHAR(255) NOT NULL,
    CONSTRAINT chk_order_amounts CHECK (total_amount >= 0 AND discount_amount >= 0 AND final_amount >= 0),
    CONSTRAINT fk_order_user FOREIGN KEY (user_id) REFERENCES users(user_id)
) ENGINE=InnoDB;

CREATE TABLE order_details (
    order_detail_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    vari_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(15,2) NOT NULL,
    subtotal DECIMAL(15,2) NOT NULL,
    CONSTRAINT chk_od_quantity CHECK (quantity > 0),
    CONSTRAINT chk_od_price CHECK (unit_price >= 0),
    CONSTRAINT chk_od_subtotal CHECK (subtotal >= 0),
    CONSTRAINT fk_od_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    CONSTRAINT fk_od_vari FOREIGN KEY (vari_id) REFERENCES product_vari(vari_id)
) ENGINE=InnoDB;

CREATE TABLE payment (
    payment_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    payment_status VARCHAR(30) NOT NULL DEFAULT 'Cho thanh toan',
    transaction_code VARCHAR(150),
    paid_at TIMESTAMP NULL,
    CONSTRAINT chk_payment_amount CHECK (amount >= 0),
    CONSTRAINT fk_payment_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE shipping_method (
    shipping_method_id INT AUTO_INCREMENT PRIMARY KEY,
    provider_name VARCHAR(100) NOT NULL,
    base_cost DECIMAL(15,2) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT chk_ship_cost CHECK (base_cost >= 0)
) ENGINE=InnoDB;

CREATE TABLE shipment (
    shipment_id INT AUTO_INCREMENT PRIMARY KEY,
    shipping_method_id INT NOT NULL,
    order_id INT NOT NULL,
    tracking_code VARCHAR(100) UNIQUE,
    shipped_date TIMESTAMP NULL,
    delivered_date TIMESTAMP NULL,
    shipping_status VARCHAR(30) NOT NULL DEFAULT 'Cho lay hang',
    CONSTRAINT fk_shipment_method FOREIGN KEY (shipping_method_id) REFERENCES shipping_method(shipping_method_id),
    CONSTRAINT fk_shipment_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE product_review (
    review_id INT AUTO_INCREMENT PRIMARY KEY,
    order_detail_id INT NOT NULL,
    user_id INT NOT NULL,
    rating INT NOT NULL,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_review_rating CHECK (rating BETWEEN 1 AND 5),
    CONSTRAINT fk_review_order_detail FOREIGN KEY (order_detail_id) REFERENCES order_details(order_detail_id) ON DELETE CASCADE,
    CONSTRAINT fk_review_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT uq_review_order_detail UNIQUE (order_detail_id)
) ENGINE=InnoDB;

-- =========================================================
-- 2. INDEX TỐI ƯU HÓA TRUY VẤN
-- =========================================================

CREATE INDEX idx_products_approval_seller ON products(approval_status, seller_id);
CREATE INDEX idx_product_vari_pid ON product_vari(product_id);
CREATE INDEX idx_orders_user_status ON orders(user_id, status);
CREATE INDEX idx_order_details_order ON order_details(order_id);
CREATE INDEX idx_shipment_order ON shipment(order_id);
CREATE INDEX idx_payment_order ON payment(order_id);

-- =========================================================
-- 3. NẠP DỮ LIỆU MẪU
-- =========================================================

INSERT INTO admin_roles (roles_id, roles_name, description) VALUES
(1, 'Quan tri vien', 'Toan quyen quan ly he thong'),
(2, 'Nhan vien', 'Ho tro van hanh he thong');

INSERT INTO admin_user (admin_id, user_admin, password_hash, full_name, phone, email, roles_id, status) VALUES
(1, 'admin', 'CHANGE_ME_HASH', 'Quan tri he thong', '0900000001', 'admin@example.com', 1, 'Hoat dong');

INSERT INTO admin_permission (permission_id, permission_name, description) VALUES
(1, 'Quan ly tai khoan', 'Them, sua, khoa tai khoan'),
(2, 'Duyet nguoi ban', 'Duyet ho so nguoi ban'),
(3, 'Quan ly san pham', 'Duyet va quan ly san pham'),
(4, 'Quan ly don hang', 'Theo doi va cap nhat don hang'),
(5, 'Quan ly danh muc', 'Them va sua danh muc');

INSERT INTO admin_role_permission (roles_id, permission_id) VALUES
(1,1),(1,2),(1,3),(1,4),(1,5),(2,3),(2,4);

INSERT INTO user_rank (rank_id, rank_name, min_spent, discount_rate) VALUES
(1, 'Thanh vien', 0, 0),
(2, 'Bac', 1000000, 2),
(3, 'Vang', 5000000, 5);

INSERT INTO users (user_id, username, pass_hash, full_name, phone, email, address, rank_id, status) VALUES
(1, 'khachhang01', 'CHANGE_ME_HASH', 'Nguyen Van An', '0911111111', 'an@example.com', 'Cau Giay, Ha Noi', 1, 'Hoat dong'),
(2, 'nguoiban01', 'CHANGE_ME_HASH', 'Tran Thi Binh', '0922222222', 'binh@example.com', 'Dong Da, Ha Noi', 1, 'Hoat dong'),
(3, 'khachhang02', 'CHANGE_ME_HASH', 'Le Minh Chau', '0933333333', 'chau@example.com', 'Ha Dong, Ha Noi', 2, 'Hoat dong');

INSERT INTO sellers (seller_id, user_id, shop_name, approved_by, tax_code, cccd, address, approved_at) VALUES
(1, 2, 'Binh Store', 1, '0101234567', '001200000001', 'Dong Da, Ha Noi', NOW());

INSERT INTO warehouses (warehouses_id, seller_id, warehouse_name, address) VALUES
(1, 1, 'Kho Binh Store', 'Dong Da, Ha Noi');

INSERT INTO categories (categories_id, parent_id, categories_name, slug) VALUES
(1, NULL, 'Thoi trang', 'thoi-trang'),
(2, 1, 'Ao', 'ao'),
(3, 1, 'Quan', 'quan'),
(4, NULL, 'Phu kien', 'phu-kien');

INSERT INTO brands (brand_id, brand_name, company_name, contact_name) VALUES
(1, 'Basic Wear', 'Basic Wear Viet Nam', 'Nguyen Hai'),
(2, 'Daily Style', 'Daily Style Co.', 'Pham Linh');

INSERT INTO products (product_id, category_id, approved_by, seller_id, brand_id, product_name, description, approval_status, approved_at) VALUES
(1, 2, 1, 1, 1, 'Ao thun basic', 'Ao thun cotton mac hang ngay, form regular.', 'Da duyet', NOW()),
(2, 3, 1, 1, 2, 'Quan jeans xanh', 'Quan jeans xanh co gian nhe.', 'Da duyet', NOW()),
(3, 4, 1, 1, 1, 'Mu luoi trai', 'Mu luoi trai phong cach don gian.', 'Da duyet', NOW());

INSERT INTO product_vari (vari_id, product_id, sku, size, color, price, quantity) VALUES
(1, 1, 'ATB-TR-M', 'M', 'Trang', 150000, 50),
(2, 1, 'ATB-DEN-L', 'L', 'Den', 155000, 30),
(3, 2, 'QJ-XANH-30', '30', 'Xanh', 320000, 20),
(4, 3, 'MLT-DEN-F', 'Free size', 'Den', 99000, 40);

INSERT INTO promotion (promotion_id, seller_id, code, discount_percent, discount_amount, start_date, end_date, is_active) VALUES
(1, 1, 'WELCOME10', 10, 0, '2026-01-01 00:00:00', '2028-12-31 23:59:59', TRUE);

INSERT INTO promotion_product (promotion_id, product_id) VALUES (1,1), (1,3);

INSERT INTO purchase_orders (po_id, seller_id, warehouse_id, order_date, total_amount, status) VALUES
(1, 1, 1, NOW(), 2700000, 'Da nhap kho');

INSERT INTO purchase_order_details (po_id, vari_id, quantity, unit_cost) VALUES
(1, 1, 20, 80000), (1, 3, 5, 220000);

INSERT INTO orders (order_id, user_id, order_date, total_amount, discount_amount, final_amount, status, shipping_address) VALUES
(1, 1, NOW(), 300000, 30000, 270000, 'Da giao', 'Cau Giay, Ha Noi'),
(2, 3, NOW(), 320000, 0, 320000, 'Cho xac nhan', 'Ha Dong, Ha Noi');

INSERT INTO order_details (order_detail_id, order_id, vari_id, quantity, unit_price, subtotal) VALUES
(1, 1, 1, 2, 150000, 300000),
(2, 2, 3, 1, 320000, 320000);

INSERT INTO payment (payment_id, order_id, payment_method, amount, payment_status, transaction_code, paid_at) VALUES
(1, 1, 'COD', 270000, 'Da thanh toan', 'COD-DEMO-0001', NOW()),
(2, 2, 'COD', 320000, 'Cho thanh toan', NULL, NULL);

INSERT INTO shipping_method (shipping_method_id, provider_name, base_cost, is_active) VALUES
(1, 'Giao hang tiet kiem', 25000, TRUE),
(2, 'Giao hang nhanh', 35000, TRUE);

INSERT INTO shipment (shipment_id, shipping_method_id, order_id, tracking_code, shipped_date, delivered_date, shipping_status) VALUES
(1, 1, 1, 'DEMO-TRACK-0001', DATE_SUB(NOW(), INTERVAL 2 DAY), NOW(), 'Da giao'),
(2, 2, 2, 'DEMO-TRACK-0002', NULL, NULL, 'Cho lay hang');

INSERT INTO product_review (review_id, order_detail_id, user_id, rating, comment) VALUES
(1, 1, 1, 5, 'San pham dung mo ta, dong goi tot.');

SET FOREIGN_KEY_CHECKS = 1;

-- =========================================================
-- 4. BỘ 7 CÂU LỆNH IN TOÀN BỘ THÔNG TIN HỆ THỐNG
-- =========================================================

-- 1. THỐNG KÊ TỔNG QUAN SỐ LƯỢNG BẢN GHI
SELECT 'Khách hàng / User' AS Doi_Tuong, COUNT(*) AS So_Luong FROM users
UNION ALL SELECT 'Người bán (Shop)', COUNT(*) FROM sellers
UNION ALL SELECT 'Danh mục sản phẩm', COUNT(*) FROM categories
UNION ALL SELECT 'Sản phẩm', COUNT(*) FROM products
UNION ALL SELECT 'Biến thể (SKU)', COUNT(*) FROM product_vari
UNION ALL SELECT 'Đơn đặt hàng', COUNT(*) FROM orders;

-- 2. IN CHI TIẾT THÔNG TIN KHÁCH HÀNG & HẠNG THÀNH VIÊN
SELECT 
    u.user_id AS 'ID_KH',
    u.username AS 'Ten_Dang_Nhap',
    u.full_name AS 'Ho_Va_Ten',
    u.phone AS 'So_Dien_Thoai',
    u.email AS 'Email',
    u.address AS 'Dia_Chi',
    COALESCE(r.rank_name, 'Chua xep hang') AS 'Hang_Thanh_Vien',
    COALESCE(r.discount_rate, 0) AS 'Giam_Gia_Hang(%)',
    u.status AS 'Trang_Thai'
FROM users u
LEFT JOIN user_rank r ON u.rank_id = r.rank_id;

-- 3. IN CHI TIẾT THÔNG TIN NGƯỜI BÁN (SHOP) & KHO HÀNG
SELECT 
    s.seller_id AS 'ID_Shop',
    u.full_name AS 'Chu_So_Huu',
    s.shop_name AS 'Ten_Shop',
    s.tax_code AS 'Ma_So_Thue',
    w.warehouse_name AS 'Ten_Kho_Hang',
    w.address AS 'Dia_Chi_Kho',
    s.approved_at AS 'Ngay_Duyet_Shop'
FROM sellers s
JOIN users u ON s.user_id = u.user_id
LEFT JOIN warehouses w ON s.seller_id = w.seller_id;

-- 4. IN CHI TIẾT SẢN PHẨM, DANH MỤC & TỒN KHO BIẾN THỂ (SKU)
SELECT 
    p.product_id AS 'ID_SP',
    p.product_name AS 'Ten_San_Pham',
    c.categories_name AS 'Danh_Muc',
    s.shop_name AS 'Shop_Ban',
    v.sku AS 'Ma_SKU',
    v.size AS 'Size',
    v.color AS 'Mau',
    v.price AS 'Gia_Ban(D)',
    v.quantity AS 'Ton_Kho'
FROM products p
JOIN categories c ON p.category_id = c.categories_id
JOIN sellers s ON p.seller_id = s.seller_id
JOIN product_vari v ON p.product_id = v.product_id;

-- 5. IN CHI TIẾT ĐƠN HÀNG, THANH TOÁN & VẬN CHUYỂN
SELECT 
    o.order_id AS 'Ma_DH',
    u.full_name AS 'Ten_Khach_Hang',
    o.order_date AS 'Ngay_Dat',
    o.total_amount AS 'Tam_Tinh(D)',
    o.discount_amount AS 'Giam_Gia(D)',
    o.final_amount AS 'Tong_Thanh_Toan(D)',
    o.status AS 'Trang_Thai_DH',
    p.payment_method AS 'Phuong_Thuc_TT',
    p.payment_status AS 'Trang_Thai_TT',
    sm.provider_name AS 'Don_Vi_Giao_Hang',
    sh.tracking_code AS 'Ma_Van_Don'
FROM orders o
JOIN users u ON o.user_id = u.user_id
LEFT JOIN payment p ON o.order_id = p.order_id
LEFT JOIN shipment sh ON o.order_id = sh.order_id
LEFT JOIN shipping_method sm ON sh.shipping_method_id = sm.shipping_method_id;

-- 6. IN CHI TIẾT CÁC MẶT HÀNG TRONG TỪNG ĐƠN HÀNG
SELECT 
    od.order_id AS 'Ma_DH',
    p.product_name AS 'Ten_San_Pham',
    v.sku AS 'SKU',
    od.quantity AS 'So_Luong',
    od.unit_price AS 'Don_Gia(D)',
    od.subtotal AS 'Thanh_Tien(D)'
FROM order_details od
JOIN product_vari v ON od.vari_id = v.vari_id
JOIN products p ON v.product_id = p.product_id;

-- 7. IN CHI TIẾT ĐÁNH GIÁ SẢN PHẨM TỪ KHÁCH HÀNG
SELECT 
    r.review_id AS 'ID_Review',
    u.full_name AS 'Nguoi_Danh_Gia',
    p.product_name AS 'San_Pham',
    r.rating AS 'So_Sao',
    r.comment AS 'Nhan_Xet',
    r.created_at AS 'Thoi_Gian'
FROM product_review r
JOIN users u ON r.user_id = u.user_id
JOIN order_details od ON r.order_detail_id = od.order_detail_id
JOIN product_vari v ON od.vari_id = v.vari_id
JOIN products p ON v.product_id = p.product_id;