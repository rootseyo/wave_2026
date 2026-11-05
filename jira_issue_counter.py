#!/usr/bin/env python3
import os
import sys
import argparse
import urllib3
import requests
from requests.auth import HTTPBasicAuth
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

def log(message):
    print(f"[*] {message}")

def load_environment():
    # 현재 디렉토리 또는 부모 디렉토리의 env 파일 로드
    possible_paths = ['env', '../env']
    loaded = False
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            log(f"Loaded environment variables from: {path}")
            loaded = True
            break
    if not loaded:
        # 기본 시스템 환경 변수 사용을 위해 경고만 출력
        log("Warning: 'env' file not found in common paths. Using system environment variables.")

def get_projects(file_path):
    if not os.path.exists(file_path):
        log(f"Error: Project file '{file_path}' not found.")
        sys.exit(1)
            
    log(f"Reading projects from: {file_path}")
    projects = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                projects.append(line)
    return projects

def fetch_issuetype_count(session, base_url, auth, project_key, issuetype_id, issuetype_name, is_dc=True):
    jql = f'project="{project_key}" AND issuetype="{issuetype_id}"'
    if is_dc:
        url = f"{base_url}/rest/api/2/search"
        params = {"jql": jql, "maxResults": 0}
        try:
            response = session.get(url, params=params, auth=auth, timeout=15)
            if response.status_code == 200:
                data = response.json()
                return issuetype_name, data.get("total", 0)
            else:
                return issuetype_name, f"Error (HTTP {response.status_code})"
        except Exception as e:
            return issuetype_name, f"Error ({str(e)})"
    else:
        # Jira Cloud uses /rest/api/3/search/jql
        url = f"{base_url}/rest/api/3/search/jql"
        total_count = 0
        next_page_token = None
        try:
            while True:
                params = {"jql": jql, "maxResults": 5000}
                if next_page_token:
                    params["nextPageToken"] = next_page_token
                response = session.get(url, params=params, auth=auth, timeout=15)
                if response.status_code != 200:
                    return issuetype_name, f"Error (HTTP {response.status_code})"
                data = response.json()
                issues = data.get("issues", [])
                total_count += len(issues)
                if data.get("isLast", True) or not issues:
                    break
                next_page_token = data.get("nextPageToken")
            return issuetype_name, total_count
        except Exception as e:
            return issuetype_name, f"Error ({str(e)})"

def process_project(session, base_url, auth, project_key, max_workers, is_dc=True):
    # 1. 프로젝트 정보 및 Issue Types 선조회
    api_ver = "2" if is_dc else "3"
    url = f"{base_url}/rest/api/{api_ver}/project/{project_key}"
    try:
        response = session.get(url, auth=auth, timeout=15)
        if response.status_code == 404:
            return project_key, "Error: Project not found"
        elif response.status_code != 200:
            return project_key, f"Error: HTTP {response.status_code}"
            
        project_data = response.json()
        issue_types = project_data.get("issueTypes", [])
        if not issue_types:
            return project_key, "No Issue Types defined"
            
        # 2. 각 Issue Type별 개수 병렬 조회
        counts = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_type = {
                executor.submit(
                    fetch_issuetype_count, session, base_url, auth, project_key, it["id"], it["name"], is_dc
                ): it["name"]
                for it in issue_types
            }
            for future in as_completed(future_to_type):
                it_name = future_to_type[future]
                try:
                    name, count = future.result()
                    counts[name] = count
                except Exception as e:
                    counts[it_name] = f"Error ({str(e)})"
                    
        return project_key, counts
    except Exception as e:
        return project_key, f"Connection Error ({str(e)})"

