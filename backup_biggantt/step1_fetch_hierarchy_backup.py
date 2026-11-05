import os
import requests
import json
import urllib3
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from 'env'
possible_paths = ['env', '../env']
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        break
else:
    print("Error: 'env' file not found.")
    exit(1)

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
    BG_DC_JWT_TOKEN = os.getenv("JIRA_DC_OPS_BIGGANTT_API_TOKEN")
else:
    BASE_URL = os.getenv("JIRA_DC_STG_URL")
    BG_DC_JWT_TOKEN = os.getenv("JIRA_DC_STG_BIGGANTT_API_TOKEN")

VERIFY_SSL = False

if not BASE_URL or not BG_DC_JWT_TOKEN:
    print(f"Error: Missing environment variables for {ENV}.")
    exit(1)

# Ensure base URL has the correct prefix for DC
if "/rest/bigpicture/1.0" not in BASE_URL and "/rest/biggantt/1.0" not in BASE_URL:
    BASE_URL = f"{BASE_URL.rstrip('/')}/rest/biggantt/1.0"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_headers():
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {BG_DC_JWT_TOKEN}",
        "Content-Type": "application/json"
    }

def log(message):
    print(message)
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

def fetch_hierarchy():
    log(f"--- Migration Step 1: Fetching Hierarchy {datetime.now()} (DRY_RUN={DRY_RUN}) ---")
    
    if not DRY_RUN:
        # Delete previous files
        for f in ["hierarchy.json", "boxes.json"]:
            if os.path.exists(f):
                os.remove(f)
                log(f"Deleted previous {f}")

    url = f"{BASE_URL.rstrip('/')}/public/ppm/box/box-hierarchy"
    log(f"Fetching hierarchy from: {url}")
    
    try:
        response = requests.get(url, headers=get_headers(), verify=VERIFY_SSL)
        if response.status_code == 401:
            log("ERROR: 401 Unauthorized - Invalid or expired token. Stopping migration.")
            exit(1)
            
        if response.status_code == 200:
            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                log(f"ERROR: Failed to parse JSON. Raw response:\n{response.text[:500]}...")
                exit(1)
            
            if DRY_RUN:
                log("DRY RUN: Connection successful. Skipping file save.")
                return

            # Save raw hierarchy
            with open("hierarchy.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            # Extract flat boxes for summary, excluding archived ones
            all_boxes = []
            archived_count = 0
            unarchived_count = 0
            
            def collect_boxes(node):
                nonlocal archived_count, unarchived_count
                if isinstance(node, dict):
                    is_archived = node.get("boxArchived") is True
                    if is_archived:
                        archived_count += 1
                    else:
                        unarchived_count += 1
                        box_info = {k: v for k, v in node.items() if k != "boxChildren"}
                        all_boxes.append(box_info)
                    for child in node.get("boxChildren", []):
                        collect_boxes(child)
            
            cargo = data.get("cargo", {})
            collect_boxes(cargo)
            
            with open("boxes.json", "w", encoding="utf-8") as f:
                json.dump(all_boxes, f, indent=2, ensure_ascii=False)
                
            # Summary Report
            log("\n" + "="*40)
            log("STEP 1 SUMMARY REPORT")
            log("="*40)
            log(f"Total boxes found: {archived_count + unarchived_count}")
            log(f"  - Archived True: {archived_count}")
            log(f"  - Archived False: {unarchived_count}")
            log(f"Hierarchy saved to: hierarchy.json")
            log(f"Flat list saved to: boxes.json")
            log("="*40)
        else:
            log(f"Error fetching hierarchy: {response.status_code} - {response.text}")
    except Exception as e:
        log(f"Exception during hierarchy fetch: {e}")

if __name__ == "__main__":
    # Initialize log file for a new run
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"--- New Migration Session Started: {datetime.now()} ---\n")
    fetch_hierarchy()
