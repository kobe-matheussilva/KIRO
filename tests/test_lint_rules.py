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
    _check_has_report_structure,
    _check_internal_components,
    _check_internal_jargon,
    _check_ope_codes,
    _check_question_too_burocratica,
    _check_scope_note_too_long,
    _check_team_references,
    _check_title_too_generic,
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


# ─── Issue #15: title_too_generic ──────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Otimizando a Execução de Testes no Seu Aplicativo",
        "Análise de Performance e Configurações de Promoções no App",
        "Boas Práticas no Aplicativo",
        "Gestão de Configurações Diversas",
    ],
)
def test_generic_title_warns(title):
    v = _check_title_too_generic({"title": title})
    assert any(viol.rule_name == "title_too_generic" for viol in v)


@pytest.mark.parametrize(
    "title",
    [
        "Solução de Problemas com Push Notifications no App",
        "Configurando Cashback por Loja Física no VTEX",
        "Dúvidas sobre Deeplink no App",
        "Entendendo o Modo Debug do Firebase",
    ],
)
def test_specific_title_passes(title):
    assert _check_title_too_generic({"title": title}) == []


# ─── Issue #15: has_report_structure ───────────────────────────────


@pytest.mark.parametrize(
    "heading",
    [
        "Sobre este artigo",
        "Visão Geral",
        "Quando isso acontece",
        "Como resolver",
        "Causa raiz",
        "Introdução",
        "Problema",
    ],
)
def test_report_heading_detected(heading):
    fields = {f"sections.0.heading": heading, "sections.0.body": "..."}
    v = _check_has_report_structure(fields)
    assert any(viol.rule_name == "has_report_structure" for viol in v)


@pytest.mark.parametrize(
    "heading",
    [
        "Como ativar push no painel",
        "Push não está chegando no iOS",
        "Deeplink X Navegador",
        "Quero excluir um link",
        "O que é preciso para ativar o Deeplink",
    ],
)
def test_natural_heading_passes(heading):
    fields = {"sections.0.heading": heading, "sections.0.body": "..."}
    assert _check_has_report_structure(fields) == []


def test_report_heading_in_title_also_warns():
    """Mesmo título não deve ter 'Sobre este artigo' como cabeçalho."""
    fields = {"title": "Sobre este artigo"}
    v = _check_has_report_structure(fields)
    assert len(v) == 1


# ─── Issue #15: scope_note_too_long ────────────────────────────────


def test_scope_note_under_threshold_passes():
    v = _check_scope_note_too_long({"scope_note": "Perguntas frequentes sobre push."})
    assert v == []


def test_scope_note_over_threshold_warns():
    long_scope = (
        "Este artigo cobre todas as configurações relacionadas ao envio de "
        "push notifications no aplicativo, incluindo cenários de iOS, Android, "
        "diferentes plataformas e-commerce integradas e configurações específicas "
        "que podem afetar o comportamento de entrega."
    )
    v = _check_scope_note_too_long({"scope_note": long_scope})
    assert any(viol.rule_name == "scope_note_too_long" for viol in v)


# ─── Issue #15: question_too_burocratica ───────────────────────────


@pytest.mark.parametrize(
    "question",
    [
        "Quais procedimentos devem ser seguidos para habilitação do recurso de push notifications no aplicativo?",
        "Qual o procedimento adequado para configuração de cashback?",
        "Quais providências adotar quando o deeplink não funciona?",
    ],
)
def test_burocratic_question_warns(question):
    fields = {"entries.0.question": question}
    v = _check_question_too_burocratica(fields)
    assert any(viol.rule_name == "question_too_burocratica" for viol in v)


@pytest.mark.parametrize(
    "question",
    [
        "Como ativo push notifications?",
        "Por que o deeplink não está abrindo?",
        "Posso testar push no simulador?",
        "Como excluir um link do direcionamento?",
    ],
)
def test_natural_question_passes(question):
    fields = {"entries.0.question": question}
    assert _check_question_too_burocratica(fields) == []


def test_long_question_warns_even_if_not_burocratic():
    """Pergunta natural mas longa demais (>80 chars) ainda warna."""
    long_q = "Como configurar push notifications no aplicativo para clientes iOS e Android com integração VTEX?"
    fields = {"entries.0.question": long_q}
    v = _check_question_too_burocratica(fields)
    assert any(viol.rule_name == "question_too_burocratica" for viol in v)
