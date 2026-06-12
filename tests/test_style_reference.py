"""Testes do StyleReferenceFinder — find_similar + find_dedupe_match (issue #10)."""

import json
from pathlib import Path

from kiro.application.style_reference import (
    StyleReferenceFinder,
    build_style_finder,
)
from kiro.domain.models import Cluster


def _cluster(topic: str, labels: list[str] | None = None) -> Cluster:
    return Cluster(
        topic=topic,
        tickets=["OPE-1"],
        summaries=["s"],
        labels=labels or [],
        components=[],
    )


def _write_cache(path: Path, chunks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-06-12T00:00:00Z",
                "source": "confluence_kb",
                "base_url": "https://confluence",
                "space_key": "SUP",
                "chunks": chunks,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _chunk(page: str, section: str, content: str, idx: int = 0) -> dict:
    return {
        "page_title": page,
        "page_url": f"https://confluence/{page.lower()}",
        "section_title": section,
        "section_anchor": section.lower(),
        "content": content,
        "char_count": len(content),
    }


# ─── cache ausente ──────────────────────────────────────────────────


def test_missing_cache_finder_not_ready(tmp_path):
    finder = StyleReferenceFinder(tmp_path / "nope.json")
    assert finder.is_ready is False
    assert finder.chunk_count == 0
    assert finder.find_similar(_cluster("push")) == []
    assert finder.find_dedupe_match(_cluster("push")) is None


def test_build_style_finder_returns_none_when_cache_missing(tmp_path):
    assert build_style_finder(tmp_path / "nope.json") is None


def test_build_style_finder_returns_instance_when_cache_present(tmp_path):
    p = tmp_path / "sup.json"
    _write_cache(p, [_chunk("Push", "Setup", "configurar push notifications mobile")])
    finder = build_style_finder(p)
    assert finder is not None
    assert finder.is_ready


# ─── find_similar (few-shot) ────────────────────────────────────────


def test_find_similar_ranks_by_relevance(tmp_path):
    p = tmp_path / "sup.json"
    _write_cache(
        p,
        [
            _chunk(
                "Push iOS",
                "Configuração",
                "Como configurar push notifications para iOS no painel admin",
                idx=0,
            ),
            _chunk(
                "Cashback",
                "Regras",
                "Configurando regras de cashback no Master Data VTEX",
                idx=1,
            ),
        ],
    )
    finder = StyleReferenceFinder(p)
    results = finder.find_similar(
        _cluster("push notifications iOS"), top_k=2, min_score=0.0
    )
    assert len(results) >= 1
    assert results[0].page_title == "Push iOS"


def test_find_similar_respects_top_k(tmp_path):
    p = tmp_path / "sup.json"
    _write_cache(
        p,
        [
            _chunk("A", "x", "push notifications setup configurar mobile ios", idx=0),
            _chunk("B", "x", "push notifications setup configurar mobile android", idx=1),
            _chunk("C", "x", "push notifications setup configurar painel admin", idx=2),
        ],
    )
    finder = StyleReferenceFinder(p)
    results = finder.find_similar(_cluster("push notifications"), top_k=2, min_score=0.0)
    assert len(results) == 2


# ─── find_dedupe_match ──────────────────────────────────────────────


def test_dedupe_returns_match_above_threshold(tmp_path):
    p = tmp_path / "sup.json"
    # Conteúdo altamente similar à query — deve passar threshold alto
    _write_cache(
        p,
        [
            _chunk(
                "Personalização de Validação de Cartão",
                "Visão Geral",
                "personalizacao texto validacao cartao credito mensagem informativa",
                idx=0,
            ),
        ],
    )
    finder = StyleReferenceFinder(p)
    # Query muito alinhada ao único chunk → cosine alto
    match = finder.find_dedupe_match(
        _cluster("personalizacao texto validacao cartao credito"), threshold=0.5
    )
    assert match is not None
    assert match.page_title == "Personalização de Validação de Cartão"


def test_dedupe_returns_none_below_threshold(tmp_path):
    p = tmp_path / "sup.json"
    _write_cache(
        p,
        [
            _chunk("Push", "x", "push notifications configurar mobile", idx=0),
        ],
    )
    finder = StyleReferenceFinder(p)
    # Tópico não relacionado → cosine baixo → None
    match = finder.find_dedupe_match(
        _cluster("blockchain criptomoeda staking"), threshold=0.6
    )
    assert match is None


def test_dedupe_returns_only_one_chunk(tmp_path):
    """Dedupe é sinal binário — top 1 basta."""
    p = tmp_path / "sup.json"
    _write_cache(
        p,
        [
            _chunk("A", "x", "push notifications mobile ios android setup", idx=0),
            _chunk("B", "x", "push notifications mobile ios android painel", idx=1),
            _chunk("C", "x", "push notifications mobile ios android admin", idx=2),
        ],
    )
    finder = StyleReferenceFinder(p)
    match = finder.find_dedupe_match(_cluster("push notifications mobile"), threshold=0.0)
    assert match is not None
    # Garante que retorna chunk único, não lista
    assert isinstance(match.page_title, str)
