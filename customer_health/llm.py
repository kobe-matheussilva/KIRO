import json
import re
from typing import Any

import httpx

from customer_health.settings import RelationshipSettings


def _redact_tokens(text: str, tokens: list[str]) -> str:
    output = text
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        output = re.sub(re.escape(cleaned), "[REDACTED]", output, flags=re.IGNORECASE)
    return output


def _build_prompt(payload: dict[str, Any]) -> str:
    safe_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "Você é o Kizuma, um analista de Customer Success e Suporte. "
        "Avalie a qualidade do relacionamento entre cliente e suporte com base nos dados estruturados.\n\n"
        "Regras obrigatórias:\n"
        "1) NÃO citar nome da empresa, produto interno, time interno, domínio, e-mail ou nomes de pessoas.\n"
        "2) Trate o alvo apenas como 'o cliente'.\n"
        "3) Linguagem clara e amigável para público não técnico.\n"
        "4) Leve em consideração os sinais de SLA no diagnóstico (breach, cumprimento e volume).\n"
        "5) Seja objetivo e acionável.\n\n"
        "Interpretação obrigatória da temperatura:\n"
        "- Baixa = relação boa/saudável.\n"
        "- Moderada = relação estável com pontos de atenção.\n"
        "- Alta = relação mais balançada, difícil, com atrito relevante.\n"
        "- Crítica = relação em risco alto.\n"
        "Nunca descreva temperatura Alta ou Crítica como cenário bom.\n\n"
        "Formato da resposta:\n"
        "- Responda em Markdown simples e legível\n"
        "- Resumo executivo (2-3 frases)\n"
        "- Temperatura da relação (Baixa/Moderada/Alta/Crítica + justificativa)\n"
        "- Leitura de SLA (como isso impacta o relacionamento)\n"
        "- Principais sinais observados\n"
        "- Recomendações práticas para os próximos 30 dias\n"
        "- Conclusão final em 1 frase\n\n"
        "Dados para análise (JSON):\n"
        f"{safe_json}"
    )


def _call_gemini(settings: RelationshipSettings, prompt: str) -> str:
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/models/{settings.llm_model}:generateContent"
    params = {"key": settings.llm_api_key.get_secret_value()}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": settings.llm_temperature,
        },
    }

    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        response = client.post(url, params=params, json=body)
        response.raise_for_status()
        data = response.json()

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini retornou resposta vazia")

    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    chunks = [str(part.get("text") or "") for part in parts if isinstance(part, dict)]
    text = "\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    if not text:
        raise RuntimeError("Gemini retornou resposta sem texto")
    return text


def _call_anthropic(settings: RelationshipSettings, prompt: str) -> str:
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/messages"
    headers = {
        "x-api-key": settings.llm_api_key.get_secret_value(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": settings.llm_model,
        "max_tokens": 1200,
        "temperature": settings.llm_temperature,
        "messages": [{"role": "user", "content": prompt}],
    }

    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    content = data.get("content") or []
    chunks = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
    text = "\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    if not text:
        raise RuntimeError("Anthropic retornou resposta sem texto")
    return text


def generate_friendly_assessment(
    settings: RelationshipSettings,
    *,
    customer_name: str,
    company_tokens: list[str],
    payload: dict[str, Any],
) -> str:
    prompt = _build_prompt(payload)

    if settings.llm_provider == "gemini":
        raw = _call_gemini(settings, prompt)
    else:
        raw = _call_anthropic(settings, prompt)

    redaction_tokens = [customer_name, *company_tokens]
    safe = _redact_tokens(raw, redaction_tokens)
    return safe
