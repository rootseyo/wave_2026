#!/usr/bin/env python3
import os
import sys
import time
import csv
import re
import html
import getpass
import urllib3
import requests
from dotenv import load_dotenv
from datetime import datetime

def setup_environment_file(filename="./env"):
    """Load ./env, creating an empty settings file when it does not exist."""
    if os.path.isfile(filename):
        load_dotenv(dotenv_path=filename)
        print(f"Loaded environment variables from '{filename}'.")
        return filename

    try:
        with open(filename, "w", encoding="utf-8") as env_file:
            env_file.write("# Jira / Confluence ID mappings configuration\n")
        os.chmod(filename, 0o600)
        print(f"'{filename}' 파일을 만들었습니다. 선택한 작업에 필요한 설정을 입력받아 추가합니다.")
        load_dotenv(dotenv_path=filename, override=True)
        return filename
    except OSError as error:
        print(f"환경 설정 파일을 만들지 못했습니다: {error}")
        sys.exit(1)

def save_environment_values(filename, values):
    """Append prompted settings to ./env without exposing secrets in the terminal."""
    with open(filename, "a", encoding="utf-8") as env_file:
        for key, value in values.items():
            escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            env_file.write(f'{key}="{escaped}"\n')
    try:
        os.chmod(filename, 0o600)
    except OSError:
        pass
    load_dotenv(dotenv_path=filename, override=True)

def ensure_environment_values(required):
    """Prompt for missing settings and append them to the existing ./env file."""
    missing = [(key, label, secret) for key, label, secret in required if not os.getenv(key)]
    if not missing:
        return True
    print("필요한 설정을 입력받아 ./env에 추가합니다. API Token은 입력 중 표시되지 않습니다.")
    entered = {}
    try:
        for key, label, secret in missing:
            ask = getpass.getpass if secret else input
            entered[key] = ask(f"{label}: ").strip()
            if not entered[key]:
                raise ValueError(f"{label}은(는) 필수 입력입니다.")
        save_environment_values(ENV_FILE, entered)
        print("설정을 ./env에 추가했습니다.")
        return True
    except (EOFError, KeyboardInterrupt, ValueError, OSError) as error:
        log(f"Unable to save settings: {error}")
        return False


ENV_FILE = setup_environment_file()

# Disable SSL verification warnings by default for enterprise/on-prem environments
VERIFY_SSL = os.getenv("JIRA_SSL_VERIFY", os.getenv("VERIFY_SSL", "False")).lower() in ("true", "1", "yes")
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[{timestamp}] {message}"
    print(formatted_message)
    log_file = "fetch_id_mappings.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(formatted_message + "\n")
    except Exception as e:
        print(f"Error writing to log file: {e}")

def is_valid_csv_file(filename):
    """Return True only when filename is a non-empty CSV with a header row."""
    try:
        if not os.path.isfile(filename) or os.path.getsize(filename) == 0:
            return False
        with open(filename, mode="r", encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), None)
        return bool(header and any(column.strip() for column in header))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        log(f"CSV validation failed for '{filename}': {e}")
        return False

