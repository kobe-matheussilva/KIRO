"""Testes dos modelos de domínio após o redesign (issue #15)."""

import pytest
from pydantic import ValidationError

from kiro.domain.models import (
    ArticleDraft,
    CustomerFAQ,
    FAQEntry,
    Section,
)


# ─── Section ────────────────────────────────────────────────────────


def test_section_requires_heading_and_body():
    s = Section(heading="Como ativar push", body="Acesse Configurações > Push.")
    assert s.heading == "Como ativar push"
    assert s.body == "Acesse Configurações > Push."


def test_section_is_frozen():
    s = Section(heading="x", body="y")
    with pytest.raises(ValidationError):
        s.heading = "outro"  # type: ignore[misc]


def test_section_rejects_empty_fields():
    with pytest.raises(ValidationError):
        Section(heading="", body="conteudo")
    with pytest.raises(ValidationError):
        Section(heading="titulo", body="")


# ─── ArticleDraft ───────────────────────────────────────────────────


def test_article_draft_minimal_valid():
    a = ArticleDraft(
        title="Solução de Problemas com Push Notifications no App",
        scope_note="Perguntas frequentes sobre push notifications no app.",
        sections=[
            Section(heading="Como ativar push", body="Acesse o painel > Push."),
            Section(heading="Push não chega no iOS", body="Verifique APNs."),
        ],
        tags=["push", "ios"],
    )
    assert a.title.startswith("Solução")
    assert len(a.sections) == 2


def test_article_draft_requires_at_least_2_sections():
    """V1.1 #15 pediu mínimo 2 sections — sem isso vira só FAQ."""
    with pytest.raises(ValidationError):
        ArticleDraft(
            title="x",
            scope_note="y",
            sections=[Section(heading="x", body="y")],
        )


def test_article_draft_rejects_empty_title():
    with pytest.raises(ValidationError):
        ArticleDraft(
            title="",
            scope_note="y",
            sections=[
                Section(heading="a", body="b"),
                Section(heading="c", body="d"),
            ],
        )


def test_article_draft_no_longer_has_problem_cause_solution():
    """Breaking change da issue #15 — esses campos NÃO existem mais."""
    fields = set(ArticleDraft.model_fields.keys())
    assert "problem" not in fields
    assert "cause" not in fields
    assert "solution" not in fields
    assert "faq" not in fields


# ─── CustomerFAQ ────────────────────────────────────────────────────


def test_customer_faq_uses_scope_note_not_intro():
    """V1.1 #15 — intro virou scope_note (sinaliza: curto)."""
    fields = set(CustomerFAQ.model_fields.keys())
    assert "scope_note" in fields
    assert "intro" not in fields


def test_customer_faq_minimal_valid():
    faq = CustomerFAQ(
        title="Dúvidas sobre Push Notifications",
        scope_note="Perguntas frequentes sobre push notifications.",
        entries=[
            FAQEntry(question="Como ativar?", answer="No painel admin."),
            FAQEntry(question="Por que não chega?", answer="Verifique APNs."),
            FAQEntry(question="Posso testar?", answer="Use o simulador."),
        ],
    )
    assert faq.title.startswith("Dúvidas")
    assert len(faq.entries) >= 3


def test_customer_faq_requires_at_least_3_entries():
    with pytest.raises(ValidationError):
        CustomerFAQ(
            title="x",
            scope_note="y",
            entries=[FAQEntry(question="q", answer="a")],
        )


# ─── FAQEntry: validator de null strings (mantido da V1.0) ──────────


def test_faq_entry_normalizes_null_string():
    """Gemini às vezes retorna 'null' como string. Tem que virar None."""
    e = FAQEntry(question="q", answer="a", when_to_contact="null")
    assert e.when_to_contact is None


def test_faq_entry_normalizes_empty_string():
    e = FAQEntry(question="q", answer="a", when_to_contact="   ")
    assert e.when_to_contact is None


def test_faq_entry_keeps_real_text():
    e = FAQEntry(
        question="q",
        answer="a",
        when_to_contact="Abra ticket com print da tela",
    )
    assert e.when_to_contact == "Abra ticket com print da tela"
