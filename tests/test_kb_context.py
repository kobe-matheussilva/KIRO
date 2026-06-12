"""Testes do helper format_kb_context_block — formato do bloco e efeito no prompt."""

from kiro.application.generation.anthropic_provider import AnthropicProvider
from kiro.application.generation.gemini_provider import GeminiProvider
from kiro.application.generation.kb_context import format_kb_context_block
from kiro.domain.models import Cluster, GitBookChunk


def _chunk(page: str, section: str, content: str) -> GitBookChunk:
    return GitBookChunk(
        page_title=page,
        page_url=f"https://example.com/{page.lower()}",
        section_title=section,
        section_anchor=section.lower(),
        content=content,
    )


def _cluster() -> Cluster:
    return Cluster(
        topic="push notifications iOS",
        tickets=["OPE-1", "OPE-2"],
        summaries=["push não chega no iOS", "iOS não recebe notificação"],
        labels=["push", "ios"],
        components=["mobile"],
    )


def test_empty_context_returns_empty_string():
    assert format_kb_context_block([]) == ""


def test_block_includes_chunk_metadata_and_content():
    block = format_kb_context_block(
        [_chunk("Push", "Setup iOS", "Configurar APNs com o certificado correto.")]
    )
    assert "REFERÊNCIA KOBE" in block
    assert 'Página: "Push"' in block
    assert 'Seção: "Setup iOS"' in block
    assert "APNs" in block


def test_block_forbids_citing_source_to_model():
    """Bloco precisa instruir o modelo a NÃO citar a fonte no output —
    política firmada em feedback-kiro-no-external-links."""
    block = format_kb_context_block([_chunk("Push", "x", "qualquer conteúdo")])
    assert "NUNCA cite" in block or "NÃO cite" in block.replace(" ", " ") or "não inclua URL" in block.lower()


def test_block_does_not_emit_chunk_urls():
    """URLs dos chunks NÃO podem aparecer no bloco — modelo nem deve ver pra não tentar copiar."""
    chunk = _chunk("Push", "x", "qualquer conteúdo")
    block = format_kb_context_block([chunk])
    assert chunk.page_url not in block


def test_block_numbers_chunks():
    block = format_kb_context_block(
        [
            _chunk("A", "secA", "conteudo a"),
            _chunk("B", "secB", "conteudo b"),
            _chunk("C", "secC", "conteudo c"),
        ]
    )
    assert "[Trecho 1]" in block
    assert "[Trecho 2]" in block
    assert "[Trecho 3]" in block


# ─── efeito no prompt ──────────────────────────────────────────────


def test_gemini_article_prompt_includes_kb_block_when_context_present():
    cluster = _cluster()
    chunks = [_chunk("Push", "iOS", "Configurar APNs com certificado válido")]
    prompt_with = GeminiProvider._build_prompt(cluster, chunks)
    prompt_without = GeminiProvider._build_prompt(cluster)
    assert "REFERÊNCIA KOBE" in prompt_with
    assert "APNs" in prompt_with
    assert "REFERÊNCIA KOBE" not in prompt_without


def test_gemini_faq_prompt_includes_kb_block_when_context_present():
    cluster = _cluster()
    chunks = [_chunk("Push", "iOS", "Configurar APNs com certificado válido")]
    prompt_with = GeminiProvider._build_customer_faq_prompt(cluster, chunks)
    prompt_without = GeminiProvider._build_customer_faq_prompt(cluster)
    assert "REFERÊNCIA KOBE" in prompt_with
    assert "REFERÊNCIA KOBE" not in prompt_without


def test_anthropic_article_prompt_includes_kb_block_when_context_present():
    cluster = _cluster()
    chunks = [_chunk("Push", "iOS", "Configurar APNs com certificado válido")]
    prompt_with = AnthropicProvider._build_prompt(cluster, chunks)
    prompt_without = AnthropicProvider._build_prompt(cluster)
    assert "REFERÊNCIA KOBE" in prompt_with
    assert "APNs" in prompt_with
    assert "REFERÊNCIA KOBE" not in prompt_without


def test_anthropic_faq_prompt_delegates_with_kb_block():
    """Anthropic delega FAQ pro Gemini — confirma que kb_context passa pelo wrapper."""
    cluster = _cluster()
    chunks = [_chunk("Push", "iOS", "Configurar APNs")]
    prompt_with = AnthropicProvider._build_customer_faq_prompt(cluster, chunks)
    assert "REFERÊNCIA KOBE" in prompt_with
    assert "APNs" in prompt_with


def test_kb_block_appears_before_directives_in_article_prompt():
    """Bloco precisa entrar ANTES das diretrizes pro modelo já ler o contexto
    enquanto está sendo instruído sobre como escrever."""
    cluster = _cluster()
    chunks = [_chunk("Push", "iOS", "Configurar APNs")]
    prompt = GeminiProvider._build_prompt(cluster, chunks)
    ref_idx = prompt.index("REFERÊNCIA KOBE")
    directives_idx = prompt.index("DIRETRIZES POSITIVAS")
    assert ref_idx < directives_idx
