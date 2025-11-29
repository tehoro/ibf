"""
Command line interface for the Impact-Based Forecast toolkit.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ConfigError, ForecastConfig, load_config
from .pipeline import execute_pipeline
from .web import ScaffoldReport, generate_site_structure

console = Console()
app = typer.Typer(help="Run and manage the unified Impact-Based Forecast workflow.")
logger = logging.getLogger(__name__)

LOG_LEVELS = ["critical", "error", "warning", "info", "debug"]


def _configure_logging(level_name: str) -> None:
    env_override = os.getenv("IBF_LOG_LEVEL")
    level_str = (env_override or level_name or "info").upper()
    if level_str not in {lvl.upper() for lvl in LOG_LEVELS}:
        level_str = "INFO"
    logging.basicConfig(
        level=getattr(logging, level_str, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.debug("Logging configured at %s", level_str)


def _resolve_config_path(value: Path) -> Path:
    """Ensure config path exists and return absolute path."""
    resolved = value.expanduser().resolve()
    if not resolved.exists():
        raise typer.BadParameter(f"No config file found at {resolved}")
    if not resolved.is_file():
        raise typer.BadParameter(f"Config path must be a file, got directory: {resolved}")
    return resolved


def _load_config_or_exit(path: Path) -> ForecastConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        raise typer.Exit(code=1) from exc


def _print_scaffold_report(report: ScaffoldReport) -> None:
    table = Table(title="Scaffold Summary")
    table.add_column("Key")
    table.add_column("Value")
    for key, value in report.summary_rows():
        table.add_row(key, value)
    console.print(table)


@app.callback(invoke_without_command=True)
def _root_command(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show ibf version and exit.",
        is_flag=True,
    ),
    log_level: str = typer.Option(
        "info",
        "--log-level",
        help="Logging level (critical, error, warning, info, debug).",
        show_default=True,
        case_sensitive=False,
    ),
) -> None:
    """
    Default command when no subcommand is selected.

    Shows metadata so `ibf --version` works even before the run command is ready.
    """
    _configure_logging(log_level)

    if version:
        console.print(f"[bold green]ibf[/] {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        console.print(
            "[bold yellow]ibf[/] is ready. Run [cyan]ibf run --config path/to/config.json[/] "
            "once the pipeline is configured.",
        )


@app.command()
def run(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to the configuration JSON file.",
        callback=_resolve_config_path,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate configuration and show a plan without generating outputs.",
    ),
) -> None:
    """
    Validate the configuration and (eventually) execute the forecast workflow.
    """
    logger.info("Loading configuration from %s", config)
    forecast_config = _load_config_or_exit(config)
    logger.info("Loaded configuration with %d locations and %d areas", len(forecast_config.locations), len(forecast_config.areas))

    summary_table = Table(title="Forecast Configuration Summary")
    summary_table.add_column("Key")
    summary_table.add_column("Value", overflow="fold")
    summary_table.add_row("Locations", str(len(forecast_config.locations)))
    summary_table.add_row("Areas", str(len(forecast_config.areas)))
    summary_table.add_row("Web root", str(forecast_config.web_root) if forecast_config.web_root else "Not set")
    summary_table.add_row("Config hash", forecast_config.hash)

    console.print(summary_table)

    if dry_run:
        console.print("[bold blue]Dry run complete.[/] No filesystem changes made.")
        return

    logger.info("Ensuring web scaffolding at %s", forecast_config.web_root or "default outputs directory")
    scaffold_report = generate_site_structure(forecast_config, force=False)
    _print_scaffold_report(scaffold_report)

    console.print("[bold green]Scaffold up to date.[/]")
    console.print("[yellow]Running pipeline...[/]")
    logger.info("Starting pipeline execution")
    execute_pipeline(forecast_config)
    logger.info("Pipeline finished successfully")
    console.print("[bold green]Pipeline completed.[/]")


@app.command("config-hash")
def config_hash(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to configuration JSON.",
        callback=_resolve_config_path,
    ),
) -> None:
    """
    Output the deterministic hash of a config file for cache invalidation or change detection.
    """
    forecast_config = _load_config_or_exit(config)
    console.print(f"[bold green]{forecast_config.hash}[/]")


@app.command()
def scaffold(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to configuration JSON.",
        callback=_resolve_config_path,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Rewrite placeholder files even if they already exist.",
    ),
) -> None:
    """
    Create/refresh the directory structure and menu for the configured web root.
    """
    forecast_config = _load_config_or_exit(config)
    report = generate_site_structure(forecast_config, force=force)
    _print_scaffold_report(report)


def main() -> None:
    """
    Entry-point used by the console script defined in pyproject.toml.
    """
    app()

