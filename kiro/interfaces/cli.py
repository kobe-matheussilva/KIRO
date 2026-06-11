"""Interface de linha de comando — único entrypoint humano."""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from kiro.application.clustering.heuristic import HeuristicClusteringStrategy
from kiro.application.generation.factory import build_llm_provider
from kiro.application.pipeline import OUTPUT_STYLES, STAGES, Pipeline, PipelineRequest
from kiro.config.settings import Settings
from kiro.domain.exceptions import ConfigError
from kiro.infrastructure.confluence_client import ConfluenceClient
from kiro.infrastructure.jira_client import JiraClient
from kiro.infrastructure.persistence import ArtifactStore
from kiro.infrastructure.slack_client import SlackClient
from kiro.infrastructure.gitbook_loader import scrape_public_gitbook
from kiro.utils.branding import print_banner, print_footer
from kiro.utils.logging import configure_logging
from kiro.utils.progress import Narrator

log = logging.getLogger("kiro.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kiro",
        description="KIRO — análise mensal de tickets recorrentes e geração de drafts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Executa o pipeline.")
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Não chama LLM real nem publica externamente — gera só artefatos locais.",
    )
    run_p.add_argument(
        "--stage",
        choices=STAGES + ("all",),
        default="all",
        help="Limita execução a um estágio (encadeia os anteriores quando necessário).",
    )
    run_p.add_argument(
        "--publish-confluence",
        action="store_true",
        help="Habilita publicação no Confluence (ignora ENABLE_CONFLUENCE_PUBLISH).",
    )
    run_p.add_argument(
        "--notify-slack",
        action="store_true",
        help="Habilita notificação no Slack (ignora ENABLE_SLACK_NOTIFY).",
    )
    run_p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Diretório de saída (default: OUTPUT_DIR ou ./output).",
    )
    run_p.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra logs detalhados em vez do spinner amigável.",
    )
    run_p.add_argument(
        "--style",
        choices=OUTPUT_STYLES,
        default=None,
        help="Estilo dos artigos gerados. Se omitido, KIRO pergunta interativamente.",
    )

    sub.add_parser("config-check", help="Valida configuração e encerra.")

    fetch_p = sub.add_parser(
        "fetch-gitbook",
        help="Baixa o GitBook e gera cache JSON (RAG).",
    )
    source_group = fetch_p.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--public",
        action="store_true",
        help="Baixa a GitBook pública (sem auth).",
    )
    fetch_p.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra logs detalhados em vez do spinner.",
    )
    return parser


