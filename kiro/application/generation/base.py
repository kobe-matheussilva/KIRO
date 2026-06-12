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
    ) -> ArticleDraft:
        """Gera um KB interno (problema/causa/solução) a partir de um cluster.

        Audiência: time de suporte Kobe. Tom técnico-diagnóstico.
        Quando `kb_context` for não-vazio, os chunks são injetados no prompt
        como referência factual interna (ver `kb_context.format_kb_context_block`).
        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...

    @abstractmethod
    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        """Gera um FAQ self-service para varejistas B2B a partir do mesmo cluster.

        Audiência: produto/operação do varejista (Amaro, Mr.Cat, Zaffari, Epharma).
        Tom: direto, instrucional, sem jargão de engenharia.
        Quando `kb_context` for não-vazio, injeta chunks como referência interna.
        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...
