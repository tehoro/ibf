## Pipeline Migration Plan

This guide maps the legacy Replit application (`originalcode/open-meteo-ensemble-forecast-text`)
into the new modular package so we can eliminate HTTP hop + shell scripts.

### Legacy Components

| Area | Legacy file/section | Responsibilities |
| ---- | ------------------- | ---------------- |
| Flask API | `main.py` | Routing (`/forecast`), input validation, caching bootstrap, HTML/text formatting, error handling |
| Forecast acquisition | `main.py#get_forecast_data`, `thinEnsembles.py`, `elevation.py`, `alerts.py`, `fetch_impact_context.py` | Call Open-Meteo, compute ensemble subsets, gather external context, maintain caches |
| LLM generation | `main.py#get_llm_forecast_text` (various branches) | Talk to OpenAI/OpenRouter/Gemini/DeepInfra, apply wordiness + reasoning flags, handle translation |
| Output | `make_forecasts.py` | Convert text to HTML, translations, IBF context accordion |
| Maps | `mapAreas.py` | Geocode, render folium map, screenshot via Selenium |

### Target Modules

| Module | Purpose |
| ------ | ------- |
| `ibf.api.open_meteo` | Thin client for Open-Meteo ensemble data, including cache keying + expiry. |
| `ibf.api.alerts` | Wrap NOAA/Met Office alert feeds exactly as current `alerts.py`. |
| `ibf.api.context` | Fetch and cache IBF context JSON. |
| `ibf.pipeline.tasks` | Define `ForecastTask` objects (location/area) with derived params (units, language, thin_select). |
| `ibf.pipeline.executor` | Orchestrate task execution with retries, rate limiting, thread/concurrency toggles. |
| `ibf.llm.base` + providers | Provide a unified `generate_forecast(prompt, *, provider, wordiness, reasoning, impact_based)` API. Each provider handles API keys + request payloads. |
| `ibf.render.html` | Merge Markdown → HTML, translations, IBF accordion. Reuse logic from `make_forecasts.py` but move to templates (Jinja). |
| `ibf.render.assets` | Manage static assets (logos, CSS) if needed. |

### Migration Steps

1. **Extract Utilities**
   - Move cache directory helpers (`create_cache_directories`, `delete_old_forecasts`) into `ibf.pipeline.cache`.
   - Convert timezone + formatting helpers (`format_issue_time_with_utc_offset`, `degrees_to_compass`, etc.) into `ibf.util.time` + `ibf.util.units`.

2. **Implement API Clients**
   - `open_meteo.py`: wrap `requests.get` plus caching; expose `fetch_ensemble(latitude, longitude, forecast_days, units, thin_select)`.
   - `alerts.py`: copy existing `get_alerts/format_alerts` logic with typed responses.
   - `context.py`: reuse `fetch_impact_context`, but store caches under `caches/impact`.

3. **LLM Layer**
   - Define `BaseProvider` with `generate(prompt: str, *, settings: ProviderSettings) -> ForecastText`.
   - Implement providers incrementally, starting with OpenRouter (current default). Each provider loads its API key from `Settings`.

4. **Pipeline Executor**
   - Create `ForecastTask` with `build_request_url()` for backward compatibility temporarily.
   - `Executor.run_tasks(tasks: list[ForecastTask])` handles: caching, API fetch, LLM generation, translation, IBF context, HTML render.
   - Compose tasks from config at CLI layer (`ibf.cli.run`).
   - ✅ Current status: location tasks plus the new area synthesizer both run through the same executor; each area now loops over representative locations, reuses the dataset formatter, and feeds the dedicated prompt before rendering HTML. Areas may specify `mode="regional"` to switch to the regional-breakdown instructions, and every location/area can request its own translation language (`lang`/`translation_language`) while sharing the global translation model.

5. **Rendering + Scaffolding Integration**
   - Move Markdown conversion + HTML template from `make_forecasts.py` into `ibf.render.html`.
   - Add support for translation + IBF context toggles (existing behavior).
   - Wire `generate_site_structure` (already implemented) to know when to rebuild placeholders vs final HTML.

6. **Maps Subcommand**
   - ✅ `ibf maps` now mirrors `mapAreas.py`: it geocodes each area using the shared client, renders folium maps, and (optionally) screenshots them via Selenium/ChromeDriver.

7. **Server Mode (optional)**
   - Rebuild Flask app as optional `ibf server` command that imports the same pipeline modules so the HTTP API is just another interface.

8. **Testing**
   - Add unit tests per module (config parsing, slugify, API client, render).
   - Create integration test using the sample config to ensure `ibf run --dry-run` stays stable.

### Risk & Mitigation

- **Long-running HTTP calls**: preserve retry/backoff logic from `make_forecasts.py` and `main.py`. Consider `tenacity`.
- **Secrets management**: centralize via `ibf.config.settings` to avoid scattering `os.getenv`.
- **Backward compatibility**: keep ability to call the existing Replit API while migrating by offering a `RemoteForecastProvider` that simply hits the HTTP endpoint. Swap to local generation when modules are ready.

Following these steps keeps the refactor incremental: scaffold and config handling are already live; next up is the API + LLM extraction so the CLI can produce real forecasts without shell scripts.

