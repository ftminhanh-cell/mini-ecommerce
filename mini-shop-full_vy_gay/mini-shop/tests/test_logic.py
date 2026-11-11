import unittest
from decimal import Decimal

import app


class FakeCursor:
    def __init__(self, values):
        self.values = values
        self.last = None

    def execute(self, sql, params):
        if "FROM products" in sql:
            self.last = {"commission_rate": self.values.get("product")}
        elif "FROM sellers" in sql:
            self.last = {"commission_rate": self.values.get("seller")}
        elif "FROM categories" in sql:
            self.last = {"commission_rate": self.values.get("category")}

    def fetchone(self):
        return self.last


class CoreLogicTests(unittest.TestCase):
    def test_money_rounding(self):
        self.assertEqual(app.money(Decimal("12.5")), Decimal("13"))
        self.assertEqual(app.money(Decimal("12.4")), Decimal("12"))

    def test_commission_precedence_product(self):
        cur = FakeCursor({"product": Decimal("2.00"), "seller": Decimal("3.00"), "category": Decimal("4.00")})
        self.assertEqual(app.effective_commission(cur, 1, 2, 3, Decimal("5")), Decimal("2.00"))

    def test_commission_precedence_shop_then_category_default(self):
        cur = FakeCursor({"product": None, "seller": Decimal("3.00"), "category": Decimal("4.00")})
        self.assertEqual(app.effective_commission(cur, 1, 2, 3, Decimal("5")), Decimal("3.00"))
        cur = FakeCursor({"product": None, "seller": None, "category": Decimal("4.00")})
        self.assertEqual(app.effective_commission(cur, 1, 2, 3, Decimal("5")), Decimal("4.00"))
        cur = FakeCursor({"product": None, "seller": None, "category": None})
        self.assertEqual(app.effective_commission(cur, 1, 2, 3, Decimal("5")), Decimal("5"))

    def test_vnpay_signature_stable_and_excludes_hash_fields(self):
        params = {"vnp_Amount": "100000", "vnp_TxnRef": "12", "vnp_SecureHashType": "HmacSHA512"}
        sig = app.vnpay_signature(params, "secret")
        params["vnp_SecureHash"] = "ignored"
        self.assertEqual(sig, app.vnpay_signature(params, "secret"))

    def test_safe_next_blocks_external_redirect(self):
        self.assertEqual(app.safe_next("/orders"), "/orders")
        self.assertIsNone(app.safe_next("https://evil.example"))
        self.assertIsNone(app.safe_next("//evil.example"))


if __name__ == "__main__":
    unittest.main()
