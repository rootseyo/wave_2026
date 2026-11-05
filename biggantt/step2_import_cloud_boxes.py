import os
import sys
import json
import csv
import re
import io
import time
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from datetime import datetime
import urllib3
from html import escape

# Disable insecure SSL warning logs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialize resilient requests session with exponential backoff retry
session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS"]
)
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))

# Load only the shared environment file one directory above the project.  This
# prevents an unrelated cwd/env file from changing a production migration.
script_dir = os.path.dirname(os.path.abspath(__file__)) if __file__ else '.'
ENV_FILE = os.path.abspath(os.path.join(script_dir, '..', 'env'))
if os.path.exists(ENV_FILE):
    load_dotenv(dotenv_path=ENV_FILE)
else:
    print(f"Error: env file not found: {ENV_FILE}")
    exit(1)

# BigPicture/BigGantt Cloud API Configuration
BP_BASE_URL = "https://api.bigpicture.one/public/biggantt/rest"

# Keep input/output files anchored to the script directory, not the launch directory.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MIGRATION_BOX_FILE = os.path.join(PROJECT_ROOT, "migration_box.json")
REPORT_FILE = os.path.join(PROJECT_ROOT, "step2_import_cloud_boxes_report.html")

def resolve_input_file():
    """Detect whether to load migration boxes from a CSV file or JSON file."""
    for idx, arg in enumerate(sys.argv):
        if arg in ("--file", "-f", "--csv") and idx + 1 < len(sys.argv):
            custom_path = os.path.abspath(sys.argv[idx + 1].strip())
            if os.path.isfile(custom_path):
                return custom_path
            print(f"Error: Specified file not found: {custom_path}")
            sys.exit(1)
        if arg.startswith("--file=") or arg.startswith("--csv="):
            custom_path = os.path.abspath(arg.split("=", 1)[1].strip())
            if os.path.isfile(custom_path):
                return custom_path
            print(f"Error: Specified file not found: {custom_path}")
            sys.exit(1)

    # Auto-detection priority:
    # 1. migration_boxes.csv (downloaded from step1_box_report.html)
    # 2. migration_box.csv
    # 3. migration_box.json (fallback/default)
    csv_candidates = [
        os.path.join(PROJECT_ROOT, "migration_boxes.csv"),
        os.path.join(PROJECT_ROOT, "migration_box.csv")
    ]
    for candidate in csv_candidates:
        if os.path.isfile(candidate):
            return candidate

    return MIGRATION_BOX_FILE

def _extract_column(row, candidates, default=""):
    # 1. Exact match (case-sensitive)
    for cand in candidates:
        if cand in row and row[cand] is not None and str(row[cand]).strip() != "":
            return str(row[cand]).strip()

    # 2. Case-insensitive exact match
    for cand in candidates:
        cand_lower = cand.strip().lower()
        for k, v in row.items():
            if k and k.strip().lower() == cand_lower and v is not None and str(v).strip() != "":
                return str(v).strip()

    # 3. Clean token match (strip punctuation/spaces)
    for cand in candidates:
        cand_clean = re.sub(r'[^a-zA-Z0-9가-힣]', '', cand).lower()
        if not cand_clean:
            continue
        for k, v in row.items():
            if not k:
                continue
            k_clean = re.sub(r'[^a-zA-Z0-9가-힣]', '', k).lower()
            if cand_clean == k_clean and v is not None and str(v).strip() != "":
                return str(v).strip()

    # 4. Safe substring match
    for cand in candidates:
        cand_lower = cand.strip().lower()
        if len(cand_lower) >= 3:
            for k, v in row.items():
                if not k:
                    continue
                k_lower = k.strip().lower()
                # Prevent matching "box id" with "new box id"
                is_cand_new = "new" in cand_lower
                is_k_new = "new" in k_lower
                if is_cand_new != is_k_new:
                    continue
                if cand_lower in k_lower and v is not None and str(v).strip() != "":
                    return str(v).strip()

    return default

def load_boxes_from_csv(csv_path):
    """Parses exported CSV file into normalized box dictionary list."""
    encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    content = None
    for enc in encodings:
        try:
            with open(csv_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, Exception):
            continue

    if content is None:
        raise ValueError(f"Could not read CSV file with supported encodings: {csv_path}")

    boxes = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        name = _extract_column(row, ["Box Name", "boxName", "box_name", "이름"])
        if not name:
            continue

        raw_id = _extract_column(row, ["Box ID", "boxId", "box_id", "ID"])
        try:
            box_id = int(raw_id) if raw_id.isdigit() else raw_id
        except Exception:
            box_id = raw_id

        raw_projects = _extract_column(row, [
            "연동 Jira 프로젝트 (Jira Projects)", "연동 Jira 프로젝트", "jira_projects",
            "projects", "Project key", "Projects", "연동 프로젝트"
        ])
        if raw_projects and raw_projects != "-":
            projects = [p.strip().upper() for p in re.split(r'[,; ]+', raw_projects) if p.strip()]
        else:
            projects = []

        box_type = _extract_column(row, ["Box Type", "boxType", "box_type", "유형"], default="Program")
        start_date = _extract_column(row, ["시작일 (Start Date)", "시작일", "boxStartDate", "box_start_date", "Start Date"])
        end_date = _extract_column(row, ["종료일 (End Date)", "종료일", "boxEndDate", "box_end_date", "End Date"])
        parent_id = _extract_column(row, ["상위 Box ID (Parent Box ID)", "상위 Box ID", "parentBoxId", "parent_id", "Parent Box ID"], default="ROOT")
        if parent_id in ("-", ""):
            parent_id = "ROOT"

        owner_name = _extract_column(row, ["소유자 (Owner Name)", "소유자", "owner_name", "Owner Name"])
        owner_email = _extract_column(row, ["소유자 이메일 (Owner Email)", "소유자 이메일", "owner_email", "Owner Email"])
        source_url = _extract_column(row, ["Source Base URL", "source_base_url", "sourceBaseUrl"])
        source_env = _extract_column(row, ["Source Environment", "source_environment", "sourceEnvironment"], default="STG")
        jql = _extract_column(row, ["Narrowing Query (JQL)", "JQL 설정 (Narrowing Query)", "narrowing_query", "JQL"])
        new_box_id = _extract_column(row, ["New Box ID", "newBoxId", "new_box_id"])

        box_entry = {
            "boxId": box_id,
            "boxName": name,
            "boxType": box_type,
            "boxStartDate": start_date or None,
            "boxEndDate": end_date or None,
            "parentBoxId": parent_id,
            "jira_projects": projects,
            "owner_name": owner_name or None,
            "owner_email": owner_email or None,
            "source_base_url": source_url or None,
            "source_environment": source_env or "STG",
            "narrowing_query": jql or None
        }
        if new_box_id:
            box_entry["newBoxId"] = new_box_id

        boxes.append(box_entry)

    return boxes

