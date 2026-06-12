"""Orquestrador. Sabe os estágios; não sabe como cada um é implementado."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kiro.application.clustering.base import ClusteringStrategy
from kiro.application.generation.base import LLMProvider
from kiro.application.retrieval import KnowledgeRetriever
from kiro.domain.exceptions import (
    ConfluenceError,
    JiraError,
    KiroError,
    LLMError,
    SlackError,
)
from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, PublishResult, Ticket
from kiro.infrastructure.confluence_client import ConfluenceClient
from kiro.infrastructure.jira_client import JiraClient
from kiro.infrastructure.persistence import ArtifactStore
from kiro.infrastructure.slack_client import SlackClient
from kiro.utils.progress import Narrator

log = logging.getLogger(__name__)

STAGES: tuple[str, ...] = ("fetch", "cluster", "generate", "publish", "notify")


# Estilos disponíveis para os artigos gerados (escolhido por rodada).
# Todos os estilos produzem conteúdo EXTERNO/cliente-facing.
OUTPUT_STYLES: tuple[str, ...] = ("artigo", "faq")


@dataclass(frozen=True)
class PipelineRequest:
    stages: tuple[str, ...] = STAGES
    dry_run: bool = False
    publish_confluence: bool = False
    notify_slack: bool = False
    # "artigo" = texto corrido (Sobre/Quando/Como/FAQ);
    # "faq"    = perguntas e respostas self-service
    style: str = "artigo"


@dataclass
class PipelineResult:
    tickets: list[Ticket] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    articles: list[tuple[Cluster, ArticleDraft]] = field(default_factory=list)
    customer_faqs: list[tuple[Cluster, CustomerFAQ]] = field(default_factory=list)
    publish_results: list[PublishResult] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    artifacts_dir: Optional[Path] = None


class Pipeline:
    def __init__(
        self,
        jira: JiraClient,
        clustering: ClusteringStrategy,
        llm: LLMProvider,
        store: ArtifactStore,
        confluence: Optional[ConfluenceClient] = None,
        slack: Optional[SlackClient] = None,
        project_key: str = "",
        closed_statuses: Optional[list[str]] = None,
        lookback_days: int = 30,
        extra_jql: Optional[str] = None,
        llm_request_delay_seconds: float = 0.0,
        narrator: Optional[Narrator] = None,
        cluster_top_n: int = 10,
        retriever: Optional[KnowledgeRetriever] = None,
        rag_top_k: int = 3,
        rag_min_score: float = 0.1,
    ) -> None:
        self.jira = jira
        self.clustering = clustering
        self.llm = llm
        self.store = store
        self.confluence = confluence
        self.slack = slack
        self.project_key = project_key
        self.closed_statuses = closed_statuses or ["Done", "Closed", "Resolved"]
        self.lookback_days = lookback_days
        self.extra_jql = extra_jql
        self.llm_request_delay_seconds = max(0.0, llm_request_delay_seconds)
        self.narrator = narrator or Narrator(enabled=False)
        self.cluster_top_n = cluster_top_n
        self.retriever = retriever
        self.rag_top_k = rag_top_k
        self.rag_min_score = rag_min_score

    def run(self, request: PipelineRequest) -> PipelineResult:
        started = datetime.now(timezone.utc)
        result = PipelineResult(artifacts_dir=self.store.root)
        stages = set(request.stages)

        if "fetch" in stages:
            self._stage_fetch(result)
            if not result.tickets:
                return self._finalize(result, started)

        if "cluster" in stages:
            self._stage_cluster(result)
            if not result.clusters:
                return self._finalize(result, started)

        if "generate" in stages:
            self._stage_generate(result, request)

        if "publish" in stages:
            self._stage_publish(result, request)

        if "notify" in stages:
            self._stage_notify(result, request)

        return self._finalize(result, started)

    # ───────────────────────── stages ─────────────────────────

    def _stage_fetch(self, result: PipelineResult) -> None:
        try:
            with self.narrator.step(
                f"lendo tickets fechados dos últimos {self.lookback_days} dias no Jira..."
            ):
                result.tickets = self.jira.search_closed(
                    project_key=self.project_key,
                    closed_statuses=self.closed_statuses,
                    lookback_days=self.lookback_days,
                    extra_jql=self.extra_jql,
                )
            self.narrator.done(
                f"{len(result.tickets)} tickets coletados do projeto {self.project_key}"
            )
        except JiraError as e:
            self.narrator.fail(f"não consegui falar com o Jira: {e}")
            log.error("fetch falhou: %s", e)
            result.errors.append({"stage": "fetch", "error": str(e)})
        self.store.save_tickets(result.tickets)

    def _stage_cluster(self, result: PipelineResult) -> None:
        with self.narrator.step(
            f"analisando padrões recorrentes em {len(result.tickets)} tickets..."
        ):
            result.clusters = self.clustering.cluster(result.tickets)
        n = len(result.clusters)
        plural = "s" if n != 1 else ""
        self.narrator.done(
            f"{n} tema{plural} recorrente{plural} selecionado{plural} (top {self.cluster_top_n} mais frequentes)"
        )
        self.store.save_clusters(result.clusters)

    def _stage_generate(self, result: PipelineResult, request: PipelineRequest) -> None:
        self.store.clear_drafts()
        style = request.style if request.style in OUTPUT_STYLES else "artigo"
        label = "FAQ" if style == "faq" else "Artigo"
        self.narrator.section(
            f"escrevendo {len(result.clusters)} {label}s com IA (estilo: {style})"
        )
        is_mock = type(self.llm).__name__ == "MockLLMProvider"
        effective_delay = 0.0 if is_mock else self.llm_request_delay_seconds
        for idx, cluster in enumerate(result.clusters):
            if idx > 0 and effective_delay > 0:
                with self.narrator.step(
                    f"aguardando {effective_delay:.0f}s para respeitar rate limit do LLM..."
                ):
                    time.sleep(effective_delay)
            kb_context = self._fetch_kb_context(cluster)
            try:
                if style == "faq":
                    with self.narrator.step(
                        f"redigindo FAQ sobre '{cluster.topic[:60]}'..."
                    ):
                        faq = self.llm.generate_customer_faq(cluster, kb_context)
                    self.narrator.done(
                        f'"{faq.title}"  ({len(faq.entries)} perguntas, {cluster.count} tickets)'
                    )
                    result.customer_faqs.append((cluster, faq))
                    self.store.save_customer_faq_markdown(cluster, faq)
                    self.store.save_customer_faq_docx(cluster, faq)
                else:  # "artigo"
                    with self.narrator.step(
                        f"redigindo Artigo sobre '{cluster.topic[:60]}'..."
                    ):
                        article = self.llm.generate_article(cluster, kb_context)
                    self.narrator.done(f'"{article.title}"  ({cluster.count} tickets)')
                    result.articles.append((cluster, article))
                    self.store.save_article_markdown(cluster, article)
                    self.store.save_article_docx(cluster, article)
            except LLMError as e:
                self.narrator.fail(f"falhei em '{cluster.topic[:50]}' — {e}")
                log.error("LLM falhou no cluster '%s': %s", cluster.topic, e)
                result.errors.append(
                    {"stage": "generate", "style": style, "topic": cluster.topic, "error": str(e)}
                )
                continue
            except Exception as e:  # noqa: BLE001 — continue per cluster
                self.narrator.fail(f"erro inesperado em '{cluster.topic[:50]}'")
                log.exception("falha inesperada gerando %s: %s", style, cluster.topic)
                result.errors.append(
                    {"stage": "generate", "style": style, "topic": cluster.topic, "error": repr(e)}
                )
                continue

        if result.articles:
            self.store.save_articles(result.articles)
        if result.customer_faqs:
            self.store.save_customer_faqs(result.customer_faqs)

    def _stage_publish(self, result: PipelineResult, request: PipelineRequest) -> None:
        if not result.articles:
            return
        if not request.publish_confluence or self.confluence is None or request.dry_run:
            self.narrator.info("publicação no Confluence desligada — drafts disponíveis localmente")
        else:
            self.narrator.section("publicando rascunhos no Confluence")
        for cluster, article in result.articles:
            if request.publish_confluence and self.confluence is not None and not request.dry_run:
                with self.narrator.step(f"publicando '{article.title[:60]}'..."):
                    pr = self._publish_one(cluster, article, request)
                if pr.error:
                    self.narrator.fail(f"falha publicando '{article.title[:50]}' — {pr.error}")
                else:
                    self.narrator.done(f"publicado: {pr.confluence_url}")
            else:
                pr = self._publish_one(cluster, article, request)
            result.publish_results.append(pr)
            if pr.error:
                result.errors.append(
                    {"stage": "publish", "topic": cluster.topic, "error": pr.error}
                )

    def _stage_notify(self, result: PipelineResult, request: PipelineRequest) -> None:
        if not request.notify_slack or self.slack is None or request.dry_run:
            return
        try:
            with self.narrator.step("avisando o time no Slack..."):
                self.slack.notify(result.publish_results)
            self.narrator.done("notificação enviada ao Slack")
        except SlackError as e:
            self.narrator.fail(f"Slack falhou: {e}")
            log.error("slack falhou: %s", e)
            result.errors.append({"stage": "notify", "error": str(e)})

    # ────────────────────── helpers ───────────────────────────

    def _fetch_kb_context(self, cluster: Cluster) -> list:
        """Retorna chunks GitBook relevantes ou lista vazia.

        Falhas (retriever ausente, sem matches, exceção inesperada) NUNCA
        derrubam a geração — RAG é grounding opcional e o artigo deve sair
        de qualquer forma. Erros viram warning.
        """
        if self.retriever is None:
            return []
        try:
            chunks = self.retriever.find_relevant(
                cluster,
                top_k=self.rag_top_k,
                min_score=self.rag_min_score,
            )
        except Exception as e:  # noqa: BLE001 — RAG nunca pode derrubar geração
            log.warning("retrieval falhou pro cluster '%s': %s", cluster.topic, e)
            return []
        if chunks:
            log.info(
                "RAG: %d chunks injetados pro cluster '%s'", len(chunks), cluster.topic
            )
        return chunks

    def _publish_one(
        self,
        cluster: Cluster,
        article: ArticleDraft,
        request: PipelineRequest,
    ) -> PublishResult:
        local_path = str((self.store.root / "drafts").resolve())
        base = PublishResult(
            cluster_topic=cluster.topic,
            article_title=article.title,
            ticket_count=cluster.count,
            local_path=local_path,
        )
        skip_remote = (
            not request.publish_confluence
            or self.confluence is None
            or request.dry_run
        )
        if skip_remote:
            return base
        try:
            base.confluence_url = self.confluence.create_draft(article, cluster)
            return base
        except ConfluenceError as e:
            log.error("confluence falhou no cluster '%s': %s", cluster.topic, e)
            base.error = str(e)
            return base
        except KiroError as e:
            base.error = str(e)
            return base

    def _finalize(
        self, result: PipelineResult, started: datetime
    ) -> PipelineResult:
        finished = datetime.now(timezone.utc)
        self.store.save_errors(result.errors)
        self.store.save_report(
            result.publish_results,
            started,
            finished,
            articles=result.articles,
            tickets_collected=len(result.tickets),
            clusters_detected=len(result.clusters),
        )
        return result
