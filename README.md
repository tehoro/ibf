# Impact-Based Forecast (IBF) Toolkit [![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.18112311-blue)](https://doi.org/10.5281/zenodo.18112311)


IBF is a command-line tool that turns weather model data into clear, impact-based forecast text and publishes it as simple HTML pages.

IBF uses raw model output without bias correction or calibration to local observations. Treat forecasts as guidance, not definitive.

What IBF Does
------------

- Reads a TOML configuration file (locations, areas, output folder, model choices).
- Pulls the latest model data from Open-Meteo (ensemble or deterministic).
- Optionally adds alerts (MetService for NZ, NWS for USA, OpenWeatherMap elsewhere) and researched impact context.
- Uses a cloud or LM Studio model to write plain-language forecasts and optional translations.
- Publishes simple HTML pages that can be viewed locally or hosted on a web server.

IBF's web research does **not** supply the weather forecast. Numerical forecasts come from
Open-Meteo and active warnings come from the official alert sources above. Web research supplies
local evidence used to translate forecast weather into plausible impacts.

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
BRAVE_SEARCH_API_KEY=
LM_STUDIO_BASE_URL=http://localhost:1234/v1
# LM_STUDIO_API_KEY=
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
- One model provider: GEMINI_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, or LM Studio.

Optional:
- OPENROUTER_API_KEY (if you want access to many models via OpenRouter).
- OPENAI_API_KEY (if you want to use OpenAI models directly or use OpenAI hosted web search).
- BRAVE_SEARCH_API_KEY (only for experimental `context_provider = "brave"`).
- LM_STUDIO_BASE_URL and, if authentication is enabled, LM_STUDIO_API_KEY.

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
- IBF only fetches impact context when `location_impact_based` / `area_impact_based` are true (default).
- `context_provider = "llm-search"` with Gemini is the recommended impact-context path. It uses one
  primary hosted-search request and reuses the result for up to three local days.
- `context_provider = "brave"` is retained as an experimental option. It performs multiple Brave
  searches, then pays the selected local or cloud model to synthesise those results.
- If impact context cannot be obtained, IBF loudly logs the failure and continues without that
  extra context. Configure `context_fallback_llm` only if an experimental Brave run should try the
  recommended hosted-search method once.
- Generated forecast pages identify the language model that actually wrote the forecast and, when
  applicable, the model that produced the translation. The expanded impact-context panel also
  records its research provider, model, and generation time; this metadata is retained when context
  is reused from cache. Forecast and menu footers identify the installed IBF version.

Recommended LLM choices
-----------------------
For a simple cloud setup, the currently recommended and operationally tested model for all three
LLM uses (context, forecast, and translation) is:

- `gemini-3-flash-preview`

