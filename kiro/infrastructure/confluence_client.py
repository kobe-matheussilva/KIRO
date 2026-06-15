"""Cliente HTTP para Confluence Cloud. Storage Format com escaping."""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kiro.domain.exceptions import ConfluenceError
from kiro.domain.models import ArticleDraft, Cluster
from kiro.utils.branding import CONFLUENCE_FOOTER

log = logging.getLogger(__name__)


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        space_key: str,
        user_email: str,
        api_token: str,
        parent_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._space_key = space_key
        self._parent_id = parent_id or None
        self._auth = (user_email, api_token)
        self._timeout = timeout_seconds

    def create_draft(self, article: ArticleDraft, cluster: Cluster) -> str:
        month_tag = datetime.now(timezone.utc).strftime("%Y-%m")
        body = self._render_storage(article, cluster)
        payload: dict = {
            "type": "page",
            "status": "draft",
            "title": f"[{month_tag}] {article.title}",
            "space": {"key": self._space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        if self._parent_id:
            payload["ancestors"] = [{"id": self._parent_id}]
        return self._post(payload)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def _post(self, payload: dict) -> str:
        try:
            with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
                resp = client.post(f"{self._base_url}/rest/api/content", json=payload)
                resp.raise_for_status()
                data = resp.json()
                page_id = data["id"]
                url = f"{self._base_url}/pages/{page_id}"
                log.info("confluence: draft criado id=%s", page_id)
                return url
        except httpx.HTTPStatusError as e:
            log.error("confluence HTTP %s", e.response.status_code)
            raise ConfluenceError(
                f"Confluence rejeitou publicação: {e.response.status_code}"
            ) from e
        except (KeyError, ValueError) as e:
            raise ConfluenceError(f"resposta Confluence inesperada: {e}") from e

    @staticmethod
    def _escape(text: str) -> str:
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @classmethod
    def _render_storage(cls, article: ArticleDraft, cluster: Cluster) -> str:
        """Renderiza Article no padrão SUP (issue #15).

        Sem metadados internos (tickets, labels, components) — eles ficam só
        em `output/audit/`. O draft que vai pro Confluence é puramente
        cliente-facing: scope_note em panel info + sections H2.
        """
        esc = cls._escape
        sections_html = "".join(
            f"<h2>{esc(section.heading)}</h2>"
            f"<p>{esc(section.body).replace(chr(10), '<br/>')}</p>"
            for section in article.sections
        )
        tags_html = esc(", ".join(article.tags)) or "—"
        return (
            '<ac:structured-macro ac:name="info">'
            "<ac:rich-text-body>"
            f"<p>{esc(article.scope_note)}</p>"
            "</ac:rich-text-body>"
            "</ac:structured-macro>"
            f"{sections_html}"
            f"<p><strong>Tags:</strong> {tags_html}</p>"
            f"{CONFLUENCE_FOOTER}"
        )
