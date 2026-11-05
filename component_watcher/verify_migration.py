import os
import sys

# Windows 환경에서 Unix 전용 pwd 모듈 임포트 에러 방지를 위한 Mocking
if sys.platform == "win32":
    import types
    class DummyPasswd:
        pw_name = os.getlogin() if hasattr(os, "getlogin") else "windows_user"
        pw_dir = os.path.expanduser("~")
        pw_uid = 0
        pw_gid = 0
        pw_gecos = "Windows User"
        pw_shell = ""
    
    pwd = types.ModuleType("pwd")
    pwd.getpwuid = lambda uid: DummyPasswd()
    pwd.getpwnam = lambda name: DummyPasswd()
    sys.modules["pwd"] = pwd

import requests
import json
import csv
import argparse
import time
from datetime import datetime
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.auth import HTTPBasicAuth

# env 파일 로드
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.abspath(os.path.join(script_dir, '..', 'env'))
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
else:
    fallback_path = os.path.join(script_dir, 'env')
    if os.path.exists(fallback_path):
        load_dotenv(dotenv_path=fallback_path)

# 실행 환경 선택
def select_environment():
    print("\nSelect Environment for Verification:")
    print("1. STG (Staging / Sandbox)")
    print("2. OPS (Operations)")
    while True:
        try:
            choice = input("Enter choice (1/2): ").strip()
            if choice == '1':
                return "STG"
            elif choice == '2':
                return "OPS"
            else:
                print("Invalid choice. Please enter 1 or 2.")
        except (KeyboardInterrupt, EOFError):
            print("\nOperation cancelled.")
            sys.exit(0)

ENV = select_environment()

if ENV == "STG":
    DC_BASE_URL = os.getenv("JIRA_DC_STG_URL")
    DC_USER = os.getenv("JIRA_DC_STG_USER") or ""
    DC_TOKEN = os.getenv("JIRA_DC_STG_API_TOKEN") or os.getenv("JIRA_DC_STG_TOKEN")
    
    cloud_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_STG_URL") or os.getenv("JIRA_CLOUD_URL") or ""
    CLOUD_USER = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_STG_USER") or os.getenv("JIRA_CLOUD_USER") or ""
    CLOUD_TOKEN = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN") or os.getenv("JIRA_CLOUD_STG_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")
    CLOUD_API_URL = cloud_url.rstrip("/") + "/rest/api/3"
    VERIFY_SSL = False if os.getenv("VERIFY_SSL_STG", "True").lower() == "false" else True
else:
    DC_BASE_URL = os.getenv("JIRA_DC_OPS_URL")
    DC_USER = os.getenv("JIRA_DC_OPS_USER") or ""
    DC_TOKEN = os.getenv("JIRA_DC_OPS_API_TOKEN") or os.getenv("JIRA_DC_OPS_TOKEN")
    
    cloud_url = os.getenv("JIRA_CLOUD_OPS_URL") or os.getenv("JIRA_CLOUD_URL") or ""
    CLOUD_USER = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_OPS_USER") or os.getenv("JIRA_CLOUD_USER") or ""
    CLOUD_TOKEN = os.getenv("JIRA_CLOUD_OPS_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")
    CLOUD_API_URL = cloud_url.rstrip("/") + "/rest/api/3"
    VERIFY_SSL = False if os.getenv("VERIFY_SSL_OPS", "True").lower() == "false" else True

if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# DC & Cloud Auth / Headers
DC_HEADERS = {
    "Authorization": f"Bearer {DC_TOKEN}" if DC_TOKEN else "",
    "Content-Type": "application/json"
}

# Cloud basic authentication (email + token)
CLOUD_AUTH = HTTPBasicAuth(CLOUD_USER, CLOUD_TOKEN) if CLOUD_USER and CLOUD_TOKEN else None
CLOUD_HEADERS = {
    "Content-Type": "application/json"
}

# Event ID to String Mapping
EVENT_MAPPING = {
    1: "avi:jira:created:issue",
    2: "avi:jira:updated:issue",
    3: "avi:jira:assigned:issue",
    4: "avi:jira:updated:resolution",
    5: "avi:jira:updated:status",
    6: "avi:jira:commented:issue",
    10: "avi:jira:created:worklog",
    15: "avi:jira:updated:worklog",
    16: "avi:jira:deleted:worklog"
}

# Caching for DC user emails
email_cache = {}

def get_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

session = get_session()

def get_user_email(username):
    if not username:
        return None
    if username in email_cache:
        return email_cache[username]
    
    url = f"{DC_BASE_URL}/rest/api/2/user?username={username}"
    try:
        response = session.get(url, headers=DC_HEADERS, timeout=10, verify=VERIFY_SSL)
        response.raise_for_status()
        user_data = response.json()
        email = user_data.get("emailAddress")
        email_cache[username] = email
        return email
    except Exception:
        return None

