"""Integração do OutputLinter no pipeline (issue #12)."""

from pathlib import Path
from typing import Sequence
from unittest.mock import MagicMock

import pytest

from kiro.application.generation.base import LLMProvider
from kiro.application.lint import OutputLinter
from kiro.application.pipeline import Pipeline, PipelineRequest, PipelineResult
from kiro.domain.exceptions import LinterBlocked
from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    GitBookChunk,
    Section,
)


# Drafts pré-fabricados pra controlar exatamente o que o linter vai ver
_CLEAN_ARTICLE = ArticleDraft(
    title="Configurando Notificações Push no Aplicativo",
    scope_note=(
        "Perguntas frequentes sobre configuração de push notifications "
        "no aplicativo do varejista."
    ),
    sections=[
        Section(
            heading="Como ativar push no painel admin",
            body=(
                "Acesse Configurações > Notificações.\n"
                "Habilite o canal e teste com a ferramenta de simulação."
            ),
        ),
        Section(
            heading="Push não chega no app iOS",
            body=(
                "Verifique se o certificado APNs está válido e se o "
                "usuário concedeu permissão no app."
            ),
        ),
        Section(
            heading="Como saber a taxa de entrega",
            body=(
                "No painel > Histórico de Envios é possível ver "
                "entrega e abertura por campanha."
            ),
        ),
    ],
    tags=["push"],
)

# Versão com vazamento — várias regras BLOCK disparam
_DIRTY_ARTICLE = ArticleDraft(
    title="Bug em OPE-1234: regressão de push",  # block: ope code + bug + regressão
    scope_note=_CLEAN_ARTICLE.scope_note,
    sections=_CLEAN_ARTICLE.sections,
    tags=_CLEAN_ARTICLE.tags,
)


class _CannedLLM(LLMProvider):
    """LLM que devolve drafts pré-definidos por chamada — caller controla."""

    def __init__(
        self,
        article_responses: list[ArticleDraft] = None,
        faq_responses: list[CustomerFAQ] = None,
    ) -> None:
        self._article = list(article_responses or [])
        self._faq = list(faq_responses or [])

    def generate_article(
        self, cluster, kb_context=(), style_examples=()
    ) -> ArticleDraft:
        if self._article:
            return self._article.pop(0)
        return _CLEAN_ARTICLE

    def generate_customer_faq(
        self, cluster, kb_context=(), style_examples=()
    ) -> CustomerFAQ:
        if self._faq:
            return self._faq.pop(0)
        return CustomerFAQ(
            title="Dúvidas sobre Push Notifications",
            scope_note="Perguntas frequentes sobre push notifications.",
            entries=[FAQEntry(question=f"q{i}", answer="r" * 30) for i in range(5)],
        )


def _cluster(topic: str = "push") -> Cluster:
    return Cluster(
        topic=topic,
        tickets=["OPE-1"],
        summaries=["s"],
        labels=[],
        components=[],
    )


def _pipeline(
    llm: LLMProvider,
    linter=None,
    block_mode: str = "skip",
    tmp_path: Path = None,
) -> Pipeline:
    store = MagicMock()
    store.root = tmp_path or Path("/tmp")
    return Pipeline(
        jira=MagicMock(),
        clustering=MagicMock(),
        llm=llm,
        store=store,
        linter=linter,
        linter_block_mode=block_mode,
    )


# ─── linter None ────────────────────────────────────────────────────


def test_no_linter_pipeline_passes_dirty_draft_through(tmp_path):
    """Sem linter, vazamento passa direto pro store — comportamento atual."""
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE]),
        linter=None,
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    # Artigo foi salvo, sem lint info
    assert len(result.articles) == 1
    assert result.lint_blocks == []
    assert result.lint_warnings == []


# ─── linter ativo, draft limpo ──────────────────────────────────────


def test_clean_draft_passes_linter(tmp_path):
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_CLEAN_ARTICLE]),
        linter=OutputLinter(),
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    assert len(result.articles) == 1
    assert result.lint_blocks == []


# ─── mode=skip ──────────────────────────────────────────────────────


