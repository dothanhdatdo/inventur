"""Ứng dụng web quản lý kho nhà hàng (FastAPI + Jinja2)."""
from __future__ import annotations

import mimetypes
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from . import config
from .db import (
    Batch,
    Dish,
    Ingredient,
    Invoice,
    InvoiceLine,
    Movement,
    RecipeItem,
    Sale,
    Stocktake,
    AlertLog,
    Supplier,
    SupplierAlias,
    SessionLocal,
    engine,
    get_session,
    init_db,
)
from .seed import seed
from .services import invoices as invoice_service
from .services import alerts, matching, ocr, stock

APP_DIR = Path(__file__).resolve().parent
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"}
MAX_UPLOAD = 15 * 1024 * 1024
MOVEMENT_LABELS = {
    "IN": "Nhập hàng",
    "USE": "Xuất dùng",
    "WASTE": "Hao hụt/hỏng",
    "SALE": "Bán món",
    "ADJUST": "Điều chỉnh kiểm kê",
}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if config.SEED_DEMO:
        with SessionLocal() as session:
            seed(session)
    yield


app = FastAPI(title="Kho Nhà Hàng", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 14)
if not config.IS_BROWSER:  # bản trình duyệt nạp static trực tiếp từ GitHub Pages
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def _money(value) -> str:
    value = value or 0.0
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {config.CURRENCY}"


def _date(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%d.%m.%Y")


templates.env.filters["money"] = _money
templates.env.filters["qty"] = stock.fmt_qty
templates.env.filters["vdate"] = _date
templates.env.globals["movement_labels"] = MOVEMENT_LABELS
templates.env.globals["ocr_labels"] = {
    "anthropic": "Claude", "openai": "GPT-4o", "gemini": "Gemini", "demo": "Demo", "manual": "Thủ công",
}
templates.env.globals["today"] = date.today


# ---------------------------------------------------------------- tiện ích


def render(request: Request, name: str, **ctx):
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx.setdefault("alert_flash", request.session.pop("alert_flash", None))
    with SessionLocal() as session:
        ocr_settings = ocr.get_settings(session)
        ctx["ocr_provider"] = ocr.active_provider(ocr_settings)
        ctx["ocr_ready"] = ocr.provider_ready(ocr_settings)
    ctx["browser_mode"] = config.IS_BROWSER
    ctx["auth_enabled"] = bool(config.APP_PASSWORD)
    ctx["path"] = request.url.path
    ctx["expiry_days"] = config.EXPIRY_WARNING_DAYS
    return templates.TemplateResponse(request, name, ctx)


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def after_stock_change(request: Request, db: Session) -> None:
    """Commit + kiểm tra ngưỡng tồn; gửi email nếu có nguyên liệu vừa xuống dưới ngưỡng."""
    newly = alerts.check_and_notify(db)
    if newly:
        names = ", ".join(i.name for i, _ in newly)
        if not alerts.alerts_enabled(db):
            msg = f"Dưới ngưỡng tồn: {names} (cảnh báo email đang tắt)"
        elif alerts.smtp_configured():
            msg = f"Dưới ngưỡng tồn: {names} — đã gửi email tới {', '.join(alerts.alert_recipients(db))}"
        else:
            msg = f"Dưới ngưỡng tồn: {names} — chưa gửi được email vì chưa cấu hình SMTP (xem Cài đặt)"
        request.session["alert_flash"] = msg


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def parse_float(value, default: float = 0.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    return ocr.to_float(value)


def parse_date(value) -> date | None:
    return ocr.to_date(value) if value else None


@app.middleware("http")
async def require_login(request: Request, call_next):
    if config.APP_PASSWORD:
        path = request.url.path
        if not (path.startswith("/static") or path in ("/login", "/healthz")):
            if not request.session.get("auth"):
                return RedirectResponse(f"/login?next={path}", status_code=303)
    return await call_next(request)


# SessionMiddleware phải bọc ngoài middleware đăng nhập -> thêm lại sau cùng.
app.user_middleware.sort(key=lambda m: 0 if m.cls is SessionMiddleware else 1)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", next=next)


@app.post("/login")
def login(request: Request, password: str = Form(...), next: str = Form("/")):
    if password == config.APP_PASSWORD:
        request.session["auth"] = True
        safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
        return redirect(safe_next)
    flash(request, "Sai mật khẩu", "error")
    return redirect("/login")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# ---------------------------------------------------------------- dashboard


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_session)):
    stocks = stock.stock_map(db)
    week_ago = datetime.now() - timedelta(days=7)
    sales = db.execute(
        select(func.coalesce(func.sum(Sale.revenue), 0), func.coalesce(func.sum(Sale.cost), 0)).where(
            Sale.sold_at >= week_ago
        )
    ).one()
    waste = db.scalar(
        select(func.coalesce(func.sum(-Movement.quantity * Movement.unit_cost), 0)).where(
            Movement.kind.in_(["WASTE", "ADJUST"]), Movement.quantity < 0, Movement.created_at >= week_ago
        )
    )
    recent = db.scalars(
        select(Movement)
        .options(selectinload(Movement.ingredient))
        .where(Movement.kind != "SALE")
        .order_by(Movement.created_at.desc(), Movement.id.desc())
        .limit(10)
    ).all()
    drafts = db.scalars(select(Invoice).where(Invoice.status == "draft").order_by(Invoice.created_at.desc())).all()
    return render(
        request,
        "dashboard.html",
        ingredient_count=db.scalar(select(func.count(Ingredient.id)).where(Ingredient.active == 1)),
        stock_value=stock.stock_value(db),
        low=stock.low_stock(db),
        expiring=stock.expiring_batches(db, config.EXPIRY_WARNING_DAYS),
        revenue=float(sales[0]),
        cogs=float(sales[1]),
        waste=float(waste or 0),
        recent=recent,
        drafts=drafts,
        stocks=stocks,
    )


