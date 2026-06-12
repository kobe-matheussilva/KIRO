"""Helpers de formatação de contexto RAG para o prompt do LLM.

Mantido aqui (não em cada provider) pra garantir que Gemini e Anthropic
recebam EXATAMENTE o mesmo bloco — o conteúdo é agnostic de provedor e
qualquer divergência afetaria reprodutibilidade.

Política firmada com o usuário (ver memória feedback-kiro-no-external-links):
artigos/FAQs NÃO devem citar nem linkar pra a GitBook. Os trechos abaixo são
grounding interno; o prompt instrui explicitamente o modelo a NÃO mencionar
fontes nem URLs no output.
"""

from typing import Sequence

from kiro.domain.models import GitBookChunk


def format_kb_context_block(chunks: Sequence[GitBookChunk]) -> str:
    """Devolve o bloco de referência pra ser injetado antes das diretrizes.

    Retorna string vazia quando `chunks` é vazio — o caller concatena
    direto no prompt sem precisar de branch.
    """
    if not chunks:
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("═══════════════════════════════════════════════════════════════")
    lines.append("REFERÊNCIA KOBE — grounding interno (use se relevante, ignore se não)")
    lines.append("═══════════════════════════════════════════════════════════════")
    lines.append("")
    lines.append(
        "Os trechos abaixo vêm da documentação técnica interna da Kobe e foram "
        "selecionados por similaridade com o tema deste cluster. USE como base "
        "factual ao escrever, mas siga estas regras:"
    )
    lines.append("")
    lines.append(
        "- NUNCA cite a documentação como fonte (\"ver documentação X\", \"conforme GitBook\") "
        "nem inclua URLs no output — o leitor do artigo NÃO tem acesso a essas referências."
    )
    lines.append("- NÃO copie literalmente — reformule no tom externo do artigo.")
    lines.append(
        "- IGNORE qualquer trecho fora de tópico — relevância foi estimada por "
        "similaridade lexical, não por compreensão semântica."
    )
    lines.append("")

    for idx, chunk in enumerate(chunks, start=1):
        lines.append(f"[Trecho {idx}] Página: \"{chunk.page_title}\"")
        lines.append(f"           Seção: \"{chunk.section_title}\"")
        lines.append("─────────────────────────────────────────────────────────────")
        lines.append(chunk.content.strip())
        lines.append("─────────────────────────────────────────────────────────────")
        lines.append("")

    return "\n".join(lines)
