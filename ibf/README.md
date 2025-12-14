Unified impact-based forecast CLI and service.

See the root-level `README.md` for full documentation and usage details.
## Impact-Based Forecast (IBF) Toolkit

IBF is a single command-line program that reads a `config.json` file, pulls the latest ensemble weather data, asks your preferred LLM to write impact-focused forecast text (plus optional translations), and publishes ready-to-share HTML pages (with optional maps) to a folder of your choice. This guide walks you through every step with the assumption that you are setting it up for the first time.

---

## Quick start (simplest path)
1) **Install uv** (recommended):  
   `curl -LsSf https://astral.sh/uv/install.sh | sh`

2) **Get the code**: clone or unzip the repo so you have the `ibf/` folder.

3) **Set up the virtual env (from inside `ibf/`)**:
   ```bash
   uv venv .venv
   source .venv/bin/activate
   uv pip install -e .   # or: uv sync
   ```

4) **Create your secrets file with required API keys**:
   ```bash
   cp .env.example .env
   # open .env and paste your keys (at minimum GOOGLE_API_KEY, OPENWEATHERMAP_API_KEY, OPENAI_API_KEY)
   ```

5) **Create a config** (JSON):  
   ```bash
   cp examples/sample-config.json /path/to/config.json   # place it anywhere you like
   # edit that file to match your locations/areas/web_root/etc.
   ```

6) **Run IBF**:
   ```bash
   uv run ibf run --config /path/to/config.json
   ```
   Outputs go under the `web_root` you set in the config. If you enable folium/Chrome screenshots for maps, ensure Chrome + chromedriver are installed locally.

If you hit PEP 668 “externally-managed-environment” errors, always use the uv venv + `uv pip` shown above instead of system `pip`.

---

### Step 1 – Download the toolkit

1. Visit <https://github.com/tehoro/ibf>.
2. Click **Code → Download ZIP** (or run `git clone https://github.com/tehoro/ibf.git` if you use Git).
3. Unzip or open the `ibf` folder somewhere easy to find (e.g., `Documents/IBF`).

All instructions below assume you are inside that `ibf` directory.

---

### Step 2 – Install prerequisites

1. Install Python **3.11 or 3.12**:
   - macOS: `brew install python@3.12`
   - Windows: download from <https://www.python.org/downloads/>.
   - Linux: use your package manager (e.g., `sudo apt install python3.12`).
2. Install **uv** (the tool that manages the virtual environment and dependencies):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Windows PowerShell users can run:

   ```powershell
   irm https://astral.sh/uv/install.ps1 | iex
   ```

3. Open a terminal/PowerShell, change to the project folder, and download the Python dependencies:

   ```bash
   cd path/to/ibf
   uv sync
   ```

---

### Step 3 – Prepare your `.env` file (API keys & secrets)

1. Copy the example file: `cp .env.example .env` (or copy/paste in File Explorer/Finder).
2. Open `.env` in any text editor and fill in the keys below.

