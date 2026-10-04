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
# Từ trông giống đồ uống nhưng là đồ bếp: "Schwein" (đuôi -wein), rượu/nước chua để nấu ăn.
FOOD_TOKENS = {
    "schwein", "wildschwein", "kochwein", "reiswein", "shaoxing", "mirin", "zitronensaft", "limettensaft",
    "kokoswasser", "rosenwasser",
}
# Nhóm hàng rõ ràng thuộc bếp -> giữ ở bếp dù tên có chữ giống đồ uống.
KITCHEN_CATEGORY_WORDS = (
    "thit", "hai san", "rau", "gia vi", "sot", "hang kho", "dong lanh", "bao bi", "fleisch", "fisch", "gemuse",
    "gewurz", "sosse", "sauce", "trockenware", "tiefkuhl", "verpackung", "kuche", "bep",
)

# Đồ hộp/đồ ngâm: "Thunfisch in Wasser", "Ananas in Saft", "Mais in Salzwasser" là đồ bếp.
FOOD_PHRASES = (" in wasser ", " in saft ", " in eigenem saft ", " im eigenen saft ", " in salzlake ", " in lake ")
FOOD_SUFFIXES = ("salzwasser", "schwein")
# Hàng không phải thực phẩm (thường VAT 19% nhưng thuộc bếp): hộp, túi, găng tay, chất tẩy rửa...
# So theo gốc từ để bắt cả số nhiều / từ ghép: Servietten, Handschuhe, Müllsäcke, Allzweckreiniger...
NONFOOD_STEMS = (
    "box", "schale", "beutel", "tute", "folie", "serviett", "handschuh", "reinig", "spul", "putz", "stabchen",
    "gabel", "loffel", "messer", "besteck", "deckel", "mullsack", "mullsacke", "sack", "tucher", "tuch",
    "papier", "rolle", "kerze", "schwamm", "desinfekt", "seife", "teller", "becher", "strohhalm", "trinkhalm",
)
NONFOOD_TOKENS = {"hop", "tui", "gang", "khan", "giay", "ong", "hut"}
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


def looks_like_food_exception(name: str) -> bool:
    """Tên có chữ giống đồ uống nhưng chắc chắn là đồ bếp (thịt heo, rượu nấu ăn, đồ hộp ngâm nước...)."""
    plain = _plain(name)
    if " nau an " in plain or any(phrase in plain for phrase in FOOD_PHRASES):
        return True
    return any(t in FOOD_TOKENS or t.endswith(FOOD_SUFFIXES) for t in plain.split())


def looks_like_drink(name: str) -> bool:
    if looks_like_food_exception(name):
        return False
    plain = _plain(name)
    tokens = plain.split()
    if any(phrase in plain for phrase in DRINK_PHRASES):
        return True
    for token in tokens:
        if token in DRINK_TOKENS or token in DRINK_SUFFIXES:
            return True
        if any(len(token) > len(sfx) + 2 and token.endswith(sfx) for sfx in DRINK_SUFFIXES):
            return True
        if any(token.startswith(pfx) for pfx in DRINK_PREFIXES):
            return True
    return False


def looks_like_nonfood(name: str) -> bool:
    return any(t in NONFOOD_TOKENS or any(stem in t for stem in NONFOOD_STEMS) for t in _tokens(name))


def normalize_area(value: str | None, default: str = KITCHEN) -> str:
    return value if value in AREAS else default


def is_drink_category(category: str) -> bool:
    plain = _plain(category)
    return any(f" {word} " in plain or plain.strip().startswith(word) for word in DRINK_CATEGORY_WORDS)


def is_kitchen_category(category: str) -> bool:
    if category in AREAS[KITCHEN]["categories"]:
        return True
    plain = _plain(category)
    return any(f" {word} " in plain or f" {word}" in plain for word in KITCHEN_CATEGORY_WORDS)


def guess_area(name: str = "", category: str = "", vat_rate: float | None = None) -> str:
    """Đoán khu cho hàng mới.

    Thứ tự: nhóm hàng đồ uống -> nhóm hàng bếp -> tên giống đồ uống -> hàng không phải thực phẩm (bếp)
    -> VAT 19% (ở Đức đồ uống chịu 19%, thực phẩm 7%) -> mặc định bếp.
    """
    if category and is_drink_category(category):
        return BAR
    if category and is_kitchen_category(category):
        return KITCHEN
    if looks_like_food_exception(name):  # trước bước VAT: rượu nấu ăn chịu VAT 19% nhưng vẫn là đồ bếp
        return KITCHEN
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
