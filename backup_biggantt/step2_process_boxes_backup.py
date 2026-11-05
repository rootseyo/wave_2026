import os
import requests
import json
import urllib3
import time
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables
possible_paths = ['env', '../env']
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        break
else:
    print("Error: 'env' file not found.")
    exit(1)

# Resolve migration files from the project root so this backup script can be
# launched from either the project root or the backup directory.
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = (
    os.path.dirname(script_dir)
    if os.path.basename(script_dir).lower() == "backup"
    else script_dir
)
HIERARCHY_FILE = os.path.join(project_root, "hierarchy.json")
PROJECTS_FILE = os.path.join(project_root, "projects.txt")
MIGRATION_BOX_FILE = os.path.join(project_root, "migration_box.json")

def select_environment():
    print("\nSelect Source Environment:")
    print("1. STG (Staging)")
    print("2. OPS (Operations)")
    choice = input("Enter choice (1/2): ").strip()
    if choice == '2':
        return "OPS"
    return "STG"

def select_dry_run():
    choice = input("Dry-run mode? (y/n): ").lower().strip()
    return choice == 'y'

ENV = select_environment()
DRY_RUN = select_dry_run()

if ENV == "OPS":
    BASE_URL = os.getenv("JIRA_DC_OPS_URL")
    JIRA_DC_PAT = os.getenv("JIRA_DC_OPS_API_TOKEN")
    BG_DC_JWT_TOKEN = os.getenv("JIRA_DC_OPS_BIGGANTT_API_TOKEN")
else:
    BASE_URL = os.getenv("JIRA_DC_STG_URL")
    JIRA_DC_PAT = os.getenv("JIRA_DC_STG_API_TOKEN")
    BG_DC_JWT_TOKEN = os.getenv("JIRA_DC_STG_BIGGANTT_API_TOKEN")

if not BASE_URL or not JIRA_DC_PAT or not BG_DC_JWT_TOKEN:
    print(f"Error: Missing environment variables for {ENV}.")
    exit(1)

VERIFY_SSL = False

# Ensure base URL has the correct prefix for DC BigGantt
if "/rest/bigpicture/1.0" not in BASE_URL and "/rest/biggantt/1.0" not in BASE_URL:
    BG_BASE_URL = f"{BASE_URL.rstrip('/')}/rest/biggantt/1.0"
else:
    BG_BASE_URL = BASE_URL

# Jira Core API Base
JIRA_CORE_URL = BASE_URL.split('/rest/')[0] + "/rest/api/2"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Tracking metrics
stats = {
    "total_boxes_in_hierarchy": 0,
    "processed": 0,
    "matched": 0,
    "failed": 0
}

def get_headers(token, prefix="Bearer"):
    return {
        "accept": "application/json",
        "Authorization": f"{prefix} {token}",
        "Content-Type": "application/json"
    }

def log(message):
    print(message)
    log_file = os.path.join(project_root, os.path.basename(__file__).replace(".py", ".log"))
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