| Variable | What it’s for | Where to get it |
| --- | --- | --- |
| `GOOGLE_API_KEY` *(required)* | All geocoding + optional elevation lookup | See [How to get a Google Maps Geocoding API key](#how-to-get-a-google-maps-geocoding-api-key) |
| `OPENWEATHERMAP_API_KEY` *(required for alerts)* | Official weather alerts in most countries | <https://openweathermap.org/api> – create free account, enable **One Call** |
| `OPENAI_API_KEY` *(required for impact context)* | Impact-context LLM calls (OpenAI-hosted) | <https://platform.openai.com/> → create API key |
| `OPENROUTER_API_KEY` | LLMs via OpenRouter (`or:provider/model`) | <https://openrouter.ai/> → Account → API Keys |
| `GEMINI_API_KEY` | Google Gemini models (optional) | <https://aistudio.google.com/app/apikey> |

Tips:

- If you only plan to use one LLM provider, you can leave the others blank.
- API keys stay private: `.env` is already excluded from Git.

#### How to get a Google Maps Geocoding API key

1. **Go to the Google Cloud Console**  
   - Visit <https://console.cloud.google.com/> and sign in with your Google account.

2. **Create (or select) a project**  
   - Use the top project selector → **New Project**. Name it something like “IBF Geocoding” and click **Create**, then switch into that project once it’s ready.

3. **Enable the Geocoding API**  
   - In the left menu choose **APIs & Services → Library**.  
   - Search for “Geocoding API”, open it, and click **Enable**. (Enable “Elevation API” as well if you want the optional elevation data we request.)

4. **Set up billing** *(required even for free tier)*  
   - Go to **Billing** in the left menu, click **Set up billing**, and add a payment method. Google provides generous free credits and you can configure usage alerts afterwards.

5. **Create an API key**  
   - Navigate to **APIs & Services → Credentials**.  
   - Click **+ Create credentials → API key**. Copy the generated key.

6. **Restrict the key to geocoding only (recommended)**  
   - On the Credentials list, click the pencil icon next to the new key.  
   - Under **API restrictions**, choose **Restrict key** and tick **Geocoding API** (plus **Elevation API** if enabled). Save.

7. **Add it to IBF**  
   - Paste the key into your `.env` as `GOOGLE_API_KEY=...`. Runs will fail fast with a clear error if the key is missing.

That’s it—after this setup, IBF can geocode any location globally without additional provider keys.

---

### Step 4 – Understand the configuration file (JSON)

IBF uses a single JSON file. Start by copying `examples/sample-config.json` to a convenient location (it does not have to live inside `ibf`; e.g., `/path/to/config.json`). The config has three parts: **global settings**, **locations**, and **areas**.

#### 4.1 Global settings (order used in the example)

| Field | Meaning | Example |
| --- | --- | --- |
| `ensemble_model` | Default ensemble model for all forecasts unless overridden per location/area (see ensemble model list below). | `"ecmwf_aifs025"` |
| `llm` | Primary model ID (OpenRouter: `or:provider/model`, OpenAI: `gpt-4o-mini`, Gemini: `gemini-*`). For OpenRouter names, copy from <https://openrouter.ai/models> and prefix with `or:`. | `"or:openai/gpt-5.1"` |
| `enable_reasoning` | Whether to allow reasoning tokens if the model supports them. | `"true"` |
| `web_root` | Where output HTML is written. Relative paths are fine. | `"./outputs/example-site"` |
| `location_forecast_days` | Days per location forecast (stay ≤7). | `"4"` |
| `location_wordiness` | Length guidance for locations: `normal`, `brief`, or `detailed`. | `"normal"` |
| `location_reasoning` | Reasoning hint for locations: `low`, `medium`, or `high`. Balance quality vs cost. | `"high"` |
| `location_impact_based` | Include impact context for locations. | `"true"` |
| `location_thin_select` | Thin ensemble members to this count for locations (e.g., from 51 ECMWF members to 10) to save LLM cost. IBF caps at available members. | `"10"` |
| `area_forecast_days` | Days per area/regional forecast (stay ≤7). | `"2"` |
| `area_wordiness` | Length guidance for areas: `normal`, `brief`, or `detailed`. | `"normal"` |
| `area_reasoning` | Reasoning hint for areas: `low`, `medium`, or `high`. | `"high"` |
| `area_impact_based` | Include impact context for areas. | `"true"` |
| `area_thin_select` | Thin ensemble members to this count for areas. | `"10"` |
| `recent_overwrite_minutes` | Skip overwriting outputs newer than this many minutes. | `"0"` |
| *(optional)* `units.*` | Global unit defaults; can be overridden per location/area. | e.g., `{"temperature_unit": "fahrenheit"}` |
| *(optional)* `translation_llm` | Alternate model for translations only. | `""` |
| *(optional)* `translation_language` | Default translation language if locations/areas omit one (English output is always produced; translations are additional). | `""` |

#### 4.2 Locations (array of objects)

Each location can include:

| Field | Meaning | Example |
| --- | --- | --- |
| `name` | Display name; must match any area references. | `"Otaki, New Zealand"` |
| `model` | Optional override of `ensemble_model` for this location. | `"ecmwf_aifs025"` |
| `translation_language` | Per-location translation target; overrides global/default. English output is always produced; this adds a translated copy. | `"es"` |
| `units.temperature_unit` | Temperature unit. | `"celsius"` |
| `units.precipitation_unit` | Precipitation unit. | `"mm"` |
| `units.windspeed_unit` | Wind unit. | `"kph"` |

#### 4.3 Areas (or regional areas; array of objects)

Each area can include:

| Field | Meaning | Example |
| --- | --- | --- |
| `name` | Area display name. | `"Trinidad and Tobago"` |
| `locations` | List of location names (must match `locations[*].name`). | `["Port of Spain, Trinidad and Tobago", ...]` |
| `mode` | `"area"` for summary per day; `"regional"` adds sub-sections per location. | `"area"` |
| `model` | Optional override of `ensemble_model` for this area. | `"gem_global"` |
| `translation_language` | Per-area translation target. English output is always produced; this adds a translated copy. | `"es"` |
| `units.temperature_unit` | Temperature unit override. | `"celsius"` |
| `units.precipitation_unit` | Precipitation unit override. | `"mm"` |
| `units.windspeed_unit` | Wind unit override. | `"kph"` |

#### 4.4 Available ensemble models

You may set `ensemble_model` globally or a `model` field on a specific location/area. IBF validates the ID and automatically limits thinning to the number of members each model exposes.

| Model ID | Members | Description |
| --- | --- | --- |
| `ecmwf_ifs025` | 51 | ECMWF IFS 0.25° (default). |
| `ecmwf_aifs025` | 51 | ECMWF AIFS 0.25°. |
| `gem_global` | 21 | Environment Canada GEM Global 25 km. |
| `ukmo_global_ensemble_20km` | 21 | UKMO MOGREPS-G 20 km global. |
| `ukmo_uk_ensemble_2km` | 3 | UKMO MOGREPS-UK 2 km (≈5‑day range). |
| `gfs025` | 31 | NOAA GFS Ensemble 0.25°. |
| `icon_seamless` | 40 | DWD ICON seamless ensemble (global + Europe). |

> Snow-level fields are not supported; omit any `snow_level` or `snow_level_enabled` entries in configs.
- When you run IBF, it also creates/upgrades a PNG map at `<web_root>/maps/<area-slug>.png`. Maps regenerate only when you change the list of locations for that area (or when you use `--force-maps`).

#### 4.5 Units (overrides per location/area)

- Global defaults are `celsius`, `mm`, `kph`. You can set `units.*` globally and override per location/area.
- `temperature_unit`: `celsius` (default) or `fahrenheit`.
- `precipitation_unit`: `mm` (default) or `in` (snow follows precip unit; default snow shown in cm when using mm).
- `windspeed_unit`: `kph` (default), `mph`, `kn` (knots), or `mps` (meters per second).
- Dual units are supported by writing the secondary in parentheses, e.g., `"mph (kph)"` or `"celsius (fahrenheit)"`. The first is primary; the second is shown alongside.

#### 4.4 Full example

```json
{
  "web_root": "outputs/example-site",
  "llm": "openai/gpt-4o-mini",
  "translation_llm": "or:openai/gpt-5-mini",
  "location_forecast_days": 4,
  "area_forecast_days": 3,
  "locations": [
    { "name": "London, United Kingdom", "translation_language": "fr" },
    { "name": "Kingston, Jamaica", "translation_language": "jam" }
  ],
  "areas": [
    {
      "name": "United Kingdom Overview",
      "locations": ["London, United Kingdom"],
      "mode": "area"
    },
    {
      "name": "Jamaica Regional Outlook",
      "locations": ["Kingston, Jamaica"],
      "mode": "regional"
    }
  ]
}
```

---

### Step 5 – Run the toolkit

From the project directory:

```bash
uv run ibf run --config path/to/config.json
```

Helpful options:

- `--log-level debug` for extra detail.
- `--dry-run` to validate the config without touching files.
- `--no-maps` if you temporarily want to skip map generation.

IBF logs every major step (“Reading config…”, “Fetching forecast…”, “Requesting LLM…”) so you know what it’s doing. If an LLM request fails, the program falls back to a dataset summary and keeps going.

---

### Step 6 – Find your output

- Everything lands under the `web_root` folder you set (example: `outputs/example-site`).
  - Each location has its own subfolder with an `index.html`.
  - Each area/regional area also gets its own subfolder and includes a “Show map” link.
  - `maps/` holds PNG snapshots of each area.
- `ibf_cache/` (in the project root) stores API responses and processed datasets so reruns are faster.
- If you’re publishing to another web server, simply upload the contents of `web_root`.

---

### Technical reference – cache behavior

IBF writes several lightweight caches under `ibf_cache/` so it does not keep hitting slow or rate-limited APIs. Removing this directory at any time is safe; it will be recreated on the next run.

| Cache | Location | Purpose | When it’s read | Expiration / deletion |
| --- | --- | --- | --- | --- |
| Forecast downloads | `ibf_cache/forecasts/<lat_lon>.json` | Raw Open-Meteo ensemble responses keyed by latitude, longitude, units, and day count. | `ibf.api.open_meteo.fetch_forecast()` reads this file before making a network call. | Used only while the file is newer than `cache_ttl_minutes` (default 60). Anything older than 48 hours is automatically deleted the next time any forecast cache is checked. Set `cache_ttl_minutes=0` in a `ForecastRequest` to skip caching altogether. |
| Processed datasets | `ibf_cache/processed/<slug>.json` | The post-processed dataset that feeds the LLM plus the fallback “dataset preview” text. | Only written; IBF does not re-read it during the same run. | Overwritten the next time the same location/area slug runs. Safe to delete anytime for disk cleanup. |
| Geocode search results | `ibf_cache/geocode/search_cache.json` | A normalized place-name lookup table so repeated runs don’t call Open-Meteo (or Google) again. | `ibf.api.geocode.geocode_name()` loads this map before making HTTP requests. | No TTL—delete the file to force fresh coordinates or timezone data. |
| Reverse country lookups | `ibf_cache/geocode/country_cache.json` | Latitude/longitude → ISO country code pairs for alert provider selection. | `ibf.api.alerts._resolve_country_code()` checks here before calling Google reverse geocoding (falls back to OpenWeatherMap if available). | No automatic expiry. Remove the file if country-routing logic needs to refresh. |
| Impact context | `ibf_cache/impact/YYYYMMDD_<type>_<slug>_<days>.json` | The structured “Impact-Based Forecast Context” text and metadata for each location/area. | `ibf.api.impact.fetch_impact_context()` will reuse the most recent file from the past three local days before asking an LLM. | Cached files older than three local days are discarded automatically when new requests run. |

Because `ibf_cache/` is git-ignored, blowing it away is the fastest way to guarantee a full re-fetch of every upstream resource.

---

### Optional commands

| Command | When to use it |
| --- | --- |
| `uv run ibf scaffold --config ...` | Rebuild the directory/menu structure without running forecasts. Useful after adding lots of locations. |
| `uv run ibf config-hash --config ...` | Prints a SHA-256 hash of your config. Great for cron jobs so you only run when the config changed. |
| `uv run ibf maps --config ...` | Manually regenerate maps. Use `--area "Belize"` to limit the run, `--tiles satellite`, or `--engine folium` if you want the legacy HTML+browser capture mode. |

---

### Troubleshooting tips

- **“Missing API key” errors** – double-check `.env` and ensure you ran the commands via `uv run` so the environment file loads automatically.
- **Geocoding failures** – confirm `GOOGLE_API_KEY` is present, the Geocoding API is enabled in the selected Google Cloud project, and billing is active (Google requires it even for free-tier usage). Restricting keys to Geocoding/Elevation only is fine.
- **LLM model errors** – verify the `llm` name matches what your provider supports. For OpenRouter models, prefix with `or:` (e.g., `or:google/gemini-2.0-pro-exp`).
- **Stuck or slow runs** – add `PYTHONUNBUFFERED=1` before the command to flush logs immediately:  
  `PYTHONUNBUFFERED=1 uv run ibf run --log-level debug --config path/to/config.json`.

Once you have `.env` and `config.json` dialed in, you can schedule `uv run ibf run --config ...` with cron, Windows Task Scheduler, or any automation tool. The program automatically refreshes web scaffolding, map images, forecasts, translations, and impact context every time it runs.

