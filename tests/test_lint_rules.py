"""Testes individuais por regra de lint (issue #12)."""

import pytest

from kiro.application.lint_rules import (
    _check_code_or_trace,
    _check_external_urls,
    _check_faq_entries_count,
    _check_field_lengths,
    _check_generic_phrases,
    _check_internal_components,
    _check_internal_jargon,
    _check_ope_codes,
    _check_solution_step_count,
    _check_team_references,
    collect_article_texts,
    collect_faq_texts,
)
from kiro.domain.models import (
    ArticleDraft,
    CustomerFAQ,
    FAQEntry,
    FAQItem,
)


# ─── extratores ─────────────────────────────────────────────────────


def test_collect_article_texts_includes_faq_items():
    draft = ArticleDraft(
        title="T",
        problem="P",
        cause="C",
        solution="1. um\n2. dois",
        faq=[
            FAQItem(question="q1", answer="a1"),
            FAQItem(question="q2", answer="a2"),
        ],
    )
    fields = collect_article_texts(draft)
    assert fields["title"] == "T"
    assert fields["problem"] == "P"
    assert fields["solution"] == "1. um\n2. dois"
    assert fields["faq.0.question"] == "q1"
    assert fields["faq.1.answer"] == "a2"


def test_collect_faq_texts_includes_when_to_contact():
    draft = CustomerFAQ(
        title="T",
        intro="I",
        entries=[
            FAQEntry(question="q1", answer="a1", when_to_contact="abra ticket"),
            FAQEntry(question="q2", answer="a2"),
            FAQEntry(question="q3", answer="a3"),
        ],
    )
    fields = collect_faq_texts(draft)
    assert fields["title"] == "T"
    assert fields["entries.0.when_to_contact"] == "abra ticket"
    assert "entries.1.when_to_contact" not in fields  # None foi pulado


# ─── BLOCK: códigos OPE ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["O ticket OPE-1234 mostra", "Veja OPE-99", "ope-50 também pega (case insensitive)"],
)
def test_ope_codes_detected(text):
    v = _check_ope_codes({"problem": text})
    assert len(v) == 1
    assert v[0].severity == "block"
    assert v[0].rule_name == "no_ope_codes"


@pytest.mark.parametrize(
    "text",
    ["Texto sem nada", "operacional ainda", "open source", "OPERA é loja"],
)
def test_ope_codes_no_false_positive(text):
    assert _check_ope_codes({"problem": text}) == []


# ─── BLOCK: jargão interno ──────────────────────────────────────────


@pytest.mark.parametrize(
    "text,label",
    [
        ("essa é uma regressão recente", "regressão"),
        ("o bug aconteceu ontem", "bug"),
        ("o workaround é reiniciar", "workaround"),
        ("identificamos a root cause", "root cause"),
        ("a causa raiz foi descoberta", "causa raiz"),
        ("temos um hotfix planejado", "hotfix"),
    ],
)
def test_internal_jargon_detected(text, label):
    v = _check_internal_jargon({"problem": text})
    assert len(v) >= 1
    assert any(label in viol.message for viol in v)


def test_internal_jargon_no_false_positive_for_debug():
    # 'debug' contém 'bug' — não deve disparar regra "bug"
    v = _check_internal_jargon({"problem": "use o modo debug do navegador"})
    bug_violations = [x for x in v if "'bug'" in x.message]
    assert bug_violations == []


def test_internal_jargon_no_false_positive_for_ambiguidade():
    # 'ambiguidade' tem 'idade' mas não deve disparar nada de jargão
    v = _check_internal_jargon({"problem": "evite ambiguidade na configuração"})
    assert v == []


# ─── BLOCK: componentes internos ────────────────────────────────────


def test_internal_components_detected():
    v = _check_internal_components({"cause": "problema no WebView do iOS"})
    assert len(v) == 1
    assert "WebView" in v[0].message


def test_sdk_connect_detected():
    v = _check_internal_components({"problem": "falha no SDK Connect"})
    assert len(v) == 1
    assert "SDK Connect" in v[0].message


def test_mobile_connect_detected():
    v = _check_internal_components({"problem": "Mobile Connect SDK falhando"})
    assert len(v) == 1


# ─── BLOCK: referências de equipe ───────────────────────────────────


