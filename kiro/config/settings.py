"""Configuração única, validada, fail-fast. Nada hardcoded em business logic."""

from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults bem-conhecidos por provedor — aplicados pelo validator quando o usuário
# não define LLM_MODEL ou LLM_BASE_URL no env. NÃO usados em business logic.
_PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-sonnet-4-20250514",
}
_PROVIDER_DEFAULT_BASE_URL: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ─── Jira (obrigatório) ─────────────────────────────────────────
    jira_base_url: str
    jira_user_email: str
    jira_api_token: SecretStr
    jira_project_key: str
    jira_extra_jql: Optional[str] = None
    jira_closed_statuses: list[str] = Field(
        default_factory=lambda: ["Done", "Closed", "Resolved"]
    )
    jira_page_size: int = Field(default=100, ge=1, le=100)
    jira_timeout_seconds: int = Field(default=30, ge=1)

    # ─── Confluence (opcional) ──────────────────────────────────────
    confluence_base_url: Optional[str] = None
    confluence_space_key: Optional[str] = None
    confluence_parent_id: Optional[str] = None
    confluence_timeout_seconds: int = Field(default=30, ge=1)

    # ─── Slack (opcional) ───────────────────────────────────────────
    slack_webhook_url: Optional[SecretStr] = None
    slack_timeout_seconds: int = Field(default=15, ge=1)

    # ─── LLM ────────────────────────────────────────────────────────
    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    llm_api_key: SecretStr
    # Vazios => validator aplica default do provedor escolhido
    llm_model: str = ""
    llm_base_url: str = ""
    llm_max_tokens: int = Field(default=1500, ge=1)
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=60, ge=1)
    # Pausa entre chamadas sequenciais — útil pra respeitar rate-limit de free tier
    # (Gemini 2.5 Flash free: ~10 RPM → use 7s; Gemini 2.0 Flash free: ~15 RPM → use 5s)
    llm_request_delay_seconds: float = Field(default=0.0, ge=0.0)

    # ─── Clustering ─────────────────────────────────────────────────
    cluster_strategy: Literal["heuristic"] = "heuristic"
    cluster_min_size: int = Field(default=3, ge=2)
    cluster_top_n: int = Field(default=10, ge=1)
    cluster_overlap_threshold: int = Field(default=3, ge=1)
    cluster_text_max_length: int = Field(default=600, ge=100)

    # ─── GitBook RAG (opcional) ─────────────────────────────────────
    gitbook_public_url: str = "https://kobeapps.gitbook.io/kobe.io-documentacao"
    gitbook_cache_path: Path = Path("kiro/data/gitbook_public_cache.json")
    gitbook_request_delay_seconds: float = Field(default=0.5, ge=0.0)
    # Quando True, o pipeline carrega o cache e injeta chunks relevantes no prompt.
    # Default False: sem cache existente, comportamento é idêntico ao pré-V1.1.
    enable_gitbook_rag: bool = False
    gitbook_rag_top_k: int = Field(default=3, ge=1, le=20)
    gitbook_rag_min_score: float = Field(default=0.1, ge=0.0)

    # ─── Pipeline ───────────────────────────────────────────────────
    lookback_days: int = Field(default=30, ge=1)
    enable_confluence_publish: bool = False
    enable_slack_notify: bool = False
    output_dir: Path = Path("output")
    log_level: str = "INFO"
    dry_run: bool = False

    @model_validator(mode="after")
    def _apply_provider_defaults(self) -> "Settings":
        if not self.llm_model:
            self.llm_model = _PROVIDER_DEFAULT_MODEL[self.llm_provider]
        if not self.llm_base_url:
            self.llm_base_url = _PROVIDER_DEFAULT_BASE_URL[self.llm_provider]
        return self

    @model_validator(mode="after")
    def _validate_integrations(self) -> "Settings":
        if self.enable_confluence_publish and not (
            self.confluence_base_url and self.confluence_space_key
        ):
            raise ValueError(
                "ENABLE_CONFLUENCE_PUBLISH=true exige CONFLUENCE_BASE_URL e CONFLUENCE_SPACE_KEY."
            )
        if self.enable_slack_notify and not self.slack_webhook_url:
            raise ValueError("ENABLE_SLACK_NOTIFY=true exige SLACK_WEBHOOK_URL.")
        if self.dry_run:
            self.enable_confluence_publish = False
            self.enable_slack_notify = False
        return self