# ---------------------------------------------------------------- nguyên liệu


def _ingredient_form_ctx(db: Session):
    return {
        "suppliers": db.scalars(select(Supplier).order_by(Supplier.name)).all(),
        "categories": sorted({c for c in db.scalars(select(Ingredient.category)).all() if c}),
        "units": ["kg", "g", "l", "ml", "cái", "quả", "bó", "gói", "hộp", "chai", "lon"],
    }


@app.get("/ingredients", response_class=HTMLResponse)
def ingredient_list(request: Request, q: str = "", category: str = "", status: str = "", db: Session = Depends(get_session)):
    query = select(Ingredient).options(selectinload(Ingredient.supplier)).where(Ingredient.active == 1)
    if category:
        query = query.where(Ingredient.category == category)
    items = db.scalars(query.order_by(Ingredient.category, Ingredient.name)).all()
    if q:
        key = matching.normalize(q)
        items = [i for i in items if key in matching.normalize(i.name)]
    stocks = stock.stock_map(db)
    if status == "low":
        items = [i for i in items if i.min_stock > 0 and stocks.get(i.id, 0) < i.min_stock]
    groups: dict[str, list] = defaultdict(list)
    for i in items:
        groups[i.category].append(i)
    return render(
        request,
        "ingredients.html",
        groups=dict(groups),
        stocks=stocks,
        q=q,
        category=category,
        status=status,
        **_ingredient_form_ctx(db),
    )


@app.get("/ingredients/new", response_class=HTMLResponse)
def ingredient_new(request: Request, name: str = "", db: Session = Depends(get_session)):
    return render(request, "ingredient_form.html", item=None, prefill_name=name, **_ingredient_form_ctx(db))


def _fill_ingredient(item: Ingredient, form) -> None:
    item.name = form.get("name", "").strip()
    item.category = form.get("category", "").strip() or "Khác"
    item.unit = form.get("unit", "").strip() or "kg"
    item.pack_unit = form.get("pack_unit", "").strip()
    item.pack_size = parse_float(form.get("pack_size"), 1.0) or 1.0
    item.min_stock = parse_float(form.get("min_stock"))
    item.last_price = parse_float(form.get("last_price"), item.last_price or 0.0)
    sup = form.get("supplier_id")
    item.supplier_id = int(sup) if sup else None


