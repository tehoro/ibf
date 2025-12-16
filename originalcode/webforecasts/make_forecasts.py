#!/usr/bin/env python3
import os
import json
import requests
import urllib.parse
import re
import datetime
import zoneinfo
import time
import logging
import argparse

# Configure logging (optional but good practice)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def simple_markdown(text):
    """
    A simple markdown converter that:
      - Replaces text between ** with <strong> (for bold)
      - Replaces text between * with <em> (for italic)
    """
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    return text

def sanitize_location_name(name):
    """Create a simple folder name by lowercasing and stripping non-alphanumeric characters."""
    return re.sub(r'[^a-z0-9]', '', name.lower())

def load_config(config_file="config.json"):
    config_path = os.path.abspath(config_file) # Get absolute path
    logging.info(f"Attempting to load config from: {config_path}")

    if not os.path.exists(config_path):
        raise Exception(f"Configuration file {config_path} not found.")

    try:
        # --- Debugging: Read and print first few bytes/chars ---
        with open(config_path, "rb") as f_bytes: # Read as bytes first
            first_bytes = f_bytes.read(10)
            logging.info(f"First 10 bytes of config file: {first_bytes}")
        with open(config_path, "r", encoding="utf-8") as f_chars: # Read as text
            first_chars = f_chars.read(10)
            logging.info(f"First 10 characters of config file: {repr(first_chars)}") # Use repr to see invisible chars
        # --- End Debugging ---

        # Now load normally
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        logging.info("Config file JSON parsed successfully.")
        return config_data
    except json.JSONDecodeError as e:
        # Log the error specifically before raising
        logging.error(f"JSONDecodeError loading {config_path}: {e}")
        raise Exception(f"Error decoding JSON from {config_path}: {e}")
    except Exception as e:
        # Log other potential errors during file reading
        logging.error(f"Error reading {config_path}: {e}")
        raise Exception(f"Error reading {config_path}: {e}")

