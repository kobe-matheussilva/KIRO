"""Scraper do Confluence space SUP — style reference (issue #10).

Diferente do gitbook_loader, aqui pegamos via Confluence REST API
autenticada e o body vem como ADF JSON. Convertemos pra markdown
preservando estrutura, chunkamos por heading (igual GitBook) e filtramos
páginas meta (homepage, índices, fluxos de uso do próprio Confluence)
que não servem como exemplo de tom.

NÃO usado pra grounding factual — esse é o papel do GitBook (issue #3).
SUP é exclusivamente style reference + sinal de dedupe.
"""

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from kiro.domain.models import GitBookChunk, ScrapingResult
from kiro.utils.adf_to_markdown import adf_to_markdown
from kiro.utils.progress import Narrator

log = logging.getLogger(__name__)


# Heurística pra excluir páginas que NÃO servem como style reference.
# Comparação após normalização (lower + sem acento) contra prefixos.
# Conservador: só pula títulos claramente meta. Falsos negativos (deixar
# entrar uma página meta) são preferíveis a falsos positivos (cortar
# conteúdo útil).
_META_TITLE_PREFIXES: tuple[str, ...] = (
    "pagina inicial",
    "visao geral do espaco",
    "fluxo recomendado",
    "checklist para abrir",
    "templates",
    "guia de uso do confluence",
    "como usar este espaco",
    "indice",
    "navegacao",
    "sumario",
)


_CHUNK_MAX_CHARS = 1000
_CHUNK_TARGET_CHARS = 800
# Captura headings markdown 1-3 no início de linha
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _normalize_title(title: str) -> str:
    """Lower + sem acentos pra comparação com prefixos meta."""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    return ascii_only.lower().strip()


def _is_meta_page(title: str) -> bool:
    """True se o título sugere página de navegação/índice e não conteúdo."""
    if not title:
        return False
    normalized = _normalize_title(title)
    return any(normalized.startswith(prefix) for prefix in _META_TITLE_PREFIXES)


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug


def _has_signal(text: str) -> bool:
    """True se o texto tem pelo menos um caractere alfanumérico."""
    return any(c.isalnum() for c in text)


def _split_oversized(content: str) -> list[str]:
    """Divide texto > _CHUNK_MAX_CHARS em sub-chunks ≤ ~_CHUNK_TARGET_CHARS.

    Quebra em fim de parágrafo (\\n\\n). Mesmo formato usado no GitBook.
    """
    if len(content) <= _CHUNK_MAX_CHARS:
        return [content]
    paragraphs = content.split("\n\n")
    sub_chunks: list[str] = []
    buffer: list[str] = []
    buffer_len = 0
    for para in paragraphs:
        para_len = len(para)
        if buffer and buffer_len + 2 + para_len > _CHUNK_TARGET_CHARS:
            sub_chunks.append("\n\n".join(buffer))
            buffer = [para]
            buffer_len = para_len
        else:
            buffer.append(para)
            buffer_len += (2 if buffer_len else 0) + para_len
    if buffer:
        sub_chunks.append("\n\n".join(buffer))
    return sub_chunks


def _chunk_markdown(
    markdown: str,
    *,
    page_title: str,
    page_url: str,
) -> list[GitBookChunk]:
    """Quebra markdown em chunks por heading (h1/h2/h3).

    Texto antes do primeiro heading vira "seção intro" com title = page_title.
    Cada heading abre uma seção até o próximo heading. Seções grandes são
    sub-divididas em ~_CHUNK_TARGET_CHARS preservando section_title.
    """
    if not markdown.strip():
        return []

    matches = list(_HEADING_RE.finditer(markdown))
    sections: list[tuple[str, str, str]] = []  # (title, anchor, content)

    if not matches:
        sections.append((page_title, _slugify(page_title), markdown.strip()))
    else:
        first_pos = matches[0].start()
        if first_pos > 0:
            intro = markdown[:first_pos].strip()
            if _has_signal(intro):
                sections.append((page_title, _slugify(page_title), intro))

        for i, match in enumerate(matches):
            section_title = match.group(2).strip()
            section_anchor = _slugify(section_title) or "section"
            content_start = match.end()
            content_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            content = markdown[content_start:content_end].strip()
            if _has_signal(content):
                sections.append((section_title, section_anchor, content))

    chunks: list[GitBookChunk] = []
    for title, anchor, content in sections:
        for sub_content in _split_oversized(content):
            if not _has_signal(sub_content):
                continue
            chunks.append(
                GitBookChunk(
                    page_title=page_title,
                    page_url=page_url,
                    section_title=title,
                    section_anchor=anchor,
                    content=sub_content,
                )
            )
    return chunks


def _extract_adf_from_page(page: dict) -> Optional[dict]:
    """Pega o ADF JSON do body.atlas_doc_format.value (string JSON aninhada).

    Confluence Cloud retorna o ADF como string JSON dentro de `value`.
    Algumas APIs/mocks já entregam o objeto JSON parseado — suportamos ambos.
    """
    body = page.get("body") or {}
    adf_block = body.get("atlas_doc_format") or {}
    raw = adf_block.get("value")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None


