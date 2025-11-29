## Impact-Based Forecast (IBF) Toolkit

This directory will become the home of the unified Python application that replaces
the legacy Replit API plus `webforecasts` scripts. The project is managed by
[uv](https://docs.astral.sh/uv/) so contributors get reproducible environments on macOS,
Windows, and Linux.

### Prerequisites

1. Install Python 3.11 or 3.12 (pyenv, Homebrew, Microsoft Store, etc.).
2. Install `uv` (one time):  
   `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Getting Started

```bash
cd /Users/gordon/Dropbox/Developer/IBF/ibf
uv sync               # create/update the virtual environment
cp .env.example .env  # populate secrets (edit the file afterwards)
uv run ibf --version  # sanity check
uv run ibf scaffold --config examples/sample-config.json --force
uv run ibf run --config ../originalcode/webforecasts/config.json --dry-run
```

### Environment Variables

API keys live in `.env` (ignored by git). See `.env.example` for the required
keys:

- `OPENWEATHERMAP_API_KEY`
- `OPENAI_API_KEY`
- `DEEP_INFRA_API_KEY`
- `OPENROUTER_API_KEY`
- `GOOGLE_API_KEY`
- `GEMINI_API_KEY`

`uv` automatically loads `.env` when running commands via `uv run`. Cache files
for API/LLM responses live under `ibf_cache/`.

### Commands

- `ibf run --config path/to/config.json` – validates config, scaffolds the web
  folders if needed, then executes the pipeline (geocoding, Open-Meteo fetch,
  alert retrieval, LLM text generation, HTML rendering). Use `--dry-run` to
  inspect without touching the filesystem.
- `ibf scaffold --config path/to/config.json` – only regenerate the menu +
  placeholder pages. Pass `--force` to overwrite existing placeholders.
- `ibf config-hash --config path/to/config.json` – print the deterministic hash
  so cron jobs can skip runs when nothing changed.

Try the example config in `examples/sample-config.json` for a local test; it
writes to `ibf/outputs/example-site`.

### Logging

Console output now includes detailed INFO-level logging by default (overridable via `--log-level` or the `IBF_LOG_LEVEL` environment variable). For full verbosity:

```
PYTHONUNBUFFERED=1 uv run ibf run --log-level debug --config path/to/config.json
```

### Configuration Basics

- `locations`: each entry needs at least a `name` and optional `units` dict (keys: `temperature_unit`, `precipitation_unit`, `snowfall_unit`, `windspeed_unit`, `altitude_m`). Units default to `celsius/mm/cm/kph`.
- `areas`: supply a `name` plus a list of representative `locations` (strings). The pipeline geocodes/fetches each spot, formats their datasets, and runs the dedicated area prompt to synthesize a single forecast + HTML page. Set `"mode": "regional"` on any area to request the regional-breakdown prompt (multiple sub-region paragraphs per day); otherwise the standard single-area summary is used.
- Tuning knobs: `location_wordiness`, `area_wordiness`, `location_thin_select`, `area_thin_select`, `location_forecast_days`, `area_forecast_days`, and the impact flags (`location_impact_based`, `area_impact_based`).
- Web scaffolding re-runs automatically before each `ibf run`, so adding/removing locations or areas only requires editing the config file.
- Translation: set `lang` or `translation_language` on any location/area to request an automatic translated copy of the forecast for that entry. The optional `translation_llm` setting (global) lets you choose a single model for all translations; otherwise the primary `llm` is reused.
- `examples/sample-config.json` shows two locations plus both a standard area and a regional-breakdown area so you can see the difference.

### LLM Setup

- Set the `llm` field in your config (e.g., `"llm": "or:openrouter/polaris-alpha"`).
- Provide matching API keys in `.env`:
  - `OPENROUTER_API_KEY` for OpenRouter (`or:…`) models
  - `OPENAI_API_KEY` for native OpenAI models (e.g., `gpt-4o-mini`)
  - `GEMINI_API_KEY` for `gemini-*`
  - `DEEP_INFRA_API_KEY` for the `deepinfrar1` shortcut
- `GOOGLE_API_KEY` enables high-quality fallback geocoding/elevation via Google Maps when Open-Meteo cannot resolve a location.
- Impact context: when an impact-based forecast is enabled and no same-day cache entry exists, the CLI automatically generates fresh context via OpenAI (`OPENAI_API_KEY`) and stores it under `ibf_cache/impact/`. Cache files older than three days are cleaned up automatically.
- If a key is missing, the CLI falls back to a deterministic dataset preview instead of LLM text.
- Optional: set `"lang"` or `"translation_language"` per location/area (or globally) to request a second pass that translates the generated forecast into that language.
- Optional: set a global `"translation_llm": "gpt-4o-mini"` (or another supported model) if you want translations to use a cheaper/different model than the main forecast generation. If omitted, translation falls back to the main `llm`.

### Testing

Install the optional dev dependencies and run the suite with `pytest` (tests rely on mocked network calls, so they run entirely offline):

```bash
cd /Users/gordon/Dropbox/Developer/IBF/ibf
uv sync --group dev
uv run pytest
```

The high-level tests exercise the Typer CLI end to end (with the external APIs mocked) and unit tests cover translation-language precedence logic. Feel free to extend the suite as you add features.

### Roadmap

- [x] Establish uv project scaffolding with CLI entry point.
- [ ] Port forecast generation logic into modular packages (`ibf.api`, `ibf.web`, etc.).
- [ ] Replace shell scripts with Typer subcommands for `setup`, `maps`, and `run`.
- [ ] Add documentation and tests to cover configuration validation and pipeline execution.

