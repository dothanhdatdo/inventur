"""Đọc hoá đơn PDF (METRO) không cần AI."""
import asyncio
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import imports
from app.db import Ingredient, Invoice
from app.imports import metro_20260919 as metro
from app.services import matching, ocr, pdf_invoice

FIXTURE = (Path(__file__).parent / "fixtures" / "metro_invoice.txt").read_text(encoding="utf-8")


def test_parse_metro_invoice_matches_totals():
    p = pdf_invoice.parse_metro(FIXTURE)
    assert p.supplier == "METRO Gundelfingen"
    assert p.invoice_number == "19.09.2026/071/0/0/0501/048886" and p.invoice_date == date(2026, 9, 19)
    assert len(p.lines) == 52
    assert p.subtotal == 1595.53  # NETTO-WARENWERT 1.721,77 - Leergut 126,24
    assert p.vat == 249.95 and p.deposit == 126.24
    by_name = {line.name: line for line in p.lines}
    coke = by_name["0,50 DPG PET COCA-COLA"]
    assert coke.quantity == 11 and coke.units_per_pack == 12 and coke.total == 91.08 and coke.vat_rate == 19
    assert by_name["0,33 MW ROTHAUS TANNENZAEPFLE"].total == 29.98  # không giảm giá -> giữ thành tiền
    assert by_name["10l ARO FRITTIEROEL"].total == 59.96  # 67,96 - Mengenrabatt 8,00
    assert by_name["1kg VENUSMUSCHELFLEISCH GEKOCH"].expiry == date(2028, 2, 28)
    belly = [line for line in p.lines if line.name.startswith("REGIO SW-BAUCH")]
    assert [b.units_per_pack for b in belly] == [5.406, 4.282] and belly[0].unit == "KG"
    assert by_name["0,33 MW ROTHAUS TANNENZAEPFLE"].category == "Đồ uống / Bier & AfG"
    assert by_name["REGIO SW-KLEINFLEISCH"].category == "Thịt & hải sản"
    assert not any("LEERGUT" in line.name for line in p.lines)


def test_non_metro_text_is_not_parsed():
    assert pdf_invoice.parse_metro("Rechnung Großmarkt XY\n1 Stk Reis 10,00") is None
    assert pdf_invoice.parse_pdf(b"%PDF-1.4 not really") is None


def test_pack_factor_with_hint():
    chai = Ingredient(name="Coca-Cola 0,5l", unit="chai")
    liters = Ingredient(name="Coca-Cola (lít)", unit="l")
    kg = Ingredient(name="Ba chỉ heo", unit="kg")
    oil = Ingredient(name="Dầu chiên", unit="l")
    assert matching.pack_factor_with_hint("0,50 DPG PET COCA-COLA", "SP", chai, 12) == 12
    assert matching.pack_factor_with_hint("0,50 DPG PET COCA-COLA", "SP", liters, 12) == 6
    assert matching.pack_factor_with_hint("0,75l BIO THOLOMIES CHARDONNAY", "KT", liters, 6) == 4.5
    assert matching.pack_factor_with_hint("REGIO SW-BAUCH E 20/50 GEZ.", "KG", kg, 5.406) == 5.406
    assert matching.pack_factor_with_hint("10l ARO FRITTIEROEL", "KS", oil, 1) == 10  # hint 1 -> đoán từ tên


def test_auto_without_key_does_not_return_fake_data():
    settings = {"provider": "auto", "anthropic_key": "", "anthropic_model": "", "openai_key": "", "openai_model": "",
                "gemini_key": "", "gemini_model": "gemini-flash-latest"}
    assert ocr.active_provider(settings) == "local"
    with pytest.raises(ocr.OcrError, match="aistudio.google.com"):
        asyncio.run(ocr.extract_invoice(b"\xff\xd8\xff", "image/jpeg", settings))
    with pytest.raises(ocr.OcrError, match="METRO"):
        asyncio.run(ocr.extract_invoice(b"%PDF-1.4", "application/pdf", settings))


