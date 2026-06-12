"""Atlassian Document Format (ADF) → markdown.

Diferente de `adf.extract_text_from_adf` (que flatten texto pra busca),
aqui preservamos estrutura — headings, listas, tabelas, ênfase — porque o
output vai ser usado como few-shot pro LLM (issue #10) e o "olha como
está organizado" é parte do sinal.

Cobre os nodes vistos em amostra real do space SUP da Kobe (paragraph,
heading, listas, tabelas, blockquote, panel, code, marks de ênfase).
Nodes não cobertos (mídia, mention, layout, expand, taskList) são
ignorados silenciosamente — bem suficiente pros artigos padrão do SUP.

Robusto a entradas malformadas: entrada inesperada → retorna "".
"""

from typing import Any


_HEADING_LEVELS = {1, 2, 3, 4, 5, 6}


def adf_to_markdown(node: Any) -> str:
    """Converte um nó ADF (geralmente o `doc` root) em markdown.

    Retorna string vazia para entradas inválidas (None, tipo errado).
    """
    if not isinstance(node, dict):
        return ""
    blocks = _render_blocks(node.get("content") or [])
    return "\n\n".join(b for b in blocks if b).strip()


# ─── block-level rendering ──────────────────────────────────────────


def _render_blocks(nodes: list) -> list[str]:
    out: list[str] = []
    for n in nodes:
        block = _render_block(n)
        if block:
            out.append(block)
    return out


def _render_block(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    t = node.get("type")

    if t == "paragraph":
        inline = _render_inline(node.get("content") or [])
        return inline

    if t == "heading":
        level = (node.get("attrs") or {}).get("level", 1)
        if level not in _HEADING_LEVELS:
            level = 1
        inline = _render_inline(node.get("content") or [])
        if not inline:
            return ""
        return f"{'#' * level} {inline}"

    if t == "bulletList":
        return _render_list(node.get("content") or [], ordered=False, depth=0)

    if t == "orderedList":
        return _render_list(node.get("content") or [], ordered=True, depth=0)

    if t == "blockquote":
        inner = _render_blocks(node.get("content") or [])
        if not inner:
            return ""
        # Prefixa cada linha com '> '
        prefixed = "\n".join(
            "> " + line if line else ">"
            for block in inner
            for line in block.split("\n")
        )
        return prefixed

    if t == "panel":
        # Panels do Confluence: info / note / warning / success / error.
        # Renderiza como blockquote com tag — preserva o "callout" sem
        # depender de extensão markdown específica.
        panel_kind = (node.get("attrs") or {}).get("panelType", "info")
        inner = _render_blocks(node.get("content") or [])
        if not inner:
            return ""
        body = "\n\n".join(inner)
        prefixed = "\n".join(f"> {line}" if line else ">" for line in body.split("\n"))
        return f"> [{panel_kind}]\n{prefixed}"

    if t == "rule":
        return "---"

    if t == "codeBlock":
        lang = (node.get("attrs") or {}).get("language", "") or ""
        # codeBlock content é text nodes — concatena raw
        text = "".join(
            child.get("text", "")
            for child in node.get("content") or []
            if isinstance(child, dict) and child.get("type") == "text"
        )
        return f"```{lang}\n{text}\n```"

    if t == "table":
        return _render_table(node)

    # Layout / expand / taskList / mídia: trata como "container desconhecido"
    # e recorre no content. Mantém o texto útil sem renderizar o wrapper.
    content = node.get("content")
    if isinstance(content, list):
        sub = _render_blocks(content)
        return "\n\n".join(sub) if sub else ""
    return ""


def _render_list(items: list, *, ordered: bool, depth: int) -> str:
    """Renderiza bulletList/orderedList com indentação por nível."""
    lines: list[str] = []
    indent = "  " * depth
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict) or item.get("type") != "listItem":
            continue
        marker = f"{i}." if ordered else "-"
        item_blocks = _render_item_blocks(item.get("content") or [], depth=depth + 1)
        if not item_blocks:
            continue
        first_line = item_blocks[0]
        lines.append(f"{indent}{marker} {first_line}")
        # Continuação de bloco do mesmo item, indentado mais
        cont_indent = indent + ("   " if ordered else "  ")
        for extra in item_blocks[1:]:
            for sub_line in extra.split("\n"):
                lines.append(f"{cont_indent}{sub_line}" if sub_line else "")
    return "\n".join(lines)


