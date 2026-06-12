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


def test_main_prints_to_stderr_on_sitemap_error_in_verbose(monkeypatch, tmp_path, capsys):
    _set_required(monkeypatch)
    monkeypatch.setenv("GITBOOK_CACHE_PATH", str(tmp_path / "cache.json"))

    with patch("kiro.interfaces.cli.print_banner"), patch(
        "kiro.interfaces.cli.scrape_public_gitbook"
    ) as mock_scrape:
        mock_scrape.side_effect = ValueError("sitemap inacessível em https://x/sitemap.xml")
        rc = main(["fetch-gitbook", "--public", "--verbose"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "sitemap inacessível" in captured.err


# ─── fetch-confluence-kb ────────────────────────────────────────────


def test_parser_aceita_fetch_confluence_kb(monkeypatch):
    parser = build_parser()
    args = parser.parse_args(["fetch-confluence-kb"])
    assert args.command == "fetch-confluence-kb"
    assert args.space is None  # default usa settings


def test_parser_aceita_space_override(monkeypatch):
    parser = build_parser()
    args = parser.parse_args(["fetch-confluence-kb", "--space", "DOCS"])
    assert args.space == "DOCS"


def test_main_dispatches_to_confluence_scraper(monkeypatch, tmp_path):
    _set_required(monkeypatch)
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://x.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_KB_CACHE_PATH", str(tmp_path / "sup_cache.json"))

    with patch("kiro.interfaces.cli.print_banner"), patch(
        "kiro.interfaces.cli.scrape_confluence_kb"
    ) as mock_scrape:
        mock_scrape.return_value = ScrapingResult(
            pages_fetched=5,
            chunks_written=15,
            failed_urls=[],
            output_path=tmp_path / "sup_cache.json",
        )

        rc = main(["fetch-confluence-kb"])

    assert rc == 0
    assert mock_scrape.called
    kwargs = mock_scrape.call_args.kwargs
    assert kwargs["space_key"] == "SUP"
    assert kwargs["base_url"] == "https://x.atlassian.net/wiki"


def test_main_uses_space_override(monkeypatch, tmp_path):
    _set_required(monkeypatch)
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://x.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_KB_CACHE_PATH", str(tmp_path / "sup_cache.json"))

    with patch("kiro.interfaces.cli.print_banner"), patch(
        "kiro.interfaces.cli.scrape_confluence_kb"
    ) as mock_scrape:
        mock_scrape.return_value = ScrapingResult(
            pages_fetched=1, chunks_written=2, failed_urls=[],
            output_path=tmp_path / "x.json",
        )
        rc = main(["fetch-confluence-kb", "--space", "DOCS"])

    assert rc == 0
    assert mock_scrape.call_args.kwargs["space_key"] == "DOCS"


def test_main_fails_without_confluence_base_url(monkeypatch, tmp_path, capsys):
    _set_required(monkeypatch)
    # Anula CONFLUENCE_BASE_URL — string vazia simula "não configurado".
    # (monkeypatch.delenv não impede o .env do projeto de prover o valor.)
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "")
    rc = main(["fetch-confluence-kb"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "CONFLUENCE_BASE_URL" in captured.err
