"""Análise proativa dos tickets para sinais de relacionamento e regressão."""

from dataclasses import dataclass
from typing import Optional

from kiro.domain.models import Ticket

_NEGATIVE_TERMS: dict[str, int] = {
    "erro": 3,
    "falha": 3,
    "indispon": 3,
    "instabilidade": 3,
    "crit": 3,
    "reclama": 2,
    "insatisfa": 2,
    "demora": 1,
    "lento": 1,
    "impacto": 2,
    "urgente": 2,
}

_POSITIVE_TERMS: dict[str, int] = {
    "resolvido": 2,
    "normalizou": 2,
    "obrigad": 1,
    "satisfa": 2,
    "ok": 1,
    "sucesso": 1,
}

_REGRESSION_TERMS: dict[str, int] = {
    "regress": 4,
    "voltou": 3,
    "novamente": 2,
    "reincid": 3,
    "parou de funcionar": 3,
}

_GENERIC_LABELS = {
    "bug",
    "suporte",
    "support",
    "incidente",
    "incident",
    "problema",
    "issue",
    "erro",
}

_INVALID_CUSTOMER_TOKENS = {
    "ao",
    "a",
    "o",
    "de",
    "do",
    "da",
    "para",
    "quando",
    "como",
    "realizar",
    "disparos",
    "notificacoes",
    "notificações",
}


@dataclass(frozen=True)
class RankedSignal:
    name: str
    score: float
    tickets: int
    rationale: str


@dataclass(frozen=True)
class ProactiveInsights:
    period_days: int
    fragile_customers: list[RankedSignal]
    satisfied_customers: list[RankedSignal]
    problematic_feature: Optional[RankedSignal]


@dataclass(frozen=True)
class ProactiveCandidateContext:
    candidate_type: str
    candidate_name: str
    heuristic_score: float
    rationale: str
    tickets_context: list[dict[str, str]]


@dataclass(frozen=True)
class ProactiveLLMValidation:
    candidate_type: str
    candidate_name: str
    validation_decision: str
    confidence: str
    status_summary: str
    key_problems: str
    recommended_action: str


@dataclass
class _CustomerAgg:
    tickets: int = 0
    negative: int = 0
    positive: int = 0


@dataclass
class _FeatureAgg:
    tickets: int = 0
    negative: int = 0
    regression: int = 0


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _score_terms(text: str, weights: dict[str, int]) -> int:
    return sum(weight for token, weight in weights.items() if token in text)


def _extract_customer(ticket: Ticket) -> Optional[str]:
    def _is_valid_candidate(value: str) -> bool:
        cleaned = " ".join(value.strip().split())
        if not (2 <= len(cleaned) <= 50):
            return False
        words = cleaned.lower().split()
        if len(words) > 4:
            return False
        if any(w in _INVALID_CUSTOMER_TOKENS for w in words):
            return False
        return True

    customer_name = (ticket.customer_name or "").strip()
    if customer_name and _is_valid_candidate(customer_name):
        return customer_name

    for raw in ticket.labels:
        label = raw.strip()
        l = label.lower()
        for prefix in ("customer_", "cliente_", "client_"):
            if l.startswith(prefix):
                value = label[len(prefix) :].replace("_", " ").strip()
                if value and _is_valid_candidate(value):
                    return value.title()

    reporter = (ticket.reporter_name or "").strip()
    if reporter and _is_valid_candidate(reporter):
        return reporter

    return None


def _extract_features(ticket: Ticket) -> list[str]:
    if ticket.components:
        return [c.strip() for c in ticket.components if c.strip()]

    selected: list[str] = []
    for raw in ticket.labels:
        label = raw.strip()
        l = label.lower()
        if not l or l in _GENERIC_LABELS:
            continue
        if l.startswith("customer_") or l.startswith("cliente_") or l.startswith("client_"):
            continue
        selected.append(label)
    return selected[:3]


def _ticket_context(ticket: Ticket) -> dict[str, str]:
    return {
        "key": ticket.key,
        "summary": ticket.summary,
        "description": (ticket.description or "")[:1200],
        "status": ticket.status or "",
        "labels": ", ".join(ticket.labels[:8]),
        "components": ", ".join(ticket.components[:8]),
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else "",
    }


def build_llm_candidates(
    insights: ProactiveInsights,
    tickets: list[Ticket],
    *,
    top_candidates: int,
    top_tickets_per_candidate: int,
) -> list[ProactiveCandidateContext]:
    candidates: list[ProactiveCandidateContext] = []

    for item in insights.fragile_customers[:top_candidates]:
        matched = [
            t for t in tickets if (_extract_customer(t) or "") == item.name
        ][:top_tickets_per_candidate]
        candidates.append(
            ProactiveCandidateContext(
                candidate_type="customer_fragile",
                candidate_name=item.name,
                heuristic_score=item.score,
                rationale=item.rationale,
                tickets_context=[_ticket_context(t) for t in matched],
            )
        )

    for item in insights.satisfied_customers[:top_candidates]:
        matched = [
            t for t in tickets if (_extract_customer(t) or "") == item.name
        ][:top_tickets_per_candidate]
        candidates.append(
            ProactiveCandidateContext(
                candidate_type="customer_satisfied",
                candidate_name=item.name,
                heuristic_score=item.score,
                rationale=item.rationale,
                tickets_context=[_ticket_context(t) for t in matched],
            )
        )

    if insights.problematic_feature is not None:
        feature = insights.problematic_feature
        matched = [
            t for t in tickets if feature.name in _extract_features(t)
        ][:top_tickets_per_candidate]
        candidates.append(
            ProactiveCandidateContext(
                candidate_type="feature_regression",
                candidate_name=feature.name,
                heuristic_score=feature.score,
                rationale=feature.rationale,
                tickets_context=[_ticket_context(t) for t in matched],
            )
        )

    return candidates


