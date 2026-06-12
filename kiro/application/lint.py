"""Engine que aplica as regras determinísticas a um draft (issue #12).

Devolve `LinterResult` com violations separadas por severidade. Caller
(Pipeline) decide o que fazer com BLOCKs conforme `LINTER_BLOCK_MODE`.

Stateless — pode ser singleton. Aceita ArticleDraft ou CustomerFAQ;
seleciona o conjunto de regras correto via dispatch por tipo.
"""

from dataclasses import dataclass, field
from typing import Optional, Union

from kiro.application.lint_rules import (
    RULES_ARTICLE,
    RULES_BLOCK_COMMON,
    RULES_FAQ,
    RULES_WARN_COMMON,
    LintRule,
    Violation,
    collect_article_texts,
    collect_faq_texts,
)
from kiro.domain.models import ArticleDraft, CustomerFAQ


@dataclass(frozen=True)
class LinterResult:
    """Resultado da varredura — blocks separados de warns pra o caller decidir."""

    blocks: list[Violation] = field(default_factory=list)
    warns: list[Violation] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocks)

    @property
    def summary(self) -> str:
        return f"{len(self.blocks)} block, {len(self.warns)} warn"


class OutputLinter:
    """Aplica regras determinísticas a ArticleDraft ou CustomerFAQ."""

    def check(self, draft: Union[ArticleDraft, CustomerFAQ]) -> LinterResult:
        """Dispatch por tipo — seleciona o conjunto de regras correto."""
        if isinstance(draft, ArticleDraft):
            return self.check_article(draft)
        if isinstance(draft, CustomerFAQ):
            return self.check_customer_faq(draft)
        raise TypeError(f"OutputLinter não sabe checar {type(draft).__name__}")

    def check_article(self, draft: ArticleDraft) -> LinterResult:
        fields = collect_article_texts(draft)
        rules = RULES_BLOCK_COMMON + RULES_WARN_COMMON + RULES_ARTICLE
        return self._apply(rules, fields)

    def check_customer_faq(self, draft: CustomerFAQ) -> LinterResult:
        fields = collect_faq_texts(draft)
        rules = RULES_BLOCK_COMMON + RULES_WARN_COMMON + RULES_FAQ
        return self._apply(rules, fields)

    @staticmethod
    def _apply(rules: list[LintRule], fields: dict[str, str]) -> LinterResult:
        blocks: list[Violation] = []
        warns: list[Violation] = []
        for rule in rules:
            for v in rule.check(fields):
                if v.severity == "block":
                    blocks.append(v)
                else:
                    warns.append(v)
        return LinterResult(blocks=blocks, warns=warns)


def build_output_linter(enabled: bool) -> Optional[OutputLinter]:
    """Helper pra construção via CLI/pipeline. None quando flag está off.

    Padrão consistente com build_retriever / build_style_finder.
    """
    return OutputLinter() if enabled else None
