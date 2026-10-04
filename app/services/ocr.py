"""Bóc tách dữ liệu hoá đơn bằng AI (Claude, GPT-4o hoặc Gemini), có chế độ demo khi chưa có API key."""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote
from dataclasses import dataclass, field
from datetime import date, datetime

from .. import config

PROMPT = """Bạn là hệ thống đọc hoá đơn nhập hàng của nhà hàng (hoá đơn có thể bằng tiếng Đức, Việt hoặc Anh).
Hãy trích xuất dữ liệu và CHỈ trả về một đối tượng JSON hợp lệ, không giải thích, theo đúng cấu trúc:
{
  "supplier": "tên nhà cung cấp",
  "invoice_number": "số hoá đơn hoặc chuỗi rỗng",
  "invoice_date": "YYYY-MM-DD hoặc chuỗi rỗng",
  "currency": "EUR",
  "lines": [
    {"name": "tên mặt hàng đúng như trên hoá đơn", "quantity": 1.0, "unit": "đơn vị trên hoá đơn (kg, Stk, Karton, Sack, l...)",
     "unit_price": 0.0, "total": 0.0, "vat_rate": 7.0}
  ],
  "subtotal": 0.0,
  "vat": 0.0,
  "total": 0.0
}
Quy tắc: số dùng dấu chấm thập phân; giá là giá NET (chưa VAT) nếu hoá đơn ghi cả hai; bỏ qua dòng
tiền cọc vỏ chai (Pfand), phí vận chuyển và chiết khấu nếu không phải hàng hoá; không bịa dữ liệu không đọc được."""

DEMO_RESULT = {
    "supplier": "Großmarkt Breisgau GmbH",
    "invoice_number": "DEMO-2026-0815",
    "invoice_date": "",
    "currency": "EUR",
    "lines": [
        {"name": "Thai Jasminreis Duftreis 18kg Sack", "quantity": 2, "unit": "Sack", "unit_price": 31.90, "total": 63.80, "vat_rate": 7},
        {"name": "Rinderhüfte frisch DE", "quantity": 6.4, "unit": "kg", "unit_price": 18.49, "total": 118.34, "vat_rate": 7},
        {"name": "Hähnchenbrustfilet frisch", "quantity": 10, "unit": "kg", "unit_price": 8.99, "total": 89.90, "vat_rate": 7},
        {"name": "Reisbandnudeln 5mm 400g", "quantity": 20, "unit": "Stk", "unit_price": 1.79, "total": 35.80, "vat_rate": 7},
        {"name": "Koriander frisch Bund", "quantity": 15, "unit": "Bund", "unit_price": 0.89, "total": 13.35, "vat_rate": 7},
        {"name": "Coca-Cola 24x0,33l Dose", "quantity": 2, "unit": "Karton", "unit_price": 17.76, "total": 35.52, "vat_rate": 19},
        {"name": "Fischsauce Squid Brand 0,7l", "quantity": 6, "unit": "Fl", "unit_price": 3.49, "total": 20.94, "vat_rate": 7},
        {"name": "Thai Basilikum frisch 100g", "quantity": 5, "unit": "Stk", "unit_price": 1.99, "total": 9.95, "vat_rate": 7},
        {"name": "Pak Choi frisch", "quantity": 3, "unit": "kg", "unit_price": 3.49, "total": 10.47, "vat_rate": 7},
    ],
    "subtotal": 398.07,
    "vat": 32.13,
    "total": 430.20,
}


class OcrError(Exception):
    pass


@dataclass
class ParsedLine:
    name: str
    quantity: float
    unit: str
    unit_price: float
    total: float
    vat_rate: float


@dataclass
class ParsedInvoice:
    supplier: str = ""
    invoice_number: str = ""
    invoice_date: date | None = None
    subtotal: float = 0.0
    vat: float = 0.0
    total: float = 0.0
    lines: list[ParsedLine] = field(default_factory=list)
    source: str = "demo"
    raw: dict = field(default_factory=dict)


