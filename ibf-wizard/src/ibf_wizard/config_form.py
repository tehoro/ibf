from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from nicegui import ui

from .app import (
    HAS_IBF,
    ConfigError,
    dump_config_text,
    parse_config,
    save_config,
    sample_config_path,
    validate_config,
    _load_config_into_state,
)

SUPPORTED_CONFIG_SUFFIXES = {".json"}


def _int_or_none(value: str) -> int | None:
    value = value.strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _update_top(config: Dict[str, Any], key: str, value: Any) -> None:
    if value in ("", None):
        config.pop(key, None)
    else:
        config[key] = value


def _config_dir(workspace: Path) -> Path:
    return (workspace / "config").expanduser().resolve()


def _list_config_files(workspace: Path) -> list[Path]:
    config_dir = _config_dir(workspace)
    config_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for path in config_dir.iterdir():
        if path.suffix.lower() in SUPPORTED_CONFIG_SUFFIXES and path.is_file():
            files.append(path)
    return sorted(files)


def _render_locations(container, state):
    container.clear()
    locations: List[Dict[str, Any]] = state.config_data.get("locations", [])

    def add_location():
        state.config_data.setdefault("locations", []).append({"name": "New location"})
        _render_locations(container, state)

    for idx, loc in enumerate(locations):
        with container:
            with ui.card().classes("w-full"):
                ui.label(f"Location {idx + 1}").classes("text-subtitle2")
                name = ui.input("Name", value=loc.get("name", "")).classes("w-full")
                lang = ui.input("Language (e.g., en)", value=loc.get("lang", "")).classes("w-full")
                translation_language = ui.input("Translation language", value=loc.get("translation_language", "")).classes("w-full")
                model = ui.input("Model override", value=loc.get("model", "")).classes("w-full")
                snow = ui.checkbox("Snow level", value=bool(loc.get("snow_level", False)))

                temp_unit = ui.input("Temp unit", value=loc.get("units", {}).get("temperature_unit", "")).classes("w-full")
                precip_unit = ui.input("Precip unit", value=loc.get("units", {}).get("precipitation_unit", "")).classes("w-full")
                wind_unit = ui.input("Wind unit", value=loc.get("units", {}).get("windspeed_unit", "")).classes("w-full")

                def persist():
                    loc["name"] = name.value
                    _update_top(loc, "lang", lang.value)
                    _update_top(loc, "translation_language", translation_language.value)
                    _update_top(loc, "model", model.value)
                    loc["snow_level"] = bool(snow.value)
                    units = {}
                    if temp_unit.value:
                        units["temperature_unit"] = temp_unit.value
                    if precip_unit.value:
                        units["precipitation_unit"] = precip_unit.value
                    if wind_unit.value:
                        units["windspeed_unit"] = wind_unit.value
                    loc["units"] = units

                def remove():
                    locations.pop(idx)
                    _render_locations(container, state)

                with ui.row():
                    ui.button("Save location", on_click=persist)
                    ui.button("Remove", on_click=remove, color="negative")

    ui.button("Add location", on_click=add_location)


def _render_areas(container, state):
    container.clear()
    areas: List[Dict[str, Any]] = state.config_data.get("areas", [])

    def add_area():
        state.config_data.setdefault("areas", []).append({"name": "New area", "locations": []})
        _render_areas(container, state)

    for idx, area in enumerate(areas):
        with container:
            with ui.card().classes("w-full"):
                ui.label(f"Area {idx + 1}").classes("text-subtitle2")
                name = ui.input("Name", value=area.get("name", "")).classes("w-full")
                locations = ui.input(
                    "Locations (comma separated names matching the Locations list)",
                    value=", ".join(area.get("locations", [])),
                ).classes("w-full")
                lang = ui.input("Language (e.g., en)", value=area.get("lang", "")).classes("w-full")
                translation_language = ui.input("Translation language", value=area.get("translation_language", "")).classes("w-full")
                mode = ui.select(["area", "regional"], value=area.get("mode", "area"), label="Mode").classes("w-full")
                model = ui.input("Model override", value=area.get("model", "")).classes("w-full")
                snow = ui.checkbox("Snow level", value=bool(area.get("snow_level", False)))

                temp_unit = ui.input("Temp unit", value=area.get("units", {}).get("temperature_unit", "")).classes("w-full")
                precip_unit = ui.input("Precip unit", value=area.get("units", {}).get("precipitation_unit", "")).classes("w-full")
                wind_unit = ui.input("Wind unit", value=area.get("units", {}).get("windspeed_unit", "")).classes("w-full")

                def persist():
                    area["name"] = name.value
                    area["locations"] = [s.strip() for s in locations.value.split(",") if s.strip()]
                    _update_top(area, "lang", lang.value)
                    _update_top(area, "translation_language", translation_language.value)
                    area["mode"] = mode.value or "area"
                    _update_top(area, "model", model.value)
                    area["snow_level"] = bool(snow.value)
                    units = {}
                    if temp_unit.value:
                        units["temperature_unit"] = temp_unit.value
                    if precip_unit.value:
                        units["precipitation_unit"] = precip_unit.value
                    if wind_unit.value:
                        units["windspeed_unit"] = wind_unit.value
                    area["units"] = units

                def remove():
                    areas.pop(idx)
                    _render_areas(container, state)

                with ui.row():
                    ui.button("Save area", on_click=persist)
                    ui.button("Remove", on_click=remove, color="negative")

    ui.button("Add area", on_click=add_area)


