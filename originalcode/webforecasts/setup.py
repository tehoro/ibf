	#!/usr/bin/env python3
import os
import json
import re
import logging
import traceback
import argparse

# Configure logging to output timestamps, log level, and messages.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def sanitize_location_name(location):
    """
    Create a simple folder name by lowercasing and removing non-alphanumeric characters.
    Handles both simple location strings and area names.
    """
    return re.sub(r'[^a-z0-9]', '', location.lower())

def create_placeholder_index(index_file_path, name_for_title):
    """Creates a placeholder index.html file."""
    with open(index_file_path, "w", encoding="utf-8") as f:
        logging.info(f"Writing placeholder index file for '{name_for_title}' at: {index_file_path}")
        # Using a more generic title "Forecast" as it could be location or area
        f.write(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Forecast for {name_for_title}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      background: #f7f7f7;
      color: #333;
      margin: 0 auto;
      padding: 20px;
      max-width: 800px;
    }}
    h1 {{
      color: #2F4F4F;
      margin-top: 0;
    }}
    #forecast-content {{
      background: #ffffff;
      padding: 20px;
      border: 1px solid #ccc;
      border-radius: 5px;
      white-space: pre-wrap;
      word-wrap: break-word;
      line-height: 1.4em;
    }}
    a {{
      color: #0066cc;
      text-decoration: none;
      font-weight: bold;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .footer-note {{
      margin-top: 20px;
      font-size: 0.9em;
      color: #666;
    }}
  </style>
</head>
<body>
  <h1>Forecast for {name_for_title}</h1>
  <div id="forecast-content">
    <p>Forecast will be updated here.</p>
  </div>
  <p><a href="../index.html">Return to Menu</a></p>
</body>
</html>
""")

def create_directories_and_main_index(config):
    web_root = config.get("web_root")
    logging.info(f"Using web_root: {web_root}")
    if not os.path.exists(web_root):
        os.makedirs(web_root)
        logging.info(f"Created web root directory: {web_root}")
    else:
        logging.info(f"Web root directory already exists: {web_root}")

    # Process individual locations
    locations = config.get("locations", [])
    logging.info(f"Processing {len(locations)} individual locations...")
    for loc_info in locations: # Iterate over location info objects
        if not isinstance(loc_info, dict) or "name" not in loc_info:
            logging.warning(f"Skipping invalid location entry: {loc_info}")
            continue
        loc_name = loc_info["name"] # Extract name

        folder_name = sanitize_location_name(loc_name) # Sanitize the name
        loc_dir = os.path.join(web_root, folder_name)
        if not os.path.exists(loc_dir):
            os.makedirs(loc_dir)
            logging.info(f"Created directory for location '{loc_name}' at: {loc_dir}") # Log with loc_name
            index_file_path = os.path.join(loc_dir, "index.html")
            create_placeholder_index(index_file_path, loc_name) # Pass location name for title
        else:
            logging.info(f"Directory for location '{loc_name}' already exists: {loc_dir}. Skipping placeholder index creation.") # Log with loc_name

    # Process areas
    areas = config.get("areas", [])
    logging.info(f"Processing {len(areas)} areas...")
    for area_info in areas:
        area_name = area_info.get("name")
        if not area_name:
            logging.warning("Skipping area entry with no name.")
            continue
        folder_name = sanitize_location_name(area_name)
        area_dir = os.path.join(web_root, folder_name)
        if not os.path.exists(area_dir):
            os.makedirs(area_dir)
            logging.info(f"Created directory for area '{area_name}' at: {area_dir}")
            index_file_path = os.path.join(area_dir, "index.html")
            create_placeholder_index(index_file_path, area_name) # Pass area name for title
        else:
            logging.info(f"Directory for area '{area_name}' already exists: {area_dir}. Skipping placeholder index creation.")


    # Create/Overwrite main index.html with separate lists for locations and areas
    main_index_path = os.path.join(web_root, "index.html")
    logging.info(f"Creating/updating main index file at: {main_index_path}")
    with open(main_index_path, "w", encoding="utf-8") as f:
        # Basic HTML structure and styles
        f.write(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Weather Forecast Menu</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f7f7f7; color: #333; margin: 0 auto; padding: 20px; max-width: 800px; }}
    h1, h2 {{ color: #2F4F4F; margin-top: 1.5em; margin-bottom: 0.5em; }}
    h1 {{ margin-top: 0; }}
    ul {{ list-style-type: none; padding: 0; }}
    li {{ margin: 10px 0; font-size: 18px; }} /* Slightly smaller font */
    a {{ color: #0066cc; text-decoration: none; font-weight: bold; }}
    a:hover {{ text-decoration: underline; }}
    hr {{ margin: 2em 0; border: 0; border-top: 1px solid #ccc; }}
    .footer-note {{ margin-top: 20px; font-size: 0.9em; color: #666; text-align: center; }}
    .footer-note a {{ font-weight: normal; }}
  </style>
</head>
<body>
  <h1>Weather Forecast Menu</h1>
""")
        # --- Locations List ---
        if locations:
            f.write("<h2>Locations</h2>\n<ul>\n")
            for loc_info in locations: # Iterate over location info objects
                if not isinstance(loc_info, dict) or "name" not in loc_info:
                    continue # Skip invalid entries silently here, already warned above
                loc_name = loc_info["name"] # Extract name

                folder_name = sanitize_location_name(loc_name) # Sanitize the name
                # Display name logic: Remove ', NZ' suffix, otherwise use full name
                if loc_name.endswith(", NZ"):
                    display_name = loc_name.replace(", NZ", "")
                else:
                    display_name = loc_name
                f.write(f'    <li><a href="{folder_name}/index.html">{display_name}</a></li>\n')
            f.write("</ul>\n")

        # --- Areas List ---
        if areas:
            f.write("<h2>Areas</h2>\n<ul>\n")
            for area_info in areas:
                area_name = area_info.get("name")
                if area_name:
                    folder_name = sanitize_location_name(area_name)
                    display_name = area_name # Use the full area name for display
                    f.write(f'    <li><a href="{folder_name}/index.html">{display_name}</a></li>\n')
            f.write("</ul>\n")

        # --- Footer ---
        f.write(f"""
  <hr>
  <p class="footer-note">For any feedback please email <a href="mailto:neil.gordon@hey.com?subject=Feedback%20On%20Ensemble%20Text%20Forecasts">Neil Gordon</a>.</p>
  <div class="footer-note">
    All forecasts &copy; Neil Gordon. Data courtesy of <a href="https://open-meteo.com/" target="_blank" rel="noopener">open-meteo.com</a>,
    using <a href="https://apps.ecmwf.int/datasets/licences/general/" target="_blank" rel="noopener">ECMWF ensemble open data</a>.
  </div>
</body>
</html>
""")
    logging.info("Main index file created successfully.")

def main(config_file):
    # config_file = "config.json" # Remove hardcoded value
    
    # Add detailed logging about config file location
    working_dir = os.getcwd()
    config_path = os.path.abspath(os.path.join(working_dir, config_file)) # Ensure absolute path construction
    logging.info(f"--- Starting setup using config file: {config_path} ---") # Log start and path
    
    # Check if file exists and log more details
    if not os.path.exists(config_path):
        logging.error(f"Configuration file not found at path: {config_path}")
        logging.info("Listing files in current directory:")
        try:
            for file in os.listdir(working_dir):
                logging.info(f"  - {file}")
        except Exception as e:
            logging.error(f"Error listing directory contents: {e}")
        return
    
    # Log file size and basic info and load config
    try:
        file_size = os.path.getsize(config_path)
        logging.info(f"Config file size: {file_size} bytes.")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        logging.info("Config file loaded successfully.")
        # --- Add logging for config structure ---
        config_summary = {k: type(v).__name__ for k, v in config.items()}
        logging.info(f"Loaded config keys and types: {config_summary}")
        # --- End logging for config structure ---
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {config_path}: {e}")
        return
    except Exception as e:
        logging.error(f"Failed to read or process config file {config_path}: {e}")
        traceback.print_exc() # Print detailed traceback
        return

    # Proceed with using the loaded config
    try:
        create_directories_and_main_index(config)
    except Exception as e:
        logging.error(f"An error occurred during setup: {e}")
        traceback.print_exc()
    else: # Add else block for successful completion message
        logging.info("--- Setup script finished successfully. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up directory structure and index files for weather forecasts.")
    parser.add_argument(
        "-f", "--config",
        default="config.json",
        help="Path to the configuration file (default: config.json)"
    )
    args = parser.parse_args()

    main(args.config) # Pass the parsed config file path