It has produced reliable Google Search-grounded context in IBF testing and remains inexpensive.
It is a preview model, so monitor Google's
[deprecation schedule](https://ai.google.dev/gemini-api/docs/deprecations). The stable
`gemini-3.6-flash` is a supported, more capable but more expensive alternative. Flash-Lite models
remain useful low-cost choices for forecast writing or translation, but are not the recommended
context model because they have sometimes returned text without invoking Search.

Suggested config snippet:

```toml
llm = "gemini-3-flash-preview"
context_provider = "llm-search"
context_llm = "gemini-3-flash-preview"
translation_llm = "gemini-3-flash-preview"
```

For local forecast writing and translation while retaining recommended Gemini context research:

```toml
llm = "lms:exact-loaded-gemma-4-model-id"
llm_fallback = "gemini-3-flash-preview"
lm_studio_base_url = "http://192.168.1.50:1234/v1"
context_provider = "llm-search"
context_llm = "gemini-3-flash-preview"
translation_llm = "lms:exact-loaded-gemma-4-model-id"
translation_llm_fallback = "gemini-3-flash-preview"
```

For LM Studio, Gemma 4 is the recommended local model family. Good candidates are Gemma 4 12B
Unified, Gemma 4 26B A4B, and Gemma 4 31B, choosing the largest suitable version that fits
comfortably in available memory. Exact identifiers vary with quantisation and backend, so load the
model first and copy its identifier from LM Studio's Developer tab after checking `/v1/models`.
Gemma 4 12B is the practical starting point; 26B A4B and 31B generally provide more headroom when
the hardware can run them without excessive memory pressure. Models prone to extended reasoning,
including some Qwen 3.6 variants, can be much slower and consume substantially more tokens in this
workflow, so they are not the recommended default.
See LM Studio's [Gemma 4 model catalogue](https://lmstudio.ai/models?search=gemma+4) for available
builds.

Experimental Brave retrieval with local synthesis remains available for comparison work:

```toml
context_provider = "brave"
context_llm = "lms:exact-loaded-gemma-4-model-id"
context_fallback_llm = "gemini-3-flash-preview"
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
- impact/evidence: private Gemini grounding queries/sources and experimental Brave evidence records
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
context_provider = "llm-search"
context_llm = "gemini-3-flash-preview"

[[location]]
name = "Otaki Beach, New Zealand"
```

Global settings (common ones)
- model: Default forecast model. Use ens:<id> or det:<id>.
- web_root: Output directory.
- llm / llm_fallback: forecast-writing model and optional one-step fallback.
- context_provider / context_llm / context_fallback_llm: impact research method, synthesis model, and optional hosted-search fallback.
- lm_studio_base_url: LM Studio server used by all `lms:` model choices.
- location_forecast_days / area_forecast_days: Days of forecast.
- location_wordiness / area_wordiness: brief, normal, detailed.
- location_impact_based / area_impact_based: include impact context.
- location_thin_select / area_thin_select: reduce ensemble members for cost.
- translation_language / translation_llm / translation_llm_fallback: optional translation settings.
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
- Impact-context search and synthesis prompts live in `src/ibf/api/impact.py` and `src/ibf/api/context_research.py`.

These prompts include required placeholders and formatting rules, so treat edits carefully.

API keys and provider mapping
-----------------------------

Environment variables:

| Variable | Used for | Required when |
| --- | --- | --- |
| `GOOGLE_API_KEY` | Geocoding and optional elevation lookup. Also used for reverse geocoding when resolving alert country codes. | Recommended for reliable geocoding/elevation. |
| `OPENWEATHERMAP_API_KEY` | Alerts (OpenWeatherMap One Call for non‑US/NZ) and fallback reverse geocoding. | Required for non‑US/NZ alerts, otherwise optional. |
| `OPENROUTER_API_KEY` | Any model name with an `or:` prefix. | Required for OpenRouter usage. |
| `OPENAI_API_KEY` | OpenAI models such as `gpt-4o-mini` or `gpt-4o-latest`. | Required if using OpenAI models. |
| `GEMINI_API_KEY` | Direct Gemini SDK usage (`gemini-*` or `google/gemini-*`). | Required if using direct Gemini models. |
| `BRAVE_SEARCH_API_KEY` | Experimental Brave LLM Context evidence retrieval. | Required when `context_provider = "brave"`. |
| `LM_STUDIO_BASE_URL` | Optional environment alternative to the TOML `lm_studio_base_url`. | Optional; defaults to `http://localhost:1234/v1`. |
| `LM_STUDIO_API_KEY` | LM Studio API token. | Only required when authentication is enabled on the LM Studio server. |
| `IBF_DEFAULT_LLM` | Optional env override for the default model when config omits `llm`. | Optional. |

Notes:
- If `GOOGLE_API_KEY` is not set, IBF will still attempt Open-Meteo geocoding first.
- With `context_provider = "llm-search"`, `context_llm` must be Gemini or OpenAI because that model must have a hosted web-search tool.
- With experimental `context_provider = "brave"`, `context_llm` may be LM Studio, OpenRouter, Gemini, or OpenAI. Brave retrieves the evidence and the model synthesises it.
- Model strings must identify their provider explicitly. Unknown strings fail with an error instead of being silently treated as OpenRouter models.
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

Gemini API key (Google AI Studio; recommended for impact context)
-----------------------------------------------------------------
1) Go to <https://aistudio.google.com/> and sign in.
2) Click "Get API key" and create a new key (choose or create a project if prompted).
3) Enable billing for the project when using Google Search grounding. Google currently makes
   grounding unavailable on the purely free API tier, while billing-enabled Gemini 3 projects
   receive a shared monthly grounding allowance before per-query charges begin.
4) Paste the key into your .env as `GEMINI_API_KEY=...`.

