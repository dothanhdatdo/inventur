"""Dữ liệu mẫu để dùng thử (chỉ chạy khi database trống)."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .areas import AREAS, BAR, KITCHEN
from .db import Dish, Ingredient, Invoice, InvoiceLine, RecipeItem, Supplier
from .services import alerts, invoices, stock

SUPPLIERS = [
    ("Großmarkt Breisgau", "+49 761 000000", "bestellung@grossmarkt.example", "Thịt, rau tươi, đồ uống"),
    ("Asia Großhandel Süd", "+49 721 000000", "order@asia-sued.example", "Hàng khô châu Á"),
    ("Frischemarkt Müller", "+49 761 111111", "", "Rau thơm, giá đỗ – giao mỗi sáng"),
]

# key, tên, nhóm, đơn vị, đơn vị mua, quy đổi, tồn tối thiểu, giá/đơn vị, NCC,
# (tên trên HĐ, đơn vị HĐ, hệ số), số lượng HĐ mỗi tuần, hạn dùng (ngày)
INGREDIENTS = [
    ("rice", "Gạo Jasmin / Jasminreis", "Hàng khô", "kg", "bao", 18, 20, 1.75, 1, ("Thai Jasminreis Duftreis 18kg Sack", "Sack", 18), 2, None),
    ("pho", "Bánh phở / Reisbandnudeln", "Hàng khô", "kg", "gói", 0.4, 4, 4.40, 1, ("Reisbandnudeln 5mm 400g", "Stk", 0.4), 30, None),
    ("bun", "Bún / Reisnudeln Vermicelli", "Hàng khô", "kg", "gói", 0.4, 2, 4.20, 1, ("Reisnudeln Vermicelli 400g", "Stk", 0.4), 10, None),
    ("paper", "Bánh tráng / Reispapier", "Hàng khô", "gói", "", 1, 8, 1.60, 1, ("Reispapier 22cm 500g", "Pck", 1), 12, None),
    ("beef", "Thịt bò / Rinderhüfte", "Thịt & hải sản", "kg", "", 1, 4, 18.20, 0, ("Rinderhüfte frisch DE", "kg", 1), 12, 6),
    ("chicken", "Ức gà / Hähnchenbrustfilet", "Thịt & hải sản", "kg", "", 1, 8, 8.80, 0, ("Hähnchenbrustfilet frisch", "kg", 1), 18, 5),
    ("duck", "Vịt / Ente knusprig", "Thịt & hải sản", "kg", "", 1, 4, 11.50, 1, ("Ente knusprig TK 1kg", "Stk", 1), 12, 60),
    ("shrimp", "Tôm / Garnelen", "Thịt & hải sản", "kg", "", 1, 3, 16.90, 0, ("Garnelen roh geschält TK 1kg", "Stk", 1), 6, 30),
    ("egg", "Trứng gà / Eier", "Thịt & hải sản", "quả", "khay", 30, 60, 0.22, 0, ("Eier Bodenhaltung M 30er", "Pal", 30), 7, 21),
    ("sprouts", "Giá đỗ / Sojasprossen", "Rau củ & rau thơm", "kg", "", 1, 2, 2.90, 2, ("Sojasprossen 1kg", "kg", 1), 6, 4),
    ("coriander", "Rau mùi / Koriander", "Rau củ & rau thơm", "bó", "", 1, 8, 0.85, 2, ("Koriander Bund", "Bund", 1), 20, 5),
    ("basil", "Húng quế Thái / Thai Basilikum", "Rau củ & rau thơm", "bó", "", 1, 4, 1.90, 2, ("Thai Basilikum 100g", "Stk", 1), 12, 5),
    ("onion", "Hành tây / Zwiebeln", "Rau củ & rau thơm", "kg", "", 1, 5, 1.20, 0, ("Zwiebeln gelb 5kg Sack", "Sack", 5), 2, 30),
    ("garlic", "Tỏi / Knoblauch", "Rau củ & rau thơm", "kg", "", 1, 1, 4.50, 0, ("Knoblauch geschält 1kg", "kg", 1), 1, 30),
    ("fishsauce", "Nước mắm / Fischsauce", "Gia vị & sốt", "l", "chai", 0.7, 2, 4.90, 1, ("Fischsauce Squid Brand 0,7l", "Fl", 0.7), 4, None),
    ("soy", "Nước tương / Sojasauce", "Gia vị & sốt", "l", "", 1, 2, 3.20, 1, ("Sojasauce hell 1l", "Fl", 1), 4, None),
    ("coconut", "Nước cốt dừa / Kokosmilch", "Gia vị & sốt", "l", "lon", 0.4, 3, 3.10, 1, ("Kokosmilch Aroy-D 400ml", "Dose", 0.4), 30, None),
    ("oil", "Dầu ăn / Rapsöl", "Gia vị & sốt", "l", "can", 10, 10, 1.90, 0, ("Rapsöl 10l Kanister", "Stk", 10), 1, None),
    ("box", "Hộp mang về / Takeaway-Box", "Bao bì", "cái", "thùng", 300, 200, 0.18, 0, ("Menübox 1-geteilt 300 Stk", "Karton", 300), 1, None),
]

# Mức tồn chuẩn (đặt hàng lên tới mức này) cho vài mặt hàng; còn lại = gấp đôi ngưỡng.
# Đồ uống của quầy không còn là dữ liệu mẫu: lấy từ hoá đơn METRO thật (app/imports).
PAR_LEVELS = {"rice": 54, "box": 600}

DISHES = [
    ("D20", "Phở bò", 14.90, {"pho": 0.15, "beef": 0.10, "sprouts": 0.05, "coriander": 0.1, "onion": 0.02, "fishsauce": 0.01, "box": 1}),
    ("L6", "Gebratener Reis mit Hühnerfleisch", 8.90, {"rice": 0.15, "chicken": 0.10, "egg": 1, "sprouts": 0.03, "oil": 0.02, "soy": 0.015, "box": 1}),
    ("L7", "Gebratener Reis mit knuspriger Ente", 14.50, {"rice": 0.15, "duck": 0.18, "egg": 1, "sprouts": 0.03, "oil": 0.02, "soy": 0.015, "box": 1}),
    ("L4", "Sommerrolle", 8.50, {"paper": 0.1, "shrimp": 0.08, "bun": 0.05, "coriander": 0.1}),
    ("L5", "Nem", 5.50, {"paper": 0.05, "shrimp": 0.03, "beef": 0.03, "bun": 0.02, "oil": 0.05, "fishsauce": 0.01}),
    ("D31", "Thai Curry mit Hähnchen", 13.90, {"chicken": 0.15, "coconut": 0.2, "basil": 0.2, "rice": 0.15, "box": 1}),
]

DAILY_SALES = {"D20": 9, "L6": 12, "L7": 6, "L4": 4, "L5": 6, "D31": 6}
WEEKEND_BOOST = 1.3
# Tuần cuối đặt thiếu các mặt hàng này -> dashboard có cảnh báo sắp hết.
SKIP_LAST_WEEK = {"shrimp", "pho", "sprouts", "box"}


def weekly_usage(key: str) -> float:
    avg_factor = (5 + 2 * WEEKEND_BOOST) / 7
    return sum(
        DAILY_SALES[code] * items.get(key, 0) for code, _, _, items in DISHES
    ) * avg_factor * 7


def seed(session: Session) -> None:
    if session.scalar(select(Ingredient.id).limit(1)) is not None:
        return
    rnd = random.Random(42)
    suppliers = []
    for name, phone, email, note in SUPPLIERS:
        s = Supplier(name=name, phone=phone, email=email, note=note)
        session.add(s)
        suppliers.append(s)
    session.flush()

    ings: dict[str, Ingredient] = {}
    meta: dict[str, tuple] = {}
    for key, name, cat, unit, pack_unit, pack_size, min_stock, price, sup, inv, weekly, shelf in INGREDIENTS:
        ing = Ingredient(
            name=name, category=cat, unit=unit, pack_unit=pack_unit, pack_size=pack_size,
            min_stock=min_stock, last_price=price, supplier_id=suppliers[sup].id,
            area=BAR if cat in AREAS[BAR]["categories"] else KITCHEN, par_level=PAR_LEVELS.get(key, 0),
        )
        session.add(ing)
        ings[key] = ing
        meta[key] = (price, sup, inv, weekly, shelf)
    session.flush()

    for code, name, price, items in DISHES:
        dish = Dish(code=code, name=name, price=price, area=BAR if code.startswith("G") else KITCHEN)
        for key, qty in items.items():
            dish.items.append(RecipeItem(ingredient_id=ings[key].id, quantity=qty))
        session.add(dish)
    session.flush()
    dishes = {d.code: d for d in session.scalars(select(Dish)).all()}

    today = date.today()
    start = today - timedelta(days=35)
    # Lịch sử: mỗi tuần một hoá đơn cho mỗi NCC, giá biến động nhẹ (thịt bò tăng dần).
    events: list[tuple[datetime, str, object]] = []
    for week in range(5):
        day = start + timedelta(days=week * 7)
        for sup_idx in range(len(suppliers)):
            events.append((datetime.combine(day + timedelta(days=sup_idx), time(8)), "invoice", (sup_idx, week)))
    for offset in range(32, 0, -1):
        day = today - timedelta(days=offset)
        events.append((datetime.combine(day, time(21)), "sales", None))
    events.sort(key=lambda e: e[0])

    for when, kind, payload in events:
        if kind == "invoice":
            sup_idx, week = payload
            _seed_invoice(session, suppliers[sup_idx], sup_idx, week, when, ings, meta, rnd)
        else:
            for code, avg in DAILY_SALES.items():
                qty = max(0, int(round(avg * rnd.uniform(0.6, 1.4) * (WEEKEND_BOOST if when.weekday() >= 5 else 1))))
                if qty:
                    stock.record_sale(session, dishes[code], qty, when)

    # Huỷ các lô đã quá hạn (ghi vào hao hụt) như bếp thực tế làm hằng ngày.
    stock.write_off_expired(session, datetime.combine(today - timedelta(days=1), time(22)))
    # Một ít hao hụt và lô sắp hết hạn để dashboard có cảnh báo.
    stock.consume(session, ings["coriander"], 3, "WASTE", "Rau héo", datetime.combine(today - timedelta(days=2), time(22)))
    stock.receive(
        session, ings["sprouts"], 3, 2.95, supplier_id=suppliers[2].id,
        expiry_date=today + timedelta(days=1), received_at=datetime.combine(today - timedelta(days=1), time(8)),
        reference="Giao buổi sáng",
    )
    stock.receive(
        session, ings["shrimp"], 2, 17.40, supplier_id=suppliers[0].id,
        expiry_date=today + timedelta(days=2), received_at=datetime.combine(today - timedelta(days=1), time(8)),
        reference="Tôm tươi",
    )
    # Đánh dấu các mục đang thiếu là "đã báo" để không gửi mail ngay khi khởi động bản demo.
    alerts.check_low_stock(session)
    session.commit()


def _seed_invoice(session, supplier, sup_idx, week, when, ings, meta, rnd):
    invoice = Invoice(
        supplier_id=supplier.id, supplier_name_raw=supplier.name,
        invoice_number=f"{supplier.name.split()[0][:3].upper()}-{when:%y%m%d}",
        invoice_date=when.date(), source="manual",
    )
    session.add(invoice)
    pos = 0
    for key, (price, sup, (raw_name, unit_raw, factor), weekly, shelf) in meta.items():
        if sup != sup_idx:
            continue
        drift = 1 + (week - 4) * (0.03 if key == "beef" else 0.01) + rnd.uniform(-0.04, 0.04)
        unit_cost = round(price * drift, 4)
        usage = weekly_usage(key)
        target = usage / factor if usage else weekly
        if week == 4 and key in SKIP_LAST_WEEK:
            target *= 0.7
        qty = max(1, round(target * rnd.uniform(1.02, 1.15)))
        line_unit_price = round(unit_cost * factor, 2)
        invoice.lines.append(
            InvoiceLine(
                position=pos, raw_name=raw_name, quantity=qty, unit_raw=unit_raw,
                unit_price=line_unit_price, line_total=round(qty * line_unit_price, 2),
                vat_rate=19 if ings[key].area == BAR else 7,
                ingredient_id=ings[key].id, pack_factor=factor,
                expiry_date=(when.date() + timedelta(days=shelf)) if shelf else None,
            )
        )
        pos += 1
    invoice.subtotal = round(sum(line.line_total for line in invoice.lines), 2)
    invoice.vat = round(sum(line.line_total * line.vat_rate / 100 for line in invoice.lines), 2)
    invoice.total = round(invoice.subtotal + invoice.vat, 2)
    session.flush()
    invoices.confirm(session, invoice)
    invoice.confirmed_at = when
