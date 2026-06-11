"""Testes do scraper de GitBook público (issue #2)."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from kiro.domain.models import GitBookChunk, ScrapingResult
from kiro.infrastructure.gitbook_loader import (
    _chunk_page,
    _extract_page_title,
    _find_content_container,
    _parse_sitemap,
    _section_anchor,
    _write_cache,
    scrape_public_gitbook,
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


def test_section_anchor_falls_back_to_section_when_text_has_no_alphanumerics():
    soup = BeautifulSoup("<h2>!!!</h2>", "html.parser")
    heading = soup.find("h2")
    assert _section_anchor(heading) == "section"


def test_page_title_ignores_h1_inside_head():
    soup = BeautifulSoup(
        '<html><head><h1>Stale</h1><title>Body title</title></head><body><main><p>x</p></main></body></html>',
        "html.parser",
    )
    assert _extract_page_title(soup) == "Body title"


def test_chunk_by_heading_three_sections():
    html = """
    <main>
      <h1>Página</h1>
      <h2>Introdução</h2>
      <p>Texto da introdução suficiente pra teste.</p>
      <h2>Configuração</h2>
      <p>Passo um.</p>
      <p>Passo dois.</p>
      <h2>Conclusão</h2>
      <p>Fim.</p>
    </main>
    """
    chunks = _chunk_page(html, page_url="https://x.com/p1")
    assert [c.section_title for c in chunks] == ["Introdução", "Configuração", "Conclusão"]
    assert all(c.page_title == "Página" for c in chunks)
    assert all(c.page_url == "https://x.com/p1" for c in chunks)
    assert "Passo um" in chunks[1].content
    assert "Passo dois" in chunks[1].content


def test_chunk_keeps_short_section():
    html = """
    <main>
      <h1>Página</h1>
      <h2>Limite iOS</h2>
      <p>Suporte apenas iOS 15+.</p>
    </main>
    """
    chunks = _chunk_page(html, page_url="https://x.com/p1")
    assert len(chunks) == 1
    assert chunks[0].section_title == "Limite iOS"
    assert "iOS 15+" in chunks[0].content
    assert chunks[0].char_count < 50


def test_chunk_returns_empty_when_no_container():
    chunks = _chunk_page("<html><body><p>nada</p></body></html>", page_url="https://x.com/p1")
    assert chunks == []


def test_chunk_uses_intro_section_when_text_before_first_heading():
    html = """
    <main>
      <h1>Página</h1>
      <p>Intro sem heading próprio.</p>
      <h2>Seção</h2>
      <p>Conteúdo.</p>
    </main>
    """
    chunks = _chunk_page(html, page_url="https://x.com/p1")
    assert len(chunks) == 2
    assert chunks[0].section_title == "Página"
    assert "Intro sem heading próprio" in chunks[0].content
    assert chunks[1].section_title == "Seção"


def test_chunk_skips_p_nested_in_li():
    html = """
    <main>
      <h1>Página</h1>
      <h2>Lista</h2>
      <ul>
        <li><p>Item um com parágrafo aninhado.</p></li>
        <li>Item dois sem parágrafo.</li>
      </ul>
    </main>
    """
    chunks = _chunk_page(html, page_url="https://x.com/p1")
    assert len(chunks) == 1
    content = chunks[0].content
    # Cada texto deve aparecer EXATAMENTE uma vez
    assert content.count("Item um com parágrafo aninhado") == 1
    assert content.count("Item dois sem parágrafo") == 1


def test_chunk_intro_when_no_h1():
    """Texto antes de qualquer heading vira seção 'intro' com page_title."""
    html = """
    <html><head><title>Da head</title></head><body><main>
      <p>Conteúdo antes de qualquer heading.</p>
      <h2>Seção</h2>
      <p>Da seção.</p>
    </main></body></html>
    """
    chunks = _chunk_page(html, page_url="https://x.com/p1")
    assert len(chunks) == 2
    # Sem h1, page_title cai pro <title> ("Da head")
    assert chunks[0].section_title == "Da head"
    assert "Conteúdo antes" in chunks[0].content
    assert chunks[1].section_title == "Seção"
    assert "Da seção" in chunks[1].content


def test_chunk_split_when_oversized():
    # 5 parágrafos de ~500 chars cada → ~2500 chars total
    big_paragraph = "Parágrafo bem longo. " * 30  # ~600 chars
    paragraphs_html = "".join(f"<p>{big_paragraph}</p>" for _ in range(5))
    html = f"<main><h1>Página</h1><h2>Grande</h2>{paragraphs_html}</main>"

    chunks = _chunk_page(html, page_url="https://x.com/big")

    # Deve gerar múltiplos sub-chunks, todos com mesmo section_title
    assert len(chunks) >= 2
    assert all(c.section_title == "Grande" for c in chunks)
    assert all(c.section_anchor == "grande" for c in chunks)
    # Cada sub-chunk deve respeitar ~1000 chars
    assert all(c.char_count <= 1100 for c in chunks)
    # Soma dos sub-chunks ≈ conteúdo total (sem perder texto)
    total_text = "\n\n".join(c.content for c in chunks)
    assert "Parágrafo bem longo." in total_text


def test_chunk_split_at_paragraph_boundary():
    # 3 parágrafos médios — deve quebrar entre parágrafos, não no meio de um
    para = "A" * 400  # 400 chars cada
    html = f"<main><h1>P</h1><h2>S</h2><p>{para}</p><p>{para}</p><p>{para}</p></main>"

    chunks = _chunk_page(html, page_url="https://x.com/m")

    # Cada chunk termina onde um parágrafo termina (sem 'A' órfão)
    for chunk in chunks:
        # Conteúdo não pode terminar com parte de um 'AAA...' truncado:
        # cada chunk deve consistir só de parágrafos completos
        parts = chunk.content.split("\n\n")
        for part in parts:
            assert part.strip() in (para, "")


def test_write_cache_creates_file_with_correct_schema(tmp_path):
    chunks = [
        GitBookChunk(
            page_title="P1",
            page_url="https://x.com/p1",
            section_title="S1",
            section_anchor="s1",
            content="Conteúdo 1",
        ),
    ]
    out = tmp_path / "cache.json"
    _write_cache(
        chunks,
        output_path=out,
        base_url="https://x.com",
    )

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "gitbook_public"
    assert data["base_url"] == "https://x.com"
    assert isinstance(data["fetched_at"], str)
    assert len(data["chunks"]) == 1
    chunk = data["chunks"][0]
    assert chunk["page_title"] == "P1"
    assert chunk["page_url"] == "https://x.com/p1"
    assert chunk["section_title"] == "S1"
    assert chunk["section_anchor"] == "s1"
    assert chunk["content"] == "Conteúdo 1"
    assert chunk["char_count"] == len("Conteúdo 1")


def test_fetched_at_is_iso8601_utc(tmp_path):
    out = tmp_path / "cache.json"
    _write_cache([], output_path=out, base_url="https://x.com")
    data = json.loads(out.read_text(encoding="utf-8"))
    # Formato ISO 8601 UTC: termina em 'Z' ou '+00:00'
    fetched = data["fetched_at"]
    assert fetched.endswith("Z") or fetched.endswith("+00:00")
    # Parseável de volta como datetime
    from datetime import datetime
    parsed = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_write_cache_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "dir" / "cache.json"
    _write_cache([], output_path=out, base_url="https://x.com")
    assert out.exists()


# ---------------------------------------------------------------------------
# T9 — orchestrator + fetch HTTP (respx mocks)
# ---------------------------------------------------------------------------

_PAGE_HTML = """<html><head><title>X</title></head><body>
<main>
  <h1>Configurando push</h1>
  <h2>Pré-requisitos</h2>
  <p>Antes de começar, certifique-se de que sua conta Firebase está ativa.</p>
  <h2>Passos</h2>
  <p>Passo 1: abra o console.</p>
  <p>Passo 2: clique em Notifications.</p>
