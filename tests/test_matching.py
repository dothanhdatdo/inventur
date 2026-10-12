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


# ---------------------------------------------------------------- Gemini

import asyncio  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

from app import config  # noqa: E402


def _gemini_settings(**kw):
    base = {"provider": "gemini", "anthropic_key": "", "anthropic_model": "", "openai_key": "",
            "openai_model": "", "gemini_key": "AIza-test", "gemini_model": "gemini-flash-latest"}
    base.update(kw)
    return base


def _fake_post(monkeypatch, status, body, calls):
    async def fake(url, headers, payload):
        calls.append((url, headers, payload))
        return status, body if isinstance(body, str) else json.dumps(body)
    monkeypatch.setattr(ocr, "_post_json", fake)


def test_gemini_request_and_parse(monkeypatch):
    calls = []
    invoice = {"supplier": "Asia Großhandel Süd", "invoice_number": "R-1", "invoice_date": "2026-10-01",
               "lines": [{"name": "Jasminreis 18kg Sack", "quantity": 2, "unit": "Sack", "unit_price": 31.9,
                          "total": 63.8, "vat_rate": 7}], "subtotal": 63.8, "vat": 4.47, "total": 68.27}
    _fake_post(monkeypatch, 200, {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": json.dumps(invoice)}]}, "finishReason": "STOP"}]}, calls)
    parsed = asyncio.run(ocr.extract_invoice(b"\x89PNG", "image/png", _gemini_settings(gemini_model="models/gemini-x")))
    assert parsed.source == "gemini"
    assert parsed.supplier == "Asia Großhandel Süd" and parsed.lines[0].quantity == 2
    url, headers, payload = calls[0]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    assert headers == {"x-goog-api-key": "AIza-test"}
    part = payload["contents"][0]["parts"][0]["inline_data"]
    assert part["mime_type"] == "image/png" and part["data"] == "iVBORw=="
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("status,body,needle", [
    (429, '{"error": {"status": "RESOURCE_EXHAUSTED"}}', "hết lượt miễn phí"),
    (400, '{"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}', "key không hợp lệ"),
    (404, '{"error": {"status": "NOT_FOUND"}}', "Không tìm thấy model"),
    (200, '{"promptFeedback": {"blockReason": "SAFETY"}}', "SAFETY"),
    (500, "boom", "Gemini API lỗi 500"),
])
def test_gemini_errors_are_readable(monkeypatch, status, body, needle):
    _fake_post(monkeypatch, status, body, [])
    with pytest.raises(ocr.OcrError, match=needle):
        asyncio.run(ocr.extract_invoice(b"%PDF", "application/pdf", _gemini_settings()))


def test_auto_provider_picks_gemini_when_only_gemini_key():
    assert ocr.active_provider(_gemini_settings(provider="auto")) == "gemini"
    assert ocr.active_provider(_gemini_settings(provider="auto", gemini_key="")) == "local"  # không còn dữ liệu giả
    assert config.GEMINI_MODEL == "gemini-flash-latest"


def test_gemini_429_limit_zero_suggests_changing_model(monkeypatch):
    body = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
                       "Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, model: gemini-pro-x"}})
    _fake_post(monkeypatch, 429, body, [])
    with pytest.raises(ocr.OcrError, match="không có lượt miễn phí.*gemini-flash-latest"):
        asyncio.run(ocr.extract_invoice(b"x", "image/png", _gemini_settings(gemini_model="gemini-pro-x")))


def test_gemini_truncated_output_is_explained(monkeypatch):
    _fake_post(monkeypatch, 200, {"candidates": [{"content": {"parts": [{"text": '{"supplier": "A", "lines": ['}]},
                                                  "finishReason": "MAX_TOKENS"}]}, [])
    with pytest.raises(ocr.OcrError, match="dừng giữa chừng \\(MAX_TOKENS\\)"):
        asyncio.run(ocr.extract_invoice(b"x", "image/png", _gemini_settings()))


def test_provider_ready_requires_key():
    assert ocr.provider_ready(_gemini_settings()) is True
    assert ocr.provider_ready(_gemini_settings(gemini_key="")) is False
    assert ocr.provider_ready(_gemini_settings(provider="demo")) is False


def _anthropic_settings(model="claude-sonnet-5-5"):
    return {"provider": "anthropic", "anthropic_key": "sk-ant-x", "anthropic_model": model, "openai_key": "",
            "openai_model": "", "gemini_key": "", "gemini_model": "gemini-flash-latest"}


def test_claude_request_leaves_room_for_thinking_and_uses_low_effort(monkeypatch):
    sent = []
    reply = {"stop_reason": "end_turn", "content": [
        {"type": "thinking", "thinking": ""},  # model mới có thể trả về khối thinking trước
        {"type": "text", "text": json.dumps({"supplier": "METRO", "lines": [
            {"name": "Reis", "quantity": 1, "unit": "Sack", "unit_price": 30, "total": 30, "vat_rate": 7}]})},
    ]}

    async def fake_post(url, headers, payload):
        sent.append((url, headers, payload))
        return 200, json.dumps(reply)

    monkeypatch.setattr(ocr, "_post_json", fake_post)
    parsed = asyncio.run(ocr.extract_invoice(b"\xff\xd8\xff", "image/jpeg", _anthropic_settings()))
    assert parsed.source == "anthropic" and parsed.lines[0].name == "Reis"
    url, headers, payload = sent[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "sk-ant-x"
    assert payload["model"] == "claude-sonnet-5-5" and payload["max_tokens"] == 16000
    assert payload["output_config"] == {"effort": "low"}
    assert "thinking" not in payload and "temperature" not in payload  # tham số cũ bị model mới từ chối


@pytest.mark.parametrize("model, expected", [
    ("claude-sonnet-5-5", True), ("claude-opus-5-5", True), ("claude-haiku-5-5", True), ("claude-fable-5-1", True),
    ("claude-sonnet-4-6", True), ("claude-opus-4-5", True), ("claude-haiku-4-5", False),
    ("claude-haiku-4-5-20251001", False), ("claude-sonnet-4-5", False), ("claude-3-5-sonnet-latest", False), ("", False),
])
def test_effort_only_for_models_that_support_it(model, expected):
    assert ocr._anthropic_supports_effort(model) is expected


@pytest.mark.parametrize("stop_reason, needle", [("refusal", "từ chối"), ("max_tokens", "quá dài")])
def test_claude_stop_reasons_give_clear_messages(monkeypatch, stop_reason, needle):
    async def fake_post(url, headers, payload):
        return 200, json.dumps({"stop_reason": stop_reason, "content": [{"type": "text", "text": '{"lines": ['}]})

    monkeypatch.setattr(ocr, "_post_json", fake_post)
    with pytest.raises(ocr.OcrError, match=needle):
        asyncio.run(ocr.extract_invoice(b"\xff\xd8\xff", "image/jpeg", _anthropic_settings("claude-haiku-4-5")))
