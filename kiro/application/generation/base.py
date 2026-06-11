"""Interface plugável para provedores de LLM."""

from abc import ABC, abstractmethod

from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ


class LLMProvider(ABC):
    @abstractmethod
    def generate_article(self, cluster: Cluster) -> ArticleDraft:
        """Gera um KB interno (problema/causa/solução) a partir de um cluster.

        Audiência: time de suporte Kobe. Tom técnico-diagnóstico.
        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...

    @abstractmethod
    def generate_customer_faq(self, cluster: Cluster) -> CustomerFAQ:
        """Gera um FAQ self-service para varejistas B2B a partir do mesmo cluster.

        Audiência: produto/operação do varejista (Amaro, Mr.Cat, Zaffari, Epharma).
        Tom: direto, instrucional, sem jargão de engenharia.
        Deve lançar LLMError/LLMResponseError em caso de falha.
        """
        ...