If you prefer using Google Cloud Console instead of AI Studio, enable the Generative Language API
and create an API key under APIs & Services -> Credentials.

Brave Search API key (experimental impact research)
----------------------------------------------------

1) Sign in or create an account at <https://api.search.brave.com/>.
2) Choose a plan that includes the LLM Context endpoint. Brave's current pricing page is
   <https://api-dashboard.search.brave.com/documentation/pricing>.
3) Open API Keys at <https://api.search.brave.com/app/keys> and create a key for IBF.
4) Add it to the `.env` file as `BRAVE_SEARCH_API_KEY=...`.
5) Set `context_provider = "brave"` in the TOML configuration.

IBF sends the key only in Brave's `X-Subscription-Token` request header. Do not put the key in
the TOML file or commit the `.env` file. Brave's authentication guide is
<https://api-dashboard.search.brave.com/documentation/guides/authentication>.

At the pricing checked for 0.8.0, Brave Search/LLM Context costs US$5 per 1,000 requests and
includes US$5 of monthly credits. With 20 forecast entities, the current experimental cadence is
about 600 daily current-condition requests, about 200 event requests on a three-day cadence, and
about 20 threshold/exposure requests per month when their 60-day caches are averaged over time:
roughly 820 requests before bounded gap fills. Re-running forecasts during the same local day
reuses that research. Check the linked pricing page before relying on this allowance because
provider pricing can change.

A cold Brave context normally uses four paid search requests (one per evidence class), plus the
token cost of the separate synthesis model; a bounded gap fill can add a fifth search and synthesis
repair can add another model call. Using a paid cloud model such as Gemini to summarise Brave
results can therefore cost materially more than starting with Gemini's native web-search tool. IBF
retains Brave for experiments and local-model workflows, but does not recommend it as the normal
operational provider.

LM Studio (local or network models)
-----------------------------------

1) In LM Studio, download/load the intended model and start the API server from the Developer tab.
2) Copy the exact model identifier reported by LM Studio. Configure it with an `lms:` prefix, for
   example an identifier may look like `llm = "lms:gemma-4-26b-a4b-it-mlx"`.
3) For LM Studio on the same machine, the default address is `http://localhost:1234/v1`.
4) For another machine, enable LM Studio's **Serve on Local Network** setting and configure, for
   example, `lm_studio_base_url = "http://192.168.1.50:1234/v1"`.
5) If LM Studio authentication is enabled, create an API token there and put it in `.env` as
   `LM_STUDIO_API_KEY=...`.

IBF does not automatically choose among local models. Before each model's first call, it checks
LM Studio's `/v1/models` endpoint and requires an exact identifier match. If the server cannot be
reached or that model is not advertised, IBF produces a prominent error and uses the configured
fallback if one exists. With LM Studio Just-In-Time loading enabled, `/v1/models` may advertise
downloaded models as well as the models already held in memory.

LM Studio's loaded context length must accommodate the complete system and user prompts plus the
requested output allowance. Area prompts can be much larger than location prompts because they
combine representative locations and ensemble scenarios, but long spot forecasts with many
retained ensemble members can also exceed a model's limit. IBF logs the character/byte size, a
rough input-token estimate, the requested maximum output tokens, and their estimated combined
context requirement before every LLM call. If LM
Studio rejects a prompt for exceeding its context window, IBF identifies that cause explicitly and
immediately tries `llm_fallback` when configured. If an area or regional forecast still fails with
an explicit context-size error, IBF rebuilds the prompt from the already downloaded data with half
as many representative ensemble scenarios and retries, progressively reducing to one scenario only
if necessary. The same recovery applies to ensemble spot forecasts. Deterministic forecasts and
other LLM failures do not trigger scenario reduction.

Useful LM Studio references:

- OpenAI-compatible models endpoint: <https://lmstudio.ai/docs/developer/openai-compat/models>
- Serving over a local network: <https://lmstudio.ai/docs/developer/core/server/serve-on-network>
- API authentication: <https://lmstudio.ai/docs/developer/core/authentication>

Only expose an LM Studio server on a trusted network, and enable authentication whenever other
devices can reach it.

Configuration reference (technical)
-----------------------------------

Global settings:

