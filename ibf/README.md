## Impact-Based Forecast (IBF) Toolkit

IBF is a single command-line program that reads a `config.json` file, pulls the latest ensemble weather data, asks your preferred LLM to write impact-focused forecast text (plus translations), and publishes ready-to-share HTML pages (with optional maps) to a folder of your choice. This guide walks you through every step with the assumption that you are setting it up for the first time.

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
| `OPENAI_API_KEY` | Forecast + impact context LLMs (OpenAI-hosted models) | <https://platform.openai.com/> → create API key |
| `OPENROUTER_API_KEY` | Forecast LLMs via OpenRouter (for `or:provider/model` names) | <https://openrouter.ai/> → Account → API Keys |
| `GEMINI_API_KEY` | Google Gemini models (optional) | <https://aistudio.google.com/app/apikey> |
| `DEEP_INFRA_API_KEY` | DeepInfra models (optional) | <https://deepinfra.com/dashboard> |
| `OPENWEATHERMAP_API_KEY` | Severe weather alerts outside the US/NZ | <https://openweathermap.org/api> – create free account, enable **One Call** |
| `GOOGLE_API_KEY` *(required)* | All geocoding + optional elevation lookup | See [How to get a Google Maps Geocoding API key](#how-to-get-a-google-maps-geocoding-api-key) |

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

### Step 4 – Understand the configuration file

IBF only needs a single JSON file (you can base yours on `examples/sample-config.json`). It has three sections:

1. **Global settings** – impact the whole run.
2. **Locations** – individual towns/cities.
3. **Areas** – combined reports that summarize several locations (including “regional” breakdowns).

#### 4.1 Global settings

| Setting | Description |
| --- | --- |
| `web_root` | Folder where finished HTML pages are written. Example: `"outputs/example-site"`. |
| `llm` | Main model name (e.g., `"openai/gpt-4o-mini"` or `"or:openrouter/polaris-alpha"`). |
| `translation_llm` | Optional cheaper/faster LLM used only for translations (leave blank to reuse `llm`). |
| `translation_language` | Default translation language if a location/area doesn’t specify one. |
| `location_forecast_days`, `area_forecast_days` | How many days of data each section covers (default 4). |
| `location_wordiness`, `area_wordiness` | `"concise"`, `"normal"`, or `"detailed"` tone hints for the LLM. |
| `location_thin_select`, `area_thin_select` | How many ensemble members to keep when thinning (defaults to 16). |
| `location_impact_based`, `area_impact_based` | `true/false` – include impact context in the prompt. |
| `enable_reasoning`, `location_reasoning`, `area_reasoning` | Advanced toggles for longer “thinking” phases; leave as default unless you know a model that supports reasoning tokens. |
| `recent_overwrite_minutes` | Minimum time before the program overwrites an existing forecast (useful for cron jobs). |

#### 4.2 Location entries

Each location object looks like:

```json
{
  "name": "Kingston, Jamaica",
  "lang": "jam",
  "units": {
    "temperature_unit": "celsius",
    "precipitation_unit": "mm",
    "windspeed_unit": "kph"
  }
}
```

Field explanations:

- `name` (required) – exactly how you want it shown on the website.
- `lang` – optional translation language code (BCP‑47 or ISO, e.g., `"jam"`). Forecast prose is always written in English; this value is only used when you request a translated copy.
- `translation_language` – per-location override that takes precedence over `lang` and any global `translation_language`.
- `units` – override defaults for that location. Snowfall automatically mirrors the precipitation unit (mm or inches), so you usually only need to set `temperature_unit`, `precipitation_unit`, and `windspeed_unit`. Dual units such as `"temperature_unit": "celsius (fahrenheit)"` are also supported.

#### 4.3 Areas and regional areas

Areas combine the text from any number of locations. Example:

```json
{
  "name": "Trinidad and Tobago",
  "locations": [
    "Port of Spain, Trinidad and Tobago",
    "Scarborough, Trinidad and Tobago"
  ],
  "mode": "area",
  "translation_language": "es"
}
```

- `locations` must match the `name` field of entries in the `locations` list.
- `mode`: `"area"` produces one summary paragraph per day; `"regional"` adds sub-sections per location so you can describe sub-regions.
- Each area inherits the global settings but you can override `lang`, `translation_language`, or `units`.
- When you run IBF, it also creates/upgrades a PNG map at `<web_root>/maps/<area-slug>.png`. Maps regenerate only when you change the list of locations for that area (or when you use `--force-maps`).

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
  - Each area/regional area also gets its own subfolder and includes an “Open map” link.
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
| Impact context | `ibf_cache/impact/YYYYMMDD_<type>_<slug>_<days>.json` | The structured “Impact-Based Forecast Context” text and metadata for each location/area. | `ibf.api.impact.fetch_impact_context()` loads the matching file (same date, context type, slug, and day span) before asking an LLM. | Only the current local-day file is considered valid. `cleanup_impact_cache()` runs on every request and deletes anything older than three days. |

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

