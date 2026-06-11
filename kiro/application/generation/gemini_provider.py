"""Implementação do LLMProvider via Google Gemini (Generative Language API)."""

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

    def generate_article(self, cluster: Cluster) -> ArticleDraft:
        prompt = self._build_prompt(cluster)
        raw = self._safe_call(prompt)
        return self._parse_response(raw)

    def generate_customer_faq(self, cluster: Cluster) -> CustomerFAQ:
        prompt = self._build_customer_faq_prompt(cluster)
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
            log.warning("Gemini retornou não-JSON; primeiros 200 chars: %r", cleaned[:200])
            raise LLMResponseError(f"resposta Gemini não é JSON válido: {e}") from e
        try:
            return ArticleDraft.model_validate(data)
        except PydanticValidationError as e:
            log.warning("JSON do Gemini falhou no schema: %s", e)
            raise LLMResponseError(f"JSON do Gemini não satisfaz o schema: {e}") from e

    @staticmethod
    def _build_customer_faq_prompt(cluster: Cluster) -> str:
        summaries = "\n".join(f"- {s}" for s in cluster.summaries) or "(nenhum)"
        labels = ", ".join(cluster.labels) or "nenhuma"
        components = ", ".join(cluster.components) or "não identificados"
        if cluster.sample_descriptions:
            descriptions_block = "\n\n".join(cluster.sample_descriptions)
        else:
            descriptions_block = (
                "(tickets sem `description` preenchida — use os títulos acima como única fonte)"
            )
        return f"""Você é especialista em escrever FAQs self-service para clientes B2B da Kobe — empresa que desenvolve aplicativos móveis para grandes varejistas brasileiros (Amaro, Mr. Cat, Zaffari, Epharma, etc.).

Sua tarefa: gerar um documento de Perguntas Frequentes que **o time de produto/operação do varejista** possa consultar ANTES de abrir um chamado de suporte. Ou seja, escrever conteúdo que o cliente leia e se resolva sozinho.

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

═══════════════════════════════════════════════════════════════
QUEM É O LEITOR (importante)
═══════════════════════════════════════════════════════════════

Equipes de PRODUTO ou OPERAÇÃO do varejista. NÃO são desenvolvedores, mas têm:
- Acesso ao painel admin Kobe (CMS/configurações)
- Familiaridade com termos como SDK, integração, push notification, deeplink
- Capacidade de configurar campanhas, produtos, regras de cashback

NÃO assume conhecimento de: código, SQL, comandos shell, debugging, root cause análise.

═══════════════════════════════════════════════════════════════
DIRETRIZES OBRIGATÓRIAS
═══════════════════════════════════════════════════════════════

1. CADA PERGUNTA é uma dúvida REAL que apareceu nos tickets — reformule no tom de quem está perguntando ("Por que...?", "Como faço para...?", "O que devo fazer quando...?").

2. CADA RESPOSTA é acionável em 2-5 frases:
   - O que verificar (no painel? no app? na configuração?)
   - O passo a passo curto
   - O que esperar como resultado

3. `when_to_contact` é OPCIONAL:
   - Preencher SE há cenário em que a auto-resolução não funciona
   - Format: "Se mesmo após verificar X, Y, Z, o problema persistir, abra um ticket de suporte fornecendo: [lista do que enviar — print, log de horário, exemplo de tela]"
   - Deixar `null` se a resposta resolve sempre.

4. NUNCA mencione:
   - Causa raiz interna (não dizer "é bug de WebView", apenas "configuração X precisa ser revisada")
   - Códigos de ticket internos (OPE-XXX) — varejista não tem acesso ao Jira
   - Código-fonte, SQL, comandos shell
   - "Entre em contato com o suporte" sem antes esgotar o que o cliente pode fazer

5. INTRO do documento (campo `intro`): 2-3 frases dizendo qual é o tópico e a quem se destina.

6. MÍNIMO 5 entries. IDEAL 7-10.

═══════════════════════════════════════════════════════════════
FORMATO DE RESPOSTA
═══════════════════════════════════════════════════════════════

Responda APENAS com JSON válido, sem markdown, sem texto adicional:

{{
  "title": "Título curto do tópico FAQ (5-12 palavras)",
  "intro": "Parágrafo curto introduzindo o tema — quem deve ler e o que vai aprender",
  "entries": [
    {{
      "question": "Pergunta direta como o leitor faria",
      "answer": "Resposta acionável em 2-5 frases",
      "when_to_contact": "Texto opcional sobre quando escalar pra suporte, ou null"
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
