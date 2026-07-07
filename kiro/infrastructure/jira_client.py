"""Cliente HTTP para Jira Cloud (REST v3). Pagina, normaliza ADF, retry.

Usa o endpoint `/rest/api/3/search/jql` (novo) com paginação por `nextPageToken`.
O antigo `/rest/api/3/search` foi descontinuado pela Atlassian em 2025.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kiro.domain.exceptions import JiraError
from kiro.domain.models import Ticket
from kiro.utils.adf import extract_text_from_adf

log = logging.getLogger(__name__)

_BASE_FIELDS = "summary,description,labels,components,status,resolutiondate,reporter"


class JiraClient:
    def __init__(
        self,
        base_url: str,
        user_email: str,
        api_token: str,
        timeout_seconds: int = 30,
        page_size: int = 100,
        customer_profile_field: Optional[str] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (user_email, api_token)
        self._timeout = timeout_seconds
        self._page_size = page_size
        self._customer_profile_field = (customer_profile_field or "").strip() or None
        self._assets_label_cache: dict[tuple[str, str], Optional[str]] = {}

    def _search_fields(self) -> str:
        if self._customer_profile_field:
            return f"{_BASE_FIELDS},{self._customer_profile_field}"
        return _BASE_FIELDS

    def search_closed(
        self,
        project_key: str,
        closed_statuses: list[str],
        lookback_days: int,
        extra_jql: Optional[str] = None,
    ) -> list[Ticket]:
        jql = self._build_jql(project_key, closed_statuses, lookback_days, extra_jql)
        log.info("jira: JQL = %s", jql)
        tickets = [self._to_ticket(issue) for issue in self._paginate(jql)]
        log.info("jira: %d tickets coletados (lookback=%dd)", len(tickets), lookback_days)
        return tickets

    def get_board_name(self, board_id: int) -> str:
        with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
            try:
                resp = client.get(f"{self._base_url}/rest/agile/1.0/board/{board_id}")
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("name") or f"Board {board_id}")
            except httpx.HTTPStatusError as e:
                body = (e.response.text or "")[:200]
                log.error("jira board HTTP %s: %s", e.response.status_code, body)
                raise JiraError(
                    f"board Jira inválido/inacessível: {board_id} (HTTP {e.response.status_code})"
                ) from e

    def create_issue(
        self,
        *,
        project_key: str,
        issue_type: str,
        issue_type_id: Optional[str] = None,
        summary: str,
        description: str,
        labels: Optional[list[str]] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> tuple[str, str]:
        fields_payload: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": (
                {"id": str(issue_type_id).strip()}
                if (issue_type_id and str(issue_type_id).strip())
                else {"name": issue_type}
            ),
            "description": self._to_adf_paragraphs(description),
            "labels": labels or [],
        }
        if extra_fields:
            fields_payload.update(extra_fields)
        payload = {"fields": fields_payload}
        with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
            try:
                resp = client.post(
                    f"{self._base_url}/rest/api/3/issue",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                key = str(data.get("key") or "")
                if not key:
                    raise JiraError("Jira retornou issue sem key")
                url = f"{self._base_url}/browse/{key}"
                return key, url
            except httpx.HTTPStatusError as e:
                body = (e.response.text or "")[:300]
                log.error("jira create issue HTTP %s: %s", e.response.status_code, body)
                raise JiraError(
                    f"criação de card proativo falhou no Jira: HTTP {e.response.status_code}"
                ) from e

    @staticmethod
    def _to_adf_paragraphs(text: str) -> dict[str, Any]:
        paragraphs = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            paragraphs.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": stripped}],
                }
            )
        if not paragraphs:
            paragraphs = [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Sem descrição."}],
                }
            ]
        return {
            "type": "doc",
            "version": 1,
            "content": paragraphs,
        }

    @staticmethod
    def _build_jql(
        project_key: str,
        statuses: list[str],
        lookback_days: int,
        extra: Optional[str],
    ) -> str:
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        status_list = ", ".join(f'"{s}"' for s in statuses) or '"Done"'
        parts = [
            f'project = "{project_key}"',
            f"status in ({status_list})",
            f'updated >= "{since}"',
        ]
        if extra:
            parts.append(f"({extra})")
        return " AND ".join(parts) + " ORDER BY updated DESC"

    def _paginate(self, jql: str) -> Iterator[dict]:
        """Paginação por nextPageToken (endpoint /search/jql).

        O novo endpoint não retorna mais `total`; quebramos quando `nextPageToken`
        não vem na resposta (e/ou `isLast=true`).
        """
        next_token: Optional[str] = None
        with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
            while True:
                data = self._fetch_page(client, jql, next_token)
                issues = data.get("issues", []) or []
                yield from issues
                next_token = data.get("nextPageToken")
                is_last = data.get("isLast")
                if not next_token or is_last:
                    return

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def _fetch_page(
        self,
        client: httpx.Client,
        jql: str,
        next_token: Optional[str],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "jql": jql,
            "maxResults": self._page_size,
            "fields": self._search_fields(),
        }
        if next_token:
            params["nextPageToken"] = next_token
        try:
            resp = client.get(
                f"{self._base_url}/rest/api/3/search/jql",
                params=params,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:200]
            log.error("jira HTTP %s: %s", e.response.status_code, body)
            raise JiraError(
                f"busca no Jira falhou: {e.response.status_code}"
            ) from e

    @staticmethod
    def _extract_customer_name_from_profile(raw_value: Any) -> Optional[str]:
        if isinstance(raw_value, str):
            cleaned = raw_value.strip()
            return cleaned or None
        if isinstance(raw_value, dict):
            for key in ("name", "displayName", "value", "title"):
                value = raw_value.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            nested_customer = raw_value.get("customer")
            if isinstance(nested_customer, dict):
                nested_name = nested_customer.get("name")
                if isinstance(nested_name, str) and nested_name.strip():
                    return nested_name.strip()
            return None
        if isinstance(raw_value, list):
            for item in raw_value:
                candidate = JiraClient._extract_customer_name_from_profile(item)
                if candidate:
                    return candidate
        return None

    @staticmethod
    def _extract_workspace_object_ids(raw_value: Any) -> Optional[tuple[str, str]]:
        def _from_dict(item: dict[str, Any]) -> Optional[tuple[str, str]]:
            workspace_id = str(item.get("workspaceId") or "").strip()
            object_id = str(item.get("objectId") or "").strip()
            if workspace_id and object_id:
                return workspace_id, object_id

            global_id = str(item.get("id") or "").strip()
            if ":" in global_id:
                left, right = global_id.split(":", 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    return left, right
            return None

        if isinstance(raw_value, dict):
            return _from_dict(raw_value)
        if isinstance(raw_value, list):
            for item in raw_value:
                if isinstance(item, dict):
                    ids = _from_dict(item)
                    if ids:
                        return ids
        return None

    def _fetch_assets_object_label(self, workspace_id: str, object_id: str) -> Optional[str]:
        cache_key = (workspace_id, object_id)
        if cache_key in self._assets_label_cache:
            return self._assets_label_cache[cache_key]

        url = f"https://api.atlassian.com/jsm/assets/workspace/{workspace_id}/v1/object/{object_id}"
        try:
            with httpx.Client(auth=self._auth, timeout=self._timeout) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json() or {}
                label = (
                    data.get("label")
                    or data.get("name")
                    or data.get("objectKey")
                )
                cleaned = str(label).strip() if label else None
                self._assets_label_cache[cache_key] = cleaned
                return cleaned
        except httpx.HTTPError as exc:
            log.warning(
                "jira assets lookup falhou para workspace=%s object=%s: %s",
                workspace_id,
                object_id,
                exc,
            )
            self._assets_label_cache[cache_key] = None
            return None

    def _to_ticket(self, issue: dict) -> Ticket:
        fields = issue.get("fields", {}) or {}
        description = extract_text_from_adf(fields.get("description"))
        resolved_raw = fields.get("resolutiondate")
        resolved_dt: Optional[datetime] = None
        if isinstance(resolved_raw, str):
            try:
                resolved_dt = datetime.fromisoformat(resolved_raw.replace("Z", "+00:00"))
            except ValueError:
                resolved_dt = None
        customer_name: Optional[str] = None
        if self._customer_profile_field:
            raw_customer_profile = fields.get(self._customer_profile_field)
            customer_name = self._extract_customer_name_from_profile(raw_customer_profile)
            if not customer_name:
                ids = self._extract_workspace_object_ids(raw_customer_profile)
                if ids:
                    customer_name = self._fetch_assets_object_label(*ids)
        return Ticket(
            key=issue.get("key", ""),
            summary=fields.get("summary") or "",
            description=description,
            labels=fields.get("labels") or [],
            components=[c.get("name", "") for c in (fields.get("components") or []) if c],
            status=(fields.get("status") or {}).get("name"),
            resolved_at=resolved_dt,
            customer_name=customer_name,
            reporter_name=((fields.get("reporter") or {}).get("displayName") or None),
        )
