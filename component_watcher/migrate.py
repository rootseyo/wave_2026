import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Windows 환경에서 Unix 전용 pwd 모듈 임포트 에러 방지
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

import json
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = (SCRIPT_DIR / ".." / "env").resolve()
DEFAULT_PROJECTS_FILE = (SCRIPT_DIR / ".." / "projects.txt").resolve()
LOG_FILE = str(SCRIPT_DIR / "log.txt")
DASHBOARD_FILE = str(SCRIPT_DIR / "dashboard.html")

if not ENV_PATH.is_file():
    raise FileNotFoundError(f"env 파일을 찾을 수 없습니다: {ENV_PATH}")

load_dotenv(dotenv_path=ENV_PATH, override=True)


def select_environments():
    print("\n" + "=" * 60)
    print("                  Jira 대상 환경 선택")
    print("=" * 60)
    print("[1/2] 소스 (Jira DC) 환경 선택:")
    print("  1. STG (Staging / 스테이징 DC 환경)")
    print("  2. OPS (Operations / 운영 DC 환경)")
    source_env = "STG"
    while True:
        try:
            choice = input("소스 환경 번호를 입력하세요 (1 또는 2, 기본값: 1): ").strip()
            if not choice or choice == "1":
                source_env = "STG"
                break
            if choice == "2":
                source_env = "OPS"
                break
            print("잘못된 입력입니다. 1 또는 2를 입력하세요.")
        except (KeyboardInterrupt, EOFError):
            print("\n작업이 취소되었습니다.")
            raise SystemExit(0)

    print("\n[2/2] 대상 (Jira Cloud) 환경 선택:")
    print("  1. SANDBOX (Sandbox Cloud 환경)")
    print("  2. OPS (Operations / 운영 Cloud 환경)")
    target_env = "SANDBOX"
    while True:
        try:
            choice = input("대상 환경 번호를 입력하세요 (1 또는 2, 기본값: 1): ").strip()
            if not choice or choice == "1":
                target_env = "SANDBOX"
                break
            if choice == "2":
                target_env = "OPS"
                break
            print("잘못된 입력입니다. 1 또는 2를 입력하세요.")
        except (KeyboardInterrupt, EOFError):
            print("\n작업이 취소되었습니다.")
            raise SystemExit(0)

    return source_env, target_env



def resolve_environments():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env", choices=["stg", "ops"])
    parser.add_argument("--source-env", choices=["stg", "ops"])
    parser.add_argument("--target-env", choices=["sandbox", "stg", "ops"])
    parser.add_argument("--target", choices=["sandbox", "stg", "ops"])
    args, _ = parser.parse_known_args()

    s_env = (args.source_env or args.env or "").upper()
    t_env = (args.target_env or args.target or "").upper()
    if t_env == "STG":
        t_env = "SANDBOX"

    if __name__ == "__main__" and any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        return s_env or "STG", t_env or "SANDBOX"

    env_s_cfg = os.getenv("COMPONENT_WATCHER_SOURCE_ENV") or os.getenv("COMPONENT_WATCHER_ENV")
    env_t_cfg = os.getenv("COMPONENT_WATCHER_TARGET_ENV")

    if env_s_cfg and env_s_cfg.upper() in {"STG", "OPS"}:
        s_env = s_env or env_s_cfg.upper()
    if env_t_cfg and env_t_cfg.upper() in {"SANDBOX", "STG", "OPS"}:
        t_env = t_env or ("SANDBOX" if env_t_cfg.upper() in {"SANDBOX", "STG"} else "OPS")

    if s_env and t_env:
        return s_env, t_env

    return select_environments()


def parse_bool(value, default=True):
    if value is None:
        return default
    return value.strip().lower() not in {"false", "0", "no", "off"}


ENV, TARGET_ENV = resolve_environments()
DC_ENV_PREFIX = f"JIRA_DC_{ENV}"
CLOUD_ENV_PREFIX = "JIRA_CLOUD_SANDBOX" if TARGET_ENV in {"STG", "SANDBOX"} else "JIRA_CLOUD_OPS"

DC_BASE_URL = os.getenv(f"{DC_ENV_PREFIX}_URL", "").rstrip("/")
DC_USER = os.getenv(f"{DC_ENV_PREFIX}_USERNAME", "")
DC_PASSWORD = os.getenv(f"{DC_ENV_PREFIX}_PASSWORD", "")

cloud_url = os.getenv(f"{CLOUD_ENV_PREFIX}_URL", "").rstrip("/")
CLOUD_USER = os.getenv(f"{CLOUD_ENV_PREFIX}_ADMIN_EMAIL", "")
CLOUD_TOKEN = os.getenv(f"{CLOUD_ENV_PREFIX}_API_TOKEN", "")

cw_api_url = (
    os.getenv(f"{CLOUD_ENV_PREFIX}_COMPONENT_WATCHER_API_URL")
    or os.getenv(f"{CLOUD_ENV_PREFIX}_CW_API_URL")
    or ""
).rstrip("/")
CLOUD_API_URL = cw_api_url or (f"{cloud_url}/rest/api/3" if cloud_url else "")

VERIFY_SSL = parse_bool(os.getenv("JIRA_SSL_VERIFY"), default=True)
if not VERIFY_SSL:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DC_AUTH = HTTPBasicAuth(DC_USER, DC_PASSWORD)
CLOUD_AUTH = HTTPBasicAuth(CLOUD_USER, CLOUD_TOKEN)
DC_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
CLOUD_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

EVENT_MAPPING = {
    1: "avi:jira:created:issue",
    2: "avi:jira:updated:issue",
    3: "avi:jira:assigned:issue",
    4: "avi:jira:updated:resolution",
    5: "avi:jira:updated:status",
    6: "avi:jira:commented:issue",
    10: "avi:jira:created:worklog",
    15: "avi:jira:updated:worklog",
    16: "avi:jira:deleted:worklog",
}

email_cache = {}
cloud_user_cache = {}
user_issue_logs = []
stats = {"projects": []}


class JiraAuthenticationError(RuntimeError):
    pass


