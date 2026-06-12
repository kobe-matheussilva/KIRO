"""Testes do parser ADF→markdown (issue #10)."""

from kiro.utils.adf_to_markdown import adf_to_markdown


def _doc(content: list) -> dict:
    return {"type": "doc", "content": content}


def _para(*inline) -> dict:
    return {"type": "paragraph", "content": list(inline)}


def _text(value: str, *marks: str, link: str = "") -> dict:
    mark_list = []
    for m in marks:
        if m == "link":
            mark_list.append({"type": "link", "attrs": {"href": link}})
        else:
            mark_list.append({"type": m})
    return {"type": "text", "text": value, "marks": mark_list}


# ─── robustez ───────────────────────────────────────────────────────


def test_returns_empty_for_none():
    assert adf_to_markdown(None) == ""


def test_returns_empty_for_non_dict():
    assert adf_to_markdown(42) == ""
    assert adf_to_markdown([1, 2]) == ""


def test_empty_doc():
    assert adf_to_markdown(_doc([])) == ""


# ─── paragraph + inline marks ───────────────────────────────────────


def test_simple_paragraph():
    out = adf_to_markdown(_doc([_para(_text("Olá mundo"))]))
    assert out == "Olá mundo"


def test_strong_renders_bold():
    out = adf_to_markdown(_doc([_para(_text("oi", "strong"))]))
    assert out == "**oi**"


def test_em_renders_italic():
    out = adf_to_markdown(_doc([_para(_text("oi", "em"))]))
    assert out == "*oi*"


def test_link_renders_markdown_link():
    out = adf_to_markdown(_doc([_para(_text("clique aqui", "link", link="https://ex.com"))]))
    assert out == "[clique aqui](https://ex.com)"


def test_code_mark_renders_inline_code():
    out = adf_to_markdown(_doc([_para(_text("API", "code"))]))
    assert out == "`API`"


def test_combined_marks_strong_em():
    out = adf_to_markdown(_doc([_para(_text("oi", "strong", "em"))]))
    assert "**" in out and "*" in out
    assert "oi" in out


def test_mixed_paragraph():
    out = adf_to_markdown(_doc([_para(
        _text("Use "),
        _text("VTEX", "strong"),
        _text(" pra integração."),
    )]))
    assert out == "Use **VTEX** pra integração."


# ─── headings ───────────────────────────────────────────────────────


def test_heading_h1():
    out = adf_to_markdown(_doc([
        {"type": "heading", "attrs": {"level": 1}, "content": [_text("Título")]}
    ]))
    assert out == "# Título"


def test_heading_h3():
    out = adf_to_markdown(_doc([
        {"type": "heading", "attrs": {"level": 3}, "content": [_text("Subseção")]}
    ]))
    assert out == "### Subseção"


def test_heading_invalid_level_falls_to_h1():
    out = adf_to_markdown(_doc([
        {"type": "heading", "attrs": {"level": 99}, "content": [_text("X")]}
    ]))
    assert out.startswith("# ")


# ─── listas ─────────────────────────────────────────────────────────


def test_bullet_list():
    out = adf_to_markdown(_doc([
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [_para(_text("um"))]},
            {"type": "listItem", "content": [_para(_text("dois"))]},
        ]}
    ]))
    assert "- um" in out
    assert "- dois" in out


def test_ordered_list():
    out = adf_to_markdown(_doc([
        {"type": "orderedList", "content": [
            {"type": "listItem", "content": [_para(_text("alpha"))]},
            {"type": "listItem", "content": [_para(_text("beta"))]},
        ]}
    ]))
    assert "1. alpha" in out
    assert "2. beta" in out


def test_nested_list():
    out = adf_to_markdown(_doc([
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [
                _para(_text("pai")),
                {"type": "bulletList", "content": [
                    {"type": "listItem", "content": [_para(_text("filho"))]},
                ]},
            ]},
        ]}
    ]))
    assert "- pai" in out
    assert "filho" in out
    # filho deve estar indentado
    filho_line = next(line for line in out.split("\n") if "filho" in line)
    assert filho_line.startswith("  ")


# ─── blockquote + panel ─────────────────────────────────────────────