| Field | Meaning | Notes |
| --- | --- | --- |
| `model` | Default forecast model for all locations/areas. | Use `ens:<id>` or `det:<id>`. Defaults to `ens:ecmwf_ifs025`. |
| `snow_levels` | Enable snow-level estimates. | Only applies to deterministic models. |
| `llm` | Model used for forecast text. | Supports LM Studio, OpenRouter, OpenAI, and Gemini naming. |
| `llm_fallback` | Optional model tried once if forecast writing fails. | May use a different provider. Primary failure is logged prominently. |
| `lm_studio_base_url` | LM Studio OpenAI-compatible server address. | Used for every `lms:` choice; defaults to `http://localhost:1234/v1`. |
| `context_provider` | Impact research method. | `llm-search` is the recommended default; `brave` is an experimental controlled-evidence option. |
| `context_llm` | Hosted-search model or experimental Brave evidence-synthesis model. | `llm-search` requires Gemini/OpenAI; `brave` supports any configured model provider. Defaults to `gemini-3-flash-preview`. |
| `context_fallback_llm` | Optional fallback if the Brave path fails. | Must be a Gemini or OpenAI model because it invokes the existing hosted web-search path. |
| `translation_llm` | Optional model used for translations only. | Used only if translation is enabled. |
| `translation_llm_fallback` | Optional model tried once if translation fails. | May use a different provider. |
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

LLM selection and fallback rules
--------------------------------

Resolution order (highest to lowest):
1) Explicit override (e.g., `translation_llm` for translation calls)
2) `llm` from config
3) `IBF_DEFAULT_LLM` environment variable
4) Default fallback (`gemini-3-flash-preview`)

Provider naming:
- LM Studio: `lms:exact-model-id` (uses `lm_studio_base_url`; optional `LM_STUDIO_API_KEY`)
- OpenRouter: `or:provider/model` (requires `OPENROUTER_API_KEY`)
- OpenAI: `gpt-4o-mini`, `gpt-4o-latest` (requires `OPENAI_API_KEY`)
- Gemini direct: `gemini-3-flash-preview`, `gemini-3.6-flash`, or the equivalent
  `google/gemini-*` form (requires `GEMINI_API_KEY`)

`llm_fallback` and `translation_llm_fallback` each permit one retry with another configured
model. A primary failure is logged prominently before the fallback is tried. This is useful for
pairing a local model with a cloud fallback, but it is deliberately bounded rather than an
unrestricted retry loop.

If forecast writing and its configured fallback both fail, IBF never publishes or translates raw
dataset diagnostics. An existing valid forecast page is preserved. If no valid earlier page
exists, IBF writes a short “Forecast temporarily unavailable” page without internal cache paths.
The pipeline continues processing its remaining locations and areas, then exits with a failure
status and lists every forecast that could not be generated.

Impact-context research
-----------------------

The research provider and the model are separate choices:

| `context_provider` | Retrieval | Allowed `context_llm` | Notes |
| --- | --- | --- | --- |
| `llm-search` | Gemini Google Search or OpenAI web search tool | Direct Gemini or OpenAI | **Recommended.** One primary context job is reused for up to three local days; the hosted provider decides its searches. |
| `brave` | IBF-controlled Brave LLM Context requests | LM Studio, OpenRouter, Gemini, or OpenAI | **Experimental.** Brave returns evidence and the separately selected model synthesises it, adding cost and complexity. |

The recommended Gemini hosted-search path:

- Supplies the canonical geocoded locality, district/region, country and representative places so
  near-name results from another place are not silently substituted.
- Uses a source hierarchy led by meteorological/hydrological services, government, emergency and
  infrastructure agencies. Generic seasonal assumptions are forbidden.
- Explicitly seeks official warning criteria and operational triggers that apply to the target,
  while distinguishing them from observed local impact magnitudes and engineering/design hazard
  references. Return periods and exceptional historical totals must not be presented as routine
  forecast triggers.
- Does not research numerical weather forecasts or active warning messages; Open-Meteo and IBF's
  official alert integrations continue to supply those separately.
- Checks official municipal, tourism, venue, sports and organiser calendars for significant events
  with exact dates within ten days and about 20 km. Cached event bullets are rechecked against the
  moving date window on every forecast run, without another web request.
