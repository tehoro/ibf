#!/usr/bin/env python3
"""
mapAreas.py - Generate PNG maps for each area in config.json showing location points.

This script reads config.json, geocodes location names, and creates maps with
terrain shading using folium, then exports them as PNG files.
"""

import os
import json
import logging
import argparse
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import folium
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Add a delay between geocoding requests to avoid rate limiting
GEOCODING_DELAY = 1.0  # seconds

def sanitize_filename(name):
    """Create a safe filename from an area name."""
    import re
    # Replace spaces and special chars with underscores, keep alphanumeric
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', name)
    return safe_name

def geocode_location(location_name, geolocator, max_retries=3):
    """
    Geocode a location name to lat/lon coordinates.
    Returns (lat, lon) tuple or None if geocoding fails.
    """
    for attempt in range(max_retries):
        try:
            logging.info(f"Geocoding '{location_name}' (attempt {attempt + 1})...")
            location = geolocator.geocode(location_name, timeout=10)
            if location:
                logging.info(f"  Found: {location.latitude}, {location.longitude}")
                time.sleep(GEOCODING_DELAY)  # Rate limiting
                return (location.latitude, location.longitude)
            else:
                logging.warning(f"  Could not geocode '{location_name}'")
                return None
        except (GeocoderTimedOut, GeocoderServiceError) as e:
            logging.warning(f"  Geocoding error for '{location_name}': {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logging.error(f"  Failed to geocode '{location_name}' after {max_retries} attempts")
                return None
        except Exception as e:
            logging.error(f"  Unexpected error geocoding '{location_name}': {e}")
            return None
    
    return None

def create_map(locations_coords, area_name):
    """
    Create a folium map with terrain tiles and markers for all locations.
    Returns the folium Map object.
    """
    if not locations_coords:
        logging.warning(f"No valid coordinates for area '{area_name}'")
        return None
    
    # Calculate center point and bounds
    lats = [coord[0] for coord in locations_coords.values()]
    lons = [coord[1] for coord in locations_coords.values()]
    
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    
    # Create map with OpenTopoMap (terrain tiles)
    m = folium.Map(
        location=[center_lat, center_lon],
        tiles='OpenTopoMap',
        zoom_start=8,
        attr='OpenTopoMap, &copy; OpenStreetMap contributors'
    )
    
    # Add alternative tile layers for better terrain visualization
    folium.TileLayer(
        tiles='https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
        attr='OpenTopoMap',
        name='OpenTopoMap',
        overlay=False,
        control=True
    ).add_to(m)
    
    # Add ESRI World Imagery with terrain
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri',
        name='Satellite',
        overlay=False,
        control=True
    ).add_to(m)
    
    # Add default OpenStreetMap layer
    folium.TileLayer(
        tiles='OpenStreetMap',
        name='OpenStreetMap',
        overlay=False,
        control=True
    ).add_to(m)
    
    # Add layer control
    folium.LayerControl().add_to(m)
    
    # Add markers for each location
    for location_name, (lat, lon) in locations_coords.items():
        # Create a popup with the location name
        popup = folium.Popup(location_name, max_width=300)
        
        # Add marker with custom icon
        folium.Marker(
            [lat, lon],
            popup=popup,
            tooltip=location_name,
            icon=folium.Icon(color='red', icon='info-sign')
        ).add_to(m)
    
    # Fit map bounds to show all markers
    if len(locations_coords) > 1:
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(20, 20))
    
    # Add title
    title_html = f'''
    <div style="position: fixed; 
                top: 10px; left: 50px; width: 300px; height: 60px; 
                background-color: white; border:2px solid grey; z-index:9999; 
                font-size:16px; padding: 10px; border-radius: 5px;">
    <b>{area_name}</b><br>
    {len(locations_coords)} locations
    </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))
    
    return m

def html_to_png(html_file, output_png, width=1920, height=1080):
    """
    Convert an HTML file (folium map) to PNG using headless Chrome.
    
    Note: Requires ChromeDriver to be installed and available in PATH.
    On macOS: brew install chromedriver
    """
    try:
        # Setup Chrome options for headless mode
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument(f'--window-size={width},{height}')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-software-rasterizer')
        
        # Create webdriver
        try:
            driver = webdriver.Chrome(options=chrome_options)
        except Exception as e:
            logging.error(f"Failed to initialize ChromeDriver: {e}")
            logging.error("Please ensure ChromeDriver is installed:")
            logging.error("  macOS: brew install chromedriver")
            logging.error("  Linux: sudo apt-get install chromium-chromedriver")
            logging.error("  Or download from: https://chromedriver.chromium.org/")
            raise
        
        # Load the HTML file
        file_path = f"file://{os.path.abspath(html_file)}"
        driver.get(file_path)
        
        # Wait for map to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "leaflet-container"))
        )
        
        # Additional wait for tiles to load
        time.sleep(3)
        
        # Take screenshot
        driver.save_screenshot(output_png)
        driver.quit()
        
        logging.info(f"Successfully created PNG: {output_png}")
        return True
        
    except Exception as e:
        logging.error(f"Error converting HTML to PNG: {e}")
        try:
            driver.quit()
        except:
            pass
        return False

def process_area(area_info, geolocator, output_dir):
    """
    Process a single area: geocode locations, create map, and save as PNG.
    """
    area_name = area_info.get("name")
    locations = area_info.get("locations", [])
    
    if not area_name:
        logging.warning("Skipping area with no name")
        return False
    
    if not locations:
        logging.warning(f"Area '{area_name}' has no locations")
        return False
    
    logging.info(f"\nProcessing area: {area_name} ({len(locations)} locations)")
    
    # Geocode all locations
    locations_coords = {}
    failed_locations = []
    
    for location_name in locations:
        coords = geocode_location(location_name, geolocator)
        if coords:
            locations_coords[location_name] = coords
        else:
            failed_locations.append(location_name)
    
    if failed_locations:
        logging.warning(f"Failed to geocode {len(failed_locations)} locations: {failed_locations}")
    
    if not locations_coords:
        logging.error(f"No valid coordinates found for area '{area_name}'. Skipping.")
        return False
    
    # Create map
    logging.info(f"Creating map for '{area_name}'...")
    m = create_map(locations_coords, area_name)
    
    if not m:
        logging.error(f"Failed to create map for '{area_name}'")
        return False
    
    # Save HTML temporarily
    safe_filename = sanitize_filename(area_name)
    html_file = os.path.join(output_dir, f"{safe_filename}_temp.html")
    png_file = os.path.join(output_dir, f"{safe_filename}.png")
    
    m.save(html_file)
    logging.info(f"Saved HTML map: {html_file}")
    
    # Convert HTML to PNG
    logging.info(f"Converting to PNG: {png_file}...")
    success = html_to_png(html_file, png_file)
    
    # Clean up temporary HTML file
    try:
        os.remove(html_file)
    except:
        pass
    
    return success

def load_config(config_file):
    """Load configuration from JSON file."""
    config_path = os.path.abspath(config_file)
    logging.info(f"Loading config from: {config_path}")
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    return config

def main(config_file="config.json", output_dir=None):
    """
    Main function to process all areas and generate maps.
    """
    # Load config
    try:
        config = load_config(config_file)
    except Exception as e:
        logging.error(f"Error loading config: {e}")
        return
    
    # Get areas
    areas = config.get("areas", [])
    if not areas:
        logging.warning("No areas found in config.json")
        return
    
    logging.info(f"Found {len(areas)} areas to process")
    
    # Setup output directory
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(config_file)), "maps")
    
    os.makedirs(output_dir, exist_ok=True)
    logging.info(f"Output directory: {output_dir}")
    
    # Initialize geocoder
    geolocator = Nominatim(user_agent="mapAreas_geocoder")
    
    # Process each area
    success_count = 0
    for area_info in areas:
        if process_area(area_info, geolocator, output_dir):
            success_count += 1
    
    logging.info(f"\n=== Complete ===")
    logging.info(f"Successfully processed {success_count} out of {len(areas)} areas")
    logging.info(f"Maps saved to: {output_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate PNG maps for each area showing location points with terrain."
    )
    parser.add_argument(
        "-f", "--config",
        default="config.json",
        help="Path to the configuration file (default: config.json)"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output directory for PNG files (default: ./maps)"
    )
    args = parser.parse_args()
    
    main(args.config, args.output)

