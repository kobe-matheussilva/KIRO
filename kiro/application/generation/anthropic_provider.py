"""Implementação do LLMProvider via Anthropic Messages API."""

import json
import logging
import re

import httpx
from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kiro.application.generation.base import LLMProvider
from kiro.domain.exceptions import LLMError, LLMResponseError
from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)


class AnthropicProvider(LLMProvider):
    """Cliente Anthropic para a interface LLMProvider.

    A URL base é a RAIZ da API (ex.: https://api.anthropic.com/v1).
    O endpoint final é montado como `{base}/messages`.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        max_tokens: int = 1500,
        temperature: float = 0.3,
        timeout_seconds: int = 60,
    ) -> None:
        if not api_key:
            raise LLMError("LLM_API_KEY vazio para o provedor Anthropic.")
        if not model:
            raise LLMError("LLM_MODEL vazio para o provedor Anthropic.")
        if not base_url:
            raise LLMError("LLM_BASE_URL vazio para o provedor Anthropic.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout_seconds

    def generate_article(self, cluster: Cluster) -> ArticleDraft:
        prompt = self._build_prompt(cluster)
        raw = self._safe_call(prompt)
        return self._parse_response(raw)

    def generate_customer_faq(self, cluster: Cluster) -> CustomerFAQ:
        prompt = self._build_customer_faq_prompt(cluster)
        raw = self._safe_call(prompt)
        return self._parse_customer_faq_response(raw)

    def _safe_call(self, prompt: str) -> str:
        try:
            return self._call_api(prompt)
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Anthropic API esgotou retries (status {e.response.status_code})"
            ) from e
        except httpx.HTTPError as e:
            raise LLMError(f"Anthropic API erro de rede após retries: {e}") from e

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _call_api(self, prompt: str) -> str:
        endpoint = f"{self._base_url}/messages"
        try:
            resp = httpx.post(
                endpoint,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "temperature": self._temperature,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            return payload["content"][0]["text"].strip()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (408, 429) or 500 <= status < 600:
                log.warning("Anthropic HTTP %s — retentando", status)
                raise
            log.error("Anthropic HTTP %s — não retentável", status)
            raise LLMError(f"Anthropic API status {status}") from e
        except (KeyError, IndexError, ValueError) as e:
            raise LLMResponseError(f"resposta Anthropic em formato inesperado: {e}") from e

    @staticmethod
    def _build_prompt(cluster: Cluster) -> str:
        summaries = "\n".join(f"- {s}" for s in cluster.summaries) or "(nenhum)"
        labels = ", ".join(cluster.labels) or "nenhuma"
        components = ", ".join(cluster.components) or "não identificados"
        if cluster.sample_descriptions:
            descriptions_block = "\n\n".join(cluster.sample_descriptions)
        else:
            descriptions_block = (
                "(tickets sem `description` preenchida — use os títulos acima como única fonte)"
            )
        return f"""Você é um especialista em documentação técnica de suporte ao cliente da Kobe — empresa que desenvolve aplicativos móveis (iOS e Android) para grandes varejistas brasileiros (ex.: Amaro, Mr. Cat, Zaffari, Epharma).

Sua tarefa: produzir um artigo de Base de Conhecimento **acertivo, específico e acionável**, em português do Brasil, a partir de tickets reais de suporte agrupados por similaridade.

═══════════════════════════════════════════════════════════════
CONTEXTO DO CLUSTER
═══════════════════════════════════════════════════════════════

Tema identificado: {cluster.topic}
Total de tickets recorrentes no período: {cluster.count}
Labels Jira aplicadas: {labels}
Componentes/módulos afetados: {components}

Títulos dos tickets de exemplo:
{summaries}

Descrições detalhadas (até 3 tickets com mais conteúdo):
─────────────────────────────────────────────────────────────
{descriptions_block}
─────────────────────────────────────────────────────────────

═══════════════════════════════════════════════════════════════
DIRETRIZES OBRIGATÓRIAS — leia antes de escrever
═══════════════════════════════════════════════════════════════

1. SEJA ESPECÍFICO. Cite mensagens de erro reais, nomes de telas/campos, fluxos
   e plataformas (iOS/Android) que aparecem nas descrições. Evite frases vagas.

2. NÃO use bullets genéricos como "verifique as configurações", "limpe o cache"
   sem dizer EXATAMENTE o quê verificar/limpar e em qual menu.

3. NÃO INVENTE causa. Se as descrições não dão pista da raiz, escreva:
   "Causa a investigar" + 2-3 hipóteses concretas baseadas no padrão observado.

4. DISTINGA PLATAFORMAS quando aplicável: se um problema só aparece em iOS,
   diga "Em iOS:" antes do passo. Mesmo pra Android. Se atinge os dois, separe.

5. A FAQ deve antecipar dúvidas REAIS dos clientes/atendentes baseado nos
   tickets — perguntas que apareceram nas descrições. Evite perguntas genéricas
   tipo "o que é deeplink".

6. Cada passo da solução deve ser ACIONÁVEL: começa com verbo no imperativo
   ("Verifique...", "Abra...", "Limpe..."), menciona caminhos (Configurações →
   X → Y) ou comandos quando aplicável. Mínimo 4 passos, ideal 5-8.

7. O cliente da Kobe é tipicamente um **varejista** — fala numa linguagem
   que faz sentido pra equipe de suporte de e-commerce/PDV, não pra usuário leigo.

═══════════════════════════════════════════════════════════════
FORMATO DE RESPOSTA
═══════════════════════════════════════════════════════════════

Responda APENAS com JSON válido, sem markdown, sem texto adicional. Estrutura:

{{
  "title": "Título objetivo de 5-12 palavras",
  "problem": "Descrição do problema da perspectiva do cliente, 2-4 frases. Mencione sintomas específicos vistos nas descrições.",
  "cause": "Causa raiz mais provável, baseada nas descrições. Se incerta, comece com 'Causa a investigar' e liste hipóteses. 2-4 frases.",
  "solution": "Passos numerados separados por \\n. 4-8 passos acionáveis.",
  "faq": [
    {{"question": "Pergunta real que cliente/atendente faria", "answer": "Resposta direta e específica"}},
    {{"question": "...", "answer": "..."}},
    {{"question": "...", "answer": "..."}}
  ],
  "tags": ["5 a 8 tags específicas, sem genéricos"]
}}"""

    @staticmethod
    def _parse_response(raw: str) -> ArticleDraft:
        cleaned = _FENCE_RE.sub("", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning("Anthropic retornou não-JSON; primeiros 200 chars: %r", cleaned[:200])
            raise LLMResponseError(f"resposta Anthropic não é JSON válido: {e}") from e
        try:
            return ArticleDraft.model_validate(data)
        except PydanticValidationError as e:
            log.warning("JSON Anthropic falhou no schema: %s", e)
            raise LLMResponseError(f"JSON Anthropic não satisfaz o schema: {e}") from e

    @staticmethod
    def _build_customer_faq_prompt(cluster: Cluster) -> str:
        # Mesmo prompt do Gemini — o conteúdo é agnostic de provedor.
        # Importamos lazy pra evitar dependência circular sutil entre os arquivos.
        from kiro.application.generation.gemini_provider import GeminiProvider
        return GeminiProvider._build_customer_faq_prompt(cluster)

    @staticmethod
    def _parse_customer_faq_response(raw: str) -> CustomerFAQ:
        cleaned = _FENCE_RE.sub("", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning("Anthropic retornou não-JSON; primeiros 200 chars: %r", cleaned[:200])
            raise LLMResponseError(f"resposta Anthropic não é JSON válido: {e}") from e
        try:
            return CustomerFAQ.model_validate(data)
        except PydanticValidationError as e:
            log.warning("JSON Anthropic falhou no schema CustomerFAQ: %s", e)
            raise LLMResponseError(
                f"JSON Anthropic não satisfaz o schema CustomerFAQ: {e}"
            ) from e
