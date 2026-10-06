from datetime import date

from sqlalchemy import func, select

from app import imports
from app.db import Batch, Dish, Ingredient, Invoice, Sale, Stocktake, Supplier
from app.imports import metro_20260919 as metro
from app.services import alerts, stock


def test_metro_lines_match_invoice_totals():
    # Tổng theo nhóm hàng trên hoá đơn (WARENGRUPPENSTATISTIK, sau giảm giá).
    by_vat = {}
    for line in metro.LINES:
        by_vat[line[-1]] = round(by_vat.get(line[-1], 0) + line[-2], 2)
    bier_afg = sum(line[-2] for line in metro.LINES if line[1] in ("Bia / Bier", "Nước ngọt / Softdrinks", "Nước ép / Säfte"))
    wein = sum(line[-2] for line in metro.LINES if line[1] == "Rượu vang / Wein")
    spirits = sum(line[-2] for line in metro.LINES if line[1] == "Rượu mạnh / Spirituosen")
    assert round(bier_afg, 2) == 423.46 and round(wein, 2) == 263.94 and round(spirits, 2) == 155.64
    assert round(sum(line[-2] for line in metro.LINES), 2) == 891.03
    assert by_vat == {19: 855.60, 7: 35.43}


def test_import_on_empty_database(db):
    notes = imports.apply_invoice_import(db, metro)
    assert "nhập 23 mặt hàng quầy" in notes
    inv = db.scalars(select(Invoice).where(Invoice.invoice_number == metro.INVOICE_NUMBER)).one()
    assert inv.status == "confirmed" and inv.invoice_date == date(2026, 9, 19) and inv.subtotal == 891.03
    assert abs(inv.vat - 165.04) < 0.01
    coke = db.scalars(select(Ingredient).where(Ingredient.name == "Coca-Cola 0,5l")).one()
    assert coke.area == "bar" and stock.stock_of(db, coke.id) == 132 and abs(coke.last_price - 0.69) < 1e-9
    chardonnay = db.scalars(select(Ingredient).where(Ingredient.name.like("%Chardonnay%"))).one()
    assert stock.stock_of(db, chardonnay.id) == 12  # hai dòng trên hoá đơn gộp vào một mặt hàng
    assert abs(stock.stock_value(db, "bar") - 891.03) < 0.01
    # Lần quét hoá đơn METRO sau: tên hàng đã được ghi nhớ.
    from app.services import matching

    match = matching.match_line(db, inv.supplier_id, "0,50 DPG PET COCA-COLA", "SP")
    assert match.from_alias and match.ingredient.id == coke.id and match.pack_factor == 12


def _old_demo_drinks(db):
    sup = Supplier(name="Getränke Breisgau")
    db.add(sup)
    db.flush()
    coke = Ingredient(name="Coca-Cola 0,33l", unit="lon", area="bar", supplier_id=sup.id, last_price=0.7)
    rice = Ingredient(name="Gạo Jasmin / Jasminreis", unit="kg", area="kitchen", last_price=1.7)
    db.add_all([coke, rice])
    db.flush()
    stock.receive(db, coke, 48, 0.7, supplier_id=sup.id)
    stock.receive(db, rice, 20, 1.7)
    dish = Dish(name="Coca-Cola 0,33l", price=3.5, area="bar")
    from app.db import RecipeItem

    dish.items = [RecipeItem(ingredient_id=coke.id, quantity=1)]
    db.add(dish)
    db.flush()
    stock.record_sale(db, dish, 5)
    stock.apply_stocktake(db, {coke.id: 40}, area="bar")
    db.commit()
    return coke, rice


