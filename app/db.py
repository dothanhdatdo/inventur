"""Mô hình dữ liệu (SQLAlchemy 2)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config

connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class Ingredient(Base):
    __tablename__ = "ingredients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    category: Mapped[str] = mapped_column(String(100), default="Khác")
    unit: Mapped[str] = mapped_column(String(20), default="kg")  # đơn vị cơ sở trong kho
    pack_unit: Mapped[str] = mapped_column(String(30), default="")  # đơn vị mua, vd "thùng"
    pack_size: Mapped[float] = mapped_column(Float, default=1.0)  # 1 pack_unit = pack_size unit
    min_stock: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)  # giá / đơn vị cơ sở
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    alert_sent: Mapped[int] = mapped_column(Integer, default=0)  # đã gửi mail cảnh báo sắp hết
    area: Mapped[str] = mapped_column(String(20), default="kitchen", server_default="kitchen")  # kitchen | bar
    # Mức tồn chuẩn (Par-Bestand): đặt hàng lên tới mức này. 0 = gấp đôi ngưỡng tối thiểu.
    par_level: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    supplier: Mapped[Supplier | None] = relationship()
    batches: Mapped[list["Batch"]] = relationship(back_populates="ingredient")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    supplier_name_raw: Mapped[str] = mapped_column(String(200), default="")
    invoice_number: Mapped[str] = mapped_column(String(100), default="")
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    file_path: Mapped[str] = mapped_column(String(300), default="")
    file_type: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | confirmed
    source: Mapped[str] = mapped_column(String(20), default="manual")  # anthropic|openai|demo|manual
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    vat: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    raw_name: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit_raw: Mapped[str] = mapped_column(String(30), default="")
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    line_total: Mapped[float] = mapped_column(Float, default=0.0)
    vat_rate: Mapped[float] = mapped_column(Float, default=0.0)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), nullable=True)
    pack_factor: Mapped[float] = mapped_column(Float, default=1.0)  # đơn vị cơ sở / 1 đơn vị trên HĐ
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)  # -1 = trùng hoá đơn đã nhập kho
    category_hint: Mapped[str] = mapped_column(String(100), default="", server_default="")
    units_per_pack: Mapped[float | None] = mapped_column(Float, nullable=True)  # số cái / đơn vị HĐ nếu HĐ ghi (METRO)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient | None] = relationship()


class SupplierAlias(Base):
    """Bộ nhớ "mapping": tên hàng trên HĐ của NCC -> nguyên liệu trong kho."""

    __tablename__ = "supplier_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    alias: Mapped[str] = mapped_column(String(300), index=True)  # tên đã chuẩn hoá
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    pack_factor: Mapped[float] = mapped_column(Float, default=1.0)

    ingredient: Mapped[Ingredient] = relationship()


class Batch(Base):
    """Lô hàng: dùng cho FIFO/FEFO và hạn sử dụng."""

    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="purchase")  # purchase | adjust
    quantity_initial: Mapped[float] = mapped_column(Float)
    quantity_remaining: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    note: Mapped[str] = mapped_column(String(300), default="")

    ingredient: Mapped[Ingredient] = relationship(back_populates="batches")
    supplier: Mapped[Supplier | None] = relationship()


class Movement(Base):
    """Nhật ký xuất nhập. quantity > 0 là nhập, < 0 là xuất."""

    __tablename__ = "movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))  # IN | USE | WASTE | SALE | ADJUST
    quantity: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    reference: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    ingredient: Mapped[Ingredient] = relationship()


class Dish(Base):
    __tablename__ = "dishes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(String(200), unique=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    area: Mapped[str] = mapped_column(String(20), default="kitchen", server_default="kitchen")  # món bếp | đồ uống quầy

    items: Mapped[list["RecipeItem"]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )


class RecipeItem(Base):
    __tablename__ = "recipe_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    quantity: Mapped[float] = mapped_column(Float)  # theo đơn vị cơ sở của nguyên liệu

    dish: Mapped[Dish] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship()


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[int] = mapped_column(primary_key=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)  # giá vốn theo FIFO
    sold_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    dish: Mapped[Dish] = relationship()


class Stocktake(Base):
    __tablename__ = "stocktakes"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    note: Mapped[str] = mapped_column(String(300), default="")
    area: Mapped[str] = mapped_column(String(20), default="", server_default="")  # "" = cả hai khu

    lines: Mapped[list["StocktakeLine"]] = relationship(
        back_populates="stocktake", cascade="all, delete-orphan"
    )


class StocktakeLine(Base):
    __tablename__ = "stocktake_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    stocktake_id: Mapped[int] = mapped_column(ForeignKey("stocktakes.id"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    expected: Mapped[float] = mapped_column(Float)
    counted: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float, default=0.0)

    stocktake: Mapped[Stocktake] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient] = relationship()

    @property
    def variance(self) -> float:
        return self.counted - self.expected

    @property
    def variance_value(self) -> float:
        return self.variance * self.unit_cost


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AlertLog(Base):
    __tablename__ = "alert_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    recipient: Mapped[str] = mapped_column(String(300), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="")  # sent | failed | not_configured
    detail: Mapped[str] = mapped_column(Text, default="")


def init_db() -> None:
    Base.metadata.create_all(engine)
    migrate()


def migrate(bind=None) -> list[str]:
    """Thêm các cột mới vào database cũ (vd. dữ liệu đã lưu trong trình duyệt) mà không mất dữ liệu.

    create_all() chỉ tạo bảng còn thiếu, không thêm cột -> tự ALTER TABLE cho từng cột mới.
    """
    from sqlalchemy import inspect, text

    bind = bind or engine
    added: list[str] = []
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=bind.dialect)
                default = column.server_default.arg if column.server_default is not None else None
                ddl = f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'
                if default is not None:
                    ddl += f" DEFAULT '{default}'" if isinstance(default, str) and not default.replace(".", "").isdigit() else f" DEFAULT {default}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
        if "ingredients.area" in added:
            _backfill_areas(conn)
    return added


def _backfill_areas(conn) -> None:
    """Dữ liệu cũ chưa có khu: nhóm/ tên giống đồ uống -> quầy, còn lại -> bếp."""
    from sqlalchemy import inspect, text

    from .areas import BAR, guess_area

    # VAT đã lưu trên dòng hoá đơn giúp nhận ra đồ uống chỉ có tên thương hiệu (vd. "Rothaus Tannenzäpfle" 19 %).
    vat_by_ingredient = {}
    if inspect(conn).has_table("invoice_lines"):
        vat_by_ingredient = dict(conn.execute(text(
            "SELECT ingredient_id, MAX(vat_rate) FROM invoice_lines WHERE ingredient_id IS NOT NULL GROUP BY ingredient_id"
        )).all())
    for ing_id, name, category in conn.execute(text("SELECT id, name, category FROM ingredients")).all():
        if guess_area(name or "", category or "", vat_by_ingredient.get(ing_id)) == BAR:
            conn.execute(text("UPDATE ingredients SET area = :a WHERE id = :i"), {"a": BAR, "i": ing_id})
    # Món -> quầy nếu: mọi thành phần thuộc quầy; hoặc tên giống đồ uống và (chưa có định lượng
    # hoặc có ít nhất một thành phần thuộc quầy, vd. Milchkaffee = cà phê quầy + sữa).
    from .areas import looks_like_drink

    if not (inspect(conn).has_table("dishes") and inspect(conn).has_table("recipe_items")):
        return
    rows = conn.execute(text(
        "SELECT d.id, d.name, SUM(CASE WHEN i.area = :bar THEN 1 ELSE 0 END), COUNT(i.id) FROM dishes d "
        "LEFT JOIN recipe_items r ON r.dish_id = d.id LEFT JOIN ingredients i ON i.id = r.ingredient_id GROUP BY d.id, d.name"
    ), {"bar": BAR}).all()
    for dish_id, name, bar_count, count in rows:
        bar_count = bar_count or 0
        drink_name = looks_like_drink(name or "")
        if (count and bar_count == count) or (drink_name and (count == 0 or bar_count > 0)):
            conn.execute(text("UPDATE dishes SET area = :a WHERE id = :i"), {"a": BAR, "i": dish_id})


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
