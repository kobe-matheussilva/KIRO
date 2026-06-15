"""Testes de persistence — atualizados para novo schema (issue #15)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    PublishResult,
    Section,
    Ticket,
)
from kiro.infrastructure.persistence import ArtifactStore


def _sample_article() -> ArticleDraft:
    return ArticleDraft(
        title="Solução de Problemas com Push Notifications no App",
        scope_note="Perguntas frequentes sobre push notifications no aplicativo.",
        sections=[
            Section(
                heading="Como ativar push no painel",
                body="Acesse Configurações > Notificações.\n- Habilite o canal\n- Teste o envio",
            ),
            Section(
                heading="Push não chega no iOS",
                body="Verifique se o certificado APNs está válido.",
            ),
        ],
        tags=["push", "ios"],
    )


def _sample_faq() -> CustomerFAQ:
    return CustomerFAQ(
        title="Dúvidas sobre Cashback por Loja",
        scope_note="Perguntas frequentes sobre cashback por loja física.",
        entries=[
            FAQEntry(question="Como ativar cashback?", answer="No painel admin."),
            FAQEntry(question="Funciona em todas as lojas?", answer="Sim, com regras."),
            FAQEntry(question="Como excluir?", answer="Pelo Master Data."),
        ],
        tags=["cashback"],
    )


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


def test_save_article_markdown_uses_new_template(tmp_path: Path):
    """Template do Artigo segue padrão SUP — sem 'Sobre este artigo' etc."""
    store = ArtifactStore(tmp_path)
    cluster = Cluster(topic="push", tickets=["OPE-1"], summaries=["s"])
    article = _sample_article()
    path = store.save_article_markdown(cluster, article)

    content = path.read_text(encoding="utf-8")
    # Tem o título + scope_note em panel info + sections como H2
    assert article.title in content
    assert article.scope_note in content
    assert "> [info]" in content
    assert f"## {article.sections[0].heading}" in content
    assert f"## {article.sections[1].heading}" in content
    # NÃO tem mais estrutura de relatório
    assert "Sobre este artigo" not in content
    assert "Quando isso acontece" not in content
    assert "Como resolver" not in content
    # NÃO tem mais nota interna com tickets
    assert "OPE-" not in content
    assert "Nota interna" not in content


def test_save_article_filename_has_no_ope_prefix(tmp_path: Path):
    """Filename é só slug do título — sem prefixo `OPE-XXX_` (issue #15)."""
    store = ArtifactStore(tmp_path)
    cluster = Cluster(topic="push", tickets=["OPE-10482"], summaries=["s"])
    path = store.save_article_markdown(cluster, _sample_article())
    assert "OPE-" not in path.name
    assert "OPE-10482" not in path.name
    # Slug derivado do título
    assert "push" in path.name.lower() or "solucao" in path.name.lower()


def test_save_faq_markdown_uses_scope_note(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    cluster = Cluster(topic="cashback", tickets=["OPE-1"], summaries=["s"])
    faq = _sample_faq()
    path = store.save_customer_faq_markdown(cluster, faq)
    content = path.read_text(encoding="utf-8")
    assert faq.title in content
    assert faq.scope_note in content
    assert "> [info]" in content
    for entry in faq.entries:
        assert f"## {entry.question}" in content
    assert "OPE-" not in content


def test_save_audit_writes_internal_metadata_separately(tmp_path: Path):
    """OPE-XXX tickets vão pra output/audit/, NÃO pro draft."""
    store = ArtifactStore(tmp_path)
    cluster = Cluster(
        topic="push",
        tickets=["OPE-100", "OPE-101", "OPE-102"],
        summaries=["s"],
        labels=["mobile"],
    )
    path = store.save_audit(
        cluster, title="Solução de Problemas com Push", kind="article", violations=[]
    )
    assert path.parent.name == "audit"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "article"
    assert data["tickets"] == ["OPE-100", "OPE-101", "OPE-102"]
    assert data["cluster_topic"] == "push"


def test_clear_drafts_includes_audit(tmp_path: Path):
    """clear_drafts limpa também `output/audit/` (rodada nova, auditoria fresca)."""
    store = ArtifactStore(tmp_path)
    cluster = Cluster(topic="x", tickets=["OPE-1"], summaries=["s"])
    store.save_article_markdown(cluster, _sample_article())
    store.save_audit(cluster, title="x", kind="article")
    removed = store.clear_drafts()
    assert removed >= 2  # pelo menos o md + o audit


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
