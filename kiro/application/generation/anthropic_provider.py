"""Implementação do LLMProvider via Anthropic Messages API."""

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
    def _build_prompt(
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> str:
        """Delega pro Gemini — o conteúdo é agnostic de provedor (issue #15).

        Mantém um único prompt pro Article evita divergência entre providers
        e simplifica manutenção. Mesmo padrão usado pro FAQ desde a V1.0.1.
        """
        from kiro.application.generation.gemini_provider import GeminiProvider
        return GeminiProvider._build_prompt(cluster, kb_context, style_examples)

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
    def _build_customer_faq_prompt(
        cluster: Cluster,
        kb_context: Sequence[GitBookChunk] = (),
        style_examples: Sequence[GitBookChunk] = (),
    ) -> str:
        # Mesmo prompt do Gemini — o conteúdo é agnostic de provedor.
        # Importamos lazy pra evitar dependência circular sutil entre os arquivos.
        from kiro.application.generation.gemini_provider import GeminiProvider
        return GeminiProvider._build_customer_faq_prompt(
            cluster, kb_context, style_examples
        )

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
