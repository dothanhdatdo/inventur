"""Nhập dữ liệu thật một lần (vd. hoá đơn lấy từ email) vào database của người dùng.

Mỗi lần nhập có KEY riêng; khi đã chạy, KEY được lưu trong bảng settings nên không chạy lại.
Chạy lúc khởi động app (server và bản trình duyệt) sau khi tạo dữ liệu mẫu.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import areas, config
from ..db import (
    Batch, Dish, Ingredient, Invoice, InvoiceLine, Movement, RecipeItem, Sale, Stocktake, StocktakeLine, Supplier,
    SupplierAlias,
)
from ..services import alerts, invoices, matching
from . import metro_20260919, metro_20261005

IMPORTS = [metro_20260919, metro_20261005]
NOTICE_KEY = "startup_notice"
log = logging.getLogger("inventory.imports")


def apply_pending(session: Session) -> list[str]:
    if not config.APPLY_IMPORTS:
        return []
    notes = []
    for module in IMPORTS:
        flag = f"import_{module.KEY}"
        if alerts.get_setting(session, flag):
            continue
        try:
            if getattr(module, "KIND", "") == "full_invoice":
                note = apply_full_invoice(session, module)
            else:
                note = apply_invoice_import(session, module)
        except Exception as exc:  # noqa: BLE001 - lỗi nhập dữ liệu không được làm app không mở được
            session.rollback()
            log.exception("Nhập dữ liệu %s thất bại", module.KEY)
            notes.append(
                f"Chưa nhập tự động được hoá đơn {getattr(module, 'INVOICE_NUMBER', module.KEY)} ({exc}). "
                "Có thể tải file PDF hoá đơn lên ở trang Nhập hàng."
            )
            continue  # không đánh dấu đã chạy -> lần mở app sau thử lại
        alerts.set_setting(session, flag, datetime.now().isoformat(timespec="seconds"))
        session.commit()
        notes.append(note)
    if notes:
        alerts.set_setting(session, NOTICE_KEY, " · ".join(notes))
        session.commit()
    return notes


def remove_demo_drinks(session: Session, module) -> int:
    """Xoá đồ uống mẫu: nguyên liệu, lô, nhật ký, mapping, định lượng, món đồ uống mẫu và lịch sử bán của chúng."""
    ing_ids = list(session.scalars(select(Ingredient.id).where(Ingredient.name.in_(module.DEMO_DRINK_INGREDIENTS))))
    dish_ids = list(session.scalars(select(Dish.id).where(Dish.name.in_(module.DEMO_DRINK_DISHES))))
    if dish_ids:
        session.execute(delete(Sale).where(Sale.dish_id.in_(dish_ids)))
        session.execute(delete(RecipeItem).where(RecipeItem.dish_id.in_(dish_ids)))
        session.execute(delete(Dish).where(Dish.id.in_(dish_ids)))
    if ing_ids:
        touched = set(session.scalars(select(InvoiceLine.invoice_id).where(InvoiceLine.ingredient_id.in_(ing_ids))))
        session.execute(delete(Movement).where(Movement.ingredient_id.in_(ing_ids)))
        session.execute(delete(Batch).where(Batch.ingredient_id.in_(ing_ids)))
        session.execute(delete(SupplierAlias).where(SupplierAlias.ingredient_id.in_(ing_ids)))
        session.execute(delete(RecipeItem).where(RecipeItem.ingredient_id.in_(ing_ids)))
        stocktakes = set(session.scalars(select(StocktakeLine.stocktake_id).where(StocktakeLine.ingredient_id.in_(ing_ids))))
        session.execute(delete(StocktakeLine).where(StocktakeLine.ingredient_id.in_(ing_ids)))
        session.execute(delete(InvoiceLine).where(InvoiceLine.ingredient_id.in_(ing_ids)))
        session.execute(delete(Ingredient).where(Ingredient.id.in_(ing_ids)))
        session.flush()
        session.expire_all()
        for st_id in stocktakes:  # phiếu kiểm kê chỉ có đồ uống mẫu -> xoá
            if not session.scalar(select(func.count(StocktakeLine.id)).where(StocktakeLine.stocktake_id == st_id)):
                session.execute(delete(Stocktake).where(Stocktake.id == st_id))
        for inv_id in touched:  # hoá đơn mẫu chỉ còn hàng bếp -> tính lại tiền; không còn dòng nào -> xoá
            invoice = session.get(Invoice, inv_id)
            if invoice is None:
                continue
            if not invoice.lines:
                session.execute(delete(Batch).where(Batch.invoice_id == inv_id))
                session.delete(invoice)
            else:
                invoice.subtotal = round(sum(line.line_total for line in invoice.lines), 2)
                invoice.vat = round(sum(line.line_total * line.vat_rate / 100 for line in invoice.lines), 2)
                invoice.total = round(invoice.subtotal + invoice.vat, 2)
    for name in module.DEMO_SUPPLIERS:
        supplier = session.scalars(select(Supplier).where(Supplier.name == name)).first()
        if supplier and not _supplier_in_use(session, supplier.id):
            session.delete(supplier)
    session.flush()
    return len(ing_ids)


def _supplier_in_use(session: Session, supplier_id: int) -> bool:
    for model, column in ((Ingredient, Ingredient.supplier_id), (Invoice, Invoice.supplier_id), (Batch, Batch.supplier_id)):
        if session.scalar(select(func.count()).select_from(model).where(column == supplier_id)):
            return True
    return False


def apply_invoice_import(session: Session, module) -> str:
    removed = remove_demo_drinks(session, module)
    if session.scalar(select(Invoice.id).where(Invoice.invoice_number == module.INVOICE_NUMBER)):
        session.commit()
        return f"Đã xoá {removed} đồ uống mẫu (hoá đơn {module.INVOICE_NUMBER} đã có sẵn, không nhập lại)"
    supplier = invoices.find_supplier(session, module.SUPPLIER)
    if supplier is None:
        supplier = Supplier(name=module.SUPPLIER, note=module.SUPPLIER_NOTE)
        session.add(supplier)
        session.flush()
    when = datetime(*module.INVOICE_DATE)
    invoice = Invoice(
        supplier_id=supplier.id, supplier_name_raw=module.SUPPLIER, invoice_number=module.INVOICE_NUMBER,
        invoice_date=when.date(), source="email",
    )
    session.add(invoice)
    for pos, (name, category, unit, pack_unit, pack_size, min_stock, par, raw_name, art_nr, unit_raw,
              qty, total, vat) in enumerate(module.LINES):
        ing = session.scalars(select(Ingredient).where(Ingredient.name == name)).first()
        if ing is None:
            ing = Ingredient(
                name=name, category=category, unit=unit, pack_unit=pack_unit, pack_size=pack_size,
                min_stock=min_stock, par_level=par, supplier_id=supplier.id, area=areas.BAR,
            )
            session.add(ing)
            session.flush()
        invoice.lines.append(InvoiceLine(
            position=pos, raw_name=raw_name, quantity=qty, unit_raw=unit_raw,
            unit_price=round(total / qty, 4), line_total=total, vat_rate=vat,
            ingredient_id=ing.id, pack_factor=pack_size, match_score=1.0,
        ))
    invoice.subtotal = round(sum(line.line_total for line in invoice.lines), 2)
    invoice.vat = round(sum(line.line_total * line.vat_rate / 100 for line in invoice.lines), 2)
    invoice.total = round(invoice.subtotal + invoice.vat, 2)
    session.flush()
    invoices.confirm(session, invoice)
    invoice.confirmed_at = when
    for batch in session.scalars(select(Batch).where(Batch.invoice_id == invoice.id)):
        batch.received_at = when
    for movement in session.scalars(select(Movement).where(Movement.reference.like(f"HĐ #{invoice.id}%"))):
        movement.created_at = when
    alerts.check_low_stock(session)  # hàng mới nhập không phải "vừa xuống dưới ngưỡng"
    session.commit()
    count = len({line[0] for line in module.LINES})
    return f"Đã xoá {removed} đồ uống mẫu và nhập {count} mặt hàng quầy từ hoá đơn METRO {when:%d.%m.%Y}"


def _resolve_item(session: Session, supplier: Supplier, raw_name: str, unit_raw: str, units: float,
                  spec: tuple, created: list[Ingredient]) -> tuple[Ingredient, float]:
    """Mặt hàng trong kho cho một dòng: theo tên hàng METRO đã nhớ (người dùng đã gắn) -> theo tên -> tạo mới."""
    name, category, unit, pack_unit, pack_size, factor, min_stock, par, area = spec
    line_spec = matching.line_spec(raw_name, unit_raw, units)
    alias = matching.find_alias(session, supplier.id, raw_name)
    if alias is not None and alias.ingredient is not None:
        ing = alias.ingredient
        ing.active = 1
        weighed = unit_raw.upper() == "KG"  # hàng cân: số lượng đã là kg -> quy theo đơn vị kho
        return ing, (matching.factor_for(line_spec, unit_raw, ing) if weighed else alias.pack_factor or 1.0)
    ing = session.scalars(select(Ingredient).where(Ingredient.name == name)).first()
    if ing is not None:
        ing.active = 1
        return ing, (factor if ing.unit == unit else matching.factor_for(line_spec, unit_raw, ing))
    ing = Ingredient(
        name=name, category=category, unit=unit, pack_unit=pack_unit, pack_size=pack_size,
        min_stock=min_stock, par_level=par, supplier_id=supplier.id, area=area,
    )
    session.add(ing)
    session.flush()
    created.append(ing)
    return ing, factor


def apply_full_invoice(session: Session, module) -> str:
    """Nhập trọn một hoá đơn (bếp + quầy) qua đúng quy trình nhập kho của app.

    invoices.confirm() chống nhập trùng theo từng dòng: nếu người dùng đã tự quét và nhập hoá đơn này
    (một phần hay toàn bộ), các dòng đó được bỏ qua.
    """
    when = datetime(*module.INVOICE_DATE)
    supplier = invoices.find_supplier(session, module.SUPPLIER)
    if supplier is None:
        supplier = Supplier(name=module.SUPPLIER, note=module.SUPPLIER_NOTE)
        session.add(supplier)
        session.flush()
    invoice = Invoice(
        supplier_id=supplier.id, supplier_name_raw=module.SUPPLIER, invoice_number=module.INVOICE_NUMBER,
        invoice_date=when.date(), source="email", subtotal=module.SUBTOTAL, vat=module.VAT,
        total=round(module.SUBTOTAL + module.VAT, 2),
        raw_json=json.dumps({"import": module.KEY, "deposit_net": module.DEPOSIT}, ensure_ascii=False),
    )
    session.add(invoice)
    created: list[Ingredient] = []
    for row, raw_name, _art, unit_raw, qty, units, total, vat, expiry, category, key in module.LINES:
        line = InvoiceLine(
            position=row, source_row=row, raw_name=raw_name, quantity=qty, unit_raw=unit_raw,
            unit_price=round(total / qty, 4), line_total=total, vat_rate=vat, expiry_date=expiry,
            category_hint=category, units_per_pack=units, pack_factor=1.0, match_score=0.0,
        )
        if key is not None:
            ing, factor = _resolve_item(session, supplier, raw_name, unit_raw, units, module.ITEMS[key], created)
            line.ingredient_id, line.pack_factor, line.match_score = ing.id, factor, 1.0
        invoice.lines.append(line)
    session.flush()
    label = f"hoá đơn METRO {when:%d.%m.%Y}"
    try:
        invoices.confirm(session, invoice)
    except invoices.ConfirmError:  # mọi dòng đã được nhập trước đó (người dùng tự quét) -> không nhập gì
        session.delete(invoice)
        for ing in created:
            session.delete(ing)
        session.commit()
        return f"{label.capitalize()} đã có trong kho (đã nhập trước đó) – không nhập lại"
    invoice.confirmed_at = when
    for batch in session.scalars(select(Batch).where(Batch.invoice_id == invoice.id)):
        batch.received_at = when
    for movement in session.scalars(select(Movement).where(Movement.reference.like(f"HĐ #{invoice.id} %"))):
        movement.created_at = when
    for ing in created:  # tạo ra nhưng dòng của nó đã được nhập trước đó -> bỏ
        if not session.scalar(select(func.count(Batch.id)).where(Batch.ingredient_id == ing.id)):
            session.execute(delete(SupplierAlias).where(SupplierAlias.ingredient_id == ing.id))
            session.delete(ing)
    alerts.check_low_stock(session)  # hàng mới nhập không phải "vừa xuống dưới ngưỡng"
    session.commit()
    booked = [line for line in invoice.lines if line.ingredient_id]
    by_area = {area: len({line.ingredient_id for line in booked if line.ingredient.area == area}) for area in areas.AREA_KEYS}
    skipped = sum(1 for line in invoice.lines if line.match_score == invoices.DUPLICATE)
    unlinked = [line for line in invoice.lines if line.ingredient_id is None and invoices.counts_in_totals(line)]
    parts = [f"Đã nhập {label}: {by_area[areas.KITCHEN]} mặt hàng bếp, {by_area[areas.BAR]} mặt hàng quầy"]
    if skipped:
        parts.append(f"{skipped} dòng đã có từ lần quét trước nên không nhập lại")
    if unlinked:
        parts.append(f"{len(unlinked)} dòng dụng cụ chỉ ghi vào hoá đơn, không tính tồn kho")
    return " · ".join(parts)
