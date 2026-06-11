"""Testes do CustomerFAQ — parser do Gemini, validator de null strings, mock provider."""

import pytest

from kiro.application.generation.gemini_provider import GeminiProvider
from kiro.application.generation.mock_provider import MockLLMProvider
from kiro.domain.exceptions import LLMResponseError
from kiro.domain.models import Cluster, CustomerFAQ, FAQEntry


# ─── FAQEntry: validator de null strings ──────────────────────────


def test_faq_entry_keeps_real_text_for_when_to_contact():
    e = FAQEntry(
        question="q",
        answer="a",
        when_to_contact="Abrir ticket com print da tela.",
    )
    assert e.when_to_contact == "Abrir ticket com print da tela."


def test_faq_entry_normalizes_literal_null_string_to_none():
    """Gemini às vezes retorna 'null' como string. Tem que virar None."""
    for raw in ("null", "NULL", "None", "n/a", "  null  ", ""):
        e = FAQEntry(question="q", answer="a", when_to_contact=raw)
        assert e.when_to_contact is None, f"falhou para {raw!r}"


def test_faq_entry_accepts_actual_none():
    e = FAQEntry(question="q", answer="a", when_to_contact=None)
    assert e.when_to_contact is None


# ─── CustomerFAQ: schema validation ───────────────────────────────


def test_customer_faq_requires_at_least_3_entries():
    """Schema exige min 3 entries — FAQ com 2 deve falhar."""
    with pytest.raises(Exception):  # ValidationError
        CustomerFAQ(
            title="t",
            intro="i",
            entries=[FAQEntry(question="q", answer="a")],
        )


def test_customer_faq_valid_with_3_entries():
    faq = CustomerFAQ(
        title="t",
        intro="i",
        entries=[FAQEntry(question=f"q{i}", answer=f"a{i}") for i in range(3)],
        tags=["x"],
    )
    assert len(faq.entries) == 3


# ─── GeminiProvider: parser de CustomerFAQ ────────────────────────


def test_gemini_parses_valid_customer_faq_json():
    raw = """{
      "title": "FAQ — Login social",
      "intro": "Este FAQ aborda dúvidas sobre login social no app Amaro.",
      "entries": [
        {"question": "Por que botão não aparece?", "answer": "Verifique ativação no painel.", "when_to_contact": null},
        {"question": "Como ativar Google?", "answer": "Vá em Configurações > Integrações.", "when_to_contact": "Se persistir após 5min, abrir ticket."},
        {"question": "Posso customizar ícone?", "answer": "Sim, em Aparência > Botões.", "when_to_contact": null}
      ],
      "tags": ["login", "amaro"]
    }"""
    faq = GeminiProvider._parse_customer_faq_response(raw)
    assert faq.title == "FAQ — Login social"
    assert len(faq.entries) == 3
    assert faq.entries[0].when_to_contact is None
    assert faq.entries[1].when_to_contact is not None
    assert "amaro" in faq.tags


def test_gemini_handles_literal_null_string_in_faq_via_validator():
    """Garante que o validator do FAQEntry pega 'null' string que veio do LLM."""
    raw = """{
      "title": "t",
      "intro": "i",
      "entries": [
        {"question": "q1", "answer": "a1", "when_to_contact": "null"},
        {"question": "q2", "answer": "a2", "when_to_contact": "N/A"},
        {"question": "q3", "answer": "a3", "when_to_contact": "Abrir ticket com print"}
      ],
      "tags": []
    }"""
    faq = GeminiProvider._parse_customer_faq_response(raw)
    assert faq.entries[0].when_to_contact is None  # "null" → None
    assert faq.entries[1].when_to_contact is None  # "N/A" → None
    assert faq.entries[2].when_to_contact == "Abrir ticket com print"


def test_gemini_strips_markdown_fences_from_faq():
    raw = (
        "```json\n"
        '{"title":"t","intro":"i","entries":['
        '{"question":"q1","answer":"a1"},'
        '{"question":"q2","answer":"a2"},'
        '{"question":"q3","answer":"a3"}'
        ']}\n'
        "```"
    )
    faq = GeminiProvider._parse_customer_faq_response(raw)
    assert faq.title == "t"


def test_gemini_invalid_faq_json_raises():
    with pytest.raises(LLMResponseError):
        GeminiProvider._parse_customer_faq_response("isto não é json")


# ─── MockLLMProvider: gera FAQ válido pra dry-run ─────────────────


def test_mock_provider_returns_valid_customer_faq():
    cluster = Cluster(
        topic="Cashback",
        tickets=["OPE-1", "OPE-2", "OPE-3"],
        summaries=["s1", "s2"],
        labels=["varejo"],
    )
    faq = MockLLMProvider().generate_customer_faq(cluster)
    assert isinstance(faq, CustomerFAQ)
    assert "[DRY-RUN]" in faq.title
    assert len(faq.entries) >= 3
    assert all(isinstance(e, FAQEntry) for e in faq.entries)


# ─── Prompt do FAQ B2B: contém diretrizes-chave ───────────────────


def test_gemini_faq_prompt_includes_b2b_audience_context():
    """Sanity check do prompt: tem que mencionar varejista, painel admin, etc."""
    cluster = Cluster(
        topic="Login",
        tickets=["OPE-1", "OPE-2", "OPE-3"],
        summaries=["s"],
    )
    prompt = GeminiProvider._build_customer_faq_prompt(cluster)
    # Pontos críticos que o prompt PRECISA ter pra cumprir o brief da chefe:
    assert "varejista" in prompt.lower()
    assert "painel admin" in prompt.lower()
    assert "NÃO" in prompt  # diretrizes negativas
    assert "self-service" in prompt.lower()
    assert "OPE-XXX" in prompt  # exemplo de o que NÃO mencionar
    assert "when_to_contact" in prompt