def test_team_references_detected():
    v = _check_team_references({"problem": "nosso backlog tem prioridade"})
    assert len(v) == 1
    assert "nosso backlog" in v[0].message


def test_team_references_case_insensitive():
    v = _check_team_references({"problem": "TIME INTERNO está revisando"})
    assert len(v) == 1


# ─── BLOCK: URLs externas ───────────────────────────────────────────


def test_external_url_gitbook_detected():
    v = _check_external_urls(
        {"solution": "veja https://kobeapps.gitbook.io/docs/push para detalhes"}
    )
    assert len(v) == 1
    # Pode bater 'gitbook.io' OU 'kobeapps.gitbook' — qualquer é correto
    assert "gitbook" in v[0].message


def test_external_url_confluence_detected():
    v = _check_external_urls(
        {"solution": "documentação em https://kobesoftware.atlassian.net/wiki/x"}
    )
    assert len(v) == 1
    assert "atlassian.net" in v[0].message


def test_external_url_safe_url_not_detected():
    # URL pública de varejista (ex.: cliente.com) NÃO deve ser bloqueada —
    # só hosts internos.
    v = _check_external_urls(
        {"solution": "acesse https://amaro.com/checkout/teste"}
    )
    assert v == []


# ─── BLOCK: código / stack trace ────────────────────────────────────


def test_code_fence_detected():
    v = _check_code_or_trace({"solution": "rode ```python\nprint(1)\n``` no terminal"})
    assert any(viol.rule_name == "no_code_or_trace" for viol in v)


def test_stack_trace_detected():
    v = _check_code_or_trace(
        {"cause": "Traceback (most recent call last):\n  at line 42"}
    )
    assert any(viol.rule_name == "no_code_or_trace" for viol in v)


def test_passos_com_seta_nao_dispara_code():
    # "Configurações > Push" tem `>` mas não é código
    v = _check_code_or_trace({"solution": "Acesse Configurações > Push"})
    assert v == []


# ─── WARN: frases genéricas ─────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "verifique as configurações",
        "limpe o cache",
        "tente novamente",
        "entre em contato com o suporte",
    ],
)
def test_generic_phrases_detected(phrase):
    v = _check_generic_phrases({"solution": f"primeiro: {phrase}"})
    assert any(viol.rule_name == "generic_phrases" for viol in v)
    assert all(viol.severity == "warn" for viol in v)


def test_specific_phrase_passes():
    v = _check_generic_phrases(
        {"solution": "Acesse Configurações > Notificações > Push"}
    )
    assert v == []


# ─── WARN: contagem de passos ───────────────────────────────────────


def test_solution_too_few_steps():
    v = _check_solution_step_count({"solution": "1. um\n2. dois\n3. três"})
    assert len(v) == 1
    assert "3 passo" in v[0].message


def test_solution_ok_steps_passes():
    sol = "1. um\n2. dois\n3. três\n4. quatro\n5. cinco"
    assert _check_solution_step_count({"solution": sol}) == []


def test_solution_absent_no_violation():
    """Se draft não tem campo solution (ex: CustomerFAQ), regra não dispara."""
    assert _check_solution_step_count({"title": "x"}) == []


# ─── WARN: tamanho de campo ─────────────────────────────────────────


def test_short_problem_field_warns():
    v = _check_field_lengths({"problem": "muito curto"})
    assert any(viol.field == "problem" for viol in v)


def test_ok_lengths_pass():
    fields = {
        "problem": "P" * 80,
        "cause": "C" * 40,
        "solution": "S" * 200,
        "intro": "I" * 60,
    }
    assert _check_field_lengths(fields) == []


# ─── WARN: FAQ entries ──────────────────────────────────────────────


def test_faq_few_entries_warns():
    fields = {
        "entries.0.question": "q0",
        "entries.1.question": "q1",
        "entries.2.question": "q2",
    }
    v = _check_faq_entries_count(fields)
    assert len(v) == 1
    assert "3 entries" in v[0].message


def test_faq_ok_entries_pass():
    fields = {f"entries.{i}.question": f"q{i}" for i in range(5)}
    assert _check_faq_entries_count(fields) == []


def test_faq_zero_entries_skipped():
    # Sem entries (não é CustomerFAQ) — regra não deve disparar
    assert _check_faq_entries_count({}) == []
