"""Nghiệp vụ tồn kho: nhập lô, xuất theo FEFO/FIFO, kiểm kê, bán món."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Batch, Dish, Ingredient, Movement, Sale, Stocktake, StocktakeLine

EPS = 1e-9


@dataclass
class ConsumeResult:
    quantity: float  # lượng yêu cầu
    cost: float  # giá vốn của lượng đã xuất
    shortfall: float  # lượng thiếu (kho không đủ)


def stock_of(session: Session, ingredient_id: int) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(Batch.quantity_remaining), 0.0)).where(
            Batch.ingredient_id == ingredient_id
        )
    )
    return float(total or 0.0)


def stock_map(session: Session) -> dict[int, float]:
    rows = session.execute(
        select(Batch.ingredient_id, func.sum(Batch.quantity_remaining)).group_by(Batch.ingredient_id)
    ).all()
    return {ing_id: float(qty or 0.0) for ing_id, qty in rows}


def stock_value(session: Session) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(Batch.quantity_remaining * Batch.unit_cost), 0.0))
    )
    return float(total or 0.0)


def receive(
    session: Session,
    ingredient: Ingredient,
    quantity: float,
    unit_cost: float,
    *,
    supplier_id: int | None = None,
    invoice_id: int | None = None,
    expiry_date: date | None = None,
    received_at: datetime | None = None,
    reference: str = "",
    source: str = "purchase",
) -> Batch:
    """Nhập kho một lô mới (quantity theo đơn vị cơ sở)."""
    if quantity <= 0:
        raise ValueError("Số lượng nhập phải > 0")
    when = received_at or datetime.now()
    batch = Batch(
        ingredient_id=ingredient.id,
        supplier_id=supplier_id,
        invoice_id=invoice_id,
        source=source,
        quantity_initial=quantity,
        quantity_remaining=quantity,
        unit_cost=unit_cost,
        expiry_date=expiry_date,
        received_at=when,
        note=reference,
    )
    session.add(batch)
    session.flush()
    session.add(
        Movement(
            ingredient_id=ingredient.id,
            batch_id=batch.id,
            kind="IN" if source == "purchase" else "ADJUST",
            quantity=quantity,
            unit_cost=unit_cost,
            reference=reference,
            created_at=when,
        )
    )
    if source == "purchase" and unit_cost > 0:
        ingredient.last_price = unit_cost
    return batch


def consume(
    session: Session,
    ingredient: Ingredient,
    quantity: float,
    kind: str,
    reference: str = "",
    when: datetime | None = None,
) -> ConsumeResult:
    """Xuất kho: lô nào hết hạn sớm nhất xuất trước (FEFO), cùng hạn thì nhập trước xuất trước."""
    if quantity <= 0:
        raise ValueError("Số lượng xuất phải > 0")
    when = when or datetime.now()
    batches = session.scalars(
        select(Batch)
        .where(Batch.ingredient_id == ingredient.id, Batch.quantity_remaining > EPS)
        .order_by(Batch.expiry_date.is_(None), Batch.expiry_date, Batch.received_at, Batch.id)
    ).all()
    remaining = quantity
    cost = 0.0
    for batch in batches:
        if remaining <= EPS:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining = round(batch.quantity_remaining - take, 6)
        remaining -= take
        cost += take * batch.unit_cost
        session.add(
            Movement(
                ingredient_id=ingredient.id,
                batch_id=batch.id,
                kind=kind,
                quantity=-take,
                unit_cost=batch.unit_cost,
                reference=reference,
                created_at=when,
            )
        )
    shortfall = max(remaining, 0.0) if remaining > EPS else 0.0
    if shortfall:
        # Ghi nhận phần thiếu để không mất dấu (giá theo giá nhập gần nhất).
        cost += shortfall * ingredient.last_price
        session.add(
            Movement(
                ingredient_id=ingredient.id,
                batch_id=None,
                kind=kind,
                quantity=-shortfall,
                unit_cost=ingredient.last_price,
                reference=(reference + " (thiếu hàng)").strip(),
                created_at=when,
            )
        )
    return ConsumeResult(quantity=quantity, cost=cost, shortfall=shortfall)


def record_sale(session: Session, dish: Dish, quantity: int, when: datetime | None = None) -> tuple[Sale, list[str]]:
    """Bán món: trừ kho theo định lượng (BOM). Trả về (sale, cảnh báo thiếu hàng)."""
    warnings: list[str] = []
    cost = 0.0
    ref = f"Bán {quantity} × {dish.name}"
    for item in dish.items:
        res = consume(session, item.ingredient, item.quantity * quantity, "SALE", ref, when)
        cost += res.cost
        if res.shortfall:
            warnings.append(
                f"{item.ingredient.name}: thiếu {fmt_qty(res.shortfall)} {item.ingredient.unit}"
            )
    sale = Sale(
        dish_id=dish.id,
        quantity=quantity,
        revenue=dish.price * quantity,
        cost=cost,
        sold_at=when or datetime.now(),
    )
    session.add(sale)
    return sale, warnings


def dish_cost(dish: Dish) -> float:
    return sum(item.quantity * item.ingredient.last_price for item in dish.items)


def apply_stocktake(session: Session, counts: dict[int, float], note: str = "") -> Stocktake:
    """Kiểm kê: so sánh tồn lý thuyết với thực tế và điều chỉnh kho."""
    stocktake = Stocktake(note=note)
    session.add(stocktake)
    session.flush()
    ref = f"Kiểm kê #{stocktake.id}"
    for ing_id, counted in counts.items():
        ingredient = session.get(Ingredient, ing_id)
        if ingredient is None:
            continue
        expected = stock_of(session, ing_id)
        unit_cost = _avg_cost(session, ing_id) or ingredient.last_price
        stocktake.lines.append(
            StocktakeLine(
                ingredient_id=ing_id, expected=expected, counted=counted, unit_cost=unit_cost
            )
        )
        diff = counted - expected
        if diff > EPS:
            receive(session, ingredient, diff, unit_cost, reference=ref, source="adjust")
        elif diff < -EPS:
            consume(session, ingredient, -diff, "ADJUST", ref)
    return stocktake


def _avg_cost(session: Session, ingredient_id: int) -> float:
    row = session.execute(
        select(func.sum(Batch.quantity_remaining * Batch.unit_cost), func.sum(Batch.quantity_remaining)).where(
            Batch.ingredient_id == ingredient_id, Batch.quantity_remaining > EPS
        )
    ).one()
    value, qty = row
    return float(value / qty) if qty else 0.0


def low_stock(session: Session) -> list[tuple[Ingredient, float]]:
    stocks = stock_map(session)
    items = session.scalars(select(Ingredient).where(Ingredient.active == 1).order_by(Ingredient.name)).all()
    return [(i, stocks.get(i.id, 0.0)) for i in items if i.min_stock > 0 and stocks.get(i.id, 0.0) < i.min_stock]


def expiring_batches(session: Session, days: int) -> list[Batch]:
    limit = date.today() + timedelta(days=days)
    return list(
        session.scalars(
            select(Batch)
            .where(Batch.quantity_remaining > EPS, Batch.expiry_date.is_not(None), Batch.expiry_date <= limit)
            .order_by(Batch.expiry_date)
        ).all()
    )


def write_off_expired(session: Session, when: datetime | None = None) -> list[tuple[Ingredient, float]]:
    """Huỷ toàn bộ lô đã quá hạn sử dụng, ghi nhận là hao hụt."""
    when = when or datetime.now()
    expired = session.scalars(
        select(Batch).where(
            Batch.quantity_remaining > EPS, Batch.expiry_date.is_not(None), Batch.expiry_date < when.date()
        )
    ).all()
    result = []
    for batch in expired:
        qty = batch.quantity_remaining
        batch.quantity_remaining = 0.0
        session.add(
            Movement(
                ingredient_id=batch.ingredient_id,
                batch_id=batch.id,
                kind="WASTE",
                quantity=-qty,
                unit_cost=batch.unit_cost,
                reference=f"Huỷ lô hết hạn {batch.expiry_date:%d.%m.%Y}",
                created_at=when,
            )
        )
        result.append((batch.ingredient, qty))
    return result


def fmt_qty(value: float) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}"
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")
