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

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV_FILE),
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

    # ─── Confluence SUP — style reference + dedupe (opcional) ──────
    # Diferente do GitBook (grounding factual), o SUP serve como style
    # reference: few-shot pro LLM imitar tom + dedupe contra artigos
    # já publicados. Política mantida: NUNCA cita URL do SUP no output.
    confluence_kb_space_key: str = "SUP"
    confluence_kb_cache_path: Path = Path("kiro/data/confluence_sup_cache.json")
    confluence_kb_request_delay_seconds: float = Field(default=0.5, ge=0.0)
    confluence_kb_page_size: int = Field(default=25, ge=1, le=100)
    enable_confluence_few_shot: bool = False
    confluence_few_shot_top_k: int = Field(default=2, ge=1, le=5)
    confluence_dedupe_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    # ─── Output linter (opcional) ───────────────────────────────────
    # Validador determinístico aplicado entre a geração do LLM e o save.
    # BLOCK = vazamento interno (OPE-XXX, jargão, URLs); WARN = qualidade
    # mínima. Default OFF — ativar quando confiar; LINTER_BLOCK_MODE
    # define o tratamento de violações block.
    enable_output_linter: bool = False
    # skip = pula cluster com block, segue rodada (recomendado pra V1)
    # fail = derruba a rodada inteira
    # warn = salva mesmo com block, só reporta
    linter_block_mode: Literal["skip", "fail", "warn"] = "skip"

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

    @model_validator(mode="after")
    def _validate_placeholder_credentials(self) -> "Settings":
        def _looks_like_placeholder(value: str) -> bool:
            lowered = value.strip().lower()
            markers = (
                "replace-with",
                "your-company",
                "example.com",
                "replace/me/please",
            )
            return any(marker in lowered for marker in markers)

        required_checks = {
            "JIRA_BASE_URL": self.jira_base_url,
            "JIRA_USER_EMAIL": self.jira_user_email,
            "JIRA_PROJECT_KEY": self.jira_project_key,
            "LLM_API_KEY": self.llm_api_key.get_secret_value(),
        }

        placeholders = [
            key for key, value in required_checks.items() if _looks_like_placeholder(value)
        ]

        if placeholders:
            joined = ", ".join(placeholders)
            raise ValueError(
                f"variáveis com valor de exemplo detectadas: {joined}. "
                "Atualize o .env com credenciais reais antes de executar."
            )

        return self
