import os
import sys

# Embedded version for copies deployed to offline environments.
# Follow Semantic Versioning and update this value whenever this file changes.
# 1.1.0: Load only ../env and display the selected environment configuration.
# 1.2.0: Continue processing when a Jira user lookup returns 404.
# 1.3.0: Continue after per-resource errors and report aggregated issues at the end.
# 1.3.1: Fail fast with an actionable message on DNS/connection failures.
# 1.3.3: Read projects.txt from the shared parent directory (../projects.txt).
# 1.3.4: Persist the selected source environment in migration entries.
# 1.4.0: Preflight REST API & PAT/JWT token validation before execution.
# 1.4.1: Smart auto-detection of BigPicture/BigGantt REST API prefix on 404.
# 1.4.2: Refactor API endpoints to align with official BigPicture/BigGantt DC spec (/rest/bigpicture/1.0).
# 1.4.3: Add detailed response body logging for JSONDecodeError when API returns non-JSON/HTML.
# 1.5.0: Save all_boxes_cache.json to support fast offline re-filtering when projects.txt changes.
# 1.6.0: Validate Box project scopes against Jira DC (filter out 404/deleted/empty/archived scopes) and export Excel-compatible TSV/CSV.
# 1.7.0: Validate Box JQL (narrowingQuery) via Jira DC REST API, detect non-project scopes (filters/boards), and expand Excel exports with JQL diagnostics.
# 1.7.1: Measure and display total script execution time upon completion.
# 1.8.0: Audit scope references and show target-project validation progress.
# 1.8.1: Record stale project keys and preserve configured Project references in reports.
# 1.8.3: Count and validate Projects configured on Boxes, not projects.txt entries.
# 1.9.0: Include Closed target boxes for scope/JQL auditing; completely exclude empty project scope boxes from report.
# 1.10.0: Add Single Box Test mode (--box-id) and Jira Connected Field detection & verification.
# 1.10.1: Inspect taskTypes (isEnabled) in scope definition to correctly exclude disabled projects/tasks.
# 1.11.0: Extract hierarchy metadata (depth, parentBoxId) and format Box names with visual tree symbols (│  ⎿  ).
__version__ = "1.11.0"


def get_version_label():
    return f"{os.path.basename(__file__)} v{__version__}"


def versioned_prompt(message):
    return input(f"[{get_version_label()}] {message}")


if __name__ == "__main__" and any(arg in ("-V", "--version") for arg in sys.argv[1:]):
    print(get_version_label())
    sys.exit(0)

import requests
import json
import urllib3
import time
import csv
from dotenv import dotenv_values
from datetime import datetime
from urllib.parse import unquote, urlsplit, quote

# Load only ../env, resolved relative to this script rather than the launch directory.
if __name__ == "__main__":
    print(f"\n=== {get_version_label()} ===")

script_dir = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.abspath(os.path.join(script_dir, "..", "env"))
PROJECT_ROOT = script_dir
# projects.txt is kept beside the project directory (next to ../env), while
# generated migration artifacts stay in the project directory itself.
PROJECTS_FILE = os.path.abspath(os.path.join(PROJECT_ROOT, "..", "projects.txt"))
HIERARCHY_FILE = os.path.join(PROJECT_ROOT, "hierarchy.json")
BOXES_FILE = os.path.join(PROJECT_ROOT, "boxes.json")
MIGRATION_BOX_FILE = os.path.join(PROJECT_ROOT, "migration_box.json")
ALL_BOXES_CACHE_FILE = os.path.join(PROJECT_ROOT, "all_boxes_cache.json")
TSV_EXPORT_FILE = os.path.join(PROJECT_ROOT, "exported_boxes.tsv")
CSV_EXPORT_FILE = os.path.join(PROJECT_ROOT, "exported_boxes.csv")
SCOPE_AUDIT_FILE = os.path.join(PROJECT_ROOT, "box_scope_audit.csv")
print(f"Environment file (script-relative ../env): {ENV_FILE}")

if not os.path.isfile(ENV_FILE):
    print(f"Error: Environment file not found: {ENV_FILE}")
    sys.exit(1)

ENV_VALUES = dotenv_values(ENV_FILE)

def select_environment():
    for idx, arg in enumerate(sys.argv):
        if arg in ("--env", "-e") and idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].upper().strip()
    if __name__ != "__main__":
        return os.environ.get("SOURCE_ENV", "STG")
    print("\nSelect Source Environment:")
    print("1. STG (Staging)")
    print("2. OPS (Operations)")
    choice = versioned_prompt("Enter choice (1/2, default 1): ").strip()
    if choice == '2':
        return "OPS"
    return "STG"

def select_dry_run():
    if "--dry-run" in sys.argv:
        return True
    if __name__ != "__main__":
        return False
    choice = versioned_prompt("Dry-run mode? (y/n, default n): ").lower().strip()
    return choice == 'y'

def select_target_box_id():
    for idx, arg in enumerate(sys.argv):
        if arg in ("--box-id", "--box", "-b") and idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].strip()
        if arg.startswith("--box-id="):
            return arg.split("=", 1)[1].strip()
    if os.environ.get("TARGET_BOX_ID"):
        return os.environ.get("TARGET_BOX_ID").strip()
    if __name__ != "__main__":
        return None
    
    print("\nSelect Execution Mode:")
    print("1. Full Hierarchy Scan (전체 Box 스캔 및 리포트)")
    print("2. Single Box Test (특정 Box ID 1개 상세 진단/테스트)")
    choice = versioned_prompt("Enter choice (1/2, default 1): ").strip()
    if choice == "2":
        box_id = versioned_prompt("Enter Box ID to test: ").strip()
        return box_id if box_id else None
    return None

ENV = select_environment()
DRY_RUN = select_dry_run()
TARGET_BOX_ID = select_target_box_id()

if ENV == "OPS":
    URL_ENV_NAME = "JIRA_DC_OPS_URL"
    JIRA_TOKEN_ENV_NAME = "JIRA_DC_OPS_API_TOKEN"
    BIGGANTT_TOKEN_ENV_NAME = "JIRA_DC_OPS_BIGGANTT_API_TOKEN"
else:
    URL_ENV_NAME = "JIRA_DC_STG_URL"
    JIRA_TOKEN_ENV_NAME = "JIRA_DC_STG_API_TOKEN"
    BIGGANTT_TOKEN_ENV_NAME = "JIRA_DC_STG_BIGGANTT_API_TOKEN"

BASE_URL = ENV_VALUES.get(URL_ENV_NAME)
JIRA_DC_PAT = ENV_VALUES.get(JIRA_TOKEN_ENV_NAME)
BG_DC_JWT_TOKEN = ENV_VALUES.get(BIGGANTT_TOKEN_ENV_NAME)

if __name__ == "__main__":
    print("\nEnvironment configuration:")
    print(f"  Source environment: {ENV}")
    print(f"  Environment file: {ENV_FILE}")
    print(f"  {URL_ENV_NAME}: {BASE_URL or '[MISSING]'}")
    print(f"  {JIRA_TOKEN_ENV_NAME}: {'configured' if JIRA_DC_PAT else '[MISSING]'}")
    print(f"  {BIGGANTT_TOKEN_ENV_NAME}: {'configured' if BG_DC_JWT_TOKEN else '[MISSING]'}")

    if not BASE_URL or not JIRA_DC_PAT or not BG_DC_JWT_TOKEN:
        print(f"Error: Missing environment variables for {ENV}.")
        exit(1)

VERIFY_SSL = False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Official BigPicture/BigGantt DC Spec base URL auto-detection:
# Base URL: {Jira Base URL}/rest/bigpicture/1.0 (Fallback: /rest/biggantt/1.0)
def detect_bg_base_url(base_url, token, verify_ssl):
    root_url = base_url.split('/rest/')[0].rstrip('/')
    
    # Official spec standard: /rest/bigpicture/1.0
    candidate_prefixes = ["/rest/bigpicture/1.0", "/rest/biggantt/1.0"]
    
    # Prioritize if base_url explicitly specified a prefix in ../env
    for prefix in list(candidate_prefixes):
        if prefix in base_url:
            candidate_prefixes.remove(prefix)
            candidate_prefixes.insert(0, prefix)
            break
            
    for prefix in candidate_prefixes:
        # Official endpoint: /public/ppm/box/box-hierarchy
        test_url = f"{root_url}{prefix}/public/ppm/box/box-hierarchy"
        try:
            resp = requests.get(
                test_url, 
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {token}"
                }, 
                verify=verify_ssl, 
                timeout=5
            )
            # Ensure it is 200 AND valid JSON
            if resp.status_code == 200:
                try:
                    resp.json()
                    return f"{root_url}{prefix}"
                except Exception:
                    pass
            elif resp.status_code != 404:
                return f"{root_url}{prefix}"
        except Exception:
            pass
            
    # Default standard fallback per official spec
    return f"{root_url}/rest/bigpicture/1.0"

BG_BASE_URL = detect_bg_base_url(BASE_URL, BG_DC_JWT_TOKEN, VERIFY_SSL) if BASE_URL and BG_DC_JWT_TOKEN else ""
if __name__ == "__main__":
    print(f"Detected BigGantt/BigPicture DC API Base URL: {BG_BASE_URL}")

# Jira Core API Base
JIRA_BASE_URL = BASE_URL.split('/rest/')[0] if BASE_URL else ""
JIRA_CORE_URL = f"{JIRA_BASE_URL}/rest/api/2" if JIRA_BASE_URL else ""

