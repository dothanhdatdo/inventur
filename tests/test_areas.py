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


def test_guess_area_food_lookalikes_and_kitchen_categories():
    assert areas.guess_area("Schwein") == areas.KITCHEN  # "-wein" nhưng là thịt heo
    assert areas.guess_area("Hackfleisch Schwein gemischt") == areas.KITCHEN
    assert areas.guess_area("Shaoxing Reiswein") == areas.KITCHEN  # rượu nấu ăn
    assert areas.guess_area("Zitronensaft") == areas.KITCHEN
    assert areas.guess_area("Rượu nấu ăn / Kochwein") == areas.KITCHEN
    assert areas.guess_area("Bier zum Kochen", category="Gia vị & sốt") == areas.KITCHEN  # nhóm bếp thắng
    assert areas.guess_area("Pflaumenwein") == areas.BAR


def test_migrate_dish_backfill_uses_drink_names(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name VARCHAR(200), category VARCHAR(100), unit VARCHAR(20))"))
        conn.execute(text("CREATE TABLE dishes (id INTEGER PRIMARY KEY, code VARCHAR(20), name VARCHAR(200), price FLOAT)"))
        conn.execute(text("CREATE TABLE recipe_items (id INTEGER PRIMARY KEY, dish_id INTEGER, ingredient_id INTEGER, quantity FLOAT)"))
        conn.execute(text("INSERT INTO ingredients VALUES (1,'Kaffeebohnen','Khác','kg'),(2,'Milch','Khác','l'),(3,'Schwein','Khác','kg')"))
        conn.execute(text("INSERT INTO dishes VALUES (1,'','Milchkaffee',3.9),(2,'','Apfelschorle 0,4l',3.5),"
                          "(3,'','Schweinebraten',14),(4,'','Mì xào',9)"))
        conn.execute(text("INSERT INTO recipe_items VALUES (1,1,1,0.009),(2,1,2,0.15),(3,3,3,0.2)"))
    migrate(engine)
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT name, area FROM ingredients")).all()) == {
            "Kaffeebohnen": "bar", "Milch": "kitchen", "Schwein": "kitchen"}
        assert dict(conn.execute(text("SELECT name, area FROM dishes")).all()) == {
            "Milchkaffee": "bar",  # một thành phần quầy + tên đồ uống
            "Apfelschorle 0,4l": "bar",  # chưa có định lượng nhưng tên là đồ uống
            "Schweinebraten": "kitchen", "Mì xào": "kitchen"}


def test_par_level_below_minimum_is_ignored(db):
    beef, coke, water = _two_areas(db)
    coke.par_level = 10  # cài nhầm: thấp hơn ngưỡng 48
    db.commit()
    assert stock.par_target(coke) == 96
    lines = {l.ingredient.name: l for _, ls in stock.order_suggestions(db, "bar") for l in ls}
    assert lines["Coca-Cola 0,33l"].urgent and lines["Coca-Cola 0,33l"].quantity >= 48 - 30


def test_order_message_uses_german_units():
    from app.main import _order_message

    beef = Ingredient(name="Thịt bò / Rinderhüfte", unit="kg", pack_unit="", pack_size=1)
    coke = Ingredient(name="Coca-Cola 0,33l", unit="lon", pack_unit="thùng", pack_size=24)
    water = Ingredient(name="Nước suối / Mineralwasser", unit="chai", pack_unit="két", pack_size=12)
    msg = _order_message([
        stock.OrderLine(coke, 0, 96, 96, 4, True),
        stock.OrderLine(water, 0, 12, 12, 1, False),
        stock.OrderLine(beef, 1, 8, 7, None, True),
    ])
    assert "4 × Kartons Coca-Cola 0,33l (à 24 Dosen)" in msg
    assert "1 × Kiste Mineralwasser (à 12 Flaschen)" in msg
    assert "7 kg Rinderhüfte" in msg
    assert "thùng" not in msg and "két" not in msg and "lon" not in msg.replace("Kartons", "")


def test_guess_area_canned_food_nonfood_plurals_and_cooking_alcohol_with_vat():
    for name in ("Thunfisch in Wasser", "Ananas in Saft 3kg", "Mais in Salzwasser", "Bambussprossen in Wasser"):
        assert areas.guess_area(name, vat_rate=7) == areas.KITCHEN, name
    for name in ("Servietten 3-lagig", "Einmalhandschuhe Nitril", "Müllsäcke 120l", "Allzweckreiniger 5l", "Küchenrolle 8 Rollen"):
        assert areas.guess_area(name, vat_rate=19) == areas.KITCHEN, name
    assert areas.guess_area("Shaoxing Kochwein 0,75l", vat_rate=19) == areas.KITCHEN
    assert areas.guess_area("Zitronensaft 1l", vat_rate=19) == areas.KITCHEN
    assert areas.guess_area("Orangensaft 1l", vat_rate=19) == areas.BAR
    assert areas.guess_area("Mineralwasser 12x0,75l", vat_rate=19) == areas.BAR


def test_order_suggestions_never_fractional_for_countable_units(db):
    paper = Ingredient(name="Bánh tráng / Reispapier", unit="gói", min_stock=8, area="kitchen", last_price=1.6)
    herbs = Ingredient(name="Rau mùi / Koriander", unit="bó", min_stock=8, par_level=16, area="kitchen", last_price=0.85)
    beef = Ingredient(name="Thịt bò", unit="kg", min_stock=4, area="kitchen", last_price=18)
    db.add_all([paper, herbs, beef])
    db.flush()
    stock.receive(db, paper, 3.3, 1.6)
    stock.receive(db, herbs, 5.7, 0.85)
    stock.receive(db, beef, 3.12, 18)
    db.commit()
    lines = {l.ingredient.name: l for _, ls in stock.order_suggestions(db, "kitchen") for l in ls}
    assert lines["Bánh tráng / Reispapier"].quantity == 13  # 16 - 3,3 = 12,7 -> 13 gói
    assert lines["Rau mùi / Koriander"].quantity == 11  # 16 - 5,7 = 10,3 -> 11 bó
    assert lines["Thịt bò"].quantity == 4.9  # 8 - 3,12 = 4,88 -> 4,9 kg


def test_par_ignored_flag(db):
    beef, coke, water = _two_areas(db)
    coke.par_level = 10
    assert stock.par_ignored(coke) and stock.par_target(coke) == 96
    coke.par_level = 0
    assert not stock.par_ignored(coke)


def test_migrate_uses_invoice_vat_for_brand_named_drinks(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name VARCHAR(200), category VARCHAR(100), unit VARCHAR(20))"))
        conn.execute(text("CREATE TABLE invoice_lines (id INTEGER PRIMARY KEY, invoice_id INTEGER, ingredient_id INTEGER, vat_rate FLOAT)"))
        conn.execute(text("INSERT INTO ingredients VALUES (1,'0,33 MW ROTHAUS TANNENZAEPFLE','Mới từ hoá đơn','chai'),"
                          "(2,'Bambussprossen in Wasser 540g','Mới từ hoá đơn','cái'),(3,'Servietten 1000 Stk','Mới từ hoá đơn','cái')"))
        conn.execute(text("INSERT INTO invoice_lines VALUES (1,1,1,19),(2,1,2,7),(3,1,3,19)"))
    migrate(engine)
    with engine.connect() as conn:
        assert dict(conn.execute(text("SELECT id, area FROM ingredients")).all()) == {1: "bar", 2: "kitchen", 3: "kitchen"}
