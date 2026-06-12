"""Testes do KnowledgeRetriever — tokenização, indexação e ranking TF-IDF."""

import json
from pathlib import Path

import pytest

from kiro.application.retrieval import (
    KnowledgeRetriever,
    _tokenize,
    build_retriever,
)
from kiro.domain.models import Cluster


def _cluster(topic: str, labels: list[str] | None = None) -> Cluster:
    return Cluster(
        topic=topic,
        tickets=["OPE-1"],
        summaries=["irrelevante"],
        labels=labels or [],
        components=[],
    )


def _write_cache(path: Path, chunks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-06-11T00:00:00Z",
                "source": "gitbook_public",
                "base_url": "https://example.com",
                "chunks": chunks,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _chunk(page_title: str, section_title: str, content: str, idx: int = 0) -> dict:
    return {
        "page_title": page_title,
        "page_url": f"https://example.com/page-{idx}",
        "section_title": section_title,
        "section_anchor": f"sec-{idx}",
        "content": content,
        "char_count": len(content),
    }


# ─── tokenização ─────────────────────────────────────────────────────


def test_tokenize_lowercases_and_strips_accents():
    tokens = _tokenize("Configuração de Notificações")
    assert "configuracao" in tokens
    assert "notificacoes" in tokens


def test_tokenize_drops_short_tokens_and_stopwords():
    tokens = _tokenize("o app de push para iOS")
    # 'o', 'de', 'para' (stopwords) e 'os' (curto) saem; 'app', 'push', 'ios' ficam
    assert "o" not in tokens
    assert "de" not in tokens
    assert "para" not in tokens
    assert "app" in tokens
    assert "push" in tokens
    assert "ios" in tokens


def test_tokenize_empty_text():
    assert _tokenize("") == []
    assert _tokenize("   .  .  ") == []


# ─── cache ausente / inválido ────────────────────────────────────────


def test_missing_cache_makes_retriever_empty(tmp_path):
    r = KnowledgeRetriever(tmp_path / "nope.json")
    assert r.is_ready is False
    assert r.chunk_count == 0
    assert r.find_relevant(_cluster("qualquer coisa")) == []


def test_invalid_json_cache_makes_retriever_empty(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ not valid json", encoding="utf-8")
    r = KnowledgeRetriever(p)
    assert r.is_ready is False
    assert r.find_relevant(_cluster("login")) == []


def test_cache_without_chunks_makes_retriever_empty(tmp_path):
    p = tmp_path / "empty.json"
    _write_cache(p, [])
    r = KnowledgeRetriever(p)
    assert r.is_ready is False


def test_build_retriever_returns_none_when_cache_missing(tmp_path):
    assert build_retriever(tmp_path / "nope.json") is None


def test_build_retriever_returns_instance_when_cache_present(tmp_path):
    p = tmp_path / "ok.json"
    _write_cache(p, [_chunk("Push", "Visão geral", "Configurando push notifications no app")])
    retriever = build_retriever(p)
    assert retriever is not None
    assert retriever.is_ready


# ─── ranking ─────────────────────────────────────────────────────────


def test_ranks_chunk_with_query_terms_higher(tmp_path):
    p = tmp_path / "cache.json"
    _write_cache(
        p,
        [
            _chunk(
                "Push",
                "Configuração",
                "Como configurar push notifications para iOS e Android no painel admin",
                idx=0,
            ),
            _chunk(
                "Cashback",
                "Regras",
                "Configuração de regras de cashback por categoria e segmento",
                idx=1,
            ),
            _chunk(
                "Login",
                "Erros",
                "Mensagens de erro durante o processo de autenticação do usuário",
                idx=2,
            ),
        ],
    )
    r = KnowledgeRetriever(p)
    results = r.find_relevant(
        _cluster("push notification iOS", labels=["push"]),
        top_k=2,
        min_score=0.01,
    )
    assert len(results) >= 1
    # O chunk de Push deve vir em primeiro
    assert results[0].page_title == "Push"


def test_min_score_filters_unrelated_chunks(tmp_path):
    p = tmp_path / "cache.json"
    _write_cache(
        p,
        [
            _chunk("Push", "Setup", "configurar push notifications mobile", idx=0),
            _chunk("Cashback", "Regras", "configurar regras de cashback", idx=1),
        ],
    )
    r = KnowledgeRetriever(p)
    # Query que não casa com nada no corpus — todos abaixo do threshold
    results = r.find_relevant(
        _cluster("blockchain criptomoeda staking"),
        top_k=5,
        min_score=0.5,
    )
    assert results == []


def test_top_k_caps_results(tmp_path):
    p = tmp_path / "cache.json"
    _write_cache(
        p,
        [
            _chunk("Push", "A", "push notifications mobile ios android", idx=0),
            _chunk("Push", "B", "push notifications setup configuracao", idx=1),
            _chunk("Push", "C", "push notifications painel admin", idx=2),
            _chunk("Push", "D", "push notifications campanhas regras", idx=3),
        ],
    )
    r = KnowledgeRetriever(p)
    results = r.find_relevant(
        _cluster("push notifications"), top_k=2, min_score=0.0
    )
    assert len(results) == 2


def test_empty_query_returns_empty(tmp_path):
    p = tmp_path / "cache.json"
    _write_cache(p, [_chunk("Push", "x", "push notifications", idx=0)])
    r = KnowledgeRetriever(p)
    # topic só com stopwords/curtas e sem labels → query vazia
    results = r.find_relevant(_cluster("o e de"), top_k=3, min_score=0.0)
    assert results == []


def test_labels_and_components_expand_query(tmp_path):
    """Labels e components do cluster são incluídos na query — mesmo se topic vago."""
    p = tmp_path / "cache.json"
    _write_cache(
        p,
        [
            _chunk("Push", "x", "configurar push notifications mobile", idx=0),
            _chunk("Cashback", "y", "regras de cashback por categoria", idx=1),
        ],
    )
    r = KnowledgeRetriever(p)
    # topic vago, mas labels=['push'] deve guiar pro chunk certo
    results = r.find_relevant(
        Cluster(
            topic="problema recorrente",
            tickets=["OPE-1"],
            summaries=["s"],
            labels=["push"],
            components=["mobile"],
        ),
        top_k=1,
        min_score=0.01,
    )
    assert len(results) == 1
    assert results[0].page_title == "Push"


def test_malformed_chunk_skipped(tmp_path):
    p = tmp_path / "cache.json"
    _write_cache(
        p,
        [
            {"page_title": "Bad", "missing_other_fields": True},
            _chunk("Good", "x", "push notifications mobile", idx=1),
        ],
    )
    r = KnowledgeRetriever(p)
    # Só o chunk válido entra no índice
    assert r.chunk_count == 1
    results = r.find_relevant(_cluster("push notifications"), top_k=3, min_score=0.0)
    assert len(results) == 1
    assert results[0].page_title == "Good"
