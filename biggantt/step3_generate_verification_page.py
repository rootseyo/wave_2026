#!/usr/bin/env python3
"""
Step 3: Generate Box Verification Page
Generates an interactive HTML dashboard to compare Data Center (Source) and Jira Cloud (Target) boxes.
Uses dedicated environment variables for Data Center and Cloud BigGantt Base URLs:
  - Source DC: JIRA_DC_STG_URL / JIRA_DC_OPS_URL (or JIRA_DC_STG_BIGGANTT_BASE_URL / JIRA_DC_OPS_BIGGANTT_BASE_URL)
  - Target Cloud: JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL / JIRA_CLOUD_OPS_BIGGANTT_BASE_URL
"""

import os
import sys
import json
import argparse
import re
from dotenv import load_dotenv
from datetime import datetime
from html import escape

# Load environment variables from ../env
script_dir = os.path.dirname(os.path.abspath(__file__)) if __file__ else '.'
ENV_FILE = os.path.abspath(os.path.join(script_dir, '..', 'env'))
if os.path.exists(ENV_FILE):
    load_dotenv(dotenv_path=ENV_FILE)
elif os.path.exists('env'):
    load_dotenv(dotenv_path='env')

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(PROJECT_ROOT, "migration_box.json")
OUTPUT_HTML = os.path.join(PROJECT_ROOT, "box_verification.html")

# Jira Cloud BigGantt's current route is a hash route without a slash after
# the hash.  Keep the app identifiers and query string fixed; only the box ID
# between ``#box/`` and the following slash is replaced per migrated box.
DEFAULT_CLOUD_BIGGANTT_URL = (
    "https://hmg.atlassian.net/jira/apps/"
    "bf8faa39-5ae4-4289-ad2f-cc0cfdd1245a/"
    "bb4f6f47-1446-4a8e-8312-e03f32029cf5#box/BOX-ID/g?redirect=true"
)

def detect_environments_from_data(boxes):
    """Detects source and target environments from migration_box.json metadata."""
    detected_source = None
    detected_target = None

    for box in boxes:
        if not detected_source and box.get("source_environment"):
            detected_source = str(box.get("source_environment")).upper()
        if not detected_target and box.get("target_environment"):
            detected_target = str(box.get("target_environment")).upper()

    return detected_source, detected_target

def select_source_env(cli_val, detected_env):
    if cli_val:
        return "OPS" if cli_val.upper() in ("OPS", "PROD", "2") else "STG"
    
    stg_url = os.getenv("JIRA_DC_STG_URL") or "Not configured"
    ops_url = os.getenv("JIRA_DC_OPS_URL") or "Not configured"
    default_choice = "2" if detected_env == "OPS" else "1"
    
    print("\nSelect Source (Data Center) Environment:")
    print(f"  1. STG (Staging) - {stg_url} {'[Detected]' if detected_env == 'STG' else ''}")
    print(f"  2. OPS (Operations) - {ops_url} {'[Detected]' if detected_env == 'OPS' else ''}")
    
    raw = input(f"Enter choice (1/2, default {default_choice}): ").strip()
    choice = raw if raw else default_choice
    return "OPS" if choice == "2" else "STG"

def select_target_env(cli_val, detected_env):
    if cli_val:
        return "OPS" if cli_val.upper() in ("OPS", "PROD", "2") else "SANDBOX"
    
    sandbox_url = os.getenv("JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_SANDBOX_URL") or "Not configured"
    ops_url = os.getenv("JIRA_CLOUD_OPS_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_OPS_URL") or "Not configured"
    default_choice = "2" if detected_env == "OPS" else "1"
    
    print("\nSelect Target (Jira Cloud) Environment:")
    print(f"  1. Sandbox (SANDBOX) - {sandbox_url} {'[Detected]' if detected_env in ('SANDBOX', 'STG') else ''}")
    print(f"  2. Production (OPS) - {ops_url} {'[Detected]' if detected_env == 'OPS' else ''}")
    
    raw = input(f"Enter choice (1/2, default {default_choice}): ").strip()
    choice = raw if raw else default_choice
    return "OPS" if choice == "2" else "SANDBOX"

def get_dc_box_url(box_id, source_env="OPS"):
    """
    Fixed Data Center BigGantt Box URL.
    - OPS: https://jira.hmg-corp.io/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/g
    - STG: https://stg-jira.hmg-corp.io/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/g
    """
    if source_env == "STG":
        return f"https://stg-jira.hmg-corp.io/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/g"
    return f"https://jira.hmg-corp.io/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/g"