@app.post("/ingredients/new")
async def ingredient_create(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    item = Ingredient()
    _fill_ingredient(item, form)
    if not item.name:
        flash(request, "Tên nguyên liệu không được để trống", "error")
        return redirect("/ingredients/new")
    if db.scalar(select(Ingredient.id).where(Ingredient.name == item.name)):
        flash(request, "Đã có nguyên liệu cùng tên", "error")
        return redirect("/ingredients/new")
    db.add(item)
    db.flush()
    opening = parse_float(form.get("opening_stock"))
    if opening > 0:
        stock.receive(db, item, opening, item.last_price, reference="Tồn đầu kỳ", source="adjust")
    after_stock_change(request, db)
    flash(request, f"Đã thêm {item.name}")
    back = form.get("back")
    return redirect(back if back and back.startswith("/") else f"/ingredients/{item.id}")


@app.get("/ingredients/{item_id}", response_class=HTMLResponse)
def ingredient_detail(request: Request, item_id: int, db: Session = Depends(get_session)):
    item = db.get(Ingredient, item_id)
    if not item:
        raise HTTPException(404)
    batches = db.scalars(
        select(Batch)
        .options(selectinload(Batch.supplier))
        .where(Batch.ingredient_id == item_id, Batch.quantity_remaining > stock.EPS)
        .order_by(Batch.expiry_date.is_(None), Batch.expiry_date, Batch.received_at)
    ).all()
    movements = db.scalars(
        select(Movement).where(Movement.ingredient_id == item_id).order_by(Movement.created_at.desc(), Movement.id.desc()).limit(40)
    ).all()
    aliases = db.scalars(select(SupplierAlias).where(SupplierAlias.ingredient_id == item_id)).all()
    supplier_names = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    return render(
        request,
        "ingredient_detail.html",
        item=item,
        current=stock.stock_of(db, item_id),
        batches=batches,
        movements=movements,
        aliases=aliases,
        supplier_names=supplier_names,
        price_history=_price_history(db, item_id),
        **_ingredient_form_ctx(db),
    )


def _price_history(db: Session, ingredient_id: int) -> dict:
    rows = db.execute(
        select(Batch.received_at, Batch.unit_cost, Batch.supplier_id)
        .where(Batch.ingredient_id == ingredient_id, Batch.source == "purchase", Batch.unit_cost > 0)
        .order_by(Batch.received_at)
    ).all()
    names = {s.id: s.name for s in db.scalars(select(Supplier)).all()}
    series: dict[str, list] = defaultdict(list)
    for when, cost, sup in rows:
        series[names.get(sup, "Khác")].append({"x": when.strftime("%Y-%m-%d"), "y": round(cost, 4)})
    return dict(series)


@app.post("/ingredients/{item_id}/edit")
async def ingredient_update(request: Request, item_id: int, db: Session = Depends(get_session)):
    item = db.get(Ingredient, item_id)
    if not item:
        raise HTTPException(404)
    form = await request.form()
    _fill_ingredient(item, form)
    after_stock_change(request, db)
    flash(request, "Đã lưu thay đổi")
    return redirect(f"/ingredients/{item_id}")


@app.post("/ingredients/{item_id}/archive")
def ingredient_archive(request: Request, item_id: int, db: Session = Depends(get_session)):
    item = db.get(Ingredient, item_id)
    if not item:
        raise HTTPException(404)
    item.active = 0
    db.commit()
    flash(request, f"Đã ẩn {item.name}")
    return redirect("/ingredients")


@app.post("/ingredients/{item_id}/receive")
def ingredient_receive(
    request: Request,
    item_id: int,
    quantity: str = Form(...),
    unit_cost: str = Form(""),
    in_packs: str = Form(""),
    expiry_date: str = Form(""),
    supplier_id: str = Form(""),
    db: Session = Depends(get_session),
):
    item = db.get(Ingredient, item_id)
    if not item:
        raise HTTPException(404)
    qty = parse_float(quantity)
    cost = parse_float(unit_cost, item.last_price)
    if in_packs and item.pack_size:
        qty *= item.pack_size
        cost = cost / item.pack_size if unit_cost else item.last_price
    if qty <= 0:
        flash(request, "Số lượng phải lớn hơn 0", "error")
        return redirect(f"/ingredients/{item_id}")
    stock.receive(
        db, item, qty, cost,
        supplier_id=int(supplier_id) if supplier_id else item.supplier_id,
        expiry_date=parse_date(expiry_date),
        reference="Nhập thủ công",
    )
    after_stock_change(request, db)
    flash(request, f"Đã nhập {stock.fmt_qty(qty)} {item.unit} {item.name}")
    return redirect(f"/ingredients/{item_id}")


@app.post("/ingredients/{item_id}/consume")
def ingredient_consume(
    request: Request,
    item_id: int,
    quantity: str = Form(...),
    kind: str = Form("USE"),
    note: str = Form(""),
    db: Session = Depends(get_session),
):
    item = db.get(Ingredient, item_id)
    if not item:
        raise HTTPException(404)
    qty = parse_float(quantity)
    if qty <= 0 or kind not in ("USE", "WASTE"):
        flash(request, "Dữ liệu không hợp lệ", "error")
        return redirect(f"/ingredients/{item_id}")
    res = stock.consume(db, item, qty, kind, note or MOVEMENT_LABELS[kind])
    after_stock_change(request, db)
    msg = f"Đã xuất {stock.fmt_qty(qty)} {item.unit} {item.name}"
    if res.shortfall:
        flash(request, msg + f" — cảnh báo: kho thiếu {stock.fmt_qty(res.shortfall)} {item.unit}", "warn")
    else:
        flash(request, msg)
    return redirect(f"/ingredients/{item_id}")


@app.post("/aliases/{alias_id}/delete")
def alias_delete(request: Request, alias_id: int, db: Session = Depends(get_session)):
    alias = db.get(SupplierAlias, alias_id)
    if not alias:
        raise HTTPException(404)
    ing_id = alias.ingredient_id
    db.delete(alias)
    db.commit()
    flash(request, "Đã xoá mapping")
    return redirect(f"/ingredients/{ing_id}")


@app.post("/batches/write-off-expired")
def write_off_expired(request: Request, db: Session = Depends(get_session)):
    removed = stock.write_off_expired(db)
    after_stock_change(request, db)
    if removed:
        flash(request, f"Đã huỷ {len(removed)} lô hết hạn và ghi vào hao hụt.")
    else:
        flash(request, "Không có lô nào đã quá hạn.")
    return redirect("/")


# ---------------------------------------------------------------- nhà cung cấp


@app.get("/suppliers", response_class=HTMLResponse)
def supplier_list(request: Request, db: Session = Depends(get_session)):
    suppliers = db.scalars(select(Supplier).order_by(Supplier.name)).all()
    totals = dict(
        db.execute(
            select(Invoice.supplier_id, func.sum(Invoice.subtotal)).where(Invoice.status == "confirmed").group_by(Invoice.supplier_id)
        ).all()
    )
    counts = dict(
        db.execute(select(Invoice.supplier_id, func.count(Invoice.id)).group_by(Invoice.supplier_id)).all()
    )
    return render(request, "suppliers.html", suppliers=suppliers, totals=totals, counts=counts)


@app.post("/suppliers")
def supplier_save(
    request: Request,
    supplier_id: str = Form(""),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_session),
):
    name = name.strip()
    if not name:
        flash(request, "Tên nhà cung cấp không được để trống", "error")
        return redirect("/suppliers")
    supplier = db.get(Supplier, int(supplier_id)) if supplier_id else Supplier()
    clash = db.scalar(select(Supplier.id).where(Supplier.name == name))
    if clash and clash != supplier.id:
        flash(request, "Đã có nhà cung cấp cùng tên", "error")
        return redirect("/suppliers")
    supplier.name, supplier.phone, supplier.email, supplier.note = name, phone, email, note
    db.add(supplier)
    db.commit()
    flash(request, f"Đã lưu {name}")
    return redirect("/suppliers")


