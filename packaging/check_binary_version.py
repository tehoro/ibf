"""Verify that a packaged IBF executable reports the project release version."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_binary_version.py PATH_TO_IBF_EXECUTABLE")

    project_root = Path(__file__).resolve().parents[1]
    with (project_root / "pyproject.toml").open("rb") as handle:
        expected = tomllib.load(handle)["project"]["version"]

    completed = subprocess.run(
        [sys.argv[1], "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    if output:
        print(output)

    expected_text = f"ibf {expected}"
    if completed.returncode != 0 or expected_text not in output:
        raise SystemExit(
            f"packaged version check failed: expected {expected_text!r}, "
            f"exit code {completed.returncode}"
        )


if __name__ == "__main__":
    main()