def get_cloud_box_url(box_id, target_env):
    """
    Builds Jira Cloud BigGantt Box URL using dedicated environment variable:
    - Sandbox: JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL
    - OPS: JIRA_CLOUD_OPS_BIGGANTT_BASE_URL
    Canonical result URL:
    https://hmg.atlassian.net/jira/apps/bf8faa39-5ae4-4289-ad2f-cc0cfdd1245a/
    bb4f6f47-1446-4a8e-8312-e03f32029cf5#box/{box_id}/g?redirect=true

    An environment variable may contain a complete Cloud box URL (including
    an existing box ID). In that case its box ID is replaced while all other
    route components and query parameters are preserved.
    """
    if target_env == "OPS":
        base = os.getenv("JIRA_CLOUD_OPS_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
        if not base:
            base = DEFAULT_CLOUD_BIGGANTT_URL
    else:
        base = os.getenv("JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
        if not base:
            base = DEFAULT_CLOUD_BIGGANTT_URL

    base = base.strip()
    if "${boxKey}" in base or "{boxId}" in base:
        return base.replace("${boxKey}", str(box_id)).replace("{boxId}", str(box_id))

    # Current Jira Cloud BigGantt route, e.g. ...#box/PROG-568/g?redirect=true.
    # Replace only the ID, retaining the app UUIDs, view mode and query string.
    if re.search(r"#box/[^/?#]+", base):
        return re.sub(r"(#box/)[^/?#]+", rf"\g<1>{box_id}", base, count=1)

    base = base.rstrip("/")
    # Backward-compatible support for older configured route formats.
    if "#/box" in base:
        return f"{base}/{box_id}"
    elif "#/" in base:
        return f"{base}/box/{box_id}"

    # A configured app base without a hash route uses the current Cloud route.
    return f"{base}#box/{box_id}/g?redirect=true"

def main():
    parser = argparse.ArgumentParser(description="Generate Step 3 Box Verification comparison HTML dashboard.")
    parser.add_argument("--source-env", choices=["stg", "ops"], help="Data Center source environment (stg or ops)")
    parser.add_argument("--target-env", choices=["sandbox", "stg", "ops"], help="Jira Cloud target environment (sandbox or ops)")
    parser.add_argument("--output", default=OUTPUT_HTML, help="Output HTML file path (default: box_verification.html)")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm using detected or default environments")
    args = parser.parse_args()

    print("=" * 60)
    print(" [Step 3: Generate Box Verification Page] (BigGantt Mode)")
    print("=" * 60)

    if not os.path.exists(DATA_FILE):
        print(f"Error: Required migration results file '{DATA_FILE}' not found.")
        print("Please run Step 1 (export) and Step 2 (import) first.")
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            boxes = json.load(f)
    except Exception as e:
        print(f"Error loading {DATA_FILE}: {e}")
        return

    print(f"Loaded {len(boxes)} boxes from '{DATA_FILE}'.")

    # Detect environments from migration_box.json metadata
    detected_source, detected_target = detect_environments_from_data(boxes)
    if detected_source or detected_target:
        print(f"\n[*] Detected metadata from Step 1 / Step 2:")
        if detected_source:
            print(f"  - Source (DC) Environment : {detected_source}")
        if detected_target:
            print(f"  - Target (Cloud) Environment: {detected_target}")

    # Resolve environment selections
    if args.yes:
        source_env = "OPS" if (args.source_env == "ops" or detected_source == "OPS") else "STG"
        target_env = "OPS" if (args.target_env == "ops" or detected_target == "OPS") else "SANDBOX"
    else:
        source_env = select_source_env(args.source_env, detected_source)
        target_env = select_target_env(args.target_env, detected_target)

    # Base URLs for display
    source_base_url = "https://stg-jira.hmg-corp.io" if source_env == "STG" else "https://jira.hmg-corp.io"
    source_label = f"DC {'STG' if source_env == 'STG' else 'OPS'}"

    if target_env == "OPS":
        target_base_url = os.getenv("JIRA_CLOUD_OPS_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_OPS_URL") or ""
        target_label = "Cloud OPS"
    else:
        target_base_url = os.getenv("JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_SANDBOX_URL") or ""
        target_label = "Cloud SANDBOX"

    print("\n" + "=" * 60)
    print(" [Verification Configuration]")
    print("=" * 60)
    print(f"Source (Origin DC) : {source_label} -> {source_base_url}")
    print(f"Target (Cloud App) : {target_label} -> {target_base_url}")
    print(f"Output HTML        : {args.output}")
    print("=" * 60)

    # Process and build comparison items
    items = []
    skipped_count = 0
    for idx, box in enumerate(boxes, 1):
        box_name = box.get("boxName") or "-"
        box_type = box.get("boxType") or "-"
        origin_id = str(box.get("boxId") or "")
        cloud_id = str(box.get("newBoxId") or "")
        jira_projects = box.get("jira_projects", [])
        
        if not cloud_id:
            skipped_count += 1
            continue

        origin_url = get_dc_box_url(origin_id, source_env)
        cloud_url = get_cloud_box_url(cloud_id, target_env)

        items.append({
            "index": idx,
            "name": box_name,
            "type": str(box_type),
            "originId": origin_id,
            "cloudId": cloud_id,
            "originUrl": origin_url,
            "cloudUrl": cloud_url,
            "projects": ", ".join(str(p) for p in jira_projects) if jira_projects else "-"
        })

    print(f"\nGenerated {len(items)} items for verification (Skipped {skipped_count} boxes without Cloud ID).")

    # Generate HTML content with Glassmorphism Dark Mode
    html_template = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BigGantt Migration Verification Dashboard</title>
    <!-- Google Fonts Inter & Outfit -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #6366f1;
            --primary-glow: rgba(99, 102, 241, 0.15);
            --success: #10b981;
            --success-glow: rgba(16, 185, 129, 0.15);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent: #a855f7;
            --hover-row: rgba(255, 255, 255, 0.03);
            --checked-row: rgba(16, 185, 129, 0.06);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.12) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(168, 85, 247, 0.08) 0px, transparent 50%);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            min-height: 100vh;
            padding: 2rem;
            line-height: 1.5;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        header {{
            margin-bottom: 2rem;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            border-bottom: 1px solid var(--card-border);
            padding-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}

        .title-area h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #fff 30%, var(--text-muted) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }}

        .env-badge-group {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-top: 0.5rem;
            font-size: 0.85rem;
        }}

        .env-badge {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-weight: 600;
            background: rgba(99, 102, 241, 0.15);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #c7d2fe;
        }}

        .env-badge.cloud {{
            background: rgba(16, 185, 129, 0.15);
            border-color: rgba(16, 185, 129, 0.3);
            color: #a7f3d0;
        }}

        .stats-badge {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            padding: 0.75rem 1.5rem;
            border-radius: 12px;
            font-family: 'Outfit', sans-serif;
            display: flex;
            gap: 1.5rem;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(10px);
        }}

        .stat-item {{
            display: flex;
            flex-direction: column;
        }}

        .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        .stat-value {{
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text-main);
        }}
        
        .stat-value.success-text {{
            color: var(--success);
        }}

        /* Alert Banner */
        .alert-banner {{
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.3);
            border-radius: 12px;
            padding: 1rem 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            align-items: center;
            gap: 1rem;
            color: #d1d5db;
            font-size: 0.95rem;
            backdrop-filter: blur(5px);
        }}

        .alert-icon {{
            font-size: 1.5rem;
            color: var(--primary);
        }}

        /* Grid Comparison Layout */
        .verification-card {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
            backdrop-filter: blur(12px);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}

        th {{
            background: rgba(17, 24, 39, 0.9);
            padding: 1.2rem 1.5rem;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            border-bottom: 1px solid var(--card-border);
        }}

        td {{
            padding: 1.2rem 1.5rem;
            border-bottom: 1px solid var(--card-border);
            vertical-align: middle;
            transition: all 0.2s ease;
        }}

        tr {{
            transition: background-color 0.25s ease;
        }}

        tr:hover td {{
            background-color: var(--hover-row);
        }}

        /* Completed/Verified state styling */
        tr.verified-row {{
            background-color: var(--checked-row);
        }}

        tr.verified-row td {{
            border-bottom-color: rgba(16, 185, 129, 0.15);
        }}

        /* Custom Checkbox Design */
        .checkbox-container {{
            display: inline-block;
            position: relative;
            padding-left: 2rem;
            cursor: pointer;
            user-select: none;
            font-size: 0.95rem;
        }}

        .checkbox-container input {{
            position: absolute;
            opacity: 0;
            cursor: pointer;
            height: 0;
            width: 0;
        }}

        .checkmark {{
            position: absolute;
            top: 50%;
            left: 0;
            transform: translateY(-50%);
            height: 20px;
            width: 20px;
            background-color: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            border-radius: 6px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .checkbox-container:hover input ~ .checkmark {{
            background-color: rgba(255, 255, 255, 0.1);
            border-color: var(--primary);
            box-shadow: 0 0 8px var(--primary-glow);
        }}

        .checkbox-container input:checked ~ .checkmark {{
            background-color: var(--success);
            border-color: var(--success);
            box-shadow: 0 0 10px var(--success-glow);
        }}

        .checkbox-container .checkmark:after {{
            content: "";
            position: absolute;
            display: none;
        }}

        .checkbox-container input:checked ~ .checkmark:after {{
            display: block;
        }}

        .checkbox-container .checkmark:after {{
            left: 6px;
            top: 2px;
            width: 5px;
            height: 10px;
            border: solid #ffffff;
            border-width: 0 2px 2px 0;
            transform: rotate(45deg);
        }}

        .box-title {{
            font-weight: 600;
            font-size: 1rem;
            color: #ffffff;
        }}

        .box-type-badge {{
            display: inline-block;
            padding: 0.15rem 0.6rem;
            font-size: 0.75rem;
            font-weight: 500;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}

        .box-type-badge.program {{
            background: rgba(99, 102, 241, 0.15);
            color: #a5b4fc;
        }}

        .box-type-badge.agile {{
            background: rgba(168, 85, 247, 0.15);
            color: #d8b4fe;
        }}

        /* Key and Link Styling */
        .key-badge {{
            font-family: monospace;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 0.25rem 0.5rem;
            border-radius: 6px;
            color: #e5e7eb;
            font-size: 0.9rem;
            display: inline-block;
        }}

        .link-btn {{
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            color: var(--primary);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 500;
            transition: color 0.2s ease;
        }}

        .link-btn:hover {{
            color: #818cf8;
            text-decoration: underline;
        }}

        .link-btn.cloud-link {{
            color: #34d399;
        }}

        .link-btn.cloud-link:hover {{
            color: #6ee7b7;
        }}

        .project-text {{
            color: var(--text-muted);
            font-size: 0.9rem;
            max-width: 250px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        /* External Link Icon */
        .ext-icon {{
            width: 12px;
            height: 12px;
            fill: currentColor;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="title-area">
                <h1>BigGantt Migration Verification</h1>
                <p>Verify migrated boxes in Data Center and Jira Cloud independently. Each checkbox opens only its own link.</p>
                <div class="env-badge-group">
                    <span class="env-badge">🏛️ {escape(source_label)}: <span style="font-family: monospace; font-size: 0.8rem;">{escape(source_base_url)}</span></span>
                    <span>➡️</span>
                    <span class="env-badge cloud">☁️ {escape(target_label)}: <span style="font-family: monospace; font-size: 0.8rem;">{escape(target_base_url)}</span></span>
                </div>
            </div>
            <div class="stats-badge">
                <div class="stat-item">
                    <span class="stat-label">Total Items</span>
                    <span class="stat-value" id="total-count">{len(items)}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Verified</span>
                    <span class="stat-value success-text" id="verified-count">0</span>
                </div>
            </div>
        </header>

        <div class="alert-banner">
            <span class="alert-icon">💡</span>
            <div>
                <strong>Popup permission required:</strong> Checking a DC or Cloud box opens that corresponding link in a new tab. Please ensure your browser's <strong>pop-up blocker is disabled</strong> for this page.
            </div>
        </div>

        <div class="verification-card">
            <table>
                <thead>
                    <tr>
                        <th style="width: 30%">Box Name & Type</th>
                        <th style="width: 30%">Original Box (Data Center)</th>
                        <th style="width: 30%">Cloud Box (Target)</th>
                        <th style="width: 10%">Jira Projects</th>
                    </tr>
                </thead>
                <tbody id="table-body">
"""

    for item in items:
        badge_class = "program" if "program" in item["type"].lower() or item["type"] == "2" else "agile" if "agile" in item["type"].lower() or item["type"] == "8" else ""
        type_label = "Program" if item["type"] == "2" or "program" in item["type"].lower() else "Agile" if item["type"] == "8" or "agile" in item["type"].lower() else item["type"]
        
        html_template += f"""                    <tr id="row-{escape(item['originId'])}">
                        <td>
                            <div class="box-title">{escape(item['name'])}</div>
                            <span class="box-type-badge {badge_class}">{escape(type_label)}</span>
                        </td>
                        <td>
                            <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                                <div><span class="key-badge">{escape(item['originId'])}</span></div>
                                <label class="checkbox-container">
                                    <input type="checkbox" id="check-dc-{escape(item['originId'])}" onchange="toggleVerify('{escape(item['originId'])}', 'dc', '{escape(item['originUrl'])}')">
                                    <span class="checkmark"></span>
                                    <span style="font-size: 0.85rem; color: var(--text-muted); margin-left: 0.25rem;">Verify DC</span>
                                </label>
                                <a href="{escape(item['originUrl'])}" target="_blank" class="link-btn">
                                    Open DC Box 
                                    <svg class="ext-icon" viewBox="0 0 24 24"><path d="M21 13v10h-21v-23h17v2h-15v19h17v-8h2zm3-12h-10.988l4.035 4-6.97 7 1.986 2 7.03-7 3.937 4v-10z"/></svg>
                                </a>
                            </div>
                        </td>
                        <td>
                            <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                                <div><span class="key-badge">{escape(item['cloudId'])}</span></div>
                                <label class="checkbox-container">
                                    <input type="checkbox" id="check-cloud-{escape(item['originId'])}" onchange="toggleVerify('{escape(item['originId'])}', 'cloud', '{escape(item['cloudUrl'])}')">
                                    <span class="checkmark"></span>
                                    <span style="font-size: 0.85rem; color: var(--text-muted); margin-left: 0.25rem;">Verify Cloud</span>
                                </label>
                                <a href="{escape(item['cloudUrl'])}" target="_blank" class="link-btn cloud-link">
                                    Open Cloud Box 
                                    <svg class="ext-icon" viewBox="0 0 24 24"><path d="M21 13v10h-21v-23h17v2h-15v19h17v-8h2zm3-12h-10.988l4.035 4-6.97 7 1.986 2 7.03-7 3.937 4v-10z"/></svg>
                                </a>
                            </div>
                        </td>
                        <td>
                            <div class="project-text" title="{escape(item['projects'])}">{escape(item['projects'])}</div>
                        </td>
                    </tr>
"""

    html_template += f"""                </tbody>
            </table>
        </div>
    </div>

    <script>
        // Store DC and Cloud verification independently (keyed by environment).
        const localStorageKey = 'biggantt_verified_boxes_v2_{source_env}_{target_env}';
        let verifiedBoxes = JSON.parse(localStorage.getItem(localStorageKey)) || {{}};

        // Initial setup
        document.addEventListener('DOMContentLoaded', () => {{
            Object.entries(verifiedBoxes).forEach(([key, checked]) => {{
                if (!checked) return;
                const [id, side] = key.split(':');
                const checkbox = document.getElementById('check-' + side + '-' + id);
                if (checkbox) {{
                    checkbox.checked = true;
                }}
                updateRowVerificationState(id);
            }});
            updateVerifiedCount();
        }});

        function toggleVerify(id, side, url) {{
            const checkbox = document.getElementById('check-' + side + '-' + id);
            const storageId = id + ':' + side;
            if (checkbox.checked) {{
                verifiedBoxes[storageId] = true;
                window.open(url, '_blank');
            }} else {{
                delete verifiedBoxes[storageId];
            }}

            updateRowVerificationState(id);
            localStorage.setItem(localStorageKey, JSON.stringify(verifiedBoxes));
            updateVerifiedCount();
        }}

        function updateRowVerificationState(id) {{
            const row = document.getElementById('row-' + id);
            if (!row) return;
            const complete = verifiedBoxes[id + ':dc'] && verifiedBoxes[id + ':cloud'];
            row.classList.toggle('verified-row', complete);
        }}

        function updateVerifiedCount() {{
            const countSpan = document.getElementById('verified-count');
            if (countSpan) {{
                // A box is complete only after both DC and Cloud are checked.
                const completedIds = new Set(
                    Object.keys(verifiedBoxes)
                        .filter(key => verifiedBoxes[key] && key.endsWith(':dc'))
                        .map(key => key.slice(0, -3))
                        .filter(id => verifiedBoxes[id + ':cloud'])
                );
                countSpan.innerText = completedIds.size;
            }}
        }}
    </script>
</body>
</html>
"""

    try:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html_template)
        print(f"\n[Success] Verification HTML generated successfully -> '{args.output}'")
        print(f"Open '{args.output}' in your browser to start comparing the boxes.")
    except Exception as e:
        print(f"Error writing HTML output: {e}")

if __name__ == "__main__":
    main()
