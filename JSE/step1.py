import argparse
import json
import os
import re
import sys
import requests
try:
    from dotenv import load_dotenv
    load_dotenv("env")
except ImportError:
    # python-dotenv 라이브러리가 없는 경우를 위한 커스텀 fallback 함수
    def load_dotenv(env_path="env"):
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, val = line.split('=', 1)
                        os.environ[key.strip()] = val.strip()
    load_dotenv("env")

# SSL Verification Setting
VERIFY_SSL_ENV = os.getenv("VERIFY_SSL", "False").strip()
VERIFY_SSL = VERIFY_SSL_ENV.lower() == "true" if VERIFY_SSL_ENV.lower() in ["true", "false"] else VERIFY_SSL_ENV

if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    print("[WARNING] SSL Verification is disabled.")

# ============================================================
#  1. 환경 변수 및 설정 파일 로드 함수
# ============================================================
def load_projects(file_path="projects.txt"):
    projects = set()
    if not os.path.exists(file_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(script_dir, "projects.txt")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    projects.add(line.upper())
    except FileNotFoundError:
        print(f"⚠️ Warning: 프로젝트 목록 파일({file_path})을 찾을 수 없습니다.")
    return projects


# ============================================================
#  2. JQL 분석 및 프로젝트 필터링 함수
# ============================================================
def extract_projects_from_jql(jql):
    project_keys = set()
    
    # 1) project = KEY 형태 분석 (따옴표 고려)
    eq_matches = re.finditer(r'(?i)\bproject\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([a-zA-Z0-9_-]+))', jql)
    for match in eq_matches:
        val = match.group(1) or match.group(2) or match.group(3)
        if val:
            project_keys.add(val.strip().upper())
            
    # 2) project in (KEY1, KEY2, ...) 형태 분석
    in_matches = re.finditer(r'(?i)\bproject\s+in\s*\(([^)]+)\)', jql)
    for match in in_matches:
        content = match.group(1)
        parts = re.split(r',', content)
        for part in parts:
            part_clean = re.sub(r'[\'"]', '', part).strip()
            if part_clean:
                project_keys.add(part_clean.upper())
                
    return project_keys


def matches_target_projects(jql, target_projects):
    extracted_projects = extract_projects_from_jql(jql)
    if not extracted_projects:
        return False, set()
        
    matched = extracted_projects.intersection(target_projects)
    return len(matched) > 0, matched


# ============================================================
#  3. JQL Search Extensions 전용 문법 정규식 패턴 정의
# ============================================================
EXT_PATTERN = re.compile(
    r"(?i)\b(\w+Count|\w+ByUser|\w+OnDate|commentLast\w+|exactTextMatch|"
    r"wildcardMatch|regex|dateCompare|parentsOf\w+|childrenOf\w+|"
    r"subtasksOf\w+|linkedIssuesOf\w+)\b|\bissue\s+in\s+\w+\("
)


# ============================================================
#  4. 메인 실행 옵션 선택
# ============================================================
def get_user_options():
    parser = argparse.ArgumentParser(description="Jira Migration JQL Filter Scanner")
    parser.add_argument("--env", choices=["STG", "OPS"], help="대상 환경 선택 (STG 또는 OPS)")
    parser.add_argument("--dry-run", action="store_true", help="드라이 런 모드로 실행 (최대 1페이지만 스캔)")
    
    args, _ = parser.parse_known_args()
    
    env = args.env
    dry_run = args.dry_run
    
    # 대화형 프롬프트 (CLI 인자가 주어지지 않았고 터미널 인터랙션이 가능한 경우)
    if not env:
        if sys.stdin.isatty():
            print("Jira 대상 환경을 선택하세요:")
            print("1. STG (Staging)")
            print("2. OPS (Operations/Production)")
            try:
                choice = input("선택 (기본값: 1): ").strip()
                env = "OPS" if choice == "2" else "STG"
            except (KeyboardInterrupt, EOFError):
                print("\n프로그램을 종료합니다.")
                sys.exit(0)
        else:
            env = "STG"
            
    # dry-run 옵션이 명시적으로 제공되지 않은 경우 대화형 프롬프트 제공
    if not args.dry_run and "--dry-run" not in sys.argv:
        if sys.stdin.isatty():
            try:
                choice = input("Dry Run 모드로 실행하시겠습니까? (Y/n, 기본값: Y): ").strip().lower()
                dry_run = choice != 'n'
            except (KeyboardInterrupt, EOFError):
                print("\n프로그램을 종료합니다.")
                sys.exit(0)
        else:
            dry_run = False
    else:
        dry_run = args.dry_run
        
    return env, dry_run


# ============================================================
#  5. 필터 조회 및 처리 메인 로직
# ============================================================
def get_all_migration_filters():
    # 1) 설정 및 프로젝트 목록 로드
    env_choice, dry_run = get_user_options()
    target_projects = load_projects()

    print("\n====================================================")
    print(f"⚙️  설정 로드 완료:")
    print(f"   - 대상 환경: {env_choice}")
    print(f"   - Dry Run 모드: {'활성화 (1페이지 검사)' if dry_run else '비활성화 (전체 검사)'}")
    print(f"   - 로드된 대상 프로젝트 수: {len(target_projects)} 개")
    print("====================================================\n")

    url_key = f"JIRA_DC_{env_choice}_URL"
    token_key = f"JIRA_DC_{env_choice}_API_TOKEN"

    jira_url = os.getenv(url_key, "").rstrip('/')
    api_token = os.getenv(token_key, "").strip()

    if not jira_url:
        print(f"❌ 에러: env 파일에 '{url_key}' 값이 설정되지 않았습니다.")
        sys.exit(1)

    auth_config = {"verify": VERIFY_SSL}
    if api_token:
        auth_config["headers"] = {"Authorization": f"Bearer {api_token}"}
    else:
        print(f"⚠️  경고: '{token_key}' 값이 설정되지 않았습니다. 인증 없이 요청을 시도합니다.")

    print(f"🚀 {env_choice} Jira 서버 ({jira_url})에서 필터 조회를 시작합니다...")

    all_rows = []
    start_at = 0
    max_results = 10 if dry_run else 100  # 드라이 런인 경우 페이지 당 10개만 스캔
    total_scanned = 0
    is_fallback = False

    while True:
        api_url = f"{jira_url}/rest/api/2/filter/search?expand=permissions&startAt={start_at}&maxResults={max_results}"

        try:
            response = requests.get(api_url, **auth_config, timeout=30)

            if response.status_code == 404:
                print("⚠️  GET /rest/api/2/filter/search가 지원되지 않거나 404 오류가 발생했습니다.")
                print("🔄 백업 방식(/rest/api/2/filter/favourite 및 /rest/api/2/filter/my)을 사용하여 필터를 로드합니다...")
                is_fallback = True
                break

            if response.status_code != 200:
                print(f"❌ API 호출 실패 (상태 코드: {response.status_code})")
                print(response.text)
                break

            data = response.json()
            filters = data.get("values", [])

            if not filters:
                break

            total_scanned += len(filters)
            print(f"--- 누적 {total_scanned}개 필터 스캔 중... (현재 페이지 처리 완료) ---")

            for f in filters:
                jql = f.get("jql", "")

                # 💡 핵심 1: JQL 문장 안에 앱 전용 문법이 포함되어 있는지 검사
                if EXT_PATTERN.search(jql):
                    # 💡 핵심 2: JQL 내 project 필드 검사 및 일치 여부 확인
                    is_match, matched_keys = matches_target_projects(jql, target_projects)
                    if not is_match:
                         continue

                    # 소유자 정보 파싱
                    owner_obj = f.get("owner", {})
                    owner_name = (
                        f"{owner_obj.get('displayName')} ({owner_obj.get('name')})"
                        if owner_obj
                        else "알 수 없음"
                    )

                    # 공유 권한 파싱 (어느 프로젝트, 그룹에 공유되었는지 텍스트화)
                    perms = f.get("permissions") or f.get("sharePermissions") or []
                    perm_list = []
                    for p in perms:
                        p_type = p.get("type")
                        if p_type == "global":
                            perm_list.append("전체공개")
                        elif p_type == "project":
                            proj_name = p.get("project", {}).get("name", "Unknown")
                            role_name = p.get("role", {}).get("name", "")
                            perm_list.append(
                                f"{proj_name} / {role_name}"
                                if role_name
                                else proj_name
                            )
                        elif p_type == "group":
                            perm_list.append(f"그룹: {p.get('group', {}).get('name')}")
                        else:
                            perm_list.append(p_type)

                    permissions_str = (
                        " | ".join(perm_list) if perm_list else "비공개"
                    )

                    all_rows.append(
                        {
                            "id": f.get("id"),
                            "name": f.get("name"),
                            "owner": owner_name,
                            "jql": jql,
                            "permissions": permissions_str,
                            "matched_projects": matched_keys,
                        }
                    )

            if dry_run:
                print("⚠️  [DRY RUN] 1페이지(최대 10개 필터)만 검색 후 스캔을 종료합니다.")
                break

            # 페이지네이션 판단
            if data.get("isLast", True):
                break
            else:
                start_at += max_results

        except Exception as e:
            print(f"❌ 에러 발생: {str(e)}")
            break

    # 💡 백업 폴백 처리: /filter/search 엔드포인트를 사용할 수 없을 때
    if is_fallback:
        combined_filters = {}
        endpoints = [
            f"{jira_url}/rest/api/2/filter/favourite",
            f"{jira_url}/rest/api/2/filter/my"
        ]

        for url in endpoints:
            try:
                resp = requests.get(url, **auth_config, timeout=30)
                if resp.status_code == 200:
                    for f in resp.json():
                        fid = f.get("id")
                        if fid not in combined_filters:
                            combined_filters[fid] = f
            except Exception as e:
                print(f"⚠️  백업 엔드포인트({url}) 호출 중 오류 발생: {e}")

        filters_list = list(combined_filters.values())
        total_scanned = len(filters_list)
        print(f"--- 백업 엔드포인트에서 총 {total_scanned}개의 필터를 조회했습니다. 스캔을 시작합니다... ---")

        for f in filters_list:
            jql = f.get("jql", "")

            # 💡 핵심 1: JQL 문장 안에 앱 전용 문법이 포함되어 있는지 검사
            if EXT_PATTERN.search(jql):
                # 💡 핵심 2: JQL 내 project 필드 검사 및 일치 여부 확인
                is_match, matched_keys = matches_target_projects(jql, target_projects)
                if not is_match:
                     continue

                # 소유자 정보 파싱
                owner_obj = f.get("owner", {})
                owner_name = (
                    f"{owner_obj.get('displayName')} ({owner_obj.get('name')})"
                    if owner_obj
                    else "알 수 없음"
                )

                # 공유 권한 파싱
                perms = f.get("permissions") or f.get("sharePermissions") or []
                perm_list = []
                for p in perms:
                    p_type = p.get("type")
                    if p_type == "global":
                        perm_list.append("전체공개")
                    elif p_type == "project":
                        proj_name = p.get("project", {}).get("name", "Unknown")
                        role_name = p.get("role", {}).get("name", "")
                        perm_list.append(
                            f"{proj_name} / {role_name}"
                            if role_name
                            else proj_name
                        )
                    elif p_type == "group":
                        perm_list.append(f"그룹: {p.get('group', {}).get('name')}")
                    else:
                        perm_list.append(p_type)

                permissions_str = (
                    " | ".join(perm_list) if perm_list else "비공개"
                )

                all_rows.append(
                    {
                        "id": f.get("id"),
                        "name": f.get("name"),
                        "owner": owner_name,
                        "jql": jql,
                        "permissions": permissions_str,
                        "matched_projects": matched_keys,
                    }
                )

    # ============================================================
    #  6. 결과를 HTML 리포트 파일로 빌드 및 저장
    # ============================================================
    dry_suffix = "_dryrun" if dry_run else ""
    html_filename = f"jira_migration_jql_report{dry_suffix}.html"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>JQL Search Extensions 마이그레이션 리포트 ({env_choice}{" - Dry Run" if dry_run else ""})</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13px; color: #172B4D; padding: 20px; }}
        h2 {{ font-size: 18px; font-weight: 600; margin-bottom: 5px; }}
        .summary {{ color: #5E6C84; margin-bottom: 20px; font-size: 12px; }}
        .highlight {{ color: #DE350B; font-weight: bold; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ border: 1px solid #DFE1E6; padding: 8px; text-align: left; vertical-align: top; word-break: break-all; }}
        th {{ background: #F4F5F7; font-weight: 600; }}
        tr:nth-child(even) td {{ background: #FAFBFC; }}
        .jql {{ color: #0052CC; font-family: 'Courier New', monospace; font-size: 12px; font-weight: bold; }}
        .perm {{ color: #4C5D7A; font-size: 11px; }}
        .proj-badge {{ background: #EAE6FF; color: #403294; font-weight: 600; padding: 2px 6px; border-radius: 3px; font-size: 11px; display: inline-block; margin: 2px; }}
    </style>
</head>
<body>
    <h2>🚨 JQL Search Extensions 마이그레이션 검사 리포트 ({env_choice}{" [Dry Run]" if dry_run else ""})</h2>
    <p class="summary">스캔한 총 순정 필터: {total_scanned}개 | <span class="highlight">이전 필요한 확장 필터: {len(all_rows)}개</span></p>
    <table>
        <thead>
            <tr>
                <th style="width: 60px;">Filter ID</th>
                <th style="width: 150px;">필터명</th>
                <th style="width: 150px;">소유자</th>
                <th>JQL 구문 (Cloud 전수조사 대상)</th>
                <th style="width: 150px;">매칭 프로젝트 Key</th>
                <th style="width: 200px;">참조/공유 권한 (프로젝트)</th>
            </tr>
        </thead>
        <tbody>
    """

    for r in all_rows:
        safe_name = (
            str(r["name"])
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        safe_jql = (
            str(r["jql"])
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        
        # 매칭 프로젝트 배지 HTML 생성
        badges = "".join(f'<span class="proj-badge">{k}</span>' for k in sorted(r["matched_projects"]))

        html_content += f"""
                <tr>
                    <td>{r['id']}</td>
                    <td><b>{safe_name}</b></td>
                    <td>{r['owner']}</td>
                    <td class="jql">{safe_jql}</td>
                    <td>{badges}</td>
                    <td class="perm">{r['permissions']}</td>
                </tr>
        """

    html_content += """
            </tbody>
        </table>
    </body>
    </html>
    """

    with open(html_filename, "w", encoding="utf-8") as f:
        f.write(html_content)

    print("\n====================================================")
    print(f"🎉 리포트 생성이 완료되었습니다!")
    print(f"📊 발견된 마이그레이션 대상 필터 수: {len(all_rows)} 개")
    print(f"📁 저장된 파일명: {html_filename}")
    print("====================================================")


if __name__ == "__main__":
    get_all_migration_filters()
