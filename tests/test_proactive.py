from kiro.application.proactive import (
    build_llm_candidates,
    compute_proactive_insights,
    format_proactive_card,
)
from kiro.domain.models import Ticket


def _ticket(summary: str, description: str = "", labels=None, components=None) -> Ticket:
    return Ticket(
        key="SUP-1",
        summary=summary,
        description=description,
        labels=labels or [],
        components=components or [],
    )


def _ticket_with_customer(
    *,
    summary: str,
    customer_name: str,
    reporter_name: str = "",
    labels=None,
    components=None,
) -> Ticket:
    return Ticket(
        key="SUP-2",
        summary=summary,
        description="",
        labels=labels or [],
        components=components or [],
        customer_name=customer_name,
        reporter_name=reporter_name or None,
    )


def test_compute_proactive_insights_identifies_fragile_and_satisfied():
    tickets = [
        _ticket(
            "Cliente: Acme com erro crítico no checkout",
            "reclama de instabilidade e indisponibilidade",
            labels=["customer_acme"],
            components=["Checkout"],
        ),
        _ticket(
            "Cliente: Acme voltou a falhar no checkout",
            "regressao novamente",
            labels=["customer_acme"],
            components=["Checkout"],
        ),
        _ticket(
            "Cliente: Beta resolveu com sucesso",
            "obrigado, fluxo normalizou",
            labels=["customer_beta"],
            components=["Cashback"],
        ),
    ]

    insights = compute_proactive_insights(tickets, period_days=30, top_n=3)

    assert insights.fragile_customers
    assert insights.fragile_customers[0].name == "Acme"
    assert insights.satisfied_customers
    assert insights.problematic_feature is not None


def test_format_proactive_card_contains_three_required_sections():
    tickets = [
        _ticket(
            "Cliente: Acme erro no checkout",
            "instabilidade",
            labels=["customer_acme"],
            components=["Checkout"],
        )
    ]
    insights = compute_proactive_insights(tickets, period_days=30, top_n=3)
    summary, description = format_proactive_card(
        insights,
        source_project_key="SUPP",
        board_name="Demandas Suporte",
    )

    assert "KIRO Proativo" in summary
    assert "Clientes potencialmente mais fragilizados" in description
    assert "Clientes com maior sinal de satisfação" in description
    assert "Funcionalidade com maior sinal de problema/regressão" in description


def test_build_llm_candidates_returns_context_for_top_signals():
    tickets = [
        _ticket(
            "Cliente: Acme erro no checkout",
            "instabilidade",
            labels=["customer_acme"],
            components=["Checkout"],
        ),
        _ticket(
            "Cliente: Beta resolveu com sucesso",
            "obrigado",
            labels=["customer_beta"],
            components=["Cashback"],
        ),
    ]
    insights = compute_proactive_insights(tickets, period_days=30, top_n=2)
    candidates = build_llm_candidates(
        insights,
        tickets,
        top_candidates=1,
        top_tickets_per_candidate=2,
    )

    assert candidates
    assert all(item.tickets_context for item in candidates)


def test_compute_proactive_insights_prioritizes_customer_profile_name():
    tickets = [
        _ticket_with_customer(
            summary="Erro crítico no checkout",
            customer_name="Cliente Real SA",
            reporter_name="Agente Suporte",
            labels=["customer_label_errado"],
            components=["Checkout"],
        )
    ]

    insights = compute_proactive_insights(tickets, period_days=30, top_n=3)

    assert insights.fragile_customers
    assert insights.fragile_customers[0].name == "Cliente Real SA"
