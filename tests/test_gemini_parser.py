"""Testes do GeminiProvider — parser, extrator de payload (atualizado issue #15)."""

import pytest

from kiro.application.generation.gemini_provider import GeminiProvider
from kiro.domain.exceptions import LLMResponseError


# ─── _parse_response (ArticleDraft) ────────────────────────────────


def test_parse_valid_article_json():
    raw = """{
      "title": "Solução de Problemas com Push Notifications no App",
      "scope_note": "Perguntas frequentes sobre push notifications.",
      "sections": [
        {"heading": "Como ativar push", "body": "Acesse Configurações > Push."},
        {"heading": "Push não chega no iOS", "body": "Verifique APNs."}
      ],
      "tags": ["push", "notificação"]
    }"""
    article = GeminiProvider._parse_response(raw)
    assert article.title.startswith("Solução")
    assert len(article.sections) == 2
    assert article.sections[0].heading == "Como ativar push"


def test_parse_strips_markdown_fences():
    raw = (
        "```json\n"
        '{"title": "T objetivo do tema", "scope_note": "Escopo curto.", '
        '"sections": [{"heading": "h1", "body": "b1"}, {"heading": "h2", "body": "b2"}]}\n'
        "```"
    )
    article = GeminiProvider._parse_response(raw)
    assert article.title.startswith("T objetivo")


def test_parse_invalid_json_raises():
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response("isto não é json")


def test_parse_missing_sections_field_raises():
    """Schema exige sections — sem isso, falha."""
    raw = '{"title": "Solução X", "scope_note": "scope"}'
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response(raw)


def test_parse_only_1_section_raises():
    """Schema exige min 2 sections."""
    raw = """{
      "title": "T",
      "scope_note": "S",
      "sections": [{"heading": "h1", "body": "b1"}]
    }"""
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response(raw)


def test_parse_rejects_old_schema():
    """Schema antigo (problem/cause/solution) deve falhar — guia o LLM ao novo."""
    raw = """{
      "title": "T",
      "problem": "P",
      "cause": "C",
      "solution": "S"
    }"""
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response(raw)


# ─── _parse_customer_faq_response ──────────────────────────────────


def test_parse_valid_faq_json():
    raw = """{
      "title": "Dúvidas sobre Push no App",
      "scope_note": "Perguntas frequentes sobre push.",
      "entries": [
        {"question": "Como ativar?", "answer": "No painel.", "when_to_contact": null},
        {"question": "Por que não chega no iOS?", "answer": "Verifique APNs.", "when_to_contact": "Se persistir, abra ticket."},
        {"question": "Posso testar?", "answer": "Sim, use simulador.", "when_to_contact": null}
      ],
      "tags": ["push"]
    }"""
    faq = GeminiProvider._parse_customer_faq_response(raw)
    assert faq.title.startswith("Dúvidas")
    assert len(faq.entries) == 3
    assert faq.entries[0].when_to_contact is None
    assert faq.entries[1].when_to_contact is not None


def test_parse_normalizes_literal_null_strings():
    """Validator no FAQEntry transforma 'null' string em None."""
    raw = """{
      "title": "T objetivo",
      "scope_note": "S",
      "entries": [
        {"question": "q1", "answer": "a1", "when_to_contact": "null"},
        {"question": "q2", "answer": "a2", "when_to_contact": "N/A"},
        {"question": "q3", "answer": "a3", "when_to_contact": "Abrir ticket com print"}
      ],
      "tags": []
    }"""
    faq = GeminiProvider._parse_customer_faq_response(raw)
    assert faq.entries[0].when_to_contact is None
    assert faq.entries[1].when_to_contact is None
    assert faq.entries[2].when_to_contact == "Abrir ticket com print"


def test_parse_faq_rejects_old_intro_field():
    """Schema novo usa scope_note — `intro` puro deve falhar."""
    raw = """{
      "title": "T",
      "intro": "Texto",
      "entries": [
        {"question": "q1", "answer": "a1"},
        {"question": "q2", "answer": "a2"},
        {"question": "q3", "answer": "a3"}
      ]
    }"""
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_customer_faq_response(raw)


def test_parse_faq_invalid_json_raises():
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_customer_faq_response("isto não é json")


# ─── _extract_text ────────────────────────────────────────────────


def test_extract_text_from_valid_payload():
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '{"hello":'}, {"text": ' "world"}'}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }
    assert GeminiProvider._extract_text(payload) == '{"hello": "world"}'


def test_extract_text_safety_block_raises():
    payload = {
        "candidates": [
            {
                "content": {"parts": [], "role": "model"},
                "finishReason": "SAFETY",
            }
        ]
    }
    with pytest.raises(LLMResponseError) as exc:
        GeminiProvider._extract_text(payload)
    assert "SAFETY" in str(exc.value)


def test_extract_text_no_candidates_raises():
    payload = {
        "candidates": [],
        "promptFeedback": {"blockReason": "SAFETY"},
    }
    with pytest.raises(LLMResponseError):
        GeminiProvider._extract_text(payload)


# ─── construtor / fail-fast ───────────────────────────────────────


def test_init_rejects_empty_api_key():
    from kiro.domain.exceptions import LLMError
    with pytest.raises(LLMError):
        GeminiProvider(api_key="", model="gemini-2.5-flash", base_url="https://x")
