"""Nhập dữ liệu thật một lần (vd. hoá đơn lấy từ email) vào database của người dùng.

Mỗi lần nhập có KEY riêng; khi đã chạy, KEY được lưu trong bảng settings nên không chạy lại.
Chạy lúc khởi động app (server và bản trình duyệt) sau khi tạo dữ liệu mẫu.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import areas, config
from ..db import (
    Batch, Dish, Ingredient, Invoice, InvoiceLine, Movement, RecipeItem, Sale, Stocktake, StocktakeLine, Supplier,
    SupplierAlias,
)
from ..services import alerts, invoices
from . import metro_20260919

IMPORTS = [metro_20260919]
NOTICE_KEY = "startup_notice"


def apply_pending(session: Session) -> list[str]:
    if not config.APPLY_IMPORTS:
        return []
    notes = []
    for module in IMPORTS:
        flag = f"import_{module.KEY}"
        if alerts.get_setting(session, flag):
            continue
        note = apply_invoice_import(session, module)
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
