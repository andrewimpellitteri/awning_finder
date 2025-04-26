import googlemaps
import requests
import time
import os
import io
import math
from PIL import Image # Needed to load image bytes for moondream library
import moondream as md # Import the moondream library
import json

# --- Configuration ---

# --- !!! SECURITY WARNING !!! ---
# --- DO NOT HARDCODE REAL KEYS ---
# --- Use Environment Variables or Secrets Management ---
Maps_API_KEY = os.environ.get("Maps_API_KEY", "YOUR_Maps_API_KEY") # Replace or set env var
# --- !!! END SECURITY WARNING !!! ---

# Define the log file for processed place_ids
PROCESSED_LOG_FILENAME = "processed_places.txt"

# --- Local Moondream Server Details ---
# !!! IMPORTANT: Replace with the actual URL your local server is running on !!!
MOONDREAM_LOCAL_ENDPOINT = os.environ.get("MOONDREAM_ENDPOINT", "http://localhost:2020/v1") # Common default if using their server script directly
VISION_PROMPT = "Analyze this street view image of a business. Does the building display a fabric awning — a cloth covering attached above the storefront? It should not be metal or vinyl. Answer with only YES or NO. If the presence is uncertain or the awning is only partially visible, answer NO. Do not include any additional text."

# --- End Local Moondream Server Details ---

JSON_OUTPUT_FILENAME = "leads_with_awnings.json" # <--- Define the output filename
IMAGES_DIR = "saved_images"
if not os.path.exists(IMAGES_DIR):
    os.makedirs(IMAGES_DIR)


# New Haven Location & Search Parameters
CITIES_TO_SEARCH = {
    "New Haven": (41.3083, -72.9279),
    "Fairfield": (41.147, -73.251639)
}
SEARCH_RADIUS_METERS = 4000 # Adjust as needed
BUSINESS_TYPES = [
    'restaurant', 'cafe', 'store', 'bakery', 'bar', 'clothing_store',
    'convenience_store', 'florist', 'hardware_store', 'book_store',
    'shoe_store', 'gift_shop'
]
# BUSINESS_TYPES = [
#     'restaurant',
#     'cafe',
#     'bakery',
#     'bar',
#     'clothing_store',
#     'convenience_store',
#     'florist',
#     'hardware_store',
#     'book_store',
#     'shoe_store',
#     'gift_shop',
#     'boutique',
#     'pizza_restaurant',
#     'deli',
#     'nail_salon',
#     'beauty_salon',
#     'massage_therapist',
#     'art_gallery',
#     'pharmacy',
#     'dry_cleaner',
#     'jewelry_store',
#     'optician',
#     'pet_store',
#     'ice_cream_shop',
#     'tea_house',
#     'tattoo_parlor',
#     'barber_shop',
#     'wine_store',
#     'furniture_store',
#     'home_goods_store',
#     'stationery_store',
#     'toy_store',
#     'candy_store',
#     'liquor_store'
# ]

# BUSINESS_TYPES = ['restaurant']
# Street View Image Parameters
STREET_VIEW_SIZE = "800x600"
STREET_VIEW_FOV = 90

# Delays to respect potential rate limits (in seconds)
DELAY_BETWEEN_PLACES_PAGES = 1
DELAY_AFTER_DETAIL_REQUEST = 0.1
DELAY_AFTER_STREETVIEW_REQUEST = 0.5
DELAY_AFTER_VISION_REQUEST = 0 # Can likely be faster with local model, adjust if needed
CAMERA_DISTANCE = 15  # Distance in meters from the business for camera placement

# --- Global Moondream Client (Optional Optimization) ---
# Initialize once here to potentially improve performance vs initializing in the loop
# If the server connection needs frequent re-establishment, keep initialization inside the function
moondream_client = None
try:
    print(f"Attempting to connect to local Moondream server at {MOONDREAM_LOCAL_ENDPOINT}...")
    # Note: md.vl might not actually establish a connection here, but on first use.
    moondream_client = md.vl(endpoint=MOONDREAM_LOCAL_ENDPOINT)
    print("Moondream client initialized (connection will be tested on first use).")
