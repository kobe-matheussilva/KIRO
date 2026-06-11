"""Exporta ArticleDraft para .docx (Microsoft Word / Google Docs compatível).

Use case: enquanto o time não tem permissão no Confluence, os drafts saem como
.docx pra serem subidos no Google Drive e revisados pela equipe via comentários.
"""

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ
from kiro.utils.branding import SIGNATURE

_LEADING_NUMBER_RE = re.compile(r"^\s*\d+\s*[.\)\-]\s+")


def article_to_docx(article: ArticleDraft, cluster: Cluster, output_path: Path) -> Path:
    """Renderiza um draft como arquivo .docx. Retorna o path escrito.

    Formatação:
      - Título grande em destaque
      - Subtítulo itálico com contagem de tickets
      - Tickets de origem em parágrafo bold-prefix
      - Seções H1 (Problema, Causa raiz, Solução, FAQ, Metadados)
      - Solução como lista numerada nativa do Word
      - FAQ como pares pergunta(bold)/resposta
      - Rodapé com slogan KIRO centralizado em cinza
    """
    doc = Document()

    # Fonte padrão limpa
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    # ─── Cabeçalho ────────────────────────────────────────────────
    doc.add_heading(article.title, level=0)

    subtitle = doc.add_paragraph()
    run = subtitle.add_run(
        f"Rascunho gerado a partir de {cluster.count} tickets recorrentes."
    )
    run.italic = True
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    tickets_para = doc.add_paragraph()
    tickets_para.add_run("Tickets de origem: ").bold = True
    tickets_para.add_run(", ".join(cluster.tickets[:15]))
    if len(cluster.tickets) > 15:
        tickets_para.add_run(f" e mais {len(cluster.tickets) - 15} ticket(s).")

    # ─── Problema ────────────────────────────────────────────────
    doc.add_heading("Problema", level=1)
    doc.add_paragraph(article.problem)

    # ─── Causa raiz ──────────────────────────────────────────────
    doc.add_heading("Causa raiz", level=1)
    doc.add_paragraph(article.cause)

    # ─── Solução ─────────────────────────────────────────────────
    doc.add_heading("Solução", level=1)
    steps = [
        _LEADING_NUMBER_RE.sub("", line).strip()
        for line in article.solution.split("\n")
        if line.strip()
    ]
    for step in steps:
        doc.add_paragraph(step, style="List Number")

    # ─── FAQ ─────────────────────────────────────────────────────
    if article.faq:
        doc.add_heading("Perguntas frequentes", level=1)
        for item in article.faq:
            q_para = doc.add_paragraph()
            q_run = q_para.add_run(item.question)
            q_run.bold = True
            doc.add_paragraph(item.answer)

    # ─── Metadados ──────────────────────────────────────────────
    doc.add_heading("Metadados", level=1)
    metadata = [
        ("Componentes", ", ".join(cluster.components) or "—"),
        ("Labels", ", ".join(cluster.labels) or "—"),
        ("Tags do artigo", ", ".join(article.tags) or "—"),
        ("Total de tickets do cluster", str(cluster.count)),
    ]
    for label, value in metadata:
        para = doc.add_paragraph()
        para.add_run(f"{label}: ").bold = True
        para.add_run(value)

    # ─── Rodapé com marca ────────────────────────────────────────
    doc.add_paragraph()  # spacer
    footer_para = doc.add_paragraph()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer_para.add_run(SIGNATURE)
    footer_run.italic = True
    footer_run.font.size = Pt(9)
    footer_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


def customer_faq_to_docx(faq: CustomerFAQ, cluster: Cluster, output_path: Path) -> Path:
    """Renderiza um CustomerFAQ como .docx voltado pra revisão pelo varejista.

    Estrutura focada em leitura self-service:
      - Título do tópico FAQ
      - Intro (parágrafo introdutório)
      - Cada entry como pergunta (bold + tamanho maior) → resposta → quando contatar (se houver)
      - Tags ao final
      - Rodapé com slogan
    """
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    # ─── Cabeçalho ────────────────────────────────────────────────
    doc.add_heading(faq.title, level=0)

    # Subtítulo identificando como FAQ self-service
    subtitle = doc.add_paragraph()
    sub_run = subtitle.add_run("FAQ — self-service para o time de produto/operação do varejista")
    sub_run.italic = True
    sub_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    # Intro
    doc.add_paragraph(faq.intro)

    # ─── Perguntas ───────────────────────────────────────────────
    doc.add_heading("Perguntas frequentes", level=1)
    for idx, entry in enumerate(faq.entries, start=1):
        # Pergunta como heading 2 (numerada)
        q_heading = doc.add_heading(f"{idx}. {entry.question}", level=2)
        # ajusta cor pra preto pra não ficar azul-padrão de heading
        for run in q_heading.runs:
            run.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)

        # Resposta
        doc.add_paragraph(entry.answer)

        # Quando contatar suporte (opcional)
        if entry.when_to_contact:
            wtc_para = doc.add_paragraph()
            wtc_para.add_run("Quando abrir um ticket de suporte: ").bold = True
            wtc_para.add_run(entry.when_to_contact)

    # ─── Tags ────────────────────────────────────────────────────
    if faq.tags:
        doc.add_heading("Tags", level=1)
        tags_para = doc.add_paragraph()
        tags_run = tags_para.add_run(", ".join(faq.tags))
        tags_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    # ─── Origem (visível só pra time interno na revisão) ─────────
    doc.add_paragraph()
    origin_para = doc.add_paragraph()
    origin_run = origin_para.add_run(
        f"FAQ gerada a partir de {cluster.count} tickets recorrentes "
        f"durante o período analisado pelo KIRO."
    )
    origin_run.italic = True
    origin_run.font.size = Pt(9)
    origin_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    # ─── Rodapé com marca ────────────────────────────────────────
    footer_para = doc.add_paragraph()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_run = footer_para.add_run(SIGNATURE)
    footer_run.italic = True
    footer_run.font.size = Pt(9)
    footer_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
