"""Testes individuais por regra de lint (issue #12).

Atualizado para novo schema da issue #15 (Section + scope_note).
"""

import pytest

from kiro.application.lint_rules import (
    _check_article_min_sections,
    _check_code_or_trace,
    _check_external_urls,
    _check_faq_entries_count,
    _check_field_lengths,
    _check_generic_phrases,
    _check_internal_components,
    _check_internal_jargon,
    _check_ope_codes,
    _check_team_references,
    collect_article_texts,
    collect_faq_texts,
)
from kiro.domain.models import (
    ArticleDraft,
    CustomerFAQ,
    FAQEntry,
    Section,
)


# ─── extratores ─────────────────────────────────────────────────────


def test_collect_article_texts_includes_sections():
    draft = ArticleDraft(
        title="T",
        scope_note="Escopo",
        sections=[
            Section(heading="h1", body="b1"),
            Section(heading="h2", body="b2"),
        ],
    )
    fields = collect_article_texts(draft)
    assert fields["title"] == "T"
    assert fields["scope_note"] == "Escopo"
    assert fields["sections.0.heading"] == "h1"
    assert fields["sections.0.body"] == "b1"
    assert fields["sections.1.heading"] == "h2"
    assert fields["sections.1.body"] == "b2"


def test_collect_faq_texts_includes_when_to_contact():
    draft = CustomerFAQ(
        title="T",
        scope_note="S",
        entries=[
            FAQEntry(question="q1", answer="a1", when_to_contact="abra ticket"),
            FAQEntry(question="q2", answer="a2"),
            FAQEntry(question="q3", answer="a3"),
        ],
    )
    fields = collect_faq_texts(draft)
    assert fields["title"] == "T"
    assert fields["scope_note"] == "S"
    assert fields["entries.0.when_to_contact"] == "abra ticket"
    assert "entries.1.when_to_contact" not in fields  # None foi pulado


# ─── BLOCK: códigos OPE ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["O ticket OPE-1234 mostra", "Veja OPE-99", "ope-50 também pega (case insensitive)"],
)
def test_ope_codes_detected(text):
    v = _check_ope_codes({"sections.0.body": text})
    assert len(v) == 1
    assert v[0].severity == "block"
    assert v[0].rule_name == "no_ope_codes"


@pytest.mark.parametrize(
    "text",
    ["Texto sem nada", "operacional ainda", "open source", "OPERA é loja"],
)
def test_ope_codes_no_false_positive(text):
    assert _check_ope_codes({"sections.0.body": text}) == []


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
    v = _check_internal_jargon({"sections.0.body": text})
    assert len(v) >= 1
    assert any(label in viol.message for viol in v)


def test_internal_jargon_no_false_positive_for_debug():
    # 'debug' contém 'bug' — não deve disparar regra "bug"
    v = _check_internal_jargon({"sections.0.body": "use o modo debug do navegador"})
    bug_violations = [x for x in v if "'bug'" in x.message]
    assert bug_violations == []


def test_internal_jargon_no_false_positive_for_ambiguidade():
    # 'ambiguidade' tem 'idade' mas não deve disparar nada de jargão
    v = _check_internal_jargon({"sections.0.body": "evite ambiguidade na configuração"})
    assert v == []


# ─── BLOCK: componentes internos ────────────────────────────────────


def test_internal_components_detected():
    v = _check_internal_components({"sections.0.body": "problema no WebView do iOS"})
    assert len(v) == 1
    assert "WebView" in v[0].message


def test_sdk_connect_detected():
    v = _check_internal_components({"sections.0.body": "falha no SDK Connect"})
    assert len(v) == 1
    assert "SDK Connect" in v[0].message


def test_mobile_connect_detected():
    v = _check_internal_components({"sections.0.body": "Mobile Connect SDK falhando"})
    assert len(v) == 1


# ─── BLOCK: referências de equipe ───────────────────────────────────


def test_team_references_detected():
    v = _check_team_references({"sections.0.body": "nosso backlog tem prioridade"})
    assert len(v) == 1
    assert "nosso backlog" in v[0].message


def test_team_references_case_insensitive():
    v = _check_team_references({"sections.0.body": "TIME INTERNO está revisando"})
    assert len(v) == 1


# ─── BLOCK: URLs externas ───────────────────────────────────────────


def test_external_url_gitbook_detected():
    v = _check_external_urls(
        {"sections.0.body": "veja https://kobeapps.gitbook.io/docs/push para detalhes"}
    )
    assert len(v) == 1
    # Pode bater 'gitbook.io' OU 'kobeapps.gitbook' — qualquer é correto
    assert "gitbook" in v[0].message


def test_external_url_confluence_detected():
    v = _check_external_urls(
        {"sections.0.body": "documentação em https://kobesoftware.atlassian.net/wiki/x"}
    )
    assert len(v) == 1
    assert "atlassian.net" in v[0].message


def test_external_url_safe_url_not_detected():
    # URL pública de varejista (ex.: cliente.com) NÃO deve ser bloqueada —
    # só hosts internos.
    v = _check_external_urls(
        {"sections.0.body": "acesse https://amaro.com/checkout/teste"}
    )
    assert v == []


# ─── BLOCK: código / stack trace ────────────────────────────────────


def test_code_fence_detected():
    v = _check_code_or_trace({"sections.0.body": "rode ```python\nprint(1)\n``` no terminal"})
    assert any(viol.rule_name == "no_code_or_trace" for viol in v)


def test_stack_trace_detected():
    v = _check_code_or_trace(
        {"sections.0.body": "Traceback (most recent call last):\n  at line 42"}
    )
    assert any(viol.rule_name == "no_code_or_trace" for viol in v)


def test_passos_com_seta_nao_dispara_code():
    # "Configurações > Push" tem `>` mas não é código
    v = _check_code_or_trace({"sections.0.body": "Acesse Configurações > Push"})
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
    v = _check_generic_phrases({"sections.0.body": f"primeiro: {phrase}"})
    assert any(viol.rule_name == "generic_phrases" for viol in v)
    assert all(viol.severity == "warn" for viol in v)


def test_specific_phrase_passes():
    v = _check_generic_phrases(
        {"sections.0.body": "Acesse Configurações > Notificações > Push"}
    )
    assert v == []


# ─── WARN: artigo com poucas sections ─────────────────────────────


def test_article_with_2_sections_warns():
    """Schema permite 2, mas linter recomenda 3+."""
    fields = {
        "sections.0.heading": "h0",
        "sections.0.body": "b0",
        "sections.1.heading": "h1",
        "sections.1.body": "b1",
    }
    v = _check_article_min_sections(fields)
    assert len(v) == 1
    assert "2 section" in v[0].message


def test_article_with_3_sections_passes():
    fields = {f"sections.{i}.heading": f"h{i}" for i in range(3)}
    fields.update({f"sections.{i}.body": f"b{i}" for i in range(3)})
    assert _check_article_min_sections(fields) == []


def test_article_absent_sections_no_violation():
    """CustomerFAQ não tem sections — regra não dispara."""
    assert _check_article_min_sections({"title": "x"}) == []


# ─── WARN: tamanho de campo ─────────────────────────────────────────


def test_short_scope_note_warns():
    v = _check_field_lengths({"scope_note": "muito curto"})
    assert any(viol.field == "scope_note" for viol in v)


def test_ok_lengths_pass():
    fields = {
        "scope_note": "S" * 100,
        "title": "T" * 30,
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
