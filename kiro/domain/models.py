"""Modelos de domínio. Imutáveis sempre que possível."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Section(BaseModel):
    """Section de Artigo no padrão SUP (issue #15).

    `heading` deve nomear uma pergunta natural ou tópico específico ("Como
    ativar X", "Y não está funcionando"). NÃO é estrutura de relatório
    ("Sobre este artigo", "Visão Geral", "Como resolver").

    `body` é markdown — pode incluir listas, sub-headings (###), tabelas,
    callouts `> [warning]` / `> [info]`.
    """

    model_config = ConfigDict(frozen=True)

    heading: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class ArticleDraft(BaseModel):
    """Artigo de documentação para o cliente B2B da Kobe (issue #15).

    Estrutura inspirada nos artigos publicados pelo Suporte da Kobe no
    Confluence space SUP — exemplo de referência: "Solução de Problemas
    com Deeplink no App" (page 87588885).

    - `title`: nomeia a funcionalidade ou módulo (NÃO genérico).
       Bom: "Solução de Problemas com Push Notifications no App".
       Ruim: "Otimizando a Execução de Testes no Aplicativo".
    - `scope_note`: 1-2 frases dizendo de que trata o artigo. Sem
       preâmbulos tipo "Sobre este artigo" — vai direto.
    - `sections`: sequência de tópicos. Cada section é um H2 com perguntas
       naturais ou tópicos específicos como heading. Mínimo 2, ideal 3-5.
    """

    title: str = Field(..., min_length=1)
    scope_note: str = Field(..., min_length=1)
    sections: list[Section] = Field(..., min_length=2)
    tags: list[str] = Field(default_factory=list)


class FAQEntry(BaseModel):
    """Uma entrada de FAQ self-service voltada ao varejista B2B.

    `when_to_contact` opcional indica quando escalar para suporte —
    preencher só se a auto-resolução não cobre todos os cenários.
    """

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    when_to_contact: Optional[str] = None

    @field_validator("when_to_contact", mode="before")
    @classmethod
    def _normalize_null_strings(cls, v: object) -> Optional[str]:
        """Gemini às vezes retorna a string literal 'null' em vez de JSON null.
        Normaliza para None pra evitar renderizar 'null' nos artefatos."""
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a"):
            return None
        return v if isinstance(v, str) else None


class CustomerFAQ(BaseModel):
    """FAQ self-service para o cliente B2B da Kobe (issue #15).

    Estrutura simplificada após feedback da chefe (2026-06-14): cliente
    no chat faz perguntas DIRECIONADAS. FAQ deve refletir isso — sem
    preâmbulo elaborado, direto pras Q&As.

    - `title`: identifica funcionalidade ("Dúvidas sobre Cashback por Loja"
       ou "Solução de Problemas com Push Notifications").
    - `scope_note`: 1 frase curta de escopo (substitui o `intro` longo
       da V1 que parecia abertura de relatório).
    - `entries`: mínimo 3 (Pydantic enforça); recomendado 5+.
    """

    title: str = Field(..., min_length=1)
    scope_note: str = Field(..., min_length=1)
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


class GitBookChunk(BaseModel):
    """Um pedaço de uma página do GitBook, indexado por seção.

    O `char_count` é derivado de content (via property) — pré-calculado
    em runtime pra evitar recontagem no retrieval da issue #3.
    """

    model_config = ConfigDict(frozen=True)

    page_title: str
    page_url: str
    section_title: str
    section_anchor: str
    content: str

    @property
    def char_count(self) -> int:
        return len(self.content)


class ScrapingResult(BaseModel):
    """Resumo de uma execução do scraper."""

    model_config = ConfigDict(frozen=True)

    pages_fetched: int
    chunks_written: int
    failed_urls: list[str]
    output_path: Path
