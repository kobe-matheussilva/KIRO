"""Testes do CustomerFAQ — schema + null normalizer.

Testes de parser e prompt foram movidos pra test_gemini_parser.py
quando o schema foi redesenhado na issue #15.
"""

import pytest

from kiro.application.generation.mock_provider import MockLLMProvider
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
            title="Solução de Problemas com Cashback",
            scope_note="Perguntas frequentes sobre cashback.",
            entries=[FAQEntry(question="q", answer="a")],
        )


def test_customer_faq_valid_with_3_entries():
    faq = CustomerFAQ(
        title="Solução de Problemas com Cashback",
        scope_note="Perguntas frequentes sobre cashback.",
        entries=[FAQEntry(question=f"q{i}", answer=f"a{i}") for i in range(3)],
        tags=["cashback"],
    )
    assert len(faq.entries) == 3


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
