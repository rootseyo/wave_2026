#!/usr/bin/env python3
"""
Jira Migration Reporting Generator (Jira DC -> Jira Cloud)
Generates an interactive HTML dashboard reporting migration status,
issue count comparisons, issue type breakdowns, and custom field mapping details.
Fixes Jira Cloud API v3 endpoint change (/rest/api/3/search/jql) and maps custom fields by name reuse.
"""

import os
import sys
import json
import argparse
import time
import urllib3
import requests
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

def log(msg):
    print(f"[*] {msg}")

def load_environment():
    possible_paths = ['env', '../env']
    loaded = False
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            log(f"Loaded environment variables from: {path}")
            loaded = True
            break
    if not loaded:
        log("Warning: 'env' file not found. Falling back to system environment variables.")

def get_projects(file_path, limit=None):
    if not os.path.exists(file_path):
        log(f"Error: Project list file '{file_path}' not found.")
        sys.exit(1)
        
    projects = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                projects.append(line)
    if limit and limit > 0:
        log(f"Limiting execution to first {limit} projects from file.")
        projects = projects[:limit]
    return projects

class JiraClient:
    def __init__(self, env_type, session, base_url, auth=None, bearer_token=None, is_dc=False):
        self.env_type = env_type
        self.session = session
        self.base_url = base_url.rstrip('/')
        self.auth = auth
        self.bearer_token = bearer_token
        self.is_dc = is_dc
        self.api_ver = "2" if is_dc else "3"

    def _get_headers(self):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.is_dc and self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def get(self, endpoint, params=None, timeout=15):
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        try:
            res = self.session.get(url, headers=headers, auth=self.auth if not self.is_dc else None, params=params, timeout=timeout)
            return res
        except Exception as e:
            log(f"API Error ({self.env_type} GET {endpoint}): {e}")
            return None

    def get_all_fields(self):
        """Fetch all field definitions."""
        res = self.get(f"/rest/api/{self.api_ver}/field")
        if res and res.status_code == 200:
            fields = res.json()
            field_map = {}
            for f in fields:
                field_map[f["id"]] = {
                    "id": f["id"],
                    "name": f.get("name", f["id"]).strip(),
                    "custom": f.get("custom", False),
                    "schema": f.get("schema", {})
                }
            return field_map
        return {}

    def get_project_details(self, project_key):
        """Fetch project details and defined issue types."""
        res = self.get(f"/rest/api/{self.api_ver}/project/{project_key}")
        if res and res.status_code == 200:
            data = res.json()
            issue_types = [{"id": it["id"], "name": it["name"]} for it in data.get("issueTypes", [])]
            return {
                "exists": True,
                "name": data.get("name", project_key),
                "key": project_key,
                "projectCategory": data.get("projectCategory", {}).get("name", "N/A"),
                "issueTypes": issue_types
            }
        elif res and res.status_code == 404:
            return {"exists": False, "key": project_key, "name": "Not Found", "issueTypes": []}
        return {"exists": False, "key": project_key, "name": f"Error (HTTP {res.status_code if res else 'Conn'})", "issueTypes": []}

    def get_issue_count(self, project_key, issuetype_id=None):
        """Get issue count via JQL search. Handles DC /rest/api/2/search and Jira Cloud /rest/api/3/search/jql."""
        if issuetype_id:
            jql = f'project="{project_key}" AND issuetype="{issuetype_id}"'
        else:
            jql = f'project="{project_key}"'

        if self.is_dc:
            endpoint = "/rest/api/2/search"
            res = self.get(endpoint, params={"jql": jql, "maxResults": 0})
            if res and res.status_code == 200:
                return res.json().get("total", 0)
            return 0
        else:
            # Jira Cloud uses /rest/api/3/search/jql
            endpoint = "/rest/api/3/search/jql"
            total_count = 0
            next_page_token = None
            while True:
                params = {"jql": jql, "maxResults": 5000}
                if next_page_token:
                    params["nextPageToken"] = next_page_token
                res = self.get(endpoint, params=params)
                if not res or res.status_code != 200:
                    break
                data = res.json()
                issues = data.get("issues", [])
                total_count += len(issues)
                if data.get("isLast", True) or not issues:
                    break
                next_page_token = data.get("nextPageToken")
            return total_count

    def get_project_custom_fields(self, project_key, issue_types=None):
        """Fetch custom fields active in createmeta (screens) or sample issues (data) for a project."""
        screen_cfs = set()
        data_cfs = set()

        if self.is_dc:
            endpoint = "/rest/api/2/issue/createmeta"
            params = {"projectKeys": project_key, "expand": "projects.issuetypes.fields"}
            res = self.get(endpoint, params=params)
            if res and res.status_code == 200:
                data = res.json()
                projects = data.get("projects", [])
                if projects:
                    for it in projects[0].get("issuetypes", []):
                        fields = it.get("fields", {})
                        for field_id in fields.keys():
                            if field_id.startswith("customfield_"):
                                screen_cfs.add(field_id)
        else:
            # Jira Cloud v3 createmeta requires querying per issue type
            if issue_types:
                for it in issue_types:
                    it_id = it.get("id")
                    if it_id:
                        res = self.get(f"/rest/api/3/issue/createmeta/{project_key}/issuetypes/{it_id}")
                        if res and res.status_code == 200:
                            fields = res.json().get("fields", [])
                            for f in fields:
                                fid = f.get("fieldId", "")
                                if fid.startswith("customfield_"):
                                    screen_cfs.add(fid)
        
        # Augment with issue search to catch populated fields
        if self.is_dc:
            search_res = self.get("/rest/api/2/search", params={"jql": f'project="{project_key}"', "maxResults": 50})
        else:
            search_res = self.get("/rest/api/3/search/jql", params={"jql": f'project="{project_key}"', "maxResults": 50})
            
        if search_res and search_res.status_code == 200:
            issues = search_res.json().get("issues", [])
            for issue in issues:
                for k, v in issue.get("fields", {}).items():
                    if k.startswith("customfield_") and v is not None:
                        data_cfs.add(k)
                        
        all_cfs = sorted(list(screen_cfs.union(data_cfs)))
        field_details = []
        for cf_id in all_cfs:
            on_screen = cf_id in screen_cfs
            has_data = cf_id in data_cfs
            if on_screen and has_data:
                usage = "Screen & Data"
            elif on_screen:
                usage = "Screen Only"
            else:
                usage = "Data Only"

            field_details.append({
                "id": cf_id,
                "on_screen": on_screen,
                "has_data": has_data,
                "usage": usage
            })

        return field_details

