"""
Command line interface for the Impact-Based Forecast toolkit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ConfigError, ForecastConfig, load_config
from .pipeline import execute_pipeline
from .web import ScaffoldReport, generate_site_structure, resolve_web_root
from .maps import generate_area_maps
from .util import slugify

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


def _load_map_state(path: Path) -> tuple[Optional[str], Dict[str, str]]:
    if not path.exists():
        return None, {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None, {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw or None, {}
    config_hash = data.get("config_hash")
    raw_areas = data.get("areas", {})
    if not isinstance(raw_areas, dict):
        raw_areas = {}
    area_hashes = {slug: str(hash_value) for slug, hash_value in raw_areas.items()}
    return config_hash, area_hashes


def _write_map_state(path: Path, config_hash: str, area_hashes: Dict[str, str]) -> None:
    payload = {
        "config_hash": config_hash,
        "areas": area_hashes,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _area_points_hash(area) -> str:
    payload = {
        "name": area.name,
        "locations": area.locations,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _area_map_exists(web_root: Path, slug: str) -> bool:
    maps_dir = web_root / "maps"
    png = maps_dir / f"{slug}.png"
    html = maps_dir / f"{slug}.html"
    return png.exists() or html.exists()


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
    maps: bool = typer.Option(
        True,
        "--maps/--no-maps",
        help="Automatically rebuild area maps when the config hash changes.",
    ),
    force_maps: bool = typer.Option(
        False,
        "--force-maps",
        help="Regenerate area maps even if the cached config hash matches.",
    ),
    map_tiles: str = typer.Option(
        "osm",
        "--map-tiles",
        help="Tile set for automatic maps (osm, terrain, satellite).",
        case_sensitive=False,
    ),
    map_engine: str = typer.Option(
        "static",
        "--map-engine",
        help="Rendering engine for automatic maps (static, folium).",
        case_sensitive=False,
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

    if maps:
        if forecast_config.areas:
            web_root = resolve_web_root(forecast_config)
            state_file = web_root / ".ibf_maps_hash"
            _, previous_area_hashes = _load_map_state(state_file)
            current_area_hashes = {
                slugify(area.name): _area_points_hash(area) for area in forecast_config.areas
            }
            areas_to_regen: List[str] = []
            for area in forecast_config.areas:
                slug = slugify(area.name)
                points_hash = current_area_hashes[slug]
                has_outputs = _area_map_exists(web_root, slug)
                if (
                    force_maps
                    or not has_outputs
                    or previous_area_hashes.get(slug) != points_hash
                ):
                    areas_to_regen.append(area.name)

            if areas_to_regen:
                console.print(f"[yellow]Regenerating {len(areas_to_regen)} area map(s)...[/]")
                try:
                    report = generate_area_maps(
                        forecast_config,
                        output_dir=web_root,
                        area_filters=areas_to_regen,
                        tile_set=map_tiles,
                        engine=map_engine,
                    )
                except ValueError as exc:
                    console.print(f"[bold yellow]Map generation skipped:[/] {exc}")
                else:
                    for line in report.summary_lines():
                        console.print(f"- {line}")
                    if report.failures:
                        console.print("[bold red]Some maps failed to generate; will retry next run.[/]")
                        for name, reason in report.failures.items():
                            console.print(f"  • {name}: {reason}")
                        succeeded_hashes = current_area_hashes.copy()
                        for failed in report.failures:
                            slug = slugify(failed)
                            if slug in previous_area_hashes:
                                succeeded_hashes[slug] = previous_area_hashes[slug]
                            else:
                                succeeded_hashes.pop(slug, None)
                        _write_map_state(state_file, forecast_config.hash, succeeded_hashes)
                    else:
                        _write_map_state(state_file, forecast_config.hash, current_area_hashes)
            else:
                console.print("[green]Area maps already up to date (point lists unchanged).[/]")
                _write_map_state(state_file, forecast_config.hash, current_area_hashes)
        else:
            console.print("[yellow]No areas defined; skipping automatic map generation.[/]")

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


@app.command()
def maps(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="Path to the configuration JSON.",
        callback=_resolve_config_path,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory for rendered map files (defaults to <web_root>/maps).",
    ),
    area: List[str] = typer.Option(
        None,
        "--area",
        help="Only generate maps for these area names (multiple allowed).",
    ),
    tiles: str = typer.Option(
        "osm",
        "--tiles",
        help="Base tiles to use (osm, terrain, satellite).",
        case_sensitive=False,
    ),
    engine: str = typer.Option(
        "static",
        "--engine",
        help="Rendering engine (static, folium).",
        case_sensitive=False,
    ),
) -> None:
    """
    Generate static maps for all configured areas (or a subset).
    """
    forecast_config = _load_config_or_exit(config)
    filters = area or None
    try:
        report = generate_area_maps(
            forecast_config,
            output_dir=output,
            area_filters=filters,
            tile_set=tiles,
            engine=engine,
        )
    except ValueError as exc:
        console.print(f"[bold yellow]{exc}[/]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Map Generation Summary")
    table.add_column("Key")
    table.add_column("Value", overflow="fold")
    for line in report.summary_lines():
        key, _, value = line.partition(": ")
        table.add_row(key, value)
    console.print(table)
    if report.failures:
        console.print("[bold red]Some maps could not be generated:[/]")
        for name, reason in report.failures.items():
            console.print(f"- {name}: {reason}")


def main() -> None:
    """
    Entry-point used by the console script defined in pyproject.toml.
    """
    app()

