"""Smoke tests do CLI — confirma que subcomandos são roteados."""

from unittest.mock import patch

import pytest

from kiro.domain.models import ScrapingResult
from kiro.interfaces.cli import build_parser, main


def test_parser_aceita_fetch_gitbook_public(monkeypatch):
    parser = build_parser()
    args = parser.parse_args(["fetch-gitbook", "--public"])
    assert args.command == "fetch-gitbook"
    assert args.public is True


def test_parser_exige_public_flag(monkeypatch):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["fetch-gitbook"])  # sem --public, --internal etc


def _set_required(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "u@x.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-xyz")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")
    monkeypatch.setenv("LLM_API_KEY", "sk-abc")


def test_main_dispatches_to_scraper(monkeypatch, tmp_path):
    _set_required(monkeypatch)
    monkeypatch.setenv("GITBOOK_CACHE_PATH", str(tmp_path / "cache.json"))

    with patch("kiro.interfaces.cli.print_banner"), patch(
        "kiro.interfaces.cli.scrape_public_gitbook"
    ) as mock_scrape:
        mock_scrape.return_value = ScrapingResult(
            pages_fetched=3,
            chunks_written=10,
            failed_urls=[],
            output_path=tmp_path / "cache.json",
        )

        rc = main(["fetch-gitbook", "--public"])

    assert rc == 0
    assert mock_scrape.called
    kwargs = mock_scrape.call_args.kwargs
    args_pos = mock_scrape.call_args.args
    # Aceita chamada com kwargs OU args:
    base_url = kwargs.get("base_url") or args_pos[0]
    output_path = kwargs.get("output_path") or args_pos[1]
    assert "kobeapps.gitbook.io" in base_url
    assert "cache.json" in str(output_path)
