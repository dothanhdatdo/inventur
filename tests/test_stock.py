from datetime import date, datetime, timedelta

from app.db import Dish, Ingredient, RecipeItem
from app.services import stock


def make(db, name="Thịt bò / Rinderhüfte", unit="kg", min_stock=0.0, price=10.0):
    ing = Ingredient(name=name, unit=unit, min_stock=min_stock, last_price=price)
    db.add(ing)
    db.flush()
    return ing


def test_fefo_consumes_earliest_expiry_first(db):
    ing = make(db)
    today = date.today()
    late = stock.receive(db, ing, 5, 10.0, expiry_date=today + timedelta(days=10), received_at=datetime(2026, 1, 1))
    early = stock.receive(db, ing, 5, 12.0, expiry_date=today + timedelta(days=2), received_at=datetime(2026, 1, 2))
    res = stock.consume(db, ing, 6, "USE")
    assert early.quantity_remaining == 0
    assert late.quantity_remaining == 4
    assert res.cost == 5 * 12.0 + 1 * 10.0
    assert stock.stock_of(db, ing.id) == 4


def test_shortfall_is_reported(db):
    ing = make(db, price=8.0)
    stock.receive(db, ing, 2, 8.0)
    res = stock.consume(db, ing, 3, "USE")
    assert res.shortfall == 1
    assert stock.stock_of(db, ing.id) == 0


def test_sale_deducts_by_recipe(db):
    pho = make(db, "Bánh phở", price=4.0)
    beef = make(db, "Thịt bò", price=18.0)
    stock.receive(db, pho, 10, 4.0)
    stock.receive(db, beef, 10, 18.0)
    dish = Dish(name="Phở bò", price=14.9)
    dish.items = [RecipeItem(ingredient_id=pho.id, quantity=0.15), RecipeItem(ingredient_id=beef.id, quantity=0.1)]
    db.add(dish)
    db.flush()
    sale, warnings = stock.record_sale(db, dish, 10)
    assert not warnings
    assert abs(stock.stock_of(db, pho.id) - 8.5) < 1e-9
    assert abs(stock.stock_of(db, beef.id) - 9.0) < 1e-9
    assert abs(sale.cost - (1.5 * 4 + 1.0 * 18)) < 1e-9
    assert abs(sale.revenue - 149.0) < 1e-9


def test_stocktake_adjusts_and_reports_loss(db):
    ing = make(db, price=5.0)
    stock.receive(db, ing, 10, 5.0)
    st = stock.apply_stocktake(db, {ing.id: 7})
    line = st.lines[0]
    assert line.expected == 10 and line.counted == 7
    assert line.variance_value == -15.0
    assert stock.stock_of(db, ing.id) == 7


def test_write_off_expired(db):
    ing = make(db)
    stock.receive(db, ing, 3, 10.0, expiry_date=date.today() - timedelta(days=1))
    stock.receive(db, ing, 2, 10.0, expiry_date=date.today() + timedelta(days=5))
    removed = stock.write_off_expired(db)
    assert len(removed) == 1
    assert stock.stock_of(db, ing.id) == 2
