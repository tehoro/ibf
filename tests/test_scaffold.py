from pathlib import Path

from ibf.config import ForecastConfig, LocationConfig
from ibf.web.scaffold import generate_site_structure
from ibf.util import slugify


def test_scaffold_uses_unique_location_names(tmp_path: Path) -> None:
    web_root = tmp_path / "site"
    config = ForecastConfig(
        web_root=web_root,
        model="ens:ecmwf_ifs025",
        locations=[
            LocationConfig(name="Duplicate City", model="ens:ecmwf_ifs025"),
            LocationConfig(name="Duplicate City", model="det:ecmwf_ifs"),
        ],
    )

    generate_site_structure(config)

    det_slug = slugify("Duplicate City (Deterministic)")
    ens_slug = slugify("Duplicate City (Ensemble)")

    assert (web_root / det_slug / "index.html").exists()
    assert (web_root / ens_slug / "index.html").exists()

    menu_html = (web_root / "index.html").read_text(encoding="utf-8")
    assert "Duplicate City (Deterministic)" in menu_html
    assert "Duplicate City (Ensemble)" in menu_html
