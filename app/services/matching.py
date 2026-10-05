"""Khớp tên mặt hàng trên hoá đơn với nguyên liệu trong kho."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Ingredient, SupplierAlias

MATCH_THRESHOLD = 0.55

# Từ "nhiễu" thường gặp trên hoá đơn, bỏ qua khi so khớp.
STOPWORDS = {
    "frisch", "tiefgekuhlt", "tk", "ca", "stk", "stuck", "sack", "karton", "kiste", "pack",
    "packung", "dose", "flasche", "fl", "glas", "beutel", "kg", "g", "l", "ml", "x", "bio",
    "metro", "chef", "aro", "premium", "klasse", "i", "ii", "de", "nl", "es", "vn", "th",
    "the", "and", "und", "mit", "loai", "tuoi",
}

UNIT_ALIASES = {
    "kg": "kg", "kilo": "kg", "kilogramm": "kg",
    "g": "g", "gr": "g", "gramm": "g",
    "l": "l", "ltr": "l", "liter": "l", "lit": "l", "lít": "l",
    "ml": "ml",
}


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ß", "ss").replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in STOPWORDS and not t.isdigit() and len(t) > 1}


def similarity(raw: str, candidate: str) -> float:
    """Điểm 0..1 giữa tên trên HĐ và một tên nguyên liệu (có thể dạng "Việt / Đức")."""
    raw_tokens = tokens(raw)
    best = 0.0
    for part in re.split(r"[/|()]", candidate):
        part = part.strip()
        if not part:
            continue
        cand_tokens = tokens(part)
        if not cand_tokens or not raw_tokens:
            continue
        # So khớp từng từ (cho phép từ ghép tiếng Đức: "jasminreis" chứa "reis").
        hits = 0.0
        for ct in cand_tokens:
            score = 0.0
            for rt in raw_tokens:
                if ct == rt:
                    score = 1.0
                    break
                if len(ct) >= 4 and (ct in rt or rt in ct):
                    score = max(score, 0.85)
                else:
                    score = max(score, SequenceMatcher(None, ct, rt).ratio() if len(ct) > 3 else 0)
            hits += score if score >= 0.75 else 0
        token_score = hits / len(cand_tokens)
        seq = SequenceMatcher(None, " ".join(sorted(raw_tokens)), " ".join(sorted(cand_tokens))).ratio()
        best = max(best, 0.75 * token_score + 0.25 * seq)
    return round(best, 3)


@dataclass
class Match:
    ingredient: Ingredient | None
    score: float
    pack_factor: float
    from_alias: bool = False


def find_alias(session: Session, supplier_id: int | None, raw_name: str) -> SupplierAlias | None:
    key = normalize(raw_name)
    if supplier_id is not None:
        alias = session.scalars(
            select(SupplierAlias).where(SupplierAlias.alias == key, SupplierAlias.supplier_id == supplier_id)
        ).first()
        if alias:
            return alias
    return session.scalars(select(SupplierAlias).where(SupplierAlias.alias == key)).first()


def match_line(
    session: Session,
    supplier_id: int | None,
    raw_name: str,
    unit_raw: str = "",
    ingredients: list[Ingredient] | None = None,
    units_per_pack: float | None = None,
) -> Match:
    alias = find_alias(session, supplier_id, raw_name)
    if alias:
        factor = alias.pack_factor
        if (unit_raw or "").strip().upper() == "KG" and units_per_pack and units_per_pack > 0:
            # Hàng cân: mỗi lần một khối lượng khác -> lấy số kg trên hoá đơn, không dùng hệ số lần trước.
            factor = pack_factor_with_hint(raw_name, unit_raw, alias.ingredient, units_per_pack)
        return Match(alias.ingredient, 1.0, factor, from_alias=True)
    if ingredients is None:
        ingredients = list(session.scalars(select(Ingredient).where(Ingredient.active == 1)).all())
    best: Ingredient | None = None
    best_score = 0.0
    for ing in ingredients:
        score = similarity(raw_name, ing.name)
        if score > best_score:
            best, best_score = ing, score
    if best is None or best_score < MATCH_THRESHOLD:
        return Match(None, best_score, units_per_pack or 1.0)
    return Match(best, best_score, pack_factor_with_hint(raw_name, unit_raw, best, units_per_pack))


# Hoá đơn METRO ghi dung tích chai ở đầu tên, không có đơn vị: "0,50 DPG PET COCA-COLA", "1,00 MW SCHWEPPES".
BOTTLE_SIZE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+(?:MW|DPG|PG|EW|E)\b", re.I)
SIZE_IN_NAME = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml)\b", re.I)
MULTI_SIZE = re.compile(r"\d+\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:kg|g|l|ml)\b", re.I)  # "10x80g", "4x50g"


def pack_factor_with_hint(raw_name: str, unit_raw: str, ingredient: Ingredient, units_per_pack: float | None) -> float:
    """Số đơn vị kho trong 1 đơn vị trên hoá đơn khi hoá đơn cho biết số cái/kolli (vd. METRO: 24 chai/két).

    - Hàng cân ("KG"): units_per_pack là số kg.
    - Kho tính theo kg/l: dung tích trong tên (0,75l, 10l, 1kg) × số cái.
    - Kho tính theo cái/chai/lon: số cái.
    """
    if not units_per_pack or units_per_pack <= 0 or abs(units_per_pack - 1) < 1e-9:
        return guess_pack_factor(raw_name, unit_raw, ingredient)
    base_kind = UNIT_ALIASES.get(normalize(ingredient.unit))
    if (unit_raw or "").strip().upper() == "KG":
        return round(units_per_pack * (1000 if base_kind == "g" else 1), 6)
    if base_kind:
        size = SIZE_IN_NAME.search(raw_name or "")
        if size:
            return round(units_per_pack * _convert(to_number(size.group(1)), size.group(2).lower(), base_kind), 6)
        bottle = BOTTLE_SIZE.search(raw_name or "")
        if bottle and base_kind in ("l", "ml"):
            return round(units_per_pack * _convert(to_number(bottle.group(1)), "l", base_kind), 6)
        return units_per_pack
    return units_per_pack


def to_number(text: str) -> float:
    return float(text.replace(",", "."))


def guess_pack_factor(raw_name: str, unit_raw: str, ingredient: Ingredient) -> float:
    """Đoán số đơn vị cơ sở trong 1 đơn vị trên hoá đơn.

    Ví dụ: "Jasminreis 18kg Sack" (đơn vị Sack, kho tính kg) -> 18;
    "Coca-Cola 24x0,33l" (kho tính chai) -> 24; đơn vị HĐ trùng đơn vị mua -> pack_size.
    """
    unit = normalize(unit_raw)
    base = normalize(ingredient.unit)
    if unit and unit == base:
        return 1.0
    if UNIT_ALIASES.get(unit) and UNIT_ALIASES.get(unit) == UNIT_ALIASES.get(base):
        return 1.0
    if ingredient.pack_unit and unit and unit == normalize(ingredient.pack_unit):
        return ingredient.pack_size or 1.0
    if unit in ("g",) and base == "kg":
        return 0.001
    if unit in ("ml",) and base == "l":
        return 0.001

    text = (raw_name or "").lower().replace(",", ".")
    base_kind = UNIT_ALIASES.get(base)
    multi = re.search(r"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|g|l|ml)\b", text)
    if multi:
        count, size, u = int(multi.group(1)), float(multi.group(2)), multi.group(3)
        if base_kind is None:
            return float(count)  # kho đếm theo chai/lon/gói
        return count * _convert(size, u, base_kind)
    single = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml)\b", text)
    if single and base_kind:
        return _convert(float(single.group(1)), single.group(2), base_kind)
    count_only = re.search(r"(\d+)\s*(?:x|stk|st|er)\b", text)
    if count_only and base_kind is None:
        return float(count_only.group(1))
    if ingredient.pack_unit and ingredient.pack_size and unit in ("ka", "krt", "karton", "kiste", "thung"):
        return ingredient.pack_size
    return 1.0


def _convert(value: float, unit: str, base_kind: str) -> float:
    factors = {"kg": 1.0, "g": 0.001, "l": 1.0, "ml": 0.001}
    weight = {"kg", "g"}
    if (unit in weight) != (base_kind in weight):
        return 1.0  # khác loại đơn vị (khối lượng vs thể tích) -> không đoán
    result = value * factors[unit] / factors[base_kind]
    return round(result, 6)


def remember(
    session: Session, supplier_id: int | None, raw_name: str, ingredient_id: int, pack_factor: float
) -> None:
    key = normalize(raw_name)
    if not key:
        return
    alias = session.scalars(
        select(SupplierAlias).where(SupplierAlias.alias == key, SupplierAlias.supplier_id == supplier_id)
    ).first()
    if alias:
        alias.ingredient_id = ingredient_id
        alias.pack_factor = pack_factor
    else:
        session.add(
            SupplierAlias(
                supplier_id=supplier_id, alias=key, ingredient_id=ingredient_id, pack_factor=pack_factor
            )
        )