def validate_preflight(jira_core_url, jira_pat, jira_token_name, bg_base_url, bg_token, bg_token_name, verify_ssl):
    print("\n" + "="*50)
    print("RUNNING REST API & TOKEN PREFLIGHT VALIDATION")
    print("="*50)
    
    all_passed = True
    
    # 1. Test Jira Core REST API & PAT Token (/myself)
    jira_test_url = f"{jira_core_url}/myself"
    print(f"[*] Testing Jira DC PAT Token ({jira_token_name})...")
    print(f"    Target Endpoint: {jira_test_url}")
    try:
        resp = requests.get(
            jira_test_url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {jira_pat}"
            },
            verify=verify_ssl,
            timeout=10
        )
        if resp.status_code == 200:
            try:
                user_info = resp.json()
                display_name = user_info.get("displayName", "Unknown")
                name_key = user_info.get("name") or user_info.get("key") or "Unknown"
                email = user_info.get("emailAddress", "N/A")
                print(f"    [SUCCESS] Jira DC PAT Token is VALID!")
                print(f"              Authenticated User : {display_name} ({name_key})")
                print(f"              Email              : {email}")
            except json.JSONDecodeError:
                all_passed = False
                print(f"    [FAIL] Response from Jira API is NOT valid JSON (HTML/Redirect response).")
                print(f"           Content-Type: {resp.headers.get('Content-Type')}")
                print(f"           Response Body Snippet:\n{resp.text[:300]}")
        elif resp.status_code == 401:
            all_passed = False
            print(f"    [FAIL] Jira DC PAT Authorization Failed! HTTP 401 Unauthorized.")
            print(f"           --> The token in {jira_token_name} ({ENV_FILE}) is invalid, expired, or rejected by Jira.")
            print(f"           --> Please issue a new Personal Access Token in Jira (Profile > Personal Access Tokens) and update ../env.")
        elif resp.status_code == 403:
            all_passed = False
            print(f"    [FAIL] Jira DC PAT Permission Forbidden! HTTP 403 Forbidden.")
            print(f"           --> Token authenticated but lacks permissions to access Jira API.")
        else:
            all_passed = False
            print(f"    [FAIL] Unexpected response from Jira DC API: HTTP {resp.status_code}")
            print(f"           Response body: {resp.text[:200]}")
    except requests.exceptions.ConnectionError as e:
        all_passed = False
        jira_host = jira_core_url.split('/rest/')[0]
        print(f"    [FAIL] Connection Error: Could not reach Jira host ({jira_host}). {e}")
    except Exception as e:
        all_passed = False
        print(f"    [FAIL] Error validating Jira PAT: {e}")

    # 2. Test BigGantt / BigPicture API Token according to official spec (/public/ppm/box/box-hierarchy)
    bg_test_url = f"{bg_base_url.rstrip('/')}/public/ppm/box/box-hierarchy"
    print(f"\n[*] Testing BigGantt/BigPicture Token ({bg_token_name})...")
    print(f"    Target Endpoint: {bg_test_url}")
    try:
        resp = requests.get(
            bg_test_url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {bg_token}"
            },
            verify=verify_ssl,
            timeout=10
        )
        if resp.status_code == 200:
            try:
                resp.json()
                print(f"    [SUCCESS] BigGantt/BigPicture Token is VALID!")
            except json.JSONDecodeError:
                all_passed = False
                print(f"    [FAIL] Response from BigGantt API is NOT valid JSON (HTML/Redirect response).")
                print(f"           Content-Type: {resp.headers.get('Content-Type')}")
                print(f"           Response Body Snippet:\n{resp.text[:300]}")
        elif resp.status_code in (401, 403):
            all_passed = False
            print(f"    [FAIL] BigGantt Token Authorization Failed! HTTP {resp.status_code}.")
            print(f"           --> Check {bg_token_name} in ../env.")
        elif resp.status_code == 404:
            all_passed = False
            print(f"    [FAIL] BigGantt REST API Endpoint Not Found! HTTP 404.")
            print(f"           --> BigGantt/BigPicture app might be disabled/unlicensed on this Jira DC instance,")
            print(f"               or the REST API prefix ({bg_base_url}) is incorrect.")
        else:
            all_passed = False
            print(f"    [FAIL] Unexpected response from BigGantt API: HTTP {resp.status_code}")
    except Exception as e:
        all_passed = False
        print(f"    [FAIL] Error validating BigGantt Token: {e}")

    print("="*50)
    
    if not all_passed:
        print("\nCRITICAL ERROR: Preflight API & Token Validation FAILED!")
        print("Please resolve the token/network issues above before running the scan script.")
        sys.exit(1)
    else:
        print("Preflight validation PASSED successfully! Proceeding with main execution...\n")

# Run Preflight Validation before starting main processing
if __name__ == "__main__":
    validate_preflight(
        JIRA_CORE_URL, 
        JIRA_DC_PAT, 
        JIRA_TOKEN_ENV_NAME, 
        BG_BASE_URL, 
        BG_DC_JWT_TOKEN, 
        BIGGANTT_TOKEN_ENV_NAME, 
        VERIFY_SSL
    )

# Tracking metrics
stats = {
    "total_boxes_found": 0,
    "archived_boxes_count": 0,
    "unarchived_boxes_count": 0,
    "closed_boxes_count": 0,
    "processed": 0,
    "matched": 0,
    "failed": 0,
    "api_errors": 0,
    "user_not_found": 0,
    "user_lookup_failed": 0,
    "box_processing_errors": 0,
    "invalid_scope_empty": 0,
    "invalid_scope_not_found": 0,
    "invalid_scope_archived": 0,
    "non_project_scope_count": 0,
    "filtered_out_invalid_scope": 0,
    "jql_configured_count": 0,
    "jql_valid_count": 0,
    "jql_warning_count": 0,
    "scope_reference_issue_boxes": 0,
    "jql_empty_result_count": 0,
    "jql_invalid_count": 0,
    "jql_error_count": 0
}

# Aggregated runtime issues. Identical issues are counted instead of repeated.
runtime_issues = {}

# List of boxes excluded due to invalid project scope
excluded_invalid_boxes = []

# Keep project status cached to minimize server calls:
# project_key -> {"exists": bool, "archived": bool, "status": "ACTIVE"|"ARCHIVED"|"NOT_FOUND"|"ERROR"}
project_status_cache = {}

# Keep JQL validation status cached:
# jql_str -> {"valid": bool, "status": "VALID"|"WARNING"|"INVALID"|"NONE"|"ERROR", "message": str, "total_issues": int}
jql_status_cache = {}

# Keep Saved Filter status cached:
# filter_id -> {"exists": bool, "status": "ACTIVE"|"NOT_FOUND"|"ERROR", "jql": str, "name": str}
filter_status_cache = {}
board_status_cache = {}
jira_fields_cache = {}

# File path for execution logging
LOG_FILE = os.path.join(PROJECT_ROOT, "migration_run.log")
REQUEST_TIMEOUT = 10

def log(message):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

def record_issue(category, message):
    key = (category, message)
    if key not in runtime_issues:
        runtime_issues[key] = {
            "category": category,
            "message": message,
            "count": 0
        }
    runtime_issues[key]["count"] += 1

def report_runtime_issues():
    log("\n" + "="*50)
    log("EXECUTION ISSUE REPORT")
    log("="*50)
    log(f"Unique issues: {len(runtime_issues)}")
    log(f"API request errors: {stats['api_errors']}")
    log(f"Users not found (404): {stats['user_not_found']}")
    log(f"User lookup failures: {stats['user_lookup_failed']}")
    log(f"Box processing exceptions: {stats['box_processing_errors']}")
    log(f"JQL Syntax/Validation errors: {stats['jql_invalid_count']}")
    log(f"Non-project scopes (Filter/Board): {stats['non_project_scope_count']}")

    if not runtime_issues:
        log("No runtime issues recorded.")
    else:
        log("-"*50)
        for index, issue in enumerate(runtime_issues.values(), 1):
            count_suffix = f" (x{issue['count']})" if issue["count"] > 1 else ""
            log(f"{index}. [{issue['category']}] {issue['message']}{count_suffix}")
    log("="*50)

def get_headers(token, prefix="Bearer"):
    return {
        "accept": "application/json",
        "Authorization": f"{prefix} {token}",
        "Content-Type": "application/json"
    }

