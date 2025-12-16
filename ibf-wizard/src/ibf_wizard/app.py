from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import dotenv_values
from nicegui import ui


try:
    from ibf.config.models import ForecastConfig, ConfigError

    HAS_IBF = True
except Exception:  # ibf not installed yet
    HAS_IBF = False

    class ConfigError(RuntimeError):
        """Placeholder error when ibf is not available."""

REQUIRED_KEYS = [
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "DEEP_INFRA_API_KEY",
    "OPENWEATHERMAP_API_KEY",
    "GOOGLE_API_KEY",
]


@dataclass
class WizardState:
    workspace: Path
    env_path: Path
    config_path: Path
    env_values: Dict[str, str] = field(default_factory=dict)
    config_text: str = "{}"
    config_data: Dict[str, Any] = field(default_factory=dict)


def _safe_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
    path.write_text(content, encoding="utf-8")


def load_env(env_path: Path) -> Dict[str, str]:
    values = dotenv_values(env_path)
    return {k: v for k, v in values.items() if v is not None}


def save_env(env_path: Path, data: Dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in data.items() if v]
    _safe_write(env_path, "\n".join(lines) + ("\n" if lines else ""))


def load_config_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def parse_config(text: str, ext: str) -> Dict[str, Any]:
    if not text.strip():
        return {}
    if ext.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text) or {}
    return json.loads(text)


def validate_config(data: Dict[str, Any]) -> ForecastConfig:
    if not HAS_IBF:
        raise ConfigError("ibf is not installed; install ibf to validate configs.")
    return ForecastConfig.model_validate(data)