# ---------------------------------------------------------------- hoá đơn


@app.get("/invoices", response_class=HTMLResponse)
def invoice_list(request: Request, db: Session = Depends(get_session)):
    items = db.scalars(
        select(Invoice).options(selectinload(Invoice.supplier), selectinload(Invoice.lines)).order_by(Invoice.created_at.desc())
    ).all()
    return render(request, "invoices.html", invoices=items)


@app.get("/invoices/scan", response_class=HTMLResponse)
def invoice_scan_page(request: Request):
    return render(request, "scan.html")


@app.post("/invoices/scan")
async def invoice_scan(request: Request, file: UploadFile = File(None), db: Session = Depends(get_session)):
    content = await file.read() if file is not None else b""
    if not content:
        flash(request, "Hãy chọn hoặc chụp ảnh hoá đơn", "error")
        return redirect("/invoices/scan")
    if len(content) > MAX_UPLOAD:
        flash(request, "File quá lớn (tối đa 15 MB)", "error")
        return redirect("/invoices/scan")
    media_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in ALLOWED_TYPES:
        flash(request, "Chỉ hỗ trợ JPG, PNG, WEBP hoặc PDF", "error")
        return redirect("/invoices/scan")
    ext = mimetypes.guess_extension(media_type) or ".bin"
    filename = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{ext}"
    (config.UPLOAD_DIR / filename).write_bytes(content)
    try:
        parsed = await ocr.extract_invoice(content, media_type, ocr.get_settings(db))
    except ocr.OcrError as exc:
        # Vẫn giữ ảnh, tạo HĐ trống để nhập tay.
        parsed = ocr.ParsedInvoice(source="manual")
        flash(request, f"Không đọc được hoá đơn bằng AI: {str(exc).rstrip('.')}. Bạn có thể nhập tay các dòng.", "error")
    invoice = invoice_service.create_draft(db, parsed, filename, media_type)
    db.commit()
    if parsed.source == "demo":
        flash(request, "Chế độ DEMO: chưa cấu hình API key nên đang hiển thị dữ liệu hoá đơn mẫu.", "warn")
    elif parsed.source != "manual":
        flash(request, f"AI đã đọc {len(invoice.lines)} dòng. Hãy kiểm tra trước khi nhập kho.")
    return redirect(f"/invoices/{invoice.id}")


@app.post("/invoices/manual")
def invoice_manual(request: Request, db: Session = Depends(get_session)):
    invoice = Invoice(source="manual", invoice_date=date.today())
    db.add(invoice)
    db.commit()
    return redirect(f"/invoices/{invoice.id}")


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_review(request: Request, invoice_id: int, db: Session = Depends(get_session)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404)
    ingredients = db.scalars(select(Ingredient).where(Ingredient.active == 1).order_by(Ingredient.category, Ingredient.name)).all()
    by_cat: dict[str, list] = defaultdict(list)
    for i in ingredients:
        by_cat[i.category].append(i)
    ing_json = {
        i.id: {"unit": i.unit, "pack_unit": i.pack_unit, "pack_size": i.pack_size, "last_price": i.last_price}
        for i in ingredients
    }
    return render(
        request,
        "invoice_review.html",
        invoice=invoice,
        ingredients_by_cat=dict(by_cat),
        ing_json=ing_json,
        suppliers=db.scalars(select(Supplier).order_by(Supplier.name)).all(),
    )


@app.get("/invoices/{invoice_id}/file")
def invoice_file(invoice_id: int, db: Session = Depends(get_session)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice or not invoice.file_path:
        raise HTTPException(404)
    path = (config.UPLOAD_DIR / Path(invoice.file_path).name).resolve()
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=invoice.file_type or None)