except Exception as e:
    print(f"ERROR: Could not initialize Moondream client. Is the server running at {MOONDREAM_LOCAL_ENDPOINT}? Error: {e}")
    # The script will attempt to initialize again inside the function if this fails.


# --- Helper Functions ---

def get_place_details(gmaps_client, place_id):
    """Fetches detailed information for a place."""
    try:
        fields = ['name', 'formatted_address', 'formatted_phone_number', 'geometry/location', 'place_id', 'url']
        details = gmaps_client.place(place_id=place_id, fields=fields)
        time.sleep(DELAY_AFTER_DETAIL_REQUEST) # Small delay
        return details.get('result', {})
    except Exception as e:
        print(f"    WARN: Could not retrieve details for Place ID {place_id}: {e}")
        return None

def get_street_view_with_targeted_heading(place_location, size, fov, api_key, heading_offsets=[-25, 0, 25]):
    """
    Gets Street View images using headings calculated towards the business,
    plus offsets to the left and right.

    Args:
        place_location: Dict with 'lat' and 'lng' of the business
        size: Image size string (e.g., "800x600")
        fov: Field of view integer
        api_key: Google Maps API key
        heading_offsets: List of degree offsets relative to the calculated heading.
                         Example: [-25, 0, 25] will try 25deg left, center, 25deg right.

    Returns:
        List of tuples, where each tuple is (heading, image_bytes),
        or an empty list if no suitable images are found.
    """
    if not place_location or not api_key or api_key == "YOUR_Maps_API_KEY":
        print("    Skipping Street View: Missing location or API key.")
        return []

    lat = place_location.get('lat')
    lng = place_location.get('lng')
    if lat is None or lng is None:
        print("    Skipping Street View: Missing lat/lng.")
        return []

    base_url = "https://maps.googleapis.com/maps/api/streetview"
    metadata_url = "https://maps.googleapis.com/maps/api/streetview/metadata"

    # 1. Get Metadata for the nearest panorama
    metadata_params = {
        "location": f"{lat},{lng}",
        "key": api_key,
        "source": "outdoor" # Prefer outdoor panoramas
    }
    pano_lat, pano_lng = None, None
    try:
        metadata_response = requests.get(metadata_url, params=metadata_params, timeout=10)
        metadata = metadata_response.json()

        if metadata.get('status') != 'OK':
            print(f"    No Street View metadata found near business location ({metadata.get('status')}).")
            # Optional: Could try adding a radius to metadata search here, e.g., "radius": 50
            return []

        pano_lat = float(metadata.get('location', {}).get('lat'))
        pano_lng = float(metadata.get('location', {}).get('lng'))
        print(f"    Found panorama at ({pano_lat:.5f}, {pano_lng:.5f}).")

    except Exception as e:
        print(f"    ERROR: Request failed for Street View metadata: {e}")
        return []

    # 2. Calculate the base heading from panorama to business
    y = math.sin(math.radians(lng - pano_lng)) * math.cos(math.radians(lat))
    x = math.cos(math.radians(pano_lat)) * math.sin(math.radians(lat)) - \
        math.sin(math.radians(pano_lat)) * math.cos(math.radians(lat)) * math.cos(math.radians(lng - pano_lng))
    bearing = math.atan2(y, x)
    base_heading = (math.degrees(bearing) + 360) % 360
    print(f"    Calculated base heading to business: {base_heading:.1f}°")

    # 3. Request images for each heading offset
    results = []
    for offset in heading_offsets:
        current_heading = (base_heading + offset + 360) % 360
        heading_int = int(round(current_heading)) # API expects integer heading

        params = {
            # Use the actual panorama location for the request
            "location": f"{pano_lat},{pano_lng}",
            "size": size,
            "fov": fov,
            "heading": str(heading_int),
            "pitch": "0", # Keep pitch level for simplicity
            "key": api_key,
            "source": "outdoor"
        }

        try:
            print(f"    Requesting Street View image with heading {heading_int}° (offset {offset}°)...")
            response = requests.get(base_url, params=params, timeout=15)
            time.sleep(DELAY_AFTER_STREETVIEW_REQUEST) # Respect delay

            if response.status_code == 200:
                # Basic check for valid image vs. "no image" placeholder
                if len(response.content) > 1000:
                    print(f"      --> Success (heading {heading_int}°).")
                    results.append((heading_int, response.content))
                else:
                    print(f"      --> Placeholder image received (heading {heading_int}°).")
            elif response.status_code == 404:
                 print(f"      --> Image not found (404) for heading {heading_int}°.")
            else:
                print(f"    WARN: Street View API returned status {response.status_code} for heading {heading_int}°")

        except Exception as e:
            print(f"    ERROR: Request failed for Street View image (heading {heading_int}°): {e}")

    if not results:
        print("    No valid Street View images retrieved after checking bracketed headings.")

    return results

