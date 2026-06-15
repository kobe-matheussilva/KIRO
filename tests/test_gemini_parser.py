"""Testes do GeminiProvider — parser, extrator de payload, validações de schema.

Testes do parser de ArticleDraft estão skipados durante a issue #15 — o
schema mudou (problem/cause/solution removidos, sections adicionadas). Vão
ser reescritos na camada Providers da issue #15.
"""

import pytest

from kiro.application.generation.gemini_provider import GeminiProvider
from kiro.domain.exceptions import LLMResponseError

pytestmark = pytest.mark.skip(
    reason="parser será reescrito na camada Providers da issue #15"
)


# ─── _parse_response ──────────────────────────────────────────────


def test_parse_valid_json():
    raw = """{
      "title": "Como resetar senha",
      "problem": "Usuários não conseguem resetar senha.",
      "cause": "E-mail de reset cai em spam.",
      "solution": "1. Verifique a caixa de spam\\n2. Solicite novamente",
      "faq": [{"question": "Quanto tempo demora?", "answer": "Até 5 minutos."}],
      "tags": ["senha", "reset", "spam"]
    }"""
    article = GeminiProvider._parse_response(raw)
    assert article.title == "Como resetar senha"
    assert len(article.faq) == 1
    assert article.faq[0].question == "Quanto tempo demora?"
    assert "senha" in article.tags


def test_parse_strips_markdown_fences():
    raw = (
        "```json\n"
        '{"title": "T", "problem": "P", "cause": "C", "solution": "1. a"}\n'
        "```"
    )
    article = GeminiProvider._parse_response(raw)
    assert article.title == "T"


def test_invalid_json_raises():
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response("isto definitivamente não é json")


def test_missing_required_field_raises():
    raw = '{"title": "só título"}'
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response(raw)


def test_empty_string_fields_raise():
    raw = '{"title": "", "problem": "p", "cause": "c", "solution": "s"}'
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_response(raw)


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
                "safetyRatings": [{"category": "HARM_CATEGORY_HARASSMENT", "probability": "HIGH"}],
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
    with pytest.raises(LLMResponseError) as exc:
        GeminiProvider._extract_text(payload)
    assert "SAFETY" in str(exc.value) or "candidates" in str(exc.value)


# ─── construtor / fail-fast ───────────────────────────────────────


def test_init_rejects_empty_api_key():
    from kiro.domain.exceptions import LLMError
    with pytest.raises(LLMError):
        GeminiProvider(api_key="", model="gemini-2.5-flash", base_url="https://x")
