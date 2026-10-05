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
    # Hàng cân: số lượng là số kg thật, đơn giá là €/kg.
    assert [b.quantity for b in belly] == [5.406, 4.282] and belly[0].unit == "KG" and belly[0].units_per_pack == 1
    assert abs(belly[0].unit_price - 5.49) < 0.01 and belly[0].total == 29.68
    assert p.warnings == [] and p.raw["net_printed"] == 1721.77
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
    grams = Ingredient(name="Ba chỉ heo (g)", unit="g")
    pieces = Ingredient(name="Bánh tart", unit="cái")
    beans = Ingredient(name="Đậu que", unit="kg")
    assert matching.pack_factor_with_hint("REGIO SW-BAUCH E 20/50 GEZ.", "KG", kg, 1) == 1  # số lượng đã là kg
    assert matching.pack_factor_with_hint("REGIO SW-BAUCH E 20/50 GEZ.", "KG", grams, 1) == 1000
    assert matching.pack_factor_with_hint("10l ARO FRITTIEROEL", "KS", oil, 1) == 10
    assert matching.pack_factor_with_hint("2,5kg MC PRINZESSBOHNEN EXTR F", "KT", beans, 4) == 10
    assert matching.pack_factor_with_hint("10x80g CREME BRULEE TARTLETS", "PG", pieces, 1) == 10
    assert matching.pack_factor_with_hint("1000x3,5g RIOBA ZUCKERSTICKS", "PG", kg, 1) == 3.5
    assert matching.pack_factor_with_hint("MPRO 12ER BILLY AUSGIESSER", "PG", pieces, 1) == 12


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


def _draft(db, invoice_id=None):
    db.expire_all()
    if invoice_id:
        return db.get(Invoice, invoice_id)
    return db.scalars(select(Invoice).where(Invoice.status == "draft").order_by(Invoice.id.desc())).first()


def _post(client, draft, choices, action="confirm", keep_unlisted=True, factors=None):
    """Gửi form đối soát như trình duyệt: choices = {tên trên HĐ: "new" | id nguyên liệu}; dòng trùng được "Bỏ"."""
    form = {"supplier_id": str(draft.supplier_id or ""), "invoice_number": draft.invoice_number,
            "vat": str(draft.vat), "action": action, "row": []}
    for i, line in enumerate(draft.lines):
        if not keep_unlisted and line.raw_name not in choices:
            continue
        form["row"].append(str(i))
        choice = choices.get(line.raw_name, str(line.ingredient_id or ""))
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total),
                     f"pack_factor_{i}": str((factors or {}).get(line.raw_name, line.pack_factor)),
                     f"expiry_date_{i}": line.expiry_date.isoformat() if line.expiry_date else "",
                     f"ingredient_id_{i}": str(choice)})
        if line.match_score < 0 and not line.ingredient_id and line.raw_name not in choices:
            form[f"delete_{i}"] = "1"
    return client.post(f"/invoices/{draft.id}", data=form, follow_redirects=True)


