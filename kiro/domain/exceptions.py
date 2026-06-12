"""Hierarquia de exceções de domínio. Tudo herda de KiroError."""


class KiroError(Exception):
    """Base de qualquer erro do KIRO."""


class ConfigError(KiroError):
    """Configuração ausente, inválida ou incoerente."""


class JiraError(KiroError):
    """Falha na API do Jira."""


class ConfluenceError(KiroError):
    """Falha na API do Confluence."""


class SlackError(KiroError):
    """Falha na API do Slack."""


class LLMError(KiroError):
    """Falha de comunicação com o provedor de LLM."""


class LLMResponseError(LLMError):
    """Resposta do LLM em formato inesperado ou inválida pelo schema."""


class ClusteringError(KiroError):
    """Falha ao clusterizar tickets."""


class LinterBlocked(KiroError):
    """Levantada quando LINTER_BLOCK_MODE=fail e o linter bloqueia um draft."""
