"""
Generate the directory/menu structure required for publishing forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from ..config import ForecastConfig
from ..util import slugify, write_text_file
from ..util.naming import generate_unique_location_names
from ..api import resolve_model_spec, DEFAULT_ENSEMBLE_MODEL

DEFAULT_WEB_ROOT = Path("outputs/forecasts")
FAVICON_FILENAME = "favicon.svg"
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="IBF favicon">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#bfe6ff"/>
      <stop offset="1" stop-color="#4aa3ff"/>
    </linearGradient>

    <linearGradient id="gloss" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.55"/>
      <stop offset="0.55" stop-color="#ffffff" stop-opacity="0.12"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <g transform="translate(32 32) scale(1.15) translate(-32 -32)">
    <ellipse cx="32" cy="32" rx="27" ry="22" fill="url(#sky)"/>
    <ellipse cx="32" cy="32" rx="27" ry="22" fill="none" stroke="#0a2a43" stroke-width="3"/>

    <path d="M11 27 C15 15, 28 10, 40 12 C28 14, 19 19, 14 29 C13 31, 11 30, 11 27 Z"
          fill="url(#gloss)"/>

    <g transform="translate(17 39)">
      <path d="M8 8
               C4.5 8 1.8 5.4 1.8 2.3
               C1.8 -0.2 3.6 -2.4 6.1 -3
               C7.2 -6 10.1 -8 13.7 -8
               C18.2 -8 21.9 -4.6 21.9 0
               C24.7 0.4 26.8 2.6 26.8 5.1
               C26.8 7.8 24.5 10 21.6 10
               L8 10 Z"
            fill="#ffffff" opacity="0.95"/>
    </g>

    <text x="32" y="33"
          text-anchor="middle"
          dominant-baseline="middle"
          font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif"
          font-size="20"
          font-weight="800"
          fill="#0a2a43"
          letter-spacing="1">
      IBF
    </text>
  </g>
</svg>
"""