def fetch_dc_config(project_key):
    url = f"{DC_BASE_URL}/rest/componentwatcher/1.0/config/configurations/{project_key}"
    try:
        response = session.get(url, headers=DC_HEADERS, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 404:
            return None, "Not Found (404)"
        response.raise_for_status()
        data = response.json()
        return data.get("data", []), None
    except Exception as e:
        return None, str(e)

def fetch_cloud_config(project_key):
    # Endpoint 1: App standard REST resource path (similar to DC)
    url1 = f"{cloud_url.rstrip('/')}/rest/componentwatcher/1.0/config/configurations/{project_key}"
    try:
        response = session.get(url1, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"], "App API (rest/componentwatcher)"
            elif isinstance(data, list):
                return data, "App API (rest/componentwatcher)"
    except Exception:
        pass

    # Endpoint 2: The custom CLOUD_API_URL path
    url2 = f"{CLOUD_API_URL.rstrip('/')}/configurations/{project_key}"
    try:
        response = session.get(url2, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"], "Custom API (/configurations/key)"
            elif isinstance(data, list):
                return data, "Custom API (/configurations/key)"
    except Exception:
        pass

    url2_query = f"{CLOUD_API_URL}"
    try:
        response = session.get(url2_query, params={"projectKey": project_key}, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"], "Custom API (?projectKey=key)"
            elif isinstance(data, list):
                return data, "Custom API (?projectKey=key)"
    except Exception:
        pass

    # Endpoint 3: Project properties (Jira standard Entity Properties)
    url3 = f"{cloud_url.rstrip('/')}/rest/api/3/project/{project_key}/properties"
    try:
        response = session.get(url3, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 200:
            properties = response.json().get("properties", [])
            for prop in properties:
                prop_key = prop.get("key", "")
                if "componentwatcher" in prop_key.lower() or "component_watcher" in prop_key.lower() or "watcher" in prop_key.lower():
                    prop_val_url = prop.get("self")
                    prop_resp = session.get(prop_val_url, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
                    if prop_resp.status_code == 200:
                        val_data = prop_resp.json().get("value")
                        if isinstance(val_data, list):
                            return val_data, f"Project Property ({prop_key})"
                        elif isinstance(val_data, dict):
                            if "data" in val_data:
                                return val_data["data"], f"Project Property ({prop_key})"
                            return [val_data], f"Project Property ({prop_key})"
    except Exception:
        pass

    # Endpoint 4: Component properties (if stored per component)
    url4 = f"{cloud_url.rstrip('/')}/rest/api/3/project/{project_key}/components"
    try:
        response = session.get(url4, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
        if response.status_code == 200:
            components = response.json()
            cloud_configs = []
            for comp in components:
                comp_id = comp.get("id")
                comp_name = comp.get("name")
                prop_list_url = f"{cloud_url.rstrip('/')}/rest/api/3/component/{comp_id}/properties"
                prop_list_resp = session.get(prop_list_url, headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
                if prop_list_resp.status_code == 200:
                    props = prop_list_resp.json().get("properties", [])
                    for p in props:
                        p_key = p.get("key", "")
                        if "watcher" in p_key.lower() or "componentwatcher" in p_key.lower():
                            p_val_resp = session.get(p.get("self"), headers=CLOUD_HEADERS, auth=CLOUD_AUTH, timeout=10, verify=VERIFY_SSL)
                            if p_val_resp.status_code == 200:
                                val = p_val_resp.json().get("value", {})
                                cloud_configs.append({
                                    "componentName": comp_name,
                                    "users": val.get("users", []),
                                    "groups": val.get("groups", []),
                                    "events": val.get("events", [])
                                })
            if cloud_configs:
                return cloud_configs, "Component Properties"
    except Exception:
        pass

    return None, "Not Found / Error"

def normalize_dc_config(config):
    comp_name = config.get("componentName")
    users_obj = config.get("authorityDTO", {}).get("usersObj", [])
    raw_groups = config.get("authorityDTO", {}).get("groups", [])
    event_ids = config.get("eventTypes", [])
    
    # Map users to emails
    users = []
    for u in users_obj:
        username = u.get("name")
        email = get_user_email(username)
        if email:
            users.append(email)
            
    # Normalize groups
    groups = []
    for g in raw_groups:
        if isinstance(g, dict) and "name" in g:
            groups.append(g["name"])
        elif isinstance(g, str):
            groups.append(g)
            
    # Map event IDs to strings
    events = []
    for eid in event_ids:
        if eid in EVENT_MAPPING:
            events.append(EVENT_MAPPING[eid])
            
    users.sort()
    groups.sort()
    events.sort()
    
    return {
        "componentName": comp_name,
        "users": users,
        "groups": groups,
        "events": events
    }

def normalize_cloud_config(config):
    comp_name = config.get("componentName") or config.get("name")
    
    # Normalize users/emails
    raw_users = config.get("users") or config.get("usersObj") or []
    users = []
    for u in raw_users:
        if isinstance(u, dict):
            email = u.get("emailAddress") or u.get("email") or u.get("name")
            if email:
                users.append(email)
        elif isinstance(u, str):
            users.append(u)
            
    # Normalize groups
    raw_groups = config.get("groups") or []
    groups = []
    for g in raw_groups:
        if isinstance(g, dict):
            name = g.get("name")
            if name:
                groups.append(name)
        elif isinstance(g, str):
            groups.append(g)
            
    # Normalize events
    raw_events = config.get("events") or config.get("eventTypes") or []
    events = []
    for e in raw_events:
        if isinstance(e, int):
            if e in EVENT_MAPPING:
                events.append(EVENT_MAPPING[e])
        elif isinstance(e, str):
            events.append(e)
            
    users.sort()
    groups.sort()
    events.sort()
    
    return {
        "componentName": comp_name,
        "users": users,
        "groups": groups,
        "events": events
    }

def main():
    parser = argparse.ArgumentParser(description="Jira Component Watcher Migration Verifier")
    parser.add_argument("--file", help="Path to projects file")
    parser.add_argument("--output", help="Path to output CSV report")
    args = parser.parse_args()

    # 경로 입력 단계 제거: 인자로 전달되지 않으면 고정된 기본값 직접 사용
    project_file = args.file if args.file else os.path.abspath(os.path.join(script_dir, "..", "projects.txt"))
    output_file = args.output if args.output else os.path.abspath(os.path.join(script_dir, "report.csv"))

    if not os.path.exists(project_file):
        # Fallback to ../jeditor/projects.txt as a secondary default if ../projects.txt is missing
        alt_path = os.path.abspath(os.path.join(script_dir, '..', 'jeditor', 'projects.txt'))
        default_path = os.path.abspath(os.path.join(script_dir, "..", "projects.txt"))
        if os.path.exists(alt_path) and project_file == default_path:
            project_file = alt_path
            print(f"Default projects.txt not found, using fallback: {project_file}")
        else:
            print(f"Error: Project file '{project_file}' not found.")
            sys.exit(1)

    with open(project_file, "r", encoding="utf-8") as f:
        projects = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not projects:
        print("No projects to process.")
        return

    csv_rows = []
    summary_stats = {
        "total_projects": len(projects),
        "total_components": 0,
        "match": 0,
        "mismatch": 0,
        "missing_in_cloud": 0,
        "missing_in_dc": 0,
        "dc_error": 0,
        "cloud_error": 0
    }

    print(f"\nStarting verification for {len(projects)} projects...")
    print(f"Saving comparison results to {output_file}")

    for idx, project_key in enumerate(projects, 1):
        print(f"\n[{idx}/{len(projects)}] Processing project: {project_key} ...")
        
        # 1. Fetch DC Configurations
        dc_raw, dc_error = fetch_dc_config(project_key)
        if dc_error:
            print(f"  [Error] Failed to fetch Jira DC configuration: {dc_error}")
            csv_rows.append({
                "Project Key": project_key,
                "Component Name": "N/A",
                "Status": "DC_FETCH_ERROR",
                "DC Watchers (Emails)": "",
                "Cloud Watchers (Emails)": "",
                "Watchers Match": "False",
                "DC Groups": "",
                "Cloud Groups": "",
                "Groups Match": "False",
                "DC Events": "",
                "Cloud Events": "",
                "Events Match": "False",
                "Details": f"Jira DC retrieval failed: {dc_error}"
            })
            summary_stats["dc_error"] += 1
            continue

        # 2. Fetch Cloud Configurations
        cloud_raw, fetch_method = fetch_cloud_config(project_key)
        
        # Normalize DC Configurations
        dc_configs = {}
        for c in dc_raw:
            norm = normalize_dc_config(c)
            name = norm["componentName"]
            if name:
                dc_configs[name] = norm

        # Normalize Cloud Configurations
        cloud_configs = {}
        if cloud_raw:
            print(f"  [Success] Retrieved Cloud configuration using: {fetch_method}")
            for c in cloud_raw:
                norm = normalize_cloud_config(c)
                name = norm["componentName"]
                if name:
                    cloud_configs[name] = norm
        else:
            print(f"  [Warning] No configuration found in Jira Cloud (or Cloud fetch failed).")
            cloud_configs = {}

        # Gather all component names from both sides
        all_components = set(dc_configs.keys()).union(set(cloud_configs.keys()))
        summary_stats["total_components"] += len(all_components)
        
        proj_matches = 0
        proj_mismatches = 0
        proj_missing_cloud = 0
        proj_missing_dc = 0

        for comp_name in sorted(all_components):
            dc_c = dc_configs.get(comp_name)
            cloud_c = cloud_configs.get(comp_name)

            row = {
                "Project Key": project_key,
                "Component Name": comp_name,
                "Status": "",
                "DC Watchers (Emails)": "",
                "Cloud Watchers (Emails)": "",
                "Watchers Match": "False",
                "DC Groups": "",
                "Cloud Groups": "",
                "Groups Match": "False",
                "DC Events": "",
                "Cloud Events": "",
                "Events Match": "False",
                "Details": ""
            }

            if dc_c and not cloud_c:
                row["Status"] = "MISSING_IN_CLOUD"
                row["DC Watchers (Emails)"] = ", ".join(dc_c["users"])
                row["DC Groups"] = ", ".join(dc_c["groups"])
                row["DC Events"] = ", ".join(dc_c["events"])
                row["Details"] = "Component is configured in JIRA DC but missing in Jira Cloud"
                proj_missing_cloud += 1
                summary_stats["missing_in_cloud"] += 1
                
            elif cloud_c and not dc_c:
                row["Status"] = "MISSING_IN_DC"
                row["Cloud Watchers (Emails)"] = ", ".join(cloud_c["users"])
                row["Cloud Groups"] = ", ".join(cloud_c["groups"])
                row["Cloud Events"] = ", ".join(cloud_c["events"])
                row["Details"] = "Component is configured in Jira Cloud but missing in JIRA DC"
                proj_missing_dc += 1
                summary_stats["missing_in_dc"] += 1
                
            else:
                row["DC Watchers (Emails)"] = ", ".join(dc_c["users"])
                row["Cloud Watchers (Emails)"] = ", ".join(cloud_c["users"])
                row["DC Groups"] = ", ".join(dc_c["groups"])
                row["Cloud Groups"] = ", ".join(cloud_c["groups"])
                row["DC Events"] = ", ".join(dc_c["events"])
                row["Cloud Events"] = ", ".join(cloud_c["events"])

                watchers_match = dc_c["users"] == cloud_c["users"]
                groups_match = dc_c["groups"] == cloud_c["groups"]
                events_match = dc_c["events"] == cloud_c["events"]

                row["Watchers Match"] = str(watchers_match)
                row["Groups Match"] = str(groups_match)
                row["Events Match"] = str(events_match)

                if watchers_match and groups_match and events_match:
                    row["Status"] = "MATCH"
                    row["Details"] = "Configuration verified and matched perfectly"
                    proj_matches += 1
                    summary_stats["match"] += 1
                else:
                    row["Status"] = "MISMATCH"
                    mismatches = []
                    if not watchers_match:
                        mismatches.append(f"Watchers mismatch (DC count: {len(dc_c['users'])}, Cloud count: {len(cloud_c['users'])})")
                    if not groups_match:
                        mismatches.append(f"Groups mismatch (DC: {dc_c['groups']} vs Cloud: {cloud_c['groups']})")
                    if not events_match:
                        mismatches.append(f"Events mismatch (DC: {dc_c['events']} vs Cloud: {cloud_c['events']})")
                    row["Details"] = " | ".join(mismatches)
                    proj_mismatches += 1
                    summary_stats["mismatch"] += 1

            csv_rows.append(row)
            
        print(f"  > Summary: Components: {len(all_components)} | MATCH: {proj_matches} | MISMATCH: {proj_mismatches} | MISSING_CLOUD: {proj_missing_cloud} | MISSING_DC: {proj_missing_dc}")

    # Write report to CSV
    csv_headers = [
        "Project Key", "Component Name", "Status", 
        "DC Watchers (Emails)", "Cloud Watchers (Emails)", "Watchers Match",
        "DC Groups", "Cloud Groups", "Groups Match", 
        "DC Events", "Cloud Events", "Events Match", 
        "Details"
    ]
    
    with open(output_file, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
        writer.writeheader()
        writer.writerows(csv_rows)

    print("\n" + "="*60)
    print("                     VERIFICATION SUMMARY")
    print("="*60)
    print(f"Total Projects Processed : {summary_stats['total_projects']}")
    print(f"Total Components Scanned  : {summary_stats['total_components']}")
    print(f"  - MATCH                : {summary_stats['match']}")
    print(f"  - MISMATCH             : {summary_stats['mismatch']}")
    print(f"  - MISSING_IN_CLOUD     : {summary_stats['missing_in_cloud']}")
    print(f"  - MISSING_IN_DC        : {summary_stats['missing_in_dc']}")
    print(f"  - DC RETRIEVAL ERRORS  : {summary_stats['dc_error']}")
    print("="*60)
    print(f"Detailed comparison report saved to: {output_file}")
    print("Verification completed successfully.")

if __name__ == "__main__":
    main()
