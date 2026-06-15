"""Regras determinísticas pra detectar vazamento interno + qualidade fraca.

BLOCK = não pode aparecer no output cliente-facing (códigos OPE, jargão
de engenharia, URLs externas). Trata como erro no pipeline.

WARN = sinal de qualidade baixa (poucos passos, frases genéricas).
Salva mas anota no relatório pra revisor.

Cada regra recebe um `dict[str, str]` (campo→texto) e devolve
`list[Violation]`. Regras específicas a tipo de draft (artigo vs FAQ)
ficam em listas separadas — `RULES_COMMON` aplica aos dois.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Literal

from kiro.domain.models import ArticleDraft, CustomerFAQ

Severity = Literal["block", "warn"]


@dataclass(frozen=True)
class Violation:
    rule_name: str
    severity: Severity
    field: str
    message: str


@dataclass(frozen=True)
class LintRule:
    name: str
    severity: Severity
    check: Callable[[dict[str, str]], list[Violation]]
    description: str = ""


# ─── extratores de texto ────────────────────────────────────────────


def collect_article_texts(draft: ArticleDraft) -> dict[str, str]:
    """Campos textuais escaneáveis de um ArticleDraft.

    Após o redesign da issue #15, ArticleDraft tem `scope_note` + `sections`
    (cada section com `heading` + `body`) — sem mais problem/cause/solution.
    Dot-path: `sections.0.heading`, `sections.0.body`.
    """
    out: dict[str, str] = {
        "title": draft.title,
        "scope_note": draft.scope_note,
    }
    for i, section in enumerate(draft.sections):
        out[f"sections.{i}.heading"] = section.heading
        out[f"sections.{i}.body"] = section.body
    return out


def collect_faq_texts(draft: CustomerFAQ) -> dict[str, str]:
    """Campos textuais escaneáveis de um CustomerFAQ.

    Após o redesign da issue #15, `intro` virou `scope_note` (curto).
    """
    out: dict[str, str] = {
        "title": draft.title,
        "scope_note": draft.scope_note,
    }
    for i, entry in enumerate(draft.entries):
        out[f"entries.{i}.question"] = entry.question
        out[f"entries.{i}.answer"] = entry.answer
        if entry.when_to_contact:
            out[f"entries.{i}.when_to_contact"] = entry.when_to_contact
    return out


# ─── helpers de matching ────────────────────────────────────────────


_OPE_RE = re.compile(r"\bOPE-\d+\b", re.IGNORECASE)

_INTERNAL_JARGON_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "bug", "workaround", "regressão", "root cause", "causa raiz", "stack trace"
    # word boundary pra evitar falsos positivos ("debug", "ambiguidade")
    ("bug", re.compile(r"\bbugs?\b", re.IGNORECASE)),
    ("workaround", re.compile(r"\bworkarounds?\b", re.IGNORECASE)),
    ("regressão", re.compile(r"\bregress(ão|oes|ões|ion)\b", re.IGNORECASE)),
    ("root cause", re.compile(r"\broot\s+cause\b", re.IGNORECASE)),
    ("causa raiz", re.compile(r"\bcausa\s+ra[ií]z(es)?\b", re.IGNORECASE)),
    ("stack trace", re.compile(r"\bstack\s*trace\b", re.IGNORECASE)),
    ("hotfix", re.compile(r"\bhotfix(es)?\b", re.IGNORECASE)),
)

# Componentes internos da Kobe — nomes que cliente NÃO deve ver.
# Lista derivada do prompt de proibições atual + memória de produto.
_INTERNAL_COMPONENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("WebView", re.compile(r"\bwebviews?\b", re.IGNORECASE)),
    ("SDK Connect", re.compile(r"\bsdk\s*connect\b", re.IGNORECASE)),
    ("Mobile Connect", re.compile(r"\bmobile\s*connect(\s*sdk)?\b", re.IGNORECASE)),
)

_TEAM_REFERENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("time interno", re.compile(r"\btime\s+interno\b", re.IGNORECASE)),
    ("nosso backlog", re.compile(r"\bnosso\s+backlog\b", re.IGNORECASE)),
    ("nossa engenharia", re.compile(r"\bnossa\s+engenharia\b", re.IGNORECASE)),
    ("nosso time", re.compile(r"\bnosso\s+time\b", re.IGNORECASE)),
    ("nosso sprint", re.compile(r"\bno(sso|ssa)\s+sprint\b", re.IGNORECASE)),
)

_EXTERNAL_URL_HOSTS: tuple[str, ...] = (
    "gitbook.io",
    "atlassian.net",
    "kobeapps.gitbook",
    "confluence.kobe",
)
_URL_RE = re.compile(r"https?://[^\s)<>\"']+", re.IGNORECASE)

# Triple-backtick code blocks ou inline code substancial
_CODE_FENCE_RE = re.compile(r"```|<code[\s>]")
# Stack trace típica: "at FunctionName (file.js:123)" ou "Traceback (most recent call last)"
_STACK_TRACE_RE = re.compile(
    r"\bTraceback\b|\bat\s+\w+\s*\([^)]*:\d+\)", re.IGNORECASE
)

_GENERIC_PHRASES: tuple[str, ...] = (
    "verifique as configurações",
    "verifique as configuracoes",
    "limpe o cache",
    "tente novamente",
    "entre em contato com o suporte",
    "abra um chamado",
    "contate o suporte",
)

# ─── Issue #15: qualidade estrutural (WARN) ─────────────────────────

# Palavras genéricas no título — não nomeiam funcionalidade/módulo específico.
# Pelo menos 2 dessas no título indica título "guarda-chuva" (warn).
_GENERIC_TITLE_WORDS: frozenset[str] = frozenset({
    "otimizando", "otimização", "otimizacao",
    "configurações", "configuracoes",
    "geral", "diversos", "diversas",
    "análise", "analise",
    "gestão", "gestao",
    "aplicativo",  # sem dizer qual app
    "seu",  # "no seu aplicativo" — vago
    "sua",
    "melhores", "boas",  # "melhores práticas", "boas práticas"
    "práticas", "praticas",
})

# Headings de "relatório técnico" que a chefe explicitamente pediu pra evitar
# (feedback de 2026-06-14 sobre primeira rodada).
_REPORT_STRUCTURE_HEADINGS: frozenset[str] = frozenset({
    "sobre este artigo", "sobre o artigo", "sobre este faq",
    "visão geral", "visao geral",
    "quando isso acontece",
    "como resolver",
    "como resolver isso",
    "introdução", "introducao",
    "objetivo", "objetivos",
    "contexto",
    "problema", "problemas",
    "causa", "causas", "causa raiz",
    "solução", "solucao",
})

# Prefixos burocráticos em perguntas — cliente não fala assim no chat.
_BUROCRATIC_QUESTION_PREFIXES: tuple[str, ...] = (
    "quais procedimentos",
    "qual o procedimento",
    "qual procedimento",
    "quais providências",
    "quais providencias",
    "quais medidas",
    "quais são os procedimentos",
    "qual a metodologia",
    "qual o protocolo",
)

# Limiares
_SCOPE_NOTE_MAX_CHARS = 200
_QUESTION_MAX_CHARS = 80


# ─── regras BLOCK (vazamento interno) ───────────────────────────────


def _check_ope_codes(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        m = _OPE_RE.search(text or "")
        if m:
            out.append(
                Violation(
                    rule_name="no_ope_codes",
                    severity="block",
                    field=name,
                    message=f"código de ticket interno '{m.group(0)}' não pode aparecer no output",
                )
            )
    return out


def _check_internal_jargon(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        for label, pattern in _INTERNAL_JARGON_PATTERNS:
            if pattern.search(text):
                out.append(
                    Violation(
                        rule_name="no_internal_jargon",
                        severity="block",
                        field=name,
                        message=f"jargão interno '{label}' não deve aparecer no output cliente-facing",
                    )
                )
    return out


def _check_internal_components(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        for label, pattern in _INTERNAL_COMPONENT_PATTERNS:
            if pattern.search(text):
                out.append(
                    Violation(
                        rule_name="no_internal_components",
                        severity="block",
                        field=name,
                        message=f"componente interno '{label}' não deve aparecer — use termos do produto do varejista",
                    )
                )
    return out


def _check_team_references(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        for label, pattern in _TEAM_REFERENCE_PATTERNS:
            if pattern.search(text):
                out.append(
                    Violation(
                        rule_name="no_team_references",
                        severity="block",
                        field=name,
                        message=f"referência de equipe '{label}' não deve aparecer no output",
                    )
                )
    return out


def _check_external_urls(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        for url in _URL_RE.findall(text):
            for host in _EXTERNAL_URL_HOSTS:
                if host in url.lower():
                    out.append(
                        Violation(
                            rule_name="no_external_urls",
                            severity="block",
                            field=name,
                            message=f"URL de fonte interna ('{host}') não deve aparecer no output",
                        )
                    )
                    break
    return out


def _check_code_or_trace(fields: dict[str, str]) -> list[Violation]:
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        if _CODE_FENCE_RE.search(text):
            out.append(
                Violation(
                    rule_name="no_code_or_trace",
                    severity="block",
                    field=name,
                    message="bloco de código (```) detectado — não deve aparecer no output cliente-facing",
                )
            )
        if _STACK_TRACE_RE.search(text):
            out.append(
                Violation(
                    rule_name="no_code_or_trace",
                    severity="block",
                    field=name,
                    message="stack trace detectado — não deve aparecer no output",
                )
            )
    return out


# ─── regras WARN (qualidade mínima) ─────────────────────────────────


def _check_generic_phrases(fields: dict[str, str]) -> list[Violation]:
    """Frases vagas que indicam baixa especificidade."""
    out: list[Violation] = []
    for name, text in fields.items():
        if not text:
            continue
        lower = text.lower()
        for phrase in _GENERIC_PHRASES:
            if phrase in lower:
                out.append(
                    Violation(
                        rule_name="generic_phrases",
                        severity="warn",
                        field=name,
                        message=f"frase genérica '{phrase}' — considere algo mais específico",
                    )
                )
    return out


def _check_article_min_sections(fields: dict[str, str]) -> list[Violation]:
    """Conta sections distintas; alvo é >= 3 (artigos rasos viram FAQ).

    Substitui solution_step_count após o redesign da issue #15. Schema
    já enforça min 2 — esta regra recomenda 3+ pra cobertura razoável.
    """
    section_ids: set[str] = set()
    for key in fields:
        if key.startswith("sections.") and key.endswith(".heading"):
            section_ids.add(key.split(".")[1])
    n = len(section_ids)
    if 0 < n < 3:
        return [
            Violation(
                rule_name="article_min_sections",
                severity="warn",
                field="sections",
                message=f"artigo tem {n} section(s) — ideal 3+ pra cobertura razoável",
            )
        ]
    return []


def _check_field_lengths(fields: dict[str, str]) -> list[Violation]:
    """Tamanhos mínimos por campo. Heurística simples — abaixo disso,
    raro dar conta de cobrir o tópico com detalhe.

    Após redesign da issue #15: scope_note é CURTO por design (1 frase),
    então o threshold mínimo é baixo. Conteúdo principal vai nas sections.
    """
    min_lengths = {
        "scope_note": 30,
        "title": 15,
    }
    out: list[Violation] = []
    for field, min_len in min_lengths.items():
        text = fields.get(field)
        if text is not None and len(text.strip()) < min_len:
            out.append(
                Violation(
                    rule_name="field_too_short",
                    severity="warn",
                    field=field,
                    message=f"campo '{field}' tem {len(text.strip())} chars (mínimo recomendado: {min_len})",
                )
            )
    return out


def _check_title_too_generic(fields: dict[str, str]) -> list[Violation]:
    """Título deve nomear funcionalidade/módulo. >=2 palavras genéricas = warn.

    Issue #15: feedback da chefe "não fica claro qual a funcionalidade ou
    módulo está sendo impactado". Heurística: conta palavras vagas
    ("otimizando", "configurações", "aplicativo") sem nome de produto.
    """
    title = fields.get("title", "")
    if not title:
        return []
    normalized = unicodedata.normalize("NFKD", title.lower())
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    words = re.findall(r"[a-z]+", ascii_only)
    generic_hits = sum(1 for w in words if w in _GENERIC_TITLE_WORDS)
    if generic_hits >= 2:
        return [
            Violation(
                rule_name="title_too_generic",
                severity="warn",
                field="title",
                message=(
                    f"título tem {generic_hits} palavras genéricas — considere "
                    f"identificar a funcionalidade ou módulo específico"
                ),
            )
        ]
    return []


def _check_has_report_structure(fields: dict[str, str]) -> list[Violation]:
    """Bloqueia headings tipo 'Sobre este artigo' / 'Como resolver' / etc.

    Issue #15: padrão SUP usa perguntas naturais como heading, NÃO seções
    tipo relatório técnico. Headings dessa lista viram WARN.
    """
    out: list[Violation] = []
    for key, text in fields.items():
        if not key.endswith(".heading") and key != "title":
            continue
        if not text:
            continue
        normalized = unicodedata.normalize("NFKD", text.lower().strip())
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        # Remove pontuação final pra match limpo
        cleaned = re.sub(r"[?!.:]+$", "", ascii_only).strip()
        if cleaned in _REPORT_STRUCTURE_HEADINGS:
            out.append(
                Violation(
                    rule_name="has_report_structure",
                    severity="warn",
                    field=key,
                    message=(
                        f"heading '{text}' tem estrutura de relatório — "
                        f"prefira pergunta natural ou tópico específico"
                    ),
                )
            )
    return out


def _check_scope_note_too_long(fields: dict[str, str]) -> list[Violation]:
    """scope_note deve ser CURTO (1-2 frases). >200 chars = warn.

    Issue #15: chefe pediu "intro mais simples e direcionada". O scope_note
    longo indica que voltamos ao formato relatório.
    """
    scope = fields.get("scope_note", "")
    if not scope:
        return []
    n = len(scope.strip())
    if n > _SCOPE_NOTE_MAX_CHARS:
        return [
            Violation(
                rule_name="scope_note_too_long",
                severity="warn",
                field="scope_note",
                message=(
                    f"scope_note tem {n} chars (máximo recomendado: "
                    f"{_SCOPE_NOTE_MAX_CHARS}) — corte pra 1-2 frases"
                ),
            )
        ]
    return []


def _check_question_too_burocratica(fields: dict[str, str]) -> list[Violation]:
    """Perguntas devem ser naturais (chat-style), não burocráticas.

    Issue #15: chefe disse "no chat o cliente faz perguntas bem direcionadas".
    Heurística: pergunta > 80 chars OU começa com prefixo burocrático.
    Aplica a entries.N.question (CustomerFAQ).
    """
    out: list[Violation] = []
    for key, text in fields.items():
        if not key.startswith("entries.") or not key.endswith(".question"):
            continue
        if not text:
            continue
        cleaned = text.strip()
        normalized = unicodedata.normalize("NFKD", cleaned.lower())
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        if any(ascii_only.startswith(p) for p in _BUROCRATIC_QUESTION_PREFIXES):
            out.append(
                Violation(
                    rule_name="question_too_burocratica",
                    severity="warn",
                    field=key,
                    message=(
                        f"pergunta com prefixo burocrático — reformule como "
                        f"o cliente perguntaria no chat ('Como ativo...?')"
                    ),
                )
            )
            continue
        if len(cleaned) > _QUESTION_MAX_CHARS:
            out.append(
                Violation(
                    rule_name="question_too_burocratica",
                    severity="warn",
                    field=key,
                    message=(
                        f"pergunta com {len(cleaned)} chars (máximo: "
                        f"{_QUESTION_MAX_CHARS}) — encurte pra tom de chat"
                    ),
                )
            )
    return out


def _check_faq_entries_count(fields: dict[str, str]) -> list[Violation]:
    """CustomerFAQ ideal tem 5+ entries (Pydantic exige >=3)."""
    # Conta chaves no formato entries.N.question
    entry_indices: set[str] = set()
    for key in fields.keys():
        if key.startswith("entries.") and key.endswith(".question"):
            entry_indices.add(key.split(".")[1])
    n = len(entry_indices)
    if 0 < n < 5:
        return [
            Violation(
                rule_name="faq_entries_count",
                severity="warn",
                field="entries",
                message=f"FAQ com {n} entries — ideal é 5+",
            )
        ]
    return []


# ─── registries ─────────────────────────────────────────────────────


# Regras BLOCK comuns aos dois tipos de draft (vazamento independe de schema)
RULES_BLOCK_COMMON: list[LintRule] = [
    LintRule("no_ope_codes", "block", _check_ope_codes,
             "Bloqueia códigos OPE-XXX vazados no output"),
    LintRule("no_internal_jargon", "block", _check_internal_jargon,
             "Bloqueia jargão de engenharia (bug, workaround, root cause)"),
    LintRule("no_internal_components", "block", _check_internal_components,
             "Bloqueia componentes internos (WebView, SDK Connect)"),
    LintRule("no_team_references", "block", _check_team_references,
             "Bloqueia referências internas (time interno, nosso backlog)"),
    LintRule("no_external_urls", "block", _check_external_urls,
             "Bloqueia URLs do GitBook/Confluence/atlassian"),
    LintRule("no_code_or_trace", "block", _check_code_or_trace,
             "Bloqueia blocos de código e stack traces"),
]

# Regras WARN comuns
RULES_WARN_COMMON: list[LintRule] = [
    LintRule("generic_phrases", "warn", _check_generic_phrases,
             "Sinaliza frases vagas tipo 'verifique as configurações'"),
    LintRule("field_too_short", "warn", _check_field_lengths,
             "Sinaliza campos curtos demais pra cobrir o tópico"),
    # Issue #15: feedback estrutural da chefe
    LintRule("title_too_generic", "warn", _check_title_too_generic,
             "Título não nomeia funcionalidade/módulo específico"),
    LintRule("has_report_structure", "warn", _check_has_report_structure,
             "Heading com estrutura de relatório (Sobre/Quando/Como)"),
    LintRule("scope_note_too_long", "warn", _check_scope_note_too_long,
             "scope_note longo demais (>200 chars)"),
]

RULES_ARTICLE: list[LintRule] = [
    LintRule("article_min_sections", "warn", _check_article_min_sections,
             "Artigo com menos de 3 sections (cobertura rasa)"),
]

RULES_FAQ: list[LintRule] = [
    LintRule("faq_entries_count", "warn", _check_faq_entries_count,
             "FAQ com menos de 5 entries"),
    # Issue #15: chefe pediu perguntas naturais (chat-style)
    LintRule("question_too_burocratica", "warn", _check_question_too_burocratica,
             "Pergunta burocrática ou longa demais (>80 chars)"),
]