def log_error(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def log_user_assignment_issue(project_key, component_name, username, email, reason):
    """사용자 할당 실패/이슈 발생 시 사용자별로 명확한 진단 로그를 기록한다."""
    user_info = f"이메일='{email}'" if email else f"계정명='{username}'"
    if email and username:
        user_info = f"이메일='{email}' (DC 계정: '{username}')"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[사용자 할당 이슈] {user_info} | 프로젝트='{project_key}', 컴포넌트='{component_name}' | 사유: {reason}"
    log_error(msg)
    user_issue_logs.append({
        "timestamp": timestamp,
        "project": project_key,
        "component": component_name,
        "username": username or "",
        "email": email or "",
        "reason": reason,
    })
    print(f"      - [사용자 할당 이슈] {user_info}: {reason}")


def log_http_error(context, response):
    """HTTP 진단 정보를 자격증명 노출 없이 기록한다."""
    if response is None:
        log_error(f"HTTP 진단 ({context}): 응답 객체 없음 (연결/DNS/타임아웃 가능성)")
        return

    request = response.request
    authorization = request.headers.get("Authorization", "") if request else ""
    auth_scheme = authorization.split(" ", 1)[0] if authorization else "none"
    redirect_chain = [
        {"status": item.status_code, "location": item.headers.get("Location", "")}
        for item in response.history
    ]
    diagnostic_headers = {
        name: response.headers[name]
        for name in (
            "WWW-Authenticate",
            "X-Seraph-LoginReason",
            "X-AREQUESTID",
            "Server",
            "Via",
            "Location",
        )
        if name in response.headers
    }
    response_preview = " ".join(response.text[:500].split())
    log_error(
        f"HTTP 진단 ({context}): status={response.status_code}, "
        f"request_method={request.method if request else 'unknown'}, "
        f"request_url={request.url if request else 'unknown'}, "
        f"final_url={response.url}, auth_header_present={bool(authorization)}, "
        f"auth_scheme={auth_scheme}, redirects={redirect_chain or 'none'}, "
        f"headers={diagnostic_headers or 'none'}, response_preview={response_preview or 'empty'}"
    )


def validate_configuration(require_cloud=False):
    required = {
        f"{DC_ENV_PREFIX}_URL": DC_BASE_URL,
        f"{DC_ENV_PREFIX}_USERNAME": DC_USER,
        f"{DC_ENV_PREFIX}_PASSWORD": DC_PASSWORD,
    }
    if require_cloud:
        required.update(
            {
                f"{CLOUD_ENV_PREFIX}_URL": cloud_url,
                f"{CLOUD_ENV_PREFIX}_ADMIN_EMAIL": CLOUD_USER,
                f"{CLOUD_ENV_PREFIX}_API_TOKEN": CLOUD_TOKEN,
            }
        )
    return [name for name, value in required.items() if not value]


def get_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


dc_session = get_session()
cloud_session = get_session()
# scan_projects.py 및 기존 호출부 호환용 별칭
session = dc_session


def raise_for_authentication_error(context, response):
    if response.status_code == 401:
        log_http_error(context, response)
        raise JiraAuthenticationError(f"{context}: Jira 인증 실패 (HTTP 401)")


def get_user_email(username):
    if not username:
        return None
    if username in email_cache:
        return email_cache[username]

    url = f"{DC_BASE_URL}/rest/api/2/user"
    try:
        response = dc_session.get(
            url,
            params={"username": username},
            headers=DC_HEADERS,
            auth=DC_AUTH,
            timeout=10,
            verify=VERIFY_SSL,
        )
        raise_for_authentication_error(f"사용자 {username} 이메일 조회", response)
        response.raise_for_status()
        email = response.json().get("emailAddress")
        email_cache[username] = email
        return email
    except JiraAuthenticationError:
        raise
    except requests.RequestException as e:
        log_error(f"사용자 이메일 조회 실패: username={username}, error={e}")
        log_http_error(f"사용자 {username} 이메일 조회", e.response)
        return None
    except ValueError as e:
        log_error(f"사용자 이메일 응답 JSON 파싱 실패: username={username}, error={e}")
        return None


def normalize_groups(raw_groups):
    groups = []
    for group in raw_groups:
        if isinstance(group, dict) and group.get("name"):
            groups.append(group["name"])
        elif isinstance(group, str):
            groups.append(group)
    return groups


def fetch_cloud_configurations_detail(project_key):
    """Jira Cloud에서 project_key에 대한 컴포넌트 와쳐 설정 상세 맵을 가져온다."""
    if not CLOUD_API_URL or not CLOUD_AUTH:
        return {}
    urls = [
        f"{cloud_url}/rest/componentwatcher/1.0/config/configurations/{project_key}",
        f"{CLOUD_API_URL.rstrip('/')}/configurations/{project_key}",
        f"{CLOUD_API_URL}?projectKey={project_key}",
    ]
    for url in urls:
        try:
            resp = cloud_session.get(
                url,
                headers=CLOUD_HEADERS,
                auth=CLOUD_AUTH,
                timeout=5,
                verify=VERIFY_SSL,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = (
                    data.get("data", data)
                    if isinstance(data, dict)
                    else data
                )
                if isinstance(items, list):
                    cloud_map = {}
                    for item in items:
                        if isinstance(item, dict) and item.get("componentName"):
                            comp_name = item["componentName"]
                            cloud_map[comp_name] = {
                                "users": item.get("users", []),
                                "groups": item.get("groups", []),
                            }
                    return cloud_map
        except Exception:
            pass
    return {}


def get_cloud_existing_components(project_key):
    """Jira Cloud에 이미 등록되어 있는 컴포넌트 와쳐 설정 목록을 조회한다."""
    cloud_map = fetch_cloud_configurations_detail(project_key)
    return set(cloud_map.keys())


def check_cloud_user_exists(email):
    """Jira Cloud에 해당 이메일을 가진 사용자가 존재하는지 확인한다."""
    if not email or not cloud_url or not CLOUD_AUTH:
        return True, None  # Cloud 정보 미설정 시 낙관적 허용

    email_lower = email.strip().lower()
    if email_lower in cloud_user_cache:
        return cloud_user_cache[email_lower]

    url = f"{cloud_url}/rest/api/3/user/search"
    try:
        resp = cloud_session.get(
            url,
            params={"query": email},
            headers=CLOUD_HEADERS,
            auth=CLOUD_AUTH,
            timeout=10,
            verify=VERIFY_SSL,
        )
        if resp.status_code == 200:
            users = resp.json()
            for u in users:
                u_email = (u.get("emailAddress") or "").strip().lower()
                if u_email == email_lower:
                    is_active = u.get("active", True)
                    account_id = u.get("accountId")
                    cloud_user_cache[email_lower] = (is_active, account_id)
                    return is_active, account_id
            if users:
                account_id = users[0].get("accountId")
                is_active = users[0].get("active", True)
                cloud_user_cache[email_lower] = (is_active, account_id)
                return is_active, account_id

        cloud_user_cache[email_lower] = (False, None)
        return False, None
    except Exception as e:
        log_error(f"Cloud 사용자 조회 중 오류: email={email}, error={e}")
        return True, None


def post_cloud_config(project_key, component_name, payload):
    """중복 생성 위험을 줄이기 위해 Cloud 쓰기 요청은 자동 재시도하지 않는다."""
    try:
        response = cloud_session.post(
            CLOUD_API_URL,
            json=payload,
            headers=CLOUD_HEADERS,
            auth=CLOUD_AUTH,
            timeout=15,
            verify=VERIFY_SSL,
        )
        resp_data = {}
        try:
            resp_data = response.json() if response.text else {}
        except Exception:
            pass

        if response.status_code in {200, 201, 204}:
            # API warnings 검사 (개별 사용자 할당 실패 등)
            warnings = resp_data.get("warnings", [])
            if warnings:
                for w in warnings:
                    log_user_assignment_issue(
                        project_key, component_name, "", None, f"Cloud API 경고: {w}"
                    )
            print("    마이그레이션 성공")
            return "SUCCESS", resp_data

        resp_text = response.text or ""
        if response.status_code == 400 and "already exist" in resp_text.lower():
            print("    [기존 설정 존재] Cloud에 이미 등록된 컴포넌트 설정입니다.")
            log_error(
                f"[기존 설정 존재] 프로젝트='{project_key}', 컴포넌트='{component_name}': "
                "Jira Cloud에 이미 컴포넌트 와쳐 설정이 등록되어 있습니다."
            )
            return "EXISTING", resp_data

        print(f"    마이그레이션 실패 ({response.status_code})")
        log_http_error(f"Cloud 마이그레이션 {project_key}/{component_name}", response)
        log_error(
            f"마이그레이션 실패: 프로젝트='{project_key}', 컴포넌트='{component_name}', "
            f"상태코드={response.status_code}, 응답={resp_text[:500]}"
        )
        return "FAILED", resp_data
    except requests.RequestException as e:
        print(f"    전송 오류: {e}")
        log_error(f"Cloud 전송 오류: 프로젝트='{project_key}', 컴포넌트='{component_name}', error={e}")
        log_http_error(f"Cloud 마이그레이션 {project_key}/{component_name}", getattr(e, "response", None))
        return "FAILED", None


def build_cloud_payload(project_key, config):
    component_name = config.get("componentName")
    users_obj = config.get("authorityDTO", {}).get("usersObj", [])
    groups = normalize_groups(config.get("authorityDTO", {}).get("groups", []))
    event_ids = config.get("eventTypes", [])

    watcher_count = len(users_obj)
    group_count = len(groups)

    user_emails = []
    user_issue_count = 0
    for user in users_obj:
        username = user.get("name")
        display_name = user.get("displayName", username)
        email = get_user_email(username)
        if email:
            user_emails.append(email)
        else:
            user_issue_count += 1
            log_user_assignment_issue(
                project_key,
                component_name,
                username,
                None,
                f"DC에서 이메일 정보를 가져올 수 없음 ({display_name})",
            )

    return {
        "projectKey": project_key,
        "componentName": component_name,
        "users": user_emails,
        "groups": groups,
        "events": [
            EVENT_MAPPING[event_id]
            for event_id in event_ids
            if event_id in EVENT_MAPPING
        ],
    }, watcher_count, group_count, user_issue_count



def format_count_info(watchers, groups, label_watcher="사용자", label_group="그룹"):
    """0건 항목은 제외하고 건수가 존재하는 항목만 괄호 형식으로 포맷팅한다."""
    parts = []
    if watchers > 0:
        parts.append(f"{label_watcher}: {watchers}명")
    if groups > 0:
        parts.append(f"{label_group}: {groups}개")
    if parts:
        return f"({', '.join(parts)})"
    return ""


def migrate_component_config(project_key, config, cloud_details_map):
    """
    단일 컴포넌트 설정을 마이그레이션한다:
    1단계: 사용자별 DC 이메일 및 Cloud 계정 사전 검증 (이슈 발생 시 사용자별 로그 기록)
    2단계: 설정 값(이벤트 알림 규칙, 그룹) 우선 마이그레이션
    3단계: 사용자 할당 및 Cloud API 경고 처리
    """
    component_name = config.get("componentName")
    users_obj = config.get("authorityDTO", {}).get("usersObj", [])
    raw_groups = config.get("authorityDTO", {}).get("groups", [])
    groups = normalize_groups(raw_groups)
    event_ids = config.get("eventTypes", [])
    events = [
        EVENT_MAPPING[event_id]
        for event_id in event_ids
        if event_id in EVENT_MAPPING
    ]

    target_comp = cloud_details_map.get(component_name)
    is_already_configured = target_comp is not None

    if is_already_configured:
        return "EXISTING", target_comp.get("users", []), target_comp.get("groups", [])

    # 1. 사용자별 검증 및 분리 (사용자 할당 사전 점검)
    valid_user_emails = []
    invalid_users_info = []

    for user in users_obj:
        username = user.get("name")
        display_name = user.get("displayName", username)
        email = get_user_email(username)

        if not email:
            log_user_assignment_issue(
                project_key,
                component_name,
                username,
                None,
                f"DC에서 이메일 정보를 가져올 수 없음 (표시이름: {display_name})",
            )
            invalid_users_info.append(
                {"username": username, "displayName": display_name, "reason": "DC 이메일 미발견"}
            )
            continue

        # Cloud 계정 존재 여부 검증
        is_active, _ = check_cloud_user_exists(email)
        if not is_active:
            log_user_assignment_issue(
                project_key,
                component_name,
                username,
                email,
                f"Jira Cloud에 계정이 존재하지 않거나 비활성화됨 (표시이름: {display_name})",
            )
            invalid_users_info.append(
                {"username": username, "displayName": display_name, "email": email, "reason": "Cloud 계정 미존재"}
            )
            continue

        valid_user_emails.append(email)

    # 2. 우선 설정 값 마이그레이션 (Base Configuration)
    # 사용자 할당과 설정 값을 분리:
    # 유효한 사용자만 포함하여 설정 값 생성을 방해하지 않도록 처리
    payload_users = list(valid_user_emails)

    # 만약 그룹도 없고 유효 사용자도 없는 경우 (모든 사용자에 이슈가 있는 경우)
    # 기존에는 API 400 no valid users or groups found 로 설정 값 자체가 날아갔음.
    # 설정 값(이벤트 규칙)을 보존하기 위해 관리자 계정을 fallback으로 임시 할당하여 기본 설정을 우선 생성!
    if not groups and not payload_users and users_obj:
        if CLOUD_USER:
            payload_users = [CLOUD_USER]
            log_error(
                f"[설정 값 우선 마이그레이션] 프로젝트='{project_key}', 컴포넌트='{component_name}': "
                f"유효한 와쳐 사용자가 없어 설정 값(이벤트 규칙 {len(events)}개)을 보존하기 위해 기본 관리자({CLOUD_USER})로 먼저 생성했습니다."
            )
            print(f"    [설정 값 우선 마이그레이션] 사용자 이슈로 인해 기본 관리자({CLOUD_USER})를 지정하여 컴포넌트 설정을 먼저 보존 생성합니다.")

    payload = {
        "projectKey": project_key,
        "componentName": component_name,
        "events": events,
        "groups": groups,
        "users": payload_users,
    }

    # 3. Cloud REST API 전송
    status, resp_data = post_cloud_config(project_key, component_name, payload)
    assigned_emails = payload_users if status in {"SUCCESS", "EXISTING"} else []
    assigned_groups = groups if status in {"SUCCESS", "EXISTING"} else []

    return status, assigned_emails, assigned_groups


def scan_project(project_key, project_idx, total_projects):
    """프로젝트 설정을 Jira DC에서 조회하고 Cloud 기존 설정과 비교 분석한다 (안전 스캔 모드)."""
    project_stat = {"key": project_key, "components": [], "failed": False}
    dc_config_url = f"{DC_BASE_URL}/rest/componentwatcher/1.0/config/configurations/{project_key}"
    print(f"\n[{project_idx}/{total_projects}] 프로젝트 '{project_key}' 설정을 스캔하는 중...")

    try:
        response = dc_session.get(
            dc_config_url,
            headers=DC_HEADERS,
            auth=DC_AUTH,
            timeout=10,
            verify=VERIFY_SSL,
        )
        raise_for_authentication_error(f"프로젝트 {project_key} 컴포넌트 설정 조회", response)
        response.raise_for_status()
        dc_data = response.json()
        if not isinstance(dc_data, dict) or not isinstance(dc_data.get("data", []), list):
            raise ValueError("예상하지 못한 Jira DC 응답 형식")
    except JiraAuthenticationError:
        project_stat["failed"] = True
        stats["projects"].append(project_stat)
        raise
    except (requests.RequestException, ValueError) as e:
        project_stat["failed"] = True
        stats["projects"].append(project_stat)
        error_msg = f"프로젝트 '{project_key}' 설정을 DC에서 가져오는데 실패했습니다: {e}"
        print(f"  [오류] {error_msg}")
        log_error(error_msg)
        log_http_error(f"프로젝트 {project_key} 컴포넌트 설정 조회", getattr(e, "response", None))
        return False

    configs = dc_data.get("data", [])
    print(f"  > 총 {len(configs)}개의 컴포넌트 설정 발견")

    cloud_details_map = fetch_cloud_configurations_detail(project_key)

    for config_idx, config in enumerate(configs, 1):
        component_name = config.get("componentName")
        try:
            payload, watcher_count, group_count, issue_count = build_cloud_payload(
                project_key, config
            )
        except JiraAuthenticationError:
            project_stat["failed"] = True
            stats["projects"].append(project_stat)
            raise

        target_comp = cloud_details_map.get(component_name)
        is_already_configured = target_comp is not None

        count_info = format_count_info(
            watcher_count, group_count, label_watcher="사용자", label_group="그룹"
        )
        info_suffix = f" {count_info}" if count_info else ""
        print(f"  [{config_idx}/{len(configs)}] 컴포넌트 스캔: {component_name}{info_suffix}")

        comp_status = "UNKNOWN"
        if is_already_configured:
            source_users = sorted(payload.get("users", []))
            target_users = sorted(target_comp.get("users", []))
            source_groups = sorted(payload.get("groups", []))
            target_groups = sorted(target_comp.get("groups", []))

            if source_users == target_users and source_groups == target_groups:
                comp_status = "MATCH"
            else:
                comp_status = "MISMATCH"

            print(f"    [기존 설정 존재 ({comp_status})] Cloud에 이미 등록된 컴포넌트 설정입니다.")
        else:
            comp_status = "SCAN_READY"

        t_emails = target_comp.get("users", []) if target_comp else []
        t_groups = target_comp.get("groups", []) if target_comp else []

        project_stat["components"].append(
            {
                "name": component_name,
                "raw_config": config,
                "watchers": watcher_count,
                "groups": group_count,
                "user_issue_count": issue_count,
                "watcher_emails": payload.get("users", []),
                "group_names": payload.get("groups", []),
                "source_watchers": watcher_count,
                "source_groups": group_count,
                "source_emails": payload.get("users", []),
                "source_group_names": payload.get("groups", []),
                "target_watchers": len(t_emails),
                "target_groups": len(t_groups),
                "target_emails": t_emails,
                "target_group_names": t_groups,
                "already_exists": is_already_configured,
                "status": comp_status,
            }
        )

    stats["projects"].append(project_stat)
    return not project_stat["failed"]


def migrate_project(project_key, project_idx, total_projects, dry_run=False):
    """스캔된 프로젝트의 컴포넌트 설정을 Jira Cloud로 마이그레이션한다."""
    project_stat = next((p for p in stats["projects"] if p["key"] == project_key), None)
    if not project_stat:
        scan_project(project_key, project_idx, total_projects)
        project_stat = next((p for p in stats["projects"] if p["key"] == project_key), None)

    if not project_stat or project_stat.get("failed"):
        return False

    components = project_stat.get("components", [])
    if not components:
        print(f"\n[{project_idx}/{total_projects}] 프로젝트 '{project_key}': 마이그레이션할 컴포넌트 설정 없음")
        return True

    print(f"\n[{project_idx}/{total_projects}] 프로젝트 '{project_key}' 마이그레이션 진행 중 (컴포넌트 {len(components)}개)...")
    cloud_details_map = fetch_cloud_configurations_detail(project_key)

    for config_idx, comp in enumerate(components, 1):
        component_name = comp["name"]
        raw_config = comp.get("raw_config")
        is_already_configured = comp.get("already_exists", False)
        comp_status = comp.get("status", "UNKNOWN")

        count_info = format_count_info(
            comp.get("source_watchers", 0), comp.get("source_groups", 0), label_watcher="사용자", label_group="그룹"
        )
        info_suffix = f" {count_info}" if count_info else ""
        print(f"  [{config_idx}/{len(components)}] 마이그레이션 처리: {component_name}{info_suffix}")

        if dry_run:
            print("    [DRY-RUN] 데이터 변환 및 검증 완료")
            if not is_already_configured:
                comp["status"] = "DRY-RUN"
            continue

        if is_already_configured:
            print(f"    [건너뜀] 이미 Jira Cloud에 존재하는 컴포넌트 설정입니다 ({comp_status}).")
            continue

        if not raw_config:
            continue

        # 설정 값 우선 마이그레이션 + 사용자 할당 분리 실행
        res_status, assigned_emails, assigned_groups = migrate_component_config(
            project_key, raw_config, cloud_details_map
        )
        comp["status"] = res_status
        if res_status in {"SUCCESS", "EXISTING"}:
            comp["target_emails"] = assigned_emails
            comp["target_groups"] = assigned_groups
            comp["target_watchers"] = len(assigned_emails)
            comp["target_groups"] = len(assigned_groups)
        else:
            project_stat["failed"] = True

    return not project_stat["failed"]


def validate_cloud_configuration():
    required = {
        f"{CLOUD_ENV_PREFIX}_URL": cloud_url,
        f"{CLOUD_ENV_PREFIX}_ADMIN_EMAIL": CLOUD_USER,
        f"{CLOUD_ENV_PREFIX}_API_TOKEN": CLOUD_TOKEN,
    }
    return [name for name, value in required.items() if not value]


def test_cloud_api(project_key=None, file_path=DEFAULT_PROJECTS_FILE):
    """
    Cloud REST API (Cloud Ops / Target) 조회 기능 동작을 테스트한다.
    GET 요청만 수행하며 데이터를 변경하지 않는다.
    """
    print("\n" + "=" * 60)
    print(f"       Cloud REST API 조회 테스트 (환경: {ENV} / {CLOUD_ENV_PREFIX})")
    print("=" * 60)

    missing_vars = validate_cloud_configuration()
    if missing_vars:
        print(f"[오류] Cloud 환경변수 설정이 부족합니다: {', '.join(missing_vars)}")
        log_error(f"Cloud REST API 테스트 실패: 환경변수 미설정 ({', '.join(missing_vars)})")
        return False

    print(f"• Cloud URL                 : {cloud_url}")
    print(f"• Component Watcher API URL : {CLOUD_API_URL or '미설정'}")
    print(f"• 계정 이메일               : {CLOUD_USER}")
    print(f"• API 토큰                  : {'*' * 8 if CLOUD_TOKEN else '미설정'}")
    print(f"• SSL 검증 여부             : {VERIFY_SSL}")

    # 1. Jira Cloud 사용자 정보 조회 테스트 (GET /rest/api/3/myself)
    myself_url = f"{cloud_url}/rest/api/3/myself"
    print(f"\n1. Jira Cloud 인증 및 GET API 테스트 중...")
    print(f"   요청 URL: GET {myself_url}")

    auth_ok = False
    try:
        response = cloud_session.get(
            myself_url,
            headers=CLOUD_HEADERS,
            auth=CLOUD_AUTH,
            timeout=10,
            verify=VERIFY_SSL,
        )
        if response.status_code == 200:
            user_data = response.json()
            display_name = user_data.get("displayName", "N/A")
            account_id = user_data.get("accountId", "N/A")
            email_addr = user_data.get("emailAddress", "N/A")
            print(f"   [성공] 인증 및 조회 성공 (Status: 200)")
            print(f"     - 표시 이름: {display_name}")
            print(f"     - 계정 ID: {account_id}")
            print(f"     - 이메일: {email_addr}")
            auth_ok = True
        else:
            print(f"   [실패] HTTP {response.status_code}")
            log_http_error("Cloud REST API (myself) 조회 테스트", response)
            return False
    except requests.RequestException as e:
        print(f"   [오류] 요청 실패: {e}")
        log_error(f"Cloud REST API (myself) 조회 실패: {e}")
        return False

    # 2. 프로젝트 키 입력받기 (--project 인자가 지정되지 않은 경우 프롬프트에서 요청)
    if not project_key:
        try:
            project_key = input(
                "\n조회 테스트할 프로젝트 키를 입력하세요 (예: DEMO, 엔터 시 건너뜀): "
            ).strip().upper()
        except (KeyboardInterrupt, EOFError):
            project_key = ""

    if not project_key:
        print("\n프로젝트 키가 입력되지 않아 기본 인증 테스트(myself GET) 성공 결과만 출력하고 종료합니다.")
        print("=" * 60)
        return auth_ok

    print(f"\n2. 프로젝트 '{project_key}' REST API 조회 테스트 중...")

    # (2-1) Jira Standard Project API 조회
    project_url = f"{cloud_url}/rest/api/3/project/{project_key}"
    print(f"   (A) Jira 프로젝트 정보 조회 (GET {project_url})")
    try:
        p_resp = cloud_session.get(
            project_url,
            headers=CLOUD_HEADERS,
            auth=CLOUD_AUTH,
            timeout=10,
            verify=VERIFY_SSL,
        )
        if p_resp.status_code == 200:
            p_data = p_resp.json()
            print(f"       [성공] 프로젝트명: '{p_data.get('name')}', ID: {p_data.get('id')}")
        else:
            print(f"       [참고] HTTP {p_resp.status_code}")
            log_http_error(f"Cloud REST API project/{project_key} 조회", p_resp)
    except requests.RequestException as e:
        print(f"       [오류] {e}")

    # (2-2) Component Watcher / App Config 엔드포인트 조회
    config_urls = [
        (
            "Component Watcher Cloud API Base",
            CLOUD_API_URL,
        ),
        (
            "Custom API (/configurations)",
            f"{CLOUD_API_URL.rstrip('/')}/configurations/{project_key}",
        ),
        (
            "App API (rest/componentwatcher)",
            f"{cloud_url}/rest/componentwatcher/1.0/config/configurations/{project_key}",
        ),
    ]

    print(f"   (B) Component Watcher 설정 엔드포인트 응답 테스트")
    cw_success = False
    for label, url in config_urls:
        try:
            cw_resp = cloud_session.get(
                url,
                headers=CLOUD_HEADERS,
                auth=CLOUD_AUTH,
                timeout=10,
                verify=VERIFY_SSL,
            )
            if cw_resp.status_code == 200:
                cw_data = cw_resp.json()
                items = (
                    cw_data.get("data", cw_data)
                    if isinstance(cw_data, dict)
                    else cw_data
                )
                item_count = (
                    len(items) if isinstance(items, list) else (1 if items else 0)
                )
                print(f"       [성공] {label}: {item_count}개 설정 조회됨 (Status: 200)")
                cw_success = True
                break
            elif cw_resp.status_code == 405:
                print(
                    f"       [확인] {label}: HTTP 405 (엔드포인트 접속 확인됨 - POST 전용 엔드포인트)"
                )
                cw_success = True
            else:
                print(f"       [참고] {label}: HTTP {cw_resp.status_code}")
        except requests.RequestException as e:
            print(f"       [오류] {label}: {e}")

    if not cw_success:
        print("       [참고] Component Watcher 전용 API 조회가 안되거나 대상 설정이 등록되어 있지 않을 수 있습니다.")

    print("\n" + "=" * 60)
    print(f"  Cloud REST API ({CLOUD_ENV_PREFIX}) 조회 테스트 완료")
    print("=" * 60 + "\n")
    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description="Jira Component Watcher 스캔 및 마이그레이션 도구"
    )
    parser.add_argument(
        "--env", choices=["stg", "ops"], help="소스(Jira DC) 환경 선택"
    )
    parser.add_argument(
        "--source-env", choices=["stg", "ops"], help="소스(Jira DC) 환경 선택"
    )
    parser.add_argument(
        "--target-env",
        choices=["sandbox", "stg", "ops"],
        help="대상(Jira Cloud) 환경 선택",
    )
    parser.add_argument(
        "--target",
        choices=["sandbox", "stg", "ops"],
        help="대상(Jira Cloud) 환경 선택",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="스캔 및 대시보드 생성만 수행 (Jira Cloud 마이그레이션 미수행)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="--scan-only와 동일 (스캔 및 데이터 검증만 수행)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="스캔 후 마이그레이션 진행 여부 프롬프트 없이 즉시 진행",
    )
    parser.add_argument(
        "--real-run",
        action="store_true",
        help="스캔 후 바로 실제 마이그레이션 실행",
    )
    parser.add_argument(
        "--test-cloud",
        action="store_true",
        help="Cloud REST API (Cloud Target) 조회 동작 테스트",
    )
    parser.add_argument(
        "--file",
        default=str(DEFAULT_PROJECTS_FILE),
        help="프로젝트 파일 (기본값: ../projects.txt)",
    )
    parser.add_argument("--project", help="테스트 시 사용할 특정 프로젝트 키")
    return parser.parse_args()


def print_target_domain_info(mode_name=""):
    print("\n" + "=" * 60)
    print(
        f"  [작업 대상 도메인 확인] 소스: {ENV} ({DC_ENV_PREFIX}) ➔ "
        f"대상: {TARGET_ENV} ({CLOUD_ENV_PREFIX})"
    )
    if mode_name:
        print(f"  실행 모드: {mode_name}")
    print("=" * 60)
    print(f"• Source Jira DC URL           : {DC_BASE_URL or '(미설정)'}")
    print(f"• Target Jira Cloud URL        : {cloud_url or '(미설정)'}")
    print(f"• Component Watcher Cloud API  : {CLOUD_API_URL or '(미설정)'}")
    print("=" * 60)


def interactive_select_mode(args):
    """CLI 파라미터가 명시되지 않은 경우 대화형 메뉴로 작업 모드를 선택받는다."""
    # 이미 CLI 옵션이 주어진 경우 프롬프트 생략
    if args.scan_only or args.dry_run or args.yes or args.real_run or args.test_cloud:
        return args

    print("\n" + "=" * 60)
    print("             Jira Component Watcher 실행 모드 선택")
    print("=" * 60)
    print("1. [추천] 스캔 후 확인하여 마이그레이션")
    print("   ➔ 먼저 DC 설정을 분석하고 대시보드를 생성한 후, 진행 여부를 물어봅니다.")
    print("2. 스캔 전용 모드 (조회 및 대시보드 리포트 생성만 수행)")
    print("   ➔ Jira DC 설정을 스캔하여 dashboard.html을 생성합니다 (Cloud 마이그레이션 안 함).")
    print("3. 원클릭 마이그레이션 모드 (자동 진행)")
    print("   ➔ 스캔 후 확인 질문 없이 즉시 Jira Cloud로 마이그레이션을 진행합니다.")
    print("4. Jira Cloud REST API 연결 및 응답 테스트")
    print("   ➔ Jira Cloud 및 Component Watcher API 연결 상태를 점검합니다.")
    print("=" * 60)

    while True:
        try:
            choice = input("실행할 모드를 선택하세요 (1~4, 기본값: 1): ").strip()
            if not choice or choice == "1":
                # 기본값: 스캔 후 확인하여 마이그레이션
                break
            elif choice == "2":
                args.scan_only = True
                break
            elif choice == "3":
                args.yes = True
                break
            elif choice == "4":
                args.test_cloud = True
                break
            else:
                print("잘못된 입력입니다. 1, 2, 3, 4 중에서 선택해주세요.")
        except (KeyboardInterrupt, EOFError):
            print("\n작업이 취소되었습니다.")
            raise SystemExit(0)

    return args


def interactive_select_project_file(args):
    """사용자가 프로젝트 파일 경로를 직접 지정하거나 기본값을 선택할 수 있도록 한다."""
    # CLI에 --file 인자가 명시되지 않았고 대화형 환경인 경우
    if sys.stdin.isatty() and not any(arg.startswith("--file") for arg in sys.argv[1:]):
        print(f"\n대상 프로젝트 파일 경로 (기본값: {DEFAULT_PROJECTS_FILE})")
        try:
            custom_file = input("파일 경로를 입력하세요 (엔터 시 기본값 사용): ").strip()
            if custom_file:
                args.file = custom_file
        except (KeyboardInterrupt, EOFError):
            pass
    return args




def load_projects(file_path):
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"프로젝트 파일을 찾을 수 없습니다: {path}")
    projects = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return list(dict.fromkeys(projects)), path


