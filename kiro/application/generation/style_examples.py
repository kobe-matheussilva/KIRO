"""Helper de formatação de exemplos de estilo pro prompt do LLM (issue #10).

Diferente de `kb_context` (issue #3, grounding factual), aqui o objetivo
é tom + estrutura + vocabulário. O LLM deve IMITAR como o exemplo está
escrito sem copiar o conteúdo nem mencionar a fonte.

Política firmada em `feedback-kiro-no-external-links`: artigos NUNCA
citam URLs nem fazem referência a esses exemplos no output. O prompt
instrui isso explicitamente em três regras absolutas.
"""

from typing import Sequence

from kiro.domain.models import GitBookChunk


def format_style_examples_block(chunks: Sequence[GitBookChunk]) -> str:
    """Retorna bloco "EXEMPLOS DO ESTILO KOBE" pronto pra concatenar.

    Vazio quando `chunks` é vazio — caller injeta sem precisar de branch.
    """
    if not chunks:
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("═══════════════════════════════════════════════════════════════")
    lines.append("EXEMPLOS DO ESTILO KOBE — imite o TOM, não copie o conteúdo")
    lines.append("═══════════════════════════════════════════════════════════════")
    lines.append("")
    lines.append(
        "Os trechos abaixo são artigos publicados pela Kobe — representam o "
        "padrão de redação aprovado pelos varejistas. Use como guia de:"
    )
    lines.append("")
    lines.append(
        "- ESTRUTURA: como o artigo é organizado (Visão Geral, "
        "Cloud Commerce e Integrações, Perguntas Frequentes)"
    )
    lines.append(
        "- VOCABULÁRIO: termos consistentes usados em produção "
        "(\"Cloud Commerce\", \"Master Data\", \"sistemas envolvidos\", "
        "\"painel admin\", \"sua loja\", \"seu aplicativo\")"
    )
    lines.append(
        "- TOM: instrucional, direto, sem jargão de engenharia — "
        "fala COM o varejista, não SOBRE o problema"
    )
    lines.append("")
    lines.append("REGRAS ABSOLUTAS sobre os exemplos:")
    lines.append(
        "- NÃO copie o conteúdo dos exemplos. O cluster atual é "
        "provavelmente sobre OUTRO tópico — só o estilo é reaproveitável."
    )
    lines.append(
        "- NÃO mencione esses exemplos no output (\"conforme visto em\", "
        "\"de acordo com a documentação X\")."
    )
    lines.append(
        "- NÃO inclua URLs de origem — o leitor não tem acesso a elas."
    )
    lines.append("")

    for idx, chunk in enumerate(chunks, start=1):
        lines.append(f"───── Exemplo {idx} — {chunk.page_title} ─────")
        lines.append(chunk.content.strip())
        lines.append("─────")
        lines.append("")

    return "\n".join(lines)
