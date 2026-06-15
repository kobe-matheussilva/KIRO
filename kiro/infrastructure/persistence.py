"""Persistência local de artefatos. Sempre escrita antes de publicação externa.

Após o redesign da issue #15:
- Filename: slug do título (sem prefixo `OPE-XXX_` — vazamento direto pro cliente)
- Drafts contêm APENAS conteúdo cliente-facing (sem nota interna com tickets)
- Auditoria fica em `output/audit/{slug}.json` — só pro revisor interno consultar
- Templates Markdown e DOCX seguem o padrão SUP (scope_note + sections)
"""

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, PublishResult, Ticket
from kiro.infrastructure.docx_exporter import article_to_docx, customer_faq_to_docx
from kiro.utils.branding import MARKDOWN_FOOTER

log = logging.getLogger(__name__)


class ArtifactStore:
    def __init__(self, output_dir: Path) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Subdiretórios:
        # - drafts/  → Artigo .md (cliente-facing)
        # - docs/    → Artigo .docx
        # - faqs_md/ → FAQ .md (cliente-facing)
        # - faqs_docx/ → FAQ .docx
        # - audit/   → JSON com cluster + tickets + violations (interno)
        for subdir in ("drafts", "docs", "faqs_md", "faqs_docx", "audit"):
            (self._dir / subdir).mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._dir

    def clear_drafts(self) -> int:
        """Remove drafts, FAQs e auditorias antigas. Retorna total removido."""
        removed = 0
        for subdir, pattern in (
            ("drafts", "*.md"),
            ("docs", "*.docx"),
            ("faqs_md", "*.md"),
            ("faqs_docx", "*.docx"),
            ("audit", "*.json"),
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

    def save_customer_faqs(self, faqs: list[tuple[Cluster, CustomerFAQ]]) -> Path:
        payload = [
            {"cluster": c.model_dump(mode="json"), "faq": f.model_dump(mode="json")}
            for c, f in faqs
        ]
        return self._write_json("customer_faqs.json", payload)

    def save_article_markdown(self, cluster: Cluster, article: ArticleDraft) -> Path:
        path = self._dir / "drafts" / f"{self._safe_filename(article.title)}.md"
        path.write_text(self._article_to_markdown(article), encoding="utf-8")
        log.info("draft salvo: %s", path)
        return path

    def save_article_docx(self, cluster: Cluster, article: ArticleDraft) -> Path:
        """Exporta o artigo como .docx (Word/Google Docs compatível)."""
        path = self._dir / "docs" / f"{self._safe_filename(article.title)}.docx"
        article_to_docx(article, cluster, path)
        log.info("doc salvo: %s", path)
        return path

    def save_customer_faq_markdown(self, cluster: Cluster, faq: CustomerFAQ) -> Path:
        """Salva o FAQ como Markdown legível em output/faqs_md/."""
        path = self._dir / "faqs_md" / f"{self._safe_filename(faq.title)}.md"
        path.write_text(self._faq_to_markdown(faq), encoding="utf-8")
        log.info("FAQ md salvo: %s", path)
        return path

    def save_customer_faq_docx(self, cluster: Cluster, faq: CustomerFAQ) -> Path:
        """Exporta o FAQ como .docx em output/faqs_docx/."""
        path = self._dir / "faqs_docx" / f"{self._safe_filename(faq.title)}.docx"
        customer_faq_to_docx(faq, cluster, path)
        log.info("FAQ docx salvo: %s", path)
        return path

    def save_audit(
        self,
        cluster: Cluster,
        title: str,
        kind: str,
        violations: Optional[list[dict]] = None,
    ) -> Path:
        """Salva metadados de auditoria internos em output/audit/{slug}.json.

        Esses dados NÃO vão pro cliente — ficam só pro revisor interno consultar
        depois ('esse FAQ foi gerado a partir de quais tickets?'). Substitui a
        nota com OPE- que antes era anexada no fim do `.md`.
        """
        slug = self._safe_filename(title)
        path = self._dir / "audit" / f"{slug}.json"
        payload = {
            "kind": kind,  # "article" | "faq"
            "title": title,
            "cluster_topic": cluster.topic,
            "ticket_count": cluster.count,
            "tickets": cluster.tickets[:50],
            "labels": cluster.labels,
            "components": cluster.components,
            "lint_violations": violations or [],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("auditoria salva: %s", path)
        return path

    @staticmethod
    def _safe_filename(title: str) -> str:
        """Slug do título, normalizado (ASCII, lowercase, ≤80 chars).

        Após a issue #15: NÃO prefixa mais com `OPE-XXX_`. O nome do ticket
        seria vazamento direto pro cliente se ele recebesse o arquivo.
        """
        normalized = unicodedata.normalize("NFKD", title)
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        lowered = ascii_only.lower()
        slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
        return (slug or "draft")[:80]

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
        """Gera report.md com resumo executivo da rodada."""
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
                lines.append(
                    f"   - auditoria: `output/audit/{self._safe_filename(article.title)}.json`"
                )
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

    # ─── templates Markdown ──────────────────────────────────────────

    @staticmethod
    def _article_to_markdown(article: ArticleDraft) -> str:
        """Template Article no padrão SUP (issue #15).

        Estrutura: título → scope_note em panel info → sections como H2
        com body markdown. SEM nota interna com OPE- (vai pra audit/).
        SEM "Sobre este artigo" / "Quando isso acontece" / "Como resolver".
        """
        body_parts = [
            f"# {article.title}",
            "",
            f"> [info] {article.scope_note}",
            "",
        ]
        for section in article.sections:
            body_parts.append(f"## {section.heading}")
            body_parts.append("")
            body_parts.append(section.body.strip())
            body_parts.append("")
        tags_line = ", ".join(f"`{t}`" for t in article.tags) or "—"
        body_parts.append(f"**Tags:** {tags_line}")
        body_parts.append("")
        return "\n".join(body_parts) + MARKDOWN_FOOTER

    @staticmethod
    def _faq_to_markdown(faq: CustomerFAQ) -> str:
        """Template FAQ no padrão SUP (issue #15).

        Estrutura: título → scope_note CURTO → cada entry como H2 (pergunta)
        com resposta direta. Sem preâmbulo elaborado.
        """
        body_parts = [
            f"# {faq.title}",
            "",
            f"> [info] {faq.scope_note}",
            "",
        ]
        for entry in faq.entries:
            body_parts.append(f"## {entry.question}")
            body_parts.append("")
            body_parts.append(entry.answer.strip())
            body_parts.append("")
            if entry.when_to_contact:
                body_parts.append(
                    f"> **Quando contatar o suporte:** {entry.when_to_contact}"
                )
                body_parts.append("")
        tags_line = ", ".join(f"`{t}`" for t in faq.tags) or "—"
        body_parts.append(f"**Tags:** {tags_line}")
        body_parts.append("")
        return "\n".join(body_parts) + MARKDOWN_FOOTER
