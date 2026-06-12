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
    FAQItem,
    GitBookChunk,
)


# Drafts pré-fabricados pra controlar exatamente o que o linter vai ver
_CLEAN_ARTICLE = ArticleDraft(
    title="Configurando Notificações Push no Aplicativo",
    problem=(
        "Quando o varejista habilita push notifications no painel admin, "
        "alguns clientes finais não recebem as notificações esperadas."
    ),
    cause=(
        "Pode ocorrer quando o token de dispositivo não foi sincronizado "
        "corretamente com a plataforma."
    ),
    solution=(
        "Acesse Configurações > Notificações.\n"
        "Verifique o status do canal de envio.\n"
        "Confirme permissões no aplicativo.\n"
        "Teste com a ferramenta de simulação.\n"
        "Caso persista, registre o caso."
    ),
    faq=[FAQItem(question="Como ativar?", answer="No painel > Notificações.")],
    tags=["push"],
)

# Versão com vazamento — várias regras BLOCK disparam
_DIRTY_ARTICLE = ArticleDraft(
    title="Bug em OPE-1234: regressão de push",  # block: ope code + bug + regressão
    problem=_CLEAN_ARTICLE.problem,
    cause=_CLEAN_ARTICLE.cause,
    solution=_CLEAN_ARTICLE.solution,
    faq=_CLEAN_ARTICLE.faq,
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
            title="FAQ Push",
            intro="Este FAQ cobre dúvidas comuns das equipes de produto do varejista.",
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
    """Draft só com warn (poucos passos) salva normalmente e registra warn."""
    short_steps_draft = ArticleDraft(
        title="Push Setup",
        problem=_CLEAN_ARTICLE.problem,
        cause=_CLEAN_ARTICLE.cause,
        solution="1. um\n2. dois\n3. três",  # < 4 passos = warn
        faq=_CLEAN_ARTICLE.faq,
        tags=["push"],
    )
    pipeline = _pipeline(
        llm=_CannedLLM(article_responses=[short_steps_draft]),
        linter=OutputLinter(),
        block_mode="skip",
        tmp_path=tmp_path,
    )
    result = PipelineResult(clusters=[_cluster()])
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))
    assert len(result.articles) == 1  # salvou
    assert len(result.lint_warnings) == 1
    # Conjunto de warns inclui solution_step_count
    _, warns = result.lint_warnings[0]
    assert any(w.rule_name == "solution_step_count" for w in warns)


# ─── FAQ flow ───────────────────────────────────────────────────────


def test_faq_with_block_skipped(tmp_path):
    dirty_faq = CustomerFAQ(
        title="FAQ Push",
        intro="Este FAQ cobre dúvidas comuns das equipes do varejista.",
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
