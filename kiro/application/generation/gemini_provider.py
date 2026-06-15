"""Implementação do LLMProvider via Google Gemini (Generative Language API)."""

import json
import logging
import re
from typing import Sequence

import httpx
from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from kiro.application.generation.base import LLMProvider
from kiro.application.generation.kb_context import format_kb_context_block
from kiro.application.generation.style_examples import format_style_examples_block
from kiro.domain.exceptions import LLMError, LLMResponseError
from kiro.domain.models import ArticleDraft, Cluster, CustomerFAQ, GitBookChunk

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```(?:json)?|```\s*$", re.MULTILINE)
_BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"})


class GeminiProvider(LLMProvider):
    """Cliente do Gemini para a interface LLMProvider.

    A URL base é a RAIZ da API (ex.: https://generativelanguage.googleapis.com/v1beta).
    O endpoint completo é montado como `{base}/models/{model}:generateContent`.
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
            raise LLMError("LLM_API_KEY vazio para o provedor Gemini.")
        if not model:
            raise LLMError("LLM_MODEL vazio para o provedor Gemini.")
        if not base_url:
            raise LLMError("LLM_BASE_URL vazio para o provedor Gemini.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout_seconds

    def generate_article(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> ArticleDraft:
        prompt = self._build_prompt(cluster, kb_context, style_examples)
        raw = self._safe_call(prompt)
        return self._parse_response(raw)

    def generate_customer_faq(
        self,
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> CustomerFAQ:
        prompt = self._build_customer_faq_prompt(cluster, kb_context, style_examples)
        raw = self._safe_call(prompt)
        return self._parse_customer_faq_response(raw)

    def _safe_call(self, prompt: str) -> str:
        """Wrapper que converte HTTPError pós-retry em LLMError tipado."""
        try:
            return self._call_api(prompt)
        except httpx.HTTPStatusError as e:
            raise LLMError(
                f"Gemini API esgotou retries (status {e.response.status_code})"
            ) from e
        except httpx.HTTPError as e:
            raise LLMError(f"Gemini API erro de rede após retries: {e}") from e

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _call_api(self, prompt: str) -> str:
        endpoint = f"{self._base_url}/models/{self._model}:generateContent"
        try:
            resp = httpx.post(
                endpoint,
                headers={
                    "content-type": "application/json",
                    "x-goog-api-key": self._api_key,
                },
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": self._temperature,
                        "maxOutputTokens": self._max_tokens,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            return self._extract_text(payload)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            # 408/429/5xx são transitórios — re-lança HTTPStatusError pra tenacity
            # ver e refazer (HTTPStatusError é subtipo de HTTPError).
            if status in (408, 429) or 500 <= status < 600:
                log.warning("Gemini HTTP %s — retentando", status)
                raise
            log.error("Gemini HTTP %s — não retentável", status)
            raise LLMError(f"Gemini API status {status}") from e
        except ValueError as e:
            raise LLMResponseError(f"resposta Gemini não é JSON: {e}") from e

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Extrai e valida o texto da resposta da API do Gemini.

        Detecta candidates ausentes (prompt bloqueado), finishReason de bloqueio
        (SAFETY/RECITATION/etc) e parts vazias.
        """
        if not isinstance(payload, dict):
            raise LLMResponseError("payload Gemini não é objeto JSON")

        candidates = payload.get("candidates") or []
        if not candidates:
            feedback = payload.get("promptFeedback") or {}
            block_reason = feedback.get("blockReason", "desconhecido")
            raise LLMResponseError(f"Gemini sem candidates (blockReason={block_reason})")

        cand = candidates[0]
        finish = cand.get("finishReason")
        if finish in _BLOCKED_FINISH_REASONS:
            raise LLMResponseError(f"Gemini bloqueou a resposta: finishReason={finish}")

        parts = (cand.get("content") or {}).get("parts") or []
        if not parts:
            raise LLMResponseError(
                f"Gemini retornou candidate sem parts (finishReason={finish})"
            )

        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not text:
            raise LLMResponseError("Gemini retornou texto vazio")
        return text

    @staticmethod
    def _build_prompt(
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> str:
        summaries = "\n".join(f"- {s}" for s in cluster.summaries) or "(nenhum)"
        labels = ", ".join(cluster.labels) or "nenhuma"
        components = ", ".join(cluster.components) or "não identificados"
        if cluster.sample_descriptions:
            descriptions_block = "\n\n".join(cluster.sample_descriptions)
        else:
            descriptions_block = (
                "(tickets sem `description` preenchida — use os títulos acima como única fonte)"
            )
        kb_block = format_kb_context_block(kb_context)
        style_block = format_style_examples_block(style_examples)
        return f"""Você está escrevendo um artigo de documentação para o varejista (cliente B2B da Kobe — Amaro, Mr. Cat, Zaffari, Epharma, etc.) ler e se auto-resolver SEM precisar abrir chamado de suporte.

═══════════════════════════════════════════════════════════════
CONTEXTO DO CLUSTER (tickets reais — USE como matéria-prima)
═══════════════════════════════════════════════════════════════

Tema do cluster: {cluster.topic}
Total de tickets recorrentes no período: {cluster.count}
Labels Jira (interno — NÃO mencione): {labels}
Componentes/módulos afetados: {components}

Títulos dos tickets de exemplo:
{summaries}

Descrições detalhadas (até 3 tickets com mais conteúdo):
─────────────────────────────────────────────────────────────
{descriptions_block}
─────────────────────────────────────────────────────────────
{kb_block}
═══════════════════════════════════════════════════════════════
PROIBIÇÕES ABSOLUTAS — vazar isso quebra a confiança do cliente
═══════════════════════════════════════════════════════════════

NUNCA mencione:
- "Causa raiz", "bug", "workaround", "regressão", "root cause" (linguagem interna)
- Códigos de ticket (OPE-XXX) — o varejista não tem acesso ao Jira
- Nomes de componentes internos da Kobe (ex: "WebView", "SDK Connect") — use termos do produto do varejista
- "O time interno", "engenharia", "nosso backlog", "sprint" — termos de quem está dentro
- Código-fonte, SQL, comandos shell, stack trace
- Estruturas tipo "Sobre este artigo", "Quando isso acontece", "Como resolver" — isso é relatório, não doc

═══════════════════════════════════════════════════════════════
ESTRUTURA EXIGIDA — siga o padrão dos artigos publicados pela Kobe
═══════════════════════════════════════════════════════════════

O título DEVE identificar a FUNCIONALIDADE ou MÓDULO específico:
  ✓ "Solução de Problemas com Push Notifications no App"
  ✓ "Configurando Cashback por Loja Física"
  ✓ "Entendendo o Modo Debug do Firebase no App"
  ✗ "Otimizando a Execução de Testes" (genérico — qual módulo?)
  ✗ "Análise de Performance e Configurações" (vago — sobre o quê?)

Depois do título, UMA frase de escopo (`scope_note`) — 1-2 frases curtas
introduzindo brevemente DE QUE TRATA o artigo. NUNCA escreva "Sobre este
artigo", "Sobre", "Visão Geral", "Introdução" — vá direto.
  ✓ "Perguntas frequentes sobre push notifications no app."
  ✓ "Como configurar e solucionar problemas comuns de cashback por loja."

Em seguida, SECTIONS temáticas. CADA section tem:
  - heading: PERGUNTA NATURAL como o cliente faria no chat ou TÓPICO específico
    ✓ "Como ativar push notifications no painel"
    ✓ "Push não está chegando no app iOS"
    ✓ "O que é preciso para ativar o Deeplink"
    ✗ "Quando isso acontece" (relatório)
    ✗ "Como resolver" (relatório)
    ✗ "Visão Geral" (preâmbulo desnecessário)
  - body: conteúdo em MARKDOWN. Pode usar:
    - listas (- item)
    - sub-headings (### Título)
    - tabelas markdown
    - alertas precedidos de `> [warning]` ou `> [info]`

Mínimo 2 sections (schema). Ideal 3-5 sections cobrindo o tópico.

DIRETRIZES POSITIVAS:
- ESCREVA COMO TUTORIAL/GUIA — "estamos te ensinando a usar".
- SEJA ACIONÁVEL. Cite caminhos REAIS ("Configurações > Integrações > X").
- DISTINGA PLATAFORMAS quando aplicável (iOS / Android).
- PERGUNTAS NATURAIS (curtas como no chat), não burocráticas ("Quais procedimentos...").
- Passos com verbo no imperativo ("Verifique...", "Acesse...", "Confirme...").
{style_block}
═══════════════════════════════════════════════════════════════
FORMATO DE RESPOSTA — JSON válido, sem markdown fences
═══════════════════════════════════════════════════════════════

{{
  "title": "Título objetivo IDENTIFICANDO funcionalidade/módulo (5-12 palavras)",
  "scope_note": "1-2 frases curtas dizendo de que trata o artigo. SEM preâmbulo.",
  "sections": [
    {{
      "heading": "Pergunta natural OU tópico específico (curto)",
      "body": "Markdown completo da section: parágrafos, listas, sub-headings, callouts."
    }},
    {{
      "heading": "...",
      "body": "..."
    }}
  ],
  "tags": ["5 a 8 tags do domínio do varejista"]
}}"""

    @staticmethod
    def _parse_response(raw: str) -> ArticleDraft:
        cleaned = _FENCE_RE.sub("", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning("Gemini retornou não-JSON; primeiros 200 chars: %r", cleaned[:200])
            raise LLMResponseError(f"resposta Gemini não é JSON válido: {e}") from e
        try:
            return ArticleDraft.model_validate(data)
        except PydanticValidationError as e:
            log.warning("JSON do Gemini falhou no schema: %s", e)
            raise LLMResponseError(f"JSON do Gemini não satisfaz o schema: {e}") from e

    @staticmethod
    def _build_customer_faq_prompt(
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> str:
        summaries = "\n".join(f"- {s}" for s in cluster.summaries) or "(nenhum)"
        labels = ", ".join(cluster.labels) or "nenhuma"
        components = ", ".join(cluster.components) or "não identificados"
        if cluster.sample_descriptions:
            descriptions_block = "\n\n".join(cluster.sample_descriptions)
        else:
            descriptions_block = (
                "(tickets sem `description` preenchida — use os títulos acima como única fonte)"
            )
        kb_block = format_kb_context_block(kb_context)
        style_block = format_style_examples_block(style_examples)
        return f"""Você está escrevendo um FAQ self-service para o time de produto/operação do varejista (cliente B2B da Kobe — Amaro, Mr. Cat, Zaffari, Epharma, etc.) consultar ANTES de abrir chamado.

═══════════════════════════════════════════════════════════════
CONTEXTO DO CLUSTER (tickets reais que viraram esta FAQ)
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
{kb_block}
═══════════════════════════════════════════════════════════════
QUEM É O LEITOR
═══════════════════════════════════════════════════════════════

Equipes de PRODUTO ou OPERAÇÃO do varejista. NÃO são desenvolvedores, mas têm:
- Acesso ao painel admin Kobe (CMS/configurações)
- Familiaridade com termos como SDK, integração, push, deeplink
- Capacidade de configurar campanhas, produtos, regras de cashback

═══════════════════════════════════════════════════════════════
ESTRUTURA EXIGIDA — FAQ simples e direta, no padrão Kobe
═══════════════════════════════════════════════════════════════

Estrutura referência (artigos publicados pela Kobe — siga este padrão):

  TÍTULO  → identifica funcionalidade ou problema específico:
    ✓ "Dúvidas sobre Push Notifications no App"
    ✓ "Solução de Problemas com Deeplink no App"
    ✗ "Análise de Performance" (genérico)

  SCOPE NOTE  → 1 frase curta dizendo do que trata. Sem preâmbulo elaborado.
    ✓ "Perguntas frequentes sobre push notifications no app."
    ✗ "Este FAQ cobre as dúvidas mais comuns das equipes de produto..." (longo demais)

  ENTRIES  → cada uma é uma PERGUNTA DIRETA + resposta acionável.
    ✓ "Como ativo push no painel?" → "Acesse Configurações > Notificações."
    ✓ "Deeplink não está abrindo o app, o que verificar?" → resposta curta.
    ✗ "Quais procedimentos devem ser seguidos para habilitação do recurso..."

REGRAS DE QUALIDADE:

1. PERGUNTAS NATURAIS — como o cliente faria no chat. Curtas, diretas. NÃO
   burocráticas. Imagine o cliente digitando no Slack do suporte.

2. RESPOSTAS ACIONÁVEIS em 2-5 frases:
   - O que verificar (painel? app? configuração?)
   - Passo a passo curto
   - O que esperar como resultado

3. `when_to_contact` (opcional, normalmente `null`):
   - Preencher SÓ se há cenário em que a auto-resolução não funciona
   - Format: "Se mesmo após verificar X, Y, Z, o problema persistir, abra ticket fornecendo: [lista do que enviar]"

4. NUNCA mencione:
   - Causa raiz interna ("é bug de WebView") — use "configuração X precisa ser revisada"
   - Códigos de ticket (OPE-XXX) — varejista não tem acesso ao Jira
   - Código-fonte, SQL, comandos shell
   - "Sobre este FAQ" / "Visão Geral" / "Introdução" — vá direto pras perguntas

5. MÍNIMO 5 entries (schema). IDEAL 7-10 — cobertura ampla, ainda direta.
{style_block}
═══════════════════════════════════════════════════════════════
FORMATO DE RESPOSTA — JSON válido, sem markdown fences
═══════════════════════════════════════════════════════════════

Responda APENAS com JSON válido, sem texto adicional:

{{
  "title": "Título identificando funcionalidade (ex: 'Dúvidas sobre Push Notifications no App')",
  "scope_note": "1 frase curta dizendo de que trata a FAQ. SEM preâmbulo.",
  "entries": [
    {{
      "question": "Pergunta natural curta como o cliente faria no chat",
      "answer": "Resposta acionável em 2-5 frases",
      "when_to_contact": "Texto opcional sobre quando escalar pra suporte, OU null"
    }}
  ],
  "tags": ["5 a 8 tags específicas"]
}}"""

    @staticmethod
    def _parse_customer_faq_response(raw: str) -> CustomerFAQ:
        cleaned = _FENCE_RE.sub("", raw).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning("Gemini retornou não-JSON; primeiros 200 chars: %r", cleaned[:200])
            raise LLMResponseError(f"resposta Gemini não é JSON válido: {e}") from e
        try:
            return CustomerFAQ.model_validate(data)
        except PydanticValidationError as e:
            log.warning("JSON do Gemini falhou no schema CustomerFAQ: %s", e)
            raise LLMResponseError(
                f"JSON do Gemini não satisfaz o schema CustomerFAQ: {e}"
            ) from e