def generate_dashboard_html(projects_stats=None, output_path=DASHBOARD_FILE, env_name=None):
    if projects_stats is None:
        projects_stats = stats["projects"]
    if env_name is None:
        env_name = ENV
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats_json = json.dumps(projects_stats, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jira Component Watcher 스캔 대시보드 ({env_name.upper()})</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --border-color: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent-blue: #3b82f6;
            --accent-blue-bg: rgba(59, 130, 246, 0.15);
            --accent-green: #10b981;
            --accent-green-bg: rgba(16, 185, 129, 0.15);
            --accent-purple: #8b5cf6;
            --accent-purple-bg: rgba(139, 92, 246, 0.15);
            --accent-amber: #f59e0b;
            --accent-amber-bg: rgba(245, 158, 11, 0.15);
            --accent-pink: #ec4899;
            --accent-pink-bg: rgba(236, 72, 153, 0.15);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            padding: 2rem;
            min-height: 100vh;
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
            padding-bottom: 1.5rem;
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 1rem;
        }}

        .header-title h1 {{
            font-size: 1.75rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}

        .env-badge {{
            font-size: 0.85rem;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            background: var(--accent-blue-bg);
            color: var(--accent-blue);
            border: 1px solid rgba(59, 130, 246, 0.3);
            font-weight: 600;
        }}

        .header-meta {{
            color: var(--text-secondary);
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 1rem;
        }}

        .meta-item {{
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }}

        /* Stat Cards */
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}

        .stat-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 1.25rem;
            transition: transform 0.2s ease, border-color 0.2s ease;
            position: relative;
            overflow: hidden;
        }}

        .stat-card:hover {{
            transform: translateY(-2px);
            border-color: var(--accent-blue);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 3px;
        }}

        .stat-card.blue::before {{ background: var(--accent-blue); }}
        .stat-card.green::before {{ background: var(--accent-green); }}
        .stat-card.purple::before {{ background: var(--accent-purple); }}
        .stat-card.amber::before {{ background: var(--accent-amber); }}
        .stat-card.pink::before {{ background: var(--accent-pink); }}

        .stat-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--text-secondary);
            font-size: 0.875rem;
            font-weight: 500;
            margin-bottom: 0.5rem;
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.03em;
            margin-bottom: 0.25rem;
        }}

        .stat-sub {{
            font-size: 0.8rem;
            color: var(--text-secondary);
        }}

        /* Progress Bar in Stat */
        .progress-bar-bg {{
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            margin-top: 0.75rem;
            overflow: hidden;
        }}

        .progress-bar-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.5s ease;
        }}

        /* Controls / Filter Bar */
        .controls-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 1.25rem;
            margin-bottom: 2rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .controls-top {{
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
        }}

        .search-wrapper {{
            position: relative;
            flex: 1;
            min-width: 280px;
        }}

        .search-wrapper svg {{
            position: absolute;
            left: 1rem;
            top: 50%;
            transform: translateY(-50%);
            width: 18px;
            height: 18px;
            fill: var(--text-secondary);
        }}

        .search-input {{
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.75rem;
            background-color: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            color: var(--text-primary);
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s ease;
        }}

        .search-input:focus {{
            border-color: var(--accent-blue);
        }}

        .filter-group {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }}

        .filter-btn {{
            padding: 0.5rem 1rem;
            background-color: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            color: var(--text-secondary);
            font-size: 0.85rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }}

        .filter-btn:hover {{
            background-color: var(--bg-card-hover);
            color: var(--text-primary);
        }}

        .filter-btn.active {{
            background-color: var(--accent-blue);
            color: #ffffff;
            border-color: var(--accent-blue);
        }}

        .sort-select {{
            padding: 0.5rem 1rem;
            background-color: var(--bg-primary);
            border: 1px solid var(--border-color);
            border-radius: 0.5rem;
            color: var(--text-primary);
            font-size: 0.85rem;
            outline: none;
            cursor: pointer;
        }}

        /* Projects View */
        .projects-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
            font-size: 0.95rem;
            color: var(--text-secondary);
        }}

        .projects-grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 1rem;
        }}

        .project-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            overflow: hidden;
            transition: border-color 0.2s ease;
        }}

        .project-card:hover {{
            border-color: rgba(255, 255, 255, 0.2);
        }}

        .project-card-header {{
            padding: 1rem 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background-color: rgba(255, 255, 255, 0.02);
            cursor: pointer;
            user-select: none;
        }}

        .project-key-badge {{
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}

        .tag-pill {{
            font-size: 0.75rem;
            padding: 0.2rem 0.6rem;
            border-radius: 0.25rem;
            font-weight: 600;
        }}

        .tag-configured {{
            background-color: var(--accent-green-bg);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .tag-empty {{
            background-color: rgba(148, 163, 184, 0.1);
            color: var(--text-secondary);
            border: 1px solid rgba(148, 163, 184, 0.2);
        }}

        .project-summary-badges {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}

        .badge-count {{
            font-size: 0.8rem;
            padding: 0.25rem 0.6rem;
            border-radius: 0.375rem;
            background: var(--bg-primary);
            color: var(--text-secondary);
            border: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 0.3rem;
        }}

        .badge-count strong {{
            color: var(--text-primary);
        }}

        .chevron {{
            width: 20px;
            height: 20px;
            fill: var(--text-secondary);
            transition: transform 0.2s ease;
        }}

        .project-card.expanded .chevron {{
            transform: rotate(180deg);
        }}

        /* Component Details Table */
        .project-details {{
            display: none;
            padding: 0 1.25rem 1.25rem 1.25rem;
            border-top: 1px solid var(--border-color);
        }}

        .project-card.expanded .project-details {{
            display: block;
        }}

        .comp-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
            font-size: 0.875rem;
        }}

        .comp-table th {{
            text-align: left;
            padding: 0.6rem 0.75rem;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border-color);
            font-weight: 500;
        }}

        .comp-table td {{
            padding: 0.75rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            vertical-align: top;
        }}

        .comp-table tr:last-child td {{
            border-bottom: none;
        }}

        .comp-name {{
            font-weight: 600;
            color: var(--accent-blue);
        }}

        .chip-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
        }}

        .user-chip {{
            background: var(--accent-purple-bg);
            color: #c4b5fd;
            border: 1px solid rgba(139, 92, 246, 0.3);
            font-size: 0.78rem;
            padding: 0.15rem 0.5rem;
            border-radius: 0.25rem;
        }}

        .group-chip {{
            background: var(--accent-amber-bg);
            color: #fde68a;
            border: 1px solid rgba(245, 158, 11, 0.3);
            font-size: 0.78rem;
            padding: 0.15rem 0.5rem;
            border-radius: 0.25rem;
        }}

        .no-data {{
            color: var(--text-secondary);
            font-style: italic;
            font-size: 0.8rem;
        }}

        /* Empty state */
        .empty-results {{
            text-align: center;
            padding: 4rem 2rem;
            background: var(--bg-card);
            border-radius: 0.75rem;
            border: 1px dashed var(--border-color);
            color: var(--text-secondary);
        }}

        /* Top Charts section */
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}

        .chart-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 1.25rem;
        }}

        .chart-card h3 {{
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 1rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .bar-item {{
            margin-bottom: 0.75rem;
        }}

        .bar-info {{
            display: flex;
            justify-content: space-between;
            font-size: 0.85rem;
            margin-bottom: 0.25rem;
        }}

        .bar-track {{
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
            overflow: hidden;
        }}

        .bar-fill {{
            height: 100%;
            border-radius: 4px;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple));
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="header-title">
                <h1>
                    <span>📊 Component Watcher 대시보드</span>
                    <span class="env-badge">{env_name.upper()} 환경</span>
                </h1>
            </div>
            <div class="header-meta">
                <div class="meta-item">
                    <span>🕒 생성 시각:</span>
                    <strong>{now_str}</strong>
                </div>
            </div>
        </header>

        <!-- Stat Cards -->
        <div class="stats-grid" id="statCards">
            <!-- Rendered by JS -->
        </div>

        <!-- Charts Grid -->
        <div class="charts-grid" id="chartsGrid">
            <!-- Rendered by JS -->
        </div>

        <!-- Controls / Search & Filters -->
        <div class="controls-card">
            <div class="controls-top">
                <div class="search-wrapper">
                    <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
                    <input type="text" id="searchInput" class="search-input" placeholder="프로젝트 키, 컴포넌트명, 사용자 이메일, 그룹명 검색...">
                </div>
                <div class="filter-group" id="filterButtons">
                    <button class="filter-btn active" data-filter="all">전체 프로젝트</button>
                    <button class="filter-btn" data-filter="configured">설정 존재만</button>
                    <button class="filter-btn" data-filter="users">사용자 와쳐 있음</button>
                    <button class="filter-btn" data-filter="groups">그룹 와쳐 있음</button>
                    <button class="filter-btn" data-filter="empty">설정 없음</button>
                </div>
                <select id="sortSelect" class="sort-select">
                    <option value="key-asc">프로젝트 키 (오름차순)</option>
                    <option value="key-desc">프로젝트 키 (내림차순)</option>
                    <option value="comp-desc">컴포넌트 설정수 (높은순)</option>
                    <option value="watchers-desc">와쳐 사용자수 (높은순)</option>
                    <option value="groups-desc">와쳐 그룹수 (높은순)</option>
                </select>
            </div>
        </div>

        <!-- Projects Header -->
        <div class="projects-header">
            <span id="resultsCount">결과 불러오는 중...</span>
            <button class="filter-btn" id="toggleExpandBtn">모두 펼치기 / 접기</button>
        </div>

        <!-- Projects List -->
        <div class="projects-grid" id="projectsGrid">
            <!-- Rendered by JS -->
        </div>
    </div>

    <script>
        const rawData = {stats_json};
        
        // Data Calculations
        let allProjects = rawData || [];
        let currentFilter = 'all';
        let currentSearch = '';
        let currentSort = 'key-asc';
        let allExpanded = false;

        function initStats() {{
            const totalProjects = allProjects.length;
            const configuredProjects = allProjects.filter(p => p.components && p.components.length > 0);
            const emptyProjectsCount = totalProjects - configuredProjects.length;
            
            let totalComponents = 0;
            let totalWatcherUsers = 0;
            let totalWatcherGroups = 0;
            const uniqueUsers = new Set();
            const uniqueGroups = new Set();
            const projectWatcherCounts = [];

            configuredProjects.forEach(p => {{
                let projUsers = 0;
                let projGroups = 0;
                p.components.forEach(c => {{
                    totalComponents++;
                    const uCnt = c.source_watchers || c.watchers || 0;
                    const gCnt = c.source_groups || c.groups || 0;
                    totalWatcherUsers += uCnt;
                    totalWatcherGroups += gCnt;
                    projUsers += uCnt;
                    projGroups += gCnt;

                    (c.source_emails || c.watcher_emails || []).forEach(u => uniqueUsers.add(u));
                    (c.source_group_names || c.group_names || []).forEach(g => uniqueGroups.add(g));
                }});
                projectWatcherCounts.push({{
                    key: p.key,
                    comps: p.components.length,
                    users: projUsers,
                    groups: projGroups,
                    totalWatchers: projUsers + projGroups
                }});
            }});

            const configPct = totalProjects > 0 ? ((configuredProjects.length / totalProjects) * 100).toFixed(1) : 0;

            // Render Stat Cards
            const statCardsEl = document.getElementById('statCards');
            statCardsEl.innerHTML = `
                <div class="stat-card blue">
                    <div class="stat-header">
                        <span>총 대상 프로젝트</span>
                        <span>📁</span>
                    </div>
                    <div class="stat-value">${{totalProjects}}</div>
                    <div class="stat-sub">스캔 대상 전체 프로젝트</div>
                </div>
                <div class="stat-card green">
                    <div class="stat-header">
                        <span>설정 보유 프로젝트</span>
                        <span>⚙️</span>
                    </div>
                    <div class="stat-value">${{configuredProjects.length}} <span style="font-size:1.1rem; font-weight:400; color:var(--accent-green);">(${{configPct}}%)</span></div>
                    <div class="stat-sub">설정 없음: ${{emptyProjectsCount}}개 프로젝트</div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: ${{configPct}}%; background: var(--accent-green);"></div>
                    </div>
                </div>
                <div class="stat-card purple">
                    <div class="stat-header">
                        <span>컴포넌트 설정 총합</span>
                        <span>🧩</span>
                    </div>
                    <div class="stat-value">${{totalComponents}}</div>
                    <div class="stat-sub">등록된 전체 Component 규칙</div>
                </div>
                <div class="stat-card amber">
                    <div class="stat-header">
                        <span>와쳐 지정 사용자</span>
                        <span>👤</span>
                    </div>
                    <div class="stat-value">${{uniqueUsers.size}} <span style="font-size:1.1rem; font-weight:400; color:var(--text-secondary);">(누적 ${{totalWatcherUsers}}명)</span></div>
                    <div class="stat-sub">중복 제외 유니크 사용자 이메일 수</div>
                </div>
                <div class="stat-card pink">
                    <div class="stat-header">
                        <span>와쳐 지정 그룹</span>
                        <span>👥</span>
                    </div>
                    <div class="stat-value">${{uniqueGroups.size}} <span style="font-size:1.1rem; font-weight:400; color:var(--text-secondary);">(누적 ${{totalWatcherGroups}}개)</span></div>
                    <div class="stat-sub">중복 제외 유니크 와쳐 그룹 수</div>
                </div>
            `;

            // Render Charts
            renderCharts(projectWatcherCounts, configuredProjects.length, emptyProjectsCount, uniqueUsers.size, uniqueGroups.size);
        }}

        function renderCharts(projectWatcherCounts, configuredCount, emptyCount, userCount, groupCount) {{
            const chartsGrid = document.getElementById('chartsGrid');
            
            // Top 5 Projects by Total Watcher Settings
            const topProjects = [...projectWatcherCounts].sort((a, b) => b.totalWatchers - a.totalWatchers || b.comps - a.comps).slice(0, 5);
            const maxWatchers = topProjects.length > 0 ? Math.max(...topProjects.map(p => p.totalWatchers), 1) : 1;

            let topHtml = topProjects.map(p => {{
                const pct = ((p.totalWatchers / maxWatchers) * 100).toFixed(0);
                return `
                    <div class="bar-item">
                        <div class="bar-info">
                            <strong>${{p.key}}</strong>
                            <span>컴포넌트 ${{p.comps}}개 | 와쳐 ${{p.users}}명, 그룹 ${{p.groups}}개</span>
                        </div>
                        <div class="bar-track">
                            <div class="bar-fill" style="width: ${{pct}}%;"></div>
                        </div>
                    </div>
                `;
            }}).join('');

            if (topProjects.length === 0) {{
                topHtml = '<div class="no-data">와쳐 설정이 등록된 프로젝트가 없습니다.</div>';
            }}

            chartsGrid.innerHTML = `
                <div class="chart-card">
                    <h3>🔥 와쳐 설정이 가장 많은 프로젝트 TOP 5</h3>
                    <div>${{topHtml}}</div>
                </div>
                <div class="chart-card">
                    <h3>💡 와쳐 유형별 점유 비율</h3>
                    <div style="display: flex; gap: 1rem; margin-top: 1rem; align-items: center; justify-content: space-around;">
                        <div style="text-align: center;">
                            <div style="font-size: 1.8rem; font-weight: 700; color: var(--accent-purple);">${{userCount}}</div>
                            <div style="font-size: 0.85rem; color: var(--text-secondary);">유니크 사용자</div>
                        </div>
                        <div style="font-size: 1.5rem; color: var(--border-color);">/</div>
                        <div style="text-align: center;">
                            <div style="font-size: 1.8rem; font-weight: 700; color: var(--accent-amber);">${{groupCount}}</div>
                            <div style="font-size: 0.85rem; color: var(--text-secondary);">유니크 그룹</div>
                        </div>
                    </div>
                    <div class="progress-bar-bg" style="height: 12px; margin-top: 1.25rem;">
                        <div style="display: flex; height: 100%;">
                            <div style="width: ${{ (userCount + groupCount > 0) ? (userCount / (userCount + groupCount) * 100) : 50 }}%; background: var(--accent-purple);"></div>
                            <div style="width: ${{ (userCount + groupCount > 0) ? (groupCount / (userCount + groupCount) * 100) : 50 }}%; background: var(--accent-amber);"></div>
                        </div>
                    </div>
                </div>
            `;
        }}

        function filterAndRender() {{
            const searchLower = currentSearch.toLowerCase().trim();

            let filtered = allProjects.filter(p => {{
                const comps = p.components || [];
                const hasConfig = comps.length > 0;
                
                let hasUserWatchers = false;
                let hasGroupWatchers = false;

                comps.forEach(c => {{
                    if ((c.source_watchers || c.watchers || 0) > 0) hasUserWatchers = true;
                    if ((c.source_groups || c.groups || 0) > 0) hasGroupWatchers = true;
                }});

                // Category Filter
                if (currentFilter === 'configured' && !hasConfig) return false;
                if (currentFilter === 'users' && !hasUserWatchers) return false;
                if (currentFilter === 'groups' && !hasGroupWatchers) return false;
                if (currentFilter === 'empty' && hasConfig) return false;

                // Search Query Filter
                if (searchLower) {{
                    const keyMatch = p.key.toLowerCase().includes(searchLower);
                    const compMatch = comps.some(c => {{
                        const nameMatch = c.name && c.name.toLowerCase().includes(searchLower);
                        const userMatch = (c.source_emails || c.watcher_emails || []).some(u => u.toLowerCase().includes(searchLower));
                        const groupMatch = (c.source_group_names || c.group_names || []).some(g => g.toLowerCase().includes(searchLower));
                        return nameMatch || userMatch || groupMatch;
                    }});
                    return keyMatch || compMatch;
                }}

                return true;
            }});

            // Sorting
            filtered.sort((a, b) => {{
                const compsA = a.components || [];
                const compsB = b.components || [];
                
                const watchersA = compsA.reduce((sum, c) => sum + (c.source_watchers || c.watchers || 0), 0);
                const watchersB = compsB.reduce((sum, c) => sum + (c.source_watchers || c.watchers || 0), 0);

                const groupsA = compsA.reduce((sum, c) => sum + (c.source_groups || c.groups || 0), 0);
                const groupsB = compsB.reduce((sum, c) => sum + (c.source_groups || c.groups || 0), 0);

                if (currentSort === 'key-asc') return a.key.localeCompare(b.key);
                if (currentSort === 'key-desc') return b.key.localeCompare(a.key);
                if (currentSort === 'comp-desc') return compsB.length - compsA.length || a.key.localeCompare(b.key);
                if (currentSort === 'watchers-desc') return watchersB - watchersA || a.key.localeCompare(b.key);
                if (currentSort === 'groups-desc') return groupsB - groupsA || a.key.localeCompare(b.key);
                return 0;
            }});

            document.getElementById('resultsCount').innerText = `총 ${{filtered.length}}개 프로젝트 표시 중 (전체 ${{allProjects.length}}개 중)`;

            renderProjectsList(filtered);
        }}

        function renderProjectsList(projects) {{
            const grid = document.getElementById('projectsGrid');
            if (projects.length === 0) {{
                grid.innerHTML = `
                    <div class="empty-results">
                        <h3>🔍 검색 결과가 없습니다</h3>
                        <p style="margin-top:0.5rem;">검색어나 필터 조건을 변경해 보세요.</p>
                    </div>
                `;
                return;
            }}

            grid.innerHTML = projects.map(p => {{
                const comps = p.components || [];
                const hasConfig = comps.length > 0;
                
                let totalUserCount = 0;
                let totalGroupCount = 0;

                comps.forEach(c => {{
                    totalUserCount += (c.source_watchers || c.watchers || 0);
                    totalGroupCount += (c.source_groups || c.groups || 0);
                }});

                const statusTag = hasConfig 
                    ? `<span class="tag-pill tag-configured">설정 존재 (${{comps.length}}개)</span>`
                    : `<span class="tag-pill tag-empty">설정 없음</span>`;

                const compRows = comps.map(c => {{
                    const uList = (c.source_emails || c.watcher_emails || []);
                    const gList = (c.source_group_names || c.group_names || []);

                    const uChips = uList.length > 0 
                        ? uList.map(u => `<span class="user-chip">${{u}}</span>`).join('')
                        : '<span class="no-data">없음</span>';

                    const gChips = gList.length > 0 
                        ? gList.map(g => `<span class="group-chip">${{g}}</span>`).join('')
                        : '<span class="no-data">없음</span>';

                    return `
                        <tr>
                            <td class="comp-name">${{c.name}}</td>
                            <td><div class="chip-list">${{uChips}}</div></td>
                            <td><div class="chip-list">${{gChips}}</div></td>
                        </tr>
                    `;
                }}).join('');

                const detailsContent = hasConfig ? `
                    <table class="comp-table">
                        <thead>
                            <tr>
                                <th style="width: 25%;">컴포넌트 명</th>
                                <th style="width: 45%;">와쳐 지정 사용자 (이메일)</th>
                                <th style="width: 30%;">와쳐 지정 그룹</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${{compRows}}
                        </tbody>
                    </table>
                ` : `<div style="padding-top: 1rem;" class="no-data">이 프로젝트에는 등록된 Component Watcher 설정이 없습니다.</div>`;

                const isExpanded = allExpanded || (hasConfig && projects.length <= 10);

                return `
                    <div class="project-card ${{isExpanded ? 'expanded' : ''}}" data-key="${{p.key}}">
                        <div class="project-card-header" onclick="toggleProjectExpand(this)">
                            <div class="project-key-badge">
                                <span>${{p.key}}</span>
                                ${{statusTag}}
                            </div>
                            <div class="project-summary-badges">
                                ${{hasConfig ? `
                                    <span class="badge-count">사용자: <strong>${{totalUserCount}}명</strong></span>
                                    <span class="badge-count">그룹: <strong>${{totalGroupCount}}개</strong></span>
                                ` : ''}}
                                <svg class="chevron" viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/></svg>
                            </div>
                        </div>
                        <div class="project-details">
                            ${{detailsContent}}
                        </div>
                    </div>
                `;
            }}).join('');
        }}

        function toggleProjectExpand(headerEl) {{
            const card = headerEl.parentElement;
            card.classList.toggle('expanded');
        }}

        // Event Listeners
        document.getElementById('searchInput').addEventListener('input', (e) => {{
            currentSearch = e.target.value;
            filterAndRender();
        }});

        document.getElementById('sortSelect').addEventListener('change', (e) => {{
            currentSort = e.target.value;
            filterAndRender();
        }});

        document.getElementById('filterButtons').addEventListener('click', (e) => {{
            if (e.target.tagName === 'BUTTON') {{
                document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
                e.target.classList.add('active');
                currentFilter = e.target.getAttribute('data-filter');
                filterAndRender();
            }}
        }});

        document.getElementById('toggleExpandBtn').addEventListener('click', () => {{
            allExpanded = !allExpanded;
            document.querySelectorAll('.project-card').forEach(card => {{
                if (allExpanded) card.classList.add('expanded');
                else card.classList.remove('expanded');
            }});
        }});

        // Initialization
        window.addEventListener('DOMContentLoaded', () => {{
            initStats();
            filterAndRender();
        }});
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n[대시보드 리포트 생성 완료] '{output_path}'")
    return output_path




REPORT_FILE = str(SCRIPT_DIR / "migration_report.html")


def format_html_cell(count, names_str, unit):
    """0건 항목은 '0개/0명' 대신 '설정 없음'으로 표시한다."""
    if count > 0 or (names_str and names_str != "-"):
        pill = f'<span class="count-pill">{count}{unit}</span> ' if count > 0 else ""
        return f"{pill}{names_str}"
    return '<span class="text-muted">설정 없음</span>'


def generate_html_report(output_file=REPORT_FILE):
    """Source(DC) vs Target(Cloud) 컴포넌트 와쳐 마이그레이션 비교 리포트 HTML을 생성한다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total_projects = len(stats["projects"])
    total_components = sum(len(p["components"]) for p in stats["projects"])
    match_count = sum(
        sum(1 for c in p["components"] if c.get("status") == "MATCH")
        for p in stats["projects"]
    )
    mismatch_count = sum(
        sum(1 for c in p["components"] if c.get("status") == "MISMATCH")
        for p in stats["projects"]
    )
    success_count = sum(
        sum(1 for c in p["components"] if c.get("status") == "SUCCESS")
        for p in stats["projects"]
    )
    existing_count = sum(
        sum(
            1
            for c in p["components"]
            if c.get("status") in {"EXISTING", "MATCH", "MISMATCH"}
        )
        for p in stats["projects"]
    )
    failed_count = sum(
        sum(1 for c in p["components"] if c.get("status") == "FAILED")
        for p in stats["projects"]
    )
    dry_run_count = sum(
        sum(1 for c in p["components"] if c.get("status") == "DRY-RUN")
        for p in stats["projects"]
    )

    rows_html = []
    for project in stats["projects"]:
        proj_key = project["key"]
        for comp in project["components"]:
            comp_name = comp.get("name", "")

            # Source (DC)
            s_watchers = comp.get("source_watchers", comp.get("watchers", 0))
            s_groups = comp.get("source_groups", comp.get("groups", 0))
            s_emails = (
                ", ".join(comp.get("source_emails", comp.get("watcher_emails", [])))
                or "-"
            )
            s_group_names = (
                ", ".join(comp.get("source_group_names", comp.get("group_names", [])))
                or "-"
            )

            # Target (Cloud)
            t_watchers = comp.get("target_watchers", 0)
            t_groups = comp.get("target_groups", 0)
            t_emails = ", ".join(comp.get("target_emails", [])) or "-"
            t_group_names = ", ".join(comp.get("target_group_names", [])) or "-"

            status = comp.get("status", "UNKNOWN")

            badge_class = "badge-secondary"
            status_label = status
            if status == "MATCH":
                badge_class = "badge-success"
                status_label = "MATCH (설정 일치)"
            elif status == "MISMATCH":
                badge_class = "badge-danger"
                status_label = "MISMATCH (설정 불일치)"
            elif status == "SUCCESS":
                badge_class = "badge-success"
                status_label = "마이그레이션 성공"
            elif status == "EXISTING":
                badge_class = "badge-info"
                status_label = "기존 설정 존재"
            elif status == "FAILED":
                badge_class = "badge-danger"
                status_label = "마이그레이션 실패"
            elif status == "DRY-RUN":
                badge_class = "badge-warning"
                status_label = "DRY-RUN 완료"

            s_usr_str = format_html_cell(s_watchers, s_emails, "명")
            s_grp_str = format_html_cell(s_groups, s_group_names, "개")
            t_usr_str = format_html_cell(t_watchers, t_emails, "명")
            t_grp_str = format_html_cell(t_groups, t_group_names, "개")

            rows_html.append(
                f"""<tr class="search-row" data-status="{status}">
                    <td class="font-bold">{proj_key}</td>
                    <td>{comp_name}</td>
                    <td class="source-col">{s_usr_str}</td>
                    <td class="source-col">{s_grp_str}</td>
                    <td class="target-col">{t_usr_str}</td>
                    <td class="target-col">{t_grp_str}</td>
                    <td><span class="badge {badge_class}">{status_label}</span></td>
                </tr>"""
            )

    table_body = (
        "\n".join(rows_html)
        if rows_html
        else (
            '<tr><td colspan="7" style="text-align:center; padding:24px;">'
            "처리된 컴포넌트 설정이 없습니다.</td></tr>"
        )
    )

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jira Component Watcher 마이그레이션 비교 리포트</title>
    <style>
        :root {{
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-primary: #0f172a;
            --text-secondary: #475569;
            --border-color: #e2e8f0;
            --primary: #2563eb;
            --success: #16a34a;
            --info: #0284c7;
            --warning: #d97706;
            --danger: #dc2626;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            margin: 0;
            padding: 32px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 28px;
        }}
        .header h1 {{
            font-size: 26px;
            font-weight: 700;
            margin: 0 0 8px 0;
        }}
        .header p {{
            color: var(--text-secondary);
            margin: 0;
            font-size: 14px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .stat-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .stat-card .label {{
            font-size: 13px;
            color: var(--text-secondary);
            margin-bottom: 6px;
        }}
        .stat-card .value {{
            font-size: 28px;
            font-weight: 700;
        }}
        .controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .search-box {{
            padding: 10px 14px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            width: 320px;
            font-size: 14px;
        }}
        .table-card {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}
        th {{
            padding: 14px 16px;
            font-weight: 600;
            color: var(--text-secondary);
            border-bottom: 1px solid var(--border-color);
        }}
        th.source-header {{
            background: #eff6ff;
            color: #1e40af;
        }}
        th.target-header {{
            background: #f0fdf4;
            color: #166534;
        }}
        td {{
            padding: 14px 16px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: middle;
        }}
        td.source-col {{
            background: #f8fafc;
        }}
        td.target-col {{
            background: #ffffff;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        .font-bold {{
            font-weight: 600;
        }}
        .text-muted {{
            color: #94a3b8;
            font-style: italic;
        }}
        .count-pill {{
            display: inline-block;
            padding: 2px 8px;
            background: #e2e8f0;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            margin-right: 6px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            color: #fff;
        }}
        .badge-success {{ background-color: var(--success); }}
        .badge-info {{ background-color: var(--info); }}
        .badge-danger {{ background-color: var(--danger); }}
        .badge-warning {{ background-color: var(--warning); }}
        .badge-secondary {{ background-color: var(--text-secondary); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Component Watcher 마이그레이션 비교 리포트</h1>
            <p>
                생성 시각: {now_str} |
                소스 DC: {ENV} ({DC_ENV_PREFIX}) ➔ 타겟 Cloud: {TARGET_ENV} ({CLOUD_ENV_PREFIX})
            </p>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">총 대상 프로젝트</div>
                <div class="value">{total_projects}</div>
            </div>
            <div class="stat-card">
                <div class="label">전체 컴포넌트 설정</div>
                <div class="value">{total_components}</div>
            </div>
            <div class="stat-card">
                <div class="label" style="color:var(--success)">MATCH (설정 일치)</div>
                <div class="value" style="color:var(--success)">{match_count}</div>
            </div>
            <div class="stat-card">
                <div class="label" style="color:var(--danger)">MISMATCH (불일치)</div>
                <div class="value" style="color:var(--danger)">{mismatch_count}</div>
            </div>
            <div class="stat-card">
                <div class="label" style="color:var(--success)">마이그레이션 성공</div>
                <div class="value" style="color:var(--success)">{success_count}</div>
            </div>
            <div class="stat-card">
                <div class="label" style="color:var(--warning)">DRY-RUN</div>
                <div class="value" style="color:var(--warning)">{dry_run_count}</div>
            </div>
        </div>

        <div class="controls">
            <input type="text" id="searchInput" class="search-box"
                   placeholder="프로젝트 키 또는 컴포넌트 검색..." onkeyup="filterTable()">
        </div>

        <div class="table-card">
            <table id="reportTable">
                <thead>
                    <tr>
                        <th style="background:#f1f5f9;">프로젝트 키</th>
                        <th style="background:#f1f5f9;">컴포넌트 명</th>
                        <th class="source-header">Source (DC) 와쳐 사용자</th>
                        <th class="source-header">Source (DC) 와쳐 그룹</th>
                        <th class="target-header">Target (Cloud) 와쳐 사용자</th>
                        <th class="target-header">Target (Cloud) 와쳐 그룹</th>
                        <th style="background:#f1f5f9;">비교 검증 결과</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function filterTable() {{
            const filter = document.getElementById('searchInput').value.toLowerCase();
            const rows = document.querySelectorAll('.search-row');
            rows.forEach(row => {{
                const text = row.innerText.toLowerCase();
                row.style.display = text.includes(filter) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_file


def print_scan_summary():
    print("\n" + "=" * 60)
    print("                      스캔 및 설정 분석 요약 결과")
    print("=" * 60)
    total_projects = len(stats["projects"])
    projects_with_configs = [
        p for p in stats["projects"] if len(p.get("components", [])) > 0
    ]
    total_all_components = sum(len(p.get("components", [])) for p in stats["projects"])
    existing_in_cloud_count = sum(
        sum(1 for c in p["components"] if c.get("already_exists"))
        for p in stats["projects"]
    )
    user_issue_count = len(user_issue_logs)

    print(f"총 대상 프로젝트 수: {total_projects}개")
    print(f"컴포넌트 와쳐 설정 보유 프로젝트 수: {len(projects_with_configs)}개")
    print(f"전체 컴포넌트 설정 총합: {total_all_components}개")
    print(f"Jira Cloud 기존 등록 컴포넌트 수: {existing_in_cloud_count}개")
    if user_issue_count > 0:
        print(f"감지된 사용자 할당 이슈 건수: {user_issue_count}건 (상세 내용은 '{LOG_FILE}' 참조)")

    for p in stats["projects"]:
        comps = p.get("components", [])
        if not comps:
            continue
        print(f"\n프로젝트: {p['key']}")
        print(f"  - 컴포넌트 설정 수: {len(comps)}")
        for c in comps:
            w_cnt = c.get("source_watchers", c.get("watchers", 0))
            g_cnt = c.get("source_groups", c.get("groups", 0))
            count_info = format_count_info(
                w_cnt, g_cnt, label_watcher="와쳐", label_group="그룹"
            )
            info_suffix = f" {count_info}" if count_info else ""
            exist_suffix = " [Cloud 기존 설정 존재]" if c.get("already_exists") else ""
            print(f"    * {c['name']}{info_suffix}{exist_suffix}")

    print("\n" + "=" * 60)
    print(f"컴포넌트 와쳐 사용 중인 프로젝트 수: {len(projects_with_configs)}개")
    print(f"전체 컴포넌트 설정 총합: {total_all_components}개")
    print("=" * 60)

    dashboard_path = generate_dashboard_html()
    print(f"스캔 대시보드 리포트 생성 완료: '{dashboard_path}'")


def run_scan(projects):
    """전체 프로젝트 설정을 스캔한다 (Jira Cloud로 마이그레이션하지 않는 안전 조회 모드)."""
    stats["projects"].clear()
    user_issue_logs.clear()
    email_cache.clear()

    total = len(projects)
    print(f"\n총 {total}개 프로젝트 스캔 및 설정 분석을 시작합니다 (조회 전용 모드)...")
    try:
        for index, project_key in enumerate(projects, 1):
            scan_project(project_key, index, total)
    except JiraAuthenticationError as e:
        print(f"\n[인증 오류] {e}")
        print("추가 인증 실패와 계정 잠금을 방지하기 위해 스캔을 중단합니다.")
        log_error(f"스캔 중단: {e}")
        print_scan_summary()
        return False

    print_scan_summary()
    return True


def run_migration(projects):
    """스캔된 프로젝트 설정을 Jira Cloud로 실제 마이그레이션한다."""
    missing_vars = validate_configuration(require_cloud=True)
    if missing_vars:
        print(f"\n오류: 마이그레이션을 위한 Cloud 환경변수가 정의되어 있지 않습니다: {', '.join(missing_vars)}")
        return 1

    total = len(projects)
    print(f"\n총 {total}개 프로젝트 마이그레이션을 시작합니다 (설정 값 우선 마이그레이션 후 사용자 할당)...")
    try:
        for index, project_key in enumerate(projects, 1):
            migrate_project(project_key, index, total, dry_run=False)
    except JiraAuthenticationError as e:
        print(f"\n[인증 오류] {e}")
        print("추가 인증 실패와 계정 잠금을 방지하기 위해 작업을 중단합니다.")
        log_error(f"작업 중단: {e}")
        print_summary()
        return 2

    print_summary()
    return 1 if any(project.get("failed") for project in stats["projects"]) else 0


def print_summary():
    print("\n" + "=" * 60)
    print("                      마이그레이션 요약 결과")
    print("=" * 60)
    print(f"총 대상 프로젝트 수: {len(stats['projects'])}")

    projects_with_configs = [
        project for project in stats["projects"] if project.get("components")
    ]
    failed_projects = [
        project for project in stats["projects"] if project.get("failed")
    ]
    total_components = 0
    for project in stats["projects"]:
        comps = project.get("components", [])
        component_count = len(comps)
        total_components += component_count
        if not comps:
            continue
        print(f"\n프로젝트: {project['key']}")
        print(f"  - 컴포넌트 설정 수: {component_count}")
        for component in comps:
            watchers = component.get("source_watchers", component.get("watchers", 0))
            groups = component.get("source_groups", component.get("groups", 0))
            count_info = format_count_info(
                watchers, groups, label_watcher="와쳐", label_group="그룹"
            )
            info_suffix = f" {count_info}" if count_info else ""
            status = component.get("status", "UNKNOWN")
            print(f"    * {component['name']}{info_suffix} -> [{status}]")

    print("\n" + "=" * 60)
    print(f"컴포넌트 와쳐 사용 중인 프로젝트 수: {len(projects_with_configs)}개")
    print(f"전체 컴포넌트 설정 총합: {total_components}개")
    print(f"실패한 프로젝트 수: {len(failed_projects)}개")
    if user_issue_logs:
        print(f"사용자 할당 이슈 건수: {len(user_issue_logs)}건 (상세 내용은 '{LOG_FILE}' 참조)")
    print("=" * 60)
    print(f"발생한 오류 및 사용자 할당 이슈 상세 내용은 '{LOG_FILE}'에서 확인하실 수 있습니다.")

    html_file = generate_html_report()
    print(f"상세 마이그레이션 리포트 HTML 생성 완료: {html_file}")


def main():
    args = parse_args()
    args = interactive_select_mode(args)
    args = interactive_select_project_file(args)

    if args.test_cloud:
        print_target_domain_info("Cloud REST API 조회 테스트")
        success = test_cloud_api(project_key=args.project, file_path=args.file)
        return 0 if success else 1

    mode_desc = "스캔 전용 (Cloud 변경 없음)" if (args.scan_only or args.dry_run) else "스캔 후 마이그레이션"
    print_target_domain_info(mode_desc)

    missing_vars = validate_configuration(require_cloud=False)
    if missing_vars:
        print(f"오류: env 파일에 다음 변수가 정의되어 있지 않습니다: {', '.join(missing_vars)}")
        return 1

    try:
        projects, projects_path = load_projects(args.file)
    except FileNotFoundError as e:
        print(f"오류: {e}")
        return 1
    if not projects:
        print("처리할 프로젝트가 없습니다.")
        return 1

    stats["projects"].clear()
    email_cache.clear()
    user_issue_logs.clear()

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== Component Watcher Log Started at {datetime.now()} ===\n")

    log_error(
        f"실행 설정: environment={ENV}, target={TARGET_ENV}, "
        f"scan_only={bool(args.scan_only or args.dry_run)}, yes={args.yes}, "
        f"dc_url={DC_BASE_URL}, cloud_url={cloud_url or 'not-required'}, "
        f"ssl_verify={VERIFY_SSL}, env_path={ENV_PATH}, projects_path={projects_path}"
    )

    print(f"'{projects_path}' 파일에서 총 {len(projects)}개의 프로젝트를 로드했습니다.")

    # 1단계: 먼저 스캔 실행
    scan_ok = run_scan(projects)
    if not scan_ok:
        return 1

    # 만약 스캔 전용 모드(--scan-only 또는 --dry-run)이면 여기서 안전하게 종료
    if args.scan_only or args.dry_run:
        print("\n모든 프로젝트의 스캔 작업이 완료되었습니다 (Jira Cloud 마이그레이션 미수행).")
        return 0

    # 2단계: 스캔 후 마이그레이션 진행 여부 확인
    if not args.yes and not args.real_run:
        print("\n" + "=" * 60)
        print("           Jira Cloud 마이그레이션 진행 확인")
        print("=" * 60)
        print("스캔 및 설정 분석이 완료되었습니다. (dashboard.html 생성됨)")
        try:
            confirm = input(
                "\n위 스캔 결과를 바탕으로 Jira Cloud로 migration(마이그레이션)을 진행하시겠습니까? (Y/N, 기본값: Y): "
            ).strip().upper()
        except (KeyboardInterrupt, EOFError):
            confirm = "N"

        if confirm and confirm != "Y":
            print("\n마이그레이션이 취소되었습니다. (스캔 결과 대시보드만 보존됩니다)")
            return 0

    # 3단계: 실제 마이그레이션 실행
    return run_migration(projects)



if __name__ == "__main__":
    raise SystemExit(main())


