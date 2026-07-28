from pathlib import Path
import tomllib

import ibf


def test_embedded_version_matches_project_version() -> None:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    assert ibf._EMBEDDED_VERSION == project_version


def test_version_falls_back_to_embedded_version(monkeypatch) -> None:
    def missing_distribution(_name: str) -> str:
        raise ibf._metadata.PackageNotFoundError("ibf")

    monkeypatch.setattr(ibf._metadata, "version", missing_distribution)

    assert ibf._resolve_version() == ibf._EMBEDDED_VERSION
