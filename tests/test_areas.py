from sqlalchemy import create_engine, inspect, select, text

from app import areas
from app.db import Dish, Ingredient, RecipeItem, Supplier, migrate
from app.services import alerts, stock


def test_guess_area():
    assert areas.guess_area("Coca-Cola 24x0,33l Dose") == areas.BAR
    assert areas.guess_area("Mineralwasser 12x0,75l") == areas.BAR
    assert areas.guess_area("Weißwein Riesling 0,75l") == areas.BAR
    assert areas.guess_area("Tiger Bier 24x0,33") == areas.BAR
    assert areas.guess_area("Tomaten passiert") == areas.KITCHEN  # "mate" không được khớp nhầm
    assert areas.guess_area("Rumpsteak") == areas.KITCHEN
    assert areas.guess_area("Wassermelone") == areas.KITCHEN
    assert areas.guess_area("Weinessig") == areas.KITCHEN
    assert areas.guess_area("Pak Choi", vat_rate=7) == areas.KITCHEN
    assert areas.guess_area("Unbekannter Artikel", vat_rate=19) == areas.BAR  # VAT 19 % ~ đồ uống
    assert areas.guess_area("Menübox 300 Stk", vat_rate=19) == areas.KITCHEN  # bao bì vẫn thuộc bếp
    assert areas.guess_area("X", category="Đồ uống") == areas.BAR