def main():
    parser = argparse.ArgumentParser(description="Jira Project Issue Type Counter")
    parser.add_argument(
        "--env", 
        choices=["JIRA_DC_STG", "JIRA_DC_OPS", "JIRA_CLOUD_SANDBOX", "JIRA_CLOUD_OPS"], 
        help="Jira Target Environment: JIRA_DC_STG, JIRA_DC_OPS, JIRA_CLOUD_SANDBOX, or JIRA_CLOUD_OPS."
    )
    parser.add_argument(
        "--file", 
        default="projects.txt", 
        help="Path to projects.txt containing project keys. Default is projects.txt."
    )
    parser.add_argument(
        "--workers", 
        type=int, 
        default=5, 
        help="Number of concurrent project workers. Default is 5."
    )
    parser.add_argument(
        "--subworkers", 
        type=int, 
        default=5, 
        help="Number of concurrent issue type queries per project. Default is 5."
    )
    args = parser.parse_args()

    # 1. 환경 변수 로드
    load_environment()
    
    # 환경 선택 (CLI 인자가 없으면 인터랙티브 프롬프트 제공)
    env_choice = args.env
    if not env_choice:
        print("Select Jira Target Environment:")
        print("1. JIRA_DC_STG (Data Center Staging)")
        print("2. JIRA_DC_OPS (Data Center Operations)")
        print("3. JIRA_CLOUD_SANDBOX (Cloud Sandbox)")
        print("4. JIRA_CLOUD_OPS (Cloud Operations)")
        while True:
            try:
                choice = input("Enter choice (1, 2, 3 or 4): ").strip()
                if choice == "1":
                    env_choice = "JIRA_DC_STG"
                    break
                elif choice == "2":
                    env_choice = "JIRA_DC_OPS"
                    break
                elif choice == "3":
                    env_choice = "JIRA_CLOUD_SANDBOX"
                    break
                elif choice == "4":
                    env_choice = "JIRA_CLOUD_OPS"
                    break
                else:
                    print("Invalid choice. Please enter 1, 2, 3, or 4.")
            except (KeyboardInterrupt, EOFError):
                print("\nOperation cancelled.")
                sys.exit(0)

    # 2. Jira 연결 및 인증 정보 설정
    ssl_verify_env = os.getenv("VERIFY_SSL") or os.getenv("JIRA_SSL_VERIFY")
    if ssl_verify_env is not None:
        ssl_verify = ssl_verify_env.lower() in ("true", "1", "yes")
    else:
        ssl_verify = False  # SSL 에러 방지를 위한 기본값 설정 (검증 비활성화)
    
    if not ssl_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log("SSL Verification disabled to prevent SSL errors.")

    session = requests.Session()
    session.verify = ssl_verify
    session.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json"
    })

    if env_choice in ("JIRA_DC_STG", "JIRA_DC_OPS"):
        if env_choice == "JIRA_DC_STG":
            base_url = os.getenv("JIRA_DC_STG_URL")
            token = os.getenv("JIRA_DC_STG_API_TOKEN") or os.getenv("JIRA_DC_STG_TOKEN")
        else:  # JIRA_DC_OPS
            base_url = os.getenv("JIRA_DC_OPS_URL")
            token = os.getenv("JIRA_DC_OPS_API_TOKEN") or os.getenv("JIRA_DC_OPS_TOKEN")

        if not base_url or not token:
            print(f"Error: URL or token for {env_choice} not found in env.")
            sys.exit(1)
        base_url = base_url.rstrip('/')
        session.headers.update({"Authorization": f"Bearer {token}"})
        auth = None
        log(f"Target: Jira Data Center ({base_url}) [Using Bearer Token]")
            
    else:  # Cloud (JIRA_CLOUD_SANDBOX or JIRA_CLOUD_OPS)
        if env_choice == "JIRA_CLOUD_SANDBOX":
            base_url = os.getenv("JIRA_CLOUD_SANDBOX_URL") or os.getenv("JIRA_CLOUD_URL")
            email = os.getenv("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_SANDBOX_EMAIL") or os.getenv("JIRA_CLOUD_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_EMAIL")
            api_token = os.getenv("JIRA_CLOUD_SANDBOX_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")
        else:  # JIRA_CLOUD_OPS
            base_url = os.getenv("JIRA_CLOUD_OPS_URL") or os.getenv("JIRA_CLOUD_URL")
            email = os.getenv("JIRA_CLOUD_OPS_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_OPS_EMAIL") or os.getenv("JIRA_CLOUD_ADMIN_EMAIL") or os.getenv("JIRA_CLOUD_EMAIL")
            api_token = os.getenv("JIRA_CLOUD_OPS_API_TOKEN") or os.getenv("JIRA_CLOUD_API_TOKEN")

        if not base_url or not email or not api_token:
            print(f"Error: Jira Cloud environment variables for {env_choice} (URL, EMAIL, API_TOKEN) not found in env.")
            sys.exit(1)
        base_url = base_url.rstrip('/')
        auth = HTTPBasicAuth(email, api_token)
        log(f"Target: Jira Cloud ({base_url}) for {email}")

    # 3. 프로젝트 목록 로드
    projects = get_projects(args.file)
    log(f"Total projects loaded: {len(projects)}")
    print("=" * 60)
    print(f"{'PROJECT KEY':<15} | {'ISSUE TYPE COUNTS'}")
    print("=" * 60)

    # 4. 프로젝트 처리 (병렬 실행)
    is_dc = env_choice in ("JIRA_DC_STG", "JIRA_DC_OPS")
    totals = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_proj = {
            executor.submit(
                process_project, session, base_url, auth, proj, args.subworkers, is_dc
            ): proj
            for proj in projects
        }
        
        for future in as_completed(future_to_proj):
            proj_key = future_to_proj[future]
            try:
                key, result = future.result()
                if isinstance(result, dict):
                    count_strs = [f"{k}: {v}" for k, v in sorted(result.items())]
                    counts_line = ", ".join(count_strs)
                    print(f"{key:<15} | {counts_line}")
                    
                    # Accumulate totals per issue type
                    for k, v in result.items():
                        if isinstance(v, int):
                            totals[k] = totals.get(k, 0) + v
                else:
                    print(f"{key:<15} | {result}")
            except Exception as e:
                print(f"{proj_key:<15} | Execution Error: {str(e)}")

    print("=" * 60)
    print("SUMMARY (TOTAL ISSUE COUNTS BY TYPE):")
    if totals:
        print(f"Total Unique Issue Types: {len(totals)}")
        for k, v in sorted(totals.items()):
            print(f"- {k}: {v}")
    else:
        print("No issue type data was successfully retrieved.")
    print("=" * 60)
    log("Completed checking issue counts.")

if __name__ == "__main__":
    main()