def get_forecast_data(request_url, display_name):
    """Fetches and validates forecast data from the API with retries.
    Returns a tuple: (primary_forecast, translated_forecast, translation_lang, issue_time, ibf_context)
    or (None, None, None, None, None) on failure.
    """
    max_attempts = 3
    attempt = 0
    default_return = (None, None, None, None, None)

    while attempt < max_attempts:
        try:
            logging.info(f"Fetching forecast for '{display_name}' from: {request_url} (Attempt {attempt+1})")
            response = requests.get(request_url, timeout=300)

            if response.status_code != 200:
                error_details = response.text[:200] if response.text else "No further details available."
                logging.error(f"Error fetching forecast for {display_name}: HTTP {response.status_code}. Details: {error_details}")
                return default_return # Indicate failure

            data = response.json()
            forecast_object = None

            # --- Adapt for list (location) or dict (area) response ---
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    forecast_object = data[0]
                else:
                    logging.error(f"Unexpected list format for {display_name}: {data}")
                    return default_return
            elif isinstance(data, dict):
                forecast_object = data
            else:
                 logging.error(f"Response is not a list or dict for {display_name}: {type(data)}")
                 return default_return
            # --- End adaptation ---

            if not forecast_object:
                 logging.error(f"Could not extract forecast object for {display_name}.")
                 return default_return

            # --- Extract Forecast Texts (Primary and Translated) ---
            primary_forecast = None
            translated_forecast = None
            translation_lang = None

            # Check for new primary key first, fallback to old key
            if "text_forecast_en" in forecast_object:
                primary_forecast = forecast_object.get("text_forecast_en", "").strip()
            elif "text_forecast" in forecast_object:
                primary_forecast = forecast_object.get("text_forecast", "").strip()

            # Check for translated text
            if "text_forecast_translated" in forecast_object and "translation_language" in forecast_object:
                translated_forecast = forecast_object.get("text_forecast_translated", "").strip()
                translation_lang = forecast_object.get("translation_language", "").strip()
                if not translated_forecast or not translation_lang:
                    # If one is missing, ignore translation
                    translated_forecast = None
                    translation_lang = None
                    logging.warning(f"Found partial translation keys for {display_name}, ignoring translation.")

            # --- Validate Primary Forecast ---
            if not primary_forecast:
                error_msg = forecast_object.get("error")
                if error_msg:
                    logging.error(f"API returned error for {display_name}: {error_msg}")
                else:
                    logging.error(f"Missing primary forecast text ('text_forecast_en' or 'text_forecast') in API response for {display_name}: {forecast_object}")
                return default_return # Indicate failure

            # Check for specific API-side generation failure message in primary forecast
            if "Forecast generation failed" in primary_forecast:
                attempt += 1
                if attempt < max_attempts:
                    logging.warning(f"Forecast generation failed for {display_name}. Waiting 15 seconds and retrying...")
                    time.sleep(15)
                    continue # Retry the loop
                else:
                    logging.error(f"Forecast generation failed for {display_name} after {max_attempts} attempts.")
                    return default_return # Indicate failure after retries

            # --- Success! Extract issue time. ---
            issue_time = forecast_object.get("issue_time")
            if not issue_time:
                 # Basic timezone guess (improve if needed)
                tz_name = "Pacific/Auckland" if "NZ" in display_name else ("Europe/London" if "England" in display_name else "UTC")
                try:
                    tz = zoneinfo.ZoneInfo(tz_name)
                except zoneinfo.ZoneInfoNotFoundError:
                    logging.warning(f"Timezone '{tz_name}' not found for {display_name}. Defaulting to UTC.")
                    tz = zoneinfo.ZoneInfo("UTC")
                issue_time = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")

            # --- Extract ibf_context if available ---
            ibf_context = forecast_object.get("ibf_context", "").strip() if forecast_object.get("ibf_context") else None

            logging.info(f"Successfully retrieved forecast for {display_name}" + (f" (with {translation_lang} translation)" if translation_lang else "") + (f" (with ibf_context)" if ibf_context else ""))
            return primary_forecast, translated_forecast, translation_lang, issue_time, ibf_context # Return successful data

        except requests.exceptions.Timeout:
             logging.error(f"Request timed out after 300 seconds for {display_name}.")
             return default_return
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error fetching forecast for {display_name}: {e}")
            return default_return
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON response for {display_name}: {e}")
            try:
                logging.error(f"Response text (partial): {response.text[:500]}")
            except NameError: pass
            return default_return
        except Exception as e:
            logging.exception(f"Unexpected exception occurred processing {display_name}: {e}") # Log full traceback
            return default_return

    # Should only reach here if all retries failed specifically due to "Forecast generation failed"
    return default_return