def save_config(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        _safe_write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    else:
        _safe_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def dump_config_text(path: Path, data: Dict[str, Any]) -> str:
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return json.dumps(data, indent=2, ensure_ascii=False)


def sample_config_path(workspace: Path) -> Optional[Path]:
    candidate = workspace / "examples" / "sample-config.json"
    return candidate if candidate.exists() else None


def _load_config_into_state(state: WizardState, path: Path) -> str:
    """Load config file into both raw text and structured data."""
    state.config_path = path
    state.config_text = load_config_text(path)
    ext = path.suffix or ".json"
    if state.config_text.strip():
        try:
            state.config_data = parse_config(state.config_text, ext)
            return f"Loaded {path}"
        except Exception as exc:
            state.config_data = {}
            return f"Loaded text from {path} but could not parse: {exc}"
    state.config_data = {}
    return f"New file {path}"


def check_command_available(cmd: List[str], cwd: Optional[Path] = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=True)
        return True, (result.stdout.strip() or "ok")
    except FileNotFoundError:
        return False, "not found"
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr.strip() or exc.stdout.strip() or str(exc)


def check_prereqs(workspace: Path) -> List[tuple[str, bool, str]]:
    checks = []
    ok, msg = check_command_available(["uv", "--version"])
    checks.append(("uv", ok, msg))
    ok_ibf, msg_ibf = check_command_available(["uv", "run", "ibf", "--version"], cwd=workspace)
    checks.append(("ibf (via uv)", ok_ibf, msg_ibf))
    terrain = workspace / "assets" / "terrain" / "global_terrain_ultra_compressed.pkl.gz"
    checks.append(("terrain dataset", terrain.exists(), str(terrain) if terrain.exists() else "missing"))
    return checks


async def run_pipeline(workspace: Path, config: Path, log_component) -> int:
    cmd = ["uv", "run", "ibf", "run", "--config", str(config)]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        log_component.push("uv not found on PATH.")
        return 127

    log_component.push(f"Running: {' '.join(cmd)}")
    assert process.stdout is not None
    async for raw in process.stdout:
        log_component.push(raw.decode().rstrip())
    rc = await process.wait()
    log_component.push(f"Exit code: {rc}")
    return rc


def _env_editor(state: WizardState):
    state.env_values = load_env(state.env_path)

    with ui.card().style("width: 100%;"):
        ui.label(".env editor").classes("text-h6")
        ui.markdown(
            "Paste or type your API keys here. Values stay local on this machine. "
            "Toggle visibility to verify what you pasted. Only non-empty values are written."
        ).classes("text-body2")

        show_values = ui.checkbox("Show values", value=False)

        rows = []
        for key in REQUIRED_KEYS:
            value = state.env_values.get(key, "")
            row = ui.input(key, value=value, password=True).classes("w-full")
            # Ensure initial state matches checkbox
            row.props(f'type={"text" if show_values.value else "password"}')
            rows.append((key, row))

        status = ui.label("")

        def _toggle_visibility():
            for _, field in rows:
                field.props(f'type={"text" if show_values.value else "password"}')
                field.update()

        def save():
            for key, field in rows:
                if field.value:
                    state.env_values[key] = field.value
                elif key in state.env_values:
                    state.env_values.pop(key)
            save_env(state.env_path, state.env_values)
            status.text = f"Saved to {state.env_path}"

        show_values.on_value_change(lambda _: _toggle_visibility())
        ui.button("Save .env", on_click=save)
        status.classes("text-positive" if status.text else "")


def _config_editor(state: WizardState):
    from .config_form import _config_form_editor

    _load_config_into_state(state, state.config_path)

    with ui.card().style("width: 100%;"):
        ui.label("Config").classes("text-h6")
        ui.markdown(
            "Use the guided form for settings, locations, and areas. Validation requires IBF installed, "
            "but you can still save without validation. Loading/saving works on the current config path."
        ).classes("text-body2")
        _config_form_editor(state)


def _prereq_panel(state: WizardState):
    with ui.card().style("width: 100%;"):
        ui.label("Prerequisite checks").classes("text-h6")
        columns = [
            {"name": "check", "label": "Check", "field": "check", "sortable": False},
            {"name": "status", "label": "Status", "field": "status", "sortable": False},
            {"name": "details", "label": "Details", "field": "details", "sortable": False},
        ]
        table = ui.table(columns=columns, rows=[], row_key="check").classes("w-full")

        def refresh():
            rows = []
            for name, ok, detail in check_prereqs(state.workspace):
                rows.append({"check": name, "status": "ok" if ok else "missing", "details": detail})
            table.rows = rows

        ui.button("Refresh", on_click=refresh)
        refresh()


def _run_helper(state: WizardState):
    with ui.card().style("width: 100%;"):
        ui.label("Run helper (uv run ibf run)").classes("text-h6")
        config_path_input = ui.input("Config path", value=str(state.config_path)).classes("w-full")
        log = ui.log().classes("w-full")

        async def start():
            if not HAS_IBF:
                log.push("ibf is not installed. Install ibf first, then retry.")
                return
            path = Path(config_path_input.value).expanduser()
            if not path.exists():
                log.push(f"Config not found: {path}")
                return
            state.config_path = path
            await run_pipeline(state.workspace, path, log)

        ui.button("Run pipeline", on_click=lambda: ui.run_task(start()))


def run_app(workspace: Path, host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True) -> None:
    workspace = workspace.expanduser().resolve()
    state = WizardState(
        workspace=workspace,
        env_path=workspace / ".env",
        config_path=workspace / "config.json",
    )

    with ui.header():
        ui.label("IBF Wizard").classes("text-h5")
        ui.label(f"Workspace: {workspace}")

    with ui.tabs() as tabs:
        prereq_tab = ui.tab("Prereqs")
        env_tab = ui.tab(".env")
        config_tab = ui.tab("Config")
        run_tab = ui.tab("Run")

    with ui.tab_panels(tabs, value=prereq_tab):
        with ui.tab_panel(prereq_tab):
            _prereq_panel(state)
        with ui.tab_panel(env_tab):
            _env_editor(state)
        with ui.tab_panel(config_tab):
            _config_editor(state)
        with ui.tab_panel(run_tab):
            _run_helper(state)

    ui.run(
        title="IBF Wizard",
        host=host,
        port=port,
        reload=False,
        show=open_browser,
    )
