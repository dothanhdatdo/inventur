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
    already = previous_lines(session, parsed.invoice_number)
    for pos, line in enumerate(parsed.lines):
        m = matching.match_line(
            session, invoice.supplier_id, line.name, line.unit, ingredients, getattr(line, "units_per_pack", None)
        )
        new_line = InvoiceLine(
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
            expiry_date=getattr(line, "expiry", None),
            category_hint=getattr(line, "category", "") or "",
        )
        key = (matching.normalize(line.name), round(line.quantity, 3))
        if already.get(key, 0) > 0:
            # Dòng này đã được nhập kho trong hoá đơn cùng số trước đó -> bỏ gắn để không nhập trùng.
            already[key] -= 1
            new_line.ingredient_id = None
            new_line.match_score = -1.0
        invoice.lines.append(new_line)
    fresh = [line for line in invoice.lines if line.match_score >= 0]
    if len(fresh) < len(invoice.lines):
        # Phần đã nhập rồi không tính lại: phiếu nháp chỉ còn các dòng mới (dòng trùng được đánh dấu "Bỏ").
        invoice.subtotal = round(sum(line.line_total for line in fresh), 2)
        invoice.vat = round(sum(line.line_total * (line.vat_rate or 0) / 100 for line in fresh), 2)
        invoice.total = round(invoice.subtotal + invoice.vat, 2)
    session.flush()
    return invoice


def previous_lines(session: Session, invoice_number: str) -> dict[tuple[str, float], int]:
    """Các dòng đã nhập kho của (những) hoá đơn cùng số – để phát hiện quét trùng."""
    if not (invoice_number or "").strip():
        return {}
    counts: dict[tuple[str, float], int] = {}
    rows = session.execute(
        select(InvoiceLine.raw_name, InvoiceLine.quantity)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(Invoice.invoice_number == invoice_number.strip(), Invoice.status == "confirmed",
               InvoiceLine.ingredient_id.is_not(None))
    ).all()
    for name, qty in rows:
        key = (matching.normalize(name), round(qty, 3))
        counts[key] = counts.get(key, 0) + 1
    return counts


def duplicate_of(session: Session, invoice: Invoice) -> Invoice | None:
    if not (invoice.invoice_number or "").strip():
        return None
    return session.scalars(
        select(Invoice).where(
            Invoice.invoice_number == invoice.invoice_number.strip(), Invoice.status == "confirmed", Invoice.id != invoice.id
        )
    ).first()


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
    unlinked: list[str] = []
    for line in invoice.lines:
        if not line.ingredient_id:
            unlinked.append(line.raw_name)
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
    if unlinked:
        shown = ", ".join(unlinked[:5]) + (f" … (+{len(unlinked) - 5})" if len(unlinked) > 5 else "")
        notes.insert(1, f"Bỏ qua {len(unlinked)} dòng chưa gắn nguyên liệu: {shown}")
    return notes


def next_position(session: Session, invoice_id: int) -> int:
    current = session.scalar(select(func.max(InvoiceLine.position)).where(InvoiceLine.invoice_id == invoice_id))
    return (current or 0) + 1


def area_totals(invoice: Invoice) -> dict[str, float]:
    """Chia tiền hàng của hoá đơn theo khu (bếp / quầy / chưa gắn)."""
    totals: dict[str, float] = {}
    for line in invoice.lines:
        if not line.ingredient and (line.match_score or 0) < 0:
            continue  # dòng đã nhập ở hoá đơn trước (quét trùng)
        key = line.ingredient.area if line.ingredient else ""
        totals[key] = round(totals.get(key, 0.0) + (line.line_total or 0.0), 2)
    return totals
