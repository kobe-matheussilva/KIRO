"""Testes do scraper de Confluence SUP (issue #10)."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from kiro.infrastructure.confluence_kb_loader import (
    _chunk_markdown,
    _extract_adf_from_page,
    _is_meta_page,
    _write_cache,
    scrape_confluence_kb,
)


# ─── _is_meta_page ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Página inicial do Suporte",
        "Visão geral do espaço SUP",
        "Fluxo recomendado de atendimento",
        "Checklist para abrir chamado",
        "Templates",
        "Índice de artigos",
        "PÁGINA INICIAL",  # case-insensitive
        "Pagina Inicial sem acento",  # accent-insensitive
    ],
)
def test_meta_page_titles_are_filtered(title):
    assert _is_meta_page(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Configurando Push Notifications iOS",
        "Personalizando o Texto de Validação",
        "Ofertas por Loja Selecionada (VTEX Master Data)",
        "Encartes e Folhetos de Ofertas",
        "FAQ Login Social",
    ],
)
def test_content_page_titles_pass(title):
    assert _is_meta_page(title) is False


def test_meta_page_empty_title_passes():
    """Título vazio: não deve ser classificado como meta — deixa o chunking decidir."""
    assert _is_meta_page("") is False
    assert _is_meta_page(None) is False  # type: ignore[arg-type]


# ─── _extract_adf_from_page ─────────────────────────────────────────


def test_extract_adf_from_string_value():
    """Confluence Cloud retorna ADF como string JSON aninhada."""
    page = {
        "body": {
            "atlas_doc_format": {
                "value": json.dumps({"type": "doc", "content": []}),
            }
        }
    }
    adf = _extract_adf_from_page(page)
    assert adf == {"type": "doc", "content": []}


def test_extract_adf_from_dict_value():
    """Alguns mocks/SDKs já entregam dict — aceita também."""
    page = {
        "body": {
            "atlas_doc_format": {
                "value": {"type": "doc", "content": []},
            }
        }
    }
    assert _extract_adf_from_page(page) == {"type": "doc", "content": []}


def test_extract_adf_missing_returns_none():
    assert _extract_adf_from_page({}) is None
    assert _extract_adf_from_page({"body": {}}) is None
    assert _extract_adf_from_page({"body": {"atlas_doc_format": {}}}) is None


def test_extract_adf_invalid_json_returns_none():
    page = {"body": {"atlas_doc_format": {"value": "{ broken json"}}}
    assert _extract_adf_from_page(page) is None


# ─── _chunk_markdown ────────────────────────────────────────────────


def test_chunk_markdown_no_headings_single_section():
    md = "Texto único sem headings nenhum.\n\nOutro parágrafo."
    chunks = _chunk_markdown(md, page_title="Página X", page_url="https://x")
    assert len(chunks) == 1
    assert chunks[0].section_title == "Página X"
    assert "Texto único" in chunks[0].content


def test_chunk_markdown_with_headings_creates_sections():
    md = """# Título Principal

Intro antes de subseção.

## Visão Geral

Conteúdo da visão geral.

## Perguntas Frequentes