def save_migration_results(input_file, boxes):
    """Saves updated boxes with newBoxId to CSV and/or JSON."""
    is_csv = str(input_file).lower().endswith(".csv")
    if is_csv and os.path.isfile(input_file):
        try:
            encodings = ["utf-8-sig", "utf-8", "cp949"]
            rows = []
            headers = None
            for enc in encodings:
                try:
                    with open(input_file, "r", encoding=enc) as f:
                        reader = csv.DictReader(f)
                        headers = list(reader.fieldnames or [])
                        rows = list(reader)
                    break
                except Exception:
                    continue

            if not headers:
                headers = [
                    "Box ID", "Box Name", "Box Type", "시작일 (Start Date)",
                    "종료일 (End Date)", "소유자 (Owner Name)", "소유자 이메일 (Owner Email)",
                    "연동 Jira 프로젝트 (Jira Projects)", "상위 Box ID (Parent Box ID)",
                    "Narrowing Query (JQL)", "Source Base URL", "Source Environment", "New Box ID"
                ]

            new_id_key = next((h for h in headers if h.lower() in ("new box id", "newboxid", "new_box_id")), "New Box ID")
            if new_id_key not in headers:
                headers.append(new_id_key)

            box_map = {str(b.get("boxId")): b for b in boxes if b.get("boxId") is not None}
            box_name_map = {str(b.get("boxName")).strip(): b for b in boxes if b.get("boxName")}

            updated_rows = []
            for r in rows:
                row_copy = dict(r)
                bid = _extract_column(r, ["Box ID", "boxId", "box_id", "ID"])
                bname = _extract_column(r, ["Box Name", "boxName", "box_name", "이름"])
                matched_box = box_map.get(str(bid)) or box_name_map.get(str(bname).strip())
                if matched_box and matched_box.get("newBoxId"):
                    row_copy[new_id_key] = matched_box["newBoxId"]
                updated_rows.append(row_copy)

            with open(input_file, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(updated_rows)
            log(f"Successfully updated CSV file with newBoxId: {input_file}")
        except Exception as e:
            log(f"Error writing update to CSV file: {e}")

    try:
        with open(MIGRATION_BOX_FILE, "w", encoding="utf-8") as f:
            json.dump(boxes, f, indent=2, ensure_ascii=False)
        log("Successfully updated migration_box.json with newBoxId.")
    except Exception as e:
        log(f"Error writing update to migration_box.json: {e}")

def get_origin_dc_url(box=None):
    """Resolve the DC origin from Step 1 metadata, not Cloud target ENV."""
    box = box or {}
    source_url = box.get("source_base_url")
    if source_url:
        return str(source_url).rstrip("/")
    source_env = str(box.get("source_environment") or "").upper()
    if source_env == "OPS":
        return (os.getenv("JIRA_DC_OPS_URL") or "https://dev-jira.osci.kr").rstrip("/")
    if source_env == "STG":
        return (os.getenv("JIRA_DC_STG_URL") or "https://dev-jira.osci.kr").rstrip("/")
    # Backward compatibility for migration files generated before source
    # metadata was added. Do not infer STG from the Cloud target selection.
    return (os.getenv("JIRA_DC_STG_URL") or "https://dev-jira.osci.kr").rstrip("/")

def select_environment():
    for idx, arg in enumerate(sys.argv):
        if arg in ("--env", "-e") and idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].upper().strip()
    if __name__ != "__main__":
        return os.environ.get("TARGET_ENV", "SANDBOX")

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
    JIRA_CLOUD_URL = os.getenv("JIRA_CLOUD_OPS_URL")
    JIRA_CLOUD_EMAIL = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL")
    JIRA_CLOUD_TOKEN = os.getenv("JIRA_CLOUD_OPS_API_TOKEN")
    BP_API_TOKEN = os.getenv("JIRA_BIGGANTT_OPS_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
    BIGGANTT_BASE_URL = os.getenv("JIRA_CLOUD_OPS_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
    ENV_LABEL = "Production/Operations (OPS)"
else:
    JIRA_CLOUD_URL = os.getenv("JIRA_CLOUD_SANDBOX_URL")
    JIRA_CLOUD_EMAIL = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL")
    JIRA_CLOUD_TOKEN = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN")
    BP_API_TOKEN = os.getenv("JIRA_BIGGANTT_SANDBOX_API_TOKEN") or os.getenv("JIRA_BIGGANTT_API_TOKEN")
    BIGGANTT_BASE_URL = os.getenv("JIRA_CLOUD_SANDBOX_BIGGANTT_BASE_URL") or os.getenv("JIRA_CLOUD_BIGGANTT_BASE_URL")
    ENV_LABEL = "Sandbox (SANDBOX)"

# Load scopeOwnerKey from environment
SCOPE_OWNER_KEY = os.getenv("SCOPE_OWNER_KEY") or os.getenv("BG_SCOPE_OWNER_KEY") or os.getenv("FALLBACK_OWNER_ACCOUNT_ID")

def _bool_env(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")

# Many of the migration environments use an internal/self-signed certificate.
# Keep that deployment-compatible by default; set JIRA_SSL_VERIFY=True (and
# preferably JIRA_CA_BUNDLE) to enable certificate verification explicitly.
JIRA_SSL_VERIFY = _bool_env("JIRA_SSL_VERIFY", False)
JIRA_CA_BUNDLE = os.getenv("JIRA_CA_BUNDLE") or None
BP_SSL_VERIFY = _bool_env("BIGPICTURE_SSL_VERIFY", JIRA_SSL_VERIFY)

if __name__ == "__main__":
    if not BP_API_TOKEN:
        print(f"Error: Missing JIRA_BIGGANTT_{ENV}_API_TOKEN (or JIRA_BIGGANTT_API_TOKEN) in env file.")
        exit(1)

    if not JIRA_CLOUD_URL or not JIRA_CLOUD_EMAIL or not JIRA_CLOUD_TOKEN:
        print(f"Error: Missing JIRA Cloud configuration variables (URL, EMAIL, or API_TOKEN) for {ENV_LABEL} in env file.")
        exit(1)

    if not SCOPE_OWNER_KEY:
        print("Error: Missing SCOPE_OWNER_KEY (or BG_SCOPE_OWNER_KEY / FALLBACK_OWNER_ACCOUNT_ID) in env file.")
        exit(1)

    print(f"Target Environment: {ENV_LABEL}")
    print(f"Environment file: {ENV_FILE}")
    print(f"Jira SSL verification: {JIRA_SSL_VERIFY}{f' (CA: {JIRA_CA_BUNDLE})' if JIRA_CA_BUNDLE else ''}")
    print(f"BigPicture SSL verification: {BP_SSL_VERIFY}")
    if not JIRA_SSL_VERIFY:
        print("WARNING: Jira SSL certificate verification is disabled (use JIRA_CA_BUNDLE for a safer setup).")

# File path for execution logging
LOG_FILE = os.path.join(PROJECT_ROOT, os.path.basename(__file__).replace(".py", ".log"))

def write_html_report(results):
    """Write a side-by-side origin/migrated migration report with a dedicated failure section."""
    cloud_url = (JIRA_CLOUD_URL or "").rstrip("/")
    custom_cloud_base = BIGGANTT_BASE_URL

    total_count = len(results)
    success_count = sum(1 for r in results if r.get("status") == "SUCCESS")
    create_failed_items = [r for r in results if r.get("status") == "CREATE FAILED"]
    scope_failed_items = [r for r in results if r.get("status") == "BOX CREATED / SCOPE FAILED"]
    skipped_items = [r for r in results if "SKIPPED" in str(r.get("status", ""))]

    # Generate Failed Boxes Section HTML
    if create_failed_items:
        failed_rows = []
        for f_idx, item in enumerate(create_failed_items, 1):
            fbox = item["box"]
            f_orig_id = str(fbox.get("boxId") or "-")
            f_name = str(fbox.get("boxName") or "-")
            f_type = str(fbox.get("boxType") or "-")
            f_proj = ", ".join(str(v) for v in (fbox.get("jira_projects") or [])) or "-"
            f_owner = str(fbox.get("owner_name") or fbox.get("owner_email") or "-")
            f_dc_url = get_origin_dc_url(fbox)
            f_href = f"{f_dc_url}/plugins/servlet/softwareplant-biggantt/#/box/{escape(f_orig_id)}/g"
            f_error = str(item.get("error_detail") or "Box creation failed after retry.")

            failed_rows.append(f"""
            <tr>
                <td style="text-align:center;font-weight:bold;">{f_idx}</td>
                <td><a href="{f_href}" target="_blank"><b>{escape(f_name)}</b></a><br><small style="color:#666;">DC ID: {escape(f_orig_id)}</small></td>
                <td>{escape(f_type)}</td>
                <td>{escape(f_proj)}</td>
                <td>{escape(f_owner)}</td>
                <td><div class="error-reason">{escape(f_error)}</div></td>
            </tr>
            """)

        failed_section_html = f"""
        <div class="failed-section">
            <h2>⚠️ Box 생성 실패 내역 ({len(create_failed_items)}건)</h2>
            <p style="margin: 0 0 12px 0; color: #555; font-size: 13px;">
                아래 Box들은 Cloud API 호출 중 오류가 발생하여 생성이 완료되지 못했습니다. 스크립트는 중단되지 않고 다음 Box 생성을 계속 진행하였으며, 실패 상세 내용은 아래와 같습니다.
            </p>
            <table class="failed-table">
                <thead>
                    <tr>
                        <th style="width:40px;text-align:center;">#</th>
                        <th>Box 이름 (ID)</th>
                        <th>유형</th>
                        <th>연동 Jira 프로젝트</th>
                        <th>소유자</th>
                        <th>실패 원인 (Error Detail)</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(failed_rows)}
                </tbody>
            </table>
        </div>
        """
    else:
        failed_section_html = """
        <div class="success-banner">
            ✓ <b>Box 생성 실패 건이 없습니다.</b> 모든 대상 Box가 정상적으로 처리되었습니다.
        </div>
        """

    # Generate Scope Failed Section HTML (if any)
    scope_failed_section_html = ""
    if scope_failed_items:
        sf_rows = []
        for sf_idx, item in enumerate(scope_failed_items, 1):
            sbox = item["box"]
            sf_name = str(sbox.get("boxName") or "-")
            sf_new_id = str(item.get("new_box_id") or "-")
            sf_error = str(item.get("error_detail") or "Scope configuration failed")
            sf_rows.append(f"<li><b>{escape(sf_name)}</b> (Cloud ID: {escape(sf_new_id)}) - {escape(sf_error)}</li>")
        scope_failed_section_html = f"""
        <div class="warning-banner">
            <b>⚠️ Box는 생성되었으나 Scope(프로젝트/소유자) 설정에 실패한 건이 있습니다 ({len(scope_failed_items)}건):</b>
            <ul style="margin: 8px 0 0 20px; padding: 0;">
                {''.join(sf_rows)}
            </ul>
        </div>
        """

    # Main Migration Results Table Rows
    rows = []
    for idx, item in enumerate(results, start=1):
        box = item["box"]
        original_id = str(box.get("boxId") or "")
        migrated_id = str(item.get("new_box_id") or "")
        projects = ", ".join(str(v) for v in (box.get("jira_projects") or [])) or "-"
        origin_owner = box.get("owner_name") or box.get("owner_email") or "-"
        migrated_owner = item.get("migrated_owner") or SCOPE_OWNER_KEY or "-"
        dc_url = get_origin_dc_url(box)
        status = str(item.get("status") or "-")
        error_detail = item.get("error_detail")

        # Jira DC Box URL
        origin_href = f"{dc_url}/plugins/servlet/softwareplant-biggantt/#/box/{escape(original_id)}/g"

        # Jira Cloud Box URL
        if migrated_id:
            if custom_cloud_base:
                migrated_href = f"{custom_cloud_base.rstrip('/')}/{escape(migrated_id)}"
            else:
                migrated_href = f"{cloud_url}/jira/apps/eu.softwareplant.biggantt/#/box/{escape(migrated_id)}"
        else:
            migrated_href = ""

        # Status Badge & Row CSS class
        row_class = ""
        if status == "SUCCESS":
            badge_html = '<span class="badge badge-success">SUCCESS</span>'
        elif status == "CREATE FAILED":
            badge_html = '<span class="badge badge-danger">CREATE FAILED</span>'
            row_class = 'class="row-failed"'
        elif status == "BOX CREATED / SCOPE FAILED":
            badge_html = '<span class="badge badge-warning">BOX CREATED / SCOPE FAILED</span>'
            row_class = 'class="row-scope-failed"'
        elif "SKIPPED" in status:
            badge_html = f'<span class="badge badge-secondary">{escape(status)}</span>'
        else:
            badge_html = f'<span class="badge badge-secondary">{escape(status)}</span>'

        origin = f'''<div class="card"><div><b>Box URL</b><br><a href="{origin_href}" target="_blank">{escape(origin_href)}</a></div><div><b>Box name</b><br>{escape(str(box.get("boxName") or "-"))}</div><div><b>Project key</b><br>{escape(projects)}</div><div><b>Scope owner</b><br>{escape(str(origin_owner))}</div></div>'''

        migrated_url = f'<a href="{migrated_href}" target="_blank">{escape(migrated_href)}</a>' if migrated_href else "-"
        error_html = f'<div class="error-reason" style="margin-top:6px;"><b>Failure Reason:</b> {escape(str(error_detail))}</div>' if error_detail else ""
        migrated = f'''<div class="card"><div><b>Box URL</b><br>{migrated_url}</div><div><b>Box name</b><br>{escape(str(box.get("boxName") or "-"))}</div><div><b>Project key</b><br>{escape(projects)}</div><div><b>Scope owner</b><br>{escape(str(migrated_owner))}</div><div><b>Status</b><br>{badge_html}</div>{error_html}</div>'''

        rows.append(f"<tr {row_class}><td style='text-align:center;font-weight:bold;width:40px;'>{idx}</td><td>{origin}</td><td>{migrated}</td></tr>")

    html = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Step 2 Import Cloud Boxes Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 24px; background: #f8f9fa; color: #222; }}
.container {{ max-width: 1400px; margin: 0 auto; background: #fff; padding: 28px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
h1 {{ font-size: 22px; color: #17365d; margin-top: 0; }}
.meta-info {{ font-size: 13px; color: #666; margin-bottom: 20px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.stat-card {{ background: #fdfdfd; border: 1px solid #e0e0e0; border-radius: 6px; padding: 14px; text-align: center; }}
.stat-card .num {{ font-size: 24px; font-weight: bold; margin-bottom: 4px; }}
.stat-card .label {{ font-size: 12px; color: #666; }}
.stat-card.total .num {{ color: #17365d; }}
.stat-card.success .num {{ color: #28a745; }}
.stat-card.danger {{ border-color: #f5c6cb; background-color: #fff9f9; }}
.stat-card.danger .num {{ color: #dc3545; }}
.stat-card.warning {{ border-color: #ffeeba; background-color: #fffdf5; }}
.stat-card.warning .num {{ color: #e67e22; }}
.stat-card.gray .num {{ color: #6c757d; }}

.badge {{ display: inline-block; padding: 3px 8px; font-size: 11px; font-weight: bold; border-radius: 4px; }}
.badge-success {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
.badge-danger {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
.badge-warning {{ background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }}
.badge-secondary {{ background: #e2e3e5; color: #383d41; border: 1px solid #d6d8db; }}

.failed-section {{ border: 1px solid #dc3545; background: #fffafa; border-radius: 6px; padding: 18px; margin-bottom: 24px; }}
.failed-section h2 {{ color: #dc3545; font-size: 16px; margin-top: 0; margin-bottom: 8px; }}
.failed-table {{ width: 100%; border-collapse: collapse; background: #fff; margin-top: 10px; font-size: 13px; }}
.failed-table th {{ background: #dc3545; color: white; text-align: left; padding: 8px 10px; }}
.failed-table td {{ padding: 8px 10px; border: 1px solid #f5c6cb; vertical-align: top; }}

.success-banner {{ background: #e8f5e9; border: 1px solid #c8e6c9; color: #2e7d32; padding: 12px 16px; border-radius: 6px; margin-bottom: 24px; font-size: 14px; }}
.warning-banner {{ background: #fff8e1; border: 1px solid #ffe082; color: #f57f17; padding: 12px 16px; border-radius: 6px; margin-bottom: 24px; font-size: 13px; }}

.error-reason {{ color: #c82333; font-family: monospace; font-size: 12px; word-break: break-all; background: #fff0f0; padding: 6px 8px; border-radius: 4px; border-left: 3px solid #dc3545; }}

table.main-table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-top: 10px; }}
table.main-table th, table.main-table td {{ border: 1px solid #dee2e6; padding: 12px; vertical-align: top; }}
table.main-table th {{ background: #17365d; color: white; font-size: 15px; }}
table.main-table tr:nth-child(even) {{ background: #f7f9fc; }}
table.main-table tr.row-failed {{ background: #fff5f5; }}
table.main-table tr.row-scope-failed {{ background: #fffdf5; }}
.card div {{ margin: 0 0 8px; font-size: 13px; }}
a {{ word-break: break-all; color: #075aa6; }}
</style>
</head>
<body>
<div class="container">
<h1>Step 2 Import Cloud Boxes Report</h1>
<div class="meta-info">
  Generated: {escape(str(datetime.now()))} | Target: {escape(ENV_LABEL)} | Base URL: {escape(str(JIRA_CLOUD_URL or ''))}
</div>

<div class="stats-grid">
  <div class="stat-card total"><div class="num">{total_count}</div><div class="label">Total Processed</div></div>
  <div class="stat-card success"><div class="num">{success_count}</div><div class="label">Success</div></div>
  <div class="stat-card danger"><div class="num">{len(create_failed_items)}</div><div class="label">Creation Failed</div></div>
  <div class="stat-card warning"><div class="num">{len(scope_failed_items)}</div><div class="label">Scope Failed</div></div>
  <div class="stat-card gray"><div class="num">{len(skipped_items)}</div><div class="label">Skipped (Duplicate)</div></div>
</div>

{failed_section_html}
{scope_failed_section_html}

<h2 style="font-size: 17px; color: #17365d; margin: 24px 0 8px 0;">All Processed Boxes</h2>
<table class="main-table">
  <thead>
    <tr>
      <th style="width:40px;">#</th>
      <th>Origin (DC)</th>
      <th>Migrated (Cloud)</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows) or '<tr><td colspan="3">No boxes processed.</td></tr>'}
  </tbody>
</table>
</div>
</body>
</html>'''
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"HTML report saved to: {REPORT_FILE}")

def log(message):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

# Caches to avoid redundant network calls
project_id_cache = {}
user_account_id_cache = {}

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

def parse_date_to_iso(date_str):
    """Converts a date string (like '25/JUL/24' or ISO formats) into 'YYYY-MM-DD'."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    import re
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return date_str[:10]
    
    parts = date_str.split('/')
    if len(parts) == 3:
        try:
            day = int(parts[0])
            month_str = parts[1].upper()
            year_short = int(parts[2])
            month = MONTHS.get(month_str, 1)
            year = 2000 + year_short if year_short < 80 else 1900 + year_short
            return f"{year:04d}-{month:02d}-{day:02d}"
        except Exception:
            pass
    return date_str

def get_jira_project_id(project_key):
    """Retrieves the Jira Cloud Project ID (numeric string) using the project key."""
    if not project_key:
        return None
    project_key = project_key.upper()
    if project_key in project_id_cache:
        return project_id_cache[project_key]
    
    url = f"{JIRA_CLOUD_URL.rstrip('/')}/rest/api/3/project/{project_key}"
    try:
        response = session.get(
            url,
            auth=HTTPBasicAuth(JIRA_CLOUD_EMAIL, JIRA_CLOUD_TOKEN),
            headers={"Accept": "application/json"},
            verify=JIRA_CA_BUNDLE or JIRA_SSL_VERIFY,
            timeout=(15, 60)
        )
        if response.status_code == 200:
            data = response.json()
            project_id = data.get("id")
            if project_id:
                project_id_cache[project_key] = project_id
                return project_id
        log(f"  Warning: Failed to fetch Jira project ID for key '{project_key}'. HTTP Status: {response.status_code}")
    except Exception as e:
        log(f"  Exception fetching Jira project ID for key '{project_key}': {e}")
    return None

def get_jira_cloud_account_id(email):
    """Searches Jira Cloud for the account ID matching the user's email."""
    if not email:
        return None
    email = email.strip()
    if email in user_account_id_cache:
        return user_account_id_cache[email]
    
    search_url = f"{JIRA_CLOUD_URL.rstrip('/')}/rest/api/3/user/search?query={email}"
    try:
        response = session.get(
            search_url,
            auth=HTTPBasicAuth(JIRA_CLOUD_EMAIL, JIRA_CLOUD_TOKEN),
            headers={"Accept": "application/json"},
            verify=JIRA_CA_BUNDLE or JIRA_SSL_VERIFY,
            timeout=(15, 60)
        )
        if response.status_code == 200:
            users = response.json()
            if users:
                account_id = users[0].get("accountId")
                if account_id:
                    user_account_id_cache[email] = account_id
                    return account_id
        log(f"  Warning: User account ID not resolved for email '{email}'. HTTP Status: {response.status_code}")
    except Exception as e:
        log(f"  Exception searching user account ID for '{email}': {e}")
    return None

def get_bp_headers():
    """Generates authentication headers for the BigPicture API."""
    return {
        "Authorization": f"APIToken {BP_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def get_box_type_id(box_type):
    """Maps human-readable box type names to the BigPicture Box Type IDs."""
    box_type_str = str(box_type).strip().lower()
    if box_type_str in ("program", "2"):
        return "2"
    elif box_type_str in ("agile", "8"):
        return "8"
    return str(box_type)

def create_box_api(box, dry_run=False):
    """Creates a new Box in BigPicture Cloud using the REST API with 1 retry on failure."""
    box_name = box.get("boxName")
    box_type = box.get("boxType")
    box_start_date = parse_date_to_iso(box.get("boxStartDate"))
    box_end_date = parse_date_to_iso(box.get("boxEndDate"))
    
    dc_box_id = box.get("boxId")
    owner_email = box.get("owner_email")
    owner_name = box.get("owner_name")
    
    payload = {
        "parentBoxId": box.get("parentBoxId") or "ROOT",
        "boxTypeId": get_box_type_id(box_type),
        "boxName": box_name,
        "boxStartDate": box_start_date,
        "boxEndDate": box_end_date,
        "boxIconId": "font-awesome-box-open@VIOLET"
    }
    
    url = f"{BP_BASE_URL}/public/ppm/box"
    
    if dry_run:
        log(f"  [DRY RUN] Would POST to: {url}")
        log(f"  Payload: {json.dumps(payload, indent=2)}")
        return "DRY_RUN_BOX_ID", None
        
    max_attempts = 2  # 1 initial try + 1 retry
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                log(f"  Sending Create Box API request for '{box_name}'...")
            else:
                log(f"  [RETRY 1/1] Re-attempting Create Box API request for '{box_name}' after failure...")

            response = session.post(url, headers=get_bp_headers(), json=payload,
                                    verify=BP_SSL_VERIFY, timeout=(15, 60))
            if response.status_code in (200, 201):
                res_data = response.json()
                log(f"  Response JSON: {json.dumps(res_data)}")
                
                # Try to get the ID from different possible keys
                created_id = res_data.get("boxId") or res_data.get("id") or res_data.get("createdBoxId")
                if not created_id:
                    # Fallback to search inside a 'cargo' wrapper if present
                    cargo = res_data.get("cargo", {})
                    if isinstance(cargo, dict):
                        created_id = cargo.get("createdBoxId") or cargo.get("boxId") or cargo.get("id")
                        
                if created_id:
                    log(f"  -> Successfully created box! New Box ID: {created_id}")
                    return created_id, None
                else:
                    last_error = f"HTTP {response.status_code} received but could not extract Box ID from response: {response.text[:200]}"
                    log(f"  Warning: {last_error}")
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:300]}"
                log(f"  Failed to create box (Attempt {attempt}/{max_attempts}). Response Code: {response.status_code}. Response: {response.text}")
        except Exception as e:
            last_error = f"Exception: {str(e)}"
            log(f"  Exception during Create Box API (Attempt {attempt}/{max_attempts}): {e}")

        if attempt < max_attempts:
            log(f"  Waiting 2 seconds before retry...")
            time.sleep(2)

    log(f"  [ERROR] All {max_attempts} attempts to create box '{box_name}' failed.")
    return None, last_error or "Unknown failure"

def get_current_scope_definition(box_id):
    """Read the server-supported owner/task types before constructing a PUT."""
    url = f"{BP_BASE_URL}/public/ppm/box/area/task/scope/def/own/{box_id}"
    try:
        response = session.get(url, headers=get_bp_headers(), verify=BP_SSL_VERIFY, timeout=(15, 60))
        if response.status_code == 200:
            return response.json()
        log(f"  Warning: Could not read current scope definition (HTTP {response.status_code}): {response.text[:500]}")
    except Exception as e:
        log(f"  Warning: Exception reading current scope definition: {e}")
    return None

def _scope_platform_metadata(scope_data):
    platforms = (scope_data or {}).get("extPlatformScopeDefinitions") or []
    platform = next((p for p in platforms if p.get("extPlatformId") == 2), None)
    if platform is None and platforms:
        platform = platforms[0]
    if not platform:
        return 2, SCOPE_OWNER_KEY, [2, 3], None, None
    owner = SCOPE_OWNER_KEY or ((platform.get("scopeOwner") or {}).get("key"))
    enabled = [t.get("key") for t in platform.get("taskTypes", [])
               if t.get("isEnabled") and t.get("key") is not None]
    if not enabled:
        enabled = [2, 3]
        log("  Warning: API returned no enabled task types; using fallback [2, 3].")
    return platform.get("extPlatformId", 2), owner, enabled, platform.get("narrowingQuery"), platform.get("manuallyAddedTasks")

def build_scope_payload(project_ids, scope_data=None):
    """Build and validate the request required by updateOwnScopeDefinition."""
    if not project_ids:
        raise ValueError("At least one numeric Jira project ID is required")
    ext_platform_id, owner, enabled, narrowing_query, manually_added = _scope_platform_metadata(scope_data)
    if not owner:
        raise ValueError("scopeOwnerKey is missing")
    if not enabled:
        raise ValueError("enabledTaskTypeKeys is empty")
    platform_payload = {
        "extPlatformId": ext_platform_id,
        "scopeOwnerKey": owner,
        "enabledTaskTypeKeys": enabled,
        "scopeDefinitionElements": [{"type": "JIRA_PROJECT", "values": [str(v) for v in project_ids]}]
    }
    # Omitting these fields deletes their current values according to the API.
    if narrowing_query is not None:
        platform_payload["narrowingQuery"] = narrowing_query
    if manually_added is not None:
        platform_payload["manuallyAddedTasks"] = [
            t.get("id") if isinstance(t, dict) else t for t in manually_added
        ]
    return {"extPlatformScopeDefinitions": [platform_payload]}

def update_scope_definition_api(new_box_id, box, dry_run=False):
    """Updates the Task Scope Definition and Box Owner via BigPicture API."""
    jira_projects = box.get("jira_projects", [])
    
    # Retrieve Jira Cloud numeric Project IDs using the project keys
    project_ids = []
    for key in jira_projects:
        proj_id = get_jira_project_id(key)
        if proj_id:
            project_ids.append(proj_id)
        else:
            log(f"  Warning: Could not resolve project ID for key '{key}'")
            
    if not project_ids:
        log(f"  Warning: No Jira Project IDs resolved from keys: {jira_projects}. Skipping Scope definition setup.")
        return False
        
    scope_data = get_current_scope_definition(new_box_id) if not dry_run else None
    try:
        payload = build_scope_payload(project_ids, scope_data)
    except ValueError as e:
        log(f"  Error: Invalid scope definition payload: {e}")
        return False
    platform_payload = payload["extPlatformScopeDefinitions"][0]
    log(f"  Using scope metadata: extPlatformId={platform_payload['extPlatformId']}, scopeOwnerKey='{platform_payload['scopeOwnerKey']}', enabledTaskTypeKeys={platform_payload['enabledTaskTypeKeys']}")
    
    url = f"{BP_BASE_URL}/public/ppm/box/area/task/scope/def/own/{new_box_id}"
    
    if dry_run:
        log(f"  [DRY RUN] Would PUT to: {url}")
        log(f"  Payload: {json.dumps(payload, indent=2)}")
        return True
        
    try:
        log(f"  Updating scope definition for Box '{new_box_id}'...")
        response = session.put(url, headers=get_bp_headers(), json=payload,
                               verify=BP_SSL_VERIFY, timeout=(15, 60))
        if response.status_code in (200, 204):
            log(f"  -> Successfully updated Scope Definition! (Projects: {jira_projects}, Owner Key: {platform_payload['scopeOwnerKey']})")
            return True
        else:
            detail = response.text[:1000]
            try:
                error = response.json()
                messages = error.get("errorMessages") or []
                if messages:
                    detail = "; ".join(messages)
            except ValueError:
                pass
            log(f"  Failed to update scope definition. Response Code: {response.status_code}. Detail: {detail}")
            return False
    except Exception as e:
        log(f"  Exception during Scope Definition Update API: {e}")
        return False

def select_dry_run():
    args = {arg.lower().strip() for arg in sys.argv[1:]}
    if "--dry-run" in args or "-n" in args:
        return True
    if "--no-dry-run" in args or "--live" in args:
        return False
    # Preserve the original positional usage: script.py y|n
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if positional:
        return positional[0].lower() in ('y', 'yes', 'true')
    choice = input("Dry-run mode? (y/n): ").lower().strip()
    return choice == 'y'

def is_test_one_mode():
    """Return True when only one migration item should be processed."""
    return "--test-one" in {arg.lower().strip() for arg in sys.argv[1:]}

def main():
    # Clear / Initialize log file
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"--- New Migration Session Started: {datetime.now()} ---\n")

    log("="*80)
    log(" [Step 2: Import Cloud Boxes - REST API Mode] (Jira Cloud Only)")
    log("="*80)

    dry_run = select_dry_run()
    if dry_run:
        log("\n*** DRY RUN MODE ENABLED: No modifications will be written to Jira/BigPicture. ***\n")

    input_file = resolve_input_file()
    is_csv_source = str(input_file).lower().endswith(".csv")
    file_type_label = "CSV" if is_csv_source else "JSON"

    log(f"Input file resolved: '{input_file}' ({file_type_label} format)")

    try:
        if is_csv_source:
            boxes = load_boxes_from_csv(input_file)
        else:
            with open(input_file, "r", encoding="utf-8") as f:
                boxes = json.load(f)
    except Exception as e:
        log(f"Error loading migration boxes from '{input_file}': {e}")
        log("Run step1_export_dc_boxes.py, export CSV from step1_box_report.html, or place migration_box.json beside this script.")
        return

    test_one = is_test_one_mode()
    if test_one:
        if not boxes:
            log(f"Test-one mode requested, but '{os.path.basename(input_file)}' contains no boxes.")
            return
        log(f"TEST-ONE MODE: limiting this run to the first box: '{boxes[0].get('boxName')}'")
        boxes = boxes[:1]
    log(f"Loaded {len(boxes)} boxes from {os.path.basename(input_file)}")
    
    user_input = input("Proceed with box creation API calls? (y/n): ").lower().strip()
    if user_input not in ('yes', 'y'):
        log("Aborted by user.")
        return

    success_count = 0
    fail_count = 0
    skipped_dup_count = 0
    created_boxes_details = []
    report_results = []
    seen_box_names = set()

    for index, box in enumerate(boxes, 1):
        box_name = str(box.get("boxName") or "").strip()
        box_type = box.get("boxType")
        dc_box_id = box.get("boxId")
        log(f"\n[{index}/{len(boxes)}] Processing: '{box_name}' ({box_type}, DC ID: {dc_box_id})")

        # Skip duplicate boxNames (import only 1 per unique boxName)
        if box_name in seen_box_names:
            log(f"  [SKIPPED (DUPLICATE)] Box name '{box_name}' already processed. Skipping import.")
            skipped_dup_count += 1
            report_results.append({"box": box, "new_box_id": None, "migrated_owner": SCOPE_OWNER_KEY, "status": "SKIPPED (DUPLICATE)"})
            continue

        seen_box_names.add(box_name)

        # 1. Create Box via API.  If a previous run created the box but failed
        # during scope setup, verify if it still exists before retrying scope setup.
        new_box_id = box.get("newBoxId")
        create_error = None
        if new_box_id and not dry_run:
            log(f"  Existing newBoxId found ({new_box_id}); verifying existence in Cloud...")
            scope_check = get_current_scope_definition(new_box_id)
            if scope_check is None:
                log(f"  Warning: Box '{new_box_id}' not found in Cloud (possibly deleted). Re-creating box...")
                new_box_id, create_error = create_box_api(box, dry_run=dry_run)
            else:
                log(f"  Box '{new_box_id}' verified in Cloud. Skipping creation and proceeding with scope setup.")
        else:
            new_box_id, create_error = create_box_api(box, dry_run=dry_run)
        
        # If box creation failed after retry, do NOT halt; continue to next box as requested
        if not new_box_id and not dry_run:
            log("\n" + "!" * 80)
            log(f"  [ERROR] Box creation failed for '{box_name}' after retry.")
            log(f"  Reason: {create_error or 'Unknown API failure'}")
            log("  Continuing to next box as requested (recorded in report).")
            log("!" * 80 + "\n")
            report_results.append({
                "box": box,
                "new_box_id": None,
                "migrated_owner": SCOPE_OWNER_KEY,
                "status": "CREATE FAILED",
                "error_detail": create_error or "Box creation failed after retry"
            })
            continue

        if new_box_id:
            box["newBoxId"] = new_box_id
            box["target_environment"] = ENV
            box["target_base_url"] = JIRA_CLOUD_URL
            if BIGGANTT_BASE_URL:
                box["target_biggantt_base_url"] = BIGGANTT_BASE_URL
            
            # 2. Update Scope Definition (Jira projects and owner assignment)
            scope_updated = update_scope_definition_api(new_box_id, box, dry_run=dry_run)
            
            if scope_updated or dry_run:
                created_boxes_details.append((box_name, dc_box_id, new_box_id))
                report_results.append({
                    "box": box,
                    "new_box_id": new_box_id,
                    "migrated_owner": SCOPE_OWNER_KEY,
                    "status": "SUCCESS"
                })
            else:
                log(f"  Warning: Box created but scope definition configuration failed for '{box_name}'.")
                report_results.append({
                    "box": box,
                    "new_box_id": new_box_id,
                    "migrated_owner": SCOPE_OWNER_KEY,
                    "status": "BOX CREATED / SCOPE FAILED",
                    "error_detail": "Box created, but scope definition (Jira projects/owner) update failed."
                })

        # Small throttling delay
        time.sleep(0.5)

    success_count = sum(1 for r in report_results if r.get("status") == "SUCCESS")
    create_fail_count = sum(1 for r in report_results if r.get("status") == "CREATE FAILED")
    scope_fail_count = sum(1 for r in report_results if r.get("status") == "BOX CREATED / SCOPE FAILED")
    fail_count = create_fail_count + scope_fail_count

    log("\n" + "="*50)
    log("BOX CREATION API SUMMARY")
    log("="*50)
    log(f"Total boxes loaded: {len(boxes)}")
    log(f"Skipped duplicate boxes: {skipped_dup_count}")
    log(f"Unique boxes processed: {len(seen_box_names)}")
    log(f"Successfully processed: {success_count}")
    log(f"Box creation failed: {create_fail_count}")
    log(f"Scope update failed: {scope_fail_count}")
    log(f"Total failures / warnings: {fail_count}")
    try:
        write_html_report(report_results)
    except Exception as e:
        log(f"Warning: Could not write HTML report: {e}")
    
    if created_boxes_details:
        log("-" * 50)
        log("Created Boxes Details & URLs:")
        for name, original_id, new_id in created_boxes_details:
            source_box = next((r["box"] for r in report_results if r.get("new_box_id") == new_id), {})
            dc_url = get_origin_dc_url(source_box)
            source_url = f"{dc_url}/secure/BigPicture.jspa?boxId={original_id}#box/{original_id}"
            target_url = f"{JIRA_CLOUD_URL.rstrip('/')}/plugins/servlet/ac/com.softwareplant.biggantt/#/box/{new_id}"
            log(f"\n* {name} (Original ID: {original_id} -> New ID: {new_id})")
            log(f"  Source URL: {source_url}")
            log(f"  Target URL: {target_url}")
            
    log("="*50)

    if not dry_run:
        save_migration_results(input_file, boxes)

if __name__ == "__main__":
    main()
