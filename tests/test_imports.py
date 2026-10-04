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
