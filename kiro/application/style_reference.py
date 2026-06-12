"""Style reference + dedupe a partir do cache Confluence SUP (issue #10).

Reusa o motor TF-IDF do KnowledgeRetriever (issue #3) por composição —
mesmo algoritmo, semântica diferente:

- `find_similar(cluster, top_k)` → chunks pra few-shot (o LLM imita tom)
- `find_dedupe_match(cluster, threshold)` → chunk único acima de
  threshold sinalizando "existe artigo cobrindo isso"

Cache ausente degrada silenciosamente — pipeline segue sem few-shot e
sem dedupe (decisão consistente com a issue #3).
"""

import logging
from pathlib import Path
from typing import Optional

from kiro.application.retrieval import KnowledgeRetriever
from kiro.domain.models import Cluster, GitBookChunk

log = logging.getLogger(__name__)


class StyleReferenceFinder:
    """Encapsula busca de exemplos de estilo + dedupe sobre cache SUP."""

    def __init__(self, cache_path: Path) -> None:
        self._retriever = KnowledgeRetriever(cache_path)

    @property
    def is_ready(self) -> bool:
        """True se há chunks indexados. False quando cache ausente/vazio."""
        return self._retriever.is_ready

    @property
    def chunk_count(self) -> int:
        return self._retriever.chunk_count

    def find_similar(
        self,
        cluster: Cluster,
        top_k: int = 2,
        min_score: float = 0.1,
    ) -> list[GitBookChunk]:
        """Top-k chunks mais similares ao cluster — usados como few-shot.

        Vazio quando: cache ausente, sem matches acima do threshold,
        ou query do cluster sem tokens válidos. Caller trata como
        "sem exemplos de estilo" (prompt segue sem o bloco).
        """
        return self._retriever.find_relevant(
            cluster, top_k=top_k, min_score=min_score
        )

    def find_dedupe_match(
        self,
        cluster: Cluster,
        threshold: float = 0.6,
    ) -> Optional[GitBookChunk]:
        """Chunk único com maior score se passar do threshold; senão None.

        Pra dedupe queremos sinal binário: "existe artigo cobrindo isso?".
        Um chunk representativo basta — não precisa retornar lista.
        Threshold default 0.6 = afinidade forte; abaixo disso o sinal
        é fraco demais pra justificar reportar como "match existente".
        """
        matches = self._retriever.find_relevant(
            cluster, top_k=1, min_score=threshold
        )
        return matches[0] if matches else None


def build_style_finder(cache_path: Path) -> Optional[StyleReferenceFinder]:
    """Helper pra construção via CLI/pipeline. None se cache ausente.

    Mantém uniforme o tratamento de "few-shot ligado mas sem cache" no
    chamador: `if finder is None: ...` (mesmo padrão de build_retriever).
    """
    finder = StyleReferenceFinder(cache_path)
    return finder if finder.is_ready else None