async def _save_invoice_form(request: Request, db: Session, invoice: Invoice) -> None:
    form = await request.form()
    sup = form.get("supplier_id", "")
    invoice.supplier_id = int(sup) if sup.isdigit() else None
    invoice.supplier_name_raw = form.get("supplier_name_raw", "").strip()
    if invoice.supplier_id:
        invoice.supplier_name_raw = db.get(Supplier, invoice.supplier_id).name
    invoice.invoice_number = form.get("invoice_number", "").strip()
    invoice.invoice_date = parse_date(form.get("invoice_date"))
    invoice.vat = parse_float(form.get("vat"))
    existing = {line.id: line for line in invoice.lines}
    keep: list[InvoiceLine] = []
    for pos, row in enumerate(form.getlist("row")):
        get = lambda field: form.get(f"{field}_{row}", "")  # noqa: E731
        if get("delete"):
            continue
        raw_name = get("raw_name").strip()
        if not raw_name:
            continue
        line_id = get("line_id")
        line = existing.get(int(line_id)) if line_id.isdigit() else None
        if line is None:
            line = InvoiceLine()
            invoice.lines.append(line)
        line.position = pos
        line.raw_name = raw_name
        line.quantity = parse_float(get("quantity"))
        line.unit_raw = get("unit_raw").strip()
        line.unit_price = parse_float(get("unit_price"))
        line.line_total = parse_float(get("line_total")) or round(line.quantity * line.unit_price, 2)
        line.pack_factor = parse_float(get("pack_factor"), 1.0) or 1.0
        line.expiry_date = parse_date(get("expiry_date"))
        choice = get("ingredient_id")
        if choice == "new":
            ing = _create_ingredient_from_line(db, line)
            line.ingredient_id = ing.id
        else:
            line.ingredient_id = int(choice) if choice.isdigit() else None
        keep.append(line)
    for line in list(invoice.lines):
        if line not in keep:
            invoice.lines.remove(line)
    invoice.subtotal = round(sum(line.line_total for line in invoice.lines), 2)
    invoice.total = round(invoice.subtotal + invoice.vat, 2)


def _create_ingredient_from_line(db: Session, line: InvoiceLine) -> Ingredient:
    name = line.raw_name.strip()[:200]
    existing = db.scalars(select(Ingredient).where(Ingredient.name == name)).first()
    if existing:
        existing.active = 1
        return existing
    unit = matching.UNIT_ALIASES.get(matching.normalize(line.unit_raw), "") or "cái"
    ing = Ingredient(name=name, category="Mới từ hoá đơn", unit=unit)
    db.add(ing)
    db.flush()
    line.pack_factor = 1.0
    return ing


@app.post("/invoices/{invoice_id}")
async def invoice_save(request: Request, invoice_id: int, db: Session = Depends(get_session)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404)
    if invoice.status == "confirmed":
        flash(request, "Hoá đơn đã nhập kho, không thể sửa.", "error")
        return redirect(f"/invoices/{invoice_id}")
    await _save_invoice_form(request, db, invoice)
    form = await request.form()
    if form.get("action") == "confirm":
        try:
            notes = invoice_service.confirm(db, invoice)
        except invoice_service.ConfirmError as exc:
            db.commit()
            flash(request, str(exc), "error")
            return redirect(f"/invoices/{invoice_id}")
        after_stock_change(request, db)
        flash(request, " · ".join(notes))
        return redirect(f"/invoices/{invoice_id}")
    db.commit()
    flash(request, "Đã lưu bản nháp")
    return redirect(f"/invoices/{invoice_id}")


@app.post("/invoices/{invoice_id}/delete")
def invoice_delete(request: Request, invoice_id: int, db: Session = Depends(get_session)):
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404)
    if invoice.status == "confirmed":
        flash(request, "Không thể xoá hoá đơn đã nhập kho.", "error")
        return redirect(f"/invoices/{invoice_id}")
    if invoice.file_path:
        (config.UPLOAD_DIR / Path(invoice.file_path).name).unlink(missing_ok=True)
    db.delete(invoice)
    db.commit()
    flash(request, "Đã xoá bản nháp")
    return redirect("/invoices")


# ---------------------------------------------------------------- món ăn & định lượng


@app.get("/recipes", response_class=HTMLResponse)
def recipe_list(request: Request, db: Session = Depends(get_session)):
    dishes = db.scalars(
        select(Dish).options(selectinload(Dish.items).selectinload(RecipeItem.ingredient)).order_by(Dish.code, Dish.name)
    ).all()
    costs = {d.id: stock.dish_cost(d) for d in dishes}
    return render(request, "recipes.html", dishes=dishes, costs=costs)


@app.get("/recipes/{dish_id}", response_class=HTMLResponse)
def recipe_edit(request: Request, dish_id: str, db: Session = Depends(get_session)):
    dish = None if dish_id == "new" else db.get(Dish, int(dish_id))
    if dish_id != "new" and dish is None:
        raise HTTPException(404)
    ingredients = db.scalars(select(Ingredient).where(Ingredient.active == 1).order_by(Ingredient.name)).all()
    return render(
        request,
        "recipe_form.html",
        dish=dish,
        ingredients=ingredients,
        ing_json={i.id: {"unit": i.unit, "price": i.last_price} for i in ingredients},
    )


