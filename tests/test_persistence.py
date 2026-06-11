import json
from datetime import datetime, timezone
from pathlib import Path

from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    FAQItem,
    PublishResult,
    Ticket,
)
from kiro.infrastructure.persistence import ArtifactStore


def test_save_tickets(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    path = store.save_tickets([Ticket(key="A-1", summary="hello")])
    data = json.loads(path.read_text())
    assert data[0]["key"] == "A-1"


def test_save_clusters(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    path = store.save_clusters(
        [Cluster(topic="t", tickets=["A-1"], summaries=["s"])]
    )
    data = json.loads(path.read_text())
    assert data[0]["topic"] == "t"


def test_save_article_markdown(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    cluster = Cluster(topic="login", tickets=["A-1"], summaries=["s"])
    article = ArticleDraft(
        title="Como resolver login",
        problem="P",
        cause="C",
        solution="1. fazer X\n2. fazer Y",
        faq=[FAQItem(question="q", answer="a")],
        tags=["login"],
    )
    path = store.save_article_markdown(cluster, article)
    assert path.exists()
    content = path.read_text()
    assert "Como resolver login" in content
    assert "fazer X" in content
    # Novo header externo (substituiu "Problema") — V1.0.1
    assert "Sobre este artigo" in content


def test_save_report(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    results = [
        PublishResult(
            cluster_topic="t",
            article_title="T",
            ticket_count=5,
            local_path="/x",
        ),
        PublishResult(
            cluster_topic="u",
            article_title="U",
            ticket_count=2,
            local_path="/y",
            error="boom",
        ),
    ]
    now = datetime.now(timezone.utc)
    path = store.save_report(results, now, now)
    content = path.read_text()
    assert "Relatório" in content
    assert "T" in content
    assert "boom" in content


def test_save_errors(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    path = store.save_errors([{"stage": "generate", "error": "oops"}])
    data = json.loads(path.read_text())
    assert data[0]["error"] == "oops"