def fetch_json(url, token, prefix="Bearer", max_retries=5):
    log(f"  Fetching: {url}")
    for attempt in range(max_retries):
        try:
            # Minimal delay for performance, relying on 429 handling for throttling
            time.sleep(0.1) 
            
            response = requests.get(url, headers=get_headers(token, prefix), verify=VERIFY_SSL)
            
            if response.status_code == 200:
                return response.json()
            
            if response.status_code == 429:
                # Dynamic wait: increases with each 429 response
                wait_time = (attempt + 1) * 2 
                log(f"    [429] Rate limited. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue

            if response.status_code == 401:
                log(f"ERROR: 401 Unauthorized for URL: {url}. Stopping migration.")
                exit(1)
            
            log(f"    Error: {response.status_code}")
            return None
        except Exception as e:
            log(f"    Exception: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            return None
    return None

def get_user_info(user_key):
    if not user_key:
        return None, None
    url = f"{JIRA_CORE_URL}/user?key={user_key}"
    # Use Jira PAT for Core API
    user_data = fetch_json(url, token=JIRA_DC_PAT, prefix="Bearer")
    if user_data:
        email = user_data.get("emailAddress")
        display_name = user_data.get("displayName")
        return email, display_name
    return None, None

project_archived_cache = {}

def is_project_archived(project_key):
    if not project_key:
        return False
    project_key = project_key.upper()
    if project_key in project_archived_cache:
        return project_archived_cache[project_key]
    
    url = f"{JIRA_CORE_URL}/project/{project_key}"
    # Use Jira PAT for Core API
    project_data = fetch_json(url, token=JIRA_DC_PAT, prefix="Bearer")
    if project_data:
        is_archived = project_data.get("archived", False)
        project_archived_cache[project_key] = is_archived
        return is_archived
    return False

def extract_project_keys(scope_data):
    keys = []
    if not scope_data:
        return keys
    
    cargo = scope_data.get("cargo", {})
    platforms = cargo.get("extPlatformScopeDefinitions", [])
    for plat in platforms:
        elements = plat.get("scopeDefinitionElements", [])
        for el in elements:
            if el.get("type") == "JIRA_PROJECT":
                url = el.get("url", "")
                project_key = url.split("/")[-1] if url else ""
                if project_key:
                    keys.append(project_key.upper())
    return keys

def load_target_projects():
    if not os.path.exists(PROJECTS_FILE):
        log(f"Warning: projects.txt not found: {PROJECTS_FILE}")
        return set()
    with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
        return {line.strip().upper() for line in f if line.strip()}

def fetch_box_types():
    url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/type"
    log(f"Fetching box types from: {url}")
    types_data = fetch_json(url, token=BG_DC_JWT_TOKEN)
    
    type_map = {}
    if types_data and isinstance(types_data, dict):
        # Expecting { "cargo": [ { "id": 1, "name": "Program" }, ... ] }
        cargo = types_data.get("cargo", [])
        for t in cargo:
            type_id = str(t.get("id"))
            type_name = t.get("name")
            type_map[type_id] = type_name
            log(f"  Type Mapping: {type_id} -> {type_name}")
    return type_map

def format_date(date_str):
    if not date_str:
        return None
    try:
        # Expected input: 2024-07-25
        # Desired output: 25/JUL/24
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%b/%y").upper()
    except Exception:
        return date_str

def process_node(node, target_projects, migration_boxes, box_type_map, parent_id=None):
    if node.get("boxArchived") is True:
        return

    stats["processed"] += 1
    box_id = node.get("boxId")
    box_name = node.get("boxName")
    log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id}")

    scope_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/area/task/scope/def/own/{box_id}"
    scope_data = fetch_json(scope_url, token=BG_DC_JWT_TOKEN)
    
    if scope_data:
        box_project_keys = extract_project_keys(scope_data)
        # Filter out archived Jira projects
        active_project_keys = [key for key in box_project_keys if not is_project_archived(key)]
        is_migration_target = any(key in target_projects for key in active_project_keys)
        
        if is_migration_target:
            stats["matched"] += 1
            log(f"  -> Match found! (Active Keys: {active_project_keys})")
            
            cargo = scope_data.get("cargo", {})
            platforms = cargo.get("extPlatformScopeDefinitions", [])
            owner_email = None
            owner_name = None
            if platforms:
                owner_key = platforms[0].get("scopeOwner", {}).get("key")
                if owner_key:
                    owner_email, owner_name = get_user_info(owner_key)
            
            box_type_id = node.get("boxTypeId")
            box_type_name = box_type_map.get(str(box_type_id), box_type_id)

            migration_entry = {
                "boxId": box_id,
                "boxName": box_name,
                "boxType": box_type_name,
                "boxStartDate": format_date(node.get("boxStartDate")),
                "boxEndDate": format_date(node.get("boxEndDate")),
                "owner_email": owner_email,
                "owner_name": owner_name,
                "jira_projects": active_project_keys
            }
            migration_boxes.append(migration_entry)
        else:
            log(f"  -> Not a target project or target projects are archived.")
    else:
        stats["failed"] += 1

    children = node.get("boxChildren") or []
    for child in children:
        process_node(child, target_projects, migration_boxes, box_type_map, parent_id=box_id)

def main():
    # Append to log file
    log_file = os.path.join(project_root, os.path.basename(__file__).replace(".py", ".log"))
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n--- Migration Step 2 Log: {datetime.now()} (DRY_RUN={DRY_RUN}) ---\n")

    if not DRY_RUN:
        # Delete previous files
        if os.path.exists(MIGRATION_BOX_FILE):
            os.remove(MIGRATION_BOX_FILE)
            log(f"Deleted previous migration_box.json: {MIGRATION_BOX_FILE}")

    if not os.path.exists(HIERARCHY_FILE):
        log(f"Error: hierarchy.json not found: {HIERARCHY_FILE}. Run Step 1 first.")
        return

    target_projects = load_target_projects()
    log(f"Loaded {len(target_projects)} target projects from projects.txt")

    # Fetch box types for name mapping
    box_type_map = fetch_box_types()

    migration_boxes = []
    
    # Use hierarchy.json (which we saved in Step 1)
    if os.path.exists(HIERARCHY_FILE):
        with open(HIERARCHY_FILE, "r", encoding="utf-8") as f:
            h_data = json.load(f)
            root_node = h_data.get("cargo", {})
            # Start recursion with ROOT as the implied parent of top-level boxes
            process_node(root_node, target_projects, migration_boxes, box_type_map, parent_id=None)
    else:
        log(f"Error: hierarchy.json not found: {HIERARCHY_FILE}. Run Step 1 first.")
        return
    
    if DRY_RUN:
        log("\nDRY RUN: Processing complete. Found the following matches (not saved to file):")
        for box in migration_boxes:
            log(f"  - {box['boxName']} ({box['boxType']})")
    else:
        with open(MIGRATION_BOX_FILE, "w", encoding="utf-8") as f:
            json.dump(migration_boxes, f, indent=2, ensure_ascii=False)
    
    # Summary Report
    log("\n" + "="*40)
    log("STEP 2 SUMMARY REPORT")
    log("="*40)
    log(f"Target projects defined: {len(target_projects)}")
    log(f"Boxes scanned: {stats['processed']}")
    log(f"Boxes matched for migration: {stats['matched']}")
    log(f"Processing failures: {stats['failed']}")
    if not DRY_RUN:
        log(f"Migration list saved to: {MIGRATION_BOX_FILE}")
    else:
        log(f"DRY RUN: No files were modified.")
    log("="*40)

if __name__ == "__main__":
    main()