def _config_form_editor(state):
    cfg = state.config_data
    cfg.setdefault("locations", [])
    cfg.setdefault("areas", [])

    top_message = ui.label("").classes("text-caption")
    globals_refs = {}
    loc_container = {"ref": None}
    area_container = {"ref": None}
    path_label = ui.label(f"Current config: {state.config_path}").classes("text-caption")
    config_list = {"ref": None}
    selected_path = {"ref": None}

    def refresh_from_state():
        cfg_local = state.config_data
        cfg_local.setdefault("locations", [])
        cfg_local.setdefault("areas", [])
        if globals_refs:
            globals_refs["web_root"].value = str(cfg_local.get("web_root", "") or "")
            globals_refs["llm"].value = cfg_local.get("llm", "") or ""
            globals_refs["translation_llm"].value = cfg_local.get("translation_llm", "") or ""
            globals_refs["translation_language"].value = cfg_local.get("translation_language", "") or ""
            globals_refs["ensemble"].value = cfg_local.get("ensemble_model", "") or ""
            globals_refs["loc_days"].value = str(cfg_local.get("location_forecast_days", "") or "")
            globals_refs["area_days"].value = str(cfg_local.get("area_forecast_days", "") or "")
            globals_refs["loc_word"].value = cfg_local.get("location_wordiness")
            globals_refs["area_word"].value = cfg_local.get("area_wordiness")
            globals_refs["enable_reasoning"].value = bool(cfg_local.get("enable_reasoning", True))
            globals_refs["snow_enabled"].value = bool(cfg_local.get("snow_level_enabled", False))
            globals_refs["loc_thin"].value = str(cfg_local.get("location_thin_select", "") or "")
            globals_refs["area_thin"].value = str(cfg_local.get("area_thin_select", "") or "")
            globals_refs["recent_overwrite"].value = str(cfg_local.get("recent_overwrite_minutes", 0) or 0)
        if loc_container["ref"]:
            _render_locations(loc_container["ref"], state)
        if area_container["ref"]:
            _render_areas(area_container["ref"], state)
        path_label.text = f"Current config: {state.config_path}"
        refresh_config_list()

    def refresh_config_list():
        rows = []
        for fpath in _list_config_files(state.workspace):
            rows.append({"name": fpath.name, "path": str(fpath)})
        if config_list["ref"]:
            config_list["ref"].rows = rows
        if selected_path["ref"]:
            if rows:
                selected_path["ref"].value = rows[0]["path"]
            else:
                selected_path["ref"].value = str(state.config_path)

    def load_selected():
        if not selected_path["ref"]:
            return
        path_str = selected_path["ref"].value
        if not path_str:
            top_message.text = "No file selected."
            return
        path = Path(path_str).expanduser()
        if path.suffix.lower() not in SUPPORTED_CONFIG_SUFFIXES:
            top_message.text = "Only .json configs are supported."
            return
        note = _load_config_into_state(state, path)
        top_message.text = note
        refresh_from_state()

    def save_form():
        try:
            save_config(state.config_path, state.config_data)
            state.config_text = dump_config_text(state.config_path, state.config_data)
            top_message.text = f"Saved {state.config_path}"
        except Exception as exc:
            top_message.text = f"Save failed: {exc}"

    def validate_form():
        if not HAS_IBF:
            top_message.text = "Install ibf to validate."
            return
        try:
            cfg_obj = validate_config(state.config_data)
            top_message.text = f"Valid config ({len(cfg_obj.locations)} locations, {len(cfg_obj.areas)} areas)"
            state.config_text = dump_config_text(state.config_path, state.config_data)
        except Exception as exc:
            top_message.text = f"Validation error: {exc}"

    def load_sample():
        sample = sample_config_path(state.workspace)
        if sample:
            note = _load_config_into_state(state, sample)
            top_message.text = f"{note} (sample)"
            refresh_from_state()
        else:
            top_message.text = "No sample-config.json found."

    def load_from_file():
        note = _load_config_into_state(state, state.config_path)
        top_message.text = note
        refresh_from_state()

    with ui.card().style("width: 100%;"):
        ui.label("Guided config form").classes("text-subtitle1")
        ui.markdown(
            "Fill in global settings, then manage locations and areas. Use the buttons below to load the sample, "
            "reload the current file, save, or validate (requires IBF installed)."
        ).classes("text-body2")

        with ui.row():
            ui.button("Load sample", on_click=load_sample)
            ui.button("Reload current file", on_click=load_from_file)
            ui.button("Save", on_click=save_form)
            validate_btn = ui.button("Validate", on_click=validate_form)
            if not HAS_IBF:
                validate_btn.disable()

        with ui.row():
            selected_path["ref"] = ui.input("Selected file", value=str(state.config_path)).classes("w-full")
            ui.button("Load selected", on_click=load_selected)
            ui.button("Save as selected", on_click=lambda: (setattr(state, "config_path", Path(selected_path["ref"].value)), save_form(), refresh_from_state()))

        with ui.row():
            ui.button("Refresh config list", on_click=refresh_config_list)
            config_list["ref"] = ui.table(
                columns=[
                    {"name": "name", "label": "Name", "field": "name"},
                    {"name": "path", "label": "Path", "field": "path"},
                ],
                rows=[],
                row_key="path",
            )
            config_list["ref"].on(
                "rowClick",
                lambda e: selected_path["ref"].set_value(e.args["row"]["path"]) if selected_path["ref"] else None,
            )

        top_message.text = ""
        top_message.classes("text-caption")

        with ui.expansion("Global settings", value=True).classes("w-full"):
            web_root = ui.input("web_root", value=str(cfg.get("web_root", "") or "")).classes("w-full")
            llm = ui.input("llm", value=cfg.get("llm", "") or "").classes("w-full")
            translation_llm = ui.input("translation_llm", value=cfg.get("translation_llm", "") or "").classes("w-full")
            translation_language = ui.input("translation_language", value=cfg.get("translation_language", "") or "").classes("w-full")
            ensemble = ui.input("ensemble_model", value=cfg.get("ensemble_model", "") or "").classes("w-full")
            loc_days = ui.input("location_forecast_days", value=str(cfg.get("location_forecast_days", "") or "")).classes("w-full")
            loc_days.props('type="number"')
            area_days = ui.input("area_forecast_days", value=str(cfg.get("area_forecast_days", "") or "")).classes("w-full")
            area_days.props('type="number"')
            loc_word = ui.select(["normal", "brief", "detailed"], value=cfg.get("location_wordiness"), label="location_wordiness").classes("w-full")
            area_word = ui.select(["normal", "brief", "detailed"], value=cfg.get("area_wordiness"), label="area_wordiness").classes("w-full")
            enable_reasoning = ui.checkbox("enable_reasoning", value=bool(cfg.get("enable_reasoning", True)))
            snow_enabled = ui.checkbox("snow_level_enabled", value=bool(cfg.get("snow_level_enabled", False)))
            loc_thin = ui.input("location_thin_select", value=str(cfg.get("location_thin_select", "") or "")).classes("w-full")
            loc_thin.props('type="number"')
            area_thin = ui.input("area_thin_select", value=str(cfg.get("area_thin_select", "") or "")).classes("w-full")
            area_thin.props('type="number"')
            recent_overwrite = ui.input("recent_overwrite_minutes", value=str(cfg.get("recent_overwrite_minutes", 0) or 0)).classes("w-full")
            recent_overwrite.props('type="number"')

            globals_refs.update(
                {
                    "web_root": web_root,
                    "llm": llm,
                    "translation_llm": translation_llm,
                    "translation_language": translation_language,
                    "ensemble": ensemble,
                    "loc_days": loc_days,
                    "area_days": area_days,
                    "loc_word": loc_word,
                    "area_word": area_word,
                    "enable_reasoning": enable_reasoning,
                    "snow_enabled": snow_enabled,
                    "loc_thin": loc_thin,
                    "area_thin": area_thin,
                    "recent_overwrite": recent_overwrite,
                }
            )

            def persist_globals():
                _update_top(cfg, "web_root", web_root.value)
                _update_top(cfg, "llm", llm.value)
                _update_top(cfg, "translation_llm", translation_llm.value)
                _update_top(cfg, "translation_language", translation_language.value)
                _update_top(cfg, "ensemble_model", ensemble.value)
                _update_top(cfg, "location_wordiness", loc_word.value)
                _update_top(cfg, "area_wordiness", area_word.value)
                cfg["enable_reasoning"] = bool(enable_reasoning.value)
                cfg["snow_level_enabled"] = bool(snow_enabled.value)
                _update_top(cfg, "location_forecast_days", _int_or_none(loc_days.value))
                _update_top(cfg, "area_forecast_days", _int_or_none(area_days.value))
                _update_top(cfg, "location_thin_select", _int_or_none(loc_thin.value))
                _update_top(cfg, "area_thin_select", _int_or_none(area_thin.value))
                _update_top(cfg, "recent_overwrite_minutes", _int_or_none(recent_overwrite.value) or 0)
                top_message.text = "Globals saved in form (remember to Save config)."

            ui.button("Save globals", on_click=persist_globals)

        with ui.expansion("Locations", value=True).classes("w-full"):
            loc_container["ref"] = ui.column()
            _render_locations(loc_container["ref"], state)

        with ui.expansion("Areas", value=True).classes("w-full"):
            area_container["ref"] = ui.column()
            _render_areas(area_container["ref"], state)

    refresh_from_state()


