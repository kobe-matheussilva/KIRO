"""Mock provider — não chama API real. Usado em dry-run para demo sem custo."""

import logging
from typing import Sequence

from kiro.application.generation.base import LLMProvider
from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    GitBookChunk,
    Section,
)

log = logging.getLogger(__name__)


class MockLLMProvider(LLMProvider):
    """Retorna drafts determinísticos baseados no próprio cluster.

    Nenhuma chamada externa. Garante que `--dry-run` não consuma quota da API real
    e que a demo local seja reprodutível e gratuita.

    Aceita `kb_context` / `style_examples` por contrato da interface, mas IGNORA —
    o output mock é derivado só do cluster, sem injeção de chunks.
    """

    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        log.info(
            "MOCK LLM: gerando ArticleDraft para cluster '%s' (%d tickets)",
            cluster.topic,
            cluster.count,
        )
        sample = cluster.summaries[0] if cluster.summaries else cluster.topic
        tags = cluster.labels[:5] if cluster.labels else [
            cluster.topic.split()[0].lower() or "geral"
        ]
        return ArticleDraft(
            title=f"[DRY-RUN] Solução para {cluster.topic}",
            scope_note=(
                f"Conteúdo simulado em modo dry-run para o tema '{cluster.topic}' "
                f"({cluster.count} tickets recorrentes no período)."
            ),
            sections=[
                Section(
                    heading=f"Como verificar a configuração de {cluster.topic}",
                    body=(
                        f"Conteúdo simulado em modo dry-run. Sintoma típico reportado: "
                        f"{sample}.\n\n"
                        "- Verifique o painel admin Kobe\n"
                        "- Confirme permissões da equipe responsável\n"
                        "- Teste em ambiente de homologação"
                    ),
                ),
                Section(
                    heading=f"O que fazer se {cluster.topic} continua apresentando o comportamento",
                    body=(
                        "Conteúdo simulado em modo dry-run — produção utilizará a IA real para "
                        "gerar passos específicos baseados nas descrições dos tickets."
                    ),
                ),
            ],
            tags=tags,
        )

    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        log.info(
            "MOCK LLM: gerando FAQ B2B para cluster '%s' (%d tickets)",
            cluster.topic,
            cluster.count,
        )
        sample = cluster.summaries[0] if cluster.summaries else cluster.topic
        tags = cluster.labels[:5] if cluster.labels else [
            cluster.topic.split()[0].lower() or "geral"
        ]
        return CustomerFAQ(
            title=f"[DRY-RUN] Dúvidas sobre {cluster.topic}",
            scope_note=(
                f"Perguntas frequentes sobre '{cluster.topic}' "
                f"({cluster.count} tickets recorrentes)."
            ),
            entries=[
                FAQEntry(
                    question=f"O que devo saber sobre {cluster.topic}?",
                    answer=(
                        f"Tema recorrente no suporte ({cluster.count} ocorrências). "
                        f"Exemplo de sintoma: {sample}."
                    ),
                    when_to_contact=None,
                ),
                FAQEntry(
                    question="Como verifico se está configurado corretamente?",
                    answer=(
                        "Verifique no painel admin Kobe se a configuração está ativa "
                        "e as permissões liberadas para a equipe responsável."
                    ),
                    when_to_contact=None,
                ),
                FAQEntry(
                    question="O que fazer se mesmo configurado não funcionar?",
                    answer=(
                        "Tente reproduzir em ambiente de homologação e registre o "
                        "horário exato da ocorrência."
                    ),
                    when_to_contact=(
                        "Abra ticket fornecendo: print da tela, horário aproximado da "
                        "ocorrência e identificador do varejista."
                    ),
                ),
            ],
            tags=tags,
        )