def get_place_photos(gmaps_client, place_id, max_photos=2):
    """Gets photos specific to the place from Google Places API as a fallback"""
    try:
        place_details = gmaps_client.place(
            place_id=place_id, 
            fields=['photos']
        )
        photos = place_details.get('result', {}).get('photos', [])
        photo_references = [p.get('photo_reference') for p in photos[:max_photos]]
        
        photo_bytes_list = []
        for i, ref in enumerate(photo_references):
            if ref:
                photo_url = f"https://maps.googleapis.com/maps/api/place/photo?maxwidth=600&photoreference={ref}&key={Maps_API_KEY}"
                response = requests.get(photo_url, timeout=15)
                if response.status_code == 200:
                    print(f"    Place photo {i+1} retrieved.")
                    photo_bytes_list.append((f"place_photo_{i}", response.content))
        
        return photo_bytes_list
    except Exception as e:
        print(f"    Error fetching place photos: {e}")
        return []

def analyze_image_with_local_moondream(image_bytes, prompt, local_endpoint):
    """
    Analyzes an image using a local Moondream server via the moondream library.
    """
    global moondream_client # Use the globally initialized client if available

    if not image_bytes:
        print("    Skipping vision analysis: Missing image data.")
        return False

    try:
        # Initialize client if global initialization failed or wasn't done
        if moondream_client is None:
            print(f"    Initializing Moondream client for endpoint {local_endpoint}...")
            client = md.vl(endpoint=local_endpoint)
        else:
            client = moondream_client # Use globally initialized client

        # Load image bytes into a PIL Image object
        image = Image.open(io.BytesIO(image_bytes))

        print(f"    Sending image to local Moondream server (prompt: '{prompt}')...")
        # Use the 'ask' method for question answering
        answer = client.query(image, prompt)
        answer = answer["answer"]
        time.sleep(DELAY_AFTER_VISION_REQUEST) # Small delay

        print(f"    Local Moondream server response: '{answer}'")

        # Parse the response - assumes simple YES/NO answer based on prompt
        if answer and "YES" in answer.strip().upper():
            return True
        else:
            return False

    except Exception as e:
        print(f"    ERROR: Failed during local Moondream analysis: {e}")
        print(f"    Is the Moondream server running and accessible at {local_endpoint}?")
        # If connection fails consistently, prevent further attempts by nullifying global client
        if "connection" in str(e).lower():
            moondream_client = None
            print("    Cleared global Moondream client due to connection error.")
        return False


# --- Main Execution Logic ---

