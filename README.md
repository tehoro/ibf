# Impact-Based Forecast (IBF) Toolkit [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18112311.svg)](https://zenodo.org/records/18112311)


IBF is a command-line tool that turns weather model data into clear, impact-based forecast text and publishes it as simple HTML pages.

IBF uses raw model output without bias correction or calibration to local observations. Treat forecasts as guidance, not definitive.

What IBF Does
------------
 - Reads a TOML configuration file (locations, areas, output folder, model choices).
- Pulls the latest model data from Open-Meteo (ensemble or deterministic).
- Optionally adds alerts (MetService for NZ, NWS for USA, OpenWeatherMap elsewhere) and impact context (LLM search).
- Uses an LLM to write plain-language forecasts (plus optional translations).
- Publishes simple HTML pages that can be viewed locally or hosted on a web server.

Quick Start (Recommended)
------------------------

Step 1: Download the latest release
- Go to the GitHub Releases page (<https://github.com/tehoro/ibf/releases>) and download the build for your machine:
  - macOS arm64 (Apple Silicon)
  - macOS x86_64 (Intel)
  - Windows x86_64

Step 2: Create a working folder
Create a folder where you will keep the binary, config files, and outputs. For example:

```text
ibf/
  ibf
  config/
  outputs/
```

Working folder note:
- The “working folder” is the folder where you run the `ibf` command.
- IBF also creates `ibf_cache/` and `logs/` in that working folder.
- Your config file can be anywhere; the `--config` path tells IBF where to find it.

The cache and log folders are created automatically the first time you run IBF.
On Windows the binary is named ibf.exe.

Step 3: Set up your .env file
Create a .env file in your working folder. Either copy the existing .env.example in the repo, or use this:

```text
GOOGLE_API_KEY=
OPENWEATHERMAP_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
OPENAI_API_KEY=
```

Notes:
- IBF reads `.env` from the current working directory.
Tip (Windows): make sure the file is named `.env` (not `.env.txt`). You may need to enable “File name extensions” in File Explorer.

Step 4: Create a config file
Create a TOML config file in your config folder. You can name it anything; it just needs
to be valid TOML.

Options:
- Download <https://github.com/tehoro/ibf/blob/main/config_examples/config-example.toml> from the GitHub repo and edit it, or
- Start from the minimal example in the Configuration File Guide below.

Step 5: Run IBF

macOS or Linux:
```text
./ibf run --config config/my-config.toml
```

Windows:
```text
.\ibf.exe run --config config\my-config.toml
```

Web page outputs will be written to the web_root specified in the config.
Success check: open `<web_root>/index.html` in your browser (or `outputs/forecasts/index.html` if you didn’t set `web_root`).

API Keys (Simple Guidance)
--------------------------

Minimal setup for most users:
- GOOGLE_API_KEY (recommended for reliable geocoding and elevation lookups).
- OPENWEATHERMAP_API_KEY (for official alert feeds in many countries).
- GEMINI_API_KEY (to use Gemini models for forecasts and context).

Optional:
- OPENROUTER_API_KEY (if you want access to many models via OpenRouter).
- OPENAI_API_KEY (if you want to use OpenAI models directly, or for impact context when context_llm is an OpenAI model).

If you do not need alerts, you can omit OPENWEATHERMAP_API_KEY.

Where keys are read from:
- IBF reads API keys from a `.env` file in the current working directory or from environment variables.
- The `.env` file takes priority (it overrides any existing environment variables).
- API keys are not read from the TOML config.

Alert sources:
- New Zealand: MetService CAP feed.
- USA: National Weather Service (NWS).
- Other countries: OpenWeatherMap One Call (requires OPENWEATHERMAP_API_KEY).

Impact context note:
- IBF only fetches impact context when `location_impact_based` / `area_impact_based` are true (default). If impact context is enabled but no context LLM key is set, IBF will continue without extra context.

Recommended LLM choices
-----------------------
For most users, this is a good default for all three LLM uses (context, forecast, translation):

- gemini-3-flash-preview

Suggested config snippet:

```toml
llm = "gemini-3-flash-preview"
context_llm = "gemini-3-flash-preview"
translation_llm = "gemini-3-flash-preview"
```

Outputs and File Structure
--------------------------

Outputs:
- The web_root folder contains a menu page plus one subfolder per location or area.
- Each location/area has its own index.html.
- These files are simple static pages you can view locally or host on any web server.

Caches (created automatically under ./ibf_cache):
- forecasts: raw Open-Meteo responses
- processed: processed datasets used for prompts
- impact: cached impact context text
- prompts: snapshots of LLM prompts (auto-cleaned)
- geocode: geocoding and country lookup caches

It is safe to delete the ibf_cache folder; IBF will rebuild it as needed.

Logs (created under ./logs):
- Each `ibf run` writes a timestamped `.log` file.
- The log includes the full config file text at the top for troubleshooting.
- These files will build up with time - delete if they are no longer needed.

Configuration File Guide
------------------------

IBF uses a single TOML file. It has three sections:
- global settings
- one or more [[location]] blocks
- one or more [[area]] blocks

TOML supports comments with `#`, and uses native types for numbers and booleans.
IBF expects TOML config files and validates them strictly (unknown keys or invalid unit values will raise an error).

At least one location or area is required. If web_root is omitted, output defaults to outputs/forecasts.

Minimal example:

```toml
web_root = "./outputs/example-site"
llm = "gemini-3-flash-preview"
context_llm = "gemini-3-flash-preview"

[[location]]
name = "Otaki Beach, New Zealand"
```

Global settings (common ones)
- model: Default forecast model. Use ens:<id> or det:<id>.
- web_root: Output directory.
- location_forecast_days / area_forecast_days: Days of forecast.
- location_wordiness / area_wordiness: brief, normal, detailed.
- location_impact_based / area_impact_based: include impact context.
- location_thin_select / area_thin_select: reduce ensemble members for cost.
- translation_language / translation_llm: optional translation settings.
- temperature_unit / precipitation_unit / windspeed_unit: global unit defaults.

Locations
Each [[location]] block supports:
- name (required)
- model (override global)
- snow_levels (only for deterministic models)
- translation_language
- extra_context: optional local notes to prioritize in impact context
- minimum_refresh_minutes: optional per-location refresh interval override
- temperature_unit / precipitation_unit / windspeed_unit (per-location overrides)

Areas
Areas define their own list of point names for the area forecast. Those names may or may not also appear as standalone [[location]] entries; area-level settings (including units) apply within the area forecast.

Each [[area]] block supports:
- name (required)
- locations (list of location names)
- mode: "area" or "regional"
- model (override global)
- snow_levels (only for deterministic models)
- translation_language
- extra_context: optional local notes to prioritize in impact context
- minimum_refresh_minutes: optional per-area refresh interval override
- temperature_unit / precipitation_unit / windspeed_unit (per-area overrides)

Available ensemble models
Ensemble models are lower‑resolution but capture uncertainty by providing a spread of possible outcomes.
- ens:ecmwf_ifs025
- ens:ecmwf_aifs025
- ens:gem_global
- ens:ukmo_global_ensemble_20km
- ens:ukmo_uk_ensemble_2km
- ens:gfs025
- ens:icon_seamless
See the full list and details: <https://open-meteo.com/en/docs/ensemble-api>

Deterministic model examples
- det:ecmwf_ifs
- det:icon_seamless
- det:open-meteo (auto-selects best deterministic model for the location)
More deterministic models: <https://open-meteo.com/en/docs>

Maps (optional)
--------------
If areas are defined, IBF can generate maps:

Use:
```text
--maps / --no-maps
--map-tiles osm|terrain|satellite
```

Advanced: Install from source (technical option)
-----------------------------------------------

If you prefer running from source:

1) Install Python 3.11 or 3.12
2) Install uv
3) From the repo folder:
```text
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

Then run:
```text
uv run ibf run --config /path/to/config.toml
```

Security prompts
----------------
macOS: If the binary is blocked on first run, open System Settings > Privacy & Security and allow apps from identified developers.
Windows: The binary is unsigned, so SmartScreen may warn. Use More info > Run anyway after verifying the SHA256 checksum.

Technical Reference (Detailed)
------------------------------

Prompt customization (source installs)
--------------------------------------
If you run IBF from source (e.g., with UV), you can edit the built-in prompts directly:
- Forecast and translation prompts live in `src/ibf/llm/prompts.py`.
- Impact-context prompt lives in `src/ibf/api/impact.py` (see `_generate_context`).

These prompts include required placeholders and formatting rules, so treat edits carefully.

API keys and provider mapping
-----------------------------

Environment variables:

| Variable | Used for | Required when |
| --- | --- | --- |
| `GOOGLE_API_KEY` | Geocoding and optional elevation lookup. Also used for reverse geocoding when resolving alert country codes. | Recommended for reliable geocoding/elevation. |
| `OPENWEATHERMAP_API_KEY` | Alerts (OpenWeatherMap One Call for non‑US/NZ) and fallback reverse geocoding. | Required for non‑US/NZ alerts, otherwise optional. |
| `OPENROUTER_API_KEY` | Any model name with an `or:` prefix or unknown model names (OpenRouter). | Required for OpenRouter usage. |
| `OPENAI_API_KEY` | OpenAI models such as `gpt-4o-mini` or `gpt-4o-latest`. | Required if using OpenAI models. |
| `GEMINI_API_KEY` | Direct Gemini SDK usage (`gemini-*` or `google/gemini-*`). | Required if using direct Gemini models. |
| `IBF_DEFAULT_LLM` | Optional env override for the default model when config omits `llm`. | Optional. |

Notes:
- If `GOOGLE_API_KEY` is not set, IBF will still attempt Open-Meteo geocoding first.
- Impact context supports Gemini or OpenAI models only (`context_llm`). OpenRouter models are not supported for impact context.
- Impact context uses Gemini search when `context_llm` is a Gemini model (the default).
- Impact context uses OpenAI web search when `context_llm` is an OpenAI model and requires `OPENAI_API_KEY`.
- If a forecast/translation model string is unrecognized, IBF falls back to an OpenRouter model and will require `OPENROUTER_API_KEY`.
- Keep `GOOGLE_API_KEY` (Geocoding/Elevation) and `GEMINI_API_KEY` (Gemini) separate; they are issued in different consoles and are not interchangeable.

Google Geocoding API key (step-by-step)
---------------------------------------
1) Go to <https://console.cloud.google.com/> and sign in.
2) Create a new project (or select an existing one).
3) Enable the Geocoding API (and the Elevation API if you want elevation lookups).
4) Add billing (required by Google even for free tier).
5) Create an API key under APIs & Services -> Credentials.
6) Restrict the key to Geocoding (and Elevation if enabled).
7) Paste the key into your .env as `GOOGLE_API_KEY=...`.

OpenWeatherMap API key (alerts)
-------------------------------
1) Go to <https://home.openweathermap.org/> and sign in.
2) Open the API Keys page and create a key (e.g., "ibf-alerts").
3) Ensure the One Call API is enabled for your account (alerts come from One Call).
4) Paste the key into your .env as `OPENWEATHERMAP_API_KEY=...`.

Gemini API key (Google AI Studio)
---------------------------------
1) Go to <https://aistudio.google.com/> and sign in.
2) Click "Get API key" and create a new key (choose or create a project if prompted).
3) Paste the key into your .env as `GEMINI_API_KEY=...`.

If you prefer using Google Cloud Console instead of AI Studio, enable the Generative Language API
and create an API key under APIs & Services -> Credentials.

Configuration reference (technical)
-----------------------------------

Global settings:

| Field | Meaning | Notes |
| --- | --- | --- |
| `model` | Default forecast model for all locations/areas. | Use `ens:<id>` or `det:<id>`. Defaults to `ens:ecmwf_ifs025`. |
| `snow_levels` | Enable snow-level estimates. | Only applies to deterministic models. |
| `llm` | Model used for forecast text. | Supports OpenRouter, OpenAI, and Gemini naming. |
| `context_llm` | Model used for impact context. | Gemini or OpenAI only. Defaults to `gemini-3-flash-preview` if omitted. |
| `translation_llm` | Optional model used for translations only. | Used only if translation is enabled. |
| `translation_language` | Default translation language. | English output is always produced; translations are additional. |
| `enable_reasoning` | Enable model reasoning when supported. | Boolean; defaults to true. |
| `location_reasoning` | Reasoning level for location forecasts. | `off`/`minimal`, `low`, `medium`, `high`, or `auto`. |
| `area_reasoning` | Reasoning level for area forecasts. | Same values as above. |
| `location_forecast_days` | Days of forecast for locations. | Defaults to 4 when unset. |
| `area_forecast_days` | Days of forecast for areas. | Defaults to location days or 4. |
| `location_wordiness` | `brief`, `normal`, or `detailed`. | Default is `normal`. |
| `area_wordiness` | `brief`, `normal`, or `detailed`. | Default is `normal`. |
| `location_impact_based` | Include impact context for locations. | Boolean; defaults to true. |
| `area_impact_based` | Include impact context for areas. | Boolean; defaults to true. |
| `location_thin_select` | Thin ensemble members for locations. | Caps to model member count. |
| `area_thin_select` | Thin ensemble members for areas. | Caps to model member count. |
| `minimum_refresh_minutes` | Minimum minutes between refreshes for any output. | Useful for cron; overridden per location/area if set. |
| `web_root` | Output directory for HTML. | Defaults to `outputs/forecasts`. |
| `temperature_unit` / `precipitation_unit` / `windspeed_unit` | Global unit defaults. | See Units section below. |

Locations:

| Field | Meaning | Notes |
| --- | --- | --- |
| `name` | Display name for the location. | Required. |
| `model` | Override the global model. | Use `ens:` or `det:`. |
| `snow_levels` | Override global `snow_levels`. | Deterministic only. |
| `translation_language` | Per-location translation language. | Overrides global. |
| `extra_context` | Optional local context notes. | Added to impact context prompt. |
| `minimum_refresh_minutes` | Optional per-location refresh interval. | Overrides global. |
| `temperature_unit` / `precipitation_unit` / `windspeed_unit` | Per-location unit overrides. | See Units section. |

Areas:

| Field | Meaning | Notes |
| --- | --- | --- |
| `name` | Area display name. | Required. |
| `locations` | Location names included in the area. | Must match `locations[*].name`. |
| `mode` | `area` (summary) or `regional` (per-location breakdown). | Default is `area`. |
| `model` | Override the global model. | Use `ens:` or `det:`. |
| `snow_levels` | Override global `snow_levels`. | Deterministic only. |
| `translation_language` | Per-area translation language. | Overrides global. |
| `extra_context` | Optional local context notes. | Added to impact context prompt. |
| `minimum_refresh_minutes` | Optional per-area refresh interval. | Overrides global. |
| `temperature_unit` / `precipitation_unit` / `windspeed_unit` | Per-area unit overrides. | See Units section. |

Units
-----

Units are set inline at the global level and can be overridden per location/area. You can add
secondary units in parentheses, for example: `windspeed_unit = "mph(kph)"`.

Supported keys and values:
- `temperature_unit`: `celsius` or `fahrenheit`
- `precipitation_unit`: `mm` or `inch` (also accepts `in`, `inches`)
- `windspeed_unit`: `kph`, `mph`, `mps`, `kt` (accepts `kmh`, `km/h`, `ms`, `kn`, `knots`)

Snowfall units are derived automatically: `cm` when precipitation is metric, `inch` when precipitation is inches.
Altitude for snow levels is taken from geocoding and terrain data and is not configurable.

Models
------

Model strings:
- `ens:<id>` selects ensemble models.
- `det:<id>` selects deterministic models.
- For backwards compatibility, bare ensemble IDs (e.g., `ecmwf_ifs025`) are treated as ensemble.

Ensemble models:

| ID | Members | Description |
| --- | --- | --- |
| `ecmwf_ifs025` | 51 | ECMWF IFS 0.25 deg ensemble |
| `ecmwf_aifs025` | 51 | ECMWF AIFS 0.25 deg ensemble |
| `gem_global` | 21 | ECCC GEM Global ensemble |
| `ukmo_global_ensemble_20km` | 21 | UKMO MOGREPS-G 20 km ensemble |
| `ukmo_uk_ensemble_2km` | 3 | UKMO MOGREPS-UK 2 km ensemble |
| `gfs025` | 31 | NOAA GFS 0.25 deg ensemble |
| `icon_seamless` | 40 | DWD ICON seamless ensemble |
See <https://open-meteo.com/en/docs/ensemble-api> for the full list.

Deterministic models:

| ID | Description |
| --- | --- |
| `ecmwf_ifs` | ECMWF IFS HRES 9 km deterministic |
| `icon_seamless` | DWD ICON seamless deterministic |
| `open-meteo` | Open-Meteo auto-selects the best deterministic model |
See <https://open-meteo.com/en/docs> for more deterministic model IDs.

Snow levels:
- Snow levels are only computed for deterministic models when `snow_levels` is enabled.
- Some models may return freezing-level or pressure-level fields as all null; in that case
  snow-level output is omitted for that model.

Forecast/translation LLM selection rules
---------------------------------------

Resolution order (highest to lowest):
1) Explicit override (e.g., `translation_llm` for translation calls)
2) `llm` from config
3) `IBF_DEFAULT_LLM` environment variable
4) Default fallback (`gemini-3-flash-preview`)

Provider naming:
- OpenRouter: `or:provider/model` (requires `OPENROUTER_API_KEY`)
- OpenAI: `gpt-4o-mini`, `gpt-4o-latest` (requires `OPENAI_API_KEY`)
- Gemini direct: `gemini-3-flash-preview` or `google/gemini-3-flash-preview` (requires `GEMINI_API_KEY`)

Impact context is separate: it uses Gemini search by default (`context_llm = gemini-3-flash-preview`),
or OpenAI web search when `context_llm` is an OpenAI model.

Reasoning levels (forecast text):
- OpenAI reasoning models (direct or via OpenRouter) use `reasoning.effort` with `low`/`medium`/`high`; `minimal` maps to `low`, and `off` disables the reasoning payload.
- OpenRouter supports reasoning for select models (currently OpenAI o1/o3/GPT-5 and Grok). Other OpenRouter models ignore the reasoning settings.
- Gemini 3 Flash uses `thinkingLevel` with `minimal`/`low`/`medium`/`high`; `off` maps to `minimal` (Gemini does not fully disable thinking).
- `auto` lets the provider choose its default (dynamic) behavior.

LLM cost overrides (optional):
- If `llm_costs.toml` exists in the working directory, IBF uses it to override cost estimates in logs.
- Costs are USD per million tokens:
  ```toml
  [[model]]
  name = "gemini-3-flash-preview"
  input = 0.50
  cached_input = 0.35
  output = 3.00
  ```

Cache behavior (technical)
--------------------------

IBF writes lightweight caches under `ibf_cache/` so repeated runs are faster. It is always
safe to delete the entire folder.

| Cache | Location | Purpose | Expiration |
| --- | --- | --- | --- |
| Forecast downloads | `ibf_cache/forecasts/*.json` | Raw Open-Meteo responses keyed by request parameters. | TTL default 60 minutes; files older than 48 hours are cleaned when a new request runs. |
| Processed datasets | `ibf_cache/processed/*.json` | Pre-processed dataset used for prompts and fallback text. | Overwritten on next run for the same location. |
| Geocode cache | `ibf_cache/geocode/search_cache.json` | Place name -> lat/lon/timezone cache. | No TTL; delete to refresh. |
| Country cache | `ibf_cache/geocode/country_cache.json` | Lat/lon -> country code for alert routing. | No TTL; delete to refresh. |
| Impact context | `ibf_cache/impact/*.json` | Impact context text and metadata. | Reused for up to 3 local days. |
| Prompt snapshots | `ibf_cache/prompts/*.txt` | Prompt snapshots for debugging. | Older than 3 days are cleaned; a small number are retained. |

Impact context caching is keyed by the local date and the `context_llm` setting (not the weather model). If a new app release changes the default `context_llm`, it will regenerate context even within the 3-day window unless you pin `context_llm` in your config.

CLI commands and options
------------------------

Commands:
- `ibf run --config path/to/config.toml` runs the full pipeline.
- `ibf scaffold --config ...` refreshes the web root structure and menu.
- `ibf maps --config ...` regenerates area maps (supports `--area`, `--tiles`, `--engine`).
- `ibf config-hash --config ...` prints the deterministic config hash.

Common `run` options:
- `--dry-run` validates config without writing outputs.
- `--maps/--no-maps` toggles automatic map generation.
- `--force-maps` regenerates maps even if the hash is unchanged.
- `--map-tiles osm|terrain|satellite` selects tile set.

Troubleshooting (technical)
---------------------------

- Missing API key errors: verify `.env` and rerun with the same working directory.
- Geocoding failures: ensure the Google Geocoding API is enabled and billing is active.
- LLM errors: confirm the model string matches the provider and that the correct API key is set.
- Outputs not updating: check `minimum_refresh_minutes` or delete the target HTML.
- Maps not regenerating: use `--force-maps` or delete `<web_root>/.ibf_maps_hash`.
- If something fails, rerun with `--log-level debug` and check the terminal output and the latest file in `./logs/`.

License
-------
Apache-2.0. See LICENSE and NOTICE.

Preferred citation
------------------
If you use IBF in research, software, or documentation, please cite it using the repository’s citation metadata:

* **CITATION.cff** (preferred): [https://github.com/tehoro/ibf/blob/main/CITATION.cff](https://github.com/tehoro/ibf/blob/main/CITATION.cff)

A suggested citation (from `CITATION.cff`) is:

> Gordon, Neil. *IBF (Impact-Based Forecast Toolkit): LLM-assisted generation of impact-based weather forecasts* (v0.5.0). 2026. [https://github.com/tehoro/ibf](https://github.com/tehoro/ibf)
