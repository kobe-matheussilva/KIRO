"""Interface plugável para provedores de LLM."""

from abc import ABC, abstractmethod
from typing import Sequence

from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, GitBookChunk


class LLMProvider(ABC):
    @abstractmethod
    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        """Gera um KB interno (problema/causa/solução) a partir de um cluster.

        Audiência: time de suporte Kobe. Tom técnico-diagnóstico.

        `kb_context` (issue #3) = grounding factual, injetado entre
        contexto do cluster e diretrizes.
        `style_examples` (issue #10) = exemplos de tom/estrutura, injetado
        entre diretrizes e formato. Não confundir os dois — semânticas
        e posições no prompt diferentes.

        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...

    @abstractmethod
    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        """Gera um FAQ self-service para varejistas B2B a partir do mesmo cluster.

        Audiência: produto/operação do varejista (Amaro, Mr.Cat, Zaffari, Epharma).
        Tom: direto, instrucional, sem jargão de engenharia.

        Mesmos parâmetros opcionais que `generate_article`.
        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...

    @abstractmethod
    def validate_proactive_signal(
        self,
        *,
        candidate_type: str,
        candidate_name: str,
        heuristic_score: float,
        rationale: str,
        tickets_context: list[dict[str, str]],
    ) -> dict[str, str]:
        """Triangula um sinal proativo com IA e retorna validação estruturada.

        Retorno esperado:
        - validation_decision: confirma | parcial | descarta
        - confidence: baixa | media | alta
        - status_summary: resumo executivo curto
        - key_problems: principais problemas percebidos
        - recommended_action: próxima ação recomendada
        """
        ...
