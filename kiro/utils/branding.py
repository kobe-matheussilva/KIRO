"""Marca KIRO + Kobe. Usado pra dar identidade visual ao CLI e aos artefatos."""

import sys
from typing import Optional

from kiro import __version__

SLOGAN = "your mobile way of presence"
COMPANY = "kobe"
SIGNATURE = f"KIRO {__version__}  ·  {SLOGAN}  —  {COMPANY}"

_BANNER = r"""
 ██╗  ██╗ ██╗ ██████╗   ██████╗
 ██║ ██╔╝ ██║ ██╔══██╗ ██╔═══██╗
 █████╔╝  ██║ ██████╔╝ ██║   ██║
 ██╔═██╗  ██║ ██╔══██╗ ██║   ██║
 ██║  ██╗ ██║ ██║  ██║ ╚██████╔╝
 ╚═╝  ╚═╝ ╚═╝ ╚═╝  ╚═╝  ╚═════╝

         knowledge inferred from recurring tickets
"""


def print_banner() -> None:
    """Banner de abertura. Mostrado no início de cada `kiro run`."""
    print(_BANNER)
    print(f"   {SIGNATURE}")
    print(f"   {'─' * len(SIGNATURE)}")
    print()
    sys.stdout.flush()


def print_footer(
    *,
    tickets: int,
    clusters: int,
    articles: int,
    customer_faqs: int = 0,
    published: int,
    errors: int,
    duration_seconds: float,
    artifacts_dir: str,
    dedupe_matches: Optional[list] = None,
) -> None:
    """Resumo final humanizado. Mostrado no fim de cada `kiro run`.

    `dedupe_matches` é opcional: lista de `(Cluster, GitBookChunk)` com
    matches do SUP. Quando presente, é exibido como hint pro revisor
    "considere atualizar artigo existente" — política firmada na issue #10.
    """
    bar = "─" * 50
    print()
    print(f"   {bar}")
    print(f"   Resumo da rodada")
    print(f"   {bar}")
    print(f"     tickets coletados       : {tickets:>5}")
    print(f"     clusters detectados     : {clusters:>5}")
    print(f"     Artigos gerados pela IA : {articles:>5}")
    print(f"     FAQs gerados pela IA    : {customer_faqs:>5}")
    print(f"     publicados no Confluence: {published:>5}")
    print(f"     falhas                  : {errors:>5}")
    print(f"     duração                 : {duration_seconds:>5.1f}s")
    print(f"   {bar}")
    print()
    if dedupe_matches:
        print(f"   ⚠ {len(dedupe_matches)} cluster(s) com artigo similar em SUP — "
              "considere atualizar em vez de criar novo:")
        for cluster, chunk in dedupe_matches:
            print(f"     • '{cluster.topic[:50]}' ↔ '{chunk.page_title[:60]}'")
        print()
    print(f"   artefatos em: {artifacts_dir}")
    print()
    print(f"   ✨ {SIGNATURE} ✨")
    print()
    sys.stdout.flush()


MARKDOWN_FOOTER = f"\n---\n\n_{SIGNATURE}_\n"


CONFLUENCE_FOOTER = (
    "<hr/>"
    f"<p><em>{SIGNATURE}</em></p>"
)
