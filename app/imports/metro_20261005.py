"""Hoá đơn METRO Gundelfingen 05.10.2026 (Nr. 05.10.2026/071/0/0/0502/054905) – nhập trọn hoá đơn (bếp + quầy).

Lấy từ email hoá đơn PDF của METRO, đọc bằng app/services/pdf_invoice.py (giá NET sau Mengenrabatt).
Leergut (tiền cọc vỏ, 132,00 € net) không phải hàng tồn kho. Nắp GN (dụng cụ) chỉ ghi vào hoá đơn, không tính tồn kho.
"""
from __future__ import annotations

from datetime import date

from ..areas import BAR, KITCHEN

KEY = "metro_2026_10_05"
KIND = "full_invoice"
SUPPLIER = "METRO Gundelfingen"
SUPPLIER_NOTE = "Industriestr. 42, 79194 Gundelfingen · Tel. 0211-17607090"
INVOICE_NUMBER = "05.10.2026/071/0/0/0502/054905"
INVOICE_DATE = (2026, 10, 5, 9, 33)
SUBTOTAL = 632.60  # NETTO-WARENWERT 764,60 - Leergut 132,00
VAT = 92.98  # A 19 %: 77,10 · B 7 %: 15,88 (không gồm VAT tiền cọc)
DEPOSIT = 132.00