def test_skip_mode_blocks_save_on_violation(tmp_path):
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    # NÃO salvou
    assert len(result.articles) == 0
    pipeline.store.save_article_markdown.assert_not_called()
    # Registrou block + erro
    assert len(result.lint_blocks) == 1
    assert any(e["stage"] == "lint" for e in result.errors)


def test_skip_mode_continues_to_next_cluster(tmp_path):
    """Cluster 1 bloqueado, cluster 2 limpo → segundo deve salvar normalmente."""
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE, _CLEAN_ARTICLE]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster("dirty"), _cluster("clean")])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    assert len(result.articles) == 1
    assert len(result.lint_blocks) == 1
    # O artigo salvo é o LIMPO, não o sujo
    assert result.articles[0][0].topic == "clean"


# ─── mode=fail ──────────────────────────────────────────────────────


def test_fail_mode_raises_linter_blocked(tmp_path):
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE]),
        linter=OutputLinter(),
        block_mode="fail",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    with pytest.raises(LinterBlocked):
        pipeline._stage_generate(result, PipelineRequest(style="artigo"))


# ─── mode=warn ──────────────────────────────────────────────────────


def test_warn_mode_saves_even_with_blocks(tmp_path):
    """Em mode=warn, draft com block ainda é salvo (mas registrado)."""
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE]),
        linter=OutputLinter(),
        block_mode="warn",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    # Foi salvo apesar do block
    assert len(result.articles) == 1
    pipeline.store.save_article_markdown.assert_called_once()
    # Mas o block foi registrado pra revisor
    assert len(result.lint_blocks) == 1


# ─── warnings sempre registrados ────────────────────────────────────


def test_warn_violations_recorded_even_when_not_blocked(tmp_path):
    """Draft só com warn (artigo com 2 sections em vez de 3+) salva e registra warn."""
    short_draft = ArticleDraft(
        title="Solução de Problemas com Push Notifications no App",
        scope_note=_CLEAN_ARTICLE.scope_note,
        sections=_CLEAN_ARTICLE.sections[:2],  # 2 sections → warn article_min_sections
        tags=["push"],
    )
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[short_draft]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    assert len(result.articles) == 1  # salvou
    assert len(result.lint_warnings) == 1
    _, warns = result.lint_warnings[0]
    assert any(w.rule_name == "article_min_sections" for w in warns)


# ─── FAQ flow ───────────────────────────────────────────────────────


def test_faq_with_block_skipped(tmp_path):
    dirty_faq = CustomerFAQ(
        title="Dúvidas sobre Push Notifications",
        scope_note="Perguntas frequentes sobre push notifications.",
        entries=[
            FAQEntry(question="Como funciona?", answer="Veja OPE-9999 pra detalhes."),
            FAQEntry(question="q2", answer="r" * 30),
            FAQEntry(question="q3", answer="r" * 30),
            FAQEntry(question="q4", answer="r" * 30),
            FAQEntry(question="q5", answer="r" * 30),
        ],
    )
    pipeline = _pipeline(
        llm=_CannedLLM(faq_responses=[dirty_faq]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="faq"))
    assert len(result.customer_faqs) == 0
    assert len(result.lint_blocks) == 1
    pipeline.store.save_customer_faq_markdown.assert_not_called()


# ─── audit save ──────────────────────────────────────────────────────


def test_audit_saved_after_successful_article(tmp_path):
    """Article passado pelo linter dispara save_audit com violations."""
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_CLEAN_ARTICLE]),
        linter=OutputLinter(),
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    pipeline.store.save_audit.assert_called_once()
    kwargs = pipeline.store.save_audit.call_args.kwargs
    assert kwargs["kind"] == "article"
    assert kwargs["title"] == _CLEAN_ARTICLE.title


def test_audit_saved_after_successful_faq(tmp_path):
    pipeline = _pipeline(
        llm=_CannedLLM(),  # default canned FAQ
        linter=OutputLinter(),
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="faq"))
    pipeline.store.save_audit.assert_called_once()
    assert pipeline.store.save_audit.call_args.kwargs["kind"] == "faq"


def test_audit_not_saved_when_skip_blocks(tmp_path):
    """Article bloqueado (mode=skip) NÃO gera auditoria — sem draft."""
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[_DIRTY_ARTICLE]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    pipeline.store.save_audit.assert_not_called()
