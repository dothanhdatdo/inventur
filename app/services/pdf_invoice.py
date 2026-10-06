"""Đọc hoá đơn PDF có sẵn chữ (vd. hoá đơn METRO gửi qua email) mà KHÔNG cần AI.

PDF điện tử chứa chữ thật, nên chỉ cần lấy chữ ra (pypdf, chạy được cả trong trình duyệt)
rồi đọc theo bố cục cột cố định của hoá đơn. Ảnh chụp hoá đơn giấy thì không có chữ -> cần AI.
"""
from __future__ import annotations

import io
import re
from datetime import date

from .ocr import ParsedInvoice, ParsedLine, to_date, to_float


def extract_text(content: bytes) -> str:
    """Lấy chữ từ PDF, giữ nguyên bố cục cột. Trả về "" nếu PDF không có chữ (vd. bản scan)."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf có trong requirements
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except Exception:  # noqa: BLE001 - PDF hỏng/mã hoá -> coi như không đọc được
        return ""
    return "\n".join(pages).replace("\xa0", " ")


# ---------------------------------------------------------------- METRO

_NUM = r"-?[\d.]*\d,\d+|-?\d+"
# Dòng hàng: [+] ART.-NR [EAN] TÊN HÀNG  PACK  ĐƠN GIÁ  SỐ/KOLLI  GIÁ KOLLI  SỐ LƯỢNG  THÀNH TIỀN  VAT  GIÁ/CÁI SAU GIẢM [* W G ...]
METRO_LINE = re.compile(
    r"^\s*(?P<deposit>\+\s*)?(?P<art>\d{6}\.\d)\s+(?:(?P<ean>\d{8,14})\s+)?(?P<name>.+?)\s+(?P<pack>[A-Z]{2})\s+"
    rf"(?P<unit_price>{_NUM})\s+(?P<per_pack>{_NUM})\s+(?P<pack_price>{_NUM})\s+(?P<qty>\d+)\s+"
    rf"(?P<total>{_NUM})\s+(?P<vat>[AB])\s+(?P<piece_price>{_NUM})(?:\s+[*A-Z]{{1,2}})*\s*$"
)
METRO_MHD = re.compile(r"MHD:\s*(\d{6})")
METRO_NUMBER = re.compile(r"RECHNUNGS-NR\.\s+(\S+)")
METRO_DATE = re.compile(r"(?:RECHNUNGSDATUM|BELEGDATUM):\s+(\d{2}\.\d{2}\.\d{4})")
METRO_STORE = re.compile(r"^\s*METRO Deutschland GmbH\s{2,}(\S[^\n]*?)\s{2,}", re.M)
METRO_SECTION = re.compile(r"^\*{4} ?([^*]+?) ?\*{4}$")
METRO_NET = re.compile(r"NETTO-WARENWERT:\s+([\d.]*\d,\d{2})")
# Bảng VAT cuối hoá đơn: "405,78  A=19,00%  77,10  482,88" (dòng "LEERGUT:" là tiền cọc vỏ, không tính).
METRO_VAT_ROW = re.compile(r"^\s*([\d.]*\d,\d{2})\s+([AB])=\s*\d+,\d+%\s+([\d.]*\d,\d{2})\s+[\d.]*\d,\d{2}\s*$")
MAX_LINE = 400  # dòng dài bất thường -> bỏ qua (tránh regex chạy quá lâu với PDF lạ)
VAT_CLASS = {"A": 19.0, "B": 7.0}
# Nhóm hàng trên hoá đơn METRO -> nhóm trong kho (quyết định cả khu Bếp/Quầy khi tạo nguyên liệu mới).
METRO_GROUPS = {
    "nonfood": "Dụng cụ / Nonfood",
    "drogerie": "Vệ sinh / Drogerie",
    "konserven": "Hàng khô",
    "naehrmittel": "Hàng khô",
    "feinkost / mopro": "Đồ mát / Mopro",
    "fisch": "Thịt & hải sản",
    "fleisch / wurst": "Thịt & hải sản",
    "tiefkuehl": "Đồ đông lạnh",
    "obst / gemuese": "Rau củ & rau thơm",
    "bier / afg": "Đồ uống / Bier & AfG",
    "wein / sekt": "Rượu vang / Wein",
    "spirituosen": "Rượu mạnh / Spirituosen",
    "suesswaren / kaffee": "",  # bánh kẹo (bếp) lẫn cà phê (quầy) -> để tên hàng quyết định
}


def is_metro(text: str) -> bool:
    return "METRO Deutschland" in text and "RECHNUNGS-NR" in text


def parse_metro(text: str) -> ParsedInvoice | None:
    """Đọc hoá đơn METRO. Giá mỗi dòng là giá NET sau Mengenrabatt (cột STÜCK PREIS)."""
    if not is_metro(text):
        return None
    lines: list[ParsedLine] = []
    deposit = 0.0
    last: ParsedLine | None = None
    section = ""
    for raw in text.splitlines():
        if len(raw) > MAX_LINE:
            continue
        raw = " ".join(raw.split())  # bỏ khoảng trắng thừa của bố cục cột
        heading = METRO_SECTION.match(raw)
        if heading:
            key = heading.group(1).strip().lower().replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
            section = METRO_GROUPS.get(key, "")
            continue
        match = METRO_LINE.match(raw)
        if match:
            g = match.groupdict()
            total = to_float(g["total"])
            if g["deposit"]:  # Leergut (tiền cọc vỏ) không phải hàng tồn kho
                deposit += total
                last = None
                continue
            qty = int(g["qty"])
            per_pack = to_float(g["per_pack"])
            unit_price = to_float(g["unit_price"])
            piece_price = to_float(g["piece_price"])
            units = qty * per_pack  # số đơn vị lẻ (hàng cân "KG": INHALT là số kg)
            # Có giảm giá (Mengenrabatt) -> STÜCK PREIS thấp hơn đơn giá. Tiền giảm = số đơn vị × mức giảm mỗi đơn vị
            # (trừ vào cột GESAMT, vì GESAMT tính từ giá kiện đã làm tròn – vd. 12 × 0,991 = 11,89).
            if piece_price < unit_price - 1e-9 and units:
                total = round(total - round(units * (unit_price - piece_price), 2), 2)
            if g["pack"] == "KG":
                # Hàng cân (thịt, cá...): số lượng = số kg thật, đơn giá = €/kg -> gắn vào nguyên liệu "kg" là đúng ngay.
                quantity, per_unit = round(units, 3), 1.0
            else:
                quantity, per_unit = qty, per_pack
            last = ParsedLine(
                name=g["name"].strip(),
                quantity=quantity,
                unit=g["pack"],
                unit_price=round(total / quantity, 4) if quantity else total,
                total=total,
                vat_rate=VAT_CLASS[g["vat"]],
                units_per_pack=per_unit,
                article_no=g["art"],
                category=section,
            )
            lines.append(last)
            continue
        mhd = METRO_MHD.search(raw)
        if mhd and last is not None and last.expiry is None:
            yy, mm, dd = int(mhd.group(1)[:2]), int(mhd.group(1)[2:4]), int(mhd.group(1)[4:])
            try:
                last.expiry = date(2000 + yy, mm, dd)
            except ValueError:
                pass
    if not lines:
        return None
    number = METRO_NUMBER.search(text)
    when = METRO_DATE.search(text)
    store = METRO_STORE.search(text)
    subtotal = round(sum(line.total for line in lines), 2)
    vat = round(sum(line.total * line.vat_rate / 100 for line in lines), 2)
    warnings = []
    vat_rows = [METRO_VAT_ROW.match(raw) for raw in text.splitlines() if "LEERGUT" not in raw]
    vat_rows = [m for m in vat_rows if m]
    if vat_rows:  # VAT in trên hoá đơn chính xác hơn tự tính từng dòng
        vat = round(sum(to_float(m.group(3)) for m in vat_rows), 2)
        for m in vat_rows:
            rate = VAT_CLASS[m.group(2)]
            net = round(sum(line.total for line in lines if line.vat_rate == rate), 2)
            if abs(net - to_float(m.group(1))) > 0.05:
                warnings.append(
                    f"Tiền hàng VAT {rate:g} % đọc được ({_eur(net)}) khác hoá đơn ({_eur(to_float(m.group(1)))}) "
                    "– hãy so lại với file PDF."
                )
    printed = METRO_NET.search(text)
    if printed and abs(subtotal + deposit - to_float(printed.group(1))) > 0.05:
        # Có dòng không đọc được (vd. trả hàng, storno) -> báo để người dùng so lại với PDF.
        warnings.append(
            f"Tổng các dòng đọc được ({_eur(subtotal + deposit)}) khác NETTO-WARENWERT trên hoá đơn "
            f"({_eur(to_float(printed.group(1)))}) – có dòng chưa đọc được, hãy so lại với file PDF trước khi nhập kho."
        )
    return ParsedInvoice(
        supplier=f"METRO {store.group(1).strip()}" if store else "METRO",
        invoice_number=number.group(1) if number else "",
        invoice_date=to_date(when.group(1)) if when else None,
        subtotal=subtotal,
        vat=vat,
        total=round(subtotal + vat, 2),
        lines=lines,
        source="pdf",
        raw={"parser": "metro", "lines": len(lines), "deposit_net": round(deposit, 2),
             "net_printed": to_float(printed.group(1)) if printed else None},
        deposit=round(deposit, 2),
        warnings=warnings,
    )


def _eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


PARSERS = [parse_metro]


def parse_pdf(content: bytes) -> ParsedInvoice | None:
    """Thử các bộ đọc PDF đã biết. None nếu PDF không có chữ hoặc chưa hỗ trợ nhà cung cấp này."""
    text = extract_text(content)
    if not text.strip():
        return None
    for parser in PARSERS:
        parsed = parser(text)
        if parsed is not None:
            return parsed
    return None