def main():
    """Main function to find leads and analyze images."""
    global moondream_client # Allow main to potentially clear the client on widespread failure

    if Maps_API_KEY == "YOUR_Maps_API_KEY":
        print("ERROR: Please configure your Maps_API_KEY in the script or environment variables.")
        return

    # Check if we even have a valid moondream client configuration attempt
    # The actual connection test happens on first use inside analyze_image_with_local_moondream
    if moondream_client is None:
         # Attempt to initialize again here just in case global failed silently before print
         try:
             moondream_client = md.vl(endpoint=MOONDREAM_LOCAL_ENDPOINT)
             print("Moondream client late initialization attempt successful (connection pending use).")
         except Exception as e:
            print(f"ERROR: Failed to initialize Moondream client in main. Vision analysis will likely fail. Error: {e}")
            # Proceed without vision if client can't be initialized, or exit? Let's proceed but it will fail later.


    print("Initializing Google Maps Client...")
    try:
        gmaps = googlemaps.Client(key=Maps_API_KEY)
    except Exception as e:
        print(f"FATAL: Error initializing Google Maps client: {e}")
        return

    # Load already processed place_ids
    processed_place_ids = set()
    if os.path.exists(PROCESSED_LOG_FILENAME):
        with open(PROCESSED_LOG_FILENAME, 'r') as f:
            for line in f:
                place_id = line.strip()
                if place_id:
                    processed_place_ids.add(place_id)
    print(f"Loaded {len(processed_place_ids)} previously processed place IDs from {PROCESSED_LOG_FILENAME}")


    all_potential_places = {} # Use dict keyed by place_id to avoid duplicates

    for city_name, city_location in CITIES_TO_SEARCH.items():
        print(f"\n{'='*20} Processing City: {city_name} {'='*20}")

        print("Searching for businesses in New Haven...")
        # (Places search loop remains the same as the previous version)
        for biz_type in BUSINESS_TYPES:
            print(f"\n--- Searching for type: {biz_type} ---")
            try:
                response = gmaps.places_nearby(location=city_location, radius=SEARCH_RADIUS_METERS, type=biz_type)
                next_page_token = response.get('next_page_token')
                places_this_page = response.get('results', [])

                while True:
                    print(f"  Processing {len(places_this_page)} potential places...")
                    for place_summary in places_this_page:
                        place_id = place_summary.get('place_id')
                        if place_id and place_id not in processed_place_ids:
                            processed_place_ids.add(place_id)
                            # print(f"  Found new potential: {place_summary.get('name', 'N/A')} (ID: {place_id})")
                            place_details = get_place_details(gmaps, place_id)
                            if place_details:
                                all_potential_places[place_id] = place_details
                        elif place_id:
                            print(f"  Skipping already processed place: {place_summary.get('name', 'N/A')} (ID: {place_id})")

                    if next_page_token:
                        # print("  Fetching next page...")
                        time.sleep(DELAY_BETWEEN_PLACES_PAGES)
                        response = gmaps.places_nearby(page_token=next_page_token)
                        next_page_token = response.get('next_page_token')
                        places_this_page = response.get('results', [])
                    else:
                        break
            except Exception as e:
                print(f"ERROR: An error occurred searching for {biz_type}: {e}")


        print(f"\nFound {len(all_potential_places)} unique potential businesses. Now checking Street View and Local Moondream...")

        leads_with_awnings = []
        checked_count = 0
        awning_found_count = 0
        vision_connection_failed_persistently = False

        for place_id, place_info in all_potential_places.items():
            checked_count += 1
            print(f"\n[{checked_count}/{len(all_potential_places)}] Checking: {place_info.get('name', 'N/A')}")

            if vision_connection_failed_persistently:
                print("    Skipping vision check due to persistent connection errors.")
                continue # Skip remaining checks if server seems down

            place_location = place_info.get('geometry', {}).get('location')
            if not place_location:
                print("    Skipping: No location data available.")
                continue

            # 1. Get Street View Images with targeted heading
            image_data_list = get_street_view_with_targeted_heading(place_location, STREET_VIEW_SIZE, STREET_VIEW_FOV, Maps_API_KEY)
            
            # If no street view images were found, try place photos as a fallback
            if not image_data_list:
                print("    No suitable Street View images found. Trying Place Photos API as fallback...")
                image_data_list = get_place_photos(gmaps, place_id)

            if image_data_list:
                awning_detected_for_place = False # Flag to track if an awning was found in any heading
                saved_image_paths = []
                
                for heading, image_bytes in image_data_list:
                    # 2. Analyze each image with Local Moondream
                    has_awning = analyze_image_with_local_moondream(image_bytes, VISION_PROMPT, MOONDREAM_LOCAL_ENDPOINT)

                    # Check if the global client got cleared due to connection error
                    if moondream_client is None and not vision_connection_failed_persistently:
                        print("    WARN: Moondream connection error detected. Further vision checks may be skipped.")
                        vision_connection_failed_persistently = True # Assume server is down

                    if has_awning:
                        if not awning_detected_for_place:
                            awning_found_count += 1
                            print(f"    >>> Awning DETECTED for {place_info.get('name', 'N/A')} (heading {heading})!")
                            awning_detected_for_place = True

                            # Generate a base filename based on the place_id
                            base_image_filename = f"{IMAGES_DIR}/{place_id}"

                        # Generate a filename with the heading
                        image_filename = f"{base_image_filename}_heading_{heading}.jpg"

                        try:
                            with open(image_filename, "wb") as img_file:
                                img_file.write(image_bytes)
                            print(f"    Saved image (heading {heading}) to {image_filename}")
                            saved_image_paths.append(image_filename)
                        except Exception as e:
                            print(f"    ERROR: Could not save image (heading {heading}) for {place_info.get('name', 'N/A')}: {e}")

                
                if awning_detected_for_place:
                    # Make sure we have a valid saved_image_paths even if saving failed
                    if not saved_image_paths:
                        saved_image_paths = ["N/A"]
                    
                    # Create the new lead data
                    new_lead = {
                        'name': place_info.get('name', 'N/A'),
                        'address': place_info.get('formatted_address', 'N/A'),
                        'phone': place_info.get('formatted_phone_number', 'N/A'),
                        'Maps_url': place_info.get('url', 'N/A'),
                        'place_id': place_id,
                        'city': city_name,
                        'image_filepaths': saved_image_paths  # Recording file paths in JSON
                    }
                    
                    # Add the new lead to our in-memory list
                    leads_with_awnings.append(new_lead)

                    # Save progress after each successful identification
                    print("\n" + "="*60)
                    print(f"Saving {len(leads_with_awnings)} leads to {JSON_OUTPUT_FILENAME}...")
                    
                    try:
                        # Load existing data if the file exists
                        existing_leads = []
                        if os.path.exists(JSON_OUTPUT_FILENAME) and os.path.getsize(JSON_OUTPUT_FILENAME) > 0:
                            try:
                                with open(JSON_OUTPUT_FILENAME, 'r', encoding='utf-8') as f:
                                    existing_leads = json.load(f)
                                if not isinstance(existing_leads, list):
                                    print(f"WARNING: Existing data in {JSON_OUTPUT_FILENAME} is not a valid JSON array. Creating new file.")
                                    existing_leads = []
                            except json.JSONDecodeError:
                                print(f"WARNING: Could not parse existing JSON in {JSON_OUTPUT_FILENAME}. Creating new file.")
                                existing_leads = []
                        
                        # Write all data (existing + new) to the file
                        with open(JSON_OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
                            # Only append the new lead to existing data
                            if existing_leads:
                                # Check if this place_id already exists to avoid duplicates
                                existing_ids = {lead.get('place_id') for lead in existing_leads}
                                if new_lead['place_id'] not in existing_ids:
                                    existing_leads.append(new_lead)
                            else:
                                existing_leads = leads_with_awnings  # Use all current leads if no existing data
                            
                            json.dump(existing_leads, f, ensure_ascii=False, indent=4)
                        
                        print(f"Successfully saved {len(existing_leads)} total leads to {JSON_OUTPUT_FILENAME}")
                    
                    except IOError as e:
                        print(f"ERROR: Could not write to file {JSON_OUTPUT_FILENAME}: {e}")
                    except Exception as e:
                        print(f"ERROR: An unexpected error occurred during JSON saving: {e}")
                    
                    print("="*60)

            else:
                print("    Skipping vision analysis: Could not retrieve any images.")

            # Record that this place has been processed
            with open(PROCESSED_LOG_FILENAME, 'a') as f:
                f.write(place_id + '\n')


    # --- Print Final Results ---
    print("\n" + "="*60)
    print(f"Processing Complete. Checked {checked_count} businesses.")
    print(f"Potential Leads with Awnings Detected by Local Moondream: {awning_found_count}")
    if vision_connection_failed_persistently:
         print("WARNING: Vision analysis was stopped early due to Moondream server connection issues.")
    print("="*60)


if __name__ == "__main__":
    main()