def _build_page_url(base_url: str, space_key: str, page_id: str) -> str:
    """URL canônica pra deep-link no Confluence — usada só no cache (não no prompt)."""
    return f"{base_url.rstrip('/')}/spaces/{space_key}/pages/{page_id}"


def _write_cache(
    chunks: list[GitBookChunk],
    output_path: Path,
    base_url: str,
    space_key: str,
) -> None:
    """Grava o cache JSON. Mesmo schema do GitBook cache + `space_key`."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "confluence_kb",
        "base_url": base_url,
        "space_key": space_key,
        "chunks": [
            {
                "page_title": c.page_title,
                "page_url": c.page_url,
                "section_title": c.section_title,
                "section_anchor": c.section_anchor,
                "content": c.content,
                "char_count": c.char_count,
            }
            for c in chunks
        ],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_retriable(exc: BaseException) -> bool:
    """Retry em timeouts/network e em 408/429/5xx."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code in (408, 429) or code >= 500
    return False


@retry(
    retry=retry_if_exception(_is_retriable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    reraise=True,
)
def _fetch_page_batch(
    client: httpx.Client,
    base_url: str,
    space_key: str,
    start: int,
    limit: int,
) -> dict:
    """GET um lote de páginas via Confluence REST v1.

    Endpoint: /rest/api/content?spaceKey=...&expand=body.atlas_doc_format,version&start=N&limit=M
    """
    endpoint = f"{base_url}/rest/api/content"
    params = {
        "spaceKey": space_key,
        "expand": "body.atlas_doc_format,version",
        "start": start,
        "limit": limit,
        "status": "current",
        "type": "page",
    }
    resp = client.get(endpoint, params=params)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}", request=resp.request, response=resp
        )
    return resp.json()


def scrape_confluence_kb(
    base_url: str,
    user_email: str,
    api_token: str,
    space_key: str,
    output_path: Path,
    *,
    narrator: Optional[Narrator] = None,
    request_delay_seconds: float = 0.5,
    page_size: int = 25,
    timeout_seconds: float = 30.0,
) -> ScrapingResult:
    """Baixa páginas do space Confluence + gera cache JSON.

    Args:
        base_url: URL raiz do Confluence (ex.: https://x.atlassian.net/wiki).
        user_email: Email pra basic auth.
        api_token: Token pra basic auth.
        space_key: Space pra ler (geralmente SUP).
        output_path: Caminho do JSON de saída.
        narrator: Spinner amigável; None desabilita.
        request_delay_seconds: Pausa entre lotes.
        page_size: Páginas por request (1..100).
        timeout_seconds: Timeout por request.

    Returns:
        ScrapingResult com contagens. failed_urls lista páginas onde
        chunking falhou (sem ADF, sem texto, etc).

    Raises:
        httpx.HTTPError: erros de rede ou status code que não passam pelo retry.
    """
    output_path = Path(output_path)
    base_url = base_url.rstrip("/")

    all_chunks: list[GitBookChunk] = []
    failed: list[str] = []
    pages_processed = 0
    skipped_meta = 0

    with httpx.Client(
        auth=(user_email, api_token),
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        start = 0
        batch_idx = 0
        while True:
            batch_idx += 1
            if narrator is not None:
                with narrator.step(f"baixando lote {batch_idx} (start={start})..."):
                    data = _fetch_page_batch(client, base_url, space_key, start, page_size)
            else:
                data = _fetch_page_batch(client, base_url, space_key, start, page_size)

            results = data.get("results") or []
            if not results:
                break

            for page in results:
                pages_processed += 1
                page_id = str(page.get("id") or "")
                title = page.get("title") or "(sem título)"
                page_url = _build_page_url(base_url, space_key, page_id)

                if _is_meta_page(title):
                    skipped_meta += 1
                    continue

                adf = _extract_adf_from_page(page)
                if adf is None:
                    failed.append(page_url)
                    continue

                markdown = adf_to_markdown(adf)
                if not markdown.strip():
                    failed.append(page_url)
                    continue

                page_chunks = _chunk_markdown(
                    markdown, page_title=title, page_url=page_url
                )
                if not page_chunks:
                    failed.append(page_url)
                    continue
                all_chunks.extend(page_chunks)

            # Próximo lote? Confluence Cloud v1: presença de _links.next
            links = data.get("_links") or {}
            if not links.get("next"):
                break
            start += page_size

            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)

    _write_cache(
        all_chunks, output_path=output_path, base_url=base_url, space_key=space_key
    )

    valid_pages = pages_processed - skipped_meta - len(failed)
    log.info(
        "confluence_kb: %d páginas processadas, %d puladas (meta), %d válidas, %d falhas",
        pages_processed, skipped_meta, valid_pages, len(failed),
    )

    return ScrapingResult(
        pages_fetched=valid_pages,
        chunks_written=len(all_chunks),
        failed_urls=failed,
        output_path=output_path,
    )
