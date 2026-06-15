"""Testes do AnthropicProvider parser (atualizado issue #15)."""

import pytest

from kiro.application.generation.anthropic_provider import AnthropicProvider
from kiro.domain.exceptions import LLMResponseError


def test_parse_valid_article_json():
    raw = """{
      "title": "Solução de Problemas com Cashback",
      "scope_note": "Perguntas frequentes sobre cashback.",
      "sections": [
        {"heading": "Como ativar cashback", "body": "No painel admin."},
        {"heading": "Funciona em todas as lojas?", "body": "Sim, com regras."}
      ],
      "tags": ["cashback"]
    }"""
    article = AnthropicProvider._parse_response(raw)
    assert article.title.startswith("Solução")
    assert len(article.sections) == 2


def test_parse_strips_markdown_fences():
    raw = (
        "```json\n"
        '{"title": "T objetivo", "scope_note": "S", "sections": '
        '[{"heading": "h1", "body": "b1"}, {"heading": "h2", "body": "b2"}]}\n'
        "```"
    )
    article = AnthropicProvider._parse_response(raw)
    assert article.title.startswith("T objetivo")


def test_parse_invalid_json_raises():
    with pytest.raises(LLMResponseError):
        AnthropicProvider._parse_response("isto não é json")


def test_parse_missing_required_field_raises():
    with pytest.raises(LLMResponseError):
        AnthropicProvider._parse_response('{"title": "x"}')


def test_parse_rejects_old_schema():
    """Schema antigo (problem/cause/solution) deve falhar."""
    raw = '{"title": "T", "problem": "P", "cause": "C", "solution": "S"}'
    with pytest.raises(LLMResponseError):
        AnthropicProvider._parse_response(raw)
