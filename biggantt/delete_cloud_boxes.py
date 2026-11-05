import os
import requests
import json
import urllib3
import time
from dotenv import load_dotenv

# Load environment variables
script_dir = os.path.dirname(os.path.abspath(__file__)) if __file__ else '.'
possible_paths = [
    os.path.join(script_dir, '..', 'env'),
    os.path.join(script_dir, 'env'),
    '../env',
    'env'
]

ENV_FILE = None
for path in possible_paths:
    if os.path.exists(path):
        ENV_FILE = path
        load_dotenv(dotenv_path=path)
        break

if ENV_FILE:
    print(f"Loaded environment variables from '{ENV_FILE}'.")
else:
    print("Error: 'env' file not found.")
    exit(1)

def select_environment():
    sandbox_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or "Not configured"
    ops_url = os.getenv("JIRA_CLOUD_OPS_URL") or "Not configured"
    
    print("\nSelect Target Cloud Environment:")
    print(f"1. Sandbox (SANDBOX) - {sandbox_url}")
    print(f"2. Production/Operations (OPS) - {ops_url}")
    choice = input("Enter choice (1/2, default 1): ").strip()
    if choice == '2':
        return "OPS"
    return "SANDBOX"

ENV = select_environment()
if ENV == "OPS":
    CLOUD_BIGGANTT_TOKEN = os.getenv("JIRA_BIGGANTT_OPS_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
    ENV_LABEL = "Production/Operations (OPS)"
else:
    CLOUD_BIGGANTT_TOKEN = os.getenv("JIRA_BIGGANTT_SANDBOX_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
    ENV_LABEL = "Sandbox (SANDBOX)"

CLOUD_API_BASE = "https://api.bigpicture.one/public/biggantt/rest/public/ppm"

if not CLOUD_BIGGANTT_TOKEN:
    print(f"Error: Missing JIRA_BIGGANTT_{ENV}_API_TOKEN (or JIRA_BIGGANTT_API_TOKEN) in env file.")
    exit(1)

print(f"Target Environment: {ENV_LABEL}")

VERIFY_SSL = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_cloud_headers():
    return {
        "accept": "application/json",
        "Authorization": f"APIToken {CLOUD_BIGGANTT_TOKEN}",
        "Content-Type": "application/json"
    }

def get_all_box_ids():
    url = f"{CLOUD_API_BASE.rstrip('/')}/box/box-hierarchy"
    print(f"\nFetching hierarchy from URL: {url}")
    max_retries = 5
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=get_cloud_headers(), verify=VERIFY_SSL)
            if resp.status_code == 200:
                data = resp.json()
                box_ids = []
                
                # Support both wrapped in 'cargo' (DC/legacy) and direct root object (OpenAPI schema)
                cargo = data.get("cargo")
                root_node = cargo if cargo is not None else data
                
                children = root_node.get("boxChildren", [])
                for child in children:
                    if isinstance(child, dict):
                        b_id = child.get("boxId")
                    elif isinstance(child, (str, int)):
                        b_id = str(child)
                    else:
                        b_id = None
                        
                    if b_id:
                        box_ids.append(b_id)
                return box_ids
            elif resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait_time = int(retry_after)
                else:
                    wait_time = (attempt + 1) * 3
                print(f"[429 Too Many Requests] Retrying hierarchy fetch in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                print(f"Failed to fetch hierarchy: {resp.status_code} - {resp.text}")
                break
        except Exception as e:
            print(f"Exception during hierarchy fetch: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            break
    return []

def delete_boxes(box_ids):
    if not box_ids:
        print("No boxes to delete.")
        return

    print(f"\nDeleting {len(box_ids)} top-level boxes one by one...")
    for b_id in box_ids:
        url = f"{CLOUD_API_BASE.rstrip('/')}/box/boxes?boxIds={b_id}"
        print(f"Deleting box {b_id} (URL: {url})...", end=" ")
        
        max_retries = 5
        success = False
        
        for attempt in range(max_retries):
            # Base delay between each delete request to avoid triggering rate limits
            time.sleep(0.5)
            try:
                resp = requests.delete(url, headers=get_cloud_headers(), verify=VERIFY_SSL)
                if resp.status_code in [200, 204]:
                    print("SUCCESS")
                    success = True
                    break
                elif resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait_time = int(retry_after)
                    else:
                        wait_time = (attempt + 1) * 3
                    print(f"\n[429 Too Many Requests] Retrying delete in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    print(f"FAILED ({resp.status_code} - {resp.text})")
                    break
            except Exception as e:
                print(f"EXCEPTION: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                break

def main():
    print("--- Jira Cloud BigGantt Box Cleanup Tool ---")
    
    # 1. Fetch boxes
    box_ids = get_all_box_ids()
    
    if not box_ids:
        print("No boxes found to delete (excluding ROOT).")
        return

    print(f"\nFound {len(box_ids)} boxes to delete:")
    for b_id in box_ids:
        print(f" - {b_id}")

    # 2. First Confirmation
    confirm1 = input(f"\nAre you sure you want to delete these {len(box_ids)} boxes? (y/n): ")
    if confirm1.lower() != 'y':
        print("Cleanup cancelled.")
        return

    # 3. Second Confirmation
    print("\nWARNING: This action is permanent and will delete all data within these boxes in Jira Cloud.")
    confirm2 = input("Final confirmation - Type 'DELETE' to proceed: ")
    if confirm2 == 'DELETE':
        delete_boxes(box_ids)
    else:
        print("Cleanup cancelled (Confirmation text mismatch).")

if __name__ == "__main__":
    main()
