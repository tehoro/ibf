## IBF Modular Architecture

This document captures how the refactored system will be organized so the legacy
`webforecasts` scripts and the Replit API collapse into a single Python package.

### Package Layout

```
ibf/
├── cli.py              # Typer entry-points (`run`, `scaffold`, `maps`, etc.)
├── config/             # Config models, secrets loading, hash helpers
├── api/                # Data acquisition: Open-Meteo, alerts, impact context
├── llm/                # Wrappers for OpenAI/OpenRouter/Gemini/DeepInfra
├── pipeline/           # Forecast orchestration + caching + retries
├── web/                # HTML generation & directory scaffolding (ex setup.py)
├── maps/               # Map generation (folium + selenium screenshot)
└── util/               # Shared utilities (logging, rate limiting, file I/O)
```

- `ibf.cli` stays thin. Each subcommand (`run`, `scaffold`, `maps`, `server`)
  defers to service classes in other modules.
- `ibf.config` owns `.env` loading (via `dotenv`) plus JSON schema validation.
  It returns an immutable `ForecastConfig` object with computed hash/timestamps.
  Per-location/area options such as `lang` (translation language) are surfaced so the pipeline can render multilingual output without extra scripting.
- `ibf.api` wraps external HTTP calls:
  - `open_meteo.py` for ECMWF retrieval & caching (reusing logic from `main.py`).
  - `alerts.py`, `impacts.py`, `geocode.py`, `elevation.py`.
- `ibf.llm` provides a common interface for generating text forecasts. Each
  backend (OpenAI, OpenRouter, Gemini, Grok, etc.) lives behind a strategy
  so `pipeline` can select based on config.
- `ibf.pipeline` coordinates everything:
  1. Prepares caches & output directories.
  2. Builds forecast tasks for every location **and** every configured area. Standard areas reuse the underlying location collector, then feed the single-area prompt, while `mode="regional"` areas switch to the regional breakdown workflow.
  3. Executes acquisition → reasoning → rendering, respecting rate limits.
  4. Writes HTML (via `ibf.web`) and metadata logs.
- `ibf.web` handles:
  - Menu + per-location HTML templates (Jinja/Markdown to HTML conversion).
  - Auto scaffold (replacement for `setup.py`) run before each pipeline.
  - Optional detection of config hash changes to trigger rebuilds.
- `ibf.maps` moves `mapAreas.py` logic into reusable services (geocoding,
  folium map rendering, Selenium screenshots).

### Command Line Surface

```
ibf run --config CONFIG.json [--force] [--dry-run]
ibf scaffold --config CONFIG.json [--clean]   # ensures directories/menu exist
ibf maps --config CONFIG.json [--areas Jamaica Belize]
ibf server --config CONFIG.json               # optional Flask/Waitress API
ibf config-hash --config CONFIG.json
```

- `ibf run` always calls `scaffold` internally if the stored hash differs.
- `--force` bypasses mtime/hash checks and rewrites everything (useful for cron).
- `--dry-run` shows which locations/areas would run (already implemented).

### Data & Cache Layout

```
<project root>/
├── outputs/
│   ├── hash.txt               # latest config hash
│   └── <slug>/                # per-location or per-area HTML bundles (menu groups them)
├── caches/
│   ├── forecasts/*.json
│   ├── geocode/cache.json
│   ├── elevation/cache.json
│   └── impact/*.json
└── logs/
    ├── pipeline.log
    └── fetch.log
```

Paths stay configurable via `web_root` but default to `outputs/forecasts` when
running locally. The CLI stores the last config hash + run metadata alongside
the generated HTML so later runs can skip untouched locations.

### Secrets & Environment

All API keys live in `.env` (already templated). `ibf.config.secrets.get_settings`
will surface strongly typed settings that modules consume. Nothing in `ibf`
reads from `os.environ` directly outside the config layer.

### Migration Plan

1. **Phase 1** – move deterministic utilities:
   - Markdown → HTML conversion (from `make_forecasts.py`).
   - Directory scaffolding (from `setup.py`).
   - Config parsing + hashing (done).
2. **Phase 2** – port API + pipeline primitives out of Replit `main.py`:
   - Forecast caching, geocode caching, alerts, thin ensembles.
   - LLM integration, translation support, and area synthesis prompts.
3. **Phase 3** – unify CLI commands (`run`, `maps`, `server`).
4. **Phase 4** – add tests/docs, ensure cron-ready instructions.

This layout keeps each concern independent so future maintainers (or cron jobs)
can run `ibf run --config ...` without touching Replit-specific scripts.

