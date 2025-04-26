import os
from flask import Flask, render_template, send_from_directory, request, jsonify
import json
from collections import defaultdict

app = Flask(__name__, template_folder="templates")

# --- Path Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Image Directory: Serve existing images bundled with the app code
# Assumes 'saved_images' is in the same directory as app.py
SAVED_IMAGES_DIR = os.path.join(BASE_DIR, 'saved_images')

# JSON Data File Path Configuration:
# Define the path for the JSON file *on the persistent disk*
# Render provides the mount path via the 'RENDER_DISK_MOUNT_PATH' env var.
# Default to a local path ('./data') for development if the env var isn't set.
DISK_MOUNT_PATH = os.environ.get('RENDER_DISK_MOUNT_PATH', './local_render_data') # Use env var, fallback for local dev
JSON_FILENAME = 'leads_data_persistent.json' # Name for the file on the disk
JSON_FILE_ON_DISK = os.path.join(DISK_MOUNT_PATH, JSON_FILENAME)

# Define the path to the initial/template JSON file bundled with your code
INITIAL_JSON_FILE = os.path.join(BASE_DIR, 'leads_with_awnings.json') # Assumes it's next to app.py

# Ensure the directory on the disk exists (Render creates the mount path, but maybe not subdirs)
# For local testing, this creates the ./local_render_data directory
os.makedirs(DISK_MOUNT_PATH, exist_ok=True)
# --- End Path Configuration ---


# Function to load leads from the JSON file on the persistent disk
def load_leads():
    target_path = JSON_FILE_ON_DISK
    
    # If the file doesn't exist on the disk yet, try to copy it from the initial bundled file
    if not os.path.exists(target_path):
        print(f"JSON file not found on disk at {target_path}. Attempting to initialize from {INITIAL_JSON_FILE}...")
        if os.path.exists(INITIAL_JSON_FILE):
            try:
                with open(INITIAL_JSON_FILE, 'r', encoding='utf-8') as f_initial:
                    initial_leads = json.load(f_initial)
                if save_leads(initial_leads): # Try saving the initial data to the disk path
                     print(f"Successfully initialized JSON file on disk at {target_path}.")
                     return initial_leads # Return the loaded initial leads
                else:
                    print(f"Failed to write initial JSON file to disk at {target_path}.")
                    return [] # Failed to initialize
            except Exception as e:
                 print(f"Error reading or writing initial JSON file {INITIAL_JSON_FILE}: {e}")
                 return [] # Error during initialization
        else:
            print(f"Initial JSON file {INITIAL_JSON_FILE} not found. Cannot initialize disk file.")
            return [] # Cannot initialize

    # If the file exists on disk, load from there
    try:
        with open(target_path, 'r', encoding='utf-8') as f:
            leads = json.load(f)
            # Add default CRM fields if they are missing (good practice)
            for lead in leads:
                lead.setdefault('status', 'New')
                lead.setdefault('notes', '')
                lead.setdefault('follow_up', False)
            return leads
    except json.JSONDecodeError:
        print(f"Error decoding JSON from {target_path}. Returning empty list.")
        return []
    except Exception as e:
        print(f"An error occurred while loading leads from {target_path}: {e}")
        return []

# Function to save leads back to the JSON file on the persistent disk
def save_leads(leads):
    target_path = JSON_FILE_ON_DISK
    try:
        # Write to a temporary file first, then rename, for atomicity
        temp_path = target_path + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(leads, f, indent=2)
        os.replace(temp_path, target_path) # Atomic replace
        print(f"Successfully saved leads to {target_path}")
        return True
    except Exception as e:
        print(f"An error occurred while saving leads to {target_path}: {e}")
        # Clean up temp file if rename failed
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False

# Custom route to serve images from the SAVED_IMAGES_DIR (bundled with code)
@app.route('/images/<path:filename>')
def images(filename):
    if '..' in filename:
        return "Invalid filename", 400
    # Serve directly from the directory bundled with the app code
    print(f"Attempting to serve image: {filename} from {SAVED_IMAGES_DIR}")
    try:
        return send_from_directory(SAVED_IMAGES_DIR, filename)
    except FileNotFoundError:
         print(f"Image not found: {os.path.join(SAVED_IMAGES_DIR, filename)}")
         from flask import abort
         abort(404)


@app.route('/')
def index():
    leads = load_leads()
    grouped_leads = defaultdict(list)
    for lead in leads:
        city = lead.get('city', 'Unknown City')
        grouped_leads[city].append(lead)
    return render_template('index.html', grouped_leads=dict(grouped_leads))


# --- CRM Update Routes (No changes needed in the logic itself) ---
@app.route('/update_lead', methods=['POST'])
def update_lead():
    data = request.json
    city = data.get('city')
    index_in_city = data.get('index')
    field = data.get('field')
    value = data.get('value')

    if city is None or index_in_city is None or field is None or value is None:
        return jsonify({'success': False, 'message': 'Invalid data'}), 400

    leads = load_leads() # Load current state from disk

    found_lead = None
    current_index_in_city = 0
    for lead in leads:
        if lead.get('city', 'Unknown City') == city:
            if current_index_in_city == index_in_city:
                found_lead = lead
                break
            current_index_in_city += 1

    if not found_lead:
        return jsonify({'success': False, 'message': 'Lead not found'}), 404

    if field in ['status', 'notes', 'follow_up']:
        if field == 'follow_up':
             found_lead[field] = bool(value)
        else:
             found_lead[field] = value
    else:
        return jsonify({'success': False, 'message': 'Invalid field specified'}), 400

    # Save updated list back to disk
    if save_leads(leads):
        return jsonify({'success': True, 'message': 'Lead updated successfully'})
    else:
        return jsonify({'success': False, 'message': 'Failed to save leads'}), 500


@app.route('/delete_lead', methods=['POST'])
def delete_lead():
    data = request.json
    city = data.get('city')
    index_in_city = data.get('index')

    if city is None or index_in_city is None:
        return jsonify({'success': False, 'message': 'Invalid data'}), 400

    leads = load_leads() # Load current state from disk
    
    found_index = None
    current_index_in_city = 0
    for i, lead in enumerate(leads):
        if lead.get('city', 'Unknown City') == city:
            if current_index_in_city == int(index_in_city):
                found_index = i
                break
            current_index_in_city += 1

    if found_index is None:
        return jsonify({'success': False, 'message': 'Lead not found'}), 404

    removed_lead = leads.pop(found_index)
    
    # Save updated list back to disk
    if save_leads(leads):
        return jsonify({
            'success': True, 
            'message': f'Lead "{removed_lead.get("name", "Unknown")}" removed successfully'
        })
    else:
        return jsonify({'success': False, 'message': 'Failed to save leads'}), 500

# Remove the __main__ block or keep it only for local testing
# Render uses the Procfile, it does not execute this block
# if __name__ == '__main__':
#     print(f"Local development mode. Data directory: {DISK_MOUNT_PATH}")
#     # Local testing: Create dummy data if the initial file doesn't exist AND the disk file doesn't exist
#     if not os.path.exists(INITIAL_JSON_FILE) and not os.path.exists(JSON_FILE_ON_DISK):
#          print("Creating dummy initial JSON for local testing...")
#          # Create dummy leads_with_awnings.json if needed for local run
#     elif not os.path.exists(JSON_FILE_ON_DISK):
#          print("Attempting to initialize local disk file...")
#          load_leads() # Trigger the copy logic for local dev
         
#     app.run(debug=True, host='0.0.0.0', port=5000) # Use 0.0.0.0 for local network access if needed