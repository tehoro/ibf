from __future__ import annotations

"""
Generate static or interactive maps that visualize configured forecast areas.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

import folium
from staticmap import CircleMarker, StaticMap

from ..api import geocode_name
from ..config import ForecastConfig
from ..util import safe_unlink, slugify

logger = logging.getLogger(__name__)


@dataclass
class AreaMapReport:
    """
    Summary of the map generation process.

    Attributes:
        root: The directory where maps were saved.
        generated: Dictionary mapping area names to their generated map file paths.
        failures: Dictionary mapping area names to error messages for failed generations.
    """
    root: Path
    generated: Dict[str, Path] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)

    def summary_lines(self) -> Iterable[str]:
        yield f"Output directory: {self.root}"
        yield f"Maps created: {len(self.generated)}"
        if self.failures:
            yield f"Failed: {len(self.failures)}"


def generate_area_maps(
    config: ForecastConfig,
    *,
    output_dir: Optional[Path] = None,
    area_filters: Optional[Iterable[str]] = None,
    map_width: int = 1920,
    map_height: int = 1080,
    tile_set: str = "osm",
    engine: str = "static",
) -> AreaMapReport:
    """
    Generate visual maps for all configured areas.

    Can use either a static map engine (using `staticmap`) or a dynamic one
    (using `folium` + `selenium` for screenshots).

    Args:
        config: The forecast configuration.
        output_dir: Optional override for the output directory.
        area_filters: Optional list of area names to process (skips others).
        map_width: Width of the generated image in pixels.
        map_height: Height of the generated image in pixels.
        tile_set: "osm", "satellite", or "terrain".
        engine: "static" or "folium".

    Returns:
        An AreaMapReport summarizing the results.
    """
    if not config.areas:
        raise ValueError("No areas are defined in the configuration.")

    area_names = {name.lower() for name in area_filters} if area_filters else None

    root = Path(output_dir or (config.web_root or Path("outputs"))).expanduser().resolve() / "maps"
    root.mkdir(parents=True, exist_ok=True)

    report = AreaMapReport(root=root)

    for area in config.areas:
        if area_names and area.name.lower() not in area_names:
            continue

        try:
            figure_path = _build_area_map(
                area_name=area.name,
                locations=area.locations,
                destination=root,
                width=map_width,
                height=map_height,
                tile_set=tile_set,
                engine=engine,
            )
            if figure_path:
                report.generated[area.name] = figure_path
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error("Failed to generate map for %s: %s", area.name, exc, exc_info=True)
            report.failures[area.name] = str(exc)

    return report


def _build_area_map(
    *,
    area_name: str,
    locations: Iterable[str],
    destination: Path,
    width: int,
    height: int,
    tile_set: str,
    engine: str,
) -> Optional[Path]:
    """
    Create a map for a single area.

    Geocodes all locations in the area and plots them.

    Args:
        area_name: Name of the area.
        locations: List of location names to plot.
        destination: Directory to save the map.
        width: Image width.
        height: Image height.
        tile_set: Tile style.
        engine: Rendering engine ("static" or "folium").

    Returns:
        Path to the generated PNG file, or None if generation failed.
    """
    coordinates: Dict[str, tuple[float, float]] = {}

    for name in locations:
        result = geocode_name(name)
        if result:
            coordinates[name] = (result.latitude, result.longitude)
        else:
            logger.warning("Skipping %s in %s (unable to geocode)", name, area_name)

    if not coordinates:
        raise RuntimeError(f"No geocoded locations for area '{area_name}'.")

    safe_name = slugify(area_name)
    png_path = destination / f"{safe_name}.png"

    if engine == "static":
        if _render_static_png(png_path, coordinates, width, height, tile_set):
            logger.info("Saved static map for %s → %s", area_name, png_path)
            return png_path
        logger.warning("Static map engine failed for %s; falling back to folium screenshot.", area_name)

    # Folium fallback
    map_html = _render_folium_map(area_name, coordinates, tile_set=tile_set)
    if not map_html:
        raise RuntimeError(f"Unable to render folium map for '{area_name}'.")

    html_path = destination / f"{safe_name}.html"
    map_html.save(html_path)
    logger.info("Saved map HTML for %s → %s", area_name, html_path)

    if _html_to_png(html_path, png_path, width=width, height=height):
        logger.info("Saved folium PNG for %s → %s", area_name, png_path)
        safe_unlink(html_path, base_dir=destination)
        return png_path
    logger.warning("PNG conversion failed for %s (%s); keeping HTML only.", area_name, png_path)
    return html_path


def _render_static_png(
    png_path: Path,
    coordinates: Dict[str, tuple[float, float]],
    width: int,
    height: int,
    tile_set: str,
) -> bool:
    """
    Render a map using the `staticmap` library (no browser required).

    Args:
        png_path: Target file path.
        coordinates: Dictionary of {name: (lat, lon)}.
        width: Image width.
        height: Image height.
        tile_set: Tile style.

    Returns:
        True if successful, False otherwise.
    """
    try:
        tile_url = _static_tile_template(tile_set)
        static_map = StaticMap(width, height, url_template=tile_url)
        for name, (lat, lon) in coordinates.items():
            marker = CircleMarker((lon, lat), "#d9534f", 12)
            static_map.add_marker(marker)
        image = static_map.render()
        image.save(png_path, format="PNG")
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Static map rendering failed: %s", exc)
        return False


def _render_folium_map(
    area_name: str,
    coordinates: Dict[str, tuple[float, float]],
    *,
    tile_set: str = "osm",
) -> Optional[folium.Map]:
    """
    Create a Folium (Leaflet) map object with markers.

    Args:
        area_name: Name of the area (used in title).
        coordinates: Dictionary of {name: (lat, lon)}.
        tile_set: Tile style.

    Returns:
        A folium.Map object, or None if no coordinates provided.
    """
    if not coordinates:
        return None
    lats = [lat for lat, _ in coordinates.values()]
    lons = [lon for _, lon in coordinates.values()]
    center = [sum(lats) / len(lats), sum(lons) / len(lons)]

    tile_layers = _resolve_tile_layers(tile_set)
    base_tiles, extras = tile_layers[0], tile_layers[1:]
    fmap = folium.Map(location=center, tiles=base_tiles["tiles"], attr=base_tiles["attr"], zoom_start=7)
    for layer in extras:
        folium.TileLayer(layer["tiles"], name=layer["name"], attr=layer["attr"], overlay=False).add_to(fmap)
    if extras:
        folium.LayerControl().add_to(fmap)

    for label, (lat, lon) in coordinates.items():
        folium.Marker(
            [lat, lon],
            tooltip=label,
            icon=folium.Icon(color="red", icon="info-sign"),
        ).add_to(fmap)

    if len(coordinates) > 1:
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(20, 20))

    title = f"""
    <div style="position:absolute; top:10px; left:50px; width:320px; background-color: white;
                border:2px solid #888; border-radius:6px; padding:12px; z-index:9999;
                font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; font-size:14px;">
      <strong>{area_name}</strong><br/>
      {len(coordinates)} locations plotted
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title))
    return fmap


