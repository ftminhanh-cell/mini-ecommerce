"""Static smoke checks; does not require MySQL or third-party gateways."""
import ast
from pathlib import Path
from jinja2 import Environment

ROOT = Path(__file__).resolve().parent
app_source = (ROOT / "app.py").read_text(encoding="utf-8")
ast.parse(app_source)
print("PASS: app.py syntax")

env = Environment()
env.filters["vnd"] = lambda value: value
for template in sorted((ROOT / "templates").glob("*.html")):
    env.from_string(template.read_text(encoding="utf-8"))
print("PASS: all Jinja templates parse")

# Ensure route endpoint names are unique and include the main flows.
tree = ast.parse(app_source)
route_names = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "route":
                route_names.append(node.name)
assert len(route_names) == len(set(route_names)), "Duplicate route function names"
required = {"payment_start", "vnpay_return", "momo_ipn", "bank_transfer_webhook", "event_list", "event_detail", "admin_events", "admin_event_create", "admin_event_status", "admin_event_delete", "notifications_page",
            "request_return", "seller_process_return", "admin_refund_return", "admin_category_commission",
            "admin_seller_commission", "admin_product_commission"}
assert required.issubset(set(route_names)), f"Missing routes: {required - set(route_names)}"
print("PASS: route declarations and commerce flows")

schema = (ROOT / "schema.sql").read_text(encoding="utf-8").upper()
assert "DROP DATABASE" not in schema, "schema.sql must not drop the database"
for table in ("NOTIFICATIONS", "RETURN_REQUESTS", "PAYMENT_EVENTS", "COMMISSION_REFUNDS", "COMMISSION_RECORDS", "SELLER_WALLET", "EVENTS", "EVENT_PRODUCTS"):
    assert f"CREATE TABLE {table}" in schema, f"Missing table {table}"
print("PASS: schema safety and required tables")

assert '"BANK_QR"' in app_source and "MINISHOP" in app_source, "Missing VietQR bank transfer flow"
print("PASS: VietQR and event sale features")
