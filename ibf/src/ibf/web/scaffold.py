"""
Generate the directory/menu structure required for publishing forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from ..config import ForecastConfig
from ..util import slugify, write_text_file

DEFAULT_WEB_ROOT = Path("outputs/forecasts")

PLACEHOLDER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
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
  <p class="footer-note">For feedback email <a href="mailto:neil.gordon@hey.com?subject=Feedback%20On%20Ensemble%20Text%20Forecasts">Neil Gordon</a>.</p>
  <div class="footer-note">
    All forecasts &copy; Neil Gordon. Data courtesy of <a href="https://open-meteo.com/" target="_blank" rel="noopener">open-meteo.com</a>,
    using <a href="https://apps.ecmwf.int/datasets/licences/general/" target="_blank" rel="noopener">ECMWF ensemble open data</a>.
  </div>
</body>
</html>
"""


@dataclass
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

    # Locations
    location_entries: List[tuple[str, str]] = []
    for location in config.locations:
        slug = slugify(location.name)
        location_dir = root / slug
        ensure_directory(location_dir, report)
        write_placeholder(location_dir / "index.html", location.name, force, report)
        location_entries.append((slug, location.name.replace(", NZ", "")))

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

