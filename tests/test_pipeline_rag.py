"""Integration do retriever com o pipeline — confirma que kb_context chega ao LLM."""

from pathlib import Path
from typing import Sequence
from unittest.mock import MagicMock

import pytest

from kiro.application.generation.base import LLMProvider
from kiro.application.pipeline import Pipeline, PipelineRequest, PipelineResult
from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    GitBookChunk,
)


# ─── fakes ──────────────────────────────────────────────────────────


class _CapturingLLM(LLMProvider):
    """Captura kb_context recebido na última chamada — pra assert direta."""

    def __init__(self) -> None:
        self.last_kb_context: Sequence[GitBookChunk] = ()

    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        self.last_kb_context = kb_context
        return ArticleDraft(
            title="t", problem="p", cause="c", solution="s",
        )

    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        self.last_kb_context = kb_context
        return CustomerFAQ(
            title="t",
            intro="i",
            entries=[
                FAQEntry(question="q1", answer="a1"),
                FAQEntry(question="q2", answer="a2"),
                FAQEntry(question="q3", answer="a3"),
            ],
        )


def _chunk(idx: int = 0) -> GitBookChunk:
    return GitBookChunk(
        page_title=f"P{idx}",
        page_url=f"https://example.com/{idx}",
        section_title=f"S{idx}",
        section_anchor=f"s-{idx}",
        content=f"conteudo {idx}",
    )


def _cluster() -> Cluster:
    return Cluster(
        topic="push iOS",
        tickets=["OPE-1"],
        summaries=["push falha"],
        labels=["push"],
        components=["mobile"],
    )


def _pipeline(llm: LLMProvider, retriever=None, tmp_path: Path = None) -> Pipeline:
    """Cria Pipeline mínimo — só o que _stage_generate precisa."""
    store = MagicMock()
    store.root = tmp_path or Path("/tmp")
    return Pipeline(
        jira=MagicMock(),
        clustering=MagicMock(),
        llm=llm,
        store=store,
        retriever=retriever,
        rag_top_k=2,
        rag_min_score=0.0,
    )


# ─── testes ─────────────────────────────────────────────────────────


def test_pipeline_without_retriever_passes_empty_kb_context(tmp_path):
    llm = _CapturingLLM()
    pipeline = _pipeline(llm, retriever=None, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    assert llm.last_kb_context == []


def test_pipeline_with_retriever_passes_chunks_to_llm(tmp_path):
    llm = _CapturingLLM()
    chunks = [_chunk(0), _chunk(1)]
    retriever = MagicMock()
    retriever.find_relevant.return_value = chunks

    pipeline = _pipeline(llm, retriever=retriever, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    retriever.find_relevant.assert_called_once()
    # find_relevant é chamado com top_k e min_score configurados na Pipeline
    assert retriever.find_relevant.call_args.kwargs.get("top_k") == 2
    assert retriever.find_relevant.call_args.kwargs.get("min_score") == 0.0
    assert llm.last_kb_context == chunks


def test_pipeline_with_retriever_passes_chunks_to_faq(tmp_path):
    llm = _CapturingLLM()
    chunks = [_chunk(0)]
    retriever = MagicMock()
    retriever.find_relevant.return_value = chunks

    pipeline = _pipeline(llm, retriever=retriever, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="faq"))

    assert llm.last_kb_context == chunks


def test_pipeline_with_retriever_empty_results_passes_empty_kb_context(tmp_path):
    llm = _CapturingLLM()
    retriever = MagicMock()
    retriever.find_relevant.return_value = []

    pipeline = _pipeline(llm, retriever=retriever, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    assert llm.last_kb_context == []


def test_pipeline_retriever_exception_does_not_break_generation(tmp_path):
    """Retrieval que explode não pode derrubar o pipeline — RAG é opcional."""
    llm = _CapturingLLM()
    retriever = MagicMock()
    retriever.find_relevant.side_effect = RuntimeError("indexer crash")

    pipeline = _pipeline(llm, retriever=retriever, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    # Não deve raise
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    # E o LLM ainda foi chamado, com kb_context vazio
    assert llm.last_kb_context == []
    assert len(result.articles) == 1