@app.post("/recipes/{dish_id}")
async def recipe_save(request: Request, dish_id: str, db: Session = Depends(get_session)):
    form = await request.form()
    dish = Dish() if dish_id == "new" else db.get(Dish, int(dish_id))
    if dish is None:
        raise HTTPException(404)
    if form.get("action") == "delete" and dish.id:
        if db.scalar(select(Sale.id).where(Sale.dish_id == dish.id).limit(1)):
            flash(request, "Món đã có lịch sử bán, không thể xoá.", "error")
            return redirect(f"/recipes/{dish.id}")
        db.delete(dish)
        db.commit()
        flash(request, "Đã xoá món")
        return redirect("/recipes")
    name = form.get("name", "").strip()
    if not name:
        flash(request, "Tên món không được để trống", "error")
        return redirect(f"/recipes/{dish_id}")
    clash = db.scalar(select(Dish.id).where(Dish.name == name))
    if clash and clash != dish.id:
        flash(request, "Đã có món cùng tên", "error")
        return redirect(f"/recipes/{dish_id}")
    dish.name = name
    dish.code = form.get("code", "").strip()
    dish.price = parse_float(form.get("price"))
    dish.items.clear()
    db.add(dish)
    db.flush()
    seen = set()
    for ing_id, qty in zip(form.getlist("ingredient_id"), form.getlist("quantity")):
        q = parse_float(qty)
        if ing_id.isdigit() and q > 0 and ing_id not in seen:
            seen.add(ing_id)
            dish.items.append(RecipeItem(ingredient_id=int(ing_id), quantity=q))
    db.commit()
    flash(request, f"Đã lưu định lượng {dish.name}")
    return redirect("/recipes")


# ---------------------------------------------------------------- bán hàng


@app.get("/sales", response_class=HTMLResponse)
def sales_page(request: Request, db: Session = Depends(get_session)):
    dishes = db.scalars(select(Dish).options(selectinload(Dish.items)).order_by(Dish.code, Dish.name)).all()
    recent = db.execute(
        select(func.date(Sale.sold_at), func.sum(Sale.quantity), func.sum(Sale.revenue), func.sum(Sale.cost))
        .group_by(func.date(Sale.sold_at))
        .order_by(func.date(Sale.sold_at).desc())
        .limit(14)
    ).all()
    return render(request, "sales.html", dishes=dishes, recent=recent)


