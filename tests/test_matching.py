from app.db import Ingredient
from app.services import matching, ocr


def ing(name, unit="kg", pack_unit="", pack_size=1.0):
    return Ingredient(name=name, unit=unit, pack_unit=pack_unit, pack_size=pack_size)


def test_similarity_german_compound_words():
    assert matching.similarity("Thai Jasminreis Duftreis 18kg Sack", "Gạo Jasmin / Jasminreis") > 0.8
    assert matching.similarity("Hähnchenbrustfilet frisch", "Ức gà / Hähnchenbrustfilet") > 0.9
    assert matching.similarity("Coca-Cola 24x0,33l Dose", "Thịt bò / Rinderhüfte") < 0.4


def test_pack_factor_guessing():
    assert matching.guess_pack_factor("Jasminreis 18kg Sack", "Sack", ing("Gạo", "kg")) == 18
    assert matching.guess_pack_factor("Coca-Cola 24x0,33l", "Karton", ing("Coca", "lon")) == 24
    assert matching.guess_pack_factor("Reisbandnudeln 400g", "Stk", ing("Bánh phở", "kg")) == 0.4
    assert matching.guess_pack_factor("Fischsauce 0,7l", "Fl", ing("Nước mắm", "l")) == 0.7
    assert matching.guess_pack_factor("Rinderhüfte", "kg", ing("Bò", "kg")) == 1
    assert matching.guess_pack_factor("Eier", "khay", ing("Trứng", "quả", "khay", 30)) == 30


def test_number_parsing():
    assert ocr.to_float("1.234,56 €") == 1234.56
    assert ocr.to_float("1,234.56") == 1234.56
    assert ocr.to_float("3,49") == 3.49
    assert str(ocr.to_date("15.08.2026")) == "2026-08-15"
