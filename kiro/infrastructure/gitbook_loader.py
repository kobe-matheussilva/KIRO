"""Scraper do GitBook público da Kobe.

Baixa todas as páginas listadas no `/sitemap.xml` do GitBook, quebra cada
uma em chunks por heading, e salva como JSON pra ser consumido pelo
retrieval (issue #3). Esse módulo NÃO toca o pipeline de geração.
"""

import json
import logging
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from kiro.domain.models import GitBookChunk, ScrapingResult
from kiro.utils.progress import Narrator

log = logging.getLogger(__name__)

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _parse_sitemap(content: str, base_url: str) -> list[str]:
    """Parse sitemap XML, retorna URLs únicas que começam com base_url.

    Levanta ValueError se o XML for inválido ou se nenhuma URL casar
    com base_url (provavelmente erro de configuração).
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"sitemap XML inválido: {e}") from e

    base_normalized = base_url.rstrip("/")
    seen: set[str] = set()
    urls: list[str] = []
    for loc in root.iterfind(".//sm:url/sm:loc", _SITEMAP_NS):
        url = (loc.text or "").strip()
        if not url:
            continue
        if url != base_normalized and not url.startswith(base_normalized + "/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        raise ValueError(
            f"sitemap não retornou nenhuma URL começando com {base_url!r}"
        )
    return urls


def _parse_sitemap_index(content: str, base_url: str) -> list[str]:
    """Parse `<sitemapindex>` XML, retorna URLs únicas dos child sitemaps.

    Sitemap index é o padrão usado pelo GitBook em produção:
    /sitemap.xml é só um índice apontando pra /sitemap-pages.xml.

    Levanta ValueError se XML for inválido ou nenhum child sitemap
    casar com base_url.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"sitemap index XML inválido: {e}") from e

    base_normalized = base_url.rstrip("/")
    seen: set[str] = set()
    urls: list[str] = []
    for loc in root.iterfind(".//sm:sitemap/sm:loc", _SITEMAP_NS):
        url = (loc.text or "").strip()
        if not url:
            continue
        if url != base_normalized and not url.startswith(base_normalized + "/"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        raise ValueError(
            f"sitemap index não retornou nenhum child sitemap começando com {base_url!r}"
        )
    return urls


def _detect_sitemap_kind(content: str) -> str:
    """Identifica o tipo de XML de sitemap: 'urlset' ou 'sitemapindex'.

    Retorna 'unknown' se a raiz não bater com nenhum dos dois (chamador
    decide tratamento — provavelmente ValueError).
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return "unknown"
    tag = root.tag
    # Remove namespace prefix se presente: '{http://...}urlset' → 'urlset'
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    if tag in ("urlset", "sitemapindex"):
        return tag
    return "unknown"


def _find_content_container(soup: BeautifulSoup) -> Optional[Tag]:
    """Acha o container principal de conteúdo da página GitBook.

    Tenta na ordem: <main>, [data-testid='page.contentEditor'], <article>.
    Retorna None se nada bater (chamador trata como falha de parse).
    """
    main = soup.find("main")
    if main:
        return main
    testid = soup.find(attrs={"data-testid": "page.contentEditor"})
    if testid:
        return testid
    article = soup.find("article")
    if article:
        return article
    return None


def _extract_page_title(soup: BeautifulSoup) -> str:
    """Título da página: primeiro <h1> do <body>, ou <title> como fallback.

    Escopo restrito ao <body> evita pegar <h1> stale/SEO injetado no <head>.
    """
    body = soup.body or soup
    h1 = body.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    title = soup.find("title")
    if title and title.get_text(strip=True):
        return title.get_text(strip=True)
    return "(sem título)"


def _section_anchor(heading: Tag) -> str:
    """Anchor pra deep-link. Usa heading.id se presente, senão slugifica texto.

    Garante string não-vazia mesmo pra headings sem alfa-numéricos —
    cai pra 'section' como sentinel pra evitar fragment '#' quebrado.
    """
    if heading.get("id"):
        return heading["id"]
    return _slugify(heading.get_text(strip=True)) or "section"


def _slugify(text: str) -> str:
    """Slug ASCII simples: minúsculas, remove acentos, troca não-alfanum por '-'."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug


_HEADING_TAGS = {"h1", "h2", "h3"}

_CHUNK_MAX_CHARS = 1000
_CHUNK_TARGET_CHARS = 800


def _has_signal(text: str) -> bool:
    """True se o texto tem pelo menos um caractere alfanumérico.

    Usado pra filtrar chunks-lixo tipo '.', '—', '1', vistos no
    HTML real da GitBook (parágrafos com só pontuação ou ornament).
    """
    return any(c.isalnum() for c in text)


def _split_oversized(content: str) -> list[str]:
    """Divide texto > _CHUNK_MAX_CHARS em sub-chunks ≤ ~_CHUNK_TARGET_CHARS.

    Quebra preferencialmente em fim de parágrafo (\\n\\n). Se um parágrafo
    sozinho exceder o target, ele é mantido como sub-chunk único (fica
    acima de target mas abaixo de 2× target na prática). Sem quebra no
    meio de palavra.
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


def _chunk_page(html: str, page_url: str) -> list[GitBookChunk]:
    """Quebra a página em chunks por heading (h1/h2/h3).

    Texto antes do primeiro heading é atribuído a uma "seção intro"
    com title = page_title. Seções pequenas (<200 chars) também são
    preservadas — info curta é útil pro retrieval.

    Seções grandes (>_CHUNK_MAX_CHARS) são sub-divididas por
    _split_oversized em sub-chunks de ~_CHUNK_TARGET_CHARS chars,
    preservando section_title e section_anchor em cada sub-chunk.

    Retorna lista vazia se container principal não for encontrado.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = _find_content_container(soup)
    if container is None:
        return []

    page_title = _extract_page_title(soup)

    # Coleta (section_title, section_anchor, [paragraphs]) em ordem
    sections: list[tuple[str, str, list[str]]] = []
    current_title = page_title
    current_anchor = _slugify(page_title)
    current_paragraphs: list[str] = []

    for element in container.descendants:
        if not isinstance(element, Tag):
            continue

        if element.name in _HEADING_TAGS:
            # Skip nested headings (e.g., <h3> inside <h2>) — outer já abriu seção
            other_headings = _HEADING_TAGS - {element.name}
            if element.find_parent(other_headings):
                continue
            # Fecha a seção atual antes de abrir a nova
            if current_paragraphs:
                sections.append((current_title, current_anchor, current_paragraphs))
            current_title = element.get_text(strip=True) or "(sem título)"
            current_anchor = _section_anchor(element)
            current_paragraphs = []
            continue

        if element.name in {"p", "li"}:
            # Skip <p>/<li> dentro de heading, <li> ou <p> — texto já capturado
            # pelo ancestor (evita duplicar conteúdo em listas aninhadas e
            # parágrafos dentro de itens de lista)
            if element.find_parent(_HEADING_TAGS | {"p", "li"}):
                continue
            text = element.get_text(separator=" ", strip=True)
            if text:
                current_paragraphs.append(text)

    # Fecha última seção
    if current_paragraphs:
        sections.append((current_title, current_anchor, current_paragraphs))

    # Materializa chunks, sub-dividindo seções > _CHUNK_MAX_CHARS
    chunks: list[GitBookChunk] = []
    for title, anchor, paragraphs in sections:
        content = "\n\n".join(paragraphs).strip()
        if not _has_signal(content):
            continue
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


def _write_cache(
    chunks: list[GitBookChunk],
    output_path: Path,
    base_url: str,
) -> None:
    """Grava o cache JSON. `fetched_at` no topo (não por chunk)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "gitbook_public",
        "base_url": base_url,
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
    """Retry em timeouts/network errors e em 408/429/5xx."""
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
def _fetch_url(client: httpx.Client, url: str) -> str:
    """GET com retry em 408/429/5xx e erros de transporte. Retorna text.

    4xx fora de 408/429 falha imediatamente (sem retry — sabemos que
    não vai melhorar). HTTPStatusError é o que sobe pro chamador
    nesses casos.
    """
    resp = client.get(url)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}", request=resp.request, response=resp
        )
    return resp.text


def _resolve_sitemap_urls(
    client: httpx.Client,
    base_url: str,
    narrator: Optional[Narrator],
) -> list[str]:
    """Resolve /sitemap.xml em lista de URLs de páginas.

    Suporta dois formatos:
    - `<urlset>` direto: retorna URLs filtradas
    - `<sitemapindex>`: fetcha cada child sitemap (esperado <urlset>) e
      agrega URLs. Sitemapindex aninhado (índice de índices) é ignorado
      com warning.

    Raises:
        ValueError: /sitemap.xml inacessível, inválido, ou resultado vazio.
    """
    sitemap_url = f"{base_url}/sitemap.xml"
    try:
        root_content = _fetch_url(client, sitemap_url)
    except httpx.HTTPError as e:
        raise ValueError(
            f"sitemap inacessível em {sitemap_url}: {e}. "
            "Verifique GITBOOK_PUBLIC_URL."
        ) from e

    kind = _detect_sitemap_kind(root_content)
    if kind == "urlset":
        return _parse_sitemap(root_content, base_url=base_url)

    if kind == "sitemapindex":
        child_sitemaps = _parse_sitemap_index(root_content, base_url=base_url)
        log.info("gitbook: sitemap index com %d child sitemap(s)", len(child_sitemaps))
        all_urls: list[str] = []
        for child_url in child_sitemaps:
            try:
                child_content = _fetch_url(client, child_url)
            except httpx.HTTPError as e:
                log.warning("gitbook: child sitemap inacessível %s: %s", child_url, e)
                continue
            child_kind = _detect_sitemap_kind(child_content)
            if child_kind != "urlset":
                log.warning(
                    "gitbook: child sitemap %s não é <urlset> (%s) — pulando",
                    child_url, child_kind,
                )
                continue
            try:
                all_urls.extend(_parse_sitemap(child_content, base_url=base_url))
            except ValueError as e:
                log.warning("gitbook: erro parseando %s: %s", child_url, e)

        if not all_urls:
            raise ValueError(
                f"nenhum child sitemap em {sitemap_url} produziu URLs válidas"
            )
        # Dedup preservando ordem
        seen: set[str] = set()
        deduped = []
        for url in all_urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped

    raise ValueError(
        f"sitemap em {sitemap_url} tem raiz desconhecida (esperado <urlset> ou <sitemapindex>)"
    )


def scrape_public_gitbook(
    base_url: str,
    output_path: Path,
    *,
    narrator: Optional[Narrator] = None,
    request_delay_seconds: float = 0.5,
    timeout_seconds: float = 30.0,
) -> ScrapingResult:
    """Baixa a GitBook pública e grava cache JSON.

    Args:
        base_url: URL raiz da GitBook (ex.: https://kobeapps.gitbook.io/docs).
        output_path: Caminho do JSON de saída.
        narrator: Narrator pra spinner; None desabilita.
        request_delay_seconds: Pausa entre requisições (educação com servidor).
        timeout_seconds: Timeout por requisição.

    Returns:
        ScrapingResult com contagens e lista de URLs que falharam.

    Raises:
        ValueError: sitemap.xml inacessível, inválido ou sem URLs.
    """
    output_path = Path(output_path)
    base_url = base_url.rstrip("/")

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        # 1. Sitemap — suporta tanto <urlset> direto quanto <sitemapindex>
        urls = _resolve_sitemap_urls(client, base_url, narrator)
        log.info("gitbook: %d páginas no sitemap", len(urls))
        if narrator is not None:
            narrator.done(f"{len(urls)} páginas encontradas no sitemap")

        # 2. Fetch cada página → chunks
        all_chunks: list[GitBookChunk] = []
        failed: list[str] = []

        for index, url in enumerate(urls, start=1):
            try:
                if narrator is not None:
                    with narrator.step(f"baixando {index}/{len(urls)}: {url}"):
                        html = _fetch_url(client, url)
                else:
                    html = _fetch_url(client, url)
                page_chunks = _chunk_page(html, page_url=url)
                if not page_chunks:
                    log.warning("gitbook: 0 chunks gerados pra %s", url)
                    failed.append(url)
                else:
                    all_chunks.extend(page_chunks)
            except httpx.HTTPError as e:
                log.warning("gitbook: falha em %s: %s", url, e)
                failed.append(url)
            except Exception as e:
                log.warning("gitbook: erro parseando %s: %s", url, e)
                failed.append(url)

            if request_delay_seconds > 0 and index < len(urls):
                time.sleep(request_delay_seconds)

    # 3. Persistir
    _write_cache(all_chunks, output_path=output_path, base_url=base_url)

    return ScrapingResult(
        pages_fetched=len(urls) - len(failed),
        chunks_written=len(all_chunks),
        failed_urls=failed,
        output_path=output_path,
    )
