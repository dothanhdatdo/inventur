import io

from sqlalchemy import select

from app.db import AlertLog, Ingredient, Invoice, Supplier, SupplierAlias
from app.services import alerts, stock


def seed_basic(db):
    sup = Supplier(name="Großmarkt Breisgau")
    db.add(sup)
    db.flush()
    rice = Ingredient(name="Gạo Jasmin / Jasminreis", unit="kg", min_stock=20, last_price=1.7, category="Hàng khô")
    chicken = Ingredient(name="Ức gà / Hähnchenbrustfilet", unit="kg", min_stock=5, last_price=8.5, category="Thịt")
    coke = Ingredient(name="Coca-Cola 0,33l", unit="lon", pack_unit="thùng", pack_size=24, min_stock=24, last_price=0.7)
    db.add_all([rice, chicken, coke])
    db.commit()
    return sup, rice, chicken, coke


def test_all_pages_render(client, db):
    seed_basic(db)
    for path in ["/", "/ingredients", "/ingredients/1", "/ingredients/new", "/suppliers", "/invoices",
                 "/invoices/scan", "/recipes", "/recipes/new", "/sales", "/stocktake", "/reports", "/settings"]:
        assert client.get(path).status_code == 200, path


def test_scan_review_confirm_flow(client, db):
    sup, rice, chicken, coke = seed_basic(db)
    fake_png = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    r = client.post("/invoices/scan", files={"file": ("hd.png", fake_png, "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    inv_id = int(r.headers["location"].rsplit("/", 1)[1])
    db.expire_all()
    invoice = db.get(Invoice, inv_id)
    assert invoice.source == "demo" and invoice.supplier_id == sup.id
    by_name = {line.raw_name: line for line in invoice.lines}
    rice_line = by_name["Thai Jasminreis Duftreis 18kg Sack"]
    assert rice_line.ingredient_id == rice.id and rice_line.pack_factor == 18
    assert by_name["Coca-Cola 24x0,33l Dose"].pack_factor == 24
    assert by_name["Pak Choi frisch"].ingredient_id is None

    assert client.get(f"/invoices/{inv_id}").status_code == 200
    assert client.get(f"/invoices/{inv_id}/file").status_code == 200

    # Gửi lại form như người dùng bấm "Nhập kho" (tạo nguyên liệu mới cho Pak Choi).
    form = {"supplier_id": str(sup.id), "invoice_number": "X1", "invoice_date": "2026-10-01", "vat": "10", "action": "confirm"}
    rows = []
    for i, line in enumerate(invoice.lines):
        rows.append(str(i))
        form.update({
            f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
            f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price), f"line_total_{i}": str(line.line_total),
            f"pack_factor_{i}": str(line.pack_factor), f"expiry_date_{i}": "",
            f"ingredient_id_{i}": "new" if line.raw_name == "Pak Choi frisch" else str(line.ingredient_id or ""),
        })
    r = client.post(f"/invoices/{inv_id}", data={**form, "row": rows}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    invoice = db.get(Invoice, inv_id)
    assert invoice.status == "confirmed"
    assert stock.stock_of(db, rice.id) == 36  # 2 bao × 18 kg
    assert stock.stock_of(db, coke.id) == 48  # 2 thùng × 24 lon
    assert abs(db.get(Ingredient, rice.id).last_price - 63.80 / 36) < 1e-6
    assert db.scalars(select(Ingredient).where(Ingredient.name == "Pak Choi frisch")).first() is not None
    # Đã ghi nhớ mapping cho lần sau.
    assert db.scalars(select(SupplierAlias).where(SupplierAlias.ingredient_id == rice.id)).first() is not None
    # Không thể nhập kho hai lần.
    client.post(f"/invoices/{inv_id}", data={**form, "row": rows})
    db.expire_all()
    assert stock.stock_of(db, rice.id) == 36


def test_manual_receive_and_low_stock_alert(client, db, monkeypatch):
    sup, rice, chicken, coke = seed_basic(db)
    sent = []
    monkeypatch.setattr(alerts, "smtp_configured", lambda: True)
    monkeypatch.setattr(alerts, "send_email", lambda to, subj, text, html: (sent.append((to, subj, text)) or ("sent", "")))
    monkeypatch.setattr(alerts.threading, "Thread", _SyncThread)
    alerts.check_low_stock(db)  # gạo & coca đang 0 -> coi như đã báo trước đó
    db.commit()

    client.post(f"/ingredients/{chicken.id}/receive", data={"quantity": "10", "unit_cost": "8.9"})
    assert stock.stock_of(db, chicken.id) == 10
    assert sent == []  # trên ngưỡng -> không gửi

    client.post(f"/ingredients/{chicken.id}/consume", data={"quantity": "6", "kind": "USE"})
    assert len(sent) == 1
    to, subject, text = sent[0]
    assert to == ["dothanhdatdo@gmail.com"]
    assert "Ức gà / Hähnchenbrustfilet (MỚI)" in text and "Gạo Jasmin" in text

    # Xuất tiếp nhưng đã báo rồi -> không gửi trùng.
    client.post(f"/ingredients/{chicken.id}/consume", data={"quantity": "1", "kind": "USE"})
    assert len(sent) == 1
    # Nhập lại lên trên ngưỡng -> đặt lại cờ; xuống dưới lần nữa -> gửi lại.
    client.post(f"/ingredients/{chicken.id}/receive", data={"quantity": "10"})
    client.post(f"/ingredients/{chicken.id}/consume", data={"quantity": "10", "kind": "WASTE"})
    assert len(sent) == 2
    db.expire_all()
    assert db.scalars(select(AlertLog)).all()[-1].status == "sent"


def test_threshold_settings_and_report_without_smtp(client, db):
    sup, rice, chicken, coke = seed_basic(db)
    client.post("/settings/alerts", data={"alert_email": "a@example.com, b@example.com", "alerts_enabled": "1"})
    assert alerts.alert_recipients(db) == ["a@example.com", "b@example.com"]
    client.post("/settings/thresholds", data={f"min_{rice.id}": "50"})
    db.expire_all()
    assert db.get(Ingredient, rice.id).min_stock == 50
    r = client.post("/settings/send-report", follow_redirects=True)
    assert "Chưa gửi được email" in r.text
    log = db.scalars(select(AlertLog)).all()[-1]
    assert log.status == "not_configured"


class _SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_browser_manifest_is_up_to_date():
    """web/manifest.json phải khớp mã nguồn, nếu không bản GitHub Pages sẽ chạy code cũ."""
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools"))
    import build_manifest

    current = json.loads((root / "web" / "manifest.json").read_text(encoding="utf-8"))
    assert current == build_manifest.build(), "Hãy chạy: python tools/build_manifest.py"


def test_ingredient_names_are_escaped_in_scripts(client, db):
    db.add(Ingredient(name="Evil", unit="</script><script>alert(1)</script>", last_price=1))
    db.commit()
    inv = client.post("/invoices/manual", follow_redirects=True)
    assert "</script><script>alert(1)" not in inv.text
    assert "</script><script>alert(1)" not in client.get("/recipes/new").text


def test_login_rejects_external_redirect(monkeypatch, client, db):
    from app import config

    monkeypatch.setattr(config, "APP_PASSWORD", "pw")
    r = client.post("/login", data={"password": "pw", "next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"