PLACEHOLDER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml" sizes="any">
  <title>Forecast for {title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           background: #f7f7f7; color: #333; margin: 0 auto; padding: 20px; max-width: 800px; }}
    h1 {{ color: #2F4F4F; margin-top: 0; }}
    #forecast-content {{ background: #ffffff; padding: 20px; border: 1px solid #ccc; border-radius: 5px;
                        white-space: pre-wrap; word-wrap: break-word; line-height: 1.4em; }}
    a {{ color: #0066cc; text-decoration: none; font-weight: bold; }}
    a:hover {{ text-decoration: underline; }}
    .footer-note {{ margin-top: 20px; font-size: 0.9em; color: #666; }}
  </style>
</head>
<body>
  <h1>Forecast for {title}</h1>
  <div id="forecast-content">
    <p>Forecast will be updated here.</p>
  </div>
  <p><a href="../index.html">Return to Menu</a></p>
</body>
</html>
"""

MENU_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="icon" href="favicon.svg" type="image/svg+xml" sizes="any">
  <title>Weather Forecast Menu</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           background: #f7f7f7; color: #333; margin: 0 auto; padding: 20px; max-width: 800px; }}
    h1, h2 {{ color: #2F4F4F; margin-top: 1.5em; margin-bottom: 0.5em; }}
    h1 {{ margin-top: 0; }}
    ul {{ list-style-type: none; padding: 0; }}
    li {{ margin: 10px 0; font-size: 18px; }}
    a {{ color: #0066cc; text-decoration: none; font-weight: bold; }}
    a:hover {{ text-decoration: underline; }}
    hr {{ margin: 2em 0; border: 0; border-top: 1px solid #ccc; }}
    .footer-note {{ margin-top: 20px; font-size: 0.9em; color: #666; text-align: center; }}
    .footer-note a {{ font-weight: normal; }}
  </style>
</head>
<body>
  <h1>Weather Forecast Menu</h1>
  {location_section}
  {area_section}
  <hr>
  <div class="footer-note">
    Forecast produced using <a href="https://github.com/tehoro/ibf" target="_blank" rel="noopener">IBF</a>, developed by <a href="mailto:neil.gordon@hey.com?subject=Comment%20on%20IBF">Neil Gordon</a>.
    Data courtesy of <a href="https://open-meteo.com/" target="_blank" rel="noopener">open-meteo.com</a>.
    <br>If you want to interactively request a forecast for a location, visit the <a href="https://chatgpt.com/g/g-4OgZFHOPA-global-ensemble-weather-forecaster" target="_blank" rel="noopener">Global Ensemble Weather Forecaster</a> (ChatGPT account required).
  </div>
</body>
</html>
"""


@dataclass
class ScaffoldReport:
    """
    Stores what changed when scaffolding ran.

    Attributes:
        root: The root directory of the web output.
        directories_created: List of newly created folders.
        placeholders_written: List of newly created placeholder files.
        placeholders_skipped: List of skipped files (already existed).
        menu_written: True if the main index.html was updated.
    """
    root: Path
    directories_created: List[Path] = field(default_factory=list)
    placeholders_written: List[Path] = field(default_factory=list)
    placeholders_skipped: List[Path] = field(default_factory=list)
    menu_written: bool = False

    def summary_rows(self) -> Iterable[tuple[str, str]]:
        yield ("Root", str(self.root))
        yield ("Directories created", str(len(self.directories_created)))
        yield ("Placeholders written", str(len(self.placeholders_written)))
        yield ("Placeholders skipped", str(len(self.placeholders_skipped)))
        yield ("Menu updated", "yes" if self.menu_written else "no")


def resolve_web_root(config: ForecastConfig) -> Path:
    """
    Determine the absolute path to the web root directory.

    Args:
        config: The forecast configuration.

    Returns:
        Absolute Path object for the web root.
    """
    root = config.web_root or DEFAULT_WEB_ROOT
    return Path(root).expanduser().resolve()


def ensure_directory(path: Path, report: ScaffoldReport) -> None:
    """
    Create a directory if it doesn't exist and record the action.

    Args:
        path: Directory path to create.
        report: Report object to update.
    """
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        report.directories_created.append(path)


def write_favicon(root: Path, force: bool) -> None:
    """
    Write the default favicon to the web root unless it already exists.
    """
    target = root / FAVICON_FILENAME
    if target.exists() and not force:
        return
    write_text_file(target, FAVICON_SVG)


def write_placeholder(target: Path, title: str, force: bool, report: ScaffoldReport) -> None:
    """
    Write a placeholder HTML file if it doesn't exist or if forced.

    Args:
        target: Path to the HTML file.
        title: Title for the placeholder page.
        force: If True, overwrite existing files.
        report: Report object to update.
    """
    if target.exists() and not force:
        report.placeholders_skipped.append(target)
        return
    write_text_file(target, PLACEHOLDER_TEMPLATE.format(title=title))
    report.placeholders_written.append(target)


def build_menu_section(title: str, entries: Iterable[tuple[str, str]]) -> str:
    """
    Generate an HTML list for a section of the menu.

    Args:
        title: Section header (e.g., "Locations").
        entries: List of (slug, label) tuples.

    Returns:
        HTML string for the section.
    """
    items = "\n".join(f'    <li><a href="{slug}/index.html">{label}</a></li>' for slug, label in entries)
    if not items:
        return ""
    return f"<h2>{title}</h2>\n<ul>\n{items}\n</ul>"


def _resolve_model_spec_for_location(location, config: ForecastConfig):
    """Helper to resolve model spec for a location (similar to executor's version)."""
    candidate = getattr(location, "model", None)
    if not candidate:
        candidate = getattr(config, "model", None)
    if not candidate:
        candidate = f"ens:{DEFAULT_ENSEMBLE_MODEL}"
    return resolve_model_spec(str(candidate))


def generate_site_structure(config: ForecastConfig, *, force: bool = False) -> ScaffoldReport:
    """
    Ensure the menu + placeholder directories exist for every location and area.

    Creates the root directory, subdirectories for each location/area, and a main
    index.html menu linking to them.

    Args:
        config: The forecast configuration.
        force: If True, overwrite existing placeholder files.

    Returns:
        A ScaffoldReport detailing the actions taken.
    """
    root = resolve_web_root(config)
    report = ScaffoldReport(root=root)
    ensure_directory(root, report)
    write_favicon(root, force)

    # Locations - generate unique names to avoid conflicts
    location_names = [location.name for location in config.locations]
    location_kinds = [
        _resolve_model_spec_for_location(location, config).kind
        for location in config.locations
    ]
    unique_names = generate_unique_location_names(location_names, location_kinds)
    location_entries: List[tuple[str, str]] = []
    for i, location in enumerate(config.locations):
        unique_name = unique_names[i]
        slug = slugify(unique_name)
        location_dir = root / slug
        ensure_directory(location_dir, report)
        write_placeholder(location_dir / "index.html", unique_name, force, report)
        display_label = unique_name.replace(", NZ", "")
        location_entries.append((slug, display_label))

    # Areas
    area_entries: List[tuple[str, str]] = []
    for area in config.areas:
        slug = slugify(area.name)
        area_dir = root / slug
        ensure_directory(area_dir, report)
        write_placeholder(area_dir / "index.html", area.name, force, report)
        area_entries.append((slug, area.name))

    location_section = build_menu_section("Locations", location_entries)
    area_section = build_menu_section("Areas", area_entries)

    index_html = MENU_TEMPLATE.format(
        location_section=location_section or "<p>No individual locations configured.</p>",
        area_section=area_section or "<p>No areas configured.</p>",
    )
    write_text_file(root / "index.html", index_html)
    report.menu_written = True

    return report
