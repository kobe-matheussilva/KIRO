"""Persistência local de artefatos. Sempre escrita antes de publicação externa."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, PublishResult, Ticket
from kiro.infrastructure.docx_exporter import article_to_docx, customer_faq_to_docx
from kiro.utils.branding import MARKDOWN_FOOTER

# Casa "1.", "1)", "2 -", " 3. " no começo de uma linha — usado pra não numerar duas vezes
_LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[.\)\-]\s+")

log = logging.getLogger(__name__)


class ArtifactStore:
    def __init__(self, output_dir: Path) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Subdiretórios por tipo de artefato — separa KB interno (drafts/docs)
        # de FAQ B2B (faqs_md/faqs_docx) pra revisão organizada.
        for subdir in ("drafts", "docs", "faqs_md", "faqs_docx"):
            (self._dir / subdir).mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._dir

    def clear_drafts(self) -> int:
        """Remove drafts e FAQs antigos (.md e .docx). Retorna total removido."""
        removed = 0
        for subdir, pattern in (
            ("drafts", "*.md"),
            ("docs", "*.docx"),
            ("faqs_md", "*.md"),
            ("faqs_docx", "*.docx"),
        ):
            d = self._dir / subdir
            if d.exists():
                for path in d.glob(pattern):
                    path.unlink()
                    removed += 1
        if removed:
            log.info("artefatos antigos removidos: %d", removed)
        return removed

    def save_tickets(self, tickets: list[Ticket]) -> Path:
        return self._write_json(
            "tickets.json", [t.model_dump(mode="json") for t in tickets]
        )

    def save_clusters(self, clusters: list[Cluster]) -> Path:
        return self._write_json(
            "clusters.json", [c.model_dump(mode="json") for c in clusters]
        )

    def save_articles(self, articles: list[tuple[Cluster, ArticleDraft]]) -> Path:
        payload = [
            {"cluster": c.model_dump(mode="json"), "article": a.model_dump(mode="json")}
            for c, a in articles
        ]
        return self._write_json("articles.json", payload)

    def save_article_markdown(self, cluster: Cluster, article: ArticleDraft) -> Path:
        path = self._dir / "drafts" / f"{self._safe_filename(cluster, article.title)}.md"
        path.write_text(self._to_markdown(article, cluster), encoding="utf-8")
        log.info("draft salvo: %s", path)
        return path

    def save_article_docx(self, cluster: Cluster, article: ArticleDraft) -> Path:
        """Exporta o artigo como .docx (Word/Google Docs compatível)."""
        path = self._dir / "docs" / f"{self._safe_filename(cluster, article.title)}.docx"
        article_to_docx(article, cluster, path)
        log.info("doc salvo: %s", path)
        return path

    def save_customer_faq_markdown(self, cluster: Cluster, faq: CustomerFAQ) -> Path:
        """Salva o FAQ B2B como Markdown legível em output/faqs_md/."""
        path = self._dir / "faqs_md" / f"{self._safe_filename(cluster, faq.title)}.md"
        path.write_text(self._faq_to_markdown(faq, cluster), encoding="utf-8")
        log.info("FAQ md salvo: %s", path)
        return path

    def save_customer_faq_docx(self, cluster: Cluster, faq: CustomerFAQ) -> Path:
        """Exporta o FAQ B2B como .docx em output/faqs_docx/."""
        path = self._dir / "faqs_docx" / f"{self._safe_filename(cluster, faq.title)}.docx"
        customer_faq_to_docx(faq, cluster, path)
        log.info("FAQ docx salvo: %s", path)
        return path

    @staticmethod
    def _safe_filename(cluster: Cluster, title: str) -> str:
        """Slugify do título + prefixo do primeiro ticket pra dar âncora."""
        safe = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in title.lower()
        )[:60].strip("_")
        prefix = cluster.tickets[0] if cluster.tickets else "cluster"
        return f"{prefix}_{safe or 'draft'}"

    def save_errors(self, errors: list[dict[str, Any]]) -> Path:
        return self._write_json("errors.json", errors)

    def save_report(
        self,
        results: list[PublishResult],
        started_at: datetime,
        finished_at: datetime,
        articles: Optional[list[tuple[Cluster, ArticleDraft]]] = None,
        tickets_collected: int = 0,
        clusters_detected: int = 0,
    ) -> Path:
        """Gera report.md.

        Conta sucesso por etapa: tickets coletados, clusters detectados, artigos
        gerados (com IA) e artigos publicados externamente. Cada etapa é
        independente — útil quando se roda apenas `--stage generate` (sem publish).
        """
        articles = articles or []
        lines = [
            "# KIRO — Relatório de execução",
            "",
            f"- Início:  `{started_at.isoformat()}`",
            f"- Fim:     `{finished_at.isoformat()}`",
            f"- Duração: `{(finished_at - started_at).total_seconds():.1f}s`",
            "",
            "## Resumo por etapa",
            "",
            f"- Tickets coletados:    **{tickets_collected}**",
            f"- Clusters detectados:  **{clusters_detected}**",
            f"- Artigos gerados (IA): **{len(articles)}**",
            f"- Publicados externamente: **{sum(1 for r in results if r.succeeded and r.confluence_url)}**",
            f"- Falhas de publicação: **{sum(1 for r in results if not r.succeeded)}**",
            "",
        ]

        if articles:
            lines += ["## Artigos gerados pela IA", ""]
            for i, (cluster, article) in enumerate(articles, 1):
                tags = ", ".join(article.tags[:6]) or "—"
                lines.append(
                    f"{i}. **{article.title}** — {cluster.count} tickets"
                )
                lines.append(f"   - tags: {tags}")
                lines.append(f"   - tickets de origem: {', '.join(f'`{k}`' for k in cluster.tickets[:5])}{' …' if len(cluster.tickets) > 5 else ''}")
            lines.append("")

        if results:
            lines += ["## Publicação externa", ""]
            for i, r in enumerate(results, 1):
                status = "OK" if r.succeeded else "FAIL"
                url = r.confluence_url or r.local_path or "—"
                lines.append(
                    f"{i}. [{status}] **{r.article_title}** — "
                    f"{r.ticket_count} tickets — `{url}`"
                )
                if r.error:
                    lines.append(f"   - erro: `{r.error}`")
            lines.append("")

        if not articles and not results:
            lines.append("_Nenhum artigo gerado nem publicado nessa rodada._")

        path = self._dir / "report.md"
        path.write_text("\n".join(lines) + "\n" + MARKDOWN_FOOTER, encoding="utf-8")
        log.info("relatório salvo: %s", path)
        return path

    def _write_json(self, filename: str, data: Any) -> Path:
        path = self._dir / filename
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("artefato salvo: %s", path)
        return path

    @staticmethod
    def _to_markdown(article: ArticleDraft, cluster: Cluster) -> str:
        steps = [
            _LEADING_NUMBER_RE.sub("", s).strip()
            for s in article.solution.split("\n")
            if s.strip()
        ]
        steps_md = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps) if s)
        faq_md = "\n".join(
            f"**{f.question}**\n\n{f.answer}\n" for f in article.faq
        )
        tickets_md = ", ".join(f"`{k}`" for k in cluster.tickets[:15])
        return (
            f"# {article.title}\n\n"
            f"> Rascunho gerado a partir de {cluster.count} tickets.\n\n"
            f"**Tickets de origem:** {tickets_md}\n\n"
            f"## Problema\n\n{article.problem}\n\n"
            f"## Causa raiz\n\n{article.cause}\n\n"
            f"## Solução\n\n{steps_md}\n\n"
            f"## Perguntas frequentes\n\n{faq_md or '_Sem FAQ._'}\n\n"
            f"## Metadados\n\n"
            f"- Componentes: {', '.join(cluster.components) or '—'}\n"
            f"- Labels: {', '.join(cluster.labels) or '—'}\n"
            f"- Tags: {', '.join(article.tags) or '—'}\n"
            f"{MARKDOWN_FOOTER}"
        )

    @staticmethod
    def _faq_to_markdown(faq: CustomerFAQ, cluster: Cluster) -> str:
        """Renderiza CustomerFAQ como Markdown — formato leve pra Google Docs e Confluence."""
        entries_md = []
        for idx, entry in enumerate(faq.entries, start=1):
            block = f"### {idx}. {entry.question}\n\n{entry.answer}\n"
            if entry.when_to_contact:
                block += (
                    f"\n> **Quando abrir um ticket de suporte:** {entry.when_to_contact}\n"
                )
            entries_md.append(block)
        tags_md = ", ".join(f"`{t}`" for t in faq.tags) or "—"
        return (
            f"# {faq.title}\n\n"
            f"> FAQ self-service para o time de produto/operação do varejista — "
            f"gerada a partir de {cluster.count} tickets recorrentes.\n\n"
            f"{faq.intro}\n\n"
            f"## Perguntas frequentes\n\n"
            + "\n".join(entries_md)
            + f"\n## Tags\n\n{tags_md}\n"
            + f"{MARKDOWN_FOOTER}"
        )
