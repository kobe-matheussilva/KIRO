"""Testes do scraper de GitBook público (issue #2)."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from pydantic import ValidationError

from kiro.domain.models import GitBookChunk, ScrapingResult
from kiro.infrastructure.gitbook_loader import (
    _extract_page_title,
    _find_content_container,
    _parse_sitemap,
    _section_anchor,
)


SITEMAP_OK = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao/intro</loc></url>
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao/setup</loc></url>
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao/intro</loc></url>
</urlset>
"""

SITEMAP_WITH_EXTERNAL = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao/intro</loc></url>
  <url><loc>https://outro-site.com/some-page</loc></url>
</urlset>
"""


def test_gitbook_chunk_is_frozen():
    chunk = GitBookChunk(
        page_title="Config push",
        page_url="https://example.com/p1",
        section_title="Pré-requisitos",
        section_anchor="pre-requisitos",
        content="Antes de começar...",
    )
    assert chunk.char_count == len("Antes de começar...")
    with pytest.raises(ValidationError):
        chunk.content = "outro"  # frozen model


def test_scraping_result_holds_summary():
    result = ScrapingResult(
        pages_fetched=10,
        chunks_written=42,
        failed_urls=["https://example.com/dead"],
        output_path=Path("cache.json"),
    )
    assert result.pages_fetched == 10
    assert result.chunks_written == 42
    assert result.failed_urls == ["https://example.com/dead"]
    assert result.output_path == Path("cache.json")


def test_sitemap_parsing_dedupes():
    urls = _parse_sitemap(SITEMAP_OK, base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")
    assert len(urls) == 2
    assert "https://kobeapps.gitbook.io/kobe.io-documentacao/intro" in urls
    assert "https://kobeapps.gitbook.io/kobe.io-documentacao/setup" in urls


def test_sitemap_filters_external_urls():
    urls = _parse_sitemap(SITEMAP_WITH_EXTERNAL, base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")
    assert urls == ["https://kobeapps.gitbook.io/kobe.io-documentacao/intro"]


def test_sitemap_invalid_xml_raises():
    with pytest.raises(ValueError, match="sitemap"):
        _parse_sitemap("not xml at all", base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")


def test_sitemap_parsing_preserves_insertion_order():
    urls = _parse_sitemap(SITEMAP_OK, base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")
    assert urls == [
        "https://kobeapps.gitbook.io/kobe.io-documentacao/intro",
        "https://kobeapps.gitbook.io/kobe.io-documentacao/setup",
    ]


def test_sitemap_filters_sibling_prefix():
    xml = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao/intro</loc></url>
  <url><loc>https://kobeapps.gitbook.io/kobe.io-documentacao-v2/page</loc></url>
</urlset>"""
    urls = _parse_sitemap(xml, base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")
    assert urls == ["https://kobeapps.gitbook.io/kobe.io-documentacao/intro"]


def test_sitemap_with_only_external_urls_raises():
    xml = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://other-site.com/page1</loc></url>
  <url><loc>https://another.com/page2</loc></url>
</urlset>"""
    with pytest.raises(ValueError, match="nenhuma URL"):
        _parse_sitemap(xml, base_url="https://kobeapps.gitbook.io/kobe.io-documentacao")


def test_find_container_prefers_main():
    soup = BeautifulSoup(
        '<html><body><main><h1>X</h1></main></body></html>',
        "html.parser",
    )
    container = _find_content_container(soup)
    assert container.name == "main"


def test_find_container_falls_back_to_testid():
    soup = BeautifulSoup(
        '<html><body><div data-testid="page.contentEditor"><h1>X</h1></div></body></html>',
        "html.parser",
    )
    container = _find_content_container(soup)
    assert container.get("data-testid") == "page.contentEditor"


def test_find_container_falls_back_to_article():
    soup = BeautifulSoup(
        '<html><body><article><h1>X</h1></article></body></html>',
        "html.parser",
    )
    container = _find_content_container(soup)
    assert container.name == "article"


def test_find_container_returns_none_when_nothing_matches():
    soup = BeautifulSoup("<html><body><p>x</p></body></html>", "html.parser")
    assert _find_content_container(soup) is None


def test_page_title_from_h1():
    soup = BeautifulSoup(
        '<html><head><title>Site</title></head><body><main><h1>Tópico</h1></main></body></html>',
        "html.parser",
    )
    assert _extract_page_title(soup) == "Tópico"


def test_page_title_falls_back_to_title_tag():
    soup = BeautifulSoup(
        '<html><head><title>Fallback title</title></head><body><main><p>sem h1</p></main></body></html>',
        "html.parser",
    )
    assert _extract_page_title(soup) == "Fallback title"


def test_section_anchor_from_heading_id():
    soup = BeautifulSoup('<h2 id="pre-requisitos">Pré-requisitos</h2>', "html.parser")
    heading = soup.find("h2")
    assert _section_anchor(heading) == "pre-requisitos"


def test_section_anchor_slugifies_text():
    soup = BeautifulSoup("<h2>Configurando Notificações Push!</h2>", "html.parser")
    heading = soup.find("h2")
    assert _section_anchor(heading) == "configurando-notificacoes-push"
