"""Modelos de domínio. Imutáveis sempre que possível."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Ticket(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    summary: str
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    status: Optional[str] = None
    resolved_at: Optional[datetime] = None

    @property
    def text(self) -> str:
        return f"{self.summary} {self.description}".strip()


class Cluster(BaseModel):
    topic: str
    tickets: list[str]
    summaries: list[str]
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    # Excerto da `description` dos top N tickets (key prefixado) — dá ao LLM
    # contexto narrativo dos casos reais, não só os títulos curtos.
    sample_descriptions: list[str] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.tickets)


class FAQItem(BaseModel):
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class ArticleDraft(BaseModel):
    """Artigo de Base de Conhecimento INTERNO (público: time de suporte Kobe).

    Estrutura diagnóstica — problema/causa/solução/FAQ. Tom técnico,
    pode mencionar root cause e workarounds internos.
    """

    title: str = Field(..., min_length=1)
    problem: str = Field(..., min_length=1)
    cause: str = Field(..., min_length=1)
    solution: str = Field(..., min_length=1)
    faq: list[FAQItem] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class FAQEntry(BaseModel):
    """Uma entrada de FAQ self-service voltada ao varejista B2B.

    Diferente de FAQItem (que vive dentro de ArticleDraft do KB interno),
    esta tem `when_to_contact` opcional indicando quando escalar para suporte.
    """

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    when_to_contact: Optional[str] = None


class CustomerFAQ(BaseModel):
    """Documento FAQ self-service para varejistas B2B clientes da Kobe.

    Audiência: equipes de produto/operação de Amaro, Mr.Cat, Zaffari, Epharma, etc.
    Tom: direto, instrucional, sem jargão de engenharia. Pode citar "no painel admin",
    "via SDK", "na seção X da integração".

    Gerado em paralelo ao ArticleDraft a partir do MESMO cluster — atende a chefe
    que pediu "documentos pro cliente ler sem precisar abrir chamado".
    """

    title: str = Field(..., min_length=1)
    intro: str = Field(..., min_length=1)
    entries: list[FAQEntry] = Field(..., min_length=3)
    tags: list[str] = Field(default_factory=list)


class PublishResult(BaseModel):
    cluster_topic: str
    article_title: str
    ticket_count: int
    confluence_url: Optional[str] = None
    local_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
