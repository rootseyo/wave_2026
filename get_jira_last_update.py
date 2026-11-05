#!/usr/bin/env python3
"""
Jira Project Last Update Date Extractor
======================================
- Reads Jira project URLs from jira_url.csv (or specified input file)
- Authenticates with Jira Data Center using JIRA_DC_OPS credentials in 'env' file
- Queries the latest updated issue date for each project via REST API v2
- Robust Retry Mechanism: Retries persistently until transient network/server/retry
  errors (RetryError, ConnectionError, Timeout, HTTP 429/500/502/503/504) are resolved!
- Outputs results to jira_last_update.csv (or specified output file)
"""

import os
import sys
import re
import csv
import time
import random
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib3
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

def log(msg):
    print(f"[*] {msg}", flush=True)

def load_environment():
    """Load variables from env or .env file."""
    possible_paths = ['env', '../env', '.env', '../.env']
    loaded = False
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            log(f"Loaded environment variables from: {path}")
            loaded = True
            break
    if not loaded:
        log("Warning: 'env' file not found in common paths. Using system environment variables.")

def setup_jira_session(env_prefix="JIRA_DC_OPS"):
    """
    Configures and returns a requests.Session with connection pooling, retries,
    and Jira Data Center authentication headers.
    """
    base_url = os.getenv(f"{env_prefix}_URL") or os.getenv("OPS_JIRA_URL")
    api_token = (os.getenv(f"{env_prefix}_API_TOKEN") 
                 or os.getenv(f"{env_prefix}_TOKEN")
                 or os.getenv("OPS_JIRA_API_TOKEN")
                 or os.getenv("OPS_JIRA_TOKEN"))
    username = os.getenv(f"{env_prefix}_USERNAME") or os.getenv("OPS_JIRA_USERNAME")
    password = os.getenv(f"{env_prefix}_PASSWORD") or os.getenv("OPS_JIRA_PASSWORD")

    if not base_url:
        print(f"[!] Error: {env_prefix}_URL not found in environment variables.")
        sys.exit(1)

    base_url = base_url.rstrip('/')

    # SSL verification settings
    ssl_verify_env = os.getenv("JIRA_SSL_VERIFY", "False")
    ssl_verify = ssl_verify_env.lower() in ("true", "1", "yes")
    if not ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log("SSL verification disabled.")

    session = requests.Session()
    session.verify = ssl_verify

    # Light adapter-level retry for immediate TCP blips
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })

    auth = None
    if api_token:
        session.headers.update({"Authorization": f"Bearer {api_token}"})
        log(f"Target: Jira DC ({base_url}) [Using Bearer Token for {env_prefix}]")
    elif username and password:
        auth = HTTPBasicAuth(username, password)
        log(f"Target: Jira DC ({base_url}) [Using Basic Auth for user '{username}']")
    else:
        log(f"[!] Warning: No API Token or Username/Password found for {env_prefix}.")

    return session, base_url, auth

def parse_project_url(raw_line):
    """
    Parses a single line from the CSV/text file and extracts the project key.
    Handles formats like:
      - https://jira.hmg-corp.io/projects/AIRSPEECH/summary
      - https://jira.hmg-corp.io/projects/AIRSPEECH
      - https://jira.hmg-corp.io/browse/AIRSPEECH-1
      - AIRSPEECH
    """
    line = raw_line.strip()
    if not line or line.startswith('#'):
        return None

    # Skip header lines if present
    if line.lower() in ("url", "project_url", "project_key", "project", "key"):
        return None

    # Pattern 1: /projects/{KEY}/...
    match_proj = re.search(r'/projects/([^/?#\s]+)', line)
    if match_proj:
        return {"url": line, "project_key": match_proj.group(1).strip()}

    # Pattern 2: /browse/{KEY}-123
    match_browse = re.search(r'/browse/([A-Za-z0-9_]+)(?:-\d+)?', line)
    if match_browse:
        return {"url": line, "project_key": match_browse.group(1).strip()}

    # Pattern 3: plain project key string
    if re.match(r'^[A-Za-z0-9_\-]+$', line):
        return {"url": line, "project_key": line}

    return {"url": line, "project_key": ""}

def load_projects_from_file(file_path):
    """Loads and parses project list from input file."""
    if not os.path.exists(file_path):
        print(f"[!] Error: Input file '{file_path}' not found.")
        sys.exit(1)

    records = []
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            first_col = row[0].strip()
            parsed = parse_project_url(first_col)
            if parsed:
                records.append(parsed)

    log(f"Successfully loaded {len(records)} projects from '{file_path}'")
    return records

