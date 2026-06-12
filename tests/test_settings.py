import pytest
from pydantic import ValidationError

from kiro.config.settings import Settings


def _set_required(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
    monkeypatch.setenv("JIRA_USER_EMAIL", "u@x.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-xyz")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")
    monkeypatch.setenv("LLM_API_KEY", "sk-abc")


def test_loads_with_required_env(monkeypatch):
    _set_required(monkeypatch)
    s = Settings(_env_file=None)
    assert s.jira_project_key == "PROJ"
    assert s.enable_confluence_publish is False
    assert s.lookback_days == 30


def test_missing_required_raises(monkeypatch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_publish_requires_confluence_config(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ENABLE_CONFLUENCE_PUBLISH", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_publish_ok_with_confluence_config(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ENABLE_CONFLUENCE_PUBLISH", "true")
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://x.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "DOC")
    s = Settings(_env_file=None)
    assert s.enable_confluence_publish is True


def test_slack_notify_requires_webhook(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ENABLE_SLACK_NOTIFY", "true")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_dry_run_disables_externals(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ENABLE_SLACK_NOTIFY", "true")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x/y/z")
    s = Settings(_env_file=None)
    assert s.enable_slack_notify is False


def test_secrets_are_not_in_str_repr(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("JIRA_API_TOKEN", "super-secret-token-value")
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret")
    s = Settings(_env_file=None)
    assert "super-secret-token-value" not in str(s)
    assert "sk-super-secret" not in str(s)


def test_gitbook_defaults(monkeypatch):
    _set_required(monkeypatch)
    s = Settings(_env_file=None)
    assert s.gitbook_public_url == "https://kobeapps.gitbook.io/kobe.io-documentacao"
    assert str(s.gitbook_cache_path) == "kiro/data/gitbook_public_cache.json"
    assert s.gitbook_request_delay_seconds == 0.5


def test_gitbook_overrides(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("GITBOOK_PUBLIC_URL", "https://example.com/docs")
    monkeypatch.setenv("GITBOOK_CACHE_PATH", "/tmp/cache.json")
    monkeypatch.setenv("GITBOOK_REQUEST_DELAY_SECONDS", "1.5")
    s = Settings(_env_file=None)
    assert s.gitbook_public_url == "https://example.com/docs"
    assert str(s.gitbook_cache_path) == "/tmp/cache.json"
    assert s.gitbook_request_delay_seconds == 1.5


def test_gitbook_rag_defaults_off(monkeypatch):
    _set_required(monkeypatch)
    s = Settings(_env_file=None)
    assert s.enable_gitbook_rag is False
    assert s.gitbook_rag_top_k == 3
    assert s.gitbook_rag_min_score == 0.1


def test_gitbook_rag_overrides(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ENABLE_GITBOOK_RAG", "true")
    monkeypatch.setenv("GITBOOK_RAG_TOP_K", "5")
    monkeypatch.setenv("GITBOOK_RAG_MIN_SCORE", "0.25")
    s = Settings(_env_file=None)
    assert s.enable_gitbook_rag is True
    assert s.gitbook_rag_top_k == 5
    assert s.gitbook_rag_min_score == 0.25


def test_gitbook_rag_top_k_rejects_zero(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("GITBOOK_RAG_TOP_K", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ─── Confluence SUP (style reference + dedupe) ──────────────────────


def test_confluence_kb_defaults_off(monkeypatch):
    _set_required(monkeypatch)
    s = Settings(_env_file=None)
    assert s.confluence_kb_space_key == "SUP"
    assert str(s.confluence_kb_cache_path) == "kiro/data/confluence_sup_cache.json"
    assert s.enable_confluence_few_shot is False
    assert s.confluence_few_shot_top_k == 2
    assert s.confluence_dedupe_threshold == 0.6
    assert s.confluence_kb_page_size == 25


def test_confluence_kb_overrides(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CONFLUENCE_KB_SPACE_KEY", "DOCS")
    monkeypatch.setenv("ENABLE_CONFLUENCE_FEW_SHOT", "true")
    monkeypatch.setenv("CONFLUENCE_FEW_SHOT_TOP_K", "3")
    monkeypatch.setenv("CONFLUENCE_DEDUPE_THRESHOLD", "0.75")
    s = Settings(_env_file=None)
    assert s.confluence_kb_space_key == "DOCS"
    assert s.enable_confluence_few_shot is True
    assert s.confluence_few_shot_top_k == 3
    assert s.confluence_dedupe_threshold == 0.75


def test_confluence_few_shot_top_k_rejects_zero(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CONFLUENCE_FEW_SHOT_TOP_K", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_confluence_dedupe_threshold_rejects_above_one(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("CONFLUENCE_DEDUPE_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
