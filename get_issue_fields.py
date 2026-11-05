import os
import sys
import json
from getpass import getpass
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Load env variables from env file (without dot)
possible_paths = ['env', '../env']
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        break

def select_environment():
    """Prompt the user to select the Jira target environment."""
    print("\nSelect Target Environment:")
    print("1. Jira DC STG (Data Center Staging)")
    print("2. Jira DC OPS (Data Center Operations)")
    print("3. Jira Cloud Sandbox")
    print("4. Jira Cloud OPS (Production)")
    choice = input("Enter choice (1-4, default 3): ").strip()
    if choice == '1':
        return "DC_STG"
    elif choice == '2':
        return "DC_OPS"
    elif choice == '4':
        return "CLOUD_OPS"
    return "CLOUD_SANDBOX"

def check_env_variables(url, token, email=None, is_dc=False):
    """Verify that all required environment variables are set."""
    missing_vars = []
    if not url:
        missing_vars.append("URL")
    if not token:
        missing_vars.append("API Token / Personal Access Token")
    if not is_dc and not email:
        missing_vars.append("Jira Cloud Admin Email")
    
    if missing_vars:
        print(f"Error: Missing environment variables in env: {', '.join(missing_vars)}")
        print("Please check your env file.")
        sys.exit(1)

def get_jira_fields(session, base_url, auth, is_dc=False):
    """Fetch all fields metadata from Jira to map custom field IDs to their names."""
    api_version = "2" if is_dc else "3"
    url = f"{base_url.rstrip('/')}/rest/api/{api_version}/field"
    try:
        response = session.get(url, auth=auth)
        response.raise_for_status()
        fields_data = response.json()
        
        # Create map of {field_id: field_name}
        field_map = {field["id"]: field["name"] for field in fields_data}
        return field_map
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Jira fields metadata: {e}")
        sys.exit(1)

def get_jira_issue(session, base_url, auth, issue_key, is_dc=False):
    """Fetch issue details by issue key."""
    api_version = "2" if is_dc else "3"
    url = f"{base_url.rstrip('/')}/rest/api/{api_version}/issue/{issue_key}"
    try:
        response = session.get(url, auth=auth)
        if response.status_code == 404:
            print(f"Error: Issue '{issue_key}' not found.")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching issue '{issue_key}': {e}")
        return None

def format_field_value(value):
    """Format custom field values for user-friendly display."""
    if value is None:
        return "None"
    
    # If the value is a dictionary (e.g., Option, User, Status)
    if isinstance(value, dict):
        if "value" in value:  # Single select option
            return value["value"]
        if "name" in value:   # User, Priority, Status, etc.
            return value["name"]
        if "displayName" in value: # User object sometimes uses displayName
            return value["displayName"]
        return json.dumps(value, ensure_ascii=False)
        
    # If the value is a list (e.g., Multi-select, Multi-user)
    if isinstance(value, list):
        formatted_list = []
        for item in value:
            formatted_list.append(format_field_value(item))
        return ", ".join(formatted_list)
        
    return str(value)

def main():
    base_url = os.getenv("JIRA_CLOUD_URL")
    email = os.getenv("JIRA_CLOUD_EMAIL")
    api_token = os.getenv("JIRA_CLOUD_API_TOKEN")
    
    is_dc = False
    
    if not base_url or not api_token:
        env_choice = select_environment()
        if env_choice == "DC_STG":
            base_url = os.getenv("JIRA_DC_STG_URL")
            email = os.getenv("JIRA_DC_STG_EMAIL")
            api_token = os.getenv("JIRA_DC_STG_API_TOKEN")
            is_dc = True
        elif env_choice == "DC_OPS":
            base_url = os.getenv("JIRA_DC_OPS_URL")
            email = os.getenv("JIRA_DC_OPS_EMAIL")
            api_token = os.getenv("JIRA_DC_OPS_API_TOKEN") or os.getenv("JIRA_DC_OPS_TOKEN")
            is_dc = True
        elif env_choice == "CLOUD_OPS":
            base_url = os.getenv("JIRA_CLOUD_OPS_URL")
            email = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL")
            api_token = os.getenv("JIRA_CLOUD_OPS_API_TOKEN")
        else: # CLOUD_SANDBOX
            base_url = os.getenv("JIRA_CLOUD_SANDBOX_URL")
            email = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL")
            api_token = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN")
            
    check_env_variables(base_url, api_token, email, is_dc)
    
    # Load SSL verify option (defaults to False)
    ssl_verify_env = os.getenv("JIRA_SSL_VERIFY", "False")
    ssl_verify = ssl_verify_env.lower() in ("true", "1", "yes")
    
    if not ssl_verify:
        # Suppress InsecureRequestWarning if verification is disabled
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
    # Initialize session
    session = requests.Session()
    session.verify = ssl_verify
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })
    
    # Setup authorization
    if is_dc:
        session.headers.update({"Authorization": f"Bearer {api_token}"})
        auth = None
    else:
        auth = HTTPBasicAuth(email, api_token)
        
    print(f"Connecting to Jira {'DC' if is_dc else 'Cloud'} and fetching metadata...")
    field_map = get_jira_fields(session, base_url, auth, is_dc)
    print("Metadata fetched successfully.\n")
    
    while True:
        issue_key = input("Enter Jira Issue Key (e.g., PROJ-123) or 'q' to quit: ").strip()
        if not issue_key:
            continue
        if issue_key.lower() == 'q':
            print("Exiting program.")
            break
            
        print(f"Fetching details for issue {issue_key}...")
        issue_data = get_jira_issue(session, base_url, auth, issue_key, is_dc)
        
        if not issue_data:
            print("-" * 60)
            continue
            
        fields = issue_data.get("fields", {})
        
        # Filter for custom fields (keys starting with 'customfield_')
        custom_fields = {k: v for k, v in fields.items() if k.startswith("customfield_")}
        
        print("\n" + "=" * 80)
        print(f" CUSTOM FIELDS FOR ISSUE: {issue_key}")
        print(f" Summary: {fields.get('summary', 'No Summary')}")
        print("=" * 80)
        
        if not custom_fields:
            print("No custom fields found for this issue.")
        else:
            # Sort by field name or ID for structured output
            sorted_custom_fields = sorted(
                custom_fields.items(), 
                key=lambda x: field_map.get(x[0], x[0]).lower()
            )
            
            # Print table headers
            print(f"{'Field Name':<35} | {'Field ID':<18} | {'Value'}")
            print("-" * 80)
            
            for field_id, raw_value in sorted_custom_fields:
                field_name = field_map.get(field_id, "Unknown Custom Field")
                value = format_field_value(raw_value)
                
                # Truncate field name if it's too long
                display_name = field_name[:32] + "..." if len(field_name) > 35 else field_name
                
                print(f"{display_name:<35} | {field_id:<18} | {value}")
                
        print("=" * 80 + "\n")

if __name__ == "__main__":
    main()