def fetch_project_last_update(
    session, base_url, auth, project_key,
    timeout=15, max_retries=0, initial_delay=2.0, max_delay=60.0
):
    """
    Queries Jira DC REST API v2 using JQL to find the most recently updated issue.
    JQL: project = "{project_key}" ORDER BY updated DESC

    Persistent Retry Logic:
    - Retries until resolved for transient issues:
      RetryError, ConnectionError, Timeout, 429, 500, 502, 503, 504.
    - If max_retries == 0: Retries indefinitely until successful or fatal error.
    - Exponential backoff with random jitter is applied between attempts.
    """
    if not project_key:
        return {
            "last_update": "",
            "last_issue_key": "",
            "total_issues": 0,
            "status": "Error: Empty project key"
        }

    search_url = f"{base_url}/rest/api/2/search"
    jql = f'project = "{project_key}" ORDER BY updated DESC'
    params = {
        "jql": jql,
        "maxResults": 1,
        "fields": "updated,key",
        "validateQuery": "strict"
    }

    attempt = 0
    while True:
        attempt += 1
        transient_error = None
        retry_after_wait = None

        try:
            response = session.get(search_url, params=params, auth=auth, timeout=timeout)
            
            # 1. Successful response
            if response.status_code == 200:
                data = response.json()
                total = data.get("total", 0)
                issues = data.get("issues", [])
                if total == 0 or not issues:
                    return {
                        "last_update": "",
                        "last_issue_key": "",
                        "total_issues": 0,
                        "status": "No Issues"
                    }
                latest = issues[0]
                last_update = latest.get("fields", {}).get("updated", "")
                last_issue_key = latest.get("key", "")
                return {
                    "last_update": last_update,
                    "last_issue_key": last_issue_key,
                    "total_issues": total,
                    "status": "Success"
                }

            # 2. Transient Server Errors & Rate Limits (Retry Target)
            elif response.status_code in (429, 500, 502, 503, 504):
                transient_error = f"HTTP {response.status_code}"
                # Respect standard Retry-After header if provided
                retry_after_hdr = response.headers.get("Retry-After")
                if retry_after_hdr:
                    try:
                        retry_after_wait = float(retry_after_hdr)
                    except ValueError:
                        pass

            # 3. Deterministic Non-Retry Errors:
            # 400 Bad Request (Project doesn't exist or JQL syntax invalid)
            elif response.status_code == 400:
                error_text = "Project Not Found or Invalid JQL"
                try:
                    err_data = response.json()
                    err_msgs = err_data.get("errorMessages", [])
                    if err_msgs:
                        error_text = f"Project Not Found / JQL Error: {err_msgs[0]}"
                except Exception:
                    pass
                return {
                    "last_update": "",
                    "last_issue_key": "",
                    "total_issues": 0,
                    "status": error_text
                }

            # 401, 403 Permission Denied
            elif response.status_code in (401, 403):
                return {
                    "last_update": "",
                    "last_issue_key": "",
                    "total_issues": 0,
                    "status": f"Error: Permission Denied ({response.status_code})"
                }

            # 404 Endpoint Not Found
            elif response.status_code == 404:
                return {
                    "last_update": "",
                    "last_issue_key": "",
                    "total_issues": 0,
                    "status": "Error: Not Found (404)"
                }

            # Other unexpected HTTP errors
            else:
                return {
                    "last_update": "",
                    "last_issue_key": "",
                    "total_issues": 0,
                    "status": f"Error: HTTP {response.status_code}"
                }

        # Catch transient network/retry exceptions
        except (requests.exceptions.RetryError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            err_cls = type(e).__name__
            transient_error = f"{err_cls}"
        except requests.exceptions.RequestException as e:
            err_cls = type(e).__name__
            transient_error = f"RequestException ({err_cls})"
        except Exception as e:
            transient_error = f"UnexpectedException ({type(e).__name__})"

        # Check if max retries reached (if max_retries > 0)
        if max_retries > 0 and attempt >= max_retries:
            return {
                "last_update": "",
                "last_issue_key": "",
                "total_issues": 0,
                "status": f"Error: Max retries exceeded ({transient_error})"
            }

        # Calculate backoff delay with jitter
        if retry_after_wait is not None:
            delay = retry_after_wait + random.uniform(0.5, 2.0)
        else:
            # Exponential backoff capped at max_delay
            delay = min(initial_delay * (1.5 ** min(attempt - 1, 8)), max_delay) + random.uniform(0.2, 1.5)

        retry_label = f"Attempt {attempt}" if max_retries == 0 else f"Attempt {attempt}/{max_retries}"
        print(f"  [RETRY] '{project_key}' encountered {transient_error}. Retrying in {delay:.1f}s until resolved ({retry_label})...", flush=True)
        time.sleep(delay)

def save_to_csv(output_path, results):
    """Writes the collected results into a CSV file with utf-8-sig encoding."""
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(["url", "project_key", "last_update", "last_issue_key", "total_issues", "status"])
        for item in results:
            if item is not None:
                writer.writerow([
                    item["url"],
                    item["project_key"],
                    item["last_update"],
                    item["last_issue_key"],
                    item["total_issues"],
                    item["status"]
                ])
    log(f"Saved results to '{output_path}'")

def main():
    parser = argparse.ArgumentParser(
        description="Extract the latest issue update date for Jira projects with persistent retry."
    )
    parser.add_argument(
        "-i", "--input",
        default="jira_url.csv",
        help="Path to input CSV/text file containing Jira project URLs (default: jira_url.csv)"
    )
    parser.add_argument(
        "-o", "--output",
        default="jira_last_update.csv",
        help="Path to output CSV file (default: jira_last_update.csv)"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=5,
        help="Number of concurrent worker threads (default: 5 to prevent server overload)"
    )
    parser.add_argument(
        "-e", "--env-key",
        default="JIRA_DC_OPS",
        help="Environment variable prefix in env file (default: JIRA_DC_OPS)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Request timeout in seconds (default: 20)"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Max retries on transient errors. 0 = retry indefinitely until resolved (default: 0)"
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        help="Initial delay in seconds before retrying (default: 2.0)"
    )
    parser.add_argument(
        "--max-retry-delay",
        type=float,
        default=60.0,
        help="Maximum backoff delay in seconds (default: 60.0)"
    )

    args = parser.parse_args()

    # 1. 환경 변수 로드
    load_environment()

    # 2. Jira 세션 설정
    session, base_url, auth = setup_jira_session(env_prefix=args.env_key)

    # 3. 프로젝트 목록 로드
    records = load_projects_from_file(args.input)
    total_projects = len(records)
    if total_projects == 0:
        log("No projects to process. Exiting.")
        return

    retry_mode = "Infinite (Until Resolved)" if args.max_retries == 0 else f"Max {args.max_retries} times"
    log(f"Starting inspection for {total_projects} projects with {args.workers} workers...")
    log(f"Retry Mode: {retry_mode}, Initial Delay: {args.retry_delay}s, Max Delay: {args.max_retry_delay}s")
    print("=" * 85)
    print(f"{'INDEX':<7} | {'PROJECT KEY':<15} | {'LAST UPDATE':<28} | {'TOTAL':<7} | {'STATUS'}")
    print("=" * 85)

    results = [None] * total_projects
    completed_count = 0
    lock = threading.Lock()

    def worker_task(index, record):
        proj_key = record["project_key"]
        res = fetch_project_last_update(
            session=session,
            base_url=base_url,
            auth=auth,
            project_key=proj_key,
            timeout=args.timeout,
            max_retries=args.max_retries,
            initial_delay=args.retry_delay,
            max_delay=args.max_retry_delay
        )
        combined = {
            "url": record["url"],
            "project_key": proj_key,
            "last_update": res["last_update"],
            "last_issue_key": res["last_issue_key"],
            "total_issues": res["total_issues"],
            "status": res["status"]
        }
        return index, combined

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_index = {
                executor.submit(worker_task, idx, rec): idx 
                for idx, rec in enumerate(records)
            }

            for future in as_completed(future_to_index):
                idx, item = future.result()
                results[idx] = item

                with lock:
                    completed_count += 1
                    current = completed_count

                # Display progress
                disp_last_update = item["last_update"] or "-"
                disp_total = str(item["total_issues"])
                disp_status = item["status"]
                print(f"[{current:04d}/{total_projects:04d}] | {item['project_key']:<15} | {disp_last_update:<28} | {disp_total:<7} | {disp_status}", flush=True)

                # Save intermediate progress every 50 items
                if current % 50 == 0:
                    with lock:
                        save_to_csv(args.output, results)

    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user! Saving accumulated results...")
    finally:
        # 4. 최종 결과 저장
        save_to_csv(args.output, results)

    # 5. 결과 요약 출력
    success_cnt = sum(1 for r in results if r and r["status"] == "Success")
    no_issues_cnt = sum(1 for r in results if r and r["status"] == "No Issues")
    error_cnt = sum(1 for r in results if r and r["status"] not in ("Success", "No Issues"))

    print("=" * 85)
    print("EXECUTION SUMMARY:")
    print(f"- Total Projects Processed : {completed_count}/{total_projects}")
    print(f"- Projects with Issues     : {success_cnt}")
    print(f"- Projects without Issues  : {no_issues_cnt}")
    print(f"- Errors / Not Found       : {error_cnt}")
    print(f"- Output CSV File          : {args.output}")
    print("=" * 85)

if __name__ == "__main__":
    main()
