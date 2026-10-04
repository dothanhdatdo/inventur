"""Khu vực kho: Bếp (Küche) và Quầy đồ uống (Theke)."""
from __future__ import annotations

import re
import unicodedata

KITCHEN = "kitchen"
BAR = "bar"

AREAS: dict[str, dict] = {
    KITCHEN: {
        "label": "Bếp",
        "label_de": "Küche",
        "icon": "🍳",
        "cost_label": "Food cost",
        "categories": ["Thịt & hải sản", "Rau củ & rau thơm", "Hàng khô", "Gia vị & sốt", "Đồ đông lạnh", "Bao bì"],
    },
    BAR: {
        "label": "Quầy",
        "label_de": "Theke",
        "icon": "🍹",
        "cost_label": "Beverage cost",
        "categories": [
            "Nước ngọt / Softdrinks", "Nước / Wasser", "Nước ép / Säfte", "Bia / Bier",
            "Rượu vang / Wein", "Rượu mạnh / Spirituosen", "Cà phê & trà / Kaffee & Tee", "Quầy khác",
        ],
    },
}
AREA_KEYS = tuple(AREAS)

# Nhận diện đồ uống từ tên hàng (đã bỏ dấu, chữ thường). Tiếng Đức hay ghép từ, phần chính đứng cuối:
# "Mineralwasser", "Orangensaft", "Weißwein" -> so khớp theo đuôi từ; tránh khớp nhầm như "Tomate" (mate).
DRINK_TOKENS = {
    "rum", "gin", "sake", "soju", "tee", "mate", "bia", "ruou", "cola", "pils", "radler", "sekt", "prosecco",
    "secco", "vodka", "whisky", "whiskey", "tonic", "espresso", "cappuccino", "fanta", "sprite", "mezzo",
    "spezi", "schorle", "limonade", "energy", "redbull", "fritz", "bionade", "apfelschorle",
}
DRINK_SUFFIXES = ("wasser", "saft", "nektar", "bier", "wein", "schorle", "limonade", "likor", "schnaps", "tee", "kaffee")
DRINK_PREFIXES = ("kaffee", "cola", "whisk", "vodka", "prosecco", "espresso", "energy")
DRINK_PHRASES = (" nuoc ngot ", " nuoc suoi ", " ca phe ", " red bull ", " ginger ale ", " coca cola ", " tra sua ")
# Hàng không phải thực phẩm (thường VAT 19% nhưng thuộc bếp): hộp, túi, găng tay, chất tẩy rửa...
NONFOOD_WORDS = (
    "box", "menubox", "schale", "beutel", "tute", "folie", "alufolie", "serviette", "handschuh", "reiniger",
    "spulmittel", "putz", "stabchen", "gabel", "loffel", "hop", "tui", "gang", "deckel",
)
# Danh mục cũ (trước khi có khu vực) được xem là đồ uống.
DRINK_CATEGORY_WORDS = (
    "do uong", "getrank", "drink", "bia", "bier", "ruou", "wein", "softdrink", "softdrinks", "nuoc ngot", "nuoc ep",
    "nuoc suoi", "wasser", "saft", "safte", "spirituosen", "kaffee", "ca phe", "tee", "theke", "quay",
)


def _plain(text: str) -> str:
    text = (text or "").lower().replace("đ", "d").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def _tokens(text: str) -> list[str]:
    return _plain(text).split()


def looks_like_drink(name: str) -> bool:
    plain = _plain(name)
    if any(phrase in plain for phrase in DRINK_PHRASES):
        return True
    for token in plain.split():
        if token in DRINK_TOKENS or token in DRINK_SUFFIXES:
            return True
        if any(len(token) > len(sfx) + 2 and token.endswith(sfx) for sfx in DRINK_SUFFIXES):
            return True
        if any(token.startswith(pfx) for pfx in DRINK_PREFIXES):
            return True
    return False


def looks_like_nonfood(name: str) -> bool:
    return any(t in NONFOOD_WORDS or t.endswith(("box", "beutel", "folie", "schale")) for t in _tokens(name))


def normalize_area(value: str | None, default: str = KITCHEN) -> str:
    return value if value in AREAS else default


def is_drink_category(category: str) -> bool:
    plain = _plain(category)
    return any(f" {word} " in plain or plain.strip().startswith(word) for word in DRINK_CATEGORY_WORDS)


def guess_area(name: str = "", category: str = "", vat_rate: float | None = None) -> str:
    """Đoán khu cho hàng mới.

    Thứ tự: nhóm hàng giống đồ uống -> tên giống đồ uống -> hàng không phải thực phẩm (bếp)
    -> VAT 19% (ở Đức đồ uống chịu 19%, thực phẩm 7%) -> mặc định bếp.
    """
    if category and is_drink_category(category):
        return BAR
    if looks_like_drink(name):
        return BAR
    if looks_like_nonfood(name):
        return KITCHEN
    if vat_rate is not None and abs(vat_rate - 19) < 0.5:
        return BAR
    return KITCHEN


def label(area: str | None) -> str:
    info = AREAS.get(area or "")
    return f"{info['label']} · {info['label_de']}" if info else "Tất cả"