def fetch_json(url, token, prefix="Bearer", max_retries=5, not_found_value=None):
    log(f"  Fetching: {url}")
    for attempt in range(max_retries):
        try:
            # Minimal delay for performance
            time.sleep(0.1) 
            response = requests.get(
                url,
                headers=get_headers(token, prefix),
                verify=VERIFY_SSL,
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    stats["api_errors"] += 1
                    content_type = response.headers.get('Content-Type', 'unknown')
                    snippet = response.text[:400].replace('\n', ' ').replace('\r', ' ').strip()
                    err_msg = (
                        f"JSONDecodeError: Server returned HTTP 200 but body is not JSON. "
                        f"Content-Type: {content_type}. Snippet: '{snippet}'"
                    )
                    record_issue("NON-JSON RESPONSE", f"{url} -> {err_msg}")
                    log(f"    ERROR: {err_msg}")
                    return None
            
            if response.status_code == 429:
                wait_time = (attempt + 1) * 2 
                log(f"    [429] Rate limited. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue

            if response.status_code == 401:
                stats["api_errors"] += 1
                record_issue("HTTP 401", f"Unauthorized: {url}")
                log(f"    [401] Unauthorized. Recording error and continuing: {url}")
                return None

            if response.status_code == 404 and not_found_value is not None:
                log(f"    [404] Resource not found. Continuing: {url}")
                return not_found_value
            
            stats["api_errors"] += 1
            record_issue(f"HTTP {response.status_code}", url)
            log(f"    Error: {response.status_code}")
            return None
        except requests.exceptions.ConnectionError as e:
            stats["api_errors"] += 1
            record_issue("NETWORK CONNECTION", f"{url} -> {e}")
            log(
                "    ERROR: Could not connect to the Jira host. "
                "Check the URL, DNS, VPN, proxy, or network connection. "
                f"Continuing without this resource: {url}"
            )
            return None
        except requests.exceptions.Timeout as e:
            log(f"    Timeout: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            stats["api_errors"] += 1
            record_issue("REQUEST TIMEOUT", f"{url} -> {e}")
            return None
        except Exception as e:
            log(f"    Exception: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            stats["api_errors"] += 1
            record_issue("REQUEST EXCEPTION", f"{url} -> {e}")
            return None

    stats["api_errors"] += 1
    record_issue("HTTP 429", f"Rate limit retries exhausted: {url}")
    return None

def get_user_info(user_key):
    if not user_key or not str(user_key).strip():
        return None, None
    user_key = str(user_key).strip()
    url = f"{JIRA_CORE_URL}/user?key={user_key}"
    not_found_user = {
        "emailAddress": "not found user",
        "displayName": "not found user"
    }
    user_data = fetch_json(
        url,
        token=JIRA_DC_PAT,
        prefix="Bearer",
        not_found_value=not_found_user
    )
    if user_data is not_found_user:
        stats["user_not_found"] += 1
        record_issue("USER NOT FOUND", f"{user_key} ({url})")
        return "not found user", "not found user"
    if user_data:
        email = user_data.get("emailAddress")
        display_name = user_data.get("displayName")
        return email, display_name

    stats["user_lookup_failed"] += 1
    record_issue("USER LOOKUP FAILED", f"{user_key} ({url})")
    return "user lookup failed", "user lookup failed"

def get_project_status(project_key):
    if not project_key:
        return {"exists": False, "archived": False, "status": "INVALID_KEY"}
    project_key = project_key.upper().strip()
    if project_key in project_status_cache:
        return project_status_cache[project_key]
    
    url = f"{JIRA_CORE_URL}/project/{project_key}"
    not_found_sentinel = {"__not_found__": True}
    project_data = fetch_json(
        url,
        token=JIRA_DC_PAT,
        prefix="Bearer",
        not_found_value=not_found_sentinel
    )
    
    if project_data is not_found_sentinel:
        res = {"exists": False, "archived": False, "status": "NOT_FOUND"}
        project_status_cache[project_key] = res
        record_issue("PROJECT NOT FOUND (404)", f"Project '{project_key}' does not exist in Jira DC ({url})")
        return res
    elif project_data and isinstance(project_data, dict):
        is_archived = project_data.get("archived", False)
        status = "ARCHIVED" if is_archived else "ACTIVE"
        res = {"exists": True, "archived": is_archived, "status": status,
               "key": str(project_data.get("key") or project_key).upper()}
        project_status_cache[project_key] = res
        return res
    else:
        res = {"exists": False, "archived": False, "status": "ERROR"}
        project_status_cache[project_key] = res
        return res

def is_project_archived(project_key):
    return get_project_status(project_key).get("archived", False)

def is_project_active(project_key):
    return get_project_status(project_key).get("status") == "ACTIVE"

def validate_jql(jql_query):
    """
    Validates a JQL query string using Jira Core REST API (/rest/api/2/search).
    Returns dict: {"valid": bool, "status": "VALID"|"WARNING"|"INVALID"|"NONE"|"ERROR", "message": str, "total_issues": int}
    """
    if not jql_query or not str(jql_query).strip():
        return {"valid": True, "status": "NONE", "message": None, "total_issues": 0}
    
    jql_str = str(jql_query).strip()
    if jql_str in jql_status_cache:
        return jql_status_cache[jql_str]
    
    stats["jql_configured_count"] += 1
    url = f"{JIRA_CORE_URL}/search"
    log(f"  Validating JQL: {jql_str}")
    
    payload = {
        "jql": jql_str,
        "validateQuery": True,
        "maxResults": 1
    }
    
    for attempt in range(3):
        try:
            time.sleep(0.1)
            response = requests.post(
                url,
                headers=get_headers(JIRA_DC_PAT, prefix="Bearer"),
                json=payload,
                verify=VERIFY_SSL,
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    total = data.get("total", 0)
                    res = {"valid": True, "status": "VALID", "message": None, "total_issues": total}
                    jql_status_cache[jql_str] = res
                    stats["jql_valid_count"] += 1
                    log(f"    [JQL VALID] Matched {total} issues in Jira DC.")
                    return res
                except json.JSONDecodeError:
                    pass
            elif response.status_code == 400:
                combined = []
                try:
                    err_data = response.json()
                    err_msgs = err_data.get("errorMessages", []) if isinstance(err_data, dict) else []
                    err_field = err_data.get("errors", {}) if isinstance(err_data, dict) else {}
                    combined = list(err_msgs)
                    for k, v in err_field.items():
                        combined.append(f"{k}: {v}")
                    err_detail = "; ".join(combined) if combined else response.text[:200]
                except Exception:
                    err_detail = response.text[:200]

                # Strict validation also rejects semantic warnings (for example,
                # inaccessible values). A relaxed search distinguishes those from
                # JQL that Jira cannot parse at all.
                try:
                    relaxed = requests.get(
                        url,
                        headers=get_headers(JIRA_DC_PAT, prefix="Bearer"),
                        params={"jql": jql_str, "validateQuery": "warn", "maxResults": 1},
                        verify=VERIFY_SSL,
                        timeout=REQUEST_TIMEOUT
                    )
                except requests.RequestException as exc:
                    relaxed = None
                    err_detail = f"Strict HTTP 400: {err_detail}; relaxed check failed: {exc}"

                if relaxed is not None and relaxed.status_code == 200:
                    try:
                        relaxed_data = relaxed.json()
                        total = relaxed_data.get("total", 0)
                        warnings = relaxed_data.get("warningMessages") or []
                        message = "; ".join(warnings) if warnings else err_detail
                        res = {"valid": True, "status": "WARNING", "message": message, "total_issues": total}
                        jql_status_cache[jql_str] = res
                        stats["jql_warning_count"] += 1
                        log(f"    [JQL WARNING] Strict validation rejected the query, but relaxed search succeeded: {message}")
                        return res
                    except (ValueError, AttributeError):
                        err_detail = f"Strict HTTP 400: {err_detail}; relaxed response was not valid JSON"

                probe_ok = False
                if relaxed is not None and relaxed.status_code == 400 and combined:
                    try:
                        probe = requests.get(
                            url,
                            headers=get_headers(JIRA_DC_PAT, prefix="Bearer"),
                            params={"jql": "project IS NOT EMPTY", "validateQuery": "warn", "maxResults": 1},
                            verify=VERIFY_SSL,
                            timeout=REQUEST_TIMEOUT
                        )
                        probe_ok = probe.status_code == 200
                    except requests.RequestException:
                        pass

                if probe_ok:
                    stats["jql_invalid_count"] += 1
                    res = {"valid": False, "status": "INVALID", "message": err_detail, "total_issues": 0}
                    log(f"    [JQL INVALID] Both checks returned HTTP 400; control query succeeded: {err_detail}")
                else:
                    stats["jql_error_count"] += 1
                    stats["api_errors"] += 1
                    relaxed_status = relaxed.status_code if relaxed is not None else "unavailable"
                    res = {"valid": False, "status": "ERROR", "message": f"Strict HTTP 400: {err_detail}; relaxed HTTP {relaxed_status}", "total_issues": 0}
                    log(f"    [JQL CHECK FAILED] {res['message']}")
                jql_status_cache[jql_str] = res
                return res
            elif response.status_code == 429:
                time.sleep((attempt + 1) * 2)
                continue
            else:
                stats["jql_error_count"] += 1
                stats["api_errors"] += 1
                err_detail = f"HTTP {response.status_code}"
                res = {"valid": False, "status": "ERROR", "message": err_detail, "total_issues": 0}
                jql_status_cache[jql_str] = res
                log(f"    [JQL CHECK FAILED] Unexpected status {response.status_code}")
                return res
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
                continue
            stats["jql_error_count"] += 1
            stats["api_errors"] += 1
            res = {"valid": False, "status": "ERROR", "message": str(e), "total_issues": 0}
            jql_status_cache[jql_str] = res
            log(f"    [JQL CHECK EXCEPTION] {e}")
            return res
            
    res = {"valid": False, "status": "ERROR", "message": "Retry exhausted", "total_issues": 0}
    jql_status_cache[jql_str] = res
    return res

def get_filter_status(filter_id):
    """
    Checks if a Saved Filter exists and is valid in Jira DC (/rest/api/2/filter/{filter_id}).
    """
    if not filter_id:
        return {"exists": False, "status": "INVALID_ID", "jql": None, "name": None}
    filter_id = str(filter_id).strip()
    if filter_id in filter_status_cache:
        return filter_status_cache[filter_id]
        
    url = f"{JIRA_CORE_URL}/filter/{filter_id}"
    not_found_sentinel = {"__not_found__": True}
    data = fetch_json(url, token=JIRA_DC_PAT, prefix="Bearer", not_found_value=not_found_sentinel)
    
    if data is not_found_sentinel:
        res = {"exists": False, "status": "NOT_FOUND", "jql": None, "name": None}
        filter_status_cache[filter_id] = res
        record_issue("FILTER NOT FOUND (404)", f"Saved Filter '{filter_id}' does not exist in Jira DC ({url})")
        return res
    elif data and isinstance(data, dict):
        filter_name = data.get("name")
        jql = data.get("jql")
        res = {"exists": True, "status": "ACTIVE", "jql": jql, "name": filter_name}
        filter_status_cache[filter_id] = res
        return res
    else:
        res = {"exists": False, "status": "ERROR", "jql": None, "name": None}
        filter_status_cache[filter_id] = res
        return res

def get_board_status(board_id):
    """Check a configured Jira Software board without assuming a 404 means deletion."""
    board_id = str(board_id).strip()
    if board_id in board_status_cache:
        return board_status_cache[board_id]
    if not board_id.isdigit():
        result = {"status": "INVALID_ID", "name": None}
    else:
        url = f"{JIRA_BASE_URL}/rest/agile/1.0/board/{board_id}"
        missing = {"__not_found__": True}
        data = fetch_json(url, token=JIRA_DC_PAT, not_found_value=missing)
        if data is missing:
            result = {"status": "NOT_FOUND_OR_INACCESSIBLE", "name": None}
        elif isinstance(data, dict):
            result = {"status": "ACTIVE", "name": data.get("name")}
            config = fetch_json(f"{url}/configuration", token=JIRA_DC_PAT)
            if isinstance(config, dict):
                board_filter = config.get("filter")
                filter_id = board_filter.get("id") if isinstance(board_filter, dict) else None
                if filter_id:
                    filter_state = get_filter_status(filter_id)
                    result["filter_id"] = str(filter_id)
                    if filter_state["status"] != "ACTIVE":
                        filter_status = ("NOT_FOUND_OR_INACCESSIBLE" if filter_state["status"] == "NOT_FOUND"
                                         else filter_state["status"])
                        result["status"] = f"FILTER_{filter_status}"
                else:
                    result["status"] = "CONFIG_UNVERIFIED"
            else:
                result["status"] = "CONFIG_UNVERIFIED"
        else:
            result = {"status": "ERROR", "name": None}
    board_status_cache[board_id] = result
    return result

def get_jira_fields():
    """Fetch all fields configured in Jira DC (system and custom fields)."""
    global jira_fields_cache
    if jira_fields_cache:
        return jira_fields_cache
    url = f"{JIRA_BASE_URL}/rest/api/2/field"
    data = fetch_json(url, token=JIRA_DC_PAT)
    if isinstance(data, list):
        for f in data:
            if isinstance(f, dict):
                fid = f.get("id")
                fname = f.get("name")
                if fid:
                    jira_fields_cache[str(fid).lower()] = f
                if fname:
                    jira_fields_cache[str(fname).lower()] = f
    return jira_fields_cache

def validate_connected_field(field_identifier):
    """
    Validates if a Jira field identifier (customfield_XXXXX, ID, or Name) exists in Jira DC.
    """
    if not field_identifier:
        return {"exists": False, "status": "EMPTY", "error": "필드 식별자 누락"}
    
    clean_key = str(field_identifier).strip().lower()
    fields = get_jira_fields()
    if not fields:
        return {"exists": False, "status": "CACHE_UNAVAILABLE", "error": "Jira 필드 목록을 조회할 수 없습니다."}
    
    if clean_key in fields:
        f = fields[clean_key]
        return {
            "exists": True,
            "status": "ACTIVE",
            "id": f.get("id"),
            "name": f.get("name"),
            "custom": f.get("custom", False),
            "schema": f.get("schema", {})
        }
    return {
        "exists": False,
        "status": "NOT_FOUND",
        "error": f"Jira DC에 '{field_identifier}' 필드가 존재하지 않습니다 (삭제되었거나 잘못된 식별자)."
    }

def audit_scope_references(project_keys, saved_filters, boards):
    """Return configured values and their Jira lookup results without altering Box scope."""
    references = []
    for key in project_keys:
        project_state = get_project_status(key)
        resolved_key = project_state.get("key", key)
        status = project_state["status"]
        if status == "ACTIVE" and not str(key).isdigit() and resolved_key != key:
            status = "KEY_CHANGED"
        references.append({"type": "PROJECT", "id": key, "status": status,
                           "resolved_key": resolved_key})
    for filter_id in saved_filters:
        filter_state = get_filter_status(filter_id)
        status = "NOT_FOUND_OR_INACCESSIBLE" if filter_state["status"] == "NOT_FOUND" else filter_state["status"]
        references.append({"type": "FILTER", "id": filter_id, "status": status})
    for board_id in boards:
        board_state = get_board_status(board_id)
        references.append({"type": "BOARD", "id": board_id, "status": board_state["status"],
                           "related_filter_id": board_state.get("filter_id", "")})
    return references

def export_scope_audit(boxes, path):
    """Write every scanned Box, including Boxes excluded from migration."""
    with open(path, "w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["Box ID", "Box Name", "Box Status", "Scope Status", "Migration Selected",
                         "Reference Type", "Configured ID", "Reference Status", "Resolved Project Key", "Board Filter ID",
                         "Configured Projects", "Active Projects", "Scope Detail"])
        for box in boxes:
            references = box.get("scope_references") or [{"type": "NONE", "id": "", "status": "NO_REFERENCES"}]
            for reference in references:
                writer.writerow([box.get("boxId"), box.get("boxName"),
                                 box.get("effective_box_status", box.get("boxStatus")), box.get("scope_status"),
                                 box.get("migration_selected", False), reference["type"],
                                 reference["id"], reference["status"], reference.get("resolved_key", ""),
                                 reference.get("related_filter_id", ""),
                                 ", ".join(box.get("configured_projects", box.get("jira_projects", []))),
                                 ", ".join(box.get("active_projects", [])),
                                 box.get("filter_reason") or ""])
def export_boxes_for_excel(boxes, tsv_path, csv_path):
    """Exports the box list into Excel-friendly TSV and CSV (UTF-8 with BOM) files."""
    headers = [
        "순번", "Box ID", "Box Name", "Box Type", "Box 상태 (Status)",
        "시작일 (Start Date)", "종료일 (End Date)", 
        "소유자 (Owner Name)", "소유자 이메일 (Owner Email)", 
        "연동 Jira 프로젝트 (Jira Projects)", "Scope 상태 (Scope Status)", 
        "JQL 설정 (Narrowing Query)", "JQL 검증 상태 (JQL Status)",
        "검증 상세 / 비고 (Validation Detail)", 
        "BigGantt DC URL", "BigPicture DC URL",
        "상위 Box ID (Parent Box ID)", "계층 레벨 (Depth)"
    ]
    
    rows = []
    for idx, b in enumerate(boxes, 1):
        box_id = b.get("boxId", "")
        parent_id = b.get("parentBoxId") or "ROOT"
        depth = b.get("depth", 0)
        prefix = b.get("hierarchy_prefix")
        if prefix is None and depth > 0:
            prefix = ("│  " * (depth - 1)) + "│  ⎿  " if depth > 0 else ""
        raw_name = str(b.get("boxName", "") or "")
        display_name = f"{prefix}{raw_name}" if prefix else raw_name

        base_url = b.get("source_base_url", BASE_URL or "")
        gantt_url = f"{base_url}/secure/GanttWebConsole.jspa?boxId={box_id}" if base_url and box_id else ""
        bp_url = f"{base_url}/secure/BigPicture.jspa?boxId={box_id}#box/{box_id}" if base_url and box_id else ""
        
        scope_status = b.get("scope_status", "VALID")
        scope_label = {
            "VALID": "정상 (Active)",
            "PARTIAL_VALID": "부분 정상 (일부 404 제외)",
            "EMPTY_SCOPE": "Scope 누락 (프로젝트 없음)",
            "PROJECT_NOT_FOUND": "프로젝트 404 (Jira에 없음)",
            "ALL_ARCHIVED": "프로젝트 보관됨 (Archived)",
            "NON_PROJECT_SCOPE": "비프로젝트 Scope (Filter/Board)"
        }.get(scope_status, scope_status)
        
        box_st = str(b.get("effective_box_status") or b.get("boxStatus") or "-").upper()
        is_jql_empty = b.get("is_jql_empty") or (bool(b.get("narrowing_query") and str(b.get("narrowing_query")).strip()) and b.get("jql_total_matched", 0) == 0)
        if box_st == "CLOSED":
            status_label = "CLOSED (제외)"
        elif not b.get("is_scope_valid", True):
            status_label = f"OPEN ({scope_label} 제외)"
        elif is_jql_empty:
            status_label = "OPEN (JQL 0건 제외)"
        elif b.get("jql_status") in ("INVALID", "ERROR"):
            status_label = "OPEN (JQL 오류 제외)"
        elif b.get("migration_selected", True):
            status_label = "OPEN (이관 대상)"
        else:
            status_label = f"OPEN ({scope_label} 제외)"

        jql_query = b.get("narrowing_query") or "-"
        jql_status = b.get("jql_status", "NONE")
        jql_error = b.get("jql_error")
        if jql_status == "VALID":
            total_issues = b.get("jql_total_matched", 0)
            if total_issues == 0 and b.get("narrowing_query"):
                jql_status_label = "정상 (Matched: 0건 - 제외)"
            else:
                jql_status_label = f"정상 (Matched: {total_issues}건)"
        elif jql_status == "WARNING":
            total_issues = b.get("jql_total_matched", 0)
            if total_issues == 0 and b.get("narrowing_query"):
                jql_status_label = f"경고: 0건 Matched (제외) - {jql_error}" if jql_error else "경고 (0건 Matched - 제외)"
            else:
                jql_status_label = f"경고: {jql_error}" if jql_error else "JQL 검증 경고"
        elif jql_status == "INVALID":
            jql_status_label = f"오류: {jql_error}" if jql_error else "문법/필드 오류"
        elif jql_status == "ERROR":
            jql_status_label = f"검증실패: {jql_error}"
        else:
            jql_status_label = "미설정 (None)"
            
        validation_detail = b.get("filter_reason") or b.get("validation_status") or "정상"
        if b.get("not_found_projects"):
            validation_detail += f" [제외된 404 프로젝트: {', '.join(b['not_found_projects'])}]"
        if jql_status == "INVALID":
            validation_detail += f" [JQL 오류: {jql_error}]"
            
        projects_str = ", ".join(b.get("jira_projects", []))
        clean_name = display_name.replace("\t", " ").replace("\r", "").replace("\n", " ")
        clean_jql = str(jql_query).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        clean_detail = str(validation_detail).replace("\t", " ").replace("\r", "").replace("\n", " ")
        
        rows.append([
            str(b.get("index") or idx),
            str(box_id),
            clean_name,
            str(b.get("boxType", "") or "-"),
            status_label,
            str(b.get("boxStartDate", "") or "-"),
            str(b.get("boxEndDate", "") or "-"),
            str(b.get("owner_name", "") or "-"),
            str(b.get("owner_email", "") or "-"),
            projects_str,
            scope_label,
            clean_jql,
            jql_status_label,
            clean_detail,
            gantt_url,
            bp_url,
            str(parent_id),
            str(depth)
        ])

    try:
        with open(tsv_path, "w", encoding="utf-8-sig") as f:
            f.write("\t".join(headers) + "\n")
            for r in rows:
                f.write("\t".join(r) + "\n")
        log(f"Saved Excel-compatible TSV export to: {tsv_path}")
    except Exception as e:
        log(f"Failed to save TSV export: {e}")

    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        log(f"Saved Excel-compatible CSV export to: {csv_path}")
    except Exception as e:
        log(f"Failed to save CSV export: {e}")

def extract_scope_details(scope_data):
    """
    Extracts detailed scope configurations from BigPicture/BigGantt DC scope definition:
    - project_keys: list of Jira project keys (uppercase)
    - narrowing_query: JQL string if configured, else None
    - saved_filters: list of filter IDs/info
    - boards: list of board IDs/info
    - connected_fields: list of connected Jira fields / sync configurations
    - other_elements: list of any other scope elements
    - owner_key: key of scope owner
    """
    res = {
        "project_keys": [],
        "disabled_projects": [],
        "narrowing_query": None,
        "saved_filters": [],
        "boards": [],
        "connected_fields": [],
        "other_elements": [],
        "owner_key": None,
        "task_types": [],
        "projects_enabled": True,
        "all_task_types_disabled": False,
        "is_corrupted": False
    }
    if not scope_data:
        return res
    
    cargo = scope_data.get("cargo", {})
    platforms = cargo.get("extPlatformScopeDefinitions", [])
    for plat in platforms:
        if plat.get("isCorrupted"):
            res["is_corrupted"] = True

        # Owner
        owner_key = plat.get("scopeOwner", {}).get("key")
        if owner_key and not res["owner_key"]:
            res["owner_key"] = owner_key
            
        # Narrowing Query (JQL)
        nq = plat.get("narrowingQuery")
        if nq and str(nq).strip():
            res["narrowing_query"] = str(nq).strip()

        # Check taskTypes (Task synchronization enabling flags)
        task_types = plat.get("taskTypes", [])
        task_types_map = {}
        for tt in task_types:
            if isinstance(tt, dict):
                lbl = str(tt.get("label", "")).strip().upper()
                key = tt.get("key")
                enabled = bool(tt.get("isEnabled", True))
                if lbl:
                    task_types_map[lbl] = enabled
                if key is not None:
                    task_types_map[str(key)] = enabled

        # Projects taskType: label "PROJECTS" or key 5
        projects_enabled = task_types_map.get("PROJECTS", task_types_map.get("5", True))
        all_task_types_disabled = bool(task_types and all(not enabled for enabled in task_types_map.values()))
        res["task_types"] = task_types
        res["projects_enabled"] = projects_enabled
        res["all_task_types_disabled"] = all_task_types_disabled
            
        # Scope Definition Elements
        elements = plat.get("scopeDefinitionElements", [])
        for el in elements:
            if el.get("isEnabled") is False or el.get("isAvailable") is False or el.get("enabled") is False:
                continue

            el_type = str(el.get("type", "")).upper()
            url = el.get("url", "")
            el_val = str(el.get("value") or el.get("id") or "").strip()
            
            if el_type == "JIRA_PROJECT":
                path_key = unquote(urlsplit(str(url)).path.rstrip("/").split("/")[-1]) if url else ""
                project_key = el.get("key") or path_key or el_val
                if project_key:
                    if not projects_enabled:
                        res["disabled_projects"].append(str(project_key).upper())
                    else:
                        res["project_keys"].append(str(project_key).upper())
            elif el_type in ("JIRA_FILTER", "JIRA_SAVED_FILTER", "SAVED_FILTER", "FILTER"):
                filter_id = el_val or (url.split("/")[-1] if url else "")
                if filter_id:
                    res["saved_filters"].append(str(filter_id))
            elif el_type in ("JIRA_AGILE_BOARD", "JIRA_BOARD", "AGILE_BOARD", "BOARD"):
                board_id = el_val or (url.split("/")[-1] if url else "")
                if board_id:
                    res["boards"].append(str(board_id))
            elif el_type in ("JIRA_FIELD", "CONNECTED_FIELD", "CUSTOM_FIELD", "FIELD") or el.get("fieldId") or el.get("fieldName"):
                field_id = el.get("fieldId") or el_val
                field_name = el.get("fieldName") or el.get("name") or ""
                res["connected_fields"].append({
                    "type": el_type or "FIELD",
                    "id": str(field_id),
                    "name": str(field_name),
                    "url": url,
                    "raw": el
                })
            else:
                if el_type:
                    res["other_elements"].append({"type": el_type, "url": url, "raw": el})

        # Check for connected field / sync configurations in platform definition
        for fkey in ("connectedField", "connectedFields", "connectedJiraField", "syncField", "synchronizer", "taskSynchronizer"):
            val = plat.get(fkey)
            if val:
                res["connected_fields"].append({"source": f"platform.{fkey}", "data": val})

    for fkey in ("connectedField", "connectedFields", "synchronizers"):
        val = cargo.get(fkey)
        if val:
            res["connected_fields"].append({"source": f"cargo.{fkey}", "data": val})
                
    # Deduplicate preserving order
    res["project_keys"] = list(dict.fromkeys(res["project_keys"]))
    res["disabled_projects"] = list(dict.fromkeys(res["disabled_projects"]))
    res["saved_filters"] = list(dict.fromkeys(res["saved_filters"]))
    res["boards"] = list(dict.fromkeys(res["boards"]))
    return res

def extract_project_keys(scope_data):
    return extract_scope_details(scope_data)["project_keys"]

def load_target_projects():
    if not os.path.exists(PROJECTS_FILE):
        log(f"Warning: projects.txt not found: {PROJECTS_FILE}")
        return set()
        
    log(f"Loaded target projects from '{PROJECTS_FILE}'")
    with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
        return {line.strip().upper() for line in f if line.strip()}

def prefetch_box_scopes(root):
    """Fetch Box scopes once and collect their configured Project references."""
    scope_cache = {}
    configured_projects = set()

    def visit(node, inherited_closed=False):
        if not isinstance(node, dict) or node.get("boxArchived") is True:
            return
        closed = inherited_closed or str(node.get("boxStatus") or "").upper() == "CLOSED"
        box_id = node.get("boxId")
        if box_id and str(box_id).upper() != "ROOT":
            scope_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/area/task/scope/def/own/{box_id}"
            scope_data = fetch_json(scope_url, token=BG_DC_JWT_TOKEN)
            if scope_data:
                try:
                    configured_projects.update(extract_scope_details(scope_data)["project_keys"])
                except (AttributeError, TypeError, KeyError) as exc:
                    record_issue("BOX SCOPE PARSE", f"Box {box_id}: {exc}")
                    scope_data = None
            scope_cache[str(box_id)] = scope_data
        for child in node.get("boxChildren") or []:
            visit(child, closed)

    visit(root)
    return scope_cache, configured_projects

def validate_box_projects_with_progress(configured_projects):
    """Check unique Projects configured on open Boxes; reuse results during Box scans."""
    total = len(configured_projects)
    if not total:
        log("Box-configured Project validation: 0/0")
        return

    log(f"Box-configured Project validation started: 0/{total}")
    interval = max(1, total // 100)
    counts = {"ACTIVE": 0, "ARCHIVED": 0, "NOT_FOUND": 0, "ERROR": 0}
    for completed, project_key in enumerate(sorted(configured_projects), 1):
        status = get_project_status(project_key)["status"]
        counts[status if status in counts else "ERROR"] += 1
        if completed == 1 or completed % interval == 0 or completed == total:
            log(f"Box-configured Projects checked: {completed}/{total} ({completed / total:.0%}) "
                f"| active {counts['ACTIVE']}, archived {counts['ARCHIVED']}, "
                f"not found {counts['NOT_FOUND']}, check failed {counts['ERROR']}")

def fetch_box_types():
    url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/type"
    log(f"Fetching box types from: {url}")
    types_data = fetch_json(url, token=BG_DC_JWT_TOKEN)
    
    type_map = {}
    if types_data and isinstance(types_data, dict):
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
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%b/%y").upper()
    except Exception:
        return date_str

def format_elapsed_time(seconds):
    if seconds is None:
        return "0.00초"
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours > 0:
        return f"{int(hours)}시간 {int(minutes)}분 {sec:.1f}초 ({seconds:.2f}s)"
    elif minutes > 0:
        return f"{int(minutes)}분 {sec:.1f}초 ({seconds:.2f}s)"
    else:
        return f"{sec:.2f}초"

def get_hierarchy_prefix(depth):
    """Returns a visual tree prefix for hierarchy display (e.g. │  ⎿  )."""
    if depth <= 0:
        return ""
    elif depth == 1:
        return "│  ⎿  "
    else:
        return ("│  " * (depth - 1)) + "│  ⎿  "

def process_node(node, target_projects, migration_boxes, all_scanned_boxes, box_type_map,
                 parent_id=None, depth=0, inherited_closed=False, scope_cache=None):
    if not isinstance(node, dict):
        stats["failed"] += 1
        stats["box_processing_errors"] += 1
        record_issue("BOX PROCESSING", f"Invalid hierarchy node under parent {parent_id}: {type(node).__name__}")
        return

    if node.get("boxArchived") is True:
        return

    box_id = node.get("boxId")
    box_name = node.get("boxName")
    box_status = str(node.get("boxStatus") or "UNKNOWN").upper()
    is_closed = inherited_closed or box_status == "CLOSED"

    # Skip scope definition lookup for virtual system root box ("ROOT")
    if str(box_id).upper() == "ROOT":
        children = node.get("boxChildren") or []
        for child in children:
            process_node(child, target_projects, migration_boxes, all_scanned_boxes,
                         box_type_map, parent_id="ROOT", depth=0, inherited_closed=is_closed,
                         scope_cache=scope_cache)
        return

    effective_parent_id = str(parent_id or "ROOT")
    if effective_parent_id == "None":
        effective_parent_id = "ROOT"
    prefix = get_hierarchy_prefix(depth)
    display_name = f"{prefix}{box_name}"

    try:
        scope_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/area/task/scope/def/own/{box_id}"
        scope_data = (scope_cache.get(str(box_id)) if scope_cache is not None
                      else fetch_json(scope_url, token=BG_DC_JWT_TOKEN))
        
        if scope_data:
            stats["processed"] += 1
            scope_info = extract_scope_details(scope_data)
            box_project_keys = scope_info["project_keys"]
            narrowing_query = scope_info["narrowing_query"]
            saved_filters = scope_info["saved_filters"]
            boards = scope_info["boards"]

            # Exclude boxes with NO project scope configured from report and migration
            if not box_project_keys:
                stats["invalid_scope_empty"] += 1
                stats["filtered_out_invalid_scope"] += 1
                if scope_info.get("all_task_types_disabled"):
                    reason = "모든 Task Types 비활성화 (Define scope 상태)"
                elif scope_info.get("disabled_projects"):
                    reason = f"Projects Task Type 비활성화 (isEnabled=false: {', '.join(scope_info['disabled_projects'])})"
                else:
                    reason = "Scope에 프로젝트 미설정"
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> [NO PROJECT SCOPE] {reason}. Excluded from report and migration.")
                for child in node.get("boxChildren") or []:
                    process_node(child, target_projects, migration_boxes, all_scanned_boxes,
                                 box_type_map, parent_id=box_id, depth=depth + 1, inherited_closed=is_closed,
                                 scope_cache=scope_cache)
                return

            # Check if this box matches target_projects from projects.txt
            if target_projects:
                matched_target_keys = [k for k in box_project_keys if k in target_projects]
                if not matched_target_keys:
                    log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> Projects {box_project_keys} not in target projects. Skipping.")
                    for child in node.get("boxChildren") or []:
                        process_node(child, target_projects, migration_boxes, all_scanned_boxes,
                                     box_type_map, parent_id=box_id, depth=depth + 1, inherited_closed=is_closed,
                                     scope_cache=scope_cache)
                    return
            else:
                matched_target_keys = box_project_keys
            
            # Scope Owner Info
            owner_email = None
            owner_name = None
            owner_key = scope_info["owner_key"]
            if owner_key:
                owner_email, owner_name = get_user_info(owner_key)
            
            box_type_id = node.get("boxTypeId")
            box_type_name = box_type_map.get(str(box_type_id), box_type_id)

            # --- 1. JQL (Narrowing Query) Validation ---
            jql_status = "NONE"
            jql_error = None
            jql_matched_count = 0
            if narrowing_query:
                jql_res = validate_jql(narrowing_query)
                jql_status = jql_res["status"]
                jql_error = jql_res.get("message")
                jql_matched_count = jql_res.get("total_issues", 0)
                if jql_status in ("INVALID", "ERROR"):
                    record_issue("JQL VALIDATION ERROR", f"Box '{box_name}' ({box_id}) JQL: '{narrowing_query}' -> {jql_error}")

            # --- 2. Scope Validation & Verification ---
            active_keys = []
            not_found_keys = []
            archived_keys = []
            error_keys = []

            for key in box_project_keys:
                p_status = get_project_status(key)
                st = p_status.get("status")
                if st == "ACTIVE":
                    active_keys.append(p_status.get("key", key))
                elif st == "NOT_FOUND":
                    not_found_keys.append(key)
                elif st == "ARCHIVED":
                    archived_keys.append(key)
                else:
                    error_keys.append(key)

            active_keys = list(dict.fromkeys(active_keys))

            scope_references = audit_scope_references(box_project_keys, saved_filters, boards)
            bad_references = [ref for ref in scope_references if ref["status"] != "ACTIVE"]
            if bad_references:
                stats["scope_reference_issue_boxes"] += 1
                details = ", ".join(f"{ref['type']} {ref['id']}: {ref['status']}" for ref in bad_references)
                record_issue("SCOPE REFERENCE ISSUE", f"Box '{box_name}' ({box_id}) -> {details}")

            is_scope_valid = True
            scope_status = "VALID"
            filter_reason = None

            if not active_keys:
                is_scope_valid = False
                if error_keys:
                    scope_status = "SCOPE_CHECK_FAILED"
                    filter_reason = f"Jira 프로젝트 상태 확인 실패: {', '.join(error_keys)}"
                elif not_found_keys and not archived_keys:
                    scope_status = "PROJECT_NOT_FOUND"
                    filter_reason = f"연결된 Jira 프로젝트 미존재 (Jira 404 Not Found: {', '.join(not_found_keys)})"
                    stats["invalid_scope_not_found"] += 1
                elif archived_keys and not not_found_keys:
                    scope_status = "ALL_ARCHIVED"
                    filter_reason = f"연결된 Jira 프로젝트 모두 보관됨 (Archived: {', '.join(archived_keys)})"
                    stats["invalid_scope_archived"] += 1
                else:
                    scope_status = "PROJECT_NOT_FOUND"
                    filter_reason = f"유효한 활성 프로젝트 없음 (404: {not_found_keys}, 보관: {archived_keys})"
                    stats["invalid_scope_not_found"] += 1
                stats["filtered_out_invalid_scope"] += 1
                record_issue(f"INVALID SCOPE ({scope_status})", f"Box '{box_name}' ({box_id}) -> {filter_reason}")
            elif bad_references:
                scope_status = "PARTIAL_VALID"
                filter_reason = ("Scope 설정 확인 필요: " + ", ".join(
                    f"{ref['type']} {ref['id']} ({ref['status']})" for ref in bad_references
                ))
                log(f"Processing Box: {box_name} ({box_id}) -> [PARTIAL SCOPE] {filter_reason}; active projects: {active_keys}")

            effective_status = "CLOSED" if is_closed else box_status
            if is_closed:
                stats["closed_boxes_count"] += 1
                closed_reason = "상위 Box가 Closed 상태" if (inherited_closed and box_status != "CLOSED") else "Box 상태가 Closed"
                filter_reason = f"{closed_reason} (이관 대상 제외)" if not filter_reason else f"{filter_reason}, {closed_reason}"

            # --- JQL 0건 및 오류 검사 (이관 제외 기준) ---
            has_narrowing_query = bool(narrowing_query and str(narrowing_query).strip())
            is_jql_empty = has_narrowing_query and (jql_matched_count == 0)
            is_jql_invalid = jql_status in ("INVALID", "ERROR")

            if not is_closed:
                if is_jql_empty:
                    stats["jql_empty_result_count"] += 1
                    jql_msg = f"JQL 검색 결과 0건 (이관 대상 제외 - '{narrowing_query}')"
                    filter_reason = f"{filter_reason}, {jql_msg}" if filter_reason else jql_msg
                elif is_jql_invalid:
                    jql_msg = f"JQL 오류 (이관 대상 제외 - {jql_error})"
                    filter_reason = f"{filter_reason}, {jql_msg}" if filter_reason else jql_msg

            # Validation status string
            val_issues = []
            if is_closed:
                val_issues.append("Closed(제외)")
            if not is_scope_valid:
                val_issues.append(f"Scope제외({scope_status})")
            elif scope_status == "PARTIAL_VALID":
                val_issues.append(f"Scope참조확인({len(bad_references)}개)")
            if is_jql_empty:
                val_issues.append("JQL결과0건(제외)")
            elif is_jql_invalid:
                val_issues.append("JQL오류(제외)")
            elif jql_status == "WARNING":
                val_issues.append("JQL경고")
            validation_status = ", ".join(val_issues) if val_issues else "정상"

            is_migration_target = (not is_closed and is_scope_valid and not is_jql_empty and not is_jql_invalid)

            scanned_entry = {
                "boxId": box_id,
                "parentBoxId": effective_parent_id,
                "depth": depth,
                "hierarchy_prefix": prefix,
                "displayName": display_name,
                "source_environment": ENV,
                "source_base_url": BASE_URL,
                "boxName": box_name,
                "boxType": box_type_name,
                "boxStatus": box_status,
                "effective_box_status": effective_status,
                "is_closed": is_closed,
                "boxStartDate": format_date(node.get("boxStartDate")),
                "boxEndDate": format_date(node.get("boxEndDate")),
                "owner_email": owner_email,
                "owner_name": owner_name,
                "jira_projects": active_keys if active_keys else box_project_keys,
                "configured_projects": box_project_keys,
                "active_projects": active_keys,
                "matched_target_projects": matched_target_keys,
                "not_found_projects": not_found_keys,
                "archived_projects": archived_keys,
                "scope_references": scope_references,
                "migration_selected": is_migration_target,
                "narrowing_query": narrowing_query,
                "jql_status": jql_status,
                "jql_error": jql_error,
                "jql_total_matched": jql_matched_count,
                "is_jql_empty": is_jql_empty,
                "saved_filters": saved_filters,
                "boards": boards,
                "scope_status": "CLOSED" if (is_closed and is_scope_valid) else scope_status,
                "is_scope_valid": is_scope_valid,
                "validation_status": validation_status,
                "filter_reason": filter_reason
            }
            all_scanned_boxes.append(scanned_entry)
            
            # --- Filtering Logic ---
            if is_closed:
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> [TARGET BOX - CLOSED] Matches target keys {matched_target_keys}; settings validated.")
            elif not is_scope_valid:
                # Exclude boxes with broken/empty/404 project scopes
                excluded_invalid_boxes.append(dict(scanned_entry))
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> [FILTERED OUT] {filter_reason}. Box excluded from migration.")
            elif is_jql_empty:
                excluded_invalid_boxes.append(dict(scanned_entry))
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> [FILTERED OUT] JQL 검색 결과 0건 (Narrowing Query: '{narrowing_query}'). Box excluded from migration.")
            elif is_jql_invalid:
                excluded_invalid_boxes.append(dict(scanned_entry))
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> [FILTERED OUT] JQL 오류 ({jql_error}). Box excluded from migration.")
            else:
                stats["matched"] += 1
                log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> MATCH FOUND! (Active Target Keys: {matched_target_keys})")
                migration_entry = dict(scanned_entry)
                migration_entry["jira_projects"] = active_keys
                migration_entry["parentBoxId"] = effective_parent_id
                migration_entry["depth"] = depth
                migration_entry["hierarchy_prefix"] = prefix
                migration_entry["displayName"] = display_name
                migration_boxes.append(migration_entry)
        else:
            stats["failed"] += 1
            log(f"Processing Box: {box_name} ({box_id}), Parent: {parent_id} (Level {depth}) -> Failed to fetch scope definition. Continuing with next Box.")
            all_scanned_boxes.append({
                "boxId": box_id, "parentBoxId": effective_parent_id, "depth": depth,
                "hierarchy_prefix": prefix, "displayName": display_name,
                "boxName": box_name, "boxType": node.get("boxTypeId"),
                "scope_status": "SCOPE_FETCH_FAILED", "is_scope_valid": False,
                "jira_projects": [], "configured_projects": [], "active_projects": [],
                "scope_references": [], "migration_selected": False,
                "filter_reason": "Box Scope 정의 API 조회 실패"
            })
    except Exception as e:
        stats["failed"] += 1
        stats["box_processing_errors"] += 1
        record_issue("BOX PROCESSING", f"{box_name} ({box_id}) -> {e}")
        log(f"Processing Box: {box_name} ({box_id}) -> Exception: {e}. Continuing with next Box.")
        if not any(box.get("boxId") == box_id for box in all_scanned_boxes):
            all_scanned_boxes.append({
                "boxId": box_id, "parentBoxId": effective_parent_id, "depth": depth,
                "hierarchy_prefix": prefix, "displayName": display_name,
                "boxName": box_name, "boxType": node.get("boxTypeId"),
                "scope_status": "SCOPE_CHECK_FAILED", "is_scope_valid": False,
                "jira_projects": [], "configured_projects": [], "active_projects": [],
                "scope_references": [], "migration_selected": False,
                "filter_reason": f"Box Scope 검증 예외: {e}"
            })

    children = node.get("boxChildren") or []
    for child in children:
        process_node(child, target_projects, migration_boxes, all_scanned_boxes,
                     box_type_map, parent_id=box_id, depth=depth + 1, inherited_closed=is_closed,
                     scope_cache=scope_cache)

def test_single_box(target_box_id, target_projects=None):
    """
    Detailed test and diagnosis of a single Box by ID.
    Fetches hierarchy node, raw scope definition, validates all elements,
    and checks if connected Jira fields, JQL, filters, or projects are broken or empty.
    """
    if target_projects is None:
        target_projects = load_target_projects()

    target_id_str = str(target_box_id).strip()
    log("\n" + "=" * 70)
    log(f"🔍 [SINGLE BOX TEST] Detailed Diagnosis for Box ID: {target_id_str}")
    log("=" * 70)

    # 1. Fetch Hierarchy to locate this box node
    hierarchy_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/box-hierarchy"
    hierarchy_data = fetch_json(hierarchy_url, token=BG_DC_JWT_TOKEN)
    box_node = None
    box_parent_id = None
    inherited_closed = False

    def find_box(node, parent_id=None, closed=False):
        nonlocal box_node, box_parent_id, inherited_closed
        if not isinstance(node, dict):
            return
        current_closed = closed or str(node.get("boxStatus") or "").upper() == "CLOSED"
        if str(node.get("boxId")) == target_id_str:
            box_node = node
            box_parent_id = parent_id
            inherited_closed = current_closed
            return
        for child in node.get("boxChildren") or []:
            find_box(child, parent_id=node.get("boxId"), closed=current_closed)
            if box_node:
                return

    if hierarchy_data and isinstance(hierarchy_data, dict):
        cargo = hierarchy_data.get("cargo", {})
        find_box(cargo)

    box_type_map = fetch_box_types()

    # 2. Box Basic Information
    log("\n📦 [1] Box Basic Information")
    if box_node:
        box_name = box_node.get("boxName", "-")
        box_status = str(box_node.get("boxStatus") or "-").upper()
        box_type_id = str(box_node.get("boxType", ""))
        box_type_name = box_type_map.get(box_type_id, box_type_id) or "-"
        is_archived = box_node.get("boxArchived", False)
        start_date = box_node.get("boxStartDate", "-")
        end_date = box_node.get("boxEndDate", "-")
        children = box_node.get("boxChildren") or []
        log(f"  • Box ID        : {target_id_str}")
        log(f"  • Box Name      : {box_name}")
        log(f"  • Box Type      : {box_type_name} (ID: {box_type_id})")
        log(f"  • Box Status    : {box_status} (Inherited Closed: {inherited_closed})")
        log(f"  • Archived      : {is_archived}")
        log(f"  • Parent Box ID : {box_parent_id or '(None / Root)'}")
        log(f"  • Dates         : {start_date} ~ {end_date}")
        log(f"  • Child Boxes   : {len(children)} children")
    else:
        log(f"  • Box ID        : {target_id_str}")
        log("  • Hierarchy     : 계층 구조에서 찾을 수 없습니다. (직접 Scope API 조회 시도)")
        box_name = f"Box-{target_id_str}"
        box_status = "UNKNOWN"
        box_type_name = "-"

    # 3. Fetch Scope Definition
    scope_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/area/task/scope/def/own/{target_id_str}"
    log(f"\n🌐 [2] Fetching Scope Definition from: {scope_url}")
    scope_data = fetch_json(scope_url, token=BG_DC_JWT_TOKEN)

    if not scope_data:
        log("❌ Scope Definition 조회 실패 (404 Not Found 또는 권한 없음).")
        log("=" * 70)
        return {
            "box_id": target_id_str,
            "status": "NOT_FOUND",
            "verdict": "❌ Scope 조회 불가",
            "issues": ["Scope Definition API 응답 없음 (404 또는 권한 없음)"]
        }

    # Print Pretty JSON
    log("\n📜 [3] Raw Scope Definition JSON:")
    try:
        log(json.dumps(scope_data, indent=2, ensure_ascii=False))
    except Exception as e:
        log(f"JSON dump error: {e}")

    # 4. Parse Scope Elements
    log("\n🔍 [4] Scope Parsing & Extraction:")
    scope_info = extract_scope_details(scope_data)
    project_keys = scope_info["project_keys"]
    narrowing_query = scope_info["narrowing_query"]
    saved_filters = scope_info["saved_filters"]
    boards = scope_info["boards"]
    connected_fields = scope_info["connected_fields"]
    other_elements = scope_info["other_elements"]
    owner_key = scope_info["owner_key"]

    log(f"  • Scope Owner       : {owner_key or '-'}")
    log(f"  • Jira Projects     : {project_keys if project_keys else '❌ 없음 (None)'}")
    if scope_info.get("disabled_projects"):
        log(f"    ⚠️ 비활성화된 Projects: {scope_info['disabled_projects']} (Task Types 설정에서 'Projects' isEnabled=false 로 제외됨)")
    if scope_info.get("all_task_types_disabled"):
        log(f"    ⚠️ 모든 Task Types가 비활성화됨 (Projects, Sprints, Components, Versions 모두 isEnabled=false)")
        log(f"       -> Box 화면에서 'Fill your box with the first tasks / Define scope' 안내가 표시되는 원인입니다!")
    log(f"  • Narrowing Query   : {narrowing_query if narrowing_query else '미설정 (None)'}")
    log(f"  • Saved Filters     : {saved_filters if saved_filters else '없음 (None)'}")
    log(f"  • Agile Boards      : {boards if boards else '없음 (None)'}")
    log(f"  • Connected Fields  : {connected_fields if connected_fields else '없음 (None)'}")
    log(f"  • Other Elements    : {other_elements if other_elements else '없음 (None)'}")

    # 5. Detailed Verification
    log("\n🩺 [5] Element Verification & Diagnostics:")
    issues_found = []

    # 5-1. Projects Verification
    if project_keys:
        for pkey in project_keys:
            pstate = get_project_status(pkey)
            pst = pstate.get("status")
            in_target = pkey in target_projects if target_projects else True
            target_mark = "🎯 Target" if in_target else "⚠️ Not in projects.txt"
            if pst == "ACTIVE":
                log(f"  [Project] {pkey} -> ✅ ACTIVE ({target_mark})")
            elif pst == "ARCHIVED":
                log(f"  [Project] {pkey} -> 📦 ARCHIVED ({target_mark})")
                issues_found.append(f"프로젝트 '{pkey}'가 Jira에서 보관(ARCHIVED)되었습니다.")
            elif pst == "NOT_FOUND":
                log(f"  [Project] {pkey} -> ❌ NOT FOUND (404) ({target_mark})")
                issues_found.append(f"프로젝트 '{pkey}'가 Jira에 존재하지 않습니다 (404).")
            else:
                log(f"  [Project] {pkey} -> ❓ {pst} ({target_mark})")
    else:
        if scope_info.get("disabled_projects"):
            issues_found.append(f"Projects Task Type이 비활성화(isEnabled: false)되어 프로젝트({', '.join(scope_info['disabled_projects'])})가 제외되었습니다.")
        else:
            issues_found.append("Scope에 연결된 Jira Project가 없습니다.")

    # 5-2. Narrowing Query (JQL) Verification
    jql_matched_count = None
    if narrowing_query:
        jql_res = validate_jql(narrowing_query)
        jst = jql_res.get("status")
        jissues = jql_res.get("total_issues", 0)
        jerr = jql_res.get("error")
        jql_matched_count = jissues

        if jst == "VALID":
            if jissues == 0:
                log(f"  [JQL] '{narrowing_query}' -> 📭 VALID (Matched: 0건) ⚠️ 검색 결과가 0건입니다! Box에 아무것도 안 나오는 원인일 수 있습니다.")
                issues_found.append("Narrowing JQL 검색 결과가 0건이어서 Box 내 내용이 비어있습니다.")
            else:
                log(f"  [JQL] '{narrowing_query}' -> ✅ VALID (Matched: {jissues}건)")
        elif jst == "WARNING":
            if jissues == 0:
                log(f"  [JQL] '{narrowing_query}' -> ⚠️ WARNING (0건 Matched: {jerr})")
                issues_found.append(f"Narrowing JQL 경고 및 0건 검색: {jerr}")
            else:
                log(f"  [JQL] '{narrowing_query}' -> ⚠️ WARNING ({jerr}, Matched: {jissues}건)")
        elif jst in ("INVALID", "ERROR"):
            log(f"  [JQL] '{narrowing_query}' -> ❌ {jst} ({jerr})")
            issues_found.append(f"Narrowing JQL 오류: {jerr}")

    # 5-3. Saved Filters Verification
    for fid in saved_filters:
        fstate = get_filter_status(fid)
        fst = fstate.get("status")
        fname = fstate.get("name")
        if fst == "ACTIVE":
            log(f"  [Filter] ID {fid} ('{fname}') -> ✅ ACTIVE")
        else:
            log(f"  [Filter] ID {fid} -> ❌ {fst}")
            issues_found.append(f"Saved Filter '{fid}' 접근 불가 또는 미존재 ({fst})")

    # 5-4. Agile Boards Verification
    for bid in boards:
        bstate = get_board_status(bid)
        bst = bstate.get("status")
        bname = bstate.get("name")
        if bst == "ACTIVE":
            log(f"  [Board] ID {bid} ('{bname}') -> ✅ ACTIVE")
        else:
            log(f"  [Board] ID {bid} -> ❌ {bst}")
            issues_found.append(f"Agile Board '{bid}' 접근 불가 또는 미존재 ({bst})")

    # 5-5. Connected Jira Fields Verification
    if connected_fields:
        for citem in connected_fields:
            c_data = citem.get("raw") or citem.get("data") or citem
            c_id = citem.get("id") or (c_data.get("id") if isinstance(c_data, dict) else "") or str(c_data)
            c_name = citem.get("name") or (c_data.get("name") if isinstance(c_data, dict) else "")
            target_id = c_id or c_name
            fval = validate_connected_field(target_id)
            if fval["exists"]:
                log(f"  [Connected Field] {target_id} -> ✅ ACTIVE in Jira DC (ID: {fval['id']}, Name: '{fval['name']}', Custom: {fval['custom']})")
                try:
                    field_search_jql = f"cf[{fval['id'].replace('customfield_', '')}] is not EMPTY" if fval['id'].startswith("customfield_") else f'"{fval["name"]}" is not EMPTY'
                    cf_res = validate_jql(field_search_jql)
                    cf_cnt = cf_res.get("total_issues", 0)
                    if cf_cnt == 0:
                        log(f"    ℹ️ '{fval['name']}' 필드에 값이 입력된 이슈: 0건 (필드가 더 이상 사용되지 않음!)")
                        issues_found.append(f"Connected Jira Field '{fval['name']}'에 데이터가 있는 이슈가 0건입니다.")
                    else:
                        log(f"    ℹ️ '{fval['name']}' 필드에 값이 입력된 이슈: {cf_cnt}건")
                except Exception:
                    pass
            else:
                log(f"  [Connected Field] {target_id} -> ❌ NOT FOUND in Jira DC ({fval.get('error')})")
                issues_found.append(f"Connected Jira Field '{target_id}'가 Jira DC에 존재하지 않습니다 (삭제되었거나 미존재).")

    # 6. Overall Verdict / 종합 판정
    log("\n⚖️ [6] 종합 판정 (Migration Verdict):")
    is_closed = inherited_closed or box_status == "CLOSED"
    is_scope_empty = not project_keys
    is_target_matched = any(k in target_projects for k in project_keys) if target_projects and project_keys else True
    is_jql_empty = bool(narrowing_query and jql_matched_count == 0)

    if is_closed:
        verdict = "❌ 이관 제외 (CLOSED)"
        verdict_reason = "상태가 CLOSED이거나 상위 Box가 CLOSED입니다."
    elif scope_info.get("all_task_types_disabled"):
        verdict = "❌ 이관 제외 (TASK TYPES 비활성화 / SCOPE 미정의)"
        verdict_reason = "Box의 모든 Task Types가 비활성화(isEnabled: false)되어 이슈가 연동되지 않습니다 (Define scope 상태)."
    elif is_scope_empty:
        if scope_info.get("disabled_projects"):
            verdict = "❌ 이관 제외 (PROJECTS 비활성화)"
            verdict_reason = f"Projects Task Type이 비활성화(isEnabled: false)되어 프로젝트({', '.join(scope_info['disabled_projects'])})가 제외되었습니다."
        else:
            verdict = "❌ 이관 제외 (NO PROJECT SCOPE)"
            verdict_reason = "Scope에 Jira Project 설정이 없습니다."
    elif not is_target_matched:
        verdict = "❌ 이관 제외 (TARGET NOT MATCHED)"
        verdict_reason = f"Box 프로젝트({project_keys})가 projects.txt({list(target_projects)[:5]}...)에 포함되어 있지 않습니다."
    elif is_jql_empty:
        verdict = "❌ 이관 제외 (JQL 0건 내용없음)"
        verdict_reason = f"Narrowing JQL '{narrowing_query}' 검색 결과가 0건입니다."
    elif issues_found:
        verdict = "⚠️ 주의 (검증 이상 발견)"
        verdict_reason = " | ".join(issues_found)
    else:
        verdict = "🚀 이관 대상 (MIGRATION CANDIDATE)"
        verdict_reason = "정상 활성 상태이며 모든 검증을 통과했습니다."

    log(f"  • 최종 판정 : {verdict}")
    log(f"  • 상세 사유 : {verdict_reason}")
    if issues_found:
        log("\n  [발견된 문제점 목록]")
        for idx, iss in enumerate(issues_found, 1):
            log(f"    {idx}. {iss}")

    # 7. Web Links
    log("\n🔗 [7] Direct Console Links:")
    base_link = BASE_URL.rstrip('/') if BASE_URL else ""
    gantt_url = f"{base_link}/plugins/servlet/softwareplant-biggantt/#/box/{quote(target_id_str, safe='')}/g" if base_link else ""
    bp_url = f"{base_link}/secure/BigPicture.jspa?boxId={target_id_str}#box/{target_id_str}" if base_link else ""
    log(f"  • BigGantt URL  : {gantt_url}")
    log(f"  • BigPicture URL: {bp_url}")
    log("=" * 70 + "\n")

    return {
        "box_id": target_id_str,
        "box_name": box_name,
        "box_status": box_status,
        "box_type": box_type_name,
        "scope_data": scope_data,
        "scope_info": scope_info,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "issues": issues_found
    }

def main():
    start_time = time.time()
    start_datetime = datetime.now()

    # Initialize log file
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(
            f"--- Migration Session Started: {start_datetime} "
            f"({get_version_label()}, ENV={ENV}, DRY_RUN={DRY_RUN}, TARGET_BOX={TARGET_BOX_ID or 'ALL'}) ---\n"
        )

    # If TARGET_BOX_ID is set (via CLI arg --box-id or interactive choice), run single box test mode
    if TARGET_BOX_ID:
        target_projects = load_target_projects()
        test_single_box(TARGET_BOX_ID, target_projects)
        return

    log(f"--- Starting Hierarchy Fetch & Box Process ({get_version_label()}, DRY_RUN={DRY_RUN}) ---")
    
    if not DRY_RUN:
        # Delete previous results
        for output_file in [HIERARCHY_FILE, BOXES_FILE, MIGRATION_BOX_FILE,
                            TSV_EXPORT_FILE, CSV_EXPORT_FILE, SCOPE_AUDIT_FILE]:
            if os.path.exists(output_file):
                os.remove(output_file)
                log(f"Deleted previous output file: {output_file}")

    target_projects = load_target_projects()
    log(f"Loaded {len(target_projects)} target projects from projects.txt")

    # Step 1: Fetch Box Hierarchy
    hierarchy_url = f"{BG_BASE_URL.rstrip('/')}/public/ppm/box/box-hierarchy"
    log(f"Fetching hierarchy from: {hierarchy_url}")
    
    try:
        data = fetch_json(hierarchy_url, token=BG_DC_JWT_TOKEN)
        if not data:
            record_issue(
                "HIERARCHY UNAVAILABLE",
                "Box hierarchy could not be loaded, so Box scanning was skipped."
            )
            log("Box hierarchy could not be loaded. Box scanning cannot continue, but the issue report will be generated.")
            return
            
        if not DRY_RUN:
            with open(HIERARCHY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                log(f"Saved raw hierarchy to {HIERARCHY_FILE}")
                
        # Count archived & unarchived, and save unarchived flat boxes to boxes.json
        all_boxes = []
        
        def collect_boxes(node):
            if isinstance(node, dict):
                is_archived = node.get("boxArchived") is True
                if is_archived:
                    stats["archived_boxes_count"] += 1
                else:
                    stats["unarchived_boxes_count"] += 1
                    box_info = {k: v for k, v in node.items() if k != "boxChildren"}
                    all_boxes.append(box_info)
                for child in node.get("boxChildren", []):
                    collect_boxes(child)
        
        cargo = data.get("cargo", {})
        collect_boxes(cargo)
        stats["total_boxes_found"] = stats["archived_boxes_count"] + stats["unarchived_boxes_count"]

        if not DRY_RUN:
            with open(BOXES_FILE, "w", encoding="utf-8") as f:
                json.dump(all_boxes, f, indent=2, ensure_ascii=False)
                log(f"Saved flat boxes list to {BOXES_FILE}")

        # Step 2: Fetch Box scopes, then validate only Projects actually configured on Boxes.
        scope_cache, configured_projects = prefetch_box_scopes(cargo)
        validate_box_projects_with_progress(configured_projects)

        # Step 3: Fetch Box Types
        box_type_map = fetch_box_types()

        # Step 4: Process cached Box Scopes and match against target Projects.
        migration_boxes = []
        all_scanned_boxes = []
        log("\n--- Starting Box Processing (Scanning unarchived boxes) ---")
        process_node(cargo, target_projects, migration_boxes, all_scanned_boxes, box_type_map,
                     parent_id="ROOT", depth=0, scope_cache=scope_cache)
        
        if not DRY_RUN:
            with open(MIGRATION_BOX_FILE, "w", encoding="utf-8") as f:
                json.dump(migration_boxes, f, indent=2, ensure_ascii=False)
                log(f"Saved migration targets to {MIGRATION_BOX_FILE}")
            
            with open(ALL_BOXES_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(all_scanned_boxes, f, indent=2, ensure_ascii=False)
                log(f"Saved all scanned boxes cache to {ALL_BOXES_CACHE_FILE}")

            export_scope_audit(all_scanned_boxes, SCOPE_AUDIT_FILE)
            log(f"Saved Box scope reference audit to {SCOPE_AUDIT_FILE}")

            # Export Excel-compatible TSV and CSV files (includes both Open and Closed target boxes)
            export_boxes_for_excel(all_scanned_boxes, TSV_EXPORT_FILE, CSV_EXPORT_FILE)
        else:
            log("DRY RUN: migration_box.json, all_boxes_cache.json, and Excel exports were not saved. Run again with dry-run 'n' to create them.")
        
        # Summary Report Creation
        summary = []
        summary.append("\n" + "="*50)
        summary.append("MIGRATION SCAN SUMMARY REPORT")
        summary.append("="*50)
        summary.append(f"Total boxes found in hierarchy: {stats['total_boxes_found']}")
        summary.append(f"  - Archived boxes: {stats['archived_boxes_count']}")
        summary.append(f"  - Closed boxes excluded from migration: {stats['closed_boxes_count']}")
        summary.append(f"  - Unarchived boxes (including Closed): {stats['unarchived_boxes_count']}")
        summary.append(f"Open boxes with scope scanned: {stats['processed']}")
        summary.append(f"Migration selection keys (projects.txt): {len(target_projects)}")
        summary.append(f"Unique Projects configured on open Boxes: {len(configured_projects)}")
        summary.append(f"  - Archived among Box-configured Projects: {sum(project_status_cache[key]['status'] == 'ARCHIVED' for key in configured_projects)}")
        summary.append(f"Matched migration target boxes: {stats['matched']}")
        summary.append(f"Invalid scope boxes filtered out: {stats['filtered_out_invalid_scope']}")
        summary.append(f"Boxes with Project/Filter/Board reference issues: {stats['scope_reference_issue_boxes']}")
        summary.append(f"Scope reference audit (includes excluded Boxes): {SCOPE_AUDIT_FILE}")
        summary.append(f"  - Empty project scope: {stats['invalid_scope_empty']}")
        summary.append(f"  - Project not found in Jira (404): {stats['invalid_scope_not_found']}")
        summary.append(f"  - All projects archived: {stats['invalid_scope_archived']}")
        summary.append(f"  - Non-project scope (Filters/Boards): {stats['non_project_scope_count']}")
        summary.append(f"JQL (Narrowing Query) Validation Summary:")
        summary.append(f"  - Boxes with JQL configured: {stats['jql_configured_count']}")
        summary.append(f"  - Valid JQL queries: {stats['jql_valid_count']}")
        summary.append(f"  - JQL queries with validation warnings: {stats['jql_warning_count']}")
        summary.append(f"  - JQL queries with 0 matched issues (excluded): {stats['jql_empty_result_count']}")
        summary.append(f"  - INVALID / Broken JQL queries: {stats['jql_invalid_count']}")
        if stats['jql_error_count'] > 0:
            summary.append(f"  - JQL check API failures: {stats['jql_error_count']}")
        summary.append(f"Processing failures: {stats['failed']}")
        summary.append(f"API request errors: {stats['api_errors']}")
        summary.append(f"Users not found (404): {stats['user_not_found']}")
        summary.append(f"User lookup failures: {stats['user_lookup_failed']}")
        summary.append(f"Box processing exceptions: {stats['box_processing_errors']}")
        summary.append(f"Box Scan Duration: {format_elapsed_time(time.time() - start_time)}")
        summary.append("-"*50)
        
        jql_error_boxes = [b for b in all_scanned_boxes if b.get("jql_status") == "INVALID"]
        if jql_error_boxes:
            summary.append("Broken / Invalid JQL Queries Detected in Boxes:")
            for idx, jb in enumerate(jql_error_boxes, 1):
                summary.append(f"  {idx}. [JQL ERROR] {jb['boxName']} (ID: {jb['boxId']})")
                summary.append(f"     JQL: {jb.get('narrowing_query')}")
                summary.append(f"     Error: {jb.get('jql_error')}")
            summary.append("-"*50)

        if excluded_invalid_boxes:
            summary.append("Filtered Out Boxes (Invalid / Inactive Scope / Empty JQL / JQL Error):")
            for idx, ex in enumerate(excluded_invalid_boxes, 1):
                reason = ex.get('filter_reason') or ex.get('reason') or '제외됨'
                keys = ', '.join(ex.get('jira_projects') or ex.get('configured_projects') or []) or ex.get('keys') or '-'
                summary.append(f"  {idx}. [{reason}] {ex.get('boxName', '')} (ID: {ex.get('boxId', '')}) - Keys: {keys}")
            summary.append("-"*50)

        if stats['matched'] > 0:
            summary.append("Matched Boxes Details & URLs (Hierarchy Tree):")
            for idx, box in enumerate(migration_boxes, 1):
                box_id = box["boxId"]
                box_name = box["boxName"]
                box_type = box["boxType"]
                projects = box["jira_projects"]
                depth = box.get("depth", 0)
                parent_id = box.get("parentBoxId", "ROOT")
                prefix = box.get("hierarchy_prefix")
                if prefix is None and depth > 0:
                    prefix = ("│  " * (depth - 1)) + "│  ⎿  "
                prefix_str = prefix or ""
                
                # Construct URLs
                gantt_url = f"{JIRA_BASE_URL}/secure/GanttWebConsole.jspa?boxId={box_id}"
                bp_url = f"{JIRA_BASE_URL}/secure/BigPicture.jspa?boxId={box_id}#box/{box_id}"
                
                parent_info = f" (Parent ID: {parent_id})" if parent_id != "ROOT" else " (Root Box)"
                summary.append(f"\n{idx}. {prefix_str}[{box_type}] {box_name} (ID: {box_id}){parent_info}")
                summary.append(f"   Jira Projects: {', '.join(projects)}")
                summary.append(f"   BigGantt URL : {gantt_url}")
                summary.append(f"   BigPicture URL: {bp_url}")
        else:
            summary.append("No active target boxes matched the configured projects.")
            
        summary.append("="*50)
        
        # Print and write summary
        for line in summary:
            log(line)

    except Exception as e:
        stats["box_processing_errors"] += 1
        record_issue("UNEXPECTED ERROR", str(e))
        log(f"Exception during hierarchy processing: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        report_runtime_issues()
        try:
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            from generate_step1_report import generate_report
            html_path = generate_report()
            log(f"\n[HTML Report] Generated verification report at: {html_path}")
            log("   💡 브라우저에서 'step1_box_report.html'을 열어 대상을 체크(또는 프로젝트 키 일괄 선택)한 후")
            log("      '선택 Box CSV 내보내기'로 'migration_boxes.csv'를 다운로드하여 Step 2에서 사용할 수 있습니다.")
            if not DRY_RUN:
                log(f"[Excel Export] Generated TSV for copy/paste at: {TSV_EXPORT_FILE}")
                log(f"[Excel Export] Generated CSV for Excel at: {CSV_EXPORT_FILE}")
        except Exception as e:
            log(f"Failed to generate HTML report: {e}")
            import traceback
            log(traceback.format_exc())

        # Overall execution timing
        end_datetime = datetime.now()
        total_elapsed_sec = time.time() - start_time
        total_duration_str = format_elapsed_time(total_elapsed_sec)
        log("\n" + "="*50)
        log(f"⏱️  EXECUTION COMPLETED: {get_version_label()}")
        log(f"   Start Time     : {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"   End Time       : {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"   Total Duration : {total_duration_str}")
        log("="*50 + "\n")

if __name__ == "__main__":
    main()
