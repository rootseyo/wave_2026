#!/usr/bin/env python3
"""
Test script to create a single sample Box in Jira Cloud (STG/Sandbox or OPS)
using the exact same logic as step2_import_cloud_boxes.py.
Includes Box Owner (scopeOwnerKey) configuration, robust retry, timeout handling,
and network diagnostic pre-flight checks.
"""

import os
import sys
import json
import time
import socket
import argparse
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from datetime import datetime, date
import urllib3

# Disable insecure SSL warning logs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load environment file from parent directory
script_dir = os.path.dirname(os.path.abspath(__file__)) if __file__ else '.'
possible_paths = [
    os.path.abspath(os.path.join(script_dir, '..', '..', 'env')),
    os.path.abspath(os.path.join(script_dir, '..', 'env')),
    os.path.abspath(os.path.join(script_dir, 'env')),
]
ENV_FILE = next((p for p in possible_paths if os.path.exists(p)), None)
if ENV_FILE:
    load_dotenv(dotenv_path=ENV_FILE)
else:
    print(f"Error: env file not found in candidates: {possible_paths}")
    sys.exit(1)

BP_BASE_URL = "https://api.bigpicture.one/public/biggantt/rest"

def _bool_env(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")

JIRA_SSL_VERIFY = _bool_env("JIRA_SSL_VERIFY", False)
JIRA_CA_BUNDLE = os.getenv("JIRA_CA_BUNDLE") or None
BP_SSL_VERIFY = _bool_env("BIGPICTURE_SSL_VERIFY", JIRA_SSL_VERIFY)

# Caches to avoid redundant network calls
project_id_cache = {}
user_account_id_cache = {}

def create_http_session(proxies=None, no_proxy=False, max_retries=3):
    """Creates a requests.Session with exponential backoff retry and proxy settings."""
    session = requests.Session()
    
    if no_proxy:
        session.trust_env = False  # Ignore system HTTP_PROXY/HTTPS_PROXY env vars
    elif proxies:
        session.proxies.update(proxies)
        
    retries = Retry(
        total=max_retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def check_tcp_connectivity(host="api.bigpicture.one", port=443, timeout=5):
    """Checks raw TCP socket connectivity to the target host and port."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, "OK"
    except socket.timeout:
        return False, f"TCP Connection timed out after {timeout}s"
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"
    except Exception as e:
        return False, f"Socket error: {e}"

def preflight_check(session, bp_token, timeout=(10, 20), verify=False):
    """Checks BigPicture Cloud API connectivity and token validity before running."""
    print("\n[*] Pre-flight Network & Auth Check:")
    
    # 1. Raw TCP check
    tcp_ok, tcp_msg = check_tcp_connectivity("api.bigpicture.one", 443, timeout=5)
    if not tcp_ok:
        print(f"  ❌ TCP Port 443 check failed (api.bigpicture.one:443): {tcp_msg}")
        print("\n  [Troubleshooting Tips]")
        print("  - 사내망/VPN 환경에서는 외부 HTTPS(443) 통신을 위한 프록시 설정이 필요할 수 있습니다.")
        print("  - '--proxy http://proxy.company.com:port' 옵션을 사용하거나,")
        print("  - 시스템 프록시 충돌인 경우 '--no-proxy' 옵션을 시도해 보세요.")
        return False
    else:
        print("  ✅ TCP Connection to api.bigpicture.one:443: OK")

    # 2. BigPicture REST API Auth check
    url = f"{BP_BASE_URL}/public/ppm/box/box-hierarchy"
    headers = {
        "Authorization": f"APIToken {bp_token}",
        "Accept": "application/json"
    }
    try:
        resp = session.get(url, headers=headers, timeout=timeout, verify=verify)
        if resp.status_code == 200:
            print("  ✅ BigPicture API Authentication & Token: VALID (HTTP 200)")
            return True
        elif resp.status_code == 401:
            print(f"  ❌ BigPicture API Token UNAUTHORIZED (HTTP 401). Token is invalid or expired.")
            return False
        elif resp.status_code == 403:
            print(f"  ❌ BigPicture API Forbidden (HTTP 403). Check user permissions.")
            return False
        else:
            print(f"  ⚠️ BigPicture API returned HTTP {resp.status_code}: {resp.text[:200]}")
            return True
    except requests.exceptions.ConnectTimeout:
        print(f"  ❌ Connection Timeout while connecting to {url}")
        return False
    except requests.exceptions.ReadTimeout:
        print(f"  ❌ Read Timeout while waiting for BigPicture API response.")
        return False
    except requests.exceptions.ProxyError as e:
        print(f"  ❌ Proxy Error: {e}")
        return False
    except requests.exceptions.SSLError as e:
        print(f"  ❌ SSL Verification Error: {e} (Try JIRA_SSL_VERIFY=False)")
        return False
    except Exception as e:
        print(f"  ❌ Pre-flight API check failed: {e}")
        return False

def get_bp_headers(bp_token):
    return {
        "Authorization": f"APIToken {bp_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def get_box_type_id(box_type):
    box_type_str = str(box_type).strip().lower()
    if box_type_str in ("program", "2"):
        return "2"
    elif box_type_str in ("agile", "8"):
        return "8"
    return str(box_type)

def get_jira_project_id(session, project_key, cloud_url, email, token, timeout=(10, 30)):
    """Retrieves numeric Project ID for Jira Cloud using project key."""
    if not project_key or not cloud_url or not email or not token:
        return None
    project_key = project_key.upper().strip()
    if project_key in project_id_cache:
        return project_id_cache[project_key]
        
    url = f"{cloud_url.rstrip('/')}/rest/api/3/project/{project_key}"
    try:
        response = session.get(
            url,
            auth=HTTPBasicAuth(email, token),
            headers={"Accept": "application/json"},
            verify=JIRA_CA_BUNDLE or JIRA_SSL_VERIFY,
            timeout=timeout
        )
        if response.status_code == 200:
            pid = response.json().get("id")
            if pid:
                project_id_cache[project_key] = pid
                return pid
        print(f"  [Warning] Failed to fetch Jira project ID for '{project_key}'. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"  [Warning] Exception fetching Jira project ID for '{project_key}': {e}")
    return None

def get_jira_cloud_account_id(session, user_query, cloud_url, email, token, timeout=(10, 30)):
    """Searches Jira Cloud for the account ID matching email or username."""
    if not user_query:
        return None
    user_query = user_query.strip()
    if user_query in user_account_id_cache:
        return user_account_id_cache[user_query]
    
    # If already formatted as an accountId (e.g. 557058:xxx or 60xxx, and no @), return directly
    if "@" not in user_query and (":" in user_query or len(user_query) >= 24):
        return user_query
        
    search_url = f"{cloud_url.rstrip('/')}/rest/api/3/user/search?query={user_query}"
    try:
        response = session.get(
            search_url,
            auth=HTTPBasicAuth(email, token),
            headers={"Accept": "application/json"},
            verify=JIRA_CA_BUNDLE or JIRA_SSL_VERIFY,
            timeout=timeout
        )
        if response.status_code == 200:
            users = response.json()
            if users:
                account_id = users[0].get("accountId")
                if account_id:
                    user_account_id_cache[user_query] = account_id
                    return account_id
        print(f"  [Warning] User account ID not resolved for query '{user_query}'. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"  [Warning] Exception searching user account ID for '{user_query}': {e}")
    return None

def get_current_scope_definition(session, box_id, bp_token, timeout=(15, 60)):
    """Read the server-supported owner/task types before constructing a PUT."""
    url = f"{BP_BASE_URL}/public/ppm/box/area/task/scope/def/own/{box_id}"
    try:
        response = session.get(url, headers=get_bp_headers(bp_token), verify=BP_SSL_VERIFY, timeout=timeout)
        if response.status_code == 200:
            return response.json()
        print(f"  [Warning] Could not read current scope definition (HTTP {response.status_code}): {response.text[:500]}")
    except Exception as e:
        print(f"  [Warning] Exception reading current scope definition: {e}")
    return None

def _scope_platform_metadata(scope_data, fallback_owner=None):
    """Extracts platform ID, owner, task types, and narrowing query from BigPicture scope definition."""
    platforms = (scope_data or {}).get("extPlatformScopeDefinitions") or []
    platform = next((p for p in platforms if p.get("extPlatformId") == 2), None)
    if platform is None and platforms:
        platform = platforms[0]
    if not platform:
        return 2, fallback_owner, [2, 3], None, None, []
    owner = fallback_owner or ((platform.get("scopeOwner") or {}).get("key"))
    enabled = [t.get("key") for t in platform.get("taskTypes", [])
               if t.get("isEnabled") and t.get("key") is not None]
    if not enabled:
        enabled = [2, 3]
    existing_elements = platform.get("scopeDefinitionElements") or []
    return platform.get("extPlatformId", 2), owner, enabled, platform.get("narrowingQuery"), platform.get("manuallyAddedTasks"), existing_elements

def build_scope_payload(project_ids=None, scope_data=None, scope_owner_key=None):
    """Build and validate the request required by updateOwnScopeDefinition."""
    ext_platform_id, owner, enabled, narrowing_query, manually_added, existing_elements = _scope_platform_metadata(scope_data, fallback_owner=scope_owner_key)
    owner = scope_owner_key or owner
    if not owner:
        raise ValueError("scopeOwnerKey is missing")
    if not enabled:
        raise ValueError("enabledTaskTypeKeys is empty")
        
    if project_ids:
        elements = [{"type": "JIRA_PROJECT", "values": [str(v) for v in project_ids]}]
    elif existing_elements:
        elements = existing_elements
    else:
        raise ValueError("At least one numeric Jira project ID (or existing scope element) is required by BigPicture API")

    platform_payload = {
        "extPlatformId": ext_platform_id,
        "scopeOwnerKey": owner,
        "enabledTaskTypeKeys": enabled,
        "scopeDefinitionElements": elements
    }
    if narrowing_query is not None:
        platform_payload["narrowingQuery"] = narrowing_query
    if manually_added is not None:
        platform_payload["manuallyAddedTasks"] = [
            t.get("id") if isinstance(t, dict) else t for t in manually_added
        ]
    return {"extPlatformScopeDefinitions": [platform_payload]}

def create_box_api(session, box_name, box_type, start_date, end_date, bp_token, parent_box_id="ROOT", timeout=(15, 60), dry_run=False):
    """Step 1: Create Box via BigPicture REST API"""
    box_type_id = get_box_type_id(box_type)
    payload = {
        "parentBoxId": parent_box_id or "ROOT",
        "boxTypeId": box_type_id,
        "boxName": box_name,
        "boxStartDate": start_date,
        "boxEndDate": end_date,
        "boxIconId": "font-awesome-box-open@VIOLET"
    }
    url = f"{BP_BASE_URL}/public/ppm/box"
    
    print("\n--- [Step 1: Create Box API] ---")
    print(f"Endpoint: POST {url}")
    print(f"Payload : {json.dumps(payload, indent=2, ensure_ascii=False)}")
    print(f"Timeout : Connect={timeout[0]}s, Read={timeout[1]}s")
    
    if dry_run:
        print("  -> [DRY RUN] Skipped actual API POST.")
        return "DRY_RUN_BOX_ID_12345"
        
    max_attempts = 2  # 1 initial try + 1 retry
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            print(f"\n  [RETRY 1/1] Re-attempting Box creation for '{box_name}' after failure...")
        try:
            t0 = time.time()
            response = session.post(
                url,
                headers=get_bp_headers(bp_token),
                json=payload,
                verify=BP_SSL_VERIFY,
                timeout=timeout
            )
            elapsed = time.time() - t0
            print(f"  -> Response Time  : {elapsed:.2f}s")
            print(f"  -> Response Status: {response.status_code}")
            
            if response.status_code in (200, 201):
                res_data = response.json()
                print(f"  -> Response Body  : {json.dumps(res_data, ensure_ascii=False)}")
                
                created_id = res_data.get("boxId") or res_data.get("id") or res_data.get("createdBoxId")
                if not created_id and isinstance(res_data.get("cargo"), dict):
                    cargo = res_data["cargo"]
                    created_id = cargo.get("createdBoxId") or cargo.get("boxId") or cargo.get("id")
                    
                if created_id:
                    return created_id
                else:
                    print(f"  [Warning] HTTP {response.status_code} received but Box ID could not be parsed.")
            else:
                print(f"  [Error] Failed to create box (Attempt {attempt}/{max_attempts}). HTTP {response.status_code}: {response.text}")
        except requests.exceptions.ConnectTimeout as e:
            print(f"\n  [ERROR - Connect Timeout] Failed to connect to api.bigpicture.one:443 within {timeout[0]}s: {e}")
        except requests.exceptions.ReadTimeout as e:
            print(f"\n  [ERROR - Read Timeout] BigPicture Cloud server did not respond within {timeout[1]}s: {e}")
        except requests.exceptions.ProxyError as e:
            print(f"\n  [ERROR - Proxy Error] Proxy connection failed: {e}")
        except Exception as e:
            print(f"\n  [Exception] Failed during Box creation (Attempt {attempt}/{max_attempts}): {type(e).__name__}: {e}")

        if attempt < max_attempts:
            print("  Waiting 2 seconds before retry...")
            time.sleep(2)

    print(f"\n  [FAILED] All {max_attempts} attempts to create box '{box_name}' failed.")
    return None

def update_scope_definition_api(session, box_id, project_key, cloud_url, email, token, bp_token, scope_owner_key, timeout=(15, 60), dry_run=False):
    """Step 2: Update Scope Definition and Box Owner via BigPicture API (same logic as step2_import_cloud_boxes.py)"""
    print("\n--- [Step 2: Scope Definition & Box Owner Update] ---")
    
    # 1. Resolve Scope Owner Account ID (if email or username was passed)
    resolved_owner = None
    if scope_owner_key:
        if "@" in scope_owner_key or not (":" in scope_owner_key or len(scope_owner_key) >= 24):
            print(f"  Resolving Atlassian Account ID for owner query '{scope_owner_key}'...")
            resolved_owner = get_jira_cloud_account_id(session, scope_owner_key, cloud_url, email, token, timeout=timeout)
            if resolved_owner:
                print(f"  -> Resolved Account ID: {resolved_owner}")
            else:
                print(f"  [Warning] Could not resolve Account ID for '{scope_owner_key}', using raw value.")
                resolved_owner = scope_owner_key
        else:
            resolved_owner = scope_owner_key
            
    if not resolved_owner:
        print("  [Warning] No valid scopeOwnerKey / Owner account ID found. Skipping Scope & Owner update.")
        return False

    # 2. Resolve Jira Project ID (if project_key provided)
    project_ids = []
    if project_key:
        keys = [k.strip() for k in project_key.split(",") if k.strip()]
        for key in keys:
            if dry_run:
                project_ids.append("10001")
            else:
                pid = get_jira_project_id(session, key, cloud_url, email, token, timeout=timeout)
                if pid:
                    project_ids.append(pid)
                else:
                    print(f"  [Warning] Could not resolve numeric ID for Jira project '{key}'")
    
    # 3. Read current scope definition from BigPicture
    scope_data = get_current_scope_definition(session, box_id, bp_token, timeout=timeout) if not dry_run else None

    # 4. Build Scope Payload
    try:
        payload = build_scope_payload(project_ids=project_ids if project_ids else None, scope_data=scope_data, scope_owner_key=resolved_owner)
    except ValueError as e:
        print(f"  [Warning] Cannot build Scope Definition payload: {e}")
        print("  (Tip: BigPicture API requires at least one Jira Project to configure Box Scope Owner. Use --project <KEY>)")
        return False

    platform_payload = payload["extPlatformScopeDefinitions"][0]
    print(f"  Using Scope Metadata: extPlatformId={platform_payload['extPlatformId']}, scopeOwnerKey='{platform_payload['scopeOwnerKey']}', taskTypes={platform_payload['enabledTaskTypeKeys']}")

    url = f"{BP_BASE_URL}/public/ppm/box/area/task/scope/def/own/{box_id}"
    print(f"Endpoint: PUT {url}")
    print(f"Payload : {json.dumps(payload, indent=2, ensure_ascii=False)}")

    if dry_run:
        print("  -> [DRY RUN] Skipped actual API PUT.")
        return True

    try:
        t0 = time.time()
        response = session.put(
            url,
            headers=get_bp_headers(bp_token),
            json=payload,
            verify=BP_SSL_VERIFY,
            timeout=timeout
        )
        elapsed = time.time() - t0
        print(f"  -> Response Time  : {elapsed:.2f}s")
        print(f"  -> Response Status: {response.status_code}")

        if response.status_code in (200, 204):
            print(f"  -> Successfully updated Scope Definition & Box Owner! (Owner: {resolved_owner})")
            return True
        else:
            detail = response.text[:1000]
            try:
                err_json = response.json()
                messages = err_json.get("errorMessages") or []
                if messages:
                    detail = "; ".join(messages)
            except Exception:
                pass
            print(f"  [Error] Scope & Owner update failed. HTTP {response.status_code}: {detail}")
            return False
    except Exception as e:
        print(f"  [Exception] Failed during Scope & Owner update: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Create a single test sample box in Jira Cloud BigPicture/BigGantt with owner configuration.")
    parser.add_argument("--env", choices=["sandbox", "stg", "ops"], help="Target Cloud environment (sandbox/stg or ops)")
    parser.add_argument("--name", help="Box name (default: auto-generated with timestamp)")
    parser.add_argument("--type", choices=["program", "agile", "2", "8"], help="Box type: program(2) or agile(8)")
    parser.add_argument("--start-date", help="Box start date (YYYY-MM-DD, default: today)")
    parser.add_argument("--end-date", help="Box end date (YYYY-MM-DD, default: end of year)")
    parser.add_argument("--project", help="Jira Project Key for scope mapping (e.g. 'PROJ')")
    parser.add_argument("--owner", help="Box Scope Owner (Atlassian Account ID or user email, default: from env SCOPE_OWNER_KEY)")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP read timeout in seconds (default: 60)")
    parser.add_argument("--connect-timeout", type=int, default=15, help="HTTP connect timeout in seconds (default: 15)")
    parser.add_argument("--proxy", help="Custom proxy URL (e.g. http://proxy.company.com:8080)")
    parser.add_argument("--no-proxy", action="store_true", help="Disable system environment proxy and connect directly")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip pre-flight connectivity check")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Simulate API calls without creating box")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    
    args = parser.parse_args()

    # Determine environment
    env_choice = args.env
    if not env_choice:
        sandbox_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or "Not configured"
        ops_url = os.getenv("JIRA_CLOUD_OPS_URL") or "Not configured"
        print("=" * 60)
        print(" [BigGantt/BigPicture Cloud] Single Sample Box Test Tool")
        print("=" * 60)
        print("Select Target Cloud Environment:")
        print(f"  1. Sandbox / STG  ({sandbox_url})")
        print(f"  2. Production / OPS ({ops_url})")
        raw_env = input("Enter choice (1/2, default 1): ").strip()
        env_choice = "ops" if raw_env == "2" else "sandbox"

    if env_choice.lower() in ("ops", "prod", "production"):
        env_name = "OPS"
        jira_cloud_url = os.getenv("JIRA_CLOUD_OPS_URL")
        jira_cloud_email = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL")
        jira_cloud_token = os.getenv("JIRA_CLOUD_OPS_API_TOKEN")
        bp_api_token = os.getenv("JIRA_BIGGANTT_OPS_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
        biggantt_base_url = os.getenv("JIRA_CLOUD_OPS_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
        env_label = "Production / Operations (OPS)"
    else:
        env_name = "SANDBOX"
        jira_cloud_url = os.getenv("JIRA_CLOUD_SANDBOX_URL")
        jira_cloud_email = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL")
        jira_cloud_token = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN")
        bp_api_token = os.getenv("JIRA_BIGGANTT_SANDBOX_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
        biggantt_base_url = os.getenv("JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
        env_label = "Sandbox / STG (SANDBOX)"

    default_scope_owner_key = os.getenv("SCOPE_OWNER_KEY") or os.getenv("BG_SCOPE_OWNER_KEY") or os.getenv("FALLBACK_OWNER_ACCOUNT_ID")

    if not bp_api_token:
        print(f"\n[Error] Missing JIRA_BIGGANTT_{env_name}_API_TOKEN in {ENV_FILE}")
        sys.exit(1)

    print(f"\nTarget Environment : {env_label}")
    print(f"Jira Cloud URL     : {jira_cloud_url}")
    print(f"Default Owner Key  : {default_scope_owner_key or '(None)'}")
    print(f"SSL Verification   : Jira={JIRA_SSL_VERIFY}, BigPicture={BP_SSL_VERIFY}")

    # Build HTTP Session
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    session = create_http_session(proxies=proxies, no_proxy=args.no_proxy)
    timeout_tuple = (args.connect_timeout, args.timeout)

    # Pre-flight check
    if not args.skip_preflight and not args.dry_run:
        ok = preflight_check(session, bp_api_token, timeout=(args.connect_timeout, 20), verify=BP_SSL_VERIFY)
        if not ok:
            print("\n[Pre-flight Failed] Network connectivity or Authentication test failed.")
            cont = input("Do you still want to proceed? (y/N): ").strip().lower()
            if cont not in ('y', 'yes'):
                sys.exit(1)

    # 1. Box Name
    default_name = f"[TEST] Sample Box - {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if args.name:
        box_name = args.name.strip()
    elif args.yes:
        box_name = default_name
    else:
        input_name = input(f"\n1. Box Name (default: '{default_name}'): ").strip()
        box_name = input_name if input_name else default_name

    # 2. Box Type
    if args.type:
        box_type = get_box_type_id(args.type)
    elif args.yes:
        box_type = "2"
    else:
        print("\n2. Box Type:")
        print("  1. Program (ID: 2) [Default]")
        print("  2. Agile (ID: 8)")
        type_choice = input("Enter choice (1/2, default 1): ").strip()
        box_type = "8" if type_choice == '2' else "2"
    box_type_label = "Agile (8)" if box_type == "8" else "Program (2)"

    # 3. Dates
    today_str = date.today().isoformat()
    end_of_year_str = f"{date.today().year}-12-31"

    if args.start_date:
        start_date = args.start_date.strip()
    elif args.yes:
        start_date = today_str
    else:
        input_start = input(f"\n3. Start Date (YYYY-MM-DD, default: {today_str}): ").strip()
        start_date = input_start if input_start else today_str

    if args.end_date:
        end_date = args.end_date.strip()
    elif args.yes:
        end_date = end_of_year_str
    else:
        input_end = input(f"4. End Date   (YYYY-MM-DD, default: {end_of_year_str}): ").strip()
        end_date = input_end if input_end else end_of_year_str

    # 5. Jira Project Key
    if args.project is not None:
        project_key = args.project.upper().strip() if args.project else None
    elif args.yes:
        project_key = None
    else:
        input_proj = input("\n5. Jira Project Key for Scope (e.g. 'PROJ', press Enter to skip): ").strip()
        project_key = input_proj.upper() if input_proj else None

    # 6. Box Scope Owner
    if args.owner:
        scope_owner_input = args.owner.strip()
    elif args.yes:
        scope_owner_input = default_scope_owner_key
    else:
        owner_prompt = f"\n6. Box Scope Owner (Account ID or Email, default: '{default_scope_owner_key or ''}'): "
        input_owner = input(owner_prompt).strip()
        scope_owner_input = input_owner if input_owner else default_scope_owner_key

    dry_run = args.dry_run

    # Summary confirmation
    print("\n" + "=" * 60)
    print(" [Box Creation Parameters Summary]")
    print("=" * 60)
    print(f"Target Env  : {env_label}")
    print(f"Box Name    : {box_name}")
    print(f"Box Type    : {box_type_label}")
    print(f"Start Date  : {start_date}")
    print(f"End Date    : {end_date}")
    print(f"Project Key : {project_key or '(None - No project scope)'}")
    print(f"Box Owner   : {scope_owner_input or '(None)'}")
    print(f"Timeout     : Connect={args.connect_timeout}s, Read={args.timeout}s")
    print(f"Proxy Mode  : {'Disabled (Direct)' if args.no_proxy else (args.proxy or 'System Default')}")
    print(f"Mode        : {'DRY-RUN (Simulated)' if dry_run else 'LIVE (Actual Creation)'}")
    print("=" * 60)

    if not args.yes:
        proceed = input("Proceed with creation? (Y/n): ").strip().lower()
        if proceed in ('n', 'no'):
            print("Cancelled by user.")
            return

    # Execute Step 1: Create Box
    new_box_id = create_box_api(
        session=session,
        box_name=box_name,
        box_type=box_type,
        start_date=start_date,
        end_date=end_date,
        bp_token=bp_api_token,
        parent_box_id="ROOT",
        timeout=timeout_tuple,
        dry_run=dry_run
    )
    
    if not new_box_id:
        print("\n[FAILED] Box creation failed.")
        return

    print(f"\n[SUCCESS] Box created! New Box ID: {new_box_id}")

    # Execute Step 2: Update Scope & Owner
    scope_success = update_scope_definition_api(
        session=session,
        box_id=new_box_id,
        project_key=project_key,
        cloud_url=jira_cloud_url,
        email=jira_cloud_email,
        token=jira_cloud_token,
        bp_token=bp_api_token,
        scope_owner_key=scope_owner_input,
        timeout=timeout_tuple,
        dry_run=dry_run
    )

    # Output Box URLs
    cloud_url = (jira_cloud_url or "").rstrip("/")
    if biggantt_base_url:
        box_href = f"{biggantt_base_url.rstrip('/')}/{new_box_id}"
    else:
        box_href = f"{cloud_url}/jira/apps/eu.softwareplant.biggantt/#/box/{new_box_id}"

    print("\n" + "=" * 60)
    print(" [Result & Access Link]")
    print("=" * 60)
    print(f"Created Box ID : {new_box_id}")
    print(f"Scope & Owner  : {'Configured' if scope_success else 'Not configured / Failed'}")
    print(f"Box Direct URL : {box_href}")
    print("=" * 60)
    print("Note: To delete this test box later, use delete_cloud_boxes.py or delete directly from BigGantt UI.")

if __name__ == "__main__":
    main()