def fetch_project_data(dc_client, cloud_client, project_key):
    """Fetch complete metrics for a single project from DC and Cloud."""
    dc_info = dc_client.get_project_details(project_key)
    cloud_info = cloud_client.get_project_details(project_key)

    dc_total_issues = 0
    cloud_total_issues = 0
    dc_issue_types_counts = {}
    cloud_issue_types_counts = {}

    if dc_info["exists"]:
        dc_total_issues = dc_client.get_issue_count(project_key)
        for it in dc_info["issueTypes"]:
            count = dc_client.get_issue_count(project_key, issuetype_id=it["id"])
            dc_issue_types_counts[it["name"]] = count

    if cloud_info["exists"]:
        cloud_total_issues = cloud_client.get_issue_count(project_key)
        for it in cloud_info["issueTypes"]:
            count = cloud_client.get_issue_count(project_key, issuetype_id=it["id"])
            cloud_issue_types_counts[it["name"]] = count

    dc_custom_fields = dc_client.get_project_custom_fields(project_key, dc_info.get("issueTypes")) if dc_info["exists"] else []
    cloud_custom_fields = cloud_client.get_project_custom_fields(project_key, cloud_info.get("issueTypes")) if cloud_info["exists"] else []

    # Migration status determination
    if not dc_info["exists"]:
        status = "Error (DC Missing)"
        status_class = "danger"
    elif not cloud_info["exists"]:
        status = "Not Migrated"
        status_class = "secondary"
    elif dc_total_issues == cloud_total_issues:
        status = "Completed"
        status_class = "success"
    elif cloud_total_issues > 0:
        status = "In Progress / Difference"
        status_class = "warning"
    else:
        status = "Project Created (0 Issues)"
        status_class = "info"

    return {
        "key": project_key,
        "name": dc_info.get("name") if dc_info["exists"] else cloud_info.get("name", project_key),
        "status": status,
        "status_class": status_class,
        "dc_exists": dc_info["exists"],
        "cloud_exists": cloud_info["exists"],
        "dc_issues": dc_total_issues,
        "cloud_issues": cloud_total_issues,
        "issue_diff": cloud_total_issues - dc_total_issues,
        "dc_issue_types": dc_issue_types_counts,
        "cloud_issue_types": cloud_issue_types_counts,
        "dc_cf_count": len(dc_custom_fields),
        "cloud_cf_count": len(cloud_custom_fields),
        "dc_custom_fields": dc_custom_fields,
        "cloud_custom_fields": cloud_custom_fields
    }

