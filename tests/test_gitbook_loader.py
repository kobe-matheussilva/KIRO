"""Testes do scraper de GitBook público (issue #2)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kiro.domain.models import GitBookChunk, ScrapingResult


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
