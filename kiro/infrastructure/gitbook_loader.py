"""Scraper do GitBook público da Kobe.

Baixa todas as páginas listadas no `/sitemap.xml` do GitBook, quebra cada
uma em chunks por heading, e salva como JSON pra ser consumido pelo
retrieval (issue #3). Esse módulo NÃO toca o pipeline de geração.
"""

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag

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
    """Título da página: primeiro <h1> do conteúdo, ou <title> como fallback."""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    title = soup.find("title")
    if title and title.get_text(strip=True):
        return title.get_text(strip=True)
    return "(sem título)"


def _section_anchor(heading: Tag) -> str:
    """Anchor pra deep-link. Usa heading.id se presente, senão slugifica texto."""
    if heading.get("id"):
        return heading["id"]
    return _slugify(heading.get_text(strip=True))


def _slugify(text: str) -> str:
    """Slug ASCII simples: minúsculas, remove acentos, troca não-alfanum por '-'."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug
