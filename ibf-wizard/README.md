## IBF Wizard (local NiceGUI helper)

`ibf-wizard` is a local, browser-based helper that guides you through:
- Checking prerequisites (Python, uv, ibf).
- Creating/updating `.env` with required API keys.
- Creating or editing `config.json`/`.yaml` with validation via IBF's Pydantic models.
- Optionally launching `uv run ibf run --config ...` and streaming logs.

### Step 1: Install & run the wizard (no IBF required yet)
Using uv (recommended on macOS/Homebrew Python):
```bash
cd /path/to/ibf-wizard
uv venv .venv
source .venv/bin/activate
uv pip install -e . --no-deps
ibf-wizard --workspace /path/to/ibf    # create the workspace folder if it doesn't exist
```
Once it’s on PyPI you can simply `pip install ibf-wizard` and run `ibf-wizard ...`.

### Step 2: Let the wizard guide IBF setup
- Prereqs tab: shows what’s missing (`uv`, `ibf`, terrain asset). If IBF is not installed, the tab reminds you to install it.
- .env tab: paste API keys (masked) and save.
- Config tab: load/create JSON/YAML configs, load `examples/sample-config.json`, validate/save (validation requires IBF; without IBF it will still save but note the missing validator). Saves make a `.bak` backup.
- Run tab: once IBF is installed, run `uv run ibf run --config ...` and watch logs live.

### What it does
- Runs a local NiceGUI on `localhost` (no cloud components).
- Surfaces prereq checks: `uv`, `ibf`, terrain dataset presence.
- .env editor with masked fields for the common IBF keys (OpenAI, OpenRouter, Gemini, DeepInfra, OpenWeatherMap, Google).
- Config editor: load existing JSON/YAML, load the bundled `examples/sample-config.json`, validate with IBF models, then save (with `.bak` backup).
- Run helper: executes `uv run ibf run --config ...` in the selected workspace and streams logs live.

### Notes
- Secrets stay local; the wizard only reads/writes files under the selected workspace.
- For JSON/YAML configs, validation uses the IBF models. Invalid fields are surfaced inline.
- Map screenshot support in IBF still requires Chrome+chromedriver if you enable folium maps. The wizard will surface that prerequisite in the run helper panel.
- Config and .env saves create a `.bak` sibling before overwriting the file.
