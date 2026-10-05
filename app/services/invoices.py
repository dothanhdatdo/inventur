"""Quy trình hoá đơn: tạo bản nháp từ kết quả AI -> duyệt -> nhập kho."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import areas
from ..db import Ingredient, Invoice, InvoiceLine, Supplier
from . import matching, stock
from .ocr import ParsedInvoice

# Mã đóng gói trên hoá đơn METRO -> tên đơn vị mua.
PACK_CODES = {
    "KA": "két", "KI": "két", "SC": "két", "KT": "thùng", "SP": "lốc", "PG": "gói", "DS": "lon", "TG": "hộp",
    "BT": "túi", "EI": "xô", "KS": "can", "FS": "bao", "ST": "cái",
}
COUNT_UNITS = ("cái", "chai")
DUPLICATE = -1.0  # match_score: dòng đã nhập kho ở bản khác của cùng hoá đơn
COUNTED_BEFORE = -2.0  # match_score: dòng chưa gắn, tiền đã tính ở bản trước của cùng hoá đơn
KEPT = -3.0  # match_score: người dùng bỏ dấu "trùng" và vẫn gắn nguyên liệu -> vẫn nhập


@dataclass
class NewItemPlan:
    """Nguyên liệu sẽ được tạo từ một dòng hoá đơn ("+ Tạo nguyên liệu mới")."""

    unit: str
    factor: float  # số đơn vị kho trong 1 đơn vị trên HĐ
    area: str
    category: str
    pack_unit: str = ""
    pack_size: float = 1.0


def new_item_plan(raw_name: str, unit_raw: str, units_per_pack: float | None = None,
                  category_hint: str = "", vat_rate: float | None = None) -> NewItemPlan:
    """Chọn đơn vị kho hợp lý cho hàng mới: đồ uống theo chai, đồ bếp có ghi kg/l theo kg/l, còn lại theo cái."""
    name = (raw_name or "").strip()
    category = category_hint or "Mới từ hoá đơn"
    # Đoán khu: nhóm hàng trên hoá đơn, tên giống đồ uống hoặc VAT 19% -> quầy; còn lại -> bếp.
    area = areas.guess_area(name, category, vat_rate or None)
    raw_kind = matching.UNIT_ALIASES.get(matching.normalize(unit_raw))
    if raw_kind:  # hàng cân/đong: số lượng trên HĐ đã là kg/l
        unit = "kg" if raw_kind in ("kg", "g") else "l"
        return NewItemPlan(unit, matching._convert(1.0, raw_kind, unit), area, category)
    spec = matching.line_spec(name, unit_raw, units_per_pack)
    plain_category = areas._plain(category)
    nonfood = areas.looks_like_nonfood(name) or any(f" {w} " in plain_category for w in ("nonfood", "drogerie", "dung cu", "ve sinh"))
    if area == areas.BAR and spec.kind == "l":
        unit, factor = "chai", spec.pieces
    elif spec.content and not spec.multipack and not nonfood:
        # "1kg ...", "800G ...", "10l DẦU" -> kho tính kg/l để dùng cho định lượng món.
        unit, factor = spec.kind, spec.content
    else:
        unit, factor = "cái", spec.pieces
    plan = NewItemPlan(unit, round(factor, 6), area, category)
    if unit in COUNT_UNITS and factor > 1:
        code = (unit_raw or "").strip()
        plan.pack_unit = PACK_CODES.get(code.upper(), code.lower() or "kiện")
        plan.pack_size = plan.factor
    return plan


def line_plan(line: InvoiceLine) -> NewItemPlan:
    return new_item_plan(line.raw_name, line.unit_raw, line.units_per_pack, line.category_hint, line.vat_rate)


def line_hints(line: InvoiceLine) -> dict:
    """Dữ liệu cho app.js: quy đổi khi chọn nguyên liệu có sẵn / khi tạo mới."""
    spec = matching.line_spec(line.raw_name, line.unit_raw, line.units_per_pack)
    plan = line_plan(line)
    return {"pieces": spec.pieces, "content": spec.content or 0, "kind": spec.kind or "",
            "new_unit": plan.unit, "new_factor": plan.factor, "new_area": plan.area}


def line_key(name: str, quantity: float) -> tuple[str, float]:
    return matching.normalize(name), round(quantity or 0.0, 3)


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
    booked = _linked_lines(same_invoices(session, invoice, "confirmed"))
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
            units_per_pack=getattr(line, "units_per_pack", None),
            source_row=pos,
        )
        if new_line.ingredient_id is None:
            new_line.pack_factor = line_plan(new_line).factor  # sẵn sàng cho "+ Tạo nguyên liệu mới"
        if take_same_row(booked, new_line) is not None:
            # Dòng này đã được nhập kho ở bản khác của cùng hoá đơn -> bỏ gắn để không nhập trùng.
            new_line.ingredient_id = None
            new_line.match_score = DUPLICATE
        invoice.lines.append(new_line)
    fresh = [line for line in invoice.lines if line.match_score >= 0]
    if len(fresh) < len(invoice.lines):
        # Phần đã nhập rồi không tính lại: phiếu nháp chỉ còn các dòng mới (dòng trùng được đánh dấu "Bỏ").
        invoice.subtotal = round(sum(line.line_total for line in fresh), 2)
        invoice.vat = round(sum(line.line_total * (line.vat_rate or 0) / 100 for line in fresh), 2)
        invoice.total = round(invoice.subtotal + invoice.vat, 2)
    session.flush()
    return invoice


def same_invoices(session: Session, invoice: Invoice, status: str) -> list[Invoice]:
    """Các bản khác của cùng hoá đơn (cùng số hoá đơn).

    Không lọc theo nhà cung cấp: cùng một hoá đơn METRO có thể được gán NCC khác tên ("METRO" / "METRO Gundelfingen").
    Từng dòng còn được so tên + số lượng (take_same_row), nên hoá đơn khác trùng số không bị nhận nhầm.
    """
    number = (invoice.invoice_number or "").strip()
    if not number:
        return []
    query = select(Invoice).where(Invoice.invoice_number == number, Invoice.status == status)
    if invoice.id is not None:
        query = query.where(Invoice.id != invoice.id)
    return list(session.scalars(query.order_by(Invoice.id)).all())


def _linked_lines(invoices: list[Invoice]) -> list[InvoiceLine]:
    return [line for inv in invoices for line in inv.lines if line.ingredient_id is not None]


def take_same_row(pool: list[InvoiceLine], line: InvoiceLine) -> InvoiceLine | None:
    """Tìm (và lấy ra khỏi pool) dòng tương ứng của bản khác của cùng hoá đơn.

    Hai lần quét cùng một PDF: so đúng số thứ tự dòng (+ tên, số lượng) -> hai dòng giống hệt nhau
    (METRO in 2 dòng cho 2 thùng) vẫn phân biệt được. Dữ liệu không có số dòng (nhập tay, bản cũ): so tên + số lượng.
    """
    key = line_key(line.raw_name, line.quantity)
    for exact in (True, False):
        for cand in pool:
            both_rows = cand.source_row is not None and line.source_row is not None
            if exact != both_rows or line_key(cand.raw_name, cand.quantity) != key:
                continue
            if both_rows and cand.source_row != line.source_row:
                continue
            pool.remove(cand)
            return cand
    return None


def duplicate_of(session: Session, invoice: Invoice) -> Invoice | None:
    """Bản của cùng hoá đơn đã nhập kho trước đó."""
    copies = same_invoices(session, invoice, "confirmed")
    return copies[0] if copies else None


def other_draft(session: Session, invoice: Invoice) -> Invoice | None:
    """Phiếu nháp khác của cùng hoá đơn (vd. quét PDF hai lần)."""
    drafts = same_invoices(session, invoice, "draft")
    return drafts[0] if drafts else None


def _drop_from_totals(invoice: Invoice, lines: list[InvoiceLine]) -> None:
    """Bớt tiền hàng + VAT của các dòng không còn tính vào hoá đơn này."""
    net = sum(line.line_total or 0.0 for line in lines)
    tax = sum((line.line_total or 0.0) * (line.vat_rate or 0.0) / 100 for line in lines)
    invoice.subtotal = round((invoice.subtotal or 0.0) - net, 2)
    invoice.vat = round(max((invoice.vat or 0.0) - tax, 0.0), 2)
    invoice.total = round(invoice.subtotal + invoice.vat, 2)


def counts_in_totals(line: InvoiceLine) -> bool:
    """Dòng trùng (đã nhập ở hoá đơn trước) mà chưa gắn thì không tính tiền lần nữa."""
    return not ((line.match_score or 0.0) < 0 and not line.ingredient_id)


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
    unlinked: list[str] = []
    # Chốt chặn: dòng đã nhập kho ở bản khác của cùng hoá đơn (vd. hai phiếu nháp của cùng một PDF) không nhập lần nữa,
    # trừ khi người dùng đã chủ động giữ lại dòng bị đánh dấu trùng (KEPT).
    copies = same_invoices(session, invoice, "confirmed")
    booked = _linked_lines(copies)
    for line in invoice.lines:  # dòng đã đánh dấu trùng lúc quét giữ chỗ của dòng tương ứng
        if line.ingredient_id is None and line.match_score == DUPLICATE:
            take_same_row(booked, line)
    to_import: list[InvoiceLine] = []
    duplicates: list[tuple[InvoiceLine, InvoiceLine]] = []
    for line in invoice.lines:
        if not line.ingredient_id:
            if counts_in_totals(line):
                unlinked.append(line.raw_name)
            continue
        if line.match_score != KEPT:
            earlier = take_same_row(booked, line)
            if earlier is not None:
                duplicates.append((line, earlier))
                continue
        if line.quantity * (line.pack_factor or 1.0) <= 0:
            notes.append(f"Bỏ qua (số lượng 0): {line.raw_name}")
            continue
        to_import.append(line)
    if not to_import:  # chưa thay đổi gì -> báo lỗi, phiếu giữ nguyên
        if duplicates:
            raise ConfirmError(
                f"Các dòng đã gắn đều đã được nhập kho ở hoá đơn #{duplicates[0][1].invoice_id} cùng số – không nhập lại. "
                "Nếu thật sự muốn nhập lại một dòng: bỏ dấu “Bỏ” ở dòng đó, gắn nguyên liệu rồi bấm Nhập kho."
            )
        raise ConfirmError("Chưa có dòng nào được gắn với nguyên liệu trong kho.")
    for line, _ in duplicates:
        line.ingredient = None
        line.ingredient_id = None
        line.match_score = DUPLICATE
    _drop_from_totals(invoice, [line for line, _ in duplicates])
    when = datetime.combine(invoice.invoice_date, time(9, 0)) if invoice.invoice_date else datetime.now()
    ref = f"HĐ #{invoice.id}" + (f" ({invoice.invoice_number})" if invoice.invoice_number else "")
    for line in to_import:
        base_qty = line.quantity * (line.pack_factor or 1.0)
        total = line.line_total or line.quantity * line.unit_price
        ingredient = session.get(Ingredient, line.ingredient_id)
        stock.receive(
            session,
            ingredient,
            base_qty,
            total / base_qty,
            supplier_id=invoice.supplier_id,
            invoice_id=invoice.id,
            expiry_date=line.expiry_date,
            received_at=when,
            reference=ref,
        )
        if ingredient.supplier_id is None:
            ingredient.supplier_id = invoice.supplier_id
        matching.remember(session, invoice.supplier_id, line.raw_name, ingredient.id, line.pack_factor or 1.0)
    if copies:
        # Hoá đơn này đã có bản nhập kho trước (nhập bổ sung bằng cách quét lại). Để tiền không bị tính hai lần:
        # dòng vừa nhập được bỏ khỏi bản cũ (nếu ở đó còn chưa gắn); dòng chưa gắn mà bản cũ đã tính thì phiếu này không tính.
        earlier_unlinked = [line for inv in copies for line in inv.lines if line.ingredient_id is None and counts_in_totals(line)]
        for line in to_import:
            moved = take_same_row(earlier_unlinked, line)
            if moved is not None:
                _drop_from_totals(moved.invoice, [moved])
                moved.invoice.lines.remove(moved)
        counted = _linked_lines(copies) + earlier_unlinked
        counted_before = []
        for line in invoice.lines:
            if line.ingredient_id is None and counts_in_totals(line) and take_same_row(counted, line) is not None:
                line.match_score = COUNTED_BEFORE
                counted_before.append(line)
        _drop_from_totals(invoice, counted_before)
    if duplicates:
        notes.append(f"Bỏ qua {len(duplicates)} dòng đã nhập kho ở hoá đơn #{duplicates[0][1].invoice_id} cùng số")
    invoice.status = "confirmed"
    invoice.confirmed_at = datetime.now()
    notes.insert(0, f"Đã nhập kho {len(to_import)} mặt hàng.")
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
        if not counts_in_totals(line):
            continue  # dòng đã nhập ở hoá đơn trước (quét trùng)
        key = line.ingredient.area if line.ingredient else ""
        totals[key] = round(totals.get(key, 0.0) + (line.line_total or 0.0), 2)
    return totals
