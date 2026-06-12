"""Retrieval de chunks relevantes do cache GitBook por TF-IDF local.

Por que TF-IDF local e não embeddings: ~1k–2k chunks no cache. Embeddings
exigem dependência pesada (transformers + modelo) e/ou chamada externa por
query — overkill pro tamanho do corpus e contraria o objetivo de ser
grounding interno barato.

Quando o cache não existe (ex.: usuário ligou ENABLE_GITBOOK_RAG=true sem
ter rodado `kiro fetch-gitbook --public`), o retriever degrada
silenciosamente: `find_relevant` retorna lista vazia. Decisão consciente
pra NÃO derrubar o pipeline — a issue #3 exige isso explicitamente.
"""

import json
import logging
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional

from kiro.domain.models import Cluster, GitBookChunk

log = logging.getLogger(__name__)


# Stopwords pt-BR mínimas — só as palavras vazias mais frequentes. Listas
# extensas (NLTK etc.) viriam com dependência pesada e ganho marginal pro
# tamanho do corpus.
_STOPWORDS_PT: frozenset[str] = frozenset(
    {
        "a", "o", "e", "de", "do", "da", "em", "para", "por", "com", "que",
        "na", "no", "um", "uma", "os", "as", "dos", "das", "se", "ao",
        "aos", "à", "às", "ou", "mais", "como", "ser", "ter", "está",
        "esta", "este", "isso", "isto", "nas", "nos", "pelo", "pela",
        "pelos", "pelas",
    }
)


def _tokenize(text: str) -> list[str]:
    """Tokenização simples: minúsculas, sem acentos, split por não-alfanumérico.

    Stopwords pt-BR e tokens com 1-2 caracteres são descartados — viram
    ruído no TF-IDF pra corpus pequeno.
    """
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    raw_tokens = re.split(r"[^a-z0-9]+", lowered)
    return [t for t in raw_tokens if len(t) >= 3 and t not in _STOPWORDS_PT]