@app.post("/sales")
async def sales_record(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    sold_on = parse_date(form.get("sold_on")) or date.today()
    when = datetime.combine(sold_on, datetime.now().time()) if sold_on != date.today() else datetime.now()
    total, warnings = 0, []
    for key, value in form.items():
        if not key.startswith("dish_"):
            continue
        qty = int(parse_float(value))
        if qty <= 0:
            continue
        dish = db.get(Dish, int(key[5:]))
        if dish is None:
            continue
        _, warn = stock.record_sale(db, dish, qty, when)
        warnings += warn
        total += qty
    if not total:
        flash(request, "Chưa nhập số lượng món nào", "error")
        return redirect("/sales")
    after_stock_change(request, db)
    if warnings:
        flash(request, f"Đã ghi {total} món. Cảnh báo kho thiếu: " + "; ".join(warnings), "warn")
    else:
        flash(request, f"Đã ghi {total} món và trừ kho theo định lượng.")
    return redirect("/sales")


# ---------------------------------------------------------------- kiểm kê


@app.get("/stocktake", response_class=HTMLResponse)
def stocktake_page(request: Request, db: Session = Depends(get_session)):
    ingredients = db.scalars(select(Ingredient).where(Ingredient.active == 1).order_by(Ingredient.category, Ingredient.name)).all()
    groups: dict[str, list] = defaultdict(list)
    for i in ingredients:
        groups[i.category].append(i)
    history = db.scalars(
        select(Stocktake).options(selectinload(Stocktake.lines)).order_by(Stocktake.created_at.desc()).limit(10)
    ).all()
    return render(request, "stocktake.html", groups=dict(groups), stocks=stock.stock_map(db), history=history)


@app.post("/stocktake")
async def stocktake_submit(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    counts = {}
    for key, value in form.items():
        if key.startswith("count_") and str(value).strip() != "":
            counts[int(key[6:])] = parse_float(value)
    if not counts:
        flash(request, "Chưa nhập số kiểm kê nào", "error")
        return redirect("/stocktake")
    st = stock.apply_stocktake(db, counts, form.get("note", ""))
    after_stock_change(request, db)
    flash(request, f"Đã lưu kiểm kê {len(counts)} nguyên liệu và điều chỉnh tồn kho.")
    return redirect(f"/stocktake/{st.id}")


@app.get("/stocktake/{st_id}", response_class=HTMLResponse)
def stocktake_report(request: Request, st_id: int, db: Session = Depends(get_session)):
    st = db.get(Stocktake, st_id)
    if not st:
        raise HTTPException(404)
    lines = sorted(st.lines, key=lambda line: line.variance_value)
    return render(
        request,
        "stocktake_report.html",
        st=st,
        lines=lines,
        loss=sum(line.variance_value for line in lines if line.variance_value < 0),
        gain=sum(line.variance_value for line in lines if line.variance_value > 0),
    )


# ---------------------------------------------------------------- báo cáo


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, days: int = 30, ingredient_id: int | None = None, db: Session = Depends(get_session)):
    days = max(1, min(days, 365))
    since = datetime.now() - timedelta(days=days)
    sales = db.execute(
        select(Dish.name, func.sum(Sale.quantity), func.sum(Sale.revenue), func.sum(Sale.cost))
        .join(Dish, Dish.id == Sale.dish_id)
        .where(Sale.sold_at >= since)
        .group_by(Dish.name)
        .order_by(func.sum(Sale.revenue).desc())
    ).all()
    revenue = sum(r[2] or 0 for r in sales)
    cogs = sum(r[3] or 0 for r in sales)
    purchases = db.scalar(
        select(func.coalesce(func.sum(Batch.quantity_initial * Batch.unit_cost), 0)).where(
            Batch.source == "purchase", Batch.received_at >= since
        )
    )
    losses = db.execute(
        select(Ingredient.name, Movement.kind, func.sum(-Movement.quantity), func.sum(-Movement.quantity * Movement.unit_cost), Ingredient.unit)
        .join(Ingredient, Ingredient.id == Movement.ingredient_id)
        .where(Movement.kind.in_(["WASTE", "ADJUST"]), Movement.quantity < 0, Movement.created_at >= since)
        .group_by(Ingredient.name, Movement.kind, Ingredient.unit)
        .order_by(func.sum(-Movement.quantity * Movement.unit_cost).desc())
    ).all()
    # Biến động giá: so sánh giá nhập đầu và cuối kỳ của từng nguyên liệu.
    rows = db.execute(
        select(Batch.ingredient_id, Batch.received_at, Batch.unit_cost)
        .where(Batch.source == "purchase", Batch.unit_cost > 0, Batch.received_at >= since)
        .order_by(Batch.received_at)
    ).all()
    first: dict[int, float] = {}
    last: dict[int, float] = {}
    for ing_id, _, cost in rows:
        first.setdefault(ing_id, cost)
        last[ing_id] = cost
    ingredients = {i.id: i for i in db.scalars(select(Ingredient)).all()}
    price_changes = sorted(
        (
            (ingredients[i], first[i], last[i], (last[i] - first[i]) / first[i] * 100)
            for i in first
            if first[i] and i in ingredients
        ),
        key=lambda r: -abs(r[3]),
    )
    active_ings = sorted((i for i in ingredients.values() if i.active), key=lambda i: i.name)
    selected = ingredient_id or (price_changes[0][0].id if price_changes else None)
    daily = db.execute(
        select(func.date(Sale.sold_at), func.sum(Sale.revenue), func.sum(Sale.cost))
        .where(Sale.sold_at >= since)
        .group_by(func.date(Sale.sold_at))
        .order_by(func.date(Sale.sold_at))
    ).all()
    return render(
        request,
        "reports.html",
        days=days,
        sales=sales,
        revenue=revenue,
        cogs=cogs,
        purchases=float(purchases or 0),
        losses=losses,
        price_changes=price_changes,
        ingredients=active_ings,
        selected=selected,
        price_history=_price_history(db, selected) if selected else {},
        daily=[{"d": str(d), "rev": round(r or 0, 2), "cost": round(c or 0, 2)} for d, r, c in daily],
    )


# ---------------------------------------------------------------- cài đặt & cảnh báo


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_session)):
    ingredients = db.scalars(
        select(Ingredient).where(Ingredient.active == 1).order_by(Ingredient.category, Ingredient.name)
    ).all()
    groups: dict[str, list] = defaultdict(list)
    for i in ingredients:
        groups[i.category].append(i)
    logs = db.scalars(select(AlertLog).order_by(AlertLog.created_at.desc()).limit(15)).all()
    return render(
        request,
        "settings.html",
        alert_email=", ".join(alerts.alert_recipients(db)),
        alerts_enabled=alerts.alerts_enabled(db),
        smtp_configured=alerts.smtp_configured(),
        smtp_host=config.SMTP_HOST,
        smtp_from=config.SMTP_FROM,
        groups=dict(groups),
        stocks=stock.stock_map(db),
        logs=logs,
        low_count=len(stock.low_stock(db)),
        ocr_settings=ocr.get_settings(db),
    )


