/* ==========================================================================
   MINI SHOP - main.js  (JavaScript thuần, không cần thư viện ngoài Bootstrap)
   Các hành vi được bật bằng thuộc tính data-* trong HTML:
     data-confirm            hỏi xác nhận trước khi bấm link/nút/gửi form
     data-confirm-field/when chỉ hỏi khi ô <select> có giá trị chỉ định
     data-autodismiss        tự đóng thông báo sau vài giây
     data-autosubmit         tự gửi form khi đổi <select>
     data-toggle-password    nút hiện/ẩn mật khẩu
     data-preview            xem trước ảnh khi chọn file
     data-order-form         form đặt hàng: cập nhật giá, tồn kho, tổng tiền
     data-fill-promo         bấm vào mã voucher để điền vào ô nhập
     data-autosubmit-input   tự gửi form khi đổi ô số (giỏ hàng)
     data-cart-form          form thanh toán giỏ hàng: tổng tiền theo ô đã chọn
     (CSRF)                  tự chèn _csrf vào mọi form POST từ <meta name="csrf-token">
   ========================================================================== */
(function () {
    'use strict';

    var $ = function (sel, root) { return (root || document).querySelector(sel); };
    var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
    var formatMoney = function (n) { return Number(n || 0).toLocaleString('vi-VN', { maximumFractionDigits: 0 }); };

    var MAX_IMAGE_BYTES = 5 * 1024 * 1024; // khớp MAX_FILE_SIZE trong app.py

    /* ---------- 1. Hộp xác nhận ---------- */
    document.addEventListener('click', function (e) {
        var el = e.target.closest('a[data-confirm], button[data-confirm]');
        if (el && !window.confirm(el.getAttribute('data-confirm'))) {
            e.preventDefault();
            e.stopPropagation();
        }
    });

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form.matches || !form.matches('form[data-confirm]')) return;

        var field = form.getAttribute('data-confirm-field');
        if (field) {
            var input = form.elements[field];
            if (!input || input.value !== form.getAttribute('data-confirm-when')) return;
        }
        if (!window.confirm(form.getAttribute('data-confirm'))) e.preventDefault();
    });

    /* ---------- 2. Chống bấm đúp khi gửi form ---------- */
    document.addEventListener('submit', function (e) {
        if (e.defaultPrevented) return; // đã bị hủy ở bước xác nhận
        var form = e.target;
        var btn = e.submitter || $('button[type="submit"]', form);
        if (!btn || btn.hasAttribute('data-no-loading')) return;

        if (form.getAttribute('data-submitted')) {
            e.preventDefault();
            return;
        }
        form.setAttribute('data-submitted', '1');

        // Trì hoãn để trình duyệt kịp lấy dữ liệu form trước khi vô hiệu hóa nút
        setTimeout(function () {
            btn.classList.add('is-loading');
            btn.disabled = true;
            btn.insertAdjacentHTML(
                'afterbegin',
                '<span class="spinner-border spinner-border-sm me-2 js-spinner" aria-hidden="true"></span>'
            );
        }, 0);
    });

    // Quay lại bằng nút Back: trả form về trạng thái ban đầu
    window.addEventListener('pageshow', function (e) {
        if (!e.persisted) return;
        $$('form[data-submitted]').forEach(function (f) { f.removeAttribute('data-submitted'); });
        $$('.is-loading').forEach(function (b) { b.classList.remove('is-loading'); b.disabled = false; });
        $$('.js-spinner').forEach(function (s) { s.remove(); });
    });

    /* ---------- 3. Tự đóng thông báo ---------- */
    $$('.alert[data-autodismiss]').forEach(function (el) {
        setTimeout(function () {
            if (window.bootstrap && document.body.contains(el)) {
                window.bootstrap.Alert.getOrCreateInstance(el).close();
            }
        }, 6000);
    });

    /* ---------- 4. Bộ lọc trang chủ tự gửi khi đổi ---------- */
    $$('select[data-autosubmit]').forEach(function (select) {
        select.addEventListener('change', function () { select.form.submit(); });
    });

    /* ---------- 5. Hiện / ẩn mật khẩu ---------- */
    $$('[data-toggle-password]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = $(btn.getAttribute('data-toggle-password'));
            if (!input) return;
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.setAttribute('aria-label', show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu');
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
            var icon = $('i', btn);
            if (icon) icon.className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
        });
    });

    /* ---------- 6. Xem trước ảnh upload ---------- */
    $$('input[type="file"][data-preview]').forEach(function (input) {
        var img = $(input.getAttribute('data-preview'));
        var objectUrl = null;
        var errorBox = null;

        function clearState() {
            if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
            if (errorBox) { errorBox.remove(); errorBox = null; }
            input.classList.remove('is-invalid');
            if (img) { img.classList.add('d-none'); img.removeAttribute('src'); }
        }

        function fail(message) {
            input.value = '';
            input.classList.add('is-invalid');
            errorBox = document.createElement('div');
            errorBox.className = 'invalid-feedback';
            errorBox.textContent = message;
            input.after(errorBox);
        }

        input.addEventListener('change', function () {
            clearState();
            var file = input.files && input.files[0];
            if (!file) return;
            if (file.type.indexOf('image/') !== 0) {
                fail('Tệp đã chọn không phải là ảnh. Hãy chọn PNG, JPG, WEBP hoặc GIF.');
                return;
            }
            if (file.size > MAX_IMAGE_BYTES) {
                fail('Ảnh lớn hơn 5MB. Hãy chọn ảnh nhỏ hơn.');
                return;
            }
            if (img) {
                objectUrl = URL.createObjectURL(file);
                img.src = objectUrl;
                img.classList.remove('d-none');
            }
        });
    });

    /* ---------- 7. Form đặt hàng: giá, tồn kho, tổng tiền ---------- */
    var orderForm = $('[data-order-form]');
    if (orderForm) {
        var variantSel = $('[name="vari_id"]', orderForm);
        var qtyInput = $('[name="quantity"]', orderForm);
        var shipSel = $('[name="shipping_method_id"]', orderForm);
        var out = {
            price: $('[data-out="price"]', orderForm),
            stock: $('[data-out="stock"]', orderForm),
            ship: $('[data-out="ship"]', orderForm),
            total: $('[data-out="total"]', orderForm)
        };

        var updateSummary = function (clampQty) {
            var opt = variantSel && variantSel.selectedOptions[0];
            var price = opt ? Number(opt.getAttribute('data-price') || 0) : 0;
            var stock = opt ? Number(opt.getAttribute('data-stock') || 0) : 0;
            var shipOpt = shipSel && shipSel.selectedOptions[0];
            var shipCost = shipOpt ? Number(shipOpt.getAttribute('data-cost') || 0) : 0;

            if (qtyInput) {
                qtyInput.max = Math.max(stock, 1);
                var q = parseInt(qtyInput.value, 10);
                if (clampQty) {
                    if (isNaN(q) || q < 1) q = 1;
                    if (stock > 0 && q > stock) q = stock;
                    qtyInput.value = q;
                }
                var qty = isNaN(q) || q < 1 ? 1 : Math.min(q, Math.max(stock, 1));
                if (out.total) out.total.textContent = formatMoney(price * qty + shipCost) + ' đ';
            }
            if (out.price) out.price.textContent = formatMoney(price) + ' đ';
            if (out.ship) out.ship.textContent = formatMoney(shipCost) + ' đ';
            if (out.stock) {
                out.stock.textContent = stock > 0 ? 'Còn ' + stock + ' sản phẩm' : 'Hết hàng';
                out.stock.classList.toggle('stock-low', stock <= 5);
            }
        };

        if (variantSel) variantSel.addEventListener('change', function () { updateSummary(true); });
        if (shipSel) shipSel.addEventListener('change', function () { updateSummary(false); });
        if (qtyInput) {
            qtyInput.addEventListener('input', function () { updateSummary(false); });
            qtyInput.addEventListener('change', function () { updateSummary(true); });
        }
        updateSummary(true);
    }

    /* ---------- 8. Bấm mã voucher để điền vào ô nhập ---------- */
    $$('[data-fill-promo]').forEach(function (chip) {
        chip.addEventListener('click', function () {
            var target = $('[name="promo_code"]');
            if (!target) return;
            target.value = chip.getAttribute('data-fill-promo');
            target.focus();
        });
    });

    /* ---------- 9. Kênh người bán: nhớ tab đang mở ---------- */
    var sellerTabs = $('#sellerTab');
    if (sellerTabs && window.bootstrap) {
        var STORAGE_KEY = 'mini-shop-seller-tab';
        try {
            var saved = sessionStorage.getItem(STORAGE_KEY);
            var savedBtn = saved && $('[data-bs-target="' + saved + '"]', sellerTabs);
            if (savedBtn) window.bootstrap.Tab.getOrCreateInstance(savedBtn).show();
        } catch (err) { /* trình duyệt chặn storage: bỏ qua */ }

        sellerTabs.addEventListener('shown.bs.tab', function (e) {
            try { sessionStorage.setItem(STORAGE_KEY, e.target.getAttribute('data-bs-target')); } catch (err) { /* bỏ qua */ }
        });
    }

    /* ---------- 10. CSRF: tự thêm token vào mọi form POST ---------- */
    var csrfMeta = $('meta[name="csrf-token"]');
    if (csrfMeta) {
        $$('form').forEach(function (form) {
            if ((form.getAttribute('method') || '').toUpperCase() !== 'POST') return;
            if (form.querySelector('input[name="_csrf"]')) return;
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = '_csrf';
            input.value = csrfMeta.getAttribute('content');
            form.appendChild(input);
        });
    }

    /* ---------- 11. Giỏ hàng: tự gửi khi đổi số lượng, tính tổng theo ô chọn ---------- */
    $$('input[data-autosubmit-input]').forEach(function (input) {
        input.addEventListener('change', function () { input.form.submit(); });
    });

    var cartForm = $('[data-cart-form]');
    if (cartForm) {
        var shipSelect = $('[data-ship]', cartForm);
        var rate = Number(cartForm.getAttribute('data-rate') || 0);
        var cartOut = {
            count: $('[data-out="cart-count"]', cartForm),
            shops: $('[data-out="cart-shops"]', cartForm),
            sub: $('[data-out="cart-sub"]', cartForm),
            rank: $('[data-out="cart-rank"]', cartForm),
            ship: $('[data-out="cart-ship"]', cartForm),
            total: $('[data-out="cart-total"]', cartForm)
        };
        var recalcCart = function () {
            var boxes = $$('[data-cart-select]').filter(function (b) { return b.checked && !b.disabled; });
            var sub = 0, shops = {};
            boxes.forEach(function (b) {
                sub += Number(b.getAttribute('data-amount') || 0);
                shops[b.getAttribute('data-shop')] = true;
            });
            var shopCount = Object.keys(shops).length;
            var opt = shipSelect && shipSelect.selectedOptions[0];
            var ship = (opt ? Number(opt.getAttribute('data-cost') || 0) : 0) * shopCount;
            var rankOff = Math.round(sub * rate / 100);
            if (cartOut.count) cartOut.count.textContent = boxes.length;
            if (cartOut.shops) cartOut.shops.textContent = shopCount;
            if (cartOut.sub) cartOut.sub.textContent = formatMoney(sub) + ' đ';
            if (cartOut.rank) cartOut.rank.textContent = '- ' + formatMoney(rankOff) + ' đ';
            if (cartOut.ship) cartOut.ship.textContent = formatMoney(ship) + ' đ';
            if (cartOut.total) cartOut.total.textContent = formatMoney(sub - rankOff + ship) + ' đ';
        };
        $$('[data-cart-select]').forEach(function (b) { b.addEventListener('change', recalcCart); });
        if (shipSelect) shipSelect.addEventListener('change', recalcCart);
        recalcCart();
    }
})();