def test_scan_metro_pdf_after_import_flags_duplicates(client, db, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    imports.apply_invoice_import(db, metro)  # hoá đơn này đã được nhập (đồ uống) từ email
    r = _scan(client)
    assert "Đã đọc 52 dòng từ PDF (không dùng AI)" in r.text and "Tiền cọc vỏ 126,24 €" in r.text
    assert "đã được nhập kho trước đó" in r.text and "Đã nhập rồi" in r.text
    draft = _draft(db)
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
    assert 'data-new-unit="kg"' in page and 'data-spec="1"' in page
    # Xác nhận: chỉ các dòng mới được giữ, đồ uống không bị cộng kho lần hai.
    from app.services import stock

    _post(client, draft, {}, action="save")
    assert len(_draft(db, draft.id).lines) == 28
    # Nhập vài hàng bếp; hàng không gắn (vd. bếp từ) vẫn được tính tiền vì lần trước chưa có.
    r = _post(client, _draft(db, draft.id), {"10l ARO FRITTIEROEL": "new", "REGIO SW-KLEINFLEISCH": "new"})
    assert "Đã nhập kho 2 mặt hàng" in r.text
    draft = _draft(db, draft.id)
    assert draft.subtotal == 704.5 and all(line.match_score >= 0 for line in draft.lines)
    both = db.scalars(select(Invoice).where(Invoice.invoice_number == draft.invoice_number)).all()
    assert round(sum(i.subtotal for i in both), 2) == 1595.53


def test_scan_metro_pdf_new_items_get_units_areas_and_aliases(client, db, monkeypatch):
    from app.db import Batch
    from app.services import stock

    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    _scan(client)
    draft = _draft(db)
    r = _post(client, draft, {line.raw_name: "new" for line in draft.lines}, keep_unlisted=False)
    assert "Đã nhập kho 52 mặt hàng" in r.text and "Nguyên liệu mới (" in r.text and "… (+" in r.text
    assert len(r.headers.get("set-cookie", "")) < 3000  # thông báo được rút gọn, cookie không quá 4 KB
    db.expire_all()
    made = {i.name: i for i in db.scalars(select(Ingredient)).all()}
    coke = made["0,50 DPG PET COCA-COLA"]
    assert coke.area == "bar" and coke.unit == "chai" and coke.pack_unit == "lốc" and coke.pack_size == 12
    assert stock.stock_of(db, coke.id) == 132
    meat = made["REGIO SW-KLEINFLEISCH"]
    assert meat.area == "kitchen" and meat.unit == "kg" and abs(stock.stock_of(db, meat.id) - 2.28) < 1e-9
    assert abs(meat.last_price - 1.29) < 0.01
    belly = made["REGIO SW-BAUCH E 20/50 GEZ."]  # hai dòng cùng tên -> cùng một nguyên liệu, đủ cả hai cân
    assert belly.unit == "kg" and abs(stock.stock_of(db, belly.id) - 9.688) < 1e-9
    assert made["MPRO BRATENWENDER 15X8cm"].area == "kitchen"  # nhóm Nonfood -> bếp dù VAT 19 %
    clam = made["1kg VENUSMUSCHELFLEISCH GEKOCH"]
    assert db.scalars(select(Batch).where(Batch.ingredient_id == clam.id)).one().expiry_date == date(2028, 2, 28)
    assert clam.unit == "kg" and stock.stock_of(db, clam.id) == 1.0  # "1kg ..." -> kho tính kg cho định lượng
    oil = made["10l ARO FRITTIEROEL"]
    assert oil.unit == "l" and oil.area == "kitchen" and stock.stock_of(db, oil.id) == 40
    beans = made["2,5kg MC PRINZESSBOHNEN EXTR F"]
    assert beans.unit == "kg" and stock.stock_of(db, beans.id) == 50 and abs(beans.last_price - 1.636) < 1e-3
    tart = made["10x80g CREME BRULEE TARTLETS"]
    assert tart.unit == "cái" and tart.pack_size == 10 and stock.stock_of(db, tart.id) == 20
    sticks = made["1000x3,5g RIOBA ZUCKERSTICKS"]
    assert sticks.unit == "cái" and stock.stock_of(db, sticks.id) == 1000
    bin_ = made["TH SCHWINGDECKELEIMER 25L"]
    assert bin_.unit == "cái" and bin_.area == "kitchen"  # thùng rác 25 L không phải "chai"
    wine = made["0,75l BIO THOLOMIES CHARDONNAY"]  # METRO ghi hai dòng cho cùng hàng
    assert wine.unit == "chai" and wine.area == "bar"
    wine_lines = [line for line in draft.lines if line.raw_name == wine.name]
    assert stock.stock_of(db, wine.id) == sum(line.quantity * line.units_per_pack for line in wine_lines)
    # Lần sau quét hoá đơn METRO: tự khớp theo tên đã nhớ, hàng cân lấy đúng số kg mới.
    m = matching.match_line(db, coke.supplier_id, "0,50 DPG PET COCA-COLA", "SP", units_per_pack=12)
    assert m.from_alias and m.ingredient.id == coke.id and m.pack_factor == 12
    m = matching.match_line(db, coke.supplier_id, "REGIO SW-BAUCH E 20/50 GEZ.", "KG", units_per_pack=1)
    assert m.from_alias and m.pack_factor == 1


def test_link_weighed_and_sized_lines_to_existing_ingredients(client, db, monkeypatch):
    """Lần đầu: gắn tay từng dòng METRO vào nguyên liệu tiếng Việt có sẵn."""
    from app.services import stock

    pork = Ingredient(name="Thịt ba chỉ", unit="kg", category="Thịt & hải sản")
    beans = Ingredient(name="Đậu que đông lạnh", unit="kg", category="Đồ đông lạnh")
    oil = Ingredient(name="Dầu chiên", unit="l", category="Hàng khô")
    db.add_all([pork, beans, oil])
    db.commit()
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    _scan(client)
    draft = _draft(db)
    # app.js tính quy đổi theo cùng quy tắc factor_for; ở đây gửi giá trị server tính cho từng nguyên liệu.
    spec = {line.raw_name: matching.line_spec(line.raw_name, line.unit_raw, line.units_per_pack) for line in draft.lines}
    picks = {"REGIO SW-BAUCH E 20/50 GEZ.": pork, "2,5kg MC PRINZESSBOHNEN EXTR F": beans, "10l ARO FRITTIEROEL": oil}
    factors = {name: matching.factor_for(spec[name], "KG" if ing is pork else "x", ing) for name, ing in picks.items()}
    assert factors == {"REGIO SW-BAUCH E 20/50 GEZ.": 1.0, "2,5kg MC PRINZESSBOHNEN EXTR F": 10.0, "10l ARO FRITTIEROEL": 10.0}
    _post(client, draft, {name: ing.id for name, ing in picks.items()}, keep_unlisted=False, factors=factors)
    db.expire_all()
    assert abs(stock.stock_of(db, pork.id) - 9.688) < 1e-9  # 5,406 + 4,282 kg, không phải 2 kg
    assert abs(db.get(Ingredient, pork.id).last_price - 5.49) < 0.01
    assert stock.stock_of(db, beans.id) == 50 and stock.stock_of(db, oil.id) == 40


def test_same_pdf_in_two_drafts_is_imported_once(client, db, monkeypatch):
    from app.services import stock

    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    _scan(client)
    first = _draft(db)
    r = _scan(client)
    second = _draft(db)
    assert second.id != first.id and f"Đã có phiếu nháp #{first.id}" in r.text
    names = ["1kg SZ ROHRZUCKER", "0,50 DPG PET COCA-COLA", "REGIO SW-BAUCH E 20/50 GEZ."]
    assert "Đã nhập kho 4 mặt hàng" in _post(client, first, {n: "new" for n in names}, keep_unlisted=True).text
    db.expire_all()
    stock_after_first = stock.stock_map(db)
    sugar = db.scalars(select(Ingredient).where(Ingredient.name == "1kg SZ ROHRZUCKER")).one()
    # Phiếu thứ hai: chọn lại các dòng đó (nguyên liệu đã có) + thêm 1 dòng mới.
    second = _draft(db, second.id)
    choices = {n: db.scalars(select(Ingredient).where(Ingredient.name == n)).one().id for n in names}
    choices["10l ARO FRITTIEROEL"] = "new"
    r = _post(client, second, choices)
    assert "Đã nhập kho 1 mặt hàng" in r.text and "Bỏ qua 4 dòng đã nhập kho ở hoá đơn" in r.text
    db.expire_all()
    stock_now = stock.stock_map(db)
    assert stock_now[sugar.id] == stock_after_first[sugar.id]  # không bị cộng hai lần
    oil = db.scalars(select(Ingredient).where(Ingredient.name == "10l ARO FRITTIEROEL")).one()
    assert stock_now[oil.id] == 40
    # Tiền không bị tính hai lần: tổng hai phiếu = tổng hoá đơn.
    both = db.scalars(select(Invoice).where(Invoice.invoice_number == first.invoice_number)).all()
    assert round(sum(i.subtotal for i in both), 2) == 1595.53
    # Quét lần ba: các dòng đã nhập được đánh dấu sẵn; bấm Nhập kho như trang hiển thị -> không nhập gì thêm.
    _scan(client)
    third = _draft(db)
    assert {line.raw_name for line in third.lines if line.match_score < 0} == set(names) | {"10l ARO FRITTIEROEL"}
    suggestions = {line.raw_name: "" for line in third.lines if line.ingredient_id}  # gợi ý mờ cho hàng khác -> bỏ
    r = _post(client, third, suggestions)
    assert "Chưa có dòng nào được gắn" in r.text
    db.expire_all()
    assert stock.stock_map(db) == stock_now


def test_failed_confirm_changes_nothing(client, db, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    _scan(client)
    first = _draft(db)
    _scan(client)
    second = _draft(db)
    _post(client, first, {"1kg SZ ROHRZUCKER": "new"})
    sugar = db.scalars(select(Ingredient).where(Ingredient.name == "1kg SZ ROHRZUCKER")).one()
    second = _draft(db, second.id)
    subtotal = second.subtotal
    r = _post(client, second, {"1kg SZ ROHRZUCKER": sugar.id})
    assert f"đều đã được nhập kho ở hoá đơn #{first.id}" in r.text
    second = _draft(db, second.id)
    assert second.status == "draft" and second.subtotal == subtotal
    assert all(line.match_score >= 0 for line in second.lines)  # không có cờ "Đã nhập rồi" sai


def test_identical_rows_missed_first_time_can_be_added_by_rescanning(client, db, monkeypatch):
    """METRO in hai dòng giống hệt (2 thùng Chardonnay). Lần đầu chỉ gắn một dòng, quét lại để nhập dòng còn lại."""
    from app.services import stock

    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    wine_name = "0,75l BIO THOLOMIES CHARDONNAY"
    wine = Ingredient(name="Chardonnay Tholomies 0,75l", unit="chai", area="bar")
    db.add(wine)
    db.commit()
    _scan(client)
    day1 = _draft(db)
    form = {"supplier_id": str(day1.supplier_id), "invoice_number": day1.invoice_number, "vat": str(day1.vat),
            "action": "confirm", "row": []}
    linked_once = False
    for i, line in enumerate(day1.lines):
        form["row"].append(str(i))
        choice = ""
        if line.raw_name == wine_name and not linked_once:
            choice, linked_once = str(wine.id), True
        form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                     f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                     f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": "6" if choice else str(line.pack_factor),
                     f"ingredient_id_{i}": choice})
    client.post(f"/invoices/{day1.id}", data=form, follow_redirects=True)
    db.expire_all()
    assert stock.stock_of(db, wine.id) == 6
    # Hôm sau quét lại: dòng đã nhập được đánh dấu, dòng còn lại tự khớp (đã nhớ) -> nhập được.
    _scan(client)
    day2 = _draft(db)
    wine_rows = [line for line in day2.lines if line.raw_name == wine_name]
    assert sorted(line.match_score for line in wine_rows) == [-1.0, 1.0]
    r = _post(client, day2, {})
    assert "Đã nhập kho 1 mặt hàng" in r.text
    db.expire_all()
    assert stock.stock_of(db, wine.id) == 12
    both = db.scalars(select(Invoice).where(Invoice.invoice_number == day1.invoice_number)).all()
    assert round(sum(i.subtotal for i in both), 2) == 1595.53


def test_kept_duplicate_line_is_not_reticked_after_saving(client, db, monkeypatch):
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    imports.apply_invoice_import(db, metro)
    _scan(client)
    draft = _draft(db)
    line = next(line for line in draft.lines if line.match_score < 0)
    ing = db.scalars(select(Ingredient)).first()
    _post(client, draft, {line.raw_name: ing.id}, action="save")
    db.expire_all()
    kept = db.get(type(line), line.id)
    assert kept.match_score == -3 and kept.ingredient_id == ing.id
    page = client.get(f"/invoices/{draft.id}").text
    assert 'class="del-box" checked' not in page and "Đã nhập rồi" not in page
    # Người dùng chủ động giữ dòng -> Nhập kho tôn trọng lựa chọn đó.
    from app.services import stock

    before = stock.stock_of(db, ing.id)
    r = _post(client, _draft(db, draft.id), {line.raw_name: ing.id})
    assert "Đã nhập kho" in r.text
    db.expire_all()
    assert stock.stock_of(db, ing.id) > before


def test_variant_names_are_not_auto_matched():
    assert matching.similarity("0,50 DPG PET COCA-COLA ZERO", "0,50 DPG PET COCA-COLA") < matching.MATCH_THRESHOLD
    assert matching.similarity("Coca-Cola Zero 0,33l", "Coca-Cola Zero / Cola Zero") >= matching.MATCH_THRESHOLD
    assert matching.similarity("Radler alkoholfrei 0,5l", "Radler 0,5l") < matching.MATCH_THRESHOLD


def test_total_mismatch_is_reported(client, db, monkeypatch):
    broken = "\n".join(line for line in FIXTURE.splitlines() if "REGIO SW-KLEINFLEISCH" not in line)
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: broken)
    r = _scan(client)
    assert "khác NETTO-WARENWERT trên hoá đơn (1.721,77 €)" in r.text


def test_settings_auto_without_key_does_not_claim_scanning_fails(client, db):
    r = client.post("/settings/ocr", data={"ocr_provider": "auto"}, follow_redirects=True)
    assert "PDF của METRO đọc được ngay" in r.text and "quét hoá đơn sẽ báo lỗi" not in r.text
    assert "(thiếu key)" not in r.text


def test_manual_row_new_item_uses_size_in_name(client, db):
    from app.services import stock

    r = client.post("/invoices/manual", follow_redirects=True)
    invoice = _draft(db)
    form = {"supplier_id": "", "invoice_number": "", "vat": "0", "action": "confirm", "row": ["0"],
            "line_id_0": "", "raw_name_0": "10l Frittieröl", "quantity_0": "4", "unit_raw_0": "Kanister",
            "unit_price_0": "14,99", "line_total_0": "59,96", "pack_factor_0": "1", "ingredient_id_0": "new"}
    client.post(f"/invoices/{invoice.id}", data=form, follow_redirects=True)
    db.expire_all()
    oil = db.scalars(select(Ingredient).where(Ingredient.name == "10l Frittieröl")).one()
    assert oil.unit == "l" and stock.stock_of(db, oil.id) == 40 and abs(oil.last_price - 1.499) < 1e-3


def test_vintage_year_is_not_a_pack_count():
    spec = matching.line_spec("2022er Jechtinger Eichert Spätburgunder trocken 0,75l", "Fl")
    assert spec.pieces == 1 and spec.kind == "l" and spec.content == 0.75
    assert matching.line_spec("0,75l 2022ER OBERKIRCHER", "KT", 6).pieces == 6
    assert matching.line_spec("6er 0,33l Bier", "KA", 4).pieces == 24
    chai = Ingredient(name="Spätburgunder", unit="chai")
    assert matching.guess_pack_factor("2021er Ihringer Grauburgunder 0,75l", "Fl", chai) == 1


def test_same_unit_on_invoice_and_in_stock_means_factor_one():
    karton = Ingredient(name="Coca-Cola Dose (Karton)", unit="Karton")
    spec = matching.line_spec("Coca-Cola 24x0,33l Dose", "Karton")
    assert matching.factor_for(spec, "Karton", karton) == 1
    sack = Ingredient(name="Gạo (bao)", unit="Sack", pack_unit="sack", pack_size=18)
    assert matching.factor_for(matching.line_spec("Thai Jasminreis 18kg Sack", "Sack"), "Sack", sack) == 1


def test_new_item_plans_for_asian_kitchen_goods():
    from app.services.invoices import new_item_plan

    rice = new_item_plan("Thai Jasminreis Duftreis 18kg Sack", "Sack", None, "", 7)
    assert (rice.unit, rice.factor, rice.area) == ("kg", 18, "kitchen")
    paper = new_item_plan("500g REISPAPIER 22CM", "ST", 1, "Hàng khô", 7)
    assert (paper.unit, paper.factor) == ("kg", 0.5)
    rolls = new_item_plan("1,5kg FRUEHLINGSROLLEN GEMUESE", "ST", 1, "Đồ đông lạnh", 7)
    assert (rolls.unit, rolls.factor) == ("kg", 1.5)
    assert new_item_plan("Müllsäcke 120l", "ST", 1, "", 19).unit == "cái"
    for name, category in (("120X10g KAFFEESAHNE 10%", "Đồ mát / Mopro"), ("1000x3,5g RIOBA ZUCKERSTICKS", "Hàng khô"),
                           ("MPRO 12ER BILLY AUSGIESSER", "Dụng cụ / Nonfood")):
        assert new_item_plan(name, "PG", 1, category, 7).area == "bar", name


def test_rescan_with_renamed_supplier_is_still_recognised(client, db, monkeypatch):
    """Đã đổi tên NCC thành "METRO" -> quét lại PDF (NCC "METRO Gundelfingen" không tìm thấy) vẫn nhận ra hoá đơn đã nhập."""
    from app.db import Supplier
    from app.services import stock

    imports.apply_invoice_import(db, metro)
    supplier = db.scalars(select(Supplier).where(Supplier.name == "METRO Gundelfingen")).one()
    supplier.name = "METRO"
    db.commit()
    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    before = stock.stock_map(db)
    r = _scan(client)
    draft = _draft(db)
    assert draft.supplier_id is None and "24 dòng trùng" in r.text
    assert sum(1 for line in draft.lines if line.match_score < 0) == 24
    # Dù người dùng gắn lại một dòng đồ uống, Nhập kho vẫn chặn (không ghi đè): chỉ khi bỏ dấu "Bỏ" + lưu nháp mới nhập lại.
    draft.lines[0].ingredient_id = None
    r = _post(client, draft, {"10l ARO FRITTIEROEL": "new"}, keep_unlisted=True)
    db.expire_all()
    after = stock.stock_map(db)
    assert all(after[k] == v for k, v in before.items())


def test_identical_rows_in_two_drafts(client, db, monkeypatch):
    from app.services import stock

    monkeypatch.setattr(pdf_invoice, "extract_text", lambda content: FIXTURE)
    wine_name = "0,75l BIO THOLOMIES CHARDONNAY"
    wine = Ingredient(name="Chardonnay Tholomies 0,75l", unit="chai", area="bar")
    db.add(wine)
    db.commit()
    _scan(client)
    first = _draft(db)
    _scan(client)
    second = _draft(db)

    def link_one(draft, which):
        rows = [line for line in draft.lines if line.raw_name == wine_name]
        form = {"supplier_id": "", "invoice_number": draft.invoice_number, "vat": str(draft.vat), "action": "confirm", "row": []}
        for i, line in enumerate(draft.lines):
            form["row"].append(str(i))
            choice = str(wine.id) if line is rows[which] else ""
            form.update({f"line_id_{i}": str(line.id), f"raw_name_{i}": line.raw_name, f"quantity_{i}": str(line.quantity),
                         f"unit_raw_{i}": line.unit_raw, f"unit_price_{i}": str(line.unit_price),
                         f"line_total_{i}": str(line.line_total), f"pack_factor_{i}": "6" if choice else "1",
                         f"ingredient_id_{i}": choice})
        return client.post(f"/invoices/{draft.id}", data=form, follow_redirects=True)

    link_one(first, 0)
    r = link_one(_draft(db, second.id), 1)  # dòng thứ hai (thùng thứ hai) ở phiếu kia
    assert "Đã nhập kho 1 mặt hàng" in r.text and "đã nhập kho ở hoá đơn" not in r.text
    db.expire_all()
    assert stock.stock_of(db, wine.id) == 12
    _scan(client)  # quét lần ba: cả hai dòng rượu đã được đánh dấu "Đã nhập rồi"
    third = _draft(db)
    assert [line.match_score for line in third.lines if line.raw_name == wine_name] == [-1.0, -1.0]
    r = _post(client, third, {line.raw_name: "" for line in third.lines if line.ingredient_id})
    assert "Chưa có dòng nào được gắn" in r.text
    db.expire_all()
    assert stock.stock_of(db, wine.id) == 12
    both = db.scalars(select(Invoice).where(Invoice.invoice_number == first.invoice_number, Invoice.status == "confirmed")).all()
    assert round(sum(i.subtotal for i in both), 2) == 1595.53


def test_variant_and_colour_words():
    carrot = "Cà rốt / Karotten"
    assert matching.similarity("Karotten 10kg Sack", carrot) >= matching.MATCH_THRESHOLD
    assert matching.similarity("Paprika rot 5kg", "Ớt chuông / Paprika") > matching.similarity("Paprika rot 5kg", carrot)
    assert matching.similarity("1L BERIEF BIO HAFER OHNE ZUCKE", "Sữa yến mạch / Berief Bio Haferdrink 1l") >= matching.MATCH_THRESHOLD
    assert matching.similarity("Sesam weiß 1kg", "Mè trắng / Sesam") >= matching.MATCH_THRESHOLD


def test_packaging_names_stay_nonfood():
    from app.areas import guess_area, looks_like_nonfood

    for name in ("Menü-Box 3-geteilt 200 Stk", "Alu-Folie 45cm 150m", "Gefrier-Beutel 3l 100 Stk", "Müll-Sack 120l",
                 "Bon-Rolle 80mm 20 Stk", "Snack Box 500 Stk", "Dessert Schale 200ml"):
        assert looks_like_nonfood(name) and guess_area(name, "", 19) == "kitchen", name
    for name in ("Kartoffeln 25kg Sack", "Thai Jasminreis 18kg Sack", "500g REISPAPIER 22CM", "Reis im Kochbeutel 4x125g"):
        assert not looks_like_nonfood(name), name
