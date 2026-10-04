"""Hoá đơn METRO Gundelfingen 19.09.2026 (Nr. 19.09.2026/071/0/0/0501/048886) – phần đồ uống / quầy.

Lấy từ email hoá đơn PDF của METRO. Giá là giá NET sau Mengenrabatt (cột "STÜCK PREIS" trên hoá đơn).
Không gồm Leergut (tiền cọc vỏ, 126,24 € net) vì không phải hàng tồn kho, và không gồm hàng bếp/nonfood.
"""
from __future__ import annotations

KEY = "metro_2026_09_19"
SUPPLIER = "METRO Gundelfingen"
SUPPLIER_NOTE = "Industriestr. 42, 79194 Gundelfingen · Tel. 0211-17607090"
INVOICE_NUMBER = "19.09.2026/071/0/0/0501/048886"
INVOICE_DATE = (2026, 9, 19, 10, 6)

# Mỗi dòng: tên trong kho, nhóm, đơn vị kho, đơn vị mua, số đơn vị kho / đơn vị mua,
# ngưỡng tối thiểu, mức tồn chuẩn, tên trên hoá đơn, mã hàng METRO, đơn vị HĐ, số lượng (Kolli),
# thành tiền NET sau giảm giá, VAT %.
LINES = [
    # ---- Bier / AfG
    ("Bia Rothaus Tannenzäpfle 0,33l", "Bia / Bier", "chai", "két", 24, 24, 48,
     "0,33 MW ROTHAUS TANNENZAEPFLE", "124027.4", "KA", 2, 29.98, 19),
    ("Schweppes Ginger Ale 1l", "Nước ngọt / Softdrinks", "chai", "két", 6, 6, 18,
     "1,00 MW SCHWEPPES GINGER ALE", "115448.3", "SC", 3, 23.22, 19),
    ("Schweppes Russian Wild Berry 1l", "Nước ngọt / Softdrinks", "chai", "két", 6, 6, 18,
     "1,00 MW SCHWEPP.RUSS.WILD BER.", "117083.6", "SC", 3, 23.22, 19),
    ("Nước ép lý chua đen / Happy Day Schwarze Johannisbeere 1l", "Nước ép / Säfte", "hộp", "thùng", 6, 6, 12,
     "1,00 PG HAPPY DAY SCHW.JOH.", "549307.7", "KT", 2, 20.28, 19),
    ("Nước ép chanh dây / Happy Day Maracuja 1l", "Nước ép / Säfte", "hộp", "thùng", 6, 6, 12,
     "1,00 PG HAPPY DAY MARACUJA", "549308.5", "KT", 2, 20.28, 19),
    ("Nước ép táo / Happy Day Apfelsaft 1l", "Nước ép / Säfte", "hộp", "thùng", 6, 6, 12,
     "1,00 PG HAPPY DAY APFELSAFT", "551844.4", "KT", 2, 20.28, 19),
    ("Coca-Cola Zero 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 24, 108,
     "0,50 DPG PET COCA-COLA ZERO", "806563.3", "SP", 9, 74.52, 19),
    ("Coca-Cola 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 24, 132,
     "0,50 DPG PET COCA-COLA", "806566.6", "SP", 11, 91.08, 19),
    ("Lift Apfelschorle 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 12, 72,
     "0,50 DPG PET LIFT APFELSCHORLE", "806571.6", "SP", 6, 49.68, 19),
    ("Sprite 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 12, 36,
     "0,50 DPG PET SPRITE", "806576.5", "SP", 3, 24.84, 19),
    ("Trà đào / Fuze Tea Pfirsich 0,4l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 12, 72,
     "0,4 DPG FUZE TEA PFIRSICH", "914770.3", "SP", 6, 46.08, 19),
    # ---- Wein / Sekt (Chardonnay có 2 dòng trên hoá đơn)
    ("Rượu vang trắng / Tholomies Chardonnay Bio 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 12,
     "0,75l BIO THOLOMIES CHARDONNAY", "251253.1", "KT", 1, 33.54, 19),
    ("Rượu vang trắng / Tholomies Chardonnay Bio 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 12,
     "0,75l BIO THOLOMIES CHARDONNAY", "251253.1", "KT", 1, 33.54, 19),
    ("Rượu vang đỏ / Oberkircher Spätburgunder trocken 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 12,
     "E0,75L OBERKIRCHER SPTBG TR", "430651.0", "KT", 2, 56.28, 19),
    ("Rượu vang đỏ / Jechtinger Spätburgunder halbtrocken 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 12,
     "0,75l JECHTINGER VF.SPTBG HTR", "583278.7", "KT", 2, 32.28, 19),
    ("Rượu vang trắng / Vollmer Grauburgunder 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 12,
     "E0,75l VOLLMER GRAUBURGUNDER", "759529.1", "KT", 2, 63.48, 19),
    ("Rượu vang nổ / Rotkäppchen Sekt trocken 0,75l", "Rượu vang / Wein", "chai", "thùng", 6, 6, 18,
     "0,75l ROTKAEPPCHEN SEKT TR", "199490.4", "KT", 3, 44.82, 19),
    # ---- Spirituosen
    ("Aperol 1l", "Rượu mạnh / Spirituosen", "chai", "thùng", 6, 2, 6,
     "1l APEROL 11", "095553.4", "KT", 1, 77.70, 19),
    ("Rum / Havana Club 3 Años 0,7l", "Rượu mạnh / Spirituosen", "chai", "thùng", 6, 2, 6,
     "0,7l HAVANA CLUB 3 ANOS 37,5", "532207.8", "KT", 1, 77.94, 19),
    # ---- Cà phê và đồ pha cà phê ở quầy
    ("Cà phê hạt / Dallmayr Espresso d'Oro", "Cà phê & trà / Kaffee & Tee", "kg", "gói", 1, 0.5, 2,
     "1KG DALLMAYR ESPRESSO D'ORO GB", "215005.0", "PG", 1, 13.07, 7),
    ("Kem cà phê / Kaffeesahne 10% (hộp 10g)", "Cà phê & trà / Kaffee & Tee", "cái", "thùng", 120, 30, 120,
     "120X10g KAFFEESAHNE 10%", "189594.5", "KT", 1, 6.99, 7),
    ("Sữa đặc / Dovgan Kondensmilch 370g", "Cà phê & trà / Kaffee & Tee", "lon", "", 1, 1, 2,
     "370g DOVGAN KONDENSMILCH GEZ.", "458910.7", "DS", 2, 3.38, 7),
    ("Đường que / Rioba Zuckersticks 3,5g", "Cà phê & trà / Kaffee & Tee", "cái", "hộp", 1000, 200, 1000,
     "1000x3,5g RIOBA ZUCKERSTICKS", "751984.6", "KT", 1, 11.99, 7),
    ("Sữa yến mạch / Berief Bio Haferdrink 1l", "Quầy khác", "hộp", "", 1, 2, 8,
     "1L BERIEF BIO HAFER OHNE ZUCKE", "432031.3", "PG", 8, 12.56, 19),
]

# Đồ uống mẫu của bản demo (các phiên bản trước) – xoá trước khi nhập hàng thật.
DEMO_DRINK_INGREDIENTS = [
    "Coca-Cola 0,33l", "Bia Saigon 0,33l", "Fanta Orange 0,33l", "Nước suối / Mineralwasser 0,75l",
    "Nước ép xoài / Mangosaft", "Rượu vang trắng / Weißwein Riesling", "Rượu mận / Pflaumenwein",
    "Trà nhài / Jasmintee", "Cà phê hạt / Kaffeebohnen", "Sữa / Vollmilch 3,5%",
]
DEMO_DRINK_DISHES = [
    "Coca-Cola 0,33l", "Fanta 0,33l", "Mineralwasser 0,75l", "Mangosaft 0,3l", "Bia Saigon 0,33l", "Weißwein 0,2l",
    "Pflaumenwein 0,1l", "Jasmintee (Kännchen)", "Espresso", "Cà phê sữa / Milchkaffee",
]
DEMO_SUPPLIERS = ["Getränke Breisgau"]