def get_settings(session=None) -> dict:
    """Cấu hình OCR: ưu tiên giá trị lưu trong trang Cài đặt, sau đó tới biến môi trường."""
    stored = {}
    if session is not None:
        from ..db import Setting

        stored = {row.key: row.value for row in session.query(Setting).filter(Setting.key.like("ocr_%")).all()}
    return {
        "provider": (stored.get("ocr_provider") or config.OCR_PROVIDER or "auto").lower(),
        "anthropic_key": stored.get("ocr_anthropic_key") or config.ANTHROPIC_API_KEY,
        "anthropic_model": stored.get("ocr_anthropic_model") or config.ANTHROPIC_MODEL,
        "openai_key": stored.get("ocr_openai_key") or config.OPENAI_API_KEY,
        "openai_model": stored.get("ocr_openai_model") or config.OPENAI_MODEL,
        "gemini_key": stored.get("ocr_gemini_key") or config.GEMINI_API_KEY,
        "gemini_model": stored.get("ocr_gemini_model") or config.GEMINI_MODEL,
    }


PROVIDER_KEYS = {"anthropic": "anthropic_key", "openai": "openai_key", "gemini": "gemini_key"}


def provider_ready(settings: dict | None = None) -> bool:
    """AI đã sẵn sàng đọc hoá đơn thật chưa (nhà cung cấp đã chọn có API key)."""
    settings = settings or get_settings()
    key_field = PROVIDER_KEYS.get(active_provider(settings))
    return bool(key_field and settings[key_field])


def active_provider(settings: dict | None = None) -> str:
    settings = settings or get_settings()
    provider = settings["provider"]
    if provider == "auto":
        if settings["anthropic_key"]:
            return "anthropic"
        if settings["openai_key"]:
            return "openai"
        if settings["gemini_key"]:
            return "gemini"
        return "demo"
    return provider


async def extract_invoice(content: bytes, media_type: str, settings: dict | None = None) -> ParsedInvoice:
    settings = settings or get_settings()
    provider = active_provider(settings)
    if provider == "anthropic":
        data = await _call_anthropic(content, media_type, settings)
    elif provider == "openai":
        data = await _call_openai(content, media_type, settings)
    elif provider == "gemini":
        data = await _call_gemini(content, media_type, settings)
    else:
        data = json.loads(json.dumps(DEMO_RESULT))
        data["invoice_date"] = date.today().isoformat()
        provider = "demo"
    parsed = parse_result(data)
    parsed.source = provider
    return parsed


async def _post_json(url: str, headers: dict, payload: dict) -> tuple[int, str]:
    """POST JSON, chạy được cả trên server (httpx) lẫn trong trình duyệt (fetch)."""
    if config.IS_BROWSER:
        from pyodide.http import pyfetch  # type: ignore[import-not-found]

        try:
            resp = await pyfetch(url, method="POST", headers={**headers, "content-type": "application/json"}, body=json.dumps(payload))
        except Exception as exc:  # noqa: BLE001
            raise OcrError(f"Không kết nối được tới {url}: {exc}") from exc
        return resp.status, await resp.string()
    import httpx

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise OcrError(f"Không kết nối được tới {url}: {exc}") from exc
    return resp.status_code, resp.text


async def _call_anthropic(content: bytes, media_type: str, settings: dict) -> dict:
    if not settings["anthropic_key"]:
        raise OcrError("Thiếu Anthropic API key")
    b64 = base64.standard_b64encode(content).decode()
    if media_type == "application/pdf":
        file_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    else:
        file_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
    payload = {
        "model": settings["anthropic_model"],
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": [file_block, {"type": "text", "text": PROMPT}]}],
    }
    headers = {
        "x-api-key": settings["anthropic_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if config.IS_BROWSER:
        headers["anthropic-dangerous-direct-browser-access"] = "true"
    status, body = await _post_json("https://api.anthropic.com/v1/messages", headers, payload)
    if status != 200:
        raise OcrError(f"Anthropic API lỗi {status}: {body[:300]}")
    data = json.loads(body)
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    return _parse_json(text)


async def _call_openai(content: bytes, media_type: str, settings: dict) -> dict:
    if not settings["openai_key"]:
        raise OcrError("Thiếu OpenAI API key")
    if media_type == "application/pdf":
        raise OcrError("OpenAI chỉ hỗ trợ ảnh trong ứng dụng này. Hãy tải ảnh JPG/PNG hoặc dùng Claude cho PDF.")
    b64 = base64.standard_b64encode(content).decode()
    payload = {
        "model": settings["openai_model"],
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }
        ],
    }
    status, body = await _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {settings['openai_key']}"},
        payload,
    )
    if status != 200:
        raise OcrError(f"OpenAI API lỗi {status}: {body[:300]}")
    return _parse_json(json.loads(body)["choices"][0]["message"]["content"])


