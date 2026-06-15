"""Testes do OutputLinter engine — agregação + dispatch por tipo (issue #12).

Atualizado pra novo schema da issue #15 (Section + scope_note).
"""

import pytest

from kiro.application.lint import LinterResult, OutputLinter, build_output_linter
from kiro.domain.models import (
    ArticleDraft,
    CustomerFAQ,
    FAQEntry,
    Section,
)


def _clean_article() -> ArticleDraft:
    return ArticleDraft(
        title="Solução de Problemas com Push Notifications no App",
        scope_note=(
            "Perguntas frequentes sobre push notifications no aplicativo para "
            "equipes de produto do varejista."
        ),
        sections=[
            Section(
                heading="Como ativar push notifications no painel",
                body=(
                    "Acesse Configurações > Notificações no painel admin.\n"
                    "- Habilite o canal de envio\n"
                    "- Confirme as credenciais APNs (iOS) e FCM (Android)\n"
                    "- Teste com a ferramenta de simulação"
                ),
            ),
            Section(
                heading="Notificações não estão chegando no app iOS",
                body=(
                    "Verifique se o certificado APNs está válido e se o app "
                    "tem permissão concedida pelo usuário final."
                ),
            ),
            Section(
                heading="Como saber quem recebeu uma notificação enviada",
                body=(
                    "No painel admin > Histórico de Envios é possível ver a taxa "
                    "de entrega e abertura por campanha."
                ),
            ),
        ],
        tags=["push", "notificação"],
    )


def _clean_faq() -> CustomerFAQ:
    return CustomerFAQ(
        title="Dúvidas sobre Push Notifications no App",
        scope_note=(
            "Perguntas frequentes sobre push notifications para equipes de produto."
        ),
        entries=[
            FAQEntry(question=f"Pergunta direta {i}?", answer="R" * 30)
            for i in range(5)
        ],
        tags=["push"],
    )


# ─── construção ─────────────────────────────────────────────────────


def test_build_output_linter_disabled_returns_none():
    assert build_output_linter(enabled=False) is None


def test_build_output_linter_enabled_returns_instance():
    linter = build_output_linter(enabled=True)
    assert isinstance(linter, OutputLinter)


# ─── LinterResult ───────────────────────────────────────────────────


def test_empty_result_not_blocked():
    r = LinterResult()
    assert r.is_blocked is False
    assert r.summary == "0 block, 0 warn"


def test_result_with_only_warns_not_blocked():
    from kiro.application.lint_rules import Violation

    r = LinterResult(warns=[Violation("r", "warn", "f", "msg")])
    assert r.is_blocked is False
    assert "0 block" in r.summary
    assert "1 warn" in r.summary


def test_result_with_blocks_is_blocked():
    from kiro.application.lint_rules import Violation

    r = LinterResult(blocks=[Violation("r", "block", "f", "msg")])
    assert r.is_blocked is True


# ─── check_article ──────────────────────────────────────────────────


def test_clean_article_passes():
    result = OutputLinter().check_article(_clean_article())
    assert result.is_blocked is False
    assert result.blocks == []


def test_article_with_ope_code_in_section_blocked():
    bad = _clean_article().model_copy(
        update={
            "sections": [
                Section(
                    heading="Configurando push notifications",
                    body=(
                        "Relacionado a OPE-1234. Acesse o painel admin > "
                        "Notificações pra ativar."
                    ),
                ),
                _clean_article().sections[1],
            ]
        }
    )
    result = OutputLinter().check_article(bad)
    assert result.is_blocked is True
    assert any(v.rule_name == "no_ope_codes" for v in result.blocks)


def test_article_with_internal_jargon_in_scope_note_blocked():
    bad = _clean_article().model_copy(
        update={"scope_note": "Este artigo aborda a root cause das falhas em push."}
    )
    result = OutputLinter().check_article(bad)
    assert any(v.rule_name == "no_internal_jargon" for v in result.blocks)


def test_article_with_too_few_sections_warns():
    """Artigo com 2 sections passa schema, mas linter warna (recomendado >=3)."""
    bad = ArticleDraft(
        title=_clean_article().title,
        scope_note=_clean_article().scope_note,
        sections=_clean_article().sections[:2],  # só 2
    )
    result = OutputLinter().check_article(bad)
    assert result.is_blocked is False
    assert any(v.rule_name == "article_min_sections" for v in result.warns)


def test_article_with_generic_phrase_warns():
    bad = _clean_article().model_copy(
        update={
            "sections": [
                Section(
                    heading="Como configurar",
                    body=(
                        "Primeiro: verifique as configurações.\n"
                        "Em seguida ajuste no painel admin.\n"
                        "Teste o envio."
                    ),
                ),
                _clean_article().sections[1],
                _clean_article().sections[2],
            ]
        }
    )
    result = OutputLinter().check_article(bad)
    assert any(v.rule_name == "generic_phrases" for v in result.warns)


# ─── check_customer_faq ────────────────────────────────────────────


def test_clean_faq_passes():
    result = OutputLinter().check_customer_faq(_clean_faq())
    assert result.is_blocked is False


def test_faq_with_ope_code_in_answer_blocked():
    bad = CustomerFAQ(
        title="Dúvidas sobre Push",
        scope_note="Perguntas frequentes sobre push notifications.",
        entries=[
            FAQEntry(
                question="Como ativar push?",
                answer="Veja OPE-9999 pra detalhes técnicos.",
            )
        ] + [
            FAQEntry(question=f"q{i}", answer="r" * 30) for i in range(4)
        ],
        tags=["push"],
    )
    result = OutputLinter().check_customer_faq(bad)
    assert any(v.rule_name == "no_ope_codes" for v in result.blocks)


def test_faq_with_few_entries_warns():
    bad = CustomerFAQ(
        title="Dúvidas sobre Cashback",
        scope_note="Perguntas frequentes sobre cashback.",
        entries=[FAQEntry(question=f"q{i}", answer="r" * 30) for i in range(3)],
    )
    result = OutputLinter().check_customer_faq(bad)
    assert any(v.rule_name == "faq_entries_count" for v in result.warns)


# ─── check (dispatch) ──────────────────────────────────────────────


def test_check_dispatches_article():
    result = OutputLinter().check(_clean_article())
    assert isinstance(result, LinterResult)
    assert result.is_blocked is False


def test_check_dispatches_faq():
    result = OutputLinter().check(_clean_faq())
    assert isinstance(result, LinterResult)


def test_check_rejects_unknown_type():
    with pytest.raises(TypeError):
        OutputLinter().check("not a draft")  # type: ignore[arg-type]


# ─── agregação de múltiplas violations ──────────────────────────────


def test_multiple_violations_aggregated():
    bad = ArticleDraft(
        title="OPE-1 vazado",  # block: ope code
        scope_note="root cause aqui",  # block: jargon
        sections=[
            Section(heading="WebView falhando", body="Verifique."),  # block: component
            Section(heading="ok", body="texto"),
        ],
    )
    result = OutputLinter().check_article(bad)
    assert len(result.blocks) >= 2
    assert result.is_blocked is True