def _ask_style_interactively() -> str:
    """Prompt simples no terminal pra escolher entre Artigo ou FAQ.

    Default = Artigo (texto corrido, mais próximo do padrão Confluence Kobe).
    """
    print()
    print("  ╭─────────────────────────────────────────────────────╮")
    print("  │  Em qual estilo gerar os artigos desta rodada?      │")
    print("  ├─────────────────────────────────────────────────────┤")
    print("  │   1) Artigo  — texto corrido (Sobre/Quando/Como)    │")
    print("  │   2) FAQ     — perguntas e respostas self-service   │")
    print("  ╰─────────────────────────────────────────────────────╯")
    while True:
        try:
            choice = input("   Escolha [1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            print("\n   (sem entrada — usando default: Artigo)")
            return "artigo"
        if choice in ("1", "artigo", "a"):
            return "artigo"
        if choice in ("2", "faq", "f"):
            return "faq"
        print("   Opção inválida — digite 1 ou 2.")


def _stages_for(stage: str) -> tuple[str, ...]:
    if stage == "all":
        return STAGES
    idx = STAGES.index(stage)
    return STAGES[: idx + 1]


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        raise ConfigError(str(e)) from e


def _build_pipeline(
    settings: Settings,
    output_dir: Optional[Path],
    *,
    dry_run: bool,
    narrator: Optional[Narrator] = None,
) -> Pipeline:
    store = ArtifactStore(output_dir or settings.output_dir)

    jira = JiraClient(
        base_url=settings.jira_base_url,
        user_email=settings.jira_user_email,
        api_token=settings.jira_api_token.get_secret_value(),
        timeout_seconds=settings.jira_timeout_seconds,
        page_size=settings.jira_page_size,
    )

    clustering = HeuristicClusteringStrategy(
        min_cluster_size=settings.cluster_min_size,
        top_n=settings.cluster_top_n,
        overlap_threshold=settings.cluster_overlap_threshold,
    )

    llm = build_llm_provider(settings, dry_run=dry_run)

    confluence: Optional[ConfluenceClient] = None
    if settings.confluence_base_url and settings.confluence_space_key:
        confluence = ConfluenceClient(
            base_url=settings.confluence_base_url,
            space_key=settings.confluence_space_key,
            user_email=settings.jira_user_email,
            api_token=settings.jira_api_token.get_secret_value(),
            parent_id=settings.confluence_parent_id,
            timeout_seconds=settings.confluence_timeout_seconds,
        )

    slack: Optional[SlackClient] = None
    if settings.slack_webhook_url is not None:
        slack = SlackClient(
            webhook_url=settings.slack_webhook_url.get_secret_value(),
            timeout_seconds=settings.slack_timeout_seconds,
        )

    return Pipeline(
        jira=jira,
        clustering=clustering,
        llm=llm,
        store=store,
        confluence=confluence,
        slack=slack,
        project_key=settings.jira_project_key,
        closed_statuses=settings.jira_closed_statuses,
        lookback_days=settings.lookback_days,
        extra_jql=settings.jira_extra_jql,
        llm_request_delay_seconds=settings.llm_request_delay_seconds,
        narrator=narrator,
        cluster_top_n=settings.cluster_top_n,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _load_settings()
    except ConfigError as e:
        print(f"[KIRO] configuração inválida:\n{e}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    if args.command == "config-check":
        log.info("configuração carregada com sucesso")
        log.info("jira project_key      = %s", settings.jira_project_key)
        log.info(
            "confluence configurado = %s",
            bool(settings.confluence_base_url and settings.confluence_space_key),
        )
        log.info("slack configurado     = %s", bool(settings.slack_webhook_url))
        log.info(
            "LLM provider          = %s (model=%s)",
            settings.llm_provider, settings.llm_model,
        )
        log.info("LLM base_url          = %s", settings.llm_base_url)
        log.info("estratégia cluster    = %s", settings.cluster_strategy)
        return 0

    if args.command == "fetch-gitbook":
        verbose = getattr(args, "verbose", False)
        narrator = Narrator(enabled=not verbose)
        if not verbose:
            logging.getLogger("kiro").setLevel(logging.ERROR)

        print_banner()
        narrator.section("GitBook · fetch público")

        try:
            result = scrape_public_gitbook(
                base_url=settings.gitbook_public_url,
                output_path=settings.gitbook_cache_path,
                narrator=narrator,
                request_delay_seconds=settings.gitbook_request_delay_seconds,
            )
        except ValueError as e:
            narrator.fail(str(e))
            return 1

        narrator.done(
            f"{result.pages_fetched} páginas baixadas, "
            f"{len(result.failed_urls)} falhas"
        )
        narrator.done(
            f"{result.chunks_written} chunks salvos em {result.output_path}"
        )
        if verbose and result.failed_urls:
            narrator.warn("URLs que falharam:")
            for url in result.failed_urls:
                narrator.info(f"  {url}")
        return 0 if result.chunks_written > 0 else 1

    if args.command == "run":
        dry_run = bool(args.dry_run or settings.dry_run)
        publish_conf = (
            args.publish_confluence or settings.enable_confluence_publish
        ) and not dry_run
        notify_slack = (
            args.notify_slack or settings.enable_slack_notify
        ) and not dry_run

        if publish_conf and not (
            settings.confluence_base_url and settings.confluence_space_key
        ):
            log.error(
                "--publish-confluence exige CONFLUENCE_BASE_URL e CONFLUENCE_SPACE_KEY"
            )
            return 2
        if notify_slack and not settings.slack_webhook_url:
            log.error("--notify-slack exige SLACK_WEBHOOK_URL")
            return 2

        # Modo visual (default) usa narrator + silencia logs INFO/WARNING do KIRO.
        # WARNING fica escondido pra não interromper o spinner durante retries.
        # Erros que merecem atenção (falha definitiva de cluster, etc.) vêm
        # pelo próprio narrator com `fail()`. Modo --verbose mostra tudo.
        verbose = getattr(args, "verbose", False)
        narrator = Narrator(enabled=not verbose)
        if not verbose:
            logging.getLogger("kiro").setLevel(logging.ERROR)

        try:
            pipeline = _build_pipeline(
                settings, args.output_dir, dry_run=dry_run, narrator=narrator
            )
        except ConfigError as e:
            log.error("falha ao montar pipeline: %s", e)
            return 2

        import time
        print_banner()

        # Estilo: --style tem prioridade; senão pergunta interativamente.
        style = args.style or _ask_style_interactively()

        req = PipelineRequest(
            stages=_stages_for(args.stage),
            dry_run=dry_run,
            publish_confluence=publish_conf,
            notify_slack=notify_slack,
            style=style,
        )
        if verbose:
            log.info(
                "iniciando pipeline: stages=%s dry_run=%s confluence=%s slack=%s",
                req.stages, req.dry_run, req.publish_confluence, req.notify_slack,
            )
        started = time.monotonic()
        result = pipeline.run(req)
        duration = time.monotonic() - started
        published_count = sum(
            1 for r in result.publish_results if r.succeeded and r.confluence_url
        )
        artifacts_dir = str((result.artifacts_dir or Path()).resolve())
        print_footer(
            tickets=len(result.tickets),
            clusters=len(result.clusters),
            articles=len(result.articles),
            customer_faqs=len(result.customer_faqs),
            published=published_count,
            errors=len(result.errors),
            duration_seconds=duration,
            artifacts_dir=artifacts_dir,
        )
        return 0 if not result.errors else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
