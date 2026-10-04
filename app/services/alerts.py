"""Cảnh báo hàng sắp hết: gửi email khi tồn kho xuống dưới ngưỡng tối thiểu."""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import threading
from html import escape as html_escape
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import areas, config
from ..db import AlertLog, Ingredient, SessionLocal, Setting
from . import stock

log = logging.getLogger("inventory.alerts")


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def _split_emails(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").replace(";", ",").split(",") if e.strip()]


def alert_recipients(session: Session, area: str | None = None) -> list[str]:
    """Email nhận cảnh báo. Mỗi khu có thể có email riêng (bếp trưởng / quầy bar), trống thì dùng email chung."""
    if area:
        own = _split_emails(get_setting(session, f"alert_email_{area}", ""))
        if own:
            return own
    return _split_emails(get_setting(session, "alert_email", config.ALERT_EMAIL))


def alerts_enabled(session: Session) -> bool:
    return get_setting(session, "alerts_enabled", "1") == "1"


def smtp_configured() -> bool:
    if config.IS_BROWSER:
        return True  # bản GitHub Pages gửi qua dịch vụ web (FormSubmit), xem web/runtime.js
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def check_low_stock(session: Session) -> list[tuple[Ingredient, float]]:
    """Cập nhật cờ cảnh báo. Trả về các nguyên liệu VỪA xuống dưới ngưỡng (chưa báo lần nào).

    Mỗi nguyên liệu chỉ báo một lần cho tới khi được nhập thêm lên trên ngưỡng.
    """
    stocks = stock.stock_map(session)
    newly_low = []
    for ing in session.scalars(select(Ingredient).where(Ingredient.active == 1)).all():
        qty = stocks.get(ing.id, 0.0)
        is_low = ing.min_stock > 0 and qty < ing.min_stock
        if is_low and not ing.alert_sent:
            ing.alert_sent = 1
            newly_low.append((ing, qty))
        elif not is_low and ing.alert_sent:
            ing.alert_sent = 0
    return newly_low


def build_email(
    low: list[tuple[Ingredient, float]],
    newly: list[tuple[Ingredient, float]] | None = None,
    area: str | None = None,
) -> tuple[str, str, str]:
    newly_ids = {i.id for i, _ in (newly or [])}
    where = f"Kho {areas.label(area)}" if area else "Kho nhà hàng"
    subject = f"[{where}] {len(low)} nguyên liệu dưới ngưỡng tồn tối thiểu"
    rows_txt, rows_html = [], []
    present = [a for a in areas.AREA_KEYS if any(i.area == a for i, _ in low)]
    for current in present:
        if len(present) > 1 or not area:
            title = f"{areas.AREAS[current]['icon']} {areas.label(current)}"
            rows_txt.append(f"\n{title}")
            rows_html.append(f"<tr><td colspan='5' style='padding:8px 10px;background:#f8fafc;font-weight:bold'>{title}</td></tr>")
        for ing, qty in (row for row in low if row[0].area == current):
            need = max(ing.min_stock - qty, 0)
            to_par = max(stock.par_target(ing) - qty, need)
            mark = " (MỚI)" if ing.id in newly_ids else ""
            rows_txt.append(
                f"- {ing.name}{mark}: còn {stock.fmt_qty(qty)} {ing.unit} / ngưỡng {stock.fmt_qty(ing.min_stock)} "
                f"→ nên đặt khoảng {stock.fmt_qty(to_par)} {ing.unit}"
                + (f" (NCC: {ing.supplier.name})" if ing.supplier else "")
            )
            badge = "<b style='color:#c2410c'> MỚI</b>" if mark else ""
            rows_html.append(
                f"<tr><td style='padding:6px 10px'>{html_escape(ing.name)}{badge}</td>"
                f"<td style='padding:6px 10px;text-align:right;color:#b91c1c'><b>{stock.fmt_qty(qty)}</b> {html_escape(ing.unit)}</td>"
                f"<td style='padding:6px 10px;text-align:right'>{stock.fmt_qty(ing.min_stock)} {html_escape(ing.unit)}</td>"
                f"<td style='padding:6px 10px;text-align:right'>{stock.fmt_qty(to_par)} {html_escape(ing.unit)}</td>"
                f"<td style='padding:6px 10px'>{html_escape(ing.supplier.name) if ing.supplier else ''}</td></tr>"
            )
    link = f"\n\nMở ứng dụng: {config.APP_URL.rstrip('/')}/orders" if config.APP_URL else ""
    text = f"{where} – các nguyên liệu sau đang dưới ngưỡng tồn kho tối thiểu:\n" + "\n".join(rows_txt) + link
    html = (
        "<div style='font-family:Arial,sans-serif'>"
        f"<h2 style='color:#0f766e'>Cảnh báo hàng sắp hết – {html_escape(where)}</h2>"
        "<p>Các nguyên liệu sau đang dưới ngưỡng tồn kho tối thiểu:</p>"
        "<table style='border-collapse:collapse;border:1px solid #e5e7eb'>"
        "<tr style='background:#f0fdfa'><th style='padding:6px 10px;text-align:left'>Nguyên liệu</th>"
        "<th style='padding:6px 10px'>Còn</th><th style='padding:6px 10px'>Ngưỡng</th>"
        "<th style='padding:6px 10px'>Nên đặt</th><th style='padding:6px 10px;text-align:left'>NCC</th></tr>"
        + "".join(rows_html)
        + "</table>"
        + (f"<p><a href='{config.APP_URL.rstrip('/')}/orders'>Mở danh sách đặt hàng</a></p>" if config.APP_URL else "")
        + "</div>"
    )
    return subject, text, html


def send_email(recipients: list[str], subject: str, text: str, html: str) -> tuple[str, str]:
    """Gửi email qua SMTP. Trả về (status, detail)."""
    if not recipients:
        return "failed", "Chưa có email nhận cảnh báo"
    if not smtp_configured():
        return "not_configured", "Chưa cấu hình SMTP (SMTP_HOST, SMTP_USER, SMTP_PASSWORD)"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        context = ssl.create_default_context()
        if config.SMTP_SSL:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=context, timeout=20) as smtp:
                _login_and_send(smtp, msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
                smtp.starttls(context=context)
                _login_and_send(smtp, msg)
    except Exception as exc:  # noqa: BLE001 - báo lỗi gửi mail cho người dùng
        log.warning("Gửi email cảnh báo thất bại: %s", exc)
        return "failed", str(exc)
    return "sent", ""


def _login_and_send(smtp: smtplib.SMTP, msg: EmailMessage) -> None:
    if config.SMTP_USER:
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
    smtp.send_message(msg)


def _dispatch(entry: AlertLog, recipients: list[str], subject: str, text: str, html: str, background: bool) -> None:
    if config.IS_BROWSER:
        import js  # type: ignore[import-not-found]

        # JS gửi email rồi gọi lại app.browser.set_alert_status() để cập nhật trạng thái.
        js.inventurSendEmail(entry.id, json.dumps(recipients), subject, text)
        return
    args = (entry.id, recipients, subject, text, html)
    if background:
        threading.Thread(target=_send_in_background, args=args, daemon=True).start()
    else:
        _send_in_background(*args)


def _new_entry(session: Session, recipients: list[str], subject: str, text: str) -> AlertLog:
    configured = smtp_configured() and bool(recipients)
    entry = AlertLog(
        recipient=", ".join(recipients), subject=subject, body=text,
        status="pending" if configured else "not_configured",
        detail="" if configured else (
            "Chưa có email nhận cảnh báo" if not recipients
            else "Chưa cấu hình SMTP (SMTP_HOST, SMTP_USER, SMTP_PASSWORD)"
        ),
    )
    session.add(entry)
    session.commit()
    return entry


def plan_emails(
    session: Session, low: list[tuple[Ingredient, float]], newly=None
) -> list[tuple[list[str], str | None, list, list]]:
    """Chia danh sách theo khu -> người nhận. Hai khu cùng người nhận thì gộp thành một email.

    Trả về [(người nhận, khu hoặc None nếu gộp, low, newly)].
    """
    newly = newly or []
    by_recipients: dict[tuple, list[str]] = {}
    for area in areas.AREA_KEYS:
        if any(i.area == area for i, _ in low):
            by_recipients.setdefault(tuple(alert_recipients(session, area)), []).append(area)
    plans = []
    for recipients, area_list in by_recipients.items():
        plans.append((
            list(recipients),
            area_list[0] if len(area_list) == 1 else None,
            [row for row in low if row[0].area in area_list],
            [row for row in newly if row[0].area in area_list],
        ))
    return plans


def notify(session: Session, low: list[tuple[Ingredient, float]], newly=None) -> list[AlertLog]:
    """Gửi ngay báo cáo các nguyên liệu dưới ngưỡng (nút "Gửi báo cáo ngay"), tách theo khu."""
    entries = []
    for recipients, area, low_part, newly_part in plan_emails(session, low, newly):
        subject, text, html = build_email(low_part, newly_part, area)
        entry = _new_entry(session, recipients, subject, text)
        if entry.status == "pending":
            _dispatch(entry, recipients, subject, text, html, background=False)
            session.refresh(entry)
        entries.append(entry)
    return entries


def _send_in_background(alert_id: int, recipients: list[str], subject: str, text: str, html: str) -> None:
    status, detail = send_email(recipients, subject, text, html)
    set_status(alert_id, status, detail)


def set_status(alert_id: int, status: str, detail: str = "") -> None:
    with SessionLocal() as session:
        entry = session.get(AlertLog, alert_id)
        if entry:
            entry.status, entry.detail = status, detail
            session.commit()


def check_and_notify(session: Session, background: bool = True) -> list[tuple[Ingredient, float]]:
    """Gọi sau mỗi thao tác làm thay đổi tồn kho. Tự commit."""
    newly = check_low_stock(session)
    session.commit()
    if newly and alerts_enabled(session):
        # Chỉ gửi cho khu có hàng VỪA xuống dưới ngưỡng, kèm toàn bộ danh sách đang thiếu của khu đó.
        new_areas = {i.area for i, _ in newly}
        low = [row for row in stock.low_stock(session) if row[0].area in new_areas]
        for recipients, area, low_part, newly_part in plan_emails(session, low, newly):
            subject, text, html = build_email(low_part, newly_part, area)
            entry = _new_entry(session, recipients, subject, text)
            if entry.status == "pending":
                _dispatch(entry, recipients, subject, text, html, background)
    return newly
