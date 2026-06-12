"""Mock provider — não chama API real. Usado em dry-run para demo sem custo."""

import logging
from typing import Sequence

from kiro.application.generation.base import LLMProvider
from kiro.domain.models import (
    ArticleDraft,
    Cluster,
    CustomerFAQ,
    FAQEntry,
    FAQItem,
    GitBookChunk,
)

log = logging.getLogger(__name__)


class MockLLMProvider(LLMProvider):
    """Retorna drafts determinísticos baseados no próprio cluster.

    Nenhuma chamada externa. Garante que `--dry-run` não consuma quota da API real
    e que a demo local seja reprodutível e gratuita.

    Aceita `kb_context` por contrato da interface, mas IGNORA — o output mock é
    derivado só do cluster, sem injeção de chunks.
    """

    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        log.info(
            "MOCK LLM: gerando draft KB interno para cluster '%s' (%d tickets)",
            cluster.topic,
            cluster.count,
        )
        sample = cluster.summaries[0] if cluster.summaries else cluster.topic
        components = ", ".join(cluster.components) or "não identificados"
        tags = cluster.labels[:5] if cluster.labels else [cluster.topic.split()[0].lower() or "geral"]
        return ArticleDraft(
            title=f"[DRY-RUN] {cluster.topic}",
            problem=(
                f"Foram identificados {cluster.count} tickets recorrentes sobre "
                f"'{cluster.topic}'. Sintoma típico relatado: {sample}."
            ),
            cause=(
                "Causa preliminar não confirmada — artigo gerado em modo dry-run, "
                f"sem consulta ao LLM real. Componentes afetados: {components}."
            ),
            solution=(
                "1. Reproduzir o problema em ambiente controlado\n"
                "2. Identificar a causa raiz a partir dos logs e dos tickets de origem\n"
                "3. Aplicar correção e validar com os tickets associados\n"
                "4. Documentar o procedimento neste artigo e publicar"
            ),
            faq=[
                FAQItem(
                    question=f"Como reconhecer o problema relacionado a '{cluster.topic}'?",
                    answer=(
                        f"Os tickets de origem ({cluster.count} ao todo) reportam sintomas "
                        f"semelhantes a: {sample}."
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
            title=f"[DRY-RUN] FAQ — {cluster.topic}",
            intro=(
                f"Este FAQ cobre dúvidas frequentes do time de produto/operação do "
                f"varejista sobre '{cluster.topic}'. Foi gerado em modo dry-run a partir "
                f"de {cluster.count} tickets recorrentes."
            ),
            entries=[
                FAQEntry(
                    question=f"O que devo saber sobre '{cluster.topic}'?",
                    answer=(
                        f"Este é um tema recorrente no suporte ({cluster.count} ocorrências). "
                        f"Exemplo de sintoma reportado: {sample}."
                    ),
                    when_to_contact=None,
                ),
                FAQEntry(
                    question="Quando devo abrir um ticket de suporte sobre isso?",
                    answer=(
                        "Após verificar as configurações no painel admin Kobe e ainda "
                        "assim o problema persistir."
                    ),
                    when_to_contact=(
                        "Abra um ticket fornecendo: print da tela com o problema, "
                        "horário aproximado da ocorrência e identificador do varejista."
                    ),
                ),
                FAQEntry(
                    question="Onde encontro a configuração relacionada no painel?",
                    answer=(
                        "(Conteúdo simulado em modo dry-run — produção utilizará a IA real "
                        "para gerar resposta específica baseada nas descrições dos tickets.)"
                    ),
                    when_to_contact=None,
                ),
            ],
            tags=tags,
        )