</main>
</body></html>"""

_PAGE_404_BODY = "<html><body>404</body></html>"


@respx.mock
def test_sitemap_404_raises(tmp_path):
    base = "https://example.com/docs"
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(404))

    with pytest.raises(ValueError, match="sitemap"):
        scrape_public_gitbook(
            base_url=base,
            output_path=tmp_path / "cache.json",
            request_delay_seconds=0,
        )


@respx.mock
def test_page_404_continues(tmp_path):
    base = "https://example.com/docs"
    sitemap = f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/ok</loc></url>
  <url><loc>{base}/dead</loc></url>
</urlset>"""
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    respx.get(f"{base}/ok").mock(return_value=httpx.Response(200, text=_PAGE_HTML))
    respx.get(f"{base}/dead").mock(return_value=httpx.Response(404, text=_PAGE_404_BODY))

    result = scrape_public_gitbook(
        base_url=base,
        output_path=tmp_path / "cache.json",
        request_delay_seconds=0,
    )

    assert result.pages_fetched == 1
    assert result.chunks_written >= 1
    assert f"{base}/dead" in result.failed_urls


@respx.mock
def test_full_pipeline_with_mocks(tmp_path):
    base = "https://example.com/docs"
    sitemap = f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{base}/intro</loc></url>
  <url><loc>{base}/setup</loc></url>
</urlset>"""
    respx.get(f"{base}/sitemap.xml").mock(return_value=httpx.Response(200, text=sitemap))
    respx.get(f"{base}/intro").mock(return_value=httpx.Response(200, text=_PAGE_HTML))
    respx.get(f"{base}/setup").mock(return_value=httpx.Response(200, text=_PAGE_HTML))

    out = tmp_path / "cache.json"
    result = scrape_public_gitbook(
        base_url=base,
        output_path=out,
        request_delay_seconds=0,
    )

    assert result.pages_fetched == 2
    assert result.failed_urls == []
    assert result.output_path == out

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "gitbook_public"
    assert data["base_url"] == base
    assert len(data["chunks"]) >= 2
    assert data["chunks"][0]["page_title"] == "Configurando push"