# Mặt hàng trong kho: tên, nhóm, đơn vị kho, đơn vị mua, số đơn vị kho / đơn vị mua (để hiển thị),
# số đơn vị kho / 1 đơn vị trên hoá đơn, ngưỡng tối thiểu, mức tồn chuẩn, khu.
# Coca-Cola / Coca-Cola Zero đã có từ hoá đơn 19.09 -> dùng lại (theo tên hàng METRO đã nhớ).
ITEMS = {
    "butter": ("Bơ / Butter mild gesalzen 250g", "Đồ mát / Mopro", "kg", "thùng", 10, 10, 2.5, 10, KITCHEN),
    "vio": ("Nước khoáng / Vio Medium 0,5l", "Nước / Wasser", "chai", "két", 18, 18, 18, 36, BAR),
    "vio_sparkling": ("Nước khoáng có ga / Vio Spritzig 0,5l", "Nước / Wasser", "chai", "két", 18, 18, 18, 36, BAR),
    "coke_zero": ("Coca-Cola Zero 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 12, 24, 108, BAR),
    "coke": ("Coca-Cola 0,5l", "Nước ngọt / Softdrinks", "chai", "thùng", 12, 12, 24, 132, BAR),
    "cleaner": ("Nước lau đa năng / Allzweckreiniger 10l", "Vệ sinh / Drogerie", "can", "", 1, 1, 1, 3, KITCHEN),
    "salt": ("Muối viên máy rửa bát / Axal Salztabletten 25kg", "Vệ sinh / Drogerie", "bao", "", 1, 1, 0, 1, KITCHEN),
    "chicken": ("Gà nguyên con / Maishähnchen Loué", "Thịt & hải sản", "kg", "", 1, 1, 0, 0, KITCHEN),
    "duck": ("Vịt nguyên con / Barbarie-Ente", "Thịt & hải sản", "kg", "", 1, 1, 0, 0, KITCHEN),
    "pork_belly": ("Ba chỉ heo / Schweinebauch", "Thịt & hải sản", "kg", "", 1, 1, 0, 0, KITCHEN),
    "pork_knuckle": ("Giò heo / Schweinshaxe (Grillhaxe)", "Thịt & hải sản", "kg", "", 1, 1, 0, 0, KITCHEN),
    "pork_feet": ("Móng giò heo / Schweinepfoten", "Thịt & hải sản", "kg", "", 1, 1, 0, 0, KITCHEN),
    "peas": ("Đậu Hà Lan / Erbsen sehr fein (TK)", "Đồ đông lạnh", "kg", "túi", 2.5, 2.5, 2.5, 10, KITCHEN),
}

# Mỗi dòng đúng thứ tự trên hoá đơn: số dòng, tên trên HĐ, mã hàng METRO, đơn vị HĐ, số lượng (Kolli; hàng cân: kg),
# số cái / Kolli (INHALT), thành tiền NET sau giảm giá, VAT %, hạn dùng (MHD), nhóm trên HĐ, mặt hàng trong kho.
LINES = [
    (0, "MPRO GN DECKEL 1/6 EDS M AUSSP", "000531.4", "ST", 5, 1, 15.70, 19, None, "Dụng cụ / Nonfood", None),
    (1, "MPRO GN DECKEL 1/4 EDS", "945746.6", "ST", 3, 1, 9.42, 19, None, "Dụng cụ / Nonfood", None),
    (2, "ARO 250g BUTTER MILD GES.", "933445.9", "KT", 2, 40, 88.80, 7, None, "Đồ mát / Mopro", "butter"),
    (3, "0,50 DPG FL VIO", "522200.5", "SP", 2, 18, 15.84, 19, None, "Đồ uống / Bier & AfG", "vio"),
    (4, "0,50 DPG PET COCA-COLA ZERO", "806563.3", "SP", 16, 12, 132.64, 19, None, "Đồ uống / Bier & AfG", "coke_zero"),
    (5, "0,50 DPG PET COCA-COLA", "806566.6", "SP", 22, 12, 182.38, 19, None, "Đồ uống / Bier & AfG", "coke"),
    (6, "0,50 DPG FL VIO SPRITZIG", "894378.9", "SP", 2, 18, 15.84, 19, None, "Đồ uống / Bier & AfG", "vio_sparkling"),
    (7, "10L ARO ALLES REINIGER", "072653.9", "KS", 3, 1, 22.47, 19, None, "Vệ sinh / Drogerie", "cleaner"),
    (8, "25kg AXAL SALZ-TABLETTE", "196982.3", "BT", 1, 1, 11.49, 19, None, "Vệ sinh / Drogerie", "salt"),
    (9, "LOUÉ FREILAND MAISHAEHNCHEN CA", "110272.2", "KG", 1.418, 1, 14.17, 7, date(2026, 10, 11), "Thịt & hải sản", "chicken"),
    (10, "LOUÉ FREILAND MAISHAEHNCHEN CA", "110272.2", "KG", 1.383, 1, 13.82, 7, date(2026, 10, 11), "Thịt & hải sản", "chicken"),
    (11, "MPM FRZ. BARBARIE EN HENRY IV", "487532.4", "KG", 1.582, 1, 14.22, 7, date(2026, 10, 9), "Thịt & hải sản", "duck"),
    (12, "MPM FRZ. BARBARIE EN HENRY IV", "487532.4", "KG", 1.651, 1, 14.84, 7, date(2026, 10, 9), "Thịt & hải sản", "duck"),
    (13, "MPM FRZ. BARBARIE EN HENRY IV", "487532.4", "KG", 1.610, 1, 14.47, 7, date(2026, 10, 9), "Thịt & hải sản", "duck"),
    (14, "REGIO SW-BAUCH E 20/50 GEZ.", "742772.7", "KG", 4.558, 1, 24.11, 7, date(2026, 10, 8), "Thịt & hải sản", "pork_belly"),
    (15, "REGIO SW-GRILLHAXEN 1/3 SCHW.", "742785.9", "KG", 1.620, 1, 4.36, 7, date(2026, 10, 10), "Thịt & hải sản", "pork_knuckle"),
    (16, "REGIO SW-GRILLHAXEN 1/3 SCHW.", "742785.9", "KG", 1.796, 1, 4.83, 7, date(2026, 10, 10), "Thịt & hải sản", "pork_knuckle"),
    (17, "REGIO SW-GRILLHAXEN 1/3 SCHW.", "742785.9", "KG", 1.738, 1, 4.68, 7, date(2026, 10, 10), "Thịt & hải sản", "pork_knuckle"),
    (18, "REGIO SW-GRILLHAXEN 1/3 SCHW.", "742785.9", "KG", 1.408, 1, 3.79, 7, date(2026, 10, 10), "Thịt & hải sản", "pork_knuckle"),
    (19, "REGIO SW-PFOTEN IM BEUTEL", "742793.3", "KG", 2.544, 1, 5.57, 7, date(2026, 10, 8), "Thịt & hải sản", "pork_feet"),
    (20, "2,5kg MC ERBSEN SEHR FEIN", "043866.3", "BT", 4, 1, 19.16, 7, None, "Đồ đông lạnh", "peas"),
]