def _config_raw_editor(state, raw_ui: dict, form_refresh):
    with ui.card().style("width: 100%;"):
        ui.label("Raw JSON/YAML").classes("text-subtitle1")
        config_path_input = ui.input("Config path", value=str(state.config_path)).classes("w-full")
        text_area = ui.textarea("Contents", value=state.config_text).classes("w-full").style("height: 320px;")
        message = ui.label("").classes("text-caption")

        raw_ui["text_area"] = text_area
        raw_ui["message"] = message

        def load_from_file():
            path = Path(config_path_input.value).expanduser()
            note = _load_config_into_state(state, path)
            state.config_text = dump_config_text(state.config_path, state.config_data)
            text_area.value = state.config_text
            message.text = note
            if form_refresh:
                form_refresh()

        def load_sample():
            sample_path = sample_config_path(state.workspace)
            if sample_path:
                config_path_input.value = str(sample_path)
                note = _load_config_into_state(state, sample_path)
                state.config_text = dump_config_text(state.config_path, state.config_data)
                text_area.value = state.config_text
                message.text = f"{note} (sample)"
                if form_refresh:
                    form_refresh()
            else:
                message.text = "No sample-config.json found."

        def validate_and_save():
            path = Path(config_path_input.value).expanduser()
            state.config_path = path
            ext = path.suffix or ".json"
            try:
                data = parse_config(text_area.value, ext)
                if HAS_IBF:
                    cfg = validate_config(data)
                    message.text = f"Valid config ({len(cfg.locations)} locations, {len(cfg.areas)} areas)"
                else:
                    message.text = "Saved (validation skipped; install ibf to validate)."
                save_config(path, data)
                state.config_data = data
                state.config_text = dump_config_text(state.config_path, state.config_data)
                if form_refresh:
                    form_refresh()
            except (json.JSONDecodeError, yaml.YAMLError) as exc:
                message.text = f"Parse error: {exc}"
            except (ConfigError, Exception) as exc:
                message.text = f"Validation error: {exc}"

        with ui.row():
            ui.button("Reload", on_click=load_from_file)
            ui.button("Load sample", on_click=load_sample)
            ui.button(
                "Validate & Save" if HAS_IBF else "Save (ibf not installed)",
                on_click=validate_and_save,
            )