- Reuses the complete context for up to three local days. A transient Gemini server failure is
  retried once and then treated as non-fatal. Incomplete output permits at most one continuation;
  a response that skipped Search permits one explicit grounding retry.
- For Gemini, privately records the provider-selected web queries, grounding source titles/URLs,
  supported response segments and confidence values. If Gemini returns text without grounding
  metadata, IBF fails closed rather than accepting unauditable model knowledge. Public forecasts
  remain citation-free.

The experimental Brave provider is staged and bounded:

- Four short searches independently cover current vulnerabilities/disruptions, exact-dated major
  events, quantitative impact thresholds, and exposed populations/assets. This avoids asking one
  search query to satisfy unrelated evidence needs.
- Current conditions use Brave's one-week freshness filter and refresh once per local day. Events
  refresh every three days and are rejected unless their evidence contains an exact date inside
  the ten-day window. Threshold and exposure evidence is cached for 60 days.
- At most one additional gap-filling search is allowed per context job, and only when Brave
  returned sources but all were rejected by evidence validation.
- Legacy geocode records are automatically enriched once with district/region information. Search
  terms use the canonical locality and an appropriate administrative region; users do not maintain
  per-location spelling exclusions.
- Every source must mention the locality, its local administrative area, the named area, or one of
  an area's representative places. Near-name results for another place are rejected before
  synthesis. Generic low-quality reference sources are also excluded.
- For areas and regional forecasts, both paths receive representative place names so research is
  not based on the area name alone; Brave also receives centroid coordinates and available country
  information.
- Brave location headers retain the forecast place's ISO country code. If that code is not one of
  Brave's supported search markets (as with some territories and small-island states), IBF uses
  Brave's global search market rather than sending an invalid code or defaulting results to the US.
- Retrieved query text, URL, title, hostname, source/published date, retrieval time, and supporting
  passages are kept in private evidence sidecars, together with sources rejected by validation.
  The synthesising model must cite accepted evidence. IBF repairs harmless formatting variations,
  validates citations and exact event dates, discards unsupported or out-of-window event bullets
  without losing otherwise valid context, and removes private citation markers from the public
  forecast context.

`context_fallback_llm` is intentionally different from a synthesis-model fallback. If the Brave
path fails, it makes one attempt through the existing hosted-search path, so it must name a direct
Gemini or OpenAI model. Leave it unset to fail closed and continue the forecast without researched
context.

Reasoning levels (forecast text):
- OpenAI reasoning models (direct or via OpenRouter) use `reasoning.effort` with `low`/`medium`/`high`; `minimal` maps to `low`, and `off` disables the reasoning payload.
- OpenRouter supports reasoning for select models (currently OpenAI o1/o3/GPT-5 and Grok). Other OpenRouter models ignore the reasoning settings.
- Current Gemini 3 models use `thinkingLevel` with `minimal`/`low`/`medium`/`high`; `off` maps to `minimal` (Gemini does not fully disable thinking).
- `auto` lets the provider choose its default (dynamic) behavior.

LLM cost overrides (optional):
- OpenRouter's provider-reported `usage.cost` is used when returned by a completed request.
- Direct providers that return tokens but not a monetary cost use the current built-in price table;
  Gemini thinking tokens are included at the model's output-token rate.
- LM Studio is treated as unpriced unless its exact model identifier has an entry in
  `llm_costs.toml`.
- Brave request cost is a list-price estimate because its response does not return a per-request
  monetary amount. At the price checked for 0.8.0, each new request is estimated at 0.5 US cents.
- Gemini's API reports token usage but not the final monetary effect of its monthly grounding
  allowance or any later Google Search tool charges. IBF records the returned search-query count
  privately, but the cost summary can only estimate the model-token portion.
- In a representative run using `gemini-3-flash-preview`, eight distinct context jobs cost about
  2.7 US cents in model tokens. At a three-day refresh cadence, 20 entities would be roughly
  US$0.60–0.70 per month while Search remains within Google's included grounding allowance.
- If `llm_costs.toml` exists in the working directory, IBF uses it to override token-based estimates in logs.
- Costs are USD per million tokens:
  ```toml
  [[model]]
  name = "gemini-3-flash-preview"
  input = 0.50
  cached_input = 0.05
  output = 3.00
  ```

Cache behavior (technical)
--------------------------