def _html_to_png(html_path: Path, png_path: Path, *, width: int, height: int) -> bool:
    """
    Convert an HTML map file to a PNG image using Selenium (headless Chrome).

    Args:
        html_path: Path to the source HTML file.
        png_path: Path to the destination PNG file.
        width: Browser window width.
        height: Browser window height.

    Returns:
        True if successful, False if Selenium is missing or fails.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        logger.warning("selenium is required to render PNG maps (%s)", exc)
        return False

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={width},{height}")
    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.get(html_path.as_uri())
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "leaflet-container")))
        driver.save_screenshot(str(png_path))
        return True
    finally:
        if driver:
            driver.quit()


def _resolve_tile_layers(tile_set: str) -> list[dict]:
    """Return the ordered list of tile layer definitions for folium maps."""
    tile_set = tile_set.lower()
    if tile_set == "satellite":
        return [
            {
                "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                "attr": "Esri",
                "name": "Satellite",
            },
            {
                "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attr": "© OpenStreetMap contributors",
                "name": "OpenStreetMap",
            },
        ]
    if tile_set == "terrain":
        return [
            {
                "tiles": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
                "attr": "© OpenTopoMap (CC-BY-SA)",
                "name": "Terrain",
            },
            {
                "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attr": "© OpenStreetMap contributors",
                "name": "OpenStreetMap",
            },
        ]
    # default OSM
    return [
        {
            "tiles": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attr": "© OpenStreetMap contributors",
            "name": "OpenStreetMap",
        }
    ]


def _static_tile_template(tile_set: str) -> str:
    """Map friendly tile set names to the `staticmap` URL templates."""
    tile_set = tile_set.lower()
    if tile_set == "terrain":
        return "https://tile.opentopomap.org/{z}/{x}/{y}.png"
    if tile_set == "satellite":
        return "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    return "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