def test_migrate_old_database_adds_columns_and_backfills(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:  # lược đồ của bản trước khi có khu vực
        conn.execute(text(
            "CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name VARCHAR(200) UNIQUE, category VARCHAR(100), "
            "unit VARCHAR(20), pack_unit VARCHAR(30), pack_size FLOAT, min_stock FLOAT, last_price FLOAT, "
            "supplier_id INTEGER, active INTEGER, alert_sent INTEGER)"))
        conn.execute(text("CREATE TABLE dishes (id INTEGER PRIMARY KEY, code VARCHAR(20), name VARCHAR(200), price FLOAT)"))
        conn.execute(text("CREATE TABLE recipe_items (id INTEGER PRIMARY KEY, dish_id INTEGER, ingredient_id INTEGER, quantity FLOAT)"))
        conn.execute(text("CREATE TABLE stocktakes (id INTEGER PRIMARY KEY, created_at DATETIME, note VARCHAR(300))"))
        conn.execute(text("INSERT INTO ingredients VALUES (1,'Coca-Cola 0,33l','Đồ uống','lon','thùng',24,48,0.7,NULL,1,0),"
                          "(2,'Thịt bò','Thịt & hải sản','kg','',1,4,18,NULL,1,0),(3,'Bia Tiger','Khác','chai','',1,0,1,NULL,1,0)"))
        conn.execute(text("INSERT INTO dishes VALUES (1,'G1','Cola',3.5),(2,'D1','Phở',14.9)"))
        conn.execute(text("INSERT INTO recipe_items VALUES (1,1,1,1),(2,2,2,0.1)"))
        conn.execute(text("INSERT INTO stocktakes VALUES (1,'2026-01-01 10:00:00','cũ')"))
    added = migrate(engine)
    assert {"ingredients.area", "ingredients.par_level", "dishes.area", "stocktakes.area"} <= set(added)
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT name, area FROM ingredients")).all())
        assert rows == {"Coca-Cola 0,33l": "bar", "Thịt bò": "kitchen", "Bia Tiger": "bar"}
        assert dict(conn.execute(text("SELECT name, area FROM dishes")).all()) == {"Cola": "bar", "Phở": "kitchen"}
        assert conn.execute(text("SELECT par_level FROM ingredients WHERE id=2")).scalar() == 0
        assert conn.execute(text("SELECT area FROM stocktakes")).scalar() == ""
    assert migrate(engine) == []  # chạy lại không làm gì
    assert "area" in {c["name"] for c in inspect(engine).get_columns("ingredients")}


def _two_areas(db):
    sup = Supplier(name="Getränke Breisgau")
    db.add(sup)
    db.flush()
    beef = Ingredient(name="Thịt bò", unit="kg", min_stock=4, last_price=18, area="kitchen")
    coke = Ingredient(name="Coca-Cola 0,33l", unit="lon", pack_unit="thùng", pack_size=24, min_stock=48,
                      par_level=96, last_price=0.7, area="bar", supplier_id=sup.id)
    water = Ingredient(name="Nước suối / Mineralwasser", unit="chai", pack_unit="két", pack_size=12, min_stock=12,
                       par_level=48, last_price=0.45, area="bar", supplier_id=sup.id)
    db.add_all([beef, coke, water])
    db.flush()
    stock.receive(db, beef, 10, 18)
    stock.receive(db, coke, 30, 0.7)
    stock.receive(db, water, 20, 0.45)
    db.commit()
    return beef, coke, water


def test_area_filters_and_values(db):
    beef, coke, water = _two_areas(db)
    assert [i.name for i, _ in stock.low_stock(db)] == ["Coca-Cola 0,33l"]
    assert stock.low_stock(db, "kitchen") == []
    assert abs(stock.stock_value(db, "kitchen") - 180) < 1e-9
    assert abs(stock.stock_value(db, "bar") - (21 + 9)) < 1e-9
    cats = stock.stock_value_by_category(db, "bar")
    assert {a for a, _, _ in cats} == {"bar"}


def test_order_suggestions_round_to_packs_and_par(db):
    beef, coke, water = _two_areas(db)
    groups = dict(stock.order_suggestions(db, "bar"))
    lines = {l.ingredient.name: l for l in groups["Getränke Breisgau"]}
    # Coca: 30 < 48 (gấp), lên tới 96 -> thiếu 66 -> 3 thùng = 72 lon
    assert lines["Coca-Cola 0,33l"].urgent and lines["Coca-Cola 0,33l"].packs == 3 and lines["Coca-Cola 0,33l"].quantity == 72
    # Nước: 20 >= 12 nhưng dưới mức chuẩn 48 -> bổ sung 28 -> 3 két
    assert not lines["Nước suối / Mineralwasser"].urgent and lines["Nước suối / Mineralwasser"].packs == 3
    assert stock.order_suggestions(db, "kitchen") == []


def test_alert_emails_go_to_each_area(db, monkeypatch):
    beef, coke, water = _two_areas(db)
    alerts.set_setting(db, "alert_email", "chu@example.com")
    alerts.set_setting(db, "alert_email_bar", "bar@example.com")
    db.commit()
    alerts.check_low_stock(db)  # Coca đang thiếu -> coi như đã báo
    db.commit()
    sent = []
    monkeypatch.setattr(alerts, "smtp_configured", lambda: True)
    monkeypatch.setattr(alerts, "send_email", lambda to, subj, text, html: (sent.append((to, subj, text)) or ("sent", "")))
    stock.consume(db, beef, 7, "USE")
    alerts.check_and_notify(db, background=False)
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == ["chu@example.com"] and "Bếp" in subject and "Thịt bò" in body and "Coca" not in body

    stock.consume(db, water, 15, "USE")
    alerts.check_and_notify(db, background=False)
    to, subject, body = sent[-1]
    assert to == ["bar@example.com"] and "Quầy" in subject and "Mineralwasser" in body and "Coca-Cola" in body

    # Gửi báo cáo cả hai khu: hai email, mỗi khu một người nhận.
    entries = alerts.notify(db, stock.low_stock(db))
    assert sorted(e.recipient for e in entries) == ["bar@example.com", "chu@example.com"]


def test_same_recipients_are_merged_into_one_email(db):
    beef, coke, water = _two_areas(db)
    stock.consume(db, beef, 7, "USE")
    db.commit()
    plans = alerts.plan_emails(db, stock.low_stock(db))
    assert len(plans) == 1 and plans[0][1] is None  # một email, cả hai khu
    subject, text_body, _ = alerts.build_email(plans[0][2], area=None)
    assert "Bếp · Küche" in text_body and "Quầy · Theke" in text_body