def test_import_removes_demo_drinks_and_keeps_kitchen(db):
    coke, rice = _old_demo_drinks(db)
    imports.apply_invoice_import(db, metro)
    names = set(db.scalars(select(Ingredient.name)))
    assert "Coca-Cola 0,33l" not in names and "Gạo Jasmin / Jasminreis" in names and "Coca-Cola 0,5l" in names
    assert db.scalar(select(func.count(Sale.id))) == 0
    assert db.scalar(select(func.count(Dish.id))) == 0
    assert db.scalar(select(func.count(Stocktake.id))) == 0  # phiếu kiểm kê chỉ có đồ uống mẫu
    assert db.scalars(select(Supplier).where(Supplier.name == "Getränke Breisgau")).first() is None
    assert stock.stock_of(db, rice.id) == 20


def test_apply_pending_runs_once(db, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "APPLY_IMPORTS", True)
    assert imports.apply_pending(db)
    assert imports.apply_pending(db) == []
    assert db.scalar(select(func.count(Invoice.id)).where(Invoice.invoice_number == metro.INVOICE_NUMBER)) == 1
    assert "METRO" in alerts.get_setting(db, imports.NOTICE_KEY)


def test_existing_invoice_is_not_imported_twice(db):
    db.add(Invoice(invoice_number=metro.INVOICE_NUMBER, source="gemini"))
    db.commit()
    note = imports.apply_invoice_import(db, metro)
    assert "không nhập lại" in note
    assert db.scalar(select(func.count(Batch.id))) == 0


# ---------------------------------------------------------------- hoá đơn METRO 05.10.2026 (nhập trọn hoá đơn)
from pathlib import Path

from app.imports import metro_20261005 as metro2
from app.services import invoices as invoice_service
from app.services import pdf_invoice

FIXTURE_2 = (Path(__file__).parent / "fixtures" / "metro_invoice_2.txt").read_text(encoding="utf-8")


def _ing(db, name):
    return db.scalars(select(Ingredient).where(Ingredient.name == name)).one()


def test_metro2_lines_match_pdf_exactly():
    parsed = pdf_invoice.parse_metro(FIXTURE_2)
    assert len(parsed.lines) == len(metro2.LINES)
    for p, (row, raw, art, unit_raw, qty, units, total, vat, expiry, category, key) in zip(parsed.lines, metro2.LINES):
        assert (p.name, p.article_no, p.unit, round(p.quantity, 3), p.units_per_pack, p.total, p.vat_rate, p.expiry, p.category) == (
            raw, art, unit_raw, round(qty, 3), units, total, vat, expiry, category), raw
        assert key is None or key in metro2.ITEMS
    assert round(sum(line[6] for line in metro2.LINES), 2) == metro2.SUBTOTAL == parsed.subtotal
    assert metro2.VAT == parsed.vat and metro2.DEPOSIT == parsed.deposit