IBF writes lightweight caches under `ibf_cache/` so repeated runs are faster. It is always
safe to delete the entire folder.

| Cache | Location | Purpose | Expiration |
| --- | --- | --- | --- |
| Forecast downloads | `ibf_cache/forecasts/*.json` | Raw Open-Meteo responses keyed by request parameters. | TTL default 60 minutes; files older than 48 hours are cleaned when a new request runs. |
| Processed datasets | `ibf_cache/processed/*.json` | Pre-processed dataset used for LLM prompts and troubleshooting. | Overwritten on next run for the same location. |
| Geocode cache | `ibf_cache/geocode/search_cache.json` | Place name -> lat/lon/timezone and administrative identity cache. Legacy entries are enriched once without replacing their forecast coordinates/elevation. | No TTL; delete to refresh. |
| Country cache | `ibf_cache/geocode/country_cache.json` | Lat/lon -> country code for alert routing. | No TTL; delete to refresh. |
| Hosted-search impact context | `ibf_cache/impact/*.json` | Synthesised impact context from `llm-search`. | Reused for up to 3 local days. |
| Gemini grounding audit | `ibf_cache/impact/evidence/hosted_*.json` | Private provider-selected queries, source URLs/titles, claim-support mappings and final context. | Retained with the impact cache; safe to delete manually. |
| Brave synthesised context | `ibf_cache/impact/*.json` | Public-ready context synthesised from Brave evidence. | Once per local day. |
| Brave current evidence | `ibf_cache/impact/evidence/*__current.json` | Fresh current vulnerabilities and disruptions. | Once per local day. |
| Brave event evidence | `ibf_cache/impact/evidence/*__events.json` | Major events with exact in-window dates. | 3 days. |
| Brave threshold evidence | `ibf_cache/impact/evidence/*__thresholds.json` | Quantitative local impact thresholds. | 60 days. |
| Brave exposure evidence | `ibf_cache/impact/evidence/*__exposure.json` | Exposed populations, infrastructure and assets. | 60 days. |
| Brave cited synthesis | `ibf_cache/impact/evidence/synthesis_*.json` | Private audit record linking synthesis claims to source markers. | Retained with the impact cache; safe to delete manually. |
| Prompt snapshots | `ibf_cache/prompts/*.txt` | Prompt snapshots for debugging. | Older than 3 days are cleaned; a small number are retained. |

Impact context caching includes the local date, provider, `context_llm`, forecast-day count, local
notes, and relevant Brave/LM Studio settings (not the numerical weather model). Hosted-search
context is reused for up to three local days; experimental Brave evidence follows its category
cadences. Numerical forecasts and active alerts still refresh normally. Evidence sidecars are
private operational audit files rather than public forecast citations; protect the working
directory accordingly.

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
- LM Studio connection errors: start its API server, verify `lm_studio_base_url`, local-network and
  firewall settings, and authentication. The error lists the model identifiers visible from
  `/v1/models`; the configured `lms:` identifier must match exactly.
- LM Studio context-length errors: IBF automatically retries ensemble location, area and regional
  forecasts with fewer representative scenarios when the server explicitly reports context
  overflow. You can also increase the model's loaded context length, reduce forecast days or
  representative locations, lower `location_thin_select` or `area_thin_select`, or configure a
  larger-context cloud model as `llm_fallback`. The logged token count is an estimate because
  OpenAI-compatible local servers do not expose a universal tokenizer.
- Experimental Brave context errors: confirm `BRAVE_SEARCH_API_KEY`, subscription to Brave's Search plan, and
  network connectivity. Brave requires a new key to be generated for a new subscription. IBF logs
  Brave's structured error code and message. Configure `context_fallback_llm` only if you want the
  existing hosted-search method as a fallback.
- Gemini context errors: confirm the Gemini project has billing enabled for Google Search grounding.
  IBF retries one transient server failure or one response that skipped Search, then fails closed
  and continues the forecast without unauditable context.
- Other LLM errors: confirm the model prefix matches the provider and that the correct API key is set.
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

> Gordon, Neil. *IBF (Impact-Based Forecast Toolkit): LLM-assisted generation of impact-based weather forecasts* (v0.8.1). 2026. [https://github.com/tehoro/ibf](https://github.com/tehoro/ibf)