@app.post("/settings/ocr")
def settings_ocr(
    request: Request,
    ocr_provider: str = Form("auto"),
    ocr_anthropic_key: str = Form(""),
    ocr_openai_key: str = Form(""),
    ocr_gemini_key: str = Form(""),
    ocr_gemini_model: str = Form(""),
    ocr_anthropic_model: str = Form(""),
    clear_keys: str = Form(""),
    db: Session = Depends(get_session),
):
    alerts.set_setting(db, "ocr_provider", ocr_provider if ocr_provider in ("auto", "anthropic", "openai", "gemini", "demo") else "auto")
    alerts.set_setting(db, "ocr_gemini_model", ocr_gemini_model.strip())
    alerts.set_setting(db, "ocr_anthropic_model", ocr_anthropic_model.strip())
    if clear_keys:
        alerts.set_setting(db, "ocr_anthropic_key", "")
        alerts.set_setting(db, "ocr_openai_key", "")
        alerts.set_setting(db, "ocr_gemini_key", "")
    # Ô để trống = giữ key cũ (key không bao giờ được hiển thị lại trên trang).
    if ocr_anthropic_key.strip():
        alerts.set_setting(db, "ocr_anthropic_key", ocr_anthropic_key.strip())
    if ocr_openai_key.strip():
        alerts.set_setting(db, "ocr_openai_key", ocr_openai_key.strip())
    if ocr_gemini_key.strip():
        alerts.set_setting(db, "ocr_gemini_key", ocr_gemini_key.strip())
    db.commit()
    settings = ocr.get_settings(db)
    provider = ocr.active_provider(settings)
    label = templates.env.globals["ocr_labels"].get(provider, provider)
    if provider != "demo" and not ocr.provider_ready(settings):
        flash(request, f"Đã chọn {label} nhưng chưa có API key – hãy dán key rồi lưu lại. Trong lúc chờ, quét hoá đơn sẽ báo lỗi.", "warn")
    else:
        flash(request, f"Đã lưu cài đặt AI · đang dùng: {label}")
    return redirect("/settings")


@app.get("/settings/backup")
def settings_backup():
    db_path = _sqlite_path()
    if db_path is None or not db_path.is_file():
        raise HTTPException(404, "Chỉ hỗ trợ sao lưu khi dùng SQLite")
    return FileResponse(
        db_path, media_type="application/x-sqlite3",
        filename=f"inventur-backup-{datetime.now():%Y%m%d-%H%M}.db",
    )


@app.post("/settings/restore")
async def settings_restore(request: Request, file: UploadFile = File(None)):
    content = await file.read() if file is not None else b""
    db_path = _sqlite_path()
    if db_path is None:
        flash(request, "Chỉ hỗ trợ khôi phục khi dùng SQLite", "error")
        return redirect("/settings")
    if not content.startswith(b"SQLite format 3\x00"):
        flash(request, "File không phải bản sao lưu hợp lệ (.db)", "error")
        return redirect("/settings")
    engine.dispose()
    db_path.write_bytes(content)
    init_db()
    flash(request, "Đã khôi phục dữ liệu từ bản sao lưu")
    return redirect("/")


@app.post("/settings/reset")
def settings_reset(request: Request):
    db_path = _sqlite_path()
    if db_path is None:
        flash(request, "Chỉ hỗ trợ khi dùng SQLite", "error")
        return redirect("/settings")
    engine.dispose()
    db_path.unlink(missing_ok=True)
    init_db()
    with SessionLocal() as session:
        seed(session)
    flash(request, "Đã tạo lại dữ liệu demo")
    return redirect("/")


def _sqlite_path() -> Path | None:
    url = config.DATABASE_URL
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


@app.post("/settings/alerts")
def settings_alerts(
    request: Request,
    alert_email: str = Form(""),
    alerts_enabled: str = Form(""),
    db: Session = Depends(get_session),
):
    alerts.set_setting(db, "alert_email", alert_email.strip())
    alerts.set_setting(db, "alerts_enabled", "1" if alerts_enabled else "0")
    db.commit()
    flash(request, "Đã lưu cài đặt cảnh báo")
    return redirect("/settings")


@app.post("/settings/thresholds")
async def settings_thresholds(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    changed = 0
    for key, value in form.items():
        if not key.startswith("min_"):
            continue
        ing = db.get(Ingredient, int(key[4:]))
        new = parse_float(value)
        if ing and abs(ing.min_stock - new) > 1e-9:
            ing.min_stock = new
            changed += 1
    after_stock_change(request, db)
    flash(request, f"Đã cập nhật ngưỡng tồn cho {changed} nguyên liệu")
    return redirect("/settings")


@app.post("/settings/send-report")
def settings_send_report(request: Request, db: Session = Depends(get_session)):
    low = stock.low_stock(db)
    if not low:
        flash(request, "Không có nguyên liệu nào dưới ngưỡng — không cần gửi báo cáo.")
        return redirect("/settings")
    entry = alerts.notify(db, low)
    if entry.status == "sent":
        flash(request, f"Đã gửi báo cáo {len(low)} nguyên liệu tới {entry.recipient}")
    elif entry.status == "pending":
        flash(request, f"Đang gửi báo cáo {len(low)} nguyên liệu tới {entry.recipient} — xem trạng thái ở Lịch sử email.")
    else:
        flash(request, f"Chưa gửi được email: {entry.detail}", "error")
    return redirect("/settings")