def test_metro2_import_after_first_metro_import(db):
    imports.apply_invoice_import(db, metro)  # như dữ liệu của người dùng: đồ uống 19.09 đã nhập
    note = imports.apply_full_invoice(db, metro2)
    assert "9 mặt hàng bếp, 4 mặt hàng quầy" in note and "2 dòng dụng cụ" in note
    inv = db.scalars(select(Invoice).where(Invoice.invoice_number == metro2.INVOICE_NUMBER)).one()
    assert inv.status == "confirmed" and inv.invoice_date == date(2026, 10, 5) and inv.source == "email"
    assert inv.subtotal == 632.60 and inv.vat == 92.98 and inv.total == 725.58
    assert inv.confirmed_at.strftime("%d.%m.%Y %H:%M") == "05.10.2026 09:33"
    # Coca-Cola: cùng mặt hàng với hoá đơn 19.09 (không tạo trùng), cộng thêm 22 × 12 chai.
    assert db.scalar(select(func.count(Ingredient.id)).where(Ingredient.name.like("Coca-Cola%"))) == 2
    assert stock.stock_of(db, _ing(db, "Coca-Cola 0,5l").id) == 132 + 264
    assert stock.stock_of(db, _ing(db, "Coca-Cola Zero 0,5l").id) == 108 + 192
    duck = _ing(db, "Vịt nguyên con / Barbarie-Ente")
    assert duck.area == "kitchen" and duck.unit == "kg" and abs(stock.stock_of(db, duck.id) - 4.843) < 1e-9
    batches = db.scalars(select(Batch).where(Batch.ingredient_id == duck.id)).all()
    assert {b.expiry_date for b in batches} == {date(2026, 10, 9)} and all(b.received_at.day == 5 for b in batches)
    butter = _ing(db, "Bơ / Butter mildgesäuert 250g")
    assert stock.stock_of(db, butter.id) == 20 and abs(butter.last_price - 4.44) < 1e-9
    assert stock.stock_of(db, _ing(db, "Nước khoáng có ga / ViO Spritzig 0,5l").id) == 36
    assert _ing(db, "Nước khoáng có ga / ViO Spritzig 0,5l").area == "bar"
    assert stock.stock_of(db, _ing(db, "Đậu Hà Lan / Erbsen sehr fein (TK)").id) == 10
    assert stock.stock_of(db, _ing(db, "Nước lau đa năng / Allzweckreiniger 10l").id) == 3
    assert not db.scalars(select(Ingredient).where(Ingredient.name.like("%DECKEL%"))).all()  # dụng cụ: không vào kho
    # Giá trị nhập kho = tiền hàng trừ 2 dòng nắp GN (25,12 €).
    received = sum(b.quantity_initial * b.unit_cost for b in db.scalars(select(Batch).where(Batch.invoice_id == inv.id)))
    assert abs(received - (632.60 - 25.12)) < 0.01


def test_metro2_runs_once_and_shows_notice(db, monkeypatch):
    monkeypatch.setattr(imports.config, "APPLY_IMPORTS", True)
    notes = imports.apply_pending(db)
    assert len(notes) == 2 and "05.10.2026" in notes[1]
    assert imports.apply_pending(db) == []
    assert db.scalar(select(func.count(Invoice.id)).where(Invoice.invoice_number == metro2.INVOICE_NUMBER)) == 1
    assert "05.10.2026" in alerts.get_setting(db, imports.NOTICE_KEY)


def _scan_fixture_2(client, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE_2)
    return client.post("/invoices/scan", files={"file": ("rechnung.pdf", b"%PDF-1.4 x", "application/pdf")},
                       follow_redirects=True)


def _confirm_as_new(client, db, draft, only=None):
    form = {"supplier_id": str(draft.supplier_id or ""), "invoice_number": draft.invoice_number, "vat": str(draft.vat),
            "action": "confirm", "row": []}
    for i, line in enumerate(draft.lines):
        form["row"].append(str(i))
        choice = "new" if (only is None or line.raw_name in only) and line.match_score >= 0 else ""
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": str(line.pack_factor),
                     f"ingredient_id_{i}": choice})
        if line.match_score < 0:
            form[f"delete_{i}"] = "1"
    return client.post(f"/invoices/{draft.id}", data=form, follow_redirects=True)


def _latest_draft(db):
    db.expire_all()
    return db.scalars(select(Invoice).where(Invoice.status == "draft").order_by(Invoice.id.desc())).first()


def test_metro2_import_skips_invoice_the_user_already_scanned(client, db, monkeypatch):
    _scan_fixture_2(client, monkeypatch)
    _confirm_as_new(client, db, _latest_draft(db))
    db.expire_all()
    before = stock.stock_map(db)
    ingredients_before = db.scalar(select(func.count(Ingredient.id)))
    note = imports.apply_full_invoice(db, metro2)
    assert note.startswith("Hoá đơn METRO 05.10.2026 đã có trong kho")
    db.expire_all()
    assert stock.stock_map(db) == before and db.scalar(select(func.count(Ingredient.id))) == ingredients_before
    assert db.scalar(select(func.count(Invoice.id)).where(Invoice.invoice_number == metro2.INVOICE_NUMBER)) == 1