def test_blockquote():
    out = adf_to_markdown(_doc([
        {"type": "blockquote", "content": [_para(_text("citação"))]}
    ]))
    assert out == "> citação"


def test_panel_info():
    out = adf_to_markdown(_doc([
        {"type": "panel", "attrs": {"panelType": "info"},
         "content": [_para(_text("aviso importante"))]}
    ]))
    assert "[info]" in out
    assert "> aviso importante" in out


# ─── rule + codeBlock + hardBreak ───────────────────────────────────


def test_rule():
    out = adf_to_markdown(_doc([{"type": "rule"}]))
    assert out == "---"


def test_code_block_with_language():
    out = adf_to_markdown(_doc([
        {"type": "codeBlock", "attrs": {"language": "python"},
         "content": [_text("print('oi')")]}
    ]))
    assert out.startswith("```python")
    assert "print('oi')" in out
    assert out.endswith("```")


def test_code_block_without_language():
    out = adf_to_markdown(_doc([
        {"type": "codeBlock", "content": [_text("texto")]}
    ]))
    assert out.startswith("```\n")
    assert "texto" in out


# ─── tabelas ────────────────────────────────────────────────────────


def test_table_with_header():
    table = {
        "type": "table",
        "content": [
            {"type": "tableRow", "content": [
                {"type": "tableHeader", "content": [_para(_text("Plataforma"))]},
                {"type": "tableHeader", "content": [_para(_text("Sistema"))]},
            ]},
            {"type": "tableRow", "content": [
                {"type": "tableCell", "content": [_para(_text("iOS"))]},
                {"type": "tableCell", "content": [_para(_text("APNs"))]},
            ]},
        ],
    }
    out = adf_to_markdown(_doc([table]))
    assert "| Plataforma | Sistema |" in out
    assert "| --- |" in out  # separator row
    assert "| iOS | APNs |" in out


def test_table_without_header():
    table = {
        "type": "table",
        "content": [
            {"type": "tableRow", "content": [
                {"type": "tableCell", "content": [_para(_text("a"))]},
                {"type": "tableCell", "content": [_para(_text("b"))]},
            ]},
        ],
    }
    out = adf_to_markdown(_doc([table]))
    assert "| a | b |" in out


# ─── nodes desconhecidos / robustez ─────────────────────────────────


def test_unknown_block_with_content_recurses():
    out = adf_to_markdown(_doc([
        {"type": "layoutSection", "content": [
            {"type": "layoutColumn", "content": [_para(_text("dentro"))]}
        ]},
    ]))
    assert "dentro" in out


def test_skips_unknown_inline_without_text():
    out = adf_to_markdown(_doc([
        _para(_text("antes "), {"type": "media", "attrs": {}}, _text(" depois"))
    ]))
    assert "antes" in out and "depois" in out


def test_hardBreak_produces_markdown_break():
    out = adf_to_markdown(_doc([_para(
        _text("linha 1"),
        {"type": "hardBreak"},
        _text("linha 2"),
    )]))
    # Markdown hard break = duas spaces + \n
    assert "linha 1" in out
    assert "linha 2" in out
    assert "  \n" in out or "\n" in out


# ─── integração: documento completo estilo SUP ──────────────────────


def test_full_sup_like_document():
    """Doc parecido com artigo SUP: H1 + parágrafo + H2 + lista + H2 + tabela."""
    doc = _doc([
        {"type": "heading", "attrs": {"level": 1}, "content": [_text("Configurando Push")]},
        _para(_text("Esta funcionalidade permite "), _text("envio de notificações", "strong"), _text(".")),
        {"type": "heading", "attrs": {"level": 2}, "content": [_text("Visão Geral")]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [_para(_text("iOS via APNs"))]},
            {"type": "listItem", "content": [_para(_text("Android via FCM"))]},
        ]},
        {"type": "heading", "attrs": {"level": 2}, "content": [_text("Perguntas Frequentes")]},
        _para(_text("Como ativar?")),
    ])
    out = adf_to_markdown(doc)
    assert "# Configurando Push" in out
    assert "**envio de notificações**" in out
    assert "## Visão Geral" in out
    assert "- iOS via APNs" in out
    assert "## Perguntas Frequentes" in out
