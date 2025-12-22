import os
from pathlib import Path

from ibf.config import settings


def test_project_dotenv_overrides_environment(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_env = project_dir / ".env"

    project_dir.mkdir()
    project_env.write_text("GEMINI_API_KEY=project\n", encoding="utf-8")

    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("GEMINI_API_KEY", "env-value")

    settings._load_dotenv()

    assert os.getenv("GEMINI_API_KEY") == "project"