def save_mapping_download(response, output_filename):
    """Atomically replace the mapping CSV only after a complete, valid download."""
    temporary_filename = f"{output_filename}.part"
    try:
        with open(temporary_filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if not is_valid_csv_file(temporary_filename):
            log("Downloaded ID mappings response is empty or is not a valid CSV. Keeping the existing CSV unchanged.")
            return False

        os.replace(temporary_filename, output_filename)
        log(f"Successfully downloaded and saved mappings to '{output_filename}'.")
        return True
    except (OSError, requests.exceptions.RequestException) as e:
        log(f"Error downloading ID mappings: {e}. Keeping the existing CSV unchanged.")
        return False
    finally:
        if os.path.exists(temporary_filename):
            try:
                os.remove(temporary_filename)
            except OSError as e:
                log(f"Unable to remove temporary download file '{temporary_filename}': {e}")

def select_environment():
    print("\nSelect Source Environment (소스 환경 선택):")
    print("1. STG")
    print("2. OPS")
    choice = input("Enter choice (1/2, default 1): ").strip()
    if choice == '2':
        return "OPS"
    return "STG"

def select_product():
    print("\n어떤 매핑 정보를 추출할까요?")
    print("1. Jira mapping")
    print("2. Confluence mapping")
    return "CONFLUENCE" if input("선택 (1/2, 기본값 1): ").strip() == "2" else "JIRA"

def select_target_cloud():
    print("\nSelect Target Cloud Environment (타겟 Cloud 선택):")
    print("1. SANDBOX")
    print("2. OPS")
    choice = input("Enter choice (1/2, default 1): ").strip()
    if choice == '2':
        return "OPS"
    return "SANDBOX"

def select_execution_mode():
    print("\nSelect Execution Mode (실행 모드 선택):")
    print("1. Download Only (기존 캐시된 ID 맵핑 다운로드만 진행)")
    print("2. Request Generation & Wait (새로운 생성 요청 및 다운로드 대기 - 2분 간격 확인)")
    choice = input("Enter choice (1/2, default 1): ").strip()
    if choice == '2':
        return 2
    return 1

def run_confluence_id_mappings():
    """Trigger, poll, and download Confluence mappings through Atlassian's migration API."""
    env = select_environment()
    cloud_env = select_target_cloud()
    suffix = "OPS" if env == "OPS" else "STG"
    cloud_suffix = "OPS" if cloud_env == "OPS" else "SANDBOX"
    source_url = os.getenv(f"CONFLUENCE_DC_{suffix}_URL")
    destination_url = os.getenv(f"CONFLUENCE_CLOUD_{cloud_suffix}_URL")
    email = os.getenv("ATLASSIAN_CLOUD_EMAIL")
    api_token = os.getenv("ATLASSIAN_CLOUD_API_TOKEN")
    required = (
        (f"CONFLUENCE_DC_{suffix}_URL", f"Confluence DC {suffix} URL", False),
        (f"CONFLUENCE_CLOUD_{cloud_suffix}_URL", f"Confluence Cloud {cloud_suffix} URL", False),
        ("ATLASSIAN_CLOUD_EMAIL", "Atlassian Cloud Admin Email", False),
        ("ATLASSIAN_CLOUD_API_TOKEN", "Atlassian Cloud API Token", True),
    )
    if not ensure_environment_values(required):
        return
    source_url = os.getenv(f"CONFLUENCE_DC_{suffix}_URL")
    destination_url = os.getenv(f"CONFLUENCE_CLOUD_{cloud_suffix}_URL")
    email = os.getenv("ATLASSIAN_CLOUD_EMAIL")
    api_token = os.getenv("ATLASSIAN_CLOUD_API_TOKEN")

    source_url = source_url.rstrip("/")
    destination_url = destination_url.rstrip("/")
    api_base = "https://api.atlassian.com/migrations/reports/v1/id-mappings"
    auth = (email, api_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    try:
        response = requests.post(
            f"{api_base}/trigger",
            json={"product": "CONFLUENCE", "sourceURL": source_url, "destinationURL": destination_url},
            auth=auth, headers=headers, verify=VERIFY_SSL, timeout=60,
        )
        if response.status_code != 202:
            log(f"Unable to start Confluence ID mapping job ({response.status_code}): {response.text}")
            return
        job_id = response.json().get("jobId")
        if not job_id:
            log(f"Trigger response did not include jobId: {response.text}")
            return
        log(f"Confluence ID mapping job started: {job_id}")

        while True:
            time.sleep(60)
            status_response = requests.get(
                f"{api_base}/{job_id}", auth=auth, headers=headers,
                verify=VERIFY_SSL, timeout=60,
            )
            if status_response.status_code != 200:
                log(f"Unable to check job status ({status_response.status_code}): {status_response.text}")
                return
            status = status_response.json().get("status", "").upper()
            log(f"Confluence mapping job status: {status}")
            if status == "SUCCESS":
                break
            if status == "ERROR":
                log(f"Confluence mapping job failed: {status_response.text}")
                return

        download_response = requests.get(
            f"{api_base}/{job_id}/download", auth=auth, headers=headers,
            verify=VERIFY_SSL, timeout=60,
        )
        if download_response.status_code != 200:
            log(f"Unable to get report download link ({download_response.status_code}): {download_response.text}")
            return
        download_url = download_response.json().get("downloadUrl")
        if not download_url:
            log(f"Download response did not include downloadUrl: {download_response.text}")
            return
        with requests.get(download_url, stream=True, verify=VERIFY_SSL, timeout=(30, 300)) as file_response:
            file_response.raise_for_status()
            output_csv = "confluence-id-mappings.csv"
            if not save_mapping_download(file_response, output_csv):
                return
        generate_html_report(output_csv, "confluence-id-mappings.html")
    except (requests.exceptions.RequestException, ValueError) as error:
        log(f"Confluence ID mapping request failed: {error}")

def generate_html_report(csv_filename="id-mappings.csv", html_filename="id-mappings.html"):
    """Stream CSV rows into a separate searchable HTML report per first-column value."""
    product_name = "Confluence" if os.path.basename(csv_filename).lower().startswith("confluence-") else "Jira"
    log(f"Generating split HTML reports from '{csv_filename}'...")
    reports = {}
    counts = {}
    index_filename = html_filename
    base = os.path.splitext(index_filename)[0]
    style = """<style>body{font:14px system-ui;background:#0f172a;color:#f8fafc;margin:24px}a{color:#38bdf8}input,textarea{box-sizing:border-box;background:#1e293b;color:inherit;border:1px solid #475569;padding:10px;margin:8px 0;width:100%}table{border-collapse:collapse;width:100%;background:#1e293b}th,td{padding:9px;border-bottom:1px solid #334155;text-align:left;word-break:break-all}th{position:sticky;top:0;background:#0f172a}.wrap{overflow:auto}tr[hidden]{display:none}</style>"""
    try:
        with open(csv_filename, mode="r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")
            first_column = reader.fieldnames[0]
            for row in reader:
                group = row.get(first_column) or "(empty)"
                if group not in reports:
                    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", group).strip("-.") or "empty"
                    filename = f"{base}-{slug}.html"
                    if any(item[0] == filename for item in reports.values()):
                        filename = f"{base}-{slug}-{len(reports) + 1}.html"
                    out = open(filename, "w", encoding="utf-8")
                    reports[group] = (filename, out)
                    counts[group] = 0
                    out.write("<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
                    out.write(style + f"<title>{html.escape(group)}</title><body><p><a href=\"{html.escape(os.path.basename(index_filename), quote=True)}\">← Report index</a></p><h1>{html.escape(group)}</h1>")
                    out.write(f"<p>{html.escape(first_column)} · <span id=\"count\">0</span> mappings</p><input id=\"search\" placeholder=\"Search mappings...\" oninput=\"filterRows()\">")
                    if group == "jira/classic:customField":
                        out.write("<h2>JQL Custom Field Converter</h2><p>cf[Server ID]를 이전된 Cloud ID로 변환합니다.</p><textarea id=\"jql\" placeholder=\"issueType = cf[1000]\" oninput=\"convertJql()\"></textarea><textarea id=\"converted\" readonly placeholder=\"변환 결과\"></textarea>")
                    out.write("<div class=\"wrap\"><table><thead><tr>")
                    for column in reader.fieldnames:
                        out.write(f"<th>{html.escape(column)}</th>")
                    out.write("</tr></thead><tbody>")
                out = reports[group][1]
                out.write("<tr>")
                for column in reader.fieldnames:
                    out.write(f"<td>{html.escape(row.get(column) or '')}</td>")
                out.write("</tr>")
                counts[group] += 1

        with open(index_filename, "w", encoding="utf-8") as index:
            index.write(f"<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><title>{product_name} ID Mappings Reports</title><body><h1>{product_name} ID Mappings Reports</h1><ul>")
            for group, (filename, out) in reports.items():
                if group == "jira/classic:customField":
                    script = "const rows=[...document.querySelectorAll('tbody tr')];document.getElementById('count').textContent=rows.length.toLocaleString();function filterRows(){const q=document.getElementById('search').value.toLowerCase();rows.forEach(r=>r.hidden=!r.innerText.toLowerCase().includes(q))}const map=Object.fromEntries(rows.map(r=>[r.cells[1]?.innerText,r.cells[2]?.innerText]));function convertJql(){document.getElementById('converted').value=document.getElementById('jql').value.replace(/cf\\[(\\d+)\\]/gi,(m,id)=>map[id]?`cf[${map[id]}]`:m)}"
                else:
                    script = "const rows=[...document.querySelectorAll('tbody tr')];document.getElementById('count').textContent=rows.length.toLocaleString();function filterRows(){const q=document.getElementById('search').value.toLowerCase();rows.forEach(r=>r.hidden=!r.innerText.toLowerCase().includes(q))}"
                out.write("</tbody></table></div><script>" + script + "</script></body></html>")
                out.close()
                index.write(f"<li><a href=\"{html.escape(os.path.basename(filename), quote=True)}\">{html.escape(group)}</a> — {counts[group]:,}</li>")
            index.write("</ul></body></html>")
        log(f"Generated report index '{index_filename}' and {len(reports):,} category reports.")
        for group, (filename, _) in reports.items():
            log(f"  {group}: {counts[group]:,} mappings → {filename}")
        return True
    except Exception as e:
        for _, out in reports.values():
            if not out.closed:
                out.close()
        log(f"Error generating split HTML reports: {e}")
        return False

def main():
    log("Starting ID Mappings fetch script...")

    product = select_product()
    if product == "CONFLUENCE":
        run_confluence_id_mappings()
        return
    
    # 1. Environment and Configuration selection
    env = select_environment()
    cloud_env = select_target_cloud()
    mode = select_execution_mode()

    source_suffix = "OPS" if env == "OPS" else "STG"
    required_settings = [
        (f"JIRA_DC_{source_suffix}_URL", f"Jira DC {source_suffix} URL", False),
        (f"JIRA_DC_{source_suffix}_API_TOKEN", f"Jira DC {source_suffix} API Token", True),
    ]
    if cloud_env == "OPS":
        if not (os.getenv("JIRA_CLOUD_OPS_URL") or os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")):
            required_settings.append(("JIRA_CLOUD_OPS_URL", "Jira Cloud OPS URL", False))
    elif not (os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")):
        required_settings.append(("JIRA_CLOUD_SANDBOX_URL", "Jira Cloud SANDBOX URL", False))
    if not ensure_environment_values(required_settings):
        return
    
    # Load corresponding environment variables for Source DC Jira
    if env == "OPS":
        base_url = os.getenv("JIRA_DC_OPS_URL")
        token = os.getenv("JIRA_DC_OPS_API_TOKEN")
    else:
        base_url = os.getenv("JIRA_DC_STG_URL")
        token = os.getenv("JIRA_DC_STG_API_TOKEN")

    # Load corresponding environment variables for Target Cloud Jira
    if cloud_env == "OPS":
        cloud_url = os.getenv("JIRA_CLOUD_OPS_URL") or os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")
    else:
        cloud_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")
    
    # Validate required configurations
    if not base_url:
        log(f"Error: Missing JIRA_DC_{env}_URL in env file.")
        sys.exit(1)
    if not token:
        log(f"Error: Missing JIRA_DC_{env}_API_TOKEN in env file.")
        sys.exit(1)
    if not cloud_url:
        log(f"Error: Missing Target Cloud URL (JIRA_CLOUD_{cloud_env}_URL or JIRA_CLOUD_SANDBOX_URL) in env file.")
        sys.exit(1)
        
    # Clean URLs (strip trailing slashes as required by Atlassian API)
    base_url = base_url.rstrip('/')
    cloud_url = cloud_url.rstrip('/')
    
    # API Request configuration
    api_url = f"{base_url}/rest/migration/latest/report/id-mappings"
    output_filename = "id-mappings.csv"
    html_output_filename = "id-mappings.html"
    
    headers = {
        "Accept": "application/json, application/octet-stream",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    log(f"Configured DC Endpoint: {api_url}")
    log(f"Target Cloud Site: {cloud_url}")
    
    download_success = False

    # --- Mode 1: Download Only ---
    if mode == 1:
        log("Execution Mode: 1. Download Only (Download existing/cached mappings)")
        params = {
            "cloudSiteUrl": cloud_url,
            "invalidateCache": "false"
        }
        
        log("Checking for any existing/cached ID mappings on the server...")
        try:
            with requests.get(
                api_url, 
                params=params, 
                headers=headers, 
                verify=VERIFY_SSL,
                stream=True
            ) as response:
                status_code = response.status_code
                if status_code == 200:
                    log("Found existing/cached ID mappings on the server. Downloading...")
                    download_success = save_mapping_download(response, output_filename)
                elif status_code == 202:
                    log("Status 202 (ACCEPTED): No finalized cached version found or generation is currently in progress.")
                    log("Exiting because 'Download Only' mode was selected and no completed mapping is available.")
                elif status_code == 401:
                    log("Error (401 Unauthorized): Invalid Jira DC credentials or API Token.")
                elif status_code == 403:
                    log("Error (403 Forbidden): User does not have Server Admin permissions on the Jira Server/DC instance.")
                elif status_code == 404:
                    log("Error (404 Not Found): The ID Mappings endpoint was not found. Ensure JCMA >= 1.11.4.")
                else:
                    log(f"Unexpected response code {status_code}: {response.text}")
                    
        except requests.exceptions.RequestException as e:
            log(f"Connection error checking for cache: {e}")
            
    # --- Mode 2: Request Generation & Wait (2 minutes polling) ---
    else:
        log("Execution Mode: 2. Request Generation & Wait (Forcefully regenerate and wait)")
        params = {
            "cloudSiteUrl": cloud_url,
            "invalidateCache": "true" # Forcefully invalidate cache to request a fresh generation
        }
        
        poll_interval = 120  # 2 minutes
        attempt = 1
        
        while True:
            log(f"Requesting ID mappings (Attempt {attempt}, invalidateCache={params['invalidateCache']})...")
            try:
                with requests.get(
                    api_url, 
                    params=params, 
                    headers=headers, 
                    verify=VERIFY_SSL,
                    stream=True
                ) as response:
                    status_code = response.status_code
                    log(f"Response status code: {status_code}")
                    
                    if status_code == 200:
                        log(f"Success! ID mappings generated. Downloading and saving to '{output_filename}'...")
                        download_success = save_mapping_download(response, output_filename)
                        if download_success:
                            break
                        log("Downloaded file was invalid. Stopping without replacing the existing CSV.")
                        break
                        
                    elif status_code == 202:
                        log("Status 202 (ACCEPTED): ID mappings generation in progress on Jira Server.")
                        log(f"Waiting {poll_interval // 60} minutes ({poll_interval} seconds) before retrying...")
                        time.sleep(poll_interval)
                        # Once we start polling, change invalidateCache to false so we don't trigger a new generation again
                        params["invalidateCache"] = "false"
                        attempt += 1
                        
                    elif status_code == 400:
                        try:
                            error_msg = response.json()
                            log(f"Bad Request (400) Error: {error_msg}")
                        except Exception:
                            log(f"Bad Request (400) Error: {response.text}")
                        break
                        
                    elif status_code == 401:
                        log("Error (401 Unauthorized): Invalid Jira DC credentials or API Token.")
                        break
                        
                    elif status_code == 403:
                        log("Error (403 Forbidden): User does not have Server Admin permissions on the Jira Server/DC instance.")
                        break
                        
                    elif status_code == 404:
                        log("Error (404 Not Found): The ID Mappings endpoint was not found.")
                        break
                        
                    else:
                        log(f"Unexpected response code {status_code}: {response.text}")
                        break
                        
            except requests.exceptions.RequestException as re_err:
                log(f"Network / Connection error occurred: {re_err}")
                log(f"Retrying in {poll_interval} seconds...")
                time.sleep(poll_interval)
                attempt += 1

    # Generate HTML report only from a non-empty, valid CSV.
    if download_success or is_valid_csv_file(output_filename):
        generate_html_report(csv_filename=output_filename, html_filename=html_output_filename)
    elif os.path.exists(output_filename):
        log(f"Skipping HTML report because '{output_filename}' is empty or invalid.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Exiting.")
        sys.exit(0)