class KnowledgeRetriever:
    """Indexa chunks do cache GitBook e devolve os top-k mais relevantes ao cluster.

    Index é construído UMA vez no __init__ — assume cache imutável durante
    a execução do pipeline. Pra recarregar, instancie de novo.
    """

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = Path(cache_path)
        self._chunks: list[GitBookChunk] = []
        # Vetores tf-idf L2-normalizados, um por chunk, alinhados com `_chunks`.
        # Sparse dict {token: weight} pra evitar matriz densa cheia de zeros.
        self._doc_vectors: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._load_and_index()

    @property
    def is_ready(self) -> bool:
        """True se há chunks indexados. False quando cache não foi encontrado/é vazio."""
        return bool(self._chunks)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def find_relevant(
        self,
        cluster: Cluster,
        top_k: int = 3,
        min_score: float = 0.1,
    ) -> list[GitBookChunk]:
        """Retorna até `top_k` chunks com cosine similarity >= `min_score`.

        Lista vazia significa: cache ausente, query sem tokens válidos, ou
        nenhum chunk passou do threshold. Em todos os casos o chamador trata
        como "sem contexto extra" (prompt vai sem bloco de referência).
        """
        if not self.is_ready:
            return []

        query_text = self._build_query(cluster)
        query_vector = self._vectorize_query(query_text)
        if not query_vector:
            return []

        scored: list[tuple[float, int]] = []
        for idx, doc_vec in enumerate(self._doc_vectors):
            score = _cosine(query_vector, doc_vec)
            if score >= min_score:
                scored.append((score, idx))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._chunks[idx] for _, idx in scored[:top_k]]

    # ───────────────────────── internals ─────────────────────────

    @staticmethod
    def _build_query(cluster: Cluster) -> str:
        """Concatena topic + labels + components — sinais semânticos do cluster.

        A issue #3 menciona "topic + tags". `Cluster` não tem `tags`; `labels`
        (Jira labels) e `components` (Jira components) são os campos
        equivalentes e ambos são tokenizáveis em palavras de domínio.
        """
        parts = [cluster.topic]
        if cluster.labels:
            parts.extend(cluster.labels)
        if cluster.components:
            parts.extend(cluster.components)
        return " ".join(parts)

    def _vectorize_query(self, text: str) -> dict[str, float]:
        """Constrói tf-idf vector L2-normalized da query, reusando idf do corpus."""
        tokens = _tokenize(text)
        if not tokens:
            return {}
        tf = Counter(tokens)
        length = len(tokens)
        # Termos da query que não aparecem em nenhum doc do corpus têm idf=0
        # (ignorados); o resto recebe peso tf*idf e depois L2-normalize.
        vector: dict[str, float] = {}
        for term, count in tf.items():
            idf = self._idf.get(term, 0.0)
            if idf > 0:
                vector[term] = (count / length) * idf
        return _l2_normalize(vector)

    def _load_and_index(self) -> None:
        """Carrega cache JSON e calcula idf + vetores tf-idf normalizados.

        Falhas (arquivo ausente, JSON quebrado, formato inesperado) viram
        warning e estado "vazio" — pipeline continua sem RAG.
        """
        if not self._cache_path.exists():
            log.warning(
                "GitBook cache não encontrado em %s — retrieval desabilitado. "
                "Rode `kiro fetch-gitbook --public` pra gerar o cache.",
                self._cache_path,
            )
            return

        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning(
                "Falha lendo GitBook cache %s: %s — retrieval desabilitado.",
                self._cache_path, e,
            )
            return

        raw_chunks = payload.get("chunks") if isinstance(payload, dict) else None
        if not raw_chunks:
            log.warning(
                "GitBook cache %s sem chunks — retrieval desabilitado.",
                self._cache_path,
            )
            return

        # Materializa GitBookChunks (descartando entradas malformadas)
        for raw in raw_chunks:
            try:
                self._chunks.append(
                    GitBookChunk(
                        page_title=raw["page_title"],
                        page_url=raw["page_url"],
                        section_title=raw["section_title"],
                        section_anchor=raw["section_anchor"],
                        content=raw["content"],
                    )
                )
            except (KeyError, TypeError):
                continue

        if not self._chunks:
            log.warning("GitBook cache não produziu chunks válidos — retrieval desabilitado.")
            return

        self._build_index()

    def _build_index(self) -> None:
        """TF-IDF clássico com smoothing: idf(t) = log((N+1)/(df+1)) + 1.

        Cada doc vira dict sparse {token: tf * idf} e é L2-normalizado pra
        que cosine seja dot-product direto no find_relevant.
        """
        n_docs = len(self._chunks)

        # Tokeniza uma vez por chunk; reusa nos cálculos de tf e df
        token_lists: list[list[str]] = [_tokenize(c.content) for c in self._chunks]

        # df (document frequency) por termo
        df: Counter[str] = Counter()
        for tokens in token_lists:
            for term in set(tokens):
                df[term] += 1

        # idf smoothed — evita div/zero e suaviza termos universais
        self._idf = {
            term: math.log((n_docs + 1) / (count + 1)) + 1
            for term, count in df.items()
        }

        # Vetores tf-idf por doc, L2-normalizados
        for tokens in token_lists:
            if not tokens:
                self._doc_vectors.append({})
                continue
            tf = Counter(tokens)
            length = len(tokens)
            vec = {term: (count / length) * self._idf[term] for term, count in tf.items()}
            self._doc_vectors.append(_l2_normalize(vec))


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    """Divide cada peso pela norma L2. Retorna {} se norma == 0."""
    norm = math.sqrt(sum(v * v for v in vector.values()))
    if norm == 0:
        return {}
    return {k: v / norm for k, v in vector.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Dot product de dois vetores sparse — assume ambos já L2-normalized."""
    # Itera no menor pra cortar trabalho quando query é muito mais curta que doc
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(term, 0.0) for term, weight in a.items())


def build_retriever(cache_path: Path) -> Optional[KnowledgeRetriever]:
    """Helper pra construção via CLI/pipeline. Retorna None se cache ausente.

    Mantém uniforme o tratamento de "RAG ligado mas sem cache" no chamador:
    `if retriever is None: ...`.
    """
    retriever = KnowledgeRetriever(cache_path)
    return retriever if retriever.is_ready else None