def compute_proactive_insights(
    tickets: list[Ticket],
    *,
    period_days: int,
    top_n: int = 3,
) -> ProactiveInsights:
    customers: dict[str, _CustomerAgg] = {}
    features: dict[str, _FeatureAgg] = {}

    for t in tickets:
        text = _norm(f"{t.summary} {t.description}")
        neg = _score_terms(text, _NEGATIVE_TERMS)
        pos = _score_terms(text, _POSITIVE_TERMS)
        reg = _score_terms(text, _REGRESSION_TERMS)

        customer = _extract_customer(t)
        if customer:
            agg = customers.setdefault(customer, _CustomerAgg())
            agg.tickets += 1
            agg.negative += neg
            agg.positive += pos

        for feature in _extract_features(t):
            aggf = features.setdefault(feature, _FeatureAgg())
            aggf.tickets += 1
            aggf.negative += neg
            aggf.regression += reg

    fragile: list[RankedSignal] = []
    for name, agg in customers.items():
        score = (agg.negative * 1.7) + (agg.tickets * 0.4) - (agg.positive * 1.1)
        fragile.append(
            RankedSignal(
                name=name,
                score=round(score, 2),
                tickets=agg.tickets,
                rationale=(
                    f"{agg.tickets} tickets, sinais negativos={agg.negative}, "
                    f"sinais positivos={agg.positive}"
                ),
            )
        )

    satisfied: list[RankedSignal] = []
    for name, agg in customers.items():
        score = (agg.positive * 1.9) - (agg.negative * 1.2) + max(agg.tickets - 1, 0) * 0.2
        satisfied.append(
            RankedSignal(
                name=name,
                score=round(score, 2),
                tickets=agg.tickets,
                rationale=(
                    f"{agg.tickets} tickets, sinais positivos={agg.positive}, "
                    f"sinais negativos={agg.negative}"
                ),
            )
        )

    problematic_feature: Optional[RankedSignal] = None
    if features:
        ranked = sorted(
            (
                RankedSignal(
                    name=name,
                    score=round((agg.negative * 1.6) + (agg.regression * 2.2) + (agg.tickets * 0.5), 2),
                    tickets=agg.tickets,
                    rationale=(
                        f"{agg.tickets} tickets, sinais negativos={agg.negative}, "
                        f"sinais de regressão={agg.regression}"
                    ),
                )
                for name, agg in features.items()
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        problematic_feature = ranked[0]

    fragile_sorted = sorted(fragile, key=lambda item: item.score, reverse=True)[:top_n]
    satisfied_sorted = sorted(satisfied, key=lambda item: item.score, reverse=True)[:top_n]

    return ProactiveInsights(
        period_days=period_days,
        fragile_customers=fragile_sorted,
        satisfied_customers=satisfied_sorted,
        problematic_feature=problematic_feature,
    )


def format_proactive_card(
    insights: ProactiveInsights,
    *,
    source_project_key: str,
    board_name: str,
    llm_validations: Optional[list[ProactiveLLMValidation]] = None,
) -> tuple[str, str]:
    summary = (
        f"[KIRO Proativo] Sinais do mês ({insights.period_days}d) — "
        f"{source_project_key}"
    )

    lines = [
        "Resumo automático gerado pelo KIRO para apoio ao painel de Demandas Suporte.",
        f"Projeto fonte analisado: {source_project_key}",
        f"Board de destino: {board_name}",
        "",
        "1) Clientes potencialmente mais fragilizados no mês:",
    ]

    if insights.fragile_customers:
        for i, item in enumerate(insights.fragile_customers, start=1):
            lines.append(
                f"- {i}. {item.name} (score={item.score}, tickets={item.tickets}) — {item.rationale}"
            )
    else:
        lines.append("- Sem sinal suficiente para classificação neste período.")

    lines.append("")
    lines.append("2) Clientes com maior sinal de satisfação no mês:")
    if insights.satisfied_customers:
        for i, item in enumerate(insights.satisfied_customers, start=1):
            lines.append(
                f"- {i}. {item.name} (score={item.score}, tickets={item.tickets}) — {item.rationale}"
            )
    else:
        lines.append("- Sem sinal suficiente para classificação neste período.")

    lines.append("")
    lines.append("3) Funcionalidade com maior sinal de problema/regressão no mês:")
    if insights.problematic_feature is not None:
        item = insights.problematic_feature
        lines.append(
            f"- {item.name} (score={item.score}, tickets={item.tickets}) — {item.rationale}"
        )
    else:
        lines.append("- Sem sinal suficiente para classificação neste período.")

    if llm_validations:
        lines.append("")
        lines.append("4) Triangulação por IA (validação e sumarização):")
        for item in llm_validations:
            lines.append(
                f"- {item.candidate_type} / {item.candidate_name} | decisão={item.validation_decision}, confiança={item.confidence}"
            )
            lines.append(f"  status: {item.status_summary}")
            lines.append(f"  problemas: {item.key_problems}")
            lines.append(f"  ação sugerida: {item.recommended_action}")

    lines.append("")
    lines.append("Observação: este card é heurístico e deve ser validado pelo time de suporte.")

    return summary, "\n".join(lines)