def test_metro2_import_completes_a_partial_scan(client, db, monkeypatch):
    _scan_fixture_2(client, monkeypatch)
    drinks = {"0,50 DPG PET COCA-COLA", "0,50 DPG PET COCA-COLA ZERO"}
    _confirm_as_new(client, db, _latest_draft(db), only=drinks)
    db.expire_all()
    coke = _ing(db, "0,50 DPG PET COCA-COLA")
    assert stock.stock_of(db, coke.id) == 264
    note = imports.apply_full_invoice(db, metro2)
    assert "2 dòng đã có từ lần quét trước" in note
    db.expire_all()
    assert stock.stock_of(db, coke.id) == 264  # không cộng lần hai
    assert not db.scalars(select(Ingredient).where(Ingredient.name == "Coca-Cola 0,5l")).all()  # không tạo trùng
    assert abs(stock.stock_of(db, _ing(db, "Vịt nguyên con / Barbarie-Ente").id) - 4.843) < 1e-9
    copies = db.scalars(select(Invoice).where(Invoice.invoice_number == metro2.INVOICE_NUMBER)).all()
    assert len(copies) == 2 and round(sum(i.subtotal for i in copies), 2) == 632.60


def test_metro2_scanning_after_import_flags_every_booked_row(client, db, monkeypatch):
    imports.apply_full_invoice(db, metro2)
    r = _scan_fixture_2(client, monkeypatch)
    draft = _latest_draft(db)
    flagged = [line.source_row for line in draft.lines if line.match_score < 0]
    assert flagged == list(range(2, 21)) and "19 dòng trùng" in r.text  # 2 dòng nắp GN không vào kho -> không đánh dấu
    before = stock.stock_map(db)
    r = _confirm_as_new(client, db, draft, only=set())
    assert "Chưa có dòng nào được gắn" in r.text
    db.expire_all()
    assert stock.stock_map(db) == before


def test_metro2_draft_left_open_is_not_double_booked(client, db, monkeypatch):
    """Người dùng quét PDF nhưng chưa bấm Nhập kho; sau đó bản nhập tự động chạy, rồi họ mới bấm Nhập kho phiếu cũ."""
    _scan_fixture_2(client, monkeypatch)
    draft = _latest_draft(db)
    imports.apply_full_invoice(db, metro2)
    db.expire_all()
    before = stock.stock_map(db)
    r = _confirm_as_new(client, db, db.get(Invoice, draft.id))
    db.expire_all()
    assert "Bỏ qua 19 dòng đã nhập kho" in r.text or "đều đã được nhập kho" in r.text
    after = stock.stock_map(db)
    assert all(after.get(k, 0) == v for k, v in before.items())


def test_failing_import_does_not_break_startup(db, monkeypatch):
    monkeypatch.setattr(imports.config, "APPLY_IMPORTS", True)

    def boom(session, module):
        session.add(Supplier(name="half-written"))
        session.flush()
        raise RuntimeError("PDF lạ")

    monkeypatch.setattr(imports, "apply_full_invoice", boom)
    notes = imports.apply_pending(db)
    assert any("Chưa nhập tự động được hoá đơn 05.10.2026" in n for n in notes)
    assert not db.scalars(select(Supplier).where(Supplier.name == "half-written")).all()  # đã rollback
    assert not alerts.get_setting(db, "import_" + metro2.KEY)  # lần sau thử lại
    assert alerts.get_setting(db, "import_" + metro.KEY)  # hoá đơn khác vẫn nhập bình thường


def test_metro2_item_names():
    names = {spec[0] for spec in metro2.ITEMS.values()}
    assert "Nước khoáng không ga / ViO Still 0,5l" in names  # "0,50 DPG FL VIO" là nước không ga
    assert not any("gesalzen" in n or "Medium" in n for n in names)
