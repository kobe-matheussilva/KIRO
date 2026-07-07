from pathlib import Path
from unittest.mock import MagicMock

from kiro.application.generation.base import LLMProvider
from kiro.application.pipeline import Pipeline, PipelineResult
from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, FAQEntry, Section, Ticket


class _NoopLLM(LLMProvider):
    def generate_article(self, cluster, kb_context=(), style_examples=()):
        return ArticleDraft(
            title="t",
            scope_note="s",
            sections=[Section(heading="h1", body="b1"), Section(heading="h2", body="b2")],
        )

    def generate_customer_faq(self, cluster, kb_context=(), style_examples=()):
        return CustomerFAQ(
            title="t",
            scope_note="s",
            entries=[
                FAQEntry(question="q1", answer="a1"),
                FAQEntry(question="q2", answer="a2"),
                FAQEntry(question="q3", answer="a3"),
            ],
        )

    def validate_proactive_signal(
        self,
        *,
        candidate_type,
        candidate_name,
        heuristic_score,
        rationale,
        tickets_context,
    ):
        return {
            "validation_decision": "parcial",
            "confidence": "media",
            "status_summary": "mock",
            "key_problems": rationale,
            "recommended_action": "mock",
        }


def _ticket(key: str, customer_name: str | None):
    return Ticket(
        key=key,
        summary="Resumo",
        description="Descricao",
        labels=[],
        components=[],
        customer_name=customer_name,
    )


def _pipeline(excluded_customer_names=None):
    store = MagicMock()
    store.root = Path("/tmp")
    jira = MagicMock()
    jira.search_closed.return_value = [
        _ticket("SUP-1", "Larissa Ferreira"),
        _ticket("SUP-2", "Cliente Bom"),
        _ticket("SUP-3", None),
    ]
    return Pipeline(
        jira=jira,
        clustering=MagicMock(),
        llm=_NoopLLM(),
        store=store,
        project_key="SUP",
        excluded_customer_names=excluded_customer_names,
    )


def test_stage_fetch_ignores_excluded_customer_names():
    pipeline = _pipeline(excluded_customer_names=["Larissa Ferreira"])
    result = PipelineResult()

    pipeline._stage_fetch(result)

    assert [t.key for t in result.tickets] == ["SUP-2", "SUP-3"]


def test_stage_fetch_customer_filter_is_accent_and_case_insensitive():
    pipeline = _pipeline(excluded_customer_names=["LARISSA ferreira"])
    result = PipelineResult()

    pipeline._stage_fetch(result)

    assert [t.key for t in result.tickets] == ["SUP-2", "SUP-3"]


def test_stage_fetch_ignores_when_excluded_name_is_reporter():
    store = MagicMock()
    store.root = Path("/tmp")
    jira = MagicMock()
    jira.search_closed.return_value = [
        Ticket(
            key="SUP-10",
            summary="Resumo",
            description="Descricao",
            labels=[],
            components=[],
            customer_name="Cliente Qualquer",
            reporter_name="Larissa Ferreira",
        ),
        Ticket(
            key="SUP-11",
            summary="Resumo",
            description="Descricao",
            labels=[],
            components=[],
            customer_name="Outro Cliente",
            reporter_name="Outra Pessoa",
        ),
    ]
    pipeline = Pipeline(
        jira=jira,
        clustering=MagicMock(),
        llm=_NoopLLM(),
        store=store,
        project_key="SUP",
        excluded_customer_names=["Larissa Ferreira"],
    )
    result = PipelineResult()

    pipeline._stage_fetch(result)

    assert [t.key for t in result.tickets] == ["SUP-11"]