def update_forecast_page(target_dir, file_name, display_name, forecast_text, issue_time, translated_forecast_text=None, translation_language=None, ibf_context=None):
    """Generates and writes the HTML content for a forecast page, including optional translation and ibf_context."""
    if forecast_text is None or issue_time is None:
        logging.warning(f"Skipping page update for {display_name} due to missing data.")
        return

    forecast_html = simple_markdown(forecast_text).lstrip()
    translated_forecast_html = ""
    translation_header_html = ""
    
    # --- Prepare ibf_context if available ---
    ibf_context_html = ""
    if ibf_context:
        # Apply markdown formatting and convert \n to <br> for HTML display
        ibf_context_formatted = simple_markdown(ibf_context).lstrip()
        # Convert \n to <br> after markdown processing
        ibf_context_html = ibf_context_formatted.replace('\n', '<br>')

    # --- Prepare translated content if available ---
    if translated_forecast_text and translation_language:
        logging.info(f"Adding translated ({translation_language}) content for {display_name}")
        translated_forecast_html = simple_markdown(translated_forecast_text).lstrip()
        # Simple language name mapping (can be expanded)
        lang_map = {
            "Fr-CA": "French (Canada)",
            "fr": "French",
            "es": "Spanish",
            "de": "German"
        }
        # Generate header: Use mapped name if available, otherwise just the code.
        # Only add code in parentheses if a different display name was found.
        mapped_name = lang_map.get(translation_language)
        if mapped_name:
            lang_display_name = mapped_name
            header_text = f"Forecast in {lang_display_name} ({translation_language})"
        else:
            lang_display_name = translation_language # Fallback to code
            header_text = f"Forecast in {lang_display_name}"

        translation_header_html = f"\n<h2>{header_text}</h2>\n"
    # --- End translated content preparation ---

    os.makedirs(target_dir, exist_ok=True)
    index_file = os.path.join(target_dir, file_name)

    # --- Add style for translated content and ibf_context (optional) ---
    style_block = """<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8f9fa; color: #212529; margin: 1em auto; padding: 0 1em; max-width: 800px; line-height: 1.6; }
h1 { color: #343a40; border-bottom: 2px solid #dee2e6; padding-bottom: 0.5em; margin-top: 1em; margin-bottom: 1em; font-size: 1.8em; }
h3 { color: #495057; font-size: 1.1em; font-weight: normal; margin-top: -0.5em; margin-bottom: 1em; }
#forecast-content, #translated-forecast-content { background: #ffffff; padding: 1.5em 2em; border: 1px solid #dee2e6; border-radius: 6px; white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 2em; }
#forecast-content strong, #translated-forecast-content strong { color: #0d6efd; font-weight: bold; }
#forecast-content em, #translated-forecast-content em { color: #6f42c1; font-style: italic; }
#translated-forecast-content { margin-top: 1em; border-top: 3px solid #6c757d; padding-top: 1.5em; } /* Style for translated section */
#ibf-context-wrapper { margin-bottom: 2em; }
#ibf-context-header { background: #ffffff; padding: 1em 1.5em; border: 1px solid #dee2e6; border-radius: 6px 6px 0 0; cursor: pointer; user-select: none; display: flex; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
#ibf-context-header:hover { background: #f8f9fa; }
#ibf-context-toggle { font-size: 0.8em; margin-right: 0.8em; transition: transform 0.2s; color: #495057; }
#ibf-context-toggle.expanded { transform: rotate(90deg); }
#ibf-context-header-text { color: #343a40; font-weight: 500; }
#ibf-context-content { display: none; margin-top: 0; background: #ffffff; border-top: 1px solid #dee2e6; border-radius: 0 0 6px 6px; padding: 1.5em 2em; white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
#ibf-context-content.expanded { display: block; }
h2 { color: #343a40; margin-top: 1.5em; margin-bottom: 0.8em; font-size: 1.4em; } /* Style for translation header */
a { color: #0d6efd; text-decoration: none; font-weight: 500; }
a:hover { text-decoration: underline; color: #0a58ca; }
.footer-note { margin-top: 2.5em; padding-top: 1em; border-top: 1px solid #dee2e6; font-size: 0.9em; color: #6c757d; text-align: center; }
.footer-note a { font-weight: normal; }
hr { display: none; }
@media (max-width: 600px) { body { margin: 0.5em; padding: 0 0.8em; } h1 { font-size: 1.5em; } #forecast-content, #translated-forecast-content, #ibf-context-content { padding: 1em 1.2em; } h2 { font-size: 1.2em;} }
</style>"""

    # --- Construct the main HTML body ---
    html_body_parts = [
        f"<h1>Forecast for {display_name}</h1>",
        f"<h3>Issued: {issue_time}</h3>",
        f"<div id=\"forecast-content\">{forecast_html}</div>"
    ]

    # --- Append translated section if available ---
    if translated_forecast_html and translation_header_html:
        html_body_parts.append(translation_header_html)
        html_body_parts.append(f"<div id=\"translated-forecast-content\">{translated_forecast_html}</div>")

    # --- Append ibf_context section if available ---
    if ibf_context_html:
        html_body_parts.append("""<div id="ibf-context-wrapper">
  <div id="ibf-context-header" onclick="toggleIbfContext()">
    <span id="ibf-context-toggle">▶</span>
    <span id="ibf-context-header-text">Impact-Based Forecast Context</span>
  </div>
  <div id="ibf-context-content">""" + ibf_context_html + """</div>
</div>""")

    # --- Add footer and closing tags ---
    html_body_parts.extend([
        f"<p><a href=\"../index.html\">Return to Menu</a></p>",
        f"<div class=\"footer-note\">",
        f"  All forecasts &copy; Neil Gordon. Data courtesy of <a href=\"https://open-meteo.com/\" target=\"_blank\" rel=\"noopener\">open-meteo.com</a>,",
        f"  using <a href=\"https://apps.ecmwf.int/datasets/licences/general/\" target=\"_blank\" rel=\"noopener\">ECMWF ensemble open data</a>.",
        f"  <br>If you want to interactively request a forecast for a location, go to the <a href=\"https://chatgpt.com/g/g-4OgZFHOPA-global-ensemble-weather-forecaster\" target=\"_blank\" rel=\"noopener\">Global Ensemble Weather Forecaster</a>. (Requires a ChatGPT account - free or paid).",
        f"</div>"
    ])

    # --- Add JavaScript for toggle functionality ---
    script_block = """<script>
function toggleIbfContext() {
  const content = document.getElementById('ibf-context-content');
  const toggle = document.getElementById('ibf-context-toggle');
  if (content.classList.contains('expanded')) {
    content.classList.remove('expanded');
    toggle.classList.remove('expanded');
  } else {
    content.classList.add('expanded');
    toggle.classList.add('expanded');
  }
}
</script>"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Forecast for {display_name}</title>
  {style_block}
</head>
<body>
{'\n'.join(html_body_parts)}
{script_block}
</body>
</html>
"""
    try:
        with open(index_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        logging.info(f"Updated forecast page for {display_name}" + (", including translation" if translated_forecast_html else "") + (", including ibf_context" if ibf_context_html else ""))
    except IOError as e:
        logging.error(f"Error writing file {index_file}: {e}")


def update_all_forecasts(config_file):
    """Loads config and updates forecasts for all locations and areas."""
    try:
        config = load_config(config_file)
    except Exception as e:
        logging.critical(f"Fatal error loading configuration: {e}") # Use critical for loading fail
        return

    api_url = config.get("api_url")
    web_root = config.get("web_root")
    llm_param = config.get("llm", "")
    location_days = config.get("location_forecast_days", "")
    area_days = config.get("area_forecast_days", "")
    # --- Add wordiness parameters ---
    location_wordiness = config.get("location_wordiness", "normal").lower()
    area_wordiness = config.get("area_wordiness", "normal").lower()
    valid_wordiness = ["brief", "normal", "detailed"]
    # --- End wordiness parameters ---
    # --- Add reasoning level parameters ---
    enable_reasoning = config.get("enable_reasoning", True)
    location_reasoning = config.get("location_reasoning", "medium").lower()
    area_reasoning = config.get("area_reasoning", "medium").lower()
    valid_reasoning = ["low", "medium", "high"]
    # --- End reasoning level parameters ---
    # --- Add impact-based parameters ---
    location_impact_based = config.get("location_impact_based", "false").lower() == "true"
    area_impact_based = config.get("area_impact_based", "false").lower() == "true"
    # --- End impact-based parameters ---
    # --- Add thin_select parameters ---
    location_thin_select = config.get("location_thin_select")
    area_thin_select = config.get("area_thin_select")
    # Default to 16 if present but empty/invalid, None if absent
    if location_thin_select is not None:
        try:
            location_thin_select = str(int(location_thin_select)) if location_thin_select else "16"
        except (ValueError, TypeError):
            location_thin_select = "16"
    if area_thin_select is not None:
        try:
            area_thin_select = str(int(area_thin_select)) if area_thin_select else "16"
        except (ValueError, TypeError):
            area_thin_select = "16"
    # --- End thin_select parameters ---
    # --- Add recent overwrite parameter ---
    recent_overwrite_minutes = int(config.get("recent_overwrite_minutes", "0"))
    recent_overwrite_seconds = recent_overwrite_minutes * 60
    # --- End recent overwrite parameter ---

    if not api_url or not web_root:
        logging.critical("Error: 'api_url' or 'web_root' missing from configuration.")
        return

    # --- Process Individual Locations ---
    locations = config.get("locations", [])
    logging.info(f"\n--- Updating {len(locations)} Individual Location Forecasts ---")
    for loc_info in locations: # Iterate over location info objects
        if not isinstance(loc_info, dict) or "name" not in loc_info:
            logging.warning(f"Skipping invalid location entry: {loc_info}")
            continue

        loc_name = loc_info.get("name") # Get the name
        loc_lang = loc_info.get("lang") # Get optional language code
        logging.info(f"Processing location: {loc_name}" + (f" (Requesting lang: {loc_lang})" if loc_lang else ""))

        # --- Add timestamp check ---
        folder_name = sanitize_location_name(loc_name) # Use loc_name
        target_dir = os.path.join(web_root, folder_name)
        index_file_path = os.path.join(target_dir, "index.html")
        if recent_overwrite_seconds > 0 and os.path.exists(index_file_path):
            try:
                file_mod_time = os.path.getmtime(index_file_path)
                current_time = time.time()
                age_seconds = current_time - file_mod_time
                if age_seconds < recent_overwrite_seconds:
                    logging.info(f"Skipping '{loc_name}': Forecast file is recent (updated {int(age_seconds/60)} minutes ago, threshold: {recent_overwrite_minutes} minutes).") # Use loc_name
                    continue # Skip to the next location
            except OSError as e:
                logging.warning(f"Could not get modification time for {index_file_path}: {e}")
        # --- End timestamp check ---

        encoded_location = urllib.parse.quote(loc_name) # Use loc_name
        request_url = f"{api_url}?location={encoded_location}"
        if llm_param:
            request_url += f"&llm={urllib.parse.quote(llm_param)}"
        if location_days:
            request_url += f"&forecast_days={urllib.parse.quote(location_days)}"
        # --- Append location wordiness if valid and not normal ---
        if location_wordiness in valid_wordiness and location_wordiness != "normal":
            logging.info(f"Setting wordiness={location_wordiness} for location: {loc_name}") # Use loc_name
            request_url += f"&wordiness={location_wordiness}"
        # --- End wordiness append ---
        # --- Append location reasoning if enabled and valid ---
        if enable_reasoning and location_reasoning in valid_reasoning:
            logging.info(f"Setting reasoning={location_reasoning} for location: {loc_name}") # Use loc_name
            request_url += f"&reasoning={location_reasoning}"
        # --- End reasoning append ---
        # --- Append location impact_based if enabled ---
        if location_impact_based:
            logging.info(f"Setting impact_based=yes for location: {loc_name}") # Use loc_name
            request_url += f"&impact_based=yes"
        # --- End impact_based append ---
        # --- Append location thin_select if specified ---
        if location_thin_select is not None:
            logging.info(f"Setting thin_select={location_thin_select} for location: {loc_name}") # Use loc_name
            request_url += f"&thin_select={location_thin_select}"
        # --- End thin_select append ---

        # --- Add location-specific unit parameters ---
        loc_units = loc_info.get("units")
        if isinstance(loc_units, dict):
            logging.info(f"Applying specific units for location '{loc_name}': {loc_units}") # Use loc_name
            for unit_key, unit_value in loc_units.items():
                if unit_value: # Only add if value is not empty
                     # Validate known unit keys
                     if unit_key in ["windspeed_unit", "temperature_unit", "precipitation_unit"]:
                         request_url += f"&{unit_key}={urllib.parse.quote(str(unit_value))}"
                     else:
                         logging.warning(f"Ignoring unknown unit key '{unit_key}' for location '{loc_name}'") # Use loc_name
        # --- End unit parameter addition ---

        # --- Add language parameter if specified ---
        if loc_lang:
            logging.info(f"Adding lang={loc_lang} parameter for location '{loc_name}'")
            request_url += f"&lang={urllib.parse.quote(str(loc_lang))}"
        # --- End language parameter addition ---

        primary_forecast, translated_forecast, translation_lang, issue_time, ibf_context = get_forecast_data(request_url, loc_name) # Use loc_name

        if primary_forecast is not None:
            folder_name = sanitize_location_name(loc_name) # Use loc_name
            target_dir = os.path.join(web_root, folder_name)
            # Pass translation info and ibf_context to update_forecast_page
            update_forecast_page(target_dir, "index.html", loc_name, primary_forecast, issue_time, translated_forecast, translation_lang, ibf_context)
        else:
            logging.error(f"Failed to get forecast for location: {loc_name}. Page not updated.") # Use loc_name


    # --- Process Areas ---
    areas = config.get("areas", [])
    logging.info(f"\n--- Updating {len(areas)} Area Forecasts ---")
    for area_info in areas:
        area_name = area_info.get("name")
        area_locations = area_info.get("locations")
        area_lang = area_info.get("lang") # Get optional language code for area
        if not area_name or not isinstance(area_locations, list) or not area_locations: # Ensure locations is a non-empty list
            logging.warning(f"Skipping invalid area entry: {area_info}")
            continue

        logging.info(f"Processing area: {area_name}" + (f" (Requesting lang: {area_lang})" if area_lang else ""))

        # --- Add timestamp check ---
        folder_name = sanitize_location_name(area_name)
        target_dir = os.path.join(web_root, folder_name)
        index_file_path = os.path.join(target_dir, "index.html")
        if recent_overwrite_seconds > 0 and os.path.exists(index_file_path):
            try:
                file_mod_time = os.path.getmtime(index_file_path)
                current_time = time.time()
                age_seconds = current_time - file_mod_time
                if age_seconds < recent_overwrite_seconds:
                    logging.info(f"Skipping '{area_name}': Forecast file is recent (updated {int(age_seconds/60)} minutes ago, threshold: {recent_overwrite_minutes} minutes).")
                    continue # Skip to the next area
            except OSError as e:
                logging.warning(f"Could not get modification time for {index_file_path}: {e}")
        # --- End timestamp check ---

        encoded_area_name = urllib.parse.quote(area_name)
        # Ensure locations are strings before quoting
        encoded_area_locations = ":".join(urllib.parse.quote(str(loc)) for loc in area_locations)

        request_url = f"{api_url}?area={encoded_area_name}&location={encoded_area_locations}"
        if llm_param:
             request_url += f"&llm={urllib.parse.quote(llm_param)}"
        if area_days:
             request_url += f"&forecast_days={urllib.parse.quote(area_days)}"
        # --- Append area wordiness if valid and not normal ---
        if area_wordiness in valid_wordiness and area_wordiness != "normal":
            logging.info(f"Setting wordiness={area_wordiness} for area: {area_name}")
            request_url += f"&wordiness={area_wordiness}"
        # --- End wordiness append ---
        # --- Append area reasoning if enabled and valid ---
        if enable_reasoning and area_reasoning in valid_reasoning:
            logging.info(f"Setting reasoning={area_reasoning} for area: {area_name}")
            request_url += f"&reasoning={area_reasoning}"
        # --- End reasoning append ---
        # --- Append area impact_based if enabled ---
        if area_impact_based:
            logging.info(f"Setting impact_based=yes for area: {area_name}")
            request_url += f"&impact_based=yes"
        # --- End impact_based append ---
        # --- Append area thin_select if specified ---
        if area_thin_select is not None:
            logging.info(f"Setting thin_select={area_thin_select} for area: {area_name}")
            request_url += f"&thin_select={area_thin_select}"
        # --- End thin_select append ---

        # --- Add area-specific unit parameters ---
        area_units = area_info.get("units")
        if isinstance(area_units, dict):
            logging.info(f"Applying specific units for area '{area_name}': {area_units}")
            for unit_key, unit_value in area_units.items():
                if unit_value: # Only add if value is not empty
                     # Validate known unit keys (optional but good practice)
                     if unit_key in ["windspeed_unit", "temperature_unit", "precipitation_unit"]:
                         request_url += f"&{unit_key}={urllib.parse.quote(str(unit_value))}"
                     else:
                         logging.warning(f"Ignoring unknown unit key '{unit_key}' for area '{area_name}'")
        # --- End unit parameter addition ---

        # --- Add language parameter if specified for area ---
        if area_lang:
            logging.info(f"Adding lang={area_lang} parameter for area '{area_name}'")
            request_url += f"&lang={urllib.parse.quote(str(area_lang))}"
        # --- End language parameter addition ---

        primary_forecast, translated_forecast, translation_lang, issue_time, ibf_context = get_forecast_data(request_url, area_name)

        if primary_forecast is not None:
            folder_name = sanitize_location_name(area_name)
            target_dir = os.path.join(web_root, folder_name)
            # Pass translation info and ibf_context to update_forecast_page
            update_forecast_page(target_dir, "index.html", area_name, primary_forecast, issue_time, translated_forecast, translation_lang, ibf_context)
        else:
             logging.error(f"Failed to get forecast for area: {area_name}. Page not updated.")


    logging.info("\n--- All forecast updates complete. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate weather forecast HTML pages.")
    parser.add_argument(
        "-f", "--config",
        default="config.json",
        help="Path to the configuration file (default: config.json)"
    )
    args = parser.parse_args()

    update_all_forecasts(args.config)