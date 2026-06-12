"""Testes do helper format_style_examples_block + efeito no prompt (issue #10)."""

from kiro.application.generation.anthropic_provider import AnthropicProvider
from kiro.application.generation.gemini_provider import GeminiProvider
from kiro.application.generation.style_examples import format_style_examples_block
from kiro.domain.models import Cluster, GitBookChunk


def _chunk(page: str, section: str, content: str) -> GitBookChunk:
    return GitBookChunk(
        page_title=page,
        page_url=f"https://confluence/{page.lower()}",
        section_title=section,
        section_anchor=section.lower(),
        content=content,
    )


def _cluster() -> Cluster:
    return Cluster(
        topic="cashback por loja",
        tickets=["OPE-1"],
        summaries=["s"],
        labels=["cashback"],
        components=[],
    )


# ─── helper isolado ─────────────────────────────────────────────────


def test_empty_examples_returns_empty_string():
    assert format_style_examples_block([]) == ""


def test_block_titles_each_example():
    block = format_style_examples_block(
        [
            _chunk("Cashback por Item", "Visão Geral", "conteudo a"),
            _chunk("Personalização Cartão", "Visão Geral", "conteudo b"),
        ]
    )
    assert "EXEMPLOS DO ESTILO KOBE" in block
    assert "Exemplo 1 — Cashback por Item" in block
    assert "Exemplo 2 — Personalização Cartão" in block


def test_block_does_not_emit_chunk_urls():
    """Política: URLs do Confluence NUNCA chegam ao modelo."""
    chunk = _chunk("X", "y", "z")
    block = format_style_examples_block([chunk])
    assert chunk.page_url not in block


def test_block_instructs_not_to_cite_or_copy():
    """Regras absolutas têm que estar no bloco — não citar fontes, não copiar."""
    block = format_style_examples_block([_chunk("X", "y", "z")])
    assert "NÃO copie" in block
    assert "NÃO mencione" in block
    assert "NÃO inclua URLs" in block


def test_block_instructs_to_imitate_tone_and_structure():
    block = format_style_examples_block([_chunk("X", "y", "z")])
    assert "ESTRUTURA" in block
    assert "VOCABULÁRIO" in block
    assert "TOM" in block


def test_block_includes_chunk_content():
    block = format_style_examples_block(
        [_chunk("X", "y", "Configurando push notifications no painel admin")]
    )
    assert "Configurando push notifications" in block


# ─── efeito no prompt (Gemini + Anthropic) ──────────────────────────


def test_gemini_article_includes_style_block_when_present():
    examples = [_chunk("Cashback", "Visão", "Cashback por item permite configurar...")]
    p_with = GeminiProvider._build_prompt(_cluster(), style_examples=examples)
    p_without = GeminiProvider._build_prompt(_cluster())
    assert "EXEMPLOS DO ESTILO KOBE" in p_with
    assert "Cashback por item" in p_with
    assert "EXEMPLOS DO ESTILO KOBE" not in p_without


def test_gemini_faq_includes_style_block_when_present():
    examples = [_chunk("Cashback", "Visão", "exemplo de FAQ Kobe")]
    p_with = GeminiProvider._build_customer_faq_prompt(_cluster(), style_examples=examples)
    p_without = GeminiProvider._build_customer_faq_prompt(_cluster())
    assert "EXEMPLOS DO ESTILO KOBE" in p_with
    assert "EXEMPLOS DO ESTILO KOBE" not in p_without


def test_anthropic_article_includes_style_block_when_present():
    examples = [_chunk("Cashback", "Visão", "exemplo Kobe")]
    p_with = AnthropicProvider._build_prompt(_cluster(), style_examples=examples)
    p_without = AnthropicProvider._build_prompt(_cluster())
    assert "EXEMPLOS DO ESTILO KOBE" in p_with
    assert "EXEMPLOS DO ESTILO KOBE" not in p_without


def test_anthropic_faq_delegates_with_style_examples():
    """Anthropic delega FAQ pro Gemini — confirma que style_examples passa."""
    examples = [_chunk("X", "y", "exemplo")]
    p_with = AnthropicProvider._build_customer_faq_prompt(_cluster(), style_examples=examples)
    assert "EXEMPLOS DO ESTILO KOBE" in p_with


def test_style_block_appears_after_diretrizes_before_formato_in_article():
    """Estrutura: contexto → kb_context → diretrizes → style → formato."""
    examples = [_chunk("X", "y", "exemplo")]
    prompt = GeminiProvider._build_prompt(_cluster(), style_examples=examples)
    style_idx = prompt.index("EXEMPLOS DO ESTILO KOBE")
    directives_idx = prompt.index("DIRETRIZES POSITIVAS")
    format_idx = prompt.index("FORMATO DE RESPOSTA")
    assert directives_idx < style_idx < format_idx


def test_style_block_appears_after_diretrizes_before_formato_in_faq():
    examples = [_chunk("X", "y", "exemplo")]
    prompt = GeminiProvider._build_customer_faq_prompt(_cluster(), style_examples=examples)
    style_idx = prompt.index("EXEMPLOS DO ESTILO KOBE")
    directives_idx = prompt.index("DIRETRIZES OBRIGATÓRIAS")
    format_idx = prompt.index("FORMATO DE RESPOSTA")
    assert directives_idx < style_idx < format_idx


def test_kb_context_and_style_examples_coexist_in_prompt():
    """kb_context e style_examples não conflitam — podem aparecer no mesmo prompt."""
    kb = [_chunk("GitBook Doc", "Setup", "config técnica")]
    style = [_chunk("SUP Artigo", "Visão", "exemplo de estilo")]
    prompt = GeminiProvider._build_prompt(_cluster(), kb_context=kb, style_examples=style)
    # Ambos os blocos estão presentes
    assert "REFERÊNCIA KOBE" in prompt  # kb_context
    assert "EXEMPLOS DO ESTILO KOBE" in prompt  # style_examples
    # kb_context vem ANTES do style_examples
    kb_idx = prompt.index("REFERÊNCIA KOBE")
    style_idx = prompt.index("EXEMPLOS DO ESTILO KOBE")
    assert kb_idx < style_idx