def build_custom_field_mapping(dc_fields, cloud_fields):
    """
    Build name-based custom field reuse mapping between DC and Cloud.
    Maps fields with identical names regardless of whether customfield_XXXXX IDs match.
    """
    # Group DC fields by normalized name
    dc_by_norm_name = {}
    for cf_id, cf in dc_fields.items():
        if cf_id.startswith("customfield_"):
            norm_name = cf["name"].strip().lower()
            dc_by_norm_name[norm_name] = cf

    # Group Cloud fields by normalized name
    cloud_by_norm_name = {}
    for cf_id, cf in cloud_fields.items():
        if cf_id.startswith("customfield_"):
            norm_name = cf["name"].strip().lower()
            cloud_by_norm_name[norm_name] = cf

    mapping_list = []
    processed_cloud_names = set()

    # Process all DC custom fields
    for norm_name, dc_cf in sorted(dc_by_norm_name.items(), key=lambda x: x[1]["name"].lower()):
        field_name = dc_cf["name"]
        dc_id = dc_cf["id"]

        if norm_name in cloud_by_norm_name:
            cloud_cf = cloud_by_norm_name[norm_name]
            cloud_id = cloud_cf["id"]
            processed_cloud_names.add(norm_name)

            if dc_id == cloud_id:
                match_type = "Identical ID"
            else:
                match_type = "Reused (Name Match, ID Differs)"

            mapping_list.append({
                "name": field_name,
                "dc_id": dc_id,
                "cloud_id": cloud_id,
                "status": "Mapped",
                "match_type": match_type
            })
        else:
            mapping_list.append({
                "name": field_name,
                "dc_id": dc_id,
                "cloud_id": "N/A",
                "status": "Unmapped (DC Only)",
                "match_type": "None"
            })

    # Process remaining Cloud-only custom fields
    for norm_name, cloud_cf in sorted(cloud_by_norm_name.items(), key=lambda x: x[1]["name"].lower()):
        if norm_name not in processed_cloud_names:
            mapping_list.append({
                "name": cloud_cf["name"],
                "dc_id": "N/A",
                "cloud_id": cloud_cf["id"],
                "status": "Cloud Only",
                "match_type": "None"
            })

    return mapping_list