Perguntas aqui."""
    chunks = _chunk_markdown(md, page_title="Página X", page_url="https://x")
    titles = [c.section_title for c in chunks]
    assert "Título Principal" in titles
    assert "Visão Geral" in titles
    assert "Perguntas Frequentes" in titles


def test_chunk_markdown_preserves_page_url_and_title():
    md = "## Seção\n\nConteúdo."
    chunks = _chunk_markdown(md, page_title="Artigo Y", page_url="https://confluence/y")
    assert all(c.page_title == "Artigo Y" for c in chunks)
    assert all(c.page_url == "https://confluence/y" for c in chunks)


def test_chunk_markdown_drops_empty_sections():
    md = "## Vazia\n\n## Com Conteúdo\n\nTexto."
    chunks = _chunk_markdown(md, page_title="x", page_url="https://x")
    section_titles = [c.section_title for c in chunks]
    assert "Vazia" not in section_titles
    assert "Com Conteúdo" in section_titles


def test_chunk_markdown_oversized_section_is_split():
    # _split_oversized quebra por \n\n — texto contínuo não é dividido.
    # Caso real: artigos têm vários parágrafos. Aqui ~100 parágrafos > 1000 chars.
    paragraph = "Frase de teste com tamanho razoável pra forçar split em múltiplos chunks."
    big = "\n\n".join([paragraph] * 30)  # ~2.2k chars total
    md = f"## Grande\n\n{big}"
    chunks = _chunk_markdown(md, page_title="x", page_url="https://x")
    assert len([c for c in chunks if c.section_title == "Grande"]) >= 2


def test_chunk_markdown_empty_returns_empty():
    assert _chunk_markdown("", page_title="x", page_url="x") == []
    assert _chunk_markdown("   \n\n", page_title="x", page_url="x") == []


def test_chunk_markdown_text_before_first_heading_uses_page_title():
    md = "Texto intro antes de qualquer heading.\n\n## Primeira Seção\n\nConteúdo."
    chunks = _chunk_markdown(md, page_title="Artigo Z", page_url="https://x")
    # Primeira chunk usa o título da página (representa a "intro")
    intro = chunks[0]
    assert intro.section_title == "Artigo Z"
    assert "Texto intro" in intro.content


# ─── _write_cache ───────────────────────────────────────────────────


def test_write_cache_creates_file_with_correct_schema(tmp_path):
    from kiro.domain.models import GitBookChunk

    out = tmp_path / "sup_cache.json"
    chunks = [
        GitBookChunk(
            page_title="Page",
            page_url="https://x/p",
            section_title="Sec",
            section_anchor="sec",
            content="conteudo",
        )
    ]
    _write_cache(chunks, out, base_url="https://x", space_key="SUP")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["source"] == "confluence_kb"
    assert data["space_key"] == "SUP"
    assert data["base_url"] == "https://x"
    assert "fetched_at" in data
    assert len(data["chunks"]) == 1
    assert data["chunks"][0]["page_title"] == "Page"
    assert data["chunks"][0]["char_count"] == len("conteudo")


# ─── scrape_confluence_kb (integration with respx) ──────────────────


def _make_page(page_id: str, title: str, markdown_like_text: str) -> dict:
    """Helper: monta página com ADF mínimo (paragraph + text)."""
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": title}],
            },
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Visão Geral"}],
            },
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": markdown_like_text}],
            },
        ],
    }
    return {
        "id": page_id,
        "title": title,
        "body": {"atlas_doc_format": {"value": json.dumps(adf)}},
    }


@respx.mock
def test_scrape_single_batch_no_pagination(tmp_path):
    base = "https://x.atlassian.net/wiki"
    respx.get(f"{base}/rest/api/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    _make_page("1", "Configurando Push", "Conteúdo sobre push notifications."),
                    _make_page("2", "Cashback VTEX", "Como configurar cashback no VTEX."),
                ],
                "_links": {},  # sem 'next' → fim da paginação
            },
        )
    )

    out = tmp_path / "cache.json"
    result = scrape_confluence_kb(
        base_url=base,
        user_email="u@x.com",
        api_token="tok",
        space_key="SUP",
        output_path=out,
        narrator=None,
        request_delay_seconds=0,
    )

    assert result.pages_fetched == 2
    assert result.chunks_written > 0
    data = json.loads(out.read_text())
    assert data["space_key"] == "SUP"
    assert len(data["chunks"]) == result.chunks_written


@respx.mock
def test_scrape_filters_meta_pages(tmp_path):
    base = "https://x.atlassian.net/wiki"
    respx.get(f"{base}/rest/api/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    _make_page("1", "Página inicial do Suporte", "Bem-vindo."),
                    _make_page("2", "Configurando Push", "Push notifications."),
                ],
                "_links": {},
            },
        )
    )
    out = tmp_path / "cache.json"
    result = scrape_confluence_kb(
        base_url=base,
        user_email="u@x.com",
        api_token="tok",
        space_key="SUP",
        output_path=out,
        request_delay_seconds=0,
    )
    # Só Configurando Push deve sobrar
    assert result.pages_fetched == 1
    page_titles = {c["page_title"] for c in json.loads(out.read_text())["chunks"]}
    assert "Página inicial do Suporte" not in page_titles
    assert "Configurando Push" in page_titles


@respx.mock
def test_scrape_paginates(tmp_path):
    base = "https://x.atlassian.net/wiki"
    # Primeiro lote tem next; segundo é vazio
    route = respx.get(f"{base}/rest/api/content").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [_make_page("1", "Página A", "A.")],
                    "_links": {"next": "/rest/api/content?start=25"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [_make_page("2", "Página B", "B.")],
                    "_links": {},
                },
            ),
        ]
    )
    out = tmp_path / "cache.json"
    result = scrape_confluence_kb(
        base_url=base,
        user_email="u@x.com",
        api_token="tok",
        space_key="SUP",
        output_path=out,
        request_delay_seconds=0,
        page_size=1,
    )
    assert route.call_count == 2
    assert result.pages_fetched == 2


@respx.mock
def test_scrape_skips_page_without_adf(tmp_path):
    base = "https://x.atlassian.net/wiki"
    respx.get(f"{base}/rest/api/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"id": "1", "title": "Sem Body", "body": {}},
                    _make_page("2", "Com Conteúdo", "Texto válido."),
                ],
                "_links": {},
            },
        )
    )
    out = tmp_path / "cache.json"
    result = scrape_confluence_kb(
        base_url=base,
        user_email="u@x.com",
        api_token="tok",
        space_key="SUP",
        output_path=out,
        request_delay_seconds=0,
    )
    assert result.pages_fetched == 1
    assert len(result.failed_urls) == 1
    assert "/pages/1" in result.failed_urls[0]


@respx.mock
def test_scrape_raises_on_auth_failure(tmp_path):
    base = "https://x.atlassian.net/wiki"
    respx.get(f"{base}/rest/api/content").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    out = tmp_path / "cache.json"
    with pytest.raises(httpx.HTTPStatusError):
        scrape_confluence_kb(
            base_url=base,
            user_email="u@x.com",
            api_token="bad",
            space_key="SUP",
            output_path=out,
            request_delay_seconds=0,
        )
