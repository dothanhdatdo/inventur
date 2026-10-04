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


def test_gemini_settings_and_scan(client, db, monkeypatch):
    import json as _json

    from app.services import ocr

    sup, rice, chicken, coke = seed_basic(db)
    r = client.post("/settings/ocr", data={"ocr_provider": "gemini", "ocr_gemini_key": "AIza-test",
                                           "ocr_gemini_model": "gemini-flash-latest"}, follow_redirects=True)
    assert "đang dùng: Gemini" in r.text
    assert "AIza-test" not in r.text  # key không bao giờ hiện lại trên trang
    settings = ocr.get_settings(db)
    assert settings["gemini_key"] == "AIza-test" and ocr.active_provider(settings) == "gemini"

    async def fake(url, headers, payload):
        assert headers["x-goog-api-key"] == "AIza-test"
        result = {"supplier": "Großmarkt Breisgau", "invoice_date": "01.10.2026",
                  "lines": [{"name": "Hähnchenbrustfilet frisch", "quantity": "5,5", "unit": "kg",
                             "unit_price": "8,99", "total": "49,45", "vat_rate": 7}]}
        return 200, _json.dumps({"candidates": [{"content": {"parts": [{"text": _json.dumps(result)}]}}]})

    monkeypatch.setattr(ocr, "_post_json", fake)
    r = client.post("/invoices/scan", files={"file": ("hd.jpg", b"\xff\xd8\xff" + b"0" * 50, "image/jpeg")},
                    follow_redirects=True)
    assert "Đọc bởi Gemini" in r.text
    inv = db.scalars(select(Invoice).order_by(Invoice.id.desc())).first()
    db.refresh(inv)
    assert inv.source == "gemini" and inv.supplier_id == sup.id
    assert inv.lines[0].ingredient_id == chicken.id and inv.lines[0].quantity == 5.5

    # Gemini hết lượt -> vẫn giữ ảnh, tạo phiếu trống để nhập tay, báo lỗi dễ hiểu.
    async def limited(url, headers, payload):
        return 429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}'

    monkeypatch.setattr(ocr, "_post_json", limited)
    r = client.post("/invoices/scan", files={"file": ("hd2.jpg", b"\xff\xd8\xff" + b"1" * 50, "image/jpeg")},
                    follow_redirects=True)
    assert "hết lượt miễn phí" in r.text and "Phiếu nhập thủ công" not in r.text


def test_gemini_without_key_warns_and_errors_have_single_period(client, db, monkeypatch):
    from app.services import ocr

    r = client.post("/settings/ocr", data={"ocr_provider": "gemini"}, follow_redirects=True)
    assert "chưa có API key" in r.text and "flash-warn" in r.text
    scan = client.get("/invoices/scan").text
    assert "Thiếu API key" in scan and "AI sẵn sàng" not in scan

    client.post("/settings/ocr", data={"ocr_provider": "gemini", "ocr_gemini_key": "AIza-x"})
    assert "AI sẵn sàng" in client.get("/invoices/scan").text

    async def bad_key(url, headers, payload):
        return 400, '{"error": {"message": "API key not valid. Please pass a valid API key."}}'

    monkeypatch.setattr(ocr, "_post_json", bad_key)
    r = client.post("/invoices/scan", files={"file": ("a.png", b"\x89PNG" + b"0" * 20, "image/png")}, follow_redirects=True)
    assert "key không hợp lệ" in r.text and ".." not in r.text.split("flash-error")[1].split("</div>")[0]


def test_area_switch_filters_pages(client, db):
    from app.db import Dish, RecipeItem

    sup, rice, chicken, coke = seed_basic(db)
    coke.area = "bar"
    cola = Dish(name="Coca-Cola", price=3.5, area="bar")
    cola.items = [RecipeItem(ingredient_id=coke.id, quantity=1)]
    pho = Dish(name="Phở bò", price=14.9, area="kitchen")
    db.add_all([cola, pho])
    db.commit()

    r = client.get("/area/bar?next=/ingredients", follow_redirects=True)
    assert "Coca-Cola 0,33l" in r.text and "Gạo Jasmin" not in r.text and "Quầy · Theke" in r.text
    assert "Phở bò" not in client.get("/sales").text and "Coca-Cola" in client.get("/recipes").text
    st = client.get("/stocktake").text
    assert 'name="area" value="bar"' in st and f"count_{coke.id}" in st and f"count_{rice.id}" not in st

    r = client.get("/area/kitchen?next=/ingredients", follow_redirects=True)
    assert "Gạo Jasmin" in r.text and "Coca-Cola 0,33l" not in r.text
    r = client.get("/area/all?next=/", follow_redirects=True)
    assert "area-cards" in r.text
    assert client.get("/area/bar?next=//evil.example", follow_redirects=False).headers["location"] == "/"


def test_stocktake_per_area_and_report(client, db):
    sup, rice, chicken, coke = seed_basic(db)
    coke.area = "bar"
    db.commit()
    from app.services import stock as st_service

    st_service.receive(db, coke, 24, 0.7)
    db.commit()
    client.get("/area/bar")
    r = client.post("/stocktake", data={"area": "bar", f"count_{coke.id}": "20", "note": "Theke"}, follow_redirects=True)
    assert "Quầy · Theke" in r.text and "-2,80" in r.text  # thiếu 4 lon × 0,70 €
    from app.db import Stocktake

    assert db.scalars(select(Stocktake)).first().area == "bar"


def test_new_ingredient_from_invoice_guesses_area(client, db):
    from app.db import Invoice as Inv, InvoiceLine

    sup, rice, chicken, coke = seed_basic(db)
    inv = Inv(supplier_id=sup.id, source="manual")
    inv.lines = [InvoiceLine(raw_name="Mangosaft 1l", quantity=6, unit_raw="Fl", unit_price=2, line_total=12, vat_rate=19),
                 InvoiceLine(raw_name="Pak Choi frisch", quantity=3, unit_raw="kg", unit_price=3, line_total=9, vat_rate=7)]
    db.add(inv)
    db.commit()
    form = {"supplier_id": str(sup.id), "vat": "0", "action": "confirm"}
    rows = []
    for i, line in enumerate(inv.lines):
        rows.append(str(i))
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": "1", f"ingredient_id_{i}": "new"})
    r = client.post(f"/invoices/{inv.id}", data={**form, "row": rows}, follow_redirects=True)
    assert "Chia theo khu" in r.text
    db.expire_all()
    areas_by_name = {i.name: i.area for i in db.scalars(select(Ingredient)).all()}
    assert areas_by_name["Mangosaft 1l"] == "bar" and areas_by_name["Pak Choi frisch"] == "kitchen"


def test_orders_page_and_area_emails_settings(client, db):
    sup, rice, chicken, coke = seed_basic(db)
    coke.supplier_id = sup.id
    coke.par_level = 96
    coke.area = "bar"
    db.commit()
    r = client.get("/orders")
    assert "Großmarkt Breisgau" in r.text and "4 × Kartons Coca-Cola 0,33l" in r.text and "wir möchten bestellen" in r.text
    client.post("/settings/alerts", data={"alert_email": "a@x.de", "alert_email_kitchen": "koch@x.de",
                                          "alert_email_bar": "", "alerts_enabled": "1"})
    assert alerts.alert_recipients(db, "kitchen") == ["koch@x.de"]
    assert alerts.alert_recipients(db, "bar") == ["a@x.de"]
    client.post("/settings/thresholds", data={f"par_{rice.id}": "60", f"min_{rice.id}": "25"})
    db.expire_all()
    assert db.get(Ingredient, rice.id).par_level == 60 and db.get(Ingredient, rice.id).min_stock == 25


def test_reports_split_by_area(client, db):
    seed_basic(db)
    r = client.get("/reports")
    assert "Bếp (Küche) và Quầy (Theke)" in r.text and "Beverage cost" in r.text
    client.get("/area/bar")
    assert client.get("/reports").status_code == 200


def test_moving_ingredient_to_other_area_alerts_new_recipient(client, db, monkeypatch):
    sup, rice, chicken, coke = seed_basic(db)
    alerts.set_setting(db, "alert_email_bar", "bar@x.de")
    alerts.check_low_stock(db)  # mọi thứ đang 0 -> coi như đã báo cho bếp
    db.commit()
    sent = []
    monkeypatch.setattr(alerts, "smtp_configured", lambda: True)
    monkeypatch.setattr(alerts, "send_email", lambda to, subj, text, html: (sent.append(to) or ("sent", "")))
    monkeypatch.setattr(alerts.threading, "Thread", _SyncThread)
    client.post(f"/ingredients/{coke.id}/edit", data={"name": coke.name, "unit": "lon", "pack_unit": "thùng",
                                                      "pack_size": "24", "min_stock": "24", "area": "bar"})
    assert sent == [["bar@x.de"]]