def _render_item_blocks(nodes: list, *, depth: int) -> list[str]:
    """Render blocks dentro de um listItem; nested lists herdam depth."""
    out: list[str] = []
    for n in nodes:
        if isinstance(n, dict) and n.get("type") in ("bulletList", "orderedList"):
            ordered = n.get("type") == "orderedList"
            sub = _render_list(n.get("content") or [], ordered=ordered, depth=depth)
            if sub:
                out.append(sub)
        else:
            b = _render_block(n)
            if b:
                out.append(b)
    return out


def _render_table(node: dict) -> str:
    """Renderiza tabela ADF em markdown.

    Detecta header pela presença de tableHeader na primeira linha;
    se ausente, gera separador genérico de 3 dashes por coluna.
    """
    rows = [
        r for r in (node.get("content") or [])
        if isinstance(r, dict) and r.get("type") == "tableRow"
    ]
    if not rows:
        return ""

    def cell_text(cell: dict) -> str:
        inner = _render_blocks(cell.get("content") or [])
        return " ".join(b.replace("\n", " ") for b in inner).strip() or " "

    table_lines: list[str] = []
    first_row_cells = rows[0].get("content") or []
    has_header = any(
        isinstance(c, dict) and c.get("type") == "tableHeader" for c in first_row_cells
    )

    n_cols = max(
        len(r.get("content") or []) for r in rows
    ) or 1

    if has_header:
        header_cells = [cell_text(c) for c in first_row_cells if isinstance(c, dict)]
        # Pad/truncate pro n_cols
        header_cells = (header_cells + [" "] * n_cols)[:n_cols]
        table_lines.append("| " + " | ".join(header_cells) + " |")
        table_lines.append("|" + "|".join([" --- "] * n_cols) + "|")
        body_rows = rows[1:]
    else:
        # Sem header explícito: usa primeira linha como dados,
        # e prepended separator pra formar markdown válido.
        table_lines.append("|" + "|".join([" "] * n_cols) + "|")
        table_lines.append("|" + "|".join([" --- "] * n_cols) + "|")
        body_rows = rows

    for r in body_rows:
        cells = [cell_text(c) for c in (r.get("content") or []) if isinstance(c, dict)]
        cells = (cells + [" "] * n_cols)[:n_cols]
        table_lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(table_lines)


# ─── inline rendering ───────────────────────────────────────────────


def _render_inline(nodes: list) -> str:
    parts: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t == "text":
            parts.append(_apply_marks(n.get("text", ""), n.get("marks") or []))
        elif t == "hardBreak":
            parts.append("  \n")  # markdown hard break
        elif t == "mention":
            # Mantém o display name se houver
            name = (n.get("attrs") or {}).get("text") or "@user"
            parts.append(name)
        elif t == "emoji":
            parts.append((n.get("attrs") or {}).get("shortName", ""))
        else:
            # Outros nodes desconhecidos com content inline → recursão
            content = n.get("content")
            if isinstance(content, list):
                parts.append(_render_inline(content))
    return "".join(p for p in parts if p)


def _apply_marks(text: str, marks: list) -> str:
    """Aplica marks ADF (strong, em, code, underline, link, strike) ao texto.

    Ordem importa: code é "mais interno" (não recebe ênfase em cima);
    link é "mais externo". Aplicamos em ordem fixa pra reprodutibilidade.
    """
    if not text:
        return ""

    mark_types = {m.get("type"): m for m in marks if isinstance(m, dict)}

    if "code" in mark_types:
        text = f"`{text}`"
    if "strong" in mark_types:
        text = f"**{text}**"
    if "em" in mark_types:
        text = f"*{text}*"
    if "strike" in mark_types:
        text = f"~~{text}~~"
    if "underline" in mark_types:
        # Markdown não tem underline canônico. Renderiza como ênfase pra
        # não perder o sinal — alternativa seria HTML <u>, mas markdown puro.
        text = f"_{text}_"
    if "link" in mark_types:
        href = (mark_types["link"].get("attrs") or {}).get("href", "")
        if href:
            text = f"[{text}]({href})"

    return text