def test_pdf_is_read_locally_even_with_ai_key(monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)

    async def fail(*args, **kwargs):  # AI không được gọi cho PDF METRO
        raise AssertionError("AI called")

    monkeypatch.setattr(ocr, "_post_json", fail)
    settings = {"provider": "gemini", "anthropic_key": "", "anthropic_model": "", "openai_key": "", "openai_model": "",
                "gemini_key": "AIza-x", "gemini_model": "gemini-flash-latest"}
    parsed = asyncio.run(ocr.extract_invoice(b"%PDF", "application/pdf", settings))
    assert parsed.source == "pdf" and len(parsed.lines) == 52


def test_photo_without_key_keeps_file_for_manual_entry(client, db):
    client.post("/settings/ocr", data={"ocr_provider": "auto"})  # mặc định: tự động, chưa có key
    assert "Ảnh: cần key AI" in client.get("/invoices/scan").text
    r = client.post("/invoices/scan", files={"file": ("hd.jpg", b"\xff\xd8\xff" + b"0" * 20, "image/jpeg")},
                    follow_redirects=True)
    error = r.text.split("flash-error")[1].split("</div>")[0]
    assert "aistudio.google.com/apikey" in error and ".)." not in error and ".." not in error
    assert "gõ tay các dòng" in error and "Chế độ DEMO" not in r.text
    invoice = db.scalars(select(Invoice)).one()
    assert invoice.source == "manual" and invoice.lines == [] and invoice.file_path


def _scan(client):
    return client.post("/invoices/scan", files={"file": ("rechnung.pdf", b"%PDF-1.4 x", "application/pdf")},
                       follow_redirects=True)


def test_scan_metro_pdf_after_import_flags_duplicates(client, db, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    imports.apply_invoice_import(db, metro)  # hoá đơn này đã được nhập (đồ uống) từ email
    r = _scan(client)
    assert "Đã đọc 52 dòng từ PDF (không dùng AI)" in r.text and "Tiền cọc vỏ 126,24 €" in r.text
    assert "đã được nhập kho trước đó" in r.text and "Đã nhập rồi" in r.text
    db.expire_all()
    draft = db.scalars(select(Invoice).where(Invoice.status == "draft")).one()
    assert draft.source == "pdf" and draft.supplier.name == "METRO Gundelfingen"
    flagged = [line for line in draft.lines if line.match_score < 0]
    assert len(flagged) == 24  # đúng các dòng đồ uống đã nhập; không dòng nào được gắn để nhập lại
    assert all(line.ingredient_id is None for line in flagged)
    kitchen = [line for line in draft.lines if line.match_score >= 0]
    assert len(kitchen) == 28
    assert draft.subtotal == round(1595.53 - 891.03, 2)  # chỉ còn phần chưa nhập (đồ uống 891,03 € đã nhập)
    assert draft.vat == round(sum(line.line_total * line.vat_rate / 100 for line in kitchen), 2)
    page = client.get(f"/invoices/{draft.id}").text
    assert page.count('class="del-box" checked') == 24  # dòng trùng được bỏ sẵn
    assert "Chưa gắn: 704,50 €" in page  # tiền các dòng trùng không tính vào phần chưa gắn
    # Xác nhận: chỉ các dòng mới được giữ, đồ uống không bị cộng kho lần hai.
    from app.services import stock

    before = stock.stock_map(db)
    form = {"supplier_id": str(draft.supplier_id), "vat": str(draft.vat), "action": "confirm", "row": []}
    for i, line in enumerate(draft.lines):
        form["row"].append(str(i))
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": str(line.pack_factor),
                     f"ingredient_id_{i}": ""})
        if line.match_score < 0:
            form[f"delete_{i}"] = "1"
    client.post(f"/invoices/{draft.id}", data=form, follow_redirects=True)
    db.expire_all()
    assert stock.stock_map(db) == before
    assert len(db.get(Invoice, draft.id).lines) == 28


def test_scan_metro_pdf_new_items_get_units_areas_and_aliases(client, db, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    _scan(client)
    db.expire_all()
    draft = db.scalars(select(Invoice).where(Invoice.status == "draft")).one()
    lines = {line.raw_name: line for line in draft.lines}
    picks = ["0,50 DPG PET COCA-COLA", "REGIO SW-KLEINFLEISCH", "MPRO BRATENWENDER 15X8cm", "1kg VENUSMUSCHELFLEISCH GEKOCH",
             "10l ARO FRITTIEROEL", "10x80g CREME BRULEE TARTLETS", "TH SCHWINGDECKELEIMER 25L", "REGIO SW-BAUCH E 20/50 GEZ."]
    form = {"supplier_id": str(draft.supplier_id), "vat": "0", "action": "confirm"}
    rows = []
    for i, name in enumerate(picks):
        line = lines[name]
        rows.append(str(i))
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": str(line.pack_factor),
                     f"expiry_date_{i}": line.expiry_date.isoformat() if line.expiry_date else "",
                     f"ingredient_id_{i}": "new"})
    r = client.post(f"/invoices/{draft.id}", data={**form, "row": rows}, follow_redirects=True)
    assert "Đã nhập kho 8 mặt hàng" in r.text
    db.expire_all()
    made = {i.name: i for i in db.scalars(select(Ingredient)).all()}
    coke = made["0,50 DPG PET COCA-COLA"]
    assert coke.area == "bar" and coke.unit == "chai" and coke.pack_unit == "lốc" and coke.pack_size == 12
    from app.services import stock

    assert stock.stock_of(db, coke.id) == 132
    meat = made["REGIO SW-KLEINFLEISCH"]
    assert meat.area == "kitchen" and meat.unit == "kg" and abs(stock.stock_of(db, meat.id) - 2.28) < 1e-9
    assert made["MPRO BRATENWENDER 15X8cm"].area == "kitchen"  # nhóm Nonfood -> bếp dù VAT 19 %
    clam = made["1kg VENUSMUSCHELFLEISCH GEKOCH"]
    from app.db import Batch

    assert db.scalars(select(Batch).where(Batch.ingredient_id == clam.id)).one().expiry_date == date(2028, 2, 28)
    assert clam.unit == "kg" and stock.stock_of(db, clam.id) == 1.0  # "1kg ..." -> kho tính kg cho định lượng
    oil = made["10l ARO FRITTIEROEL"]
    assert oil.unit == "l" and oil.area == "kitchen" and stock.stock_of(db, oil.id) == lines["10l ARO FRITTIEROEL"].quantity * 10
    tart = made["10x80g CREME BRULEE TARTLETS"]
    assert tart.unit == "cái" and tart.area == "kitchen"
    bin_ = made["TH SCHWINGDECKELEIMER 25L"]
    assert bin_.unit == "cái" and bin_.area == "kitchen"  # thùng rác 25 L không phải "chai"
    belly = made["REGIO SW-BAUCH E 20/50 GEZ."]
    assert belly.unit == "kg"
    # Hàng cân: lần sau dùng đúng số kg trên hoá đơn mới, không dùng lại hệ số cũ.
    m = matching.match_line(db, coke.supplier_id, "REGIO SW-BAUCH E 20/50 GEZ.", "KG", units_per_pack=4.282)
    assert m.from_alias and m.pack_factor == 4.282
    # Lần sau quét hoá đơn METRO: tự khớp theo tên đã nhớ.
    m = matching.match_line(db, coke.supplier_id, "0,50 DPG PET COCA-COLA", "SP", units_per_pack=12)
    assert m.from_alias and m.ingredient.id == coke.id and m.pack_factor == 12