async def _call_gemini(content: bytes, media_type: str, settings: dict) -> dict:
    """Google Gemini API (generateContent). Có gói miễn phí; đọc được cả ảnh và PDF."""
    if not settings["gemini_key"]:
        raise OcrError("Thiếu Gemini API key")
    model = (settings["gemini_model"] or config.GEMINI_MODEL).strip().removeprefix("models/")
    b64 = base64.standard_b64encode(content).decode()
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": media_type, "data": b64}},
                    {"text": PROMPT},
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    status, body = await _post_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent",
        {"x-goog-api-key": settings["gemini_key"]},
        payload,
    )
    if status == 429:
        message = _google_error_message(body)
        if "limit: 0" in message:
            raise OcrError(
                f"Model Gemini “{model}” không có lượt miễn phí cho key này. "
                "Đổi ô Model Gemini trong Cài đặt về gemini-flash-latest"
            )
        raise OcrError(
            "Gemini báo đã dùng hết lượt miễn phí (giới hạn theo phút hoặc theo ngày). Đợi một lúc rồi thử lại"
            + (f" – Google: {message[:200]}" if message else "")
        )
    if status in (400, 401, 403) and ("API_KEY" in body or "API key" in body or "PERMISSION_DENIED" in body):
        raise OcrError(f"Gemini API key không hợp lệ hoặc chưa được bật ({status}). Kiểm tra lại key trong Cài đặt")
    if status == 404:
        raise OcrError(f"Không tìm thấy model Gemini “{model}”. Sửa tên model trong Cài đặt (vd. gemini-flash-latest)")
    if status != 200:
        raise OcrError(f"Gemini API lỗi {status}: {body[:300]}")
    data = json.loads(body)
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "không có kết quả")
        raise OcrError(f"Gemini không trả về kết quả ({reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    finish = candidates[0].get("finishReason") or "STOP"
    if not text:
        raise OcrError(f"Gemini không trả về nội dung ({finish})")
    try:
        return _parse_json(text)
    except OcrError:
        if finish != "STOP":
            raise OcrError(f"Gemini dừng giữa chừng ({finish}), kết quả không đầy đủ. Thử chụp lại ảnh rõ hơn") from None
        raise


def _google_error_message(body: str) -> str:
    try:
        return str((json.loads(body).get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        return ""


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise OcrError("AI không trả về JSON hợp lệ")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise OcrError(f"Không đọc được JSON từ AI: {exc}") from exc


def to_float(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        # 1.234,56 (kiểu Đức) hoặc 1,234.56 (kiểu Anh)
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_date(value) -> date | None:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_result(data: dict) -> ParsedInvoice:
    lines = []
    for item in data.get("lines") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        qty = to_float(item.get("quantity"))
        price = to_float(item.get("unit_price"))
        total = to_float(item.get("total"))
        if not total and qty and price:
            total = round(qty * price, 2)
        if not price and qty and total:
            price = round(total / qty, 4)
        lines.append(
            ParsedLine(
                name=name,
                quantity=qty,
                unit=str(item.get("unit") or "").strip(),
                unit_price=price,
                total=total,
                vat_rate=to_float(item.get("vat_rate")),
            )
        )
    subtotal = to_float(data.get("subtotal")) or round(sum(line.total for line in lines), 2)
    return ParsedInvoice(
        supplier=str(data.get("supplier") or "").strip(),
        invoice_number=str(data.get("invoice_number") or "").strip(),
        invoice_date=to_date(data.get("invoice_date")),
        subtotal=subtotal,
        vat=to_float(data.get("vat")),
        total=to_float(data.get("total")),
        lines=lines,
        raw=data,
    )
