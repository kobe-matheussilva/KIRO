"""Integração do style_finder no pipeline (issue #10)."""

from pathlib import Path
from typing import Sequence
from unittest.mock import MagicMock

from kiro.application.generation.base import LLMProvider
from kiro.application.pipeline import Pipeline, PipelineRequest, PipelineResult
from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    GitBookChunk,
    Section,
)


class _CapturingLLM(LLMProvider):
    """Captura kb_context e style_examples recebidos na última chamada."""

    def __init__(self) -> None:
        self.last_kb_context: Sequence[GitBookChunk] = ()
        self.last_style_examples: Sequence[GitBookChunk] = ()

    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        self.last_kb_context = kb_context
        self.last_style_examples = style_examples
        return ArticleDraft(
            title="Solução para t",
            scope_note="scope",
            sections=[
                Section(heading="h1", body="b1"),
                Section(heading="h2", body="b2"),
            ],
        )

    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        self.last_kb_context = kb_context
        self.last_style_examples = style_examples
        return CustomerFAQ(
            title="Dúvidas sobre t",
            scope_note="scope",
            entries=[
                FAQEntry(question="q1", answer="a1"),
                FAQEntry(question="q2", answer="a2"),
                FAQEntry(question="q3", answer="a3"),
            ],
        )


def _chunk(idx: int = 0, page: str = "Page") -> GitBookChunk:
    return GitBookChunk(
        page_title=f"{page} {idx}",
        page_url=f"https://x/{idx}",
        section_title=f"S{idx}",
        section_anchor=f"s-{idx}",
        content=f"conteudo {idx}",
    )


def _cluster(topic: str = "push iOS") -> Cluster:
    return Cluster(
        topic=topic,
        tickets=["OPE-1"],
        summaries=["s"],
        labels=["push"],
        components=[],
    )


def _pipeline(
    llm: LLMProvider,
    style_finder=None,
    tmp_path: Path = None,
    dedupe_threshold: float = 0.6,
) -> Pipeline:
    store = MagicMock()
    store.root = tmp_path or Path("/tmp")
    return Pipeline(
        jira=MagicMock(),
        clustering=MagicMock(),
        llm=llm,
        store=store,
        style_finder=style_finder,
        style_top_k=2,
        dedupe_threshold=dedupe_threshold,
    )


# ─── style_examples flow ────────────────────────────────────────────


def test_pipeline_without_style_finder_passes_empty_examples(tmp_path):
    llm = _CapturingLLM()
    pipeline = _pipeline(llm, style_finder=None, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    assert llm.last_style_examples == []
    assert result.dedupe_matches == []


def test_pipeline_with_style_finder_passes_examples_to_llm(tmp_path):
    llm = _CapturingLLM()
    examples = [_chunk(0, "Cashback"), _chunk(1, "Push")]
    style_finder = MagicMock()
    style_finder.find_similar.return_value = examples
    style_finder.find_dedupe_match.return_value = None  # sem dedupe

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    style_finder.find_similar.assert_called_once()
    assert style_finder.find_similar.call_args.kwargs.get("top_k") == 2
    assert llm.last_style_examples == examples


def test_pipeline_passes_style_examples_to_faq(tmp_path):
    llm = _CapturingLLM()
    examples = [_chunk(0, "Cashback")]
    style_finder = MagicMock()
    style_finder.find_similar.return_value = examples
    style_finder.find_dedupe_match.return_value = None

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="faq"))

    assert llm.last_style_examples == examples


def test_pipeline_style_finder_exception_does_not_break(tmp_path):
    llm = _CapturingLLM()
    style_finder = MagicMock()
    style_finder.find_similar.side_effect = RuntimeError("indexer crash")
    style_finder.find_dedupe_match.return_value = None

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    # Não deve raise
    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    # LLM ainda foi chamado com style_examples=[]
    assert llm.last_style_examples == []
    assert len(result.articles) == 1


# ─── dedupe flow ────────────────────────────────────────────────────


def test_pipeline_records_dedupe_match_in_result(tmp_path):
    llm = _CapturingLLM()
    match = _chunk(0, "Cashback Existente")
    style_finder = MagicMock()
    style_finder.find_similar.return_value = []
    style_finder.find_dedupe_match.return_value = match

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    cluster = _cluster("cashback por loja")
    result = PipelineResult(clusters=[cluster])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    assert len(result.dedupe_matches) == 1
    matched_cluster, matched_chunk = result.dedupe_matches[0]
    assert matched_cluster.topic == "cashback por loja"
    assert matched_chunk.page_title == "Cashback Existente 0"


def test_pipeline_no_dedupe_match_leaves_result_empty(tmp_path):
    llm = _CapturingLLM()
    style_finder = MagicMock()
    style_finder.find_similar.return_value = []
    style_finder.find_dedupe_match.return_value = None

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    assert result.dedupe_matches == []


def test_pipeline_dedupe_match_does_not_prevent_generation(tmp_path):
    """Dedupe é sinal pro revisor — geração continua normal."""
    llm = _CapturingLLM()
    match = _chunk(0, "Existente")
    style_finder = MagicMock()
    style_finder.find_similar.return_value = []
    style_finder.find_dedupe_match.return_value = match

    pipeline = _pipeline(llm, style_finder=style_finder, tmp_path=tmp_path)
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    # Artigo foi gerado normalmente apesar do dedupe match
    assert len(result.articles) == 1
    assert len(result.dedupe_matches) == 1


def test_pipeline_dedupe_uses_configured_threshold(tmp_path):
    llm = _CapturingLLM()
    style_finder = MagicMock()
    style_finder.find_similar.return_value = []
    style_finder.find_dedupe_match.return_value = None

    pipeline = _pipeline(
        llm, style_finder=style_finder, tmp_path=tmp_path, dedupe_threshold=0.85
    )
    result = PipelineResult(clusters=[_cluster()])

    pipeline._stage_generate(result, PipelineRequest(style="artigo"))

    style_finder.find_dedupe_match.assert_called_once()
    assert style_finder.find_dedupe_match.call_args.kwargs.get("threshold") == 0.85