def generate_html_report(project_results, dc_fields, cloud_fields, source_name, target_name, output_filepath):
    """Generate standalone modern HTML report dashboard."""
    
    # 1. Calculate Summary KPIs
    total_projects = len(project_results)
    completed_projects = sum(1 for p in project_results if p["status"] == "Completed")
    in_progress_projects = sum(1 for p in project_results if p["status_class"] == "warning")
    not_migrated_projects = sum(1 for p in project_results if p["status"] == "Not Migrated")
    error_projects = sum(1 for p in project_results if p["status_class"] in ("danger", "info"))
    
    total_dc_issues = sum(p["dc_issues"] for p in project_results)
    total_cloud_issues = sum(p["cloud_issues"] for p in project_results)
    migration_progress_pct = round((total_cloud_issues / total_dc_issues * 100), 1) if total_dc_issues > 0 else (100.0 if total_cloud_issues > 0 else 0)

    # 2. Build Custom Fields Mapping Matrix
    cf_mapping_matrix = build_custom_field_mapping(dc_fields, cloud_fields)

    # Collect custom fields actively used in scanned projects
    active_dc_cf_ids = set()
    active_cloud_cf_ids = set()
    for p in project_results:
        active_dc_cf_ids.update([item["id"] for item in p.get("dc_custom_fields", [])])
        active_cloud_cf_ids.update([item["id"] for item in p.get("cloud_custom_fields", [])])

    for item in cf_mapping_matrix:
        item["used_in_projects"] = (item["dc_id"] in active_dc_cf_ids) or (item["cloud_id"] in active_cloud_cf_ids)

    active_cf_count = sum(1 for item in cf_mapping_matrix if item["used_in_projects"])
    mapped_cf_count = sum(1 for item in cf_mapping_matrix if item["used_in_projects"] and item["status"] == "Mapped")
    total_dc_cfs = sum(1 for item in cf_mapping_matrix if item["used_in_projects"] and item["dc_id"] != "N/A")
    cf_mapped_pct = round((mapped_cf_count / total_dc_cfs * 100), 1) if total_dc_cfs > 0 else 100.0

    report_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    # Fast lookup dictionary for DC Custom Field ID -> Cloud Custom Field ID & Name
    dc_id_to_cloud_map = {}
    for item in cf_mapping_matrix:
        if item["dc_id"] != "N/A":
            dc_id_to_cloud_map[item["dc_id"]] = item

    # Enrich Project Results with detailed Custom Field mapping labels and screen usage
    for p in project_results:
        enriched_dc_cfs = []
        for cf_info in p["dc_custom_fields"]:
            dc_cf_id = cf_info["id"]
            dc_name = dc_fields.get(dc_cf_id, {}).get("name", dc_cf_id)
            map_info = dc_id_to_cloud_map.get(dc_cf_id)
            if map_info and map_info["status"] == "Mapped":
                cloud_cf_id = map_info["cloud_id"]
                id_label = f"{dc_cf_id} ➔ {cloud_cf_id}"
            else:
                cloud_cf_id = None
                id_label = dc_cf_id

            usage = cf_info["usage"]
            if usage == "Screen & Data":
                usage_badge = '<span class="badge success" style="font-size:9px; padding:1px 4px; margin-left:4px;">Screen &amp; Data</span>'
            elif usage == "Screen Only":
                usage_badge = '<span class="badge info" style="font-size:9px; padding:1px 4px; margin-left:4px;">Screen Only</span>'
            else:
                usage_badge = '<span class="badge secondary" style="font-size:9px; padding:1px 4px; margin-left:4px;">Data Only</span>'

            enriched_dc_cfs.append({
                "id": dc_cf_id,
                "name": dc_name,
                "cloud_id": cloud_cf_id,
                "id_label": id_label,
                "usage": usage,
                "usage_badge": usage_badge
            })
        p["enriched_dc_custom_fields"] = enriched_dc_cfs

        enriched_cloud_cfs = []
        for cf_info in p["cloud_custom_fields"]:
            cloud_cf_id = cf_info["id"]
            cloud_name = cloud_fields.get(cloud_cf_id, {}).get("name", cloud_cf_id)
            usage = cf_info["usage"]
            if usage == "Screen & Data":
                usage_badge = '<span class="badge success" style="font-size:9px; padding:1px 4px; margin-left:4px;">Screen &amp; Data</span>'
            elif usage == "Screen Only":
                usage_badge = '<span class="badge info" style="font-size:9px; padding:1px 4px; margin-left:4px;">Screen Only</span>'
            else:
                usage_badge = '<span class="badge secondary" style="font-size:9px; padding:1px 4px; margin-left:4px;">Data Only</span>'

            enriched_cloud_cfs.append({
                "id": cloud_cf_id,
                "name": cloud_name,
                "id_label": cloud_cf_id,
                "usage": usage,
                "usage_badge": usage_badge
            })
        p["enriched_cloud_custom_fields"] = enriched_cloud_cfs

    # Serialize JSON data for embedded JavaScript search/filter
    projects_json = json.dumps(project_results, ensure_ascii=False)
    cf_matrix_json = json.dumps(cf_mapping_matrix, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jira Data Center to Cloud Migration Report</title>
    <style>
        :root {{
            --bg-primary: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-indigo: #818cf8;
            --border-color: #334155;
            --success-bg: #064e3b;
            --success-fg: #34d399;
            --warning-bg: #78350f;
            --warning-fg: #fbbf24;
            --danger-bg: #7f1d1d;
            --danger-fg: #f87171;
            --info-bg: #1e3a8a;
            --info-fg: #60a5fa;
            --secondary-bg: #334155;
            --secondary-fg: #cbd5e1;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }}

        body {{
            background-color: var(--bg-primary);
            color: var(--text-main);
            padding: 24px;
            line-height: 1.5;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        /* Header */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 32px;
        }}

        .header-title h1 {{
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-indigo));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 4px;
        }}

        .header-title p {{
            color: var(--text-muted);
            font-size: 14px;
        }}

        .header-meta {{
            text-align: right;
            font-size: 13px;
            color: var(--text-muted);
        }}

        .badge-env {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 600;
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent-blue);
            margin-left: 6px;
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }}

        .kpi-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}

        .kpi-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        }}

        .kpi-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }}

        .kpi-value {{
            font-size: 28px;
            font-weight: 800;
            color: var(--text-main);
        }}

        .kpi-subtext {{
            font-size: 12px;
            margin-top: 6px;
            color: var(--text-muted);
        }}

        /* Progress Bar */
        .progress-container {{
            margin-top: 8px;
            background: var(--border-color);
            border-radius: 999px;
            height: 8px;
            overflow: hidden;
        }}

        .progress-bar {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-indigo));
            border-radius: 999px;
            transition: width 0.6s ease;
        }}

        /* Tabs & Controls */
        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 16px;
        }}

        .tabs {{
            display: flex;
            gap: 8px;
            background: var(--bg-card);
            padding: 4px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        .tab-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .tab-btn.active {{
            background: var(--border-color);
            color: var(--text-main);
        }}

        .search-box input {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 8px 16px;
            color: var(--text-main);
            font-size: 14px;
            outline: none;
            width: 320px;
        }}

        .search-box input:focus {{
            border-color: var(--accent-blue);
        }}

        /* Table */
        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 40px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 14px;
        }}

        th {{
            background-color: #0f172a;
            color: var(--text-muted);
            font-weight: 600;
            padding: 14px 18px;
            border-bottom: 1px solid var(--border-color);
            text-transform: uppercase;
            font-size: 12px;
            letter-spacing: 0.5px;
        }}

        td {{
            padding: 14px 18px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-main);
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        tr.project-row {{
            cursor: pointer;
            transition: background 0.15s;
        }}

        tr.project-row:hover {{
            background-color: var(--bg-card-hover);
        }}

        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
        }}

        .badge.success {{ background: var(--success-bg); color: var(--success-fg); }}
        .badge.warning {{ background: var(--warning-bg); color: var(--warning-fg); }}
        .badge.danger {{ background: var(--danger-bg); color: var(--danger-fg); }}
        .badge.info {{ background: var(--info-bg); color: var(--info-fg); }}
        .badge.secondary {{ background: var(--secondary-bg); color: var(--secondary-fg); }}

        /* Expanded Row Details */
        .details-row {{
            display: none;
            background-color: #0f172a;
        }}

        .details-row.open {{
            display: table-row;
        }}

        .details-content {{
            padding: 20px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }}

        .details-box {{
            background: var(--bg-card);
            padding: 16px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        .details-box h4 {{
            font-size: 14px;
            margin-bottom: 12px;
            color: var(--accent-blue);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 6px;
        }}

        .tag-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}

        .tag {{
            background: var(--border-color);
            color: var(--text-main);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-family: monospace;
        }}

        .diff-tag {{
            font-weight: bold;
        }}
        .diff-tag.pos {{ color: var(--success-fg); }}
        .diff-tag.neg {{ color: var(--danger-fg); }}

        footer {{
            text-align: center;
            padding: 20px 0;
            color: var(--text-muted);
            font-size: 13px;
            border-top: 1px solid var(--border-color);
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="header-title">
                <h1>Jira Migration Reporting Dashboard</h1>
                <p>Data Center ({source_name}) &rarr; Cloud ({target_name}) Migration Status</p>
            </div>
            <div class="header-meta">
                <div>Report Generated: <strong>{report_timestamp}</strong></div>
                <div style="margin-top:4px;">
                    Source: <span class="badge-env">{source_name}</span>
                    Target: <span class="badge-env">{target_name}</span>
                </div>
            </div>
        </header>

        <!-- KPI Cards -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-title">Total Projects</div>
                <div class="kpi-value">{total_projects}</div>
                <div class="kpi-subtext">Completed: {completed_projects} | In Progress: {in_progress_projects}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Issue Migration Volume</div>
                <div class="kpi-value">{total_cloud_issues:,} <span style="font-size:16px; font-weight:normal; color:var(--text-muted);">/ {total_dc_issues:,}</span></div>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {min(migration_progress_pct, 100)}%;"></div>
                </div>
                <div class="kpi-subtext">{migration_progress_pct}% Total Issues Migrated</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Completed Projects</div>
                <div class="kpi-value" style="color:var(--success-fg);">{completed_projects}</div>
                <div class="kpi-subtext">{round(completed_projects/total_projects*100, 1) if total_projects>0 else 0}% Project Completion Rate</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-title">Custom Fields Mapped</div>
                <div class="kpi-value" style="color:var(--accent-blue);">{mapped_cf_count} <span style="font-size:16px; font-weight:normal; color:var(--text-muted);">/ {total_dc_cfs}</span></div>
                <div class="progress-container">
                    <div class="progress-bar" style="width: {min(cf_mapped_pct, 100)}%;"></div>
                </div>
                <div class="kpi-subtext">{cf_mapped_pct}% Name-matched &amp; Reused Custom Fields</div>
            </div>
        </div>

        <!-- Section 1: Projects Matrix -->
        <div class="section-header">
            <h2 style="font-size: 20px;">1. Project Migration Status</h2>
            <div style="display:flex; gap:16px;">
                <div class="search-box">
                    <input type="text" id="projectSearch" placeholder="Search Project Key or Name..." onkeyup="filterProjects()">
                </div>
                <div class="tabs">
                    <button class="tab-btn active" onclick="setProjectFilter('ALL', this)">All ({total_projects})</button>
                    <button class="tab-btn" onclick="setProjectFilter('Completed', this)">Completed ({completed_projects})</button>
                    <button class="tab-btn" onclick="setProjectFilter('In Progress', this)">In Progress ({in_progress_projects})</button>
                    <button class="tab-btn" onclick="setProjectFilter('Not Migrated', this)">Not Migrated ({not_migrated_projects})</button>
                </div>
            </div>
        </div>

        <div class="table-card">
            <table id="projectsTable">
                <thead>
                    <tr>
                        <th>Project Key</th>
                        <th>Project Name</th>
                        <th>Status</th>
                        <th>DC Issues</th>
                        <th>Cloud Issues</th>
                        <th>Difference</th>
                        <th>DC CFs</th>
                        <th>Cloud CFs</th>
                    </tr>
                </thead>
                <tbody id="projectsTbody">
                    <!-- Dynamic rendering via JS -->
                </tbody>
            </table>
        </div>

        <!-- Section 2: Custom Field Mapping Matrix -->
        <div class="section-header">
            <h2 style="font-size: 20px;">2. Custom Field Linkage &amp; Reuse Matrix</h2>
            <div style="display:flex; gap:16px;">
                <div class="search-box">
                    <input type="text" id="cfSearch" placeholder="Search Custom Field Name or ID..." onkeyup="filterCustomFields()">
                </div>
                <div class="tabs" id="cfTabHeader">
                    <button class="tab-btn active" onclick="setCFFilter('ACTIVE', this)">Active in Projects ({active_cf_count})</button>
                    <button class="tab-btn" onclick="setCFFilter('ALL', this)">All System Fields ({len(cf_mapping_matrix)})</button>
                    <button class="tab-btn" onclick="setCFFilter('Mapped', this)">Mapped ({mapped_cf_count})</button>
                </div>
            </div>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>Field Name</th>
                        <th>DC Custom Field ID</th>
                        <th>Cloud Custom Field ID</th>
                        <th>Mapping Status</th>
                        <th>ID Match Type</th>
                    </tr>
                </thead>
                <tbody id="cfTbody">
                    <!-- Dynamic rendering via JS -->
                </tbody>
            </table>
        </div>

        <footer>
            Jira DC to Cloud Migration Report &bull; Antigravity Automated Reporting System
        </footer>
    </div>

    <script>
        const rawProjects = {projects_json};
        const rawCFMatrix = {cf_matrix_json};

        let currentProjectFilter = 'ALL';
        let currentCFFilter = 'ACTIVE';

        function renderProjects() {{
            const tbody = document.getElementById('projectsTbody');
            const searchQuery = document.getElementById('projectSearch').value.toLowerCase();
            tbody.innerHTML = '';

            rawProjects.forEach((p, idx) => {{
                if (currentProjectFilter !== 'ALL') {{
                    if (currentProjectFilter === 'Completed' && p.status !== 'Completed') return;
                    if (currentProjectFilter === 'In Progress' && !p.status.includes('In Progress')) return;
                    if (currentProjectFilter === 'Not Migrated' && p.status !== 'Not Migrated') return;
                }}

                if (searchQuery) {{
                    const matchKey = p.key.toLowerCase().includes(searchQuery);
                    const matchName = p.name.toLowerCase().includes(searchQuery);
                    const matchDcCF = p.enriched_dc_custom_fields.some(cf => cf.name.toLowerCase().includes(searchQuery) || cf.id.toLowerCase().includes(searchQuery));
                    const matchCloudCF = p.enriched_cloud_custom_fields.some(cf => cf.name.toLowerCase().includes(searchQuery) || cf.id.toLowerCase().includes(searchQuery));
                    if (!matchKey && !matchName && !matchDcCF && !matchCloudCF) return;
                }}

                const tr = document.createElement('tr');
                tr.className = 'project-row';
                tr.onclick = () => toggleDetails(idx);

                const diffClass = p.issue_diff === 0 ? 'pos' : (p.issue_diff > 0 ? 'pos' : 'neg');
                const diffSign = p.issue_diff > 0 ? '+' : '';

                tr.innerHTML = `
                    <td><strong>${{p.key}}</strong></td>
                    <td>${{p.name}}</td>
                    <td><span class="badge ${{p.status_class}}">${{p.status}}</span></td>
                    <td>${{p.dc_issues.toLocaleString()}}</td>
                    <td>${{p.cloud_issues.toLocaleString()}}</td>
                    <td><span class="diff-tag ${{diffClass}}">${{diffSign}}${{p.issue_diff}}</span></td>
                    <td>${{p.dc_cf_count}}</td>
                    <td>${{p.cloud_cf_count}}</td>
                `;

                const detailsTr = document.createElement('tr');
                detailsTr.id = `details-${{idx}}`;
                detailsTr.className = 'details-row';

                let dcItHtml = Object.entries(p.dc_issue_types).map(([k, v]) => `<div class="tag">${{k}}: <strong>${{v}}</strong></div>`).join('') || '<div class="tag">None</div>';
                let cloudItHtml = Object.entries(p.cloud_issue_types).map(([k, v]) => `<div class="tag">${{k}}: <strong>${{v}}</strong></div>`).join('') || '<div class="tag">None</div>';

                const dcScreenCfs = p.enriched_dc_custom_fields.filter(cf => cf.usage !== 'Data Only');
                const dcDataOnlyCfs = p.enriched_dc_custom_fields.filter(cf => cf.usage === 'Data Only');

                let dcCfHtml = dcScreenCfs.map(item => 
                    `<div class="tag" style="background:#1e293b; border:1px solid #3b82f6; padding:6px 10px; border-radius:6px; margin:2px;">
                        <span style="color:#38bdf8; font-weight:700;">${{item.name}}</span> ${{item.usage_badge}}
                        <span style="color:#94a3b8; font-size:11px; display:block; margin-top:2px;">(${{item.id_label}})</span>
                    </div>`
                ).join('');

                if (dcDataOnlyCfs.length > 0) {{
                    const dataOnlyList = dcDataOnlyCfs.map(item => 
                        `<div class="tag" style="background:#1e293b; border:1px solid #475569; padding:6px 10px; border-radius:6px; margin:2px;">
                            <span style="color:#94a3b8; font-weight:700;">${{item.name}}</span> ${{item.usage_badge}}
                            <span style="color:#64748b; font-size:11px; display:block; margin-top:2px;">(${{item.id_label}})</span>
                        </div>`
                    ).join('');
                    dcCfHtml += `
                        <details style="margin-top:8px; width:100%;">
                            <summary style="cursor:pointer; color:#94a3b8; font-size:12px; font-weight:600; padding:4px 0;">
                                Data Only Fields (${{dcDataOnlyCfs.length}})
                            </summary>
                            <div class="tag-list" style="margin-top:6px;">${{dataOnlyList}}</div>
                        </details>
                    `;
                }}

                if (!dcCfHtml) dcCfHtml = '<div class="tag">None</div>';

                const cloudScreenCfs = p.enriched_cloud_custom_fields.filter(cf => cf.usage !== 'Data Only');
                const cloudDataOnlyCfs = p.enriched_cloud_custom_fields.filter(cf => cf.usage === 'Data Only');

                let cloudCfHtml = cloudScreenCfs.map(item => 
                    `<div class="tag" style="background:#1e293b; border:1px solid #818cf8; padding:6px 10px; border-radius:6px; margin:2px;">
                        <span style="color:#a7f3d0; font-weight:700;">${{item.name}}</span> ${{item.usage_badge}}
                        <span style="color:#94a3b8; font-size:11px; display:block; margin-top:2px;">(${{item.id_label}})</span>
                    </div>`
                ).join('');

                if (cloudDataOnlyCfs.length > 0) {{
                    const dataOnlyList = cloudDataOnlyCfs.map(item => 
                        `<div class="tag" style="background:#1e293b; border:1px solid #475569; padding:6px 10px; border-radius:6px; margin:2px;">
                            <span style="color:#94a3b8; font-weight:700;">${{item.name}}</span> ${{item.usage_badge}}
                            <span style="color:#64748b; font-size:11px; display:block; margin-top:2px;">(${{item.id_label}})</span>
                        </div>`
                    ).join('');
                    cloudCfHtml += `
                        <details style="margin-top:8px; width:100%;">
                            <summary style="cursor:pointer; color:#94a3b8; font-size:12px; font-weight:600; padding:4px 0;">
                                Data Only Fields (${{cloudDataOnlyCfs.length}})
                            </summary>
                            <div class="tag-list" style="margin-top:6px;">${{dataOnlyList}}</div>
                        </details>
                    `;
                }}

                if (!cloudCfHtml) cloudCfHtml = '<div class="tag">None</div>';

                detailsTr.innerHTML = `
                    <td colspan="8">
                        <div class="details-content">
                            <div class="details-box">
                                <h4>Data Center Issue Types (${{p.dc_issues.toLocaleString()}})</h4>
                                <div class="tag-list" style="margin-bottom:16px;">${{dcItHtml}}</div>
                                <h4>DC Active Custom Field Names &amp; Mapping (${{p.dc_cf_count}})</h4>
                                <div class="tag-list">${{dcCfHtml}}</div>
                            </div>
                            <div class="details-box">
                                <h4>Cloud Issue Types (${{p.cloud_issues.toLocaleString()}})</h4>
                                <div class="tag-list" style="margin-bottom:16px;">${{cloudItHtml}}</div>
                                <h4>Cloud Active Custom Field Names (${{p.cloud_cf_count}})</h4>
                                <div class="tag-list">${{cloudCfHtml}}</div>
                            </div>
                        </div>
                    </td>
                `;

                tbody.appendChild(tr);
                tbody.appendChild(detailsTr);
            }});
        }}

        function toggleDetails(idx) {{
            const detailsRow = document.getElementById(`details-${{idx}}`);
            if (detailsRow) {{
                detailsRow.classList.toggle('open');
            }}
        }}

        function setProjectFilter(filter, btn) {{
            currentProjectFilter = filter;
            document.querySelectorAll('.tabs .tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderProjects();
        }}

        function filterProjects() {{
            renderProjects();
        }}

        function setCFFilter(filter, btn) {{
            currentCFFilter = filter;
            document.querySelectorAll('#cfTabHeader .tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderCustomFields();
        }}

        function renderCustomFields() {{
            const tbody = document.getElementById('cfTbody');
            const searchQuery = document.getElementById('cfSearch').value.toLowerCase();
            tbody.innerHTML = '';

            rawCFMatrix.forEach(cf => {{
                if (currentCFFilter === 'ACTIVE' && !cf.used_in_projects) return;
                if (currentCFFilter === 'Mapped' && cf.status !== 'Mapped') return;

                if (searchQuery) {{
                    const matchName = cf.name.toLowerCase().includes(searchQuery);
                    const matchDc = cf.dc_id.toLowerCase().includes(searchQuery);
                    const matchCloud = (cf.cloud_id || '').toLowerCase().includes(searchQuery);
                    if (!matchName && !matchDc && !matchCloud) return;
                }}

                const tr = document.createElement('tr');
                const badgeClass = cf.status === 'Mapped' ? 'success' : (cf.status.includes('Unmapped') ? 'warning' : 'info');
                
                tr.innerHTML = `
                    <td><strong>${{cf.name}}</strong> ${{cf.used_in_projects ? '<span class="badge info" style="font-size:10px; margin-left:6px;">Active in Project</span>' : ''}}</td>
                    <td><code>${{cf.dc_id}}</code></td>
                    <td><code>${{cf.cloud_id || 'Not Found'}}</code></td>
                    <td><span class="badge ${{badgeClass}}">${{cf.status}}</span></td>
                    <td><span style="font-size:12px; color:var(--text-muted);">${{cf.match_type}}</span></td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function filterCustomFields() {{
            renderCustomFields();
        }}

        renderProjects();
        renderCustomFields();
    </script>
</body>
</html>
"""

    with open(output_filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
        
    log(f"Successfully generated HTML report at: {output_filepath}")

def main():
    parser = argparse.ArgumentParser(description="Jira Migration HTML Report Generator")
    parser.add_argument("--source", default="JIRA_DC_STG", choices=["JIRA_DC_STG", "JIRA_DC_OPS"], help="Source environment")
    parser.add_argument("--target", default="JIRA_CLOUD_SANDBOX", choices=["JIRA_CLOUD_SANDBOX", "JIRA_CLOUD_OPS"], help="Target environment")
    parser.add_argument("--file", default="projects.txt", help="Path to projects.txt file")
    parser.add_argument("--output", default="migration_report.html", help="Output HTML file path")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent thread workers")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of projects for testing")
    args = parser.parse_args()

    load_environment()

    # SSL verification setup
    ssl_verify_env = os.getenv("VERIFY_SSL") or os.getenv("JIRA_SSL_VERIFY")
    ssl_verify = ssl_verify_env.lower() in ("true", "1", "yes") if ssl_verify_env else False
    if not ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log("SSL Verification disabled.")

    session = requests.Session()
    session.verify = ssl_verify

    # Configure DC Client
    if args.source == "JIRA_DC_STG":
        dc_url = os.getenv("JIRA_DC_STG_URL")
        dc_token = os.getenv("JIRA_DC_STG_API_TOKEN") or os.getenv("JIRA_DC_STG_TOKEN")
    else:
        dc_url = os.getenv("JIRA_DC_OPS_URL")
        dc_token = os.getenv("JIRA_DC_OPS_API_TOKEN") or os.getenv("JIRA_DC_OPS_TOKEN")

    if not dc_url or not dc_token:
        log(f"Error: Missing URL or API token for source environment '{args.source}' in env file.")
        sys.exit(1)

    dc_client = JiraClient(args.source, session, dc_url, bearer_token=dc_token, is_dc=True)

    # Configure Cloud Client
    if args.target == "JIRA_CLOUD_SANDBOX":
        cloud_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")
        cloud_email = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_ADMIN_EMAIL")
        cloud_token = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")
    else:
        cloud_url = os.getenv("JIRA_CLOUD_OPS_URL") or os.getenv("JIRA_CLOUD_URL")
        cloud_email = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_ADMIN_EMAIL")
        cloud_token = os.getenv("JIRA_CLOUD_OPS_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")

    if not cloud_url or not cloud_email or not cloud_token:
        log(f"Error: Missing URL, Email or Token for target environment '{args.target}' in env file.")
        sys.exit(1)

    cloud_auth = HTTPBasicAuth(cloud_email, cloud_token)
    cloud_client = JiraClient(args.target, session, cloud_url, auth=cloud_auth, is_dc=False)

    # Load projects
    projects = get_projects(args.file, limit=args.limit)
    log(f"Starting migration scan for {len(projects)} projects...")
    log(f"Source DC: {dc_url}")
    log(f"Target Cloud: {cloud_url}")

    # Fetch global custom field definitions
    log("Fetching custom field definitions from DC and Cloud...")
    dc_fields = dc_client.get_all_fields()
    cloud_fields = cloud_client.get_all_fields()
    log(f"Retrieved {len(dc_fields)} field metadata items from DC, {len(cloud_fields)} from Cloud.")

    # Process projects in parallel
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_proj = {
            executor.submit(fetch_project_data, dc_client, cloud_client, proj): proj
            for proj in projects
        }
        for future in as_completed(future_to_proj):
            proj_key = future_to_proj[future]
            try:
                res = future.result()
                results.append(res)
                log(f"[{len(results)}/{len(projects)}] Processed {proj_key} - Status: {res['status']} (DC: {res['dc_issues']}, Cloud: {res['cloud_issues']})")
            except Exception as e:
                log(f"Error processing project {proj_key}: {e}")

    # Sort results by project key
    results.sort(key=lambda x: x["key"])

    # Generate Report
    log("Generating HTML Migration Report Dashboard...")
    generate_html_report(results, dc_fields, cloud_fields, args.source, args.target, args.output)
    log("Done!")

if __name__ == "__main__":
    main()
