"""Quy trình hoá đơn: tạo bản nháp từ kết quả AI -> duyệt -> nhập kho."""
from __future__ import annotations

import json
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Ingredient, Invoice, InvoiceLine, Supplier
from . import matching, stock
from .ocr import ParsedInvoice


def find_supplier(session: Session, name: str) -> Supplier | None:
    if not name:
        return None
    key = matching.normalize(name)
    suppliers = session.scalars(select(Supplier)).all()
    for s in suppliers:
        if matching.normalize(s.name) == key:
            return s
    # Khớp lỏng: "Großmarkt Breisgau GmbH Freiburg" ~ "Großmarkt Breisgau"
    best, best_score = None, 0.0
    for s in suppliers:
        score = matching.similarity(name, s.name)
        if score > best_score:
            best, best_score = s, score
    return best if best_score >= 0.6 else None


def create_draft(session: Session, parsed: ParsedInvoice, file_path: str, file_type: str) -> Invoice:
    supplier = find_supplier(session, parsed.supplier)
    invoice = Invoice(
        supplier_id=supplier.id if supplier else None,
        supplier_name_raw=parsed.supplier,
        invoice_number=parsed.invoice_number,
        invoice_date=parsed.invoice_date,
        file_path=file_path,
        file_type=file_type,
        source=parsed.source,
        subtotal=parsed.subtotal,
        vat=parsed.vat,
        total=parsed.total,
        raw_json=json.dumps(parsed.raw, ensure_ascii=False, indent=2),
    )
    session.add(invoice)
    ingredients = list(session.scalars(select(Ingredient).where(Ingredient.active == 1)).all())
    for pos, line in enumerate(parsed.lines):
        m = matching.match_line(session, invoice.supplier_id, line.name, line.unit, ingredients)
        invoice.lines.append(
            InvoiceLine(
                position=pos,
                raw_name=line.name,
                quantity=line.quantity,
                unit_raw=line.unit,
                unit_price=line.unit_price,
                line_total=line.total,
                vat_rate=line.vat_rate,
                ingredient_id=m.ingredient.id if m.ingredient else None,
                pack_factor=m.pack_factor,
                match_score=m.score,
            )
        )
    session.flush()
    return invoice


class ConfirmError(Exception):
    pass


def confirm(session: Session, invoice: Invoice) -> list[str]:
    """Nhập kho các dòng đã gắn nguyên liệu, ghi nhớ mapping. Trả về ghi chú."""
    if invoice.status == "confirmed":
        raise ConfirmError("Hoá đơn này đã được nhập kho.")
    notes: list[str] = []
    if invoice.supplier_id is None and invoice.supplier_name_raw.strip():
        supplier = find_supplier(session, invoice.supplier_name_raw)
        if supplier is None:
            supplier = Supplier(name=invoice.supplier_name_raw.strip())
            session.add(supplier)
            session.flush()
            notes.append(f"Đã tạo nhà cung cấp mới: {supplier.name}")
        invoice.supplier_id = supplier.id
    when = datetime.combine(invoice.invoice_date, time(9, 0)) if invoice.invoice_date else datetime.now()
    ref = f"HĐ #{invoice.id}" + (f" ({invoice.invoice_number})" if invoice.invoice_number else "")
    imported = 0
    for line in invoice.lines:
        if not line.ingredient_id:
            notes.append(f"Bỏ qua (chưa gắn nguyên liệu): {line.raw_name}")
            continue
        base_qty = line.quantity * (line.pack_factor or 1.0)
        if base_qty <= 0:
            notes.append(f"Bỏ qua (số lượng 0): {line.raw_name}")
            continue
        total = line.line_total or line.quantity * line.unit_price
        unit_cost = total / base_qty if base_qty else 0.0
        ingredient = session.get(Ingredient, line.ingredient_id)
        stock.receive(
            session,
            ingredient,
            base_qty,
            unit_cost,
            supplier_id=invoice.supplier_id,
            invoice_id=invoice.id,
            expiry_date=line.expiry_date,
            received_at=when,
            reference=ref,
        )
        if ingredient.supplier_id is None:
            ingredient.supplier_id = invoice.supplier_id
        matching.remember(session, invoice.supplier_id, line.raw_name, ingredient.id, line.pack_factor or 1.0)
        imported += 1
    if imported == 0:
        raise ConfirmError("Chưa có dòng nào được gắn với nguyên liệu trong kho.")
    invoice.status = "confirmed"
    invoice.confirmed_at = datetime.now()
    notes.insert(0, f"Đã nhập kho {imported} mặt hàng.")
    return notes


def next_position(session: Session, invoice_id: int) -> int:
    current = session.scalar(select(func.max(InvoiceLine.position)).where(InvoiceLine.invoice_id == invoice_id))
    return (current or 0) + 1


def area_totals(invoice: Invoice) -> dict[str, float]:
    """Chia tiền hàng của hoá đơn theo khu (bếp / quầy / chưa gắn)."""
    totals: dict[str, float] = {}
    for line in invoice.lines:
        key = line.ingredient.area if line.ingredient else ""
        totals[key] = round(totals.get(key, 0.0) + (line.line_total or 0.0), 2)
    return totals
