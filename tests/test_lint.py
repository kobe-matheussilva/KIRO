"""Testes do OutputLinter engine — agregação + dispatch por tipo (issue #12)."""

import pytest

from kiro.application.lint import LinterResult, OutputLinter, build_output_linter
from kiro.domain.models import (
    ArticleDraft,
    CustomerFAQ,
    FAQEntry,
    FAQItem,
)


def _clean_article() -> ArticleDraft:
    return ArticleDraft(
        title="Configurando Notificações Push no Aplicativo",
        problem=(
            "Quando o varejista habilita push notifications no painel admin, "
            "alguns clientes finais não recebem as notificações esperadas "
            "no aplicativo móvel."
        ),
        cause=(
            "O comportamento pode ocorrer quando o token de dispositivo não "
            "foi sincronizado corretamente com a plataforma de envio."
        ),
        solution=(
            "Acesse Configurações > Notificações no painel admin.\n"
            "Verifique se o status do canal está ativo.\n"
            "Confirme as permissões de notificação no aplicativo.\n"
            "Teste o envio com a ferramenta de simulação.\n"
            "Caso persista, registre o caso para análise."
        ),
        faq=[
            FAQItem(question="Como ativar?", answer="No painel admin > Notificações."),
        ],
        tags=["push", "notificação"],
    )


def _clean_faq() -> CustomerFAQ:
    return CustomerFAQ(
        title="FAQ Configuração de Push Notifications",
        intro=(
            "Este FAQ cobre as dúvidas mais comuns das equipes de produto "
            "do varejista ao configurar notificações push no aplicativo."
        ),
        entries=[
            FAQEntry(question=f"P{i}?", answer="R" * 30)
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

    r = LinterResult(
        warns=[Violation("r", "warn", "f", "msg")],
    )
    assert r.is_blocked is False
    assert "0 block" in r.summary
    assert "1 warn" in r.summary


def test_result_with_blocks_is_blocked():
    from kiro.application.lint_rules import Violation

    r = LinterResult(
        blocks=[Violation("r", "block", "f", "msg")],
    )
    assert r.is_blocked is True


# ─── check_article ──────────────────────────────────────────────────


def test_clean_article_passes():
    result = OutputLinter().check_article(_clean_article())
    assert result.is_blocked is False
    assert result.blocks == []


def test_article_with_ope_code_blocked():
    draft = _clean_article()
    bad = ArticleDraft(
        title=draft.title,
        problem=draft.problem + " Relacionado a OPE-1234.",
        cause=draft.cause,
        solution=draft.solution,
        faq=draft.faq,
        tags=draft.tags,
    )
    result = OutputLinter().check_article(bad)
    assert result.is_blocked is True
    assert any(v.rule_name == "no_ope_codes" for v in result.blocks)


def test_article_with_internal_jargon_blocked():
    draft = _clean_article()
    bad = ArticleDraft(
        title=draft.title,
        problem=draft.problem,
        cause="Identificamos a root cause como falha de sincronização.",
        solution=draft.solution,
        faq=draft.faq,
        tags=draft.tags,
    )
    result = OutputLinter().check_article(bad)
    assert any(v.rule_name == "no_internal_jargon" for v in result.blocks)


def test_article_with_few_steps_warns():
    draft = _clean_article()
    bad = ArticleDraft(
        title=draft.title,
        problem=draft.problem,
        cause=draft.cause,
        solution="1. um\n2. dois\n3. três",
        faq=draft.faq,
        tags=draft.tags,
    )
    result = OutputLinter().check_article(bad)
    assert result.is_blocked is False
    assert any(v.rule_name == "solution_step_count" for v in result.warns)


def test_article_with_generic_phrase_warns():
    draft = _clean_article()
    bad = ArticleDraft(
        title=draft.title,
        problem=draft.problem,
        cause=draft.cause,
        solution=(
            "Primeiro: verifique as configurações.\n"
            "Em seguida acesse Configurações > Push.\n"
            "Salve as alterações.\n"
            "Teste o envio.\n"
            "Caso persista, registre o caso."
        ),
        faq=draft.faq,
        tags=draft.tags,
    )
    result = OutputLinter().check_article(bad)
    assert any(v.rule_name == "generic_phrases" for v in result.warns)


# ─── check_customer_faq ────────────────────────────────────────────


def test_clean_faq_passes():
    result = OutputLinter().check_customer_faq(_clean_faq())
    assert result.is_blocked is False


def test_faq_with_ope_code_blocked():
    bad = CustomerFAQ(
        title="FAQ Push",
        intro="Este FAQ cobre dúvidas comuns das equipes do varejista sobre push.",
        entries=[
            FAQEntry(
                question="Como ativar push?",
                answer="Veja OPE-9999 pra detalhes técnicos.",
            ),
        ] * 5,
        tags=["x"],
    )
    result = OutputLinter().check_customer_faq(bad)
    assert any(v.rule_name == "no_ope_codes" for v in result.blocks)


def test_faq_with_few_entries_warns():
    bad = CustomerFAQ(
        title="FAQ",
        intro="Intro suficientemente longa pra passar do mínimo configurado.",
        entries=[
            FAQEntry(question=f"q{i}", answer="r" * 30) for i in range(3)
        ],
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
        problem="curto",  # warn: muito curto
        cause="root cause aqui",  # block: jargon
        solution="só um passo",  # warn: poucos passos + curto
        faq=[FAQItem(question="q", answer="r")],
    )
    result = OutputLinter().check_article(bad)
    # Múltiplos blocks e múltiplos warns
    assert len(result.blocks) >= 2
    assert len(result.warns) >= 1
    assert result.is_blocked is True
