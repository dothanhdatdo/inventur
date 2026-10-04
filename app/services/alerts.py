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

from .. import config
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


def alert_recipients(session: Session) -> list[str]:
    raw = get_setting(session, "alert_email", config.ALERT_EMAIL)
    return [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]


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


def build_email(low: list[tuple[Ingredient, float]], newly: list[tuple[Ingredient, float]] | None = None) -> tuple[str, str, str]:
    newly_ids = {i.id for i, _ in (newly or [])}
    subject = f"[Kho nhà hàng] {len(low)} nguyên liệu dưới ngưỡng tồn tối thiểu"
    rows_txt, rows_html = [], []
    for ing, qty in low:
        need = max(ing.min_stock - qty, 0)
        mark = " (MỚI)" if ing.id in newly_ids else ""
        rows_txt.append(
            f"- {ing.name}{mark}: còn {stock.fmt_qty(qty)} {ing.unit} / ngưỡng {stock.fmt_qty(ing.min_stock)} "
            f"→ cần nhập thêm ít nhất {stock.fmt_qty(need)} {ing.unit}"
            + (f" (NCC: {ing.supplier.name})" if ing.supplier else "")
        )
        badge = "<b style='color:#c2410c'> MỚI</b>" if mark else ""
        rows_html.append(
            f"<tr><td style='padding:6px 10px'>{html_escape(ing.name)}{badge}</td>"
            f"<td style='padding:6px 10px;text-align:right;color:#b91c1c'><b>{stock.fmt_qty(qty)}</b> {ing.unit}</td>"
            f"<td style='padding:6px 10px;text-align:right'>{stock.fmt_qty(ing.min_stock)} {ing.unit}</td>"
            f"<td style='padding:6px 10px;text-align:right'>{stock.fmt_qty(need)} {ing.unit}</td>"
            f"<td style='padding:6px 10px'>{html_escape(ing.supplier.name) if ing.supplier else ''}</td></tr>"
        )
    link = f"\n\nMở ứng dụng: {config.APP_URL.rstrip('/')}/ingredients?status=low" if config.APP_URL else ""
    text = "Các nguyên liệu sau đang dưới ngưỡng tồn kho tối thiểu:\n\n" + "\n".join(rows_txt) + link
    html = (
        "<div style='font-family:Arial,sans-serif'>"
        "<h2 style='color:#0f766e'>Cảnh báo hàng sắp hết</h2>"
        "<p>Các nguyên liệu sau đang dưới ngưỡng tồn kho tối thiểu:</p>"
        "<table style='border-collapse:collapse;border:1px solid #e5e7eb'>"
        "<tr style='background:#f0fdfa'><th style='padding:6px 10px;text-align:left'>Nguyên liệu</th>"
        "<th style='padding:6px 10px'>Còn</th><th style='padding:6px 10px'>Ngưỡng</th>"
        "<th style='padding:6px 10px'>Cần nhập</th><th style='padding:6px 10px;text-align:left'>NCC</th></tr>"
        + "".join(rows_html)
        + "</table>"
        + (f"<p><a href='{config.APP_URL.rstrip('/')}/ingredients?status=low'>Mở ứng dụng</a></p>" if config.APP_URL else "")
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


def notify(session: Session, low: list[tuple[Ingredient, float]], newly=None) -> AlertLog:
    """Gửi ngay báo cáo các nguyên liệu dưới ngưỡng (nút "Gửi báo cáo ngay")."""
    recipients = alert_recipients(session)
    subject, text, html = build_email(low, newly)
    entry = _new_entry(session, recipients, subject, text)
    if entry.status == "pending":
        _dispatch(entry, recipients, subject, text, html, background=False)
        session.refresh(entry)
    return entry


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
        low = stock.low_stock(session)
        recipients = alert_recipients(session)
        subject, text, html = build_email(low, newly)
        entry = _new_entry(session, recipients, subject, text)
        if entry.status == "pending":
            _dispatch(entry, recipients, subject, text, html, background)
    return newly
