#!/usr/bin/env python
"""
Announcer for Jira / Confluence - project.txt 기반 벌크 생성 스크립트

사용법:
    # Jira 알림 생성 (project.txt의 각 프로젝트 키에 대해 notifications_jira_sample.py를 템플릿으로 사용하여 생성)
    python announcer_by_projects.py

    # Confluence 알림 생성 (project.txt의 각 스페이스 키에 대해 notifications_confluence_sample.py를 템플릿으로 사용하여 생성)
    APP_TYPE=confluence python announcer_by_projects.py

    # 드라이런 (실제 API 요청 없이 생성 예정인 페이로드만 확인)
    python announcer_by_projects.py --dry-run

환경변수:
    APP_BASE_URL        서버 URL              (기본: https://dev-jira.osci.kr)
    APP_TYPE            jira|confluence       (기본: jira)
    APP_USERNAME        사용자 ID             (기본: ad)
    APP_PASSWORD        비밀번호              (기본: 1234)
    APP_COOKIE          쿠키 문자열           (선택)
    SAVE_DIR            결과 저장 폴더        (기본: results)
"""

import argparse
import base64
import importlib.util
import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

IS_OPS_ENV = False

# ──────────────────────────────────────────────
# 설정 로드 & 전역 환경 초기화
# ──────────────────────────────────────────────
# 전역 설정 변수 선언
APP_TYPE = None
IS_CONFLUENCE = False
DEFAULT_PROJECT_FILE = "project.txt"
DEFAULT_JIRA_TEMPLATE = "notifications_jira.py"
DEFAULT_CONF_TEMPLATE = "notifications_confluence.py"

APP_BASE_URL = ""
APP_USERNAME = ""
APP_PASSWORD = ""
APP_TOKEN = ""
APP_COOKIE = ""
API_BASE = ""
IS_OPS_ENV = False
SAVE_DIR = os.getenv("SAVE_DIR") or "results"

# 서버가 직전 변경을 반영할 시간을 주고, 간헐적인 오류에만 재시도한다.
REQUEST_DELAY = float(os.getenv("ANNOUNCER_REQUEST_DELAY", "0.5"))
MAX_RETRIES = int(os.getenv("ANNOUNCER_MAX_RETRIES", "3"))
REQUEST_TIMEOUT = float(os.getenv("ANNOUNCER_REQUEST_TIMEOUT", "30"))
RETRY_STATUS_CODES = {400, 429, 502, 503, 504}
_last_request_at = 0.0


def request_with_retry(method, url, **kwargs):
    """요청 간 간격과 지수 백오프를 적용한다 (400은 서버의 간헐 오류 대응용)."""
    global _last_request_at
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    for attempt in range(MAX_RETRIES + 1):
        wait = REQUEST_DELAY - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        try:
            response = requests.request(method, url, **kwargs)
            _last_request_at = time.monotonic()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            delay = 2 ** attempt
            print(f"    ⚠️ 네트워크 오류: {exc}. {delay}초 후 재시도 ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)
            continue

        if response.status_code not in RETRY_STATUS_CODES or attempt == MAX_RETRIES:
            return response

        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 2 ** attempt
        except ValueError:
            delay = 2 ** attempt
        print(f"    ⚠️ HTTP {response.status_code}: {response.text[:300]}. "
              f"{delay:g}초 후 재시도 ({attempt + 1}/{MAX_RETRIES})")
        time.sleep(delay)

def load_env_file(filepath="../env"):
    # 상위 env 파일과 현재 디렉토리 env 파일을 모두 순차적으로 로드
    possible_paths = [Path(filepath), Path("env")]
    for path in possible_paths:
        if path.exists():
            for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1"):
                try:
                    with open(path, encoding=enc) as f:
                        f.read()
                    encoding = enc
                    break
                except UnicodeDecodeError:
                    continue
            with open(path, "r", encoding=encoding) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                            val = val[1:-1]
                        os.environ[key] = val

def select_app_type():
    global APP_TYPE, IS_CONFLUENCE, DEFAULT_PROJECT_FILE
    
    # sys.argv에서 --app-type 옵션 확인
    selected_app = None
    for i, arg in enumerate(sys.argv):
        if arg == "--app-type" and i + 1 < len(sys.argv):
            selected_app = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--app-type="):
            selected_app = arg.split("=", 1)[1].strip().lower()

    if selected_app not in ("jira", "confluence"):
        # 대화형 터미널에서 입력 유도
        if sys.stdin.isatty():
            print("──────────────────────────────────────────────")
            print("대상 애플리케이션을 선택해주세요:")
            print("1) Jira")
            print("2) Confluence")
            print("──────────────────────────────────────────────")
            while True:
                try:
                    choice = input("선택 (1 또는 2): ").strip()
                    if choice == "1":
                        selected_app = "jira"
                        break
                    elif choice == "2":
                        selected_app = "confluence"
                        break
                    else:
                        print("올바른 선택이 아닙니다. 1 또는 2를 입력해주세요.")
                except KeyboardInterrupt:
                    print("\n실행이 중단되었습니다.")
                    sys.exit(0)
        else:
            # 비대화형 환경인 경우 기존 환경 변수(env 파일 등)의 APP_TYPE 사용
            selected_app = (os.getenv("APP_TYPE") or "").lower()

    APP_TYPE = selected_app
    IS_CONFLUENCE = (APP_TYPE == "confluence")
    DEFAULT_PROJECT_FILE = "space.txt" if IS_CONFLUENCE else "project.txt"
    print(f"▶ 대상 애플리케이션: {APP_TYPE.upper()} (키 목록 파일: {DEFAULT_PROJECT_FILE})")

def select_environment():
    global IS_OPS_ENV, APP_BASE_URL, APP_USERNAME, APP_PASSWORD, APP_TOKEN, APP_COOKIE, API_BASE
    
    # sys.argv에서 --env 옵션 확인
    selected_env = None
    for i, arg in enumerate(sys.argv):
        if arg == "--env" and i + 1 < len(sys.argv):
            selected_env = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--env="):
            selected_env = arg.split("=", 1)[1].strip().lower()

    # 상위 env 및 로컬 env 매핑 로드
    # 애플리케이션별 환경변수 매핑 준비
    if not IS_CONFLUENCE:
        # Jira인 경우
        stg_url = os.getenv("JIRA_DC_STG_URL") or os.getenv("STG_JIRA_URL")
        stg_token = os.getenv("JIRA_DC_STG_API_TOKEN") or os.getenv("JIRA_DC_STG_TOKEN")
        stg_user = os.getenv("JIRA_DC_STG_USERNAME") or os.getenv("STG_JIRA_USERNAME")
        stg_password = os.getenv("JIRA_DC_STG_PASSWORD") or os.getenv("STG_JIRA_PASSWORD")
        stg_cookie = os.getenv("JIRA_DC_STG_COOKIE") or os.getenv("STG_JIRA_COOKIE")

        ops_url = os.getenv("JIRA_DC_OPS_URL") or os.getenv("OPS_JIRA_URL")
        ops_token = os.getenv("JIRA_DC_OPS_TOKEN") or os.getenv("JIRA_DC_OPS_API_TOKEN")
        ops_user = os.getenv("JIRA_DC_OPS_USERNAME") or os.getenv("OPS_JIRA_USERNAME")
        ops_password = os.getenv("JIRA_DC_OPS_PASSWORD") or os.getenv("OPS_JIRA_PASSWORD")
        ops_cookie = os.getenv("JIRA_DC_OPS_COOKIE") or os.getenv("OPS_JIRA_COOKIE")
    else:
        # Confluence인 경우
        stg_url = os.getenv("CONF_DC_STG_URL") or os.getenv("CONF_STG_URL") or os.getenv("STG_CONF_URL")
        stg_token = os.getenv("CONF_DC_STG_TOKEN") or os.getenv("CONF_DC_STG_API_TOKEN")
        stg_user = os.getenv("CONF_DC_STG_USERNAME") or os.getenv("STG_CONF_USERNAME")
        stg_password = os.getenv("CONF_DC_STG_PASSWORD") or os.getenv("STG_CONF_PASSWORD")
        stg_cookie = os.getenv("CONF_DC_STG_COOKIE") or os.getenv("STG_CONF_COOKIE")

        ops_url = os.getenv("CONF_DC_OPS_URL") or os.getenv("CONF_OPS_URL") or os.getenv("OPS_CONF_URL")
        ops_token = os.getenv("CONF_DC_OPS_TOKEN") or os.getenv("CONF_DC_OPS_API_TOKEN")
        ops_user = os.getenv("CONF_DC_OPS_USERNAME") or os.getenv("CONF_OPS_USERNAME")
        ops_password = os.getenv("CONF_DC_OPS_PASSWORD") or os.getenv("CONF_OPS_PASSWORD")
        ops_cookie = os.getenv("CONF_DC_OPS_COOKIE") or os.getenv("OPS_CONF_COOKIE")

    if selected_env in ("stg", "ops"):
        choice_env = selected_env.upper()
    else:
        # 대화형 프롬프트로 사용자에게 선택 요청
        print("──────────────────────────────────────────────")
        print(f"[{APP_TYPE.upper()}] 실행할 환경을 선택해주세요:")
        print(f"1) STG (Staging)     - URL: {stg_url or '설정 없음'}")
        print(f"2) OPS (Production)  - URL: {ops_url or '설정 없음'}")
        print("──────────────────────────────────────────────")
        
        if not sys.stdin.isatty():
            print("비대화형 환경이므로 기본값으로 STG를 선택합니다.")
            choice_env = "STG"
        else:
            while True:
                try:
                    choice = input("선택 (1 또는 2): ").strip()
                    if choice == "1":
                        choice_env = "STG"
                        break
                    elif choice == "2":
                        choice_env = "OPS"
                        break
                    else:
                        print("올바른 선택이 아닙니다. 1 또는 2를 입력해주세요.")
                except KeyboardInterrupt:
                    print("\n실행이 중단되었습니다.")
                    sys.exit(0)

    # 선택된 환경 변수 바인딩
    if choice_env == "STG":
        APP_BASE_URL = stg_url
        APP_TOKEN = stg_token
        APP_USERNAME = stg_user
        APP_PASSWORD = stg_password
        APP_COOKIE = stg_cookie
        IS_OPS_ENV = False
    else:
        APP_BASE_URL = ops_url
        APP_TOKEN = ops_token
        APP_USERNAME = ops_user
        APP_PASSWORD = ops_password
        APP_COOKIE = ops_cookie
        IS_OPS_ENV = True

    if not APP_BASE_URL:
        print(f"❌ 에러: {choice_env} 환경의 URL이 정의되어 있지 않습니다. env 설정을 확인하세요.")
        sys.exit(1)

    API_BASE = f"{APP_BASE_URL.rstrip('/')}/rest/announcer/1.0/admin/notifications"
    
    print(f"▶ {choice_env} 환경이 선택되었습니다.")
    print(f"   URL: {APP_BASE_URL}")
    if APP_USERNAME and APP_PASSWORD:
        print(f"   사용자 계정: {APP_USERNAME} (Basic 인증 방식)")
    elif APP_TOKEN:
        print(f"   인증 방식: Bearer PAT Token (앞 8자리: {str(APP_TOKEN)[:8]}...)")
    print("──────────────────────────────────────────────")

# 초기화 실행 순서 (애플리케이션 타입 먼저 -> 그 다음 환경)
load_env_file()
select_app_type()
select_environment()

# ScriptRunner 엔드포인트
SR_ENDPOINT_JIRA = os.getenv("SR_ENDPOINT_JIRA")
if not SR_ENDPOINT_JIRA:
    SR_ENDPOINT_JIRA = "/rest/scriptrunner/latest/custom/everyLoginNotificationButton"

SR_ENDPOINT_CONF = os.getenv("SR_ENDPOINT_CONF")
if not SR_ENDPOINT_CONF:
    SR_ENDPOINT_CONF = "/rest/scriptrunner/latest/custom/everyLoginNotificationButton"

API_BASE = f"{APP_BASE_URL}/rest/announcer/1.0/admin/notifications"

# ──────────────────────────────────────────────
# 템플릿 컬럼 스키마 & 검증 정의
# ──────────────────────────────────────────────
COLUMN_SCHEMA = [
    ("title",          "title",          "str",      True),
    ("description",    "description",    "str",      False),
    ("contents",       "contents",       "str",      True),
    ("type",           "type",           "str",      True),   # dialog|flag|banner|intercept
    ("displayType",    "displayType",    "str",      True),   # 0=None 1=Warning 2=Information 3=Error
    ("showButton",     "showButton",     "str",      True),   # true|false
    ("isActive",       "isActive",       "str",      True),   # true|false
    ("format",         "format",         "str",      True),   # 0=HTML 1=plain 2=Markdown
    ("buttonText",     "buttonText",     "str",      False),
    ("delayButton",    "delayButton",    "str",      False),   # true|false
    ("anonymous",      "anonymous",      "str",      False),   # true|false
    ("filterGroups",   "filterGroups",   "str",      False),   # true|false
    ("targetGroup",    "targetGroup",    "str",      False),   # 쉼표구분 그룹명
    ("filterUser",     "filterUser",     "str",      False),   # true|false
    ("targetUser",     "targetUser",     "str_list", False),   # 쉼표구분 사용자명
]

VALID_TYPES         = {"dialog", "flag", "banner", "intercept"}
VALID_DISPLAY_TYPES = {"0", "1", "2", "3"}
VALID_FORMATS       = {"0", "1", "2"}
VALID_FREQUENCIES   = {"days", "weeks", "months", "years"}
DATE_FORMATS        = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
]

# ──────────────────────────────────────────────
# 유틸리티 함수들
# ──────────────────────────────────────────────
def detect_encoding(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            with open(path, encoding=enc, errors="strict") as f:
                f.read()
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"


def make_basic_token() -> str:
    return base64.b64encode(f"{APP_USERNAME}:{APP_PASSWORD}".encode()).decode()


def build_headers() -> dict:
    h = {
        "Content-Type":  "application/json",
    }
    # Bearer 토큰이 있고, 별도로 username이 지정되지 않은 경우 Bearer 인증 사용
    if APP_TOKEN and not APP_USERNAME:
        h["Authorization"] = f"Bearer {APP_TOKEN}"
    elif APP_USERNAME and APP_PASSWORD:
        h["Authorization"] = f"Basic {make_basic_token()}"
    elif APP_TOKEN:
        # username은 정의되어 있으나 토큰이 있는 경우, 우선 Bearer 시도 (PAT가 Jira DC에서 주로 쓰임)
        h["Authorization"] = f"Bearer {APP_TOKEN}"
        
    if APP_COOKIE:
        h["Cookie"] = APP_COOKIE
    return h


# 실행 결과 모음을 위한 글로벌 딕셔너리
EXECUTION_SUMMARY = {
    "app_type": None,
    "env": None,
    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "create_results": None,
    "reset_button_results": []
}

def save_execution_summary(is_error=False):
    os.makedirs(SAVE_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    EXECUTION_SUMMARY["app_type"] = APP_TYPE
    selected_env = "default"
    for i, arg in enumerate(sys.argv):
        if arg == "--env" and i + 1 < len(sys.argv):
            selected_env = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--env="):
            selected_env = arg.split("=", 1)[1].strip().lower()
    EXECUTION_SUMMARY["env"] = selected_env

    prefix = "create_error" if is_error else ("dry_run_create" if "--dry-run" in sys.argv else "create")
    filename = f"{SAVE_DIR}/{prefix}_{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(EXECUTION_SUMMARY, f, ensure_ascii=False, indent=2)
    print(f"  💾 결과 요약 저장: {filename}")
    return filename


def extract_ids_from_json(data) -> list:
    ids = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "id" in item:
                ids.append(item["id"])
            elif isinstance(item, (int, str)):
                ids.append(item)
    elif isinstance(data, dict):
        if "id" in data:
            ids.append(data["id"])
        for val in data.values():
            if isinstance(val, list):
                ids.extend(extract_ids_from_json(val))
    return ids


def normalize_dt(raw: str, field: str, errors: list) -> str:
    for fmt in DATE_FORMATS:
        try:
            # 입력된 시간을 타임존 정보가 없는 KST(로컬) 객체로 파싱
            dt_kst = datetime.strptime(raw, fmt)
            if IS_OPS_ENV:
                # OPS 환경의 경우 서버 타임존(KST)과 클라이언트 타임존(KST)이 일치하므로
                # 별도의 UTC 변환(9시간 차감)을 하지 않고 원본 그대로 사용
                return dt_kst.strftime("%Y-%m-%d %H:%M")
            else:
                # UTC+0 시각으로 변환 (KST는 UTC+9이므로 9시간을 뺌)
                dt_utc = dt_kst - timedelta(hours=9)
                return dt_utc.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    errors.append(f"{field}='{raw}' 날짜 형식 오류 (예: 2025-10-31 00:00)")
    return raw


def get_val(template_row: dict, key: str, fallback_keys: list = None) -> str:
    search_keys = [key]
    if fallback_keys:
        search_keys.extend(fallback_keys)
        
    for k_item in search_keys:
        snake_key = "".join(["_" + c.lower() if c.isupper() else c for c in k_item]).lstrip("_")
        
        # 1. 템플릿 딕셔너리에서 camelCase 및 snake_case 키 모두 검사 (비어있지 않은 값 최우선)
        for k in (k_item, snake_key):
            if k in template_row:
                val = template_row[k]
                if val is not None and str(val).strip() != "":
                    return val

        # 2. 환경변수 NOTI_* 매핑 조회
        env_key = f"NOTI_{snake_key.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None and str(env_val).strip() != "":
            return env_val

    return None


def get_server_time() -> datetime:
    """
    서버의 실시간 현재 시각을 조회하여 반환한다.
    1. REST API (/rest/api/2/serverInfo) 정보 활용 시도
    2. HTTP 응답 헤더 (Date) 파싱 시도
    3. 실패 시 local/UTC 시각으로 fallback
    """
    server_info_url = f"{APP_BASE_URL.rstrip('/')}/rest/api/2/serverInfo"
    try:
        resp = requests.get(server_info_url, headers=build_headers(), timeout=5)
        if resp.ok:
            data = resp.json()
            server_time_str = data.get("serverTime")
            if server_time_str:
                dt = datetime.fromisoformat(server_time_str.replace("Z", "+00:00"))
                if not IS_OPS_ENV and dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                elif dt.tzinfo is not None:
                    return dt.replace(tzinfo=None)
    except Exception:
        pass

    try:
        resp = requests.head(API_BASE, headers=build_headers(), timeout=5)
        date_header = resp.headers.get("Date")
        if date_header:
            dt_utc = parsedate_to_datetime(date_header).astimezone(timezone.utc).replace(tzinfo=None)
            if IS_OPS_ENV:
                return dt_utc + timedelta(hours=9)
            return dt_utc
    except Exception:
        pass

    return datetime.now() if IS_OPS_ENV else datetime.utcnow()


def build_recurrence(raw_row: dict):
    from_dt   = raw_row.get("recurrence_from")
    to_dt     = raw_row.get("recurrence_to")
    duration  = raw_row.get("recurrence_duration")
    frequency = raw_row.get("recurrence_frequency")

    from_dt   = str(from_dt).strip() if from_dt is not None else ""
    to_dt     = str(to_dt).strip() if to_dt is not None else ""
    duration  = str(duration).strip() if duration is not None else ""
    frequency = str(frequency).strip() if frequency is not None else ""

    if not any([from_dt, to_dt, duration, frequency]):
        return None

    errors = []
    from_dt  = normalize_dt(from_dt, "recurrence_from", errors) if from_dt else (errors.append("recurrence_from 필요") or "")
    to_dt    = normalize_dt(to_dt,   "recurrence_to",   errors) if to_dt   else (errors.append("recurrence_to 필요")   or "")

    if from_dt and to_dt and not errors:
        try:
            # 실시간 서버 시각 조회 활용
            now_server = get_server_time()
            now_plus_1m_10s = now_server + timedelta(minutes=1, seconds=10)
            parsed_from = datetime.strptime(from_dt, "%Y-%m-%d %H:%M")
            parsed_to = datetime.strptime(to_dt, "%Y-%m-%d %H:%M")
            if parsed_from < now_plus_1m_10s:
                delta = parsed_to - parsed_from
                new_from = now_plus_1m_10s
                new_to = new_from + delta
                from_dt = new_from.strftime("%Y-%m-%d %H:%M")
                to_dt = new_to.strftime("%Y-%m-%d %H:%M")
                print(f"  ℹ️  날짜 보정 (서버시각+1분 반영): {parsed_from} ~ {parsed_to} -> {from_dt} ~ {to_dt}")
        except Exception as e:
            print(f"  ⚠️  날짜 자동 보정 실패: {e}")

    if not duration:
        errors.append("recurrence_duration 필요")
    else:
        try:
            duration = int(duration)
        except ValueError:
            errors.append(f"recurrence_duration='{duration}' 은 정수여야 합니다")

    freq_map = {
        "day": "days", "days": "days",
        "week": "weeks", "weeks": "weeks",
        "month": "months", "months": "months",
        "year": "years", "years": "years",
    }
    if not frequency:
        errors.append("recurrence_frequency 필요")
    else:
        frequency = freq_map.get(frequency.lower(), frequency.lower())
        if frequency not in VALID_FREQUENCIES:
            errors.append(f"recurrence_frequency='{frequency}' 허용값: {VALID_FREQUENCIES}")

    if errors:
        for e in errors:
            print(f"  ⚠️ Recurrence 오류: {e}")
        return None

    return {
        "fromDateTime": from_dt,
        "toDateTime":   to_dt,
        "customRecurrence": {
            "dailyRecurrence": {
                "duration":      duration,
                "frequencyType": frequency,
            }
        },
    }


def cast_value(raw, cast_type: str, col: str):
    if raw is None:
        return None

    if cast_type == "str":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        raw = str(raw).strip()
        if not raw:
            return None
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            raw = raw[1:-1].replace('""', '"')
        return raw

    if cast_type == "int":
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw).strip())
        except ValueError:
            print(f"  ⚠️ [{col}]: 정수 변환 실패 '{raw}'")
            return None

    if cast_type == "bool":
        if isinstance(raw, bool):
            return raw
        val_str = str(raw).strip().lower()
        if val_str in ("true", "1", "yes"):
            return True
        if val_str in ("false", "0", "no"):
            return False
        print(f"  ⚠️ [{col}]: 불리언 오류 '{raw}' → False")
        return False

    if cast_type == "str_list":
        if isinstance(raw, list):
            return [str(v).strip() for v in raw if str(v).strip()]
        val_str = str(raw).strip()
        return [v.strip() for v in val_str.split(",") if v.strip()]

    return raw


def validate_payload(payload: dict) -> list:
    errors = []
    if payload.get("type", "") not in VALID_TYPES:
        errors.append(f"type='{payload.get('type')}' 허용값: {VALID_TYPES}")
    if payload.get("displayType", "") not in VALID_DISPLAY_TYPES:
        errors.append(f"displayType='{payload.get('displayType')}' 허용값: {VALID_DISPLAY_TYPES}")
    if payload.get("format", "") not in VALID_FORMATS:
        errors.append(f"format='{payload.get('format')}' 허용값: {VALID_FORMATS}")

    # Jira 필터 검증
    if payload.get("filterProjects") == "true" and not payload.get("targetProject"):
        errors.append("filterProjects=true → targetProject 필요")
    # Confluence 필터 검증
    if payload.get("filterSpaces") == "true" and not payload.get("targetSpace"):
        errors.append("filterSpaces=true → targetSpace 필요")
    if payload.get("filterGroups") == "true" and not payload.get("targetGroup"):
        errors.append("filterGroups=true → targetGroup 필요")

    return errors


# ──────────────────────────────────────────────
# API 처리 함수들
# ──────────────────────────────────────────────
def scriptrunner_reset_button(notification_id, dry_run: bool = False):
    endpoint = SR_ENDPOINT_CONF if IS_CONFLUENCE else SR_ENDPOINT_JIRA
    url      = f"{APP_BASE_URL}{endpoint}"
    params   = {"id": notification_id}

    if dry_run:
        print(f"  🔍 [DRY-RUN] ScriptRunner POST {url}?id={notification_id}")
        EXECUTION_SUMMARY["reset_button_results"].append({
            "id": notification_id,
            "dry_run": True,
            "endpoint": url
        })
        return True

    print(f"  🔧 ScriptRunner reset_button 설정 중... (id={notification_id})")
    try:
        resp = request_with_retry("POST", url, headers=build_headers(), params=params)
        print(f"  상태 코드: {resp.status_code}")
        if resp.ok:
            print(f"  ✅ ScriptRunner reset_button 완료 (id={notification_id})")
        else:
            print(f"  ❌ ScriptRunner 실패: {resp.text}")
        
        EXECUTION_SUMMARY["reset_button_results"].append({
            "id": notification_id,
            "status_code": resp.status_code,
            "response": resp.text
        })
        return resp.ok
    except Exception as e:
        print(f"  ❌ ScriptRunner 요청 오류: {e}")
        EXECUTION_SUMMARY["reset_button_results"].append({
            "id": notification_id,
            "error": str(e)
        })
        return False


def _apply_reset_button(results, reset_flags, dry_run: bool = False):
    print("\n── ScriptRunner reset_button 처리 ──────────────────")
    app = "Confluence" if IS_CONFLUENCE else "Jira"
    for item, flag in zip(results, reset_flags):
        nid = str(item.get("id", "")) if isinstance(item, dict) else str(item)
        if str(flag).lower() == "true" and nid:
            scriptrunner_reset_button(nid, dry_run=dry_run)
        else:
            print(f"  ⏭  [{app}] id={nid} reset_button={flag} → 건너뜀")


def create_notifications(notifications: list, dry_run: bool = False):
    print(f"\n[CREATE] 알림 {len(notifications)}개 생성 요청 중...")

    # reset_button 플래그 별도 보관
    reset_flags = [n.pop("reset_button", None) for n in notifications]

    if dry_run:
        print("  🔍 [DRY-RUN] payload:")
        print(json.dumps(notifications, ensure_ascii=False, indent=2))
        print(f"\n  reset_button 플래그: {reset_flags}")
        EXECUTION_SUMMARY["create_results"] = notifications
        return [{"id": f"dry_{i}"} for i in range(len(notifications))], reset_flags

    created_results = []
    success_reset_flags = []
    failed_notifications = []

    # 개별 알림 생성 요청으로 전환 (오류 발생 시 다음 알림으로 진행 가능하도록 루프 처리)
    for idx, (noti_payload, rb_flag) in enumerate(zip(notifications, reset_flags)):
        pkey = noti_payload.get("targetProject", ["Unknown"])[0]
        single_payload = {k: v for k, v in noti_payload.items() if k != "id"}

        # 🚀 CREATE API 발송 직전 실시간 +1분 동적 자동 설정 (Start Date Past 오류 방지 & 즉시 게재 시작)
        rec = single_payload.get("recurrence")
        if isinstance(rec, dict):
            from_dt = rec.get("fromDateTime")
            to_dt = rec.get("toDateTime")
            try:
                now_server = get_server_time()
                now_plus_1m = now_server + timedelta(minutes=1, seconds=10)
                
                if from_dt and to_dt:
                    parsed_from = datetime.strptime(from_dt, "%Y-%m-%d %H:%M")
                    parsed_to = datetime.strptime(to_dt, "%Y-%m-%d %H:%M")
                    delta = parsed_to - parsed_from
                    new_from = now_plus_1m
                    new_to = new_from + delta
                    rec["fromDateTime"] = new_from.strftime("%Y-%m-%d %H:%M")
                    rec["toDateTime"] = new_to.strftime("%Y-%m-%d %H:%M")
                else:
                    rec["fromDateTime"] = now_plus_1m.strftime("%Y-%m-%d %H:%M")
                print(f"  🚀 [{pkey}] CREATE API 전송 직전 시작시각(+1분) 실시간 반영: {rec['fromDateTime']}")
            except Exception as e:
                print(f"  ⚠️ CREATE API 날짜 보정 예외: {e}")

        payload_list = [single_payload]
        print(f"  ➡️ [{pkey}] 알림 생성 요청 중 (Recurrence: {single_payload.get('recurrence')})...")
        try:
            resp = request_with_retry("POST", API_BASE, headers=build_headers(), json=payload_list)
            print(f"    상태 코드: {resp.status_code}")
            
            try:
                result_json = resp.json()
            except ValueError:
                result_json = {"raw_text": resp.text, "status_code": resp.status_code}

            if resp.ok:
                print(f"    ✅ [{pkey}] 알림 생성 성공")
                if isinstance(result_json, list) and len(result_json) > 0:
                    created_results.append(result_json[0])
                else:
                    created_results.append(result_json)
                success_reset_flags.append(rb_flag)
            else:
                print(f"    ❌ [{pkey}] 알림 생성 실패: {resp.text}")
                failed_notifications.append({
                    "project_key": pkey,
                    "status_code": resp.status_code,
                    "error": resp.text,
                    "payload": single_payload
                })
        except Exception as e:
            print(f"    ❌ [{pkey}] 요청 중 예외 발생: {e}")
            failed_notifications.append({
                "project_key": pkey,
                "error": str(e),
                "payload": single_payload
            })

    # 전체 실행 요약에 저장
    EXECUTION_SUMMARY["create_results"] = {
        "success_count": len(created_results),
        "failed_count": len(failed_notifications),
        "failed_details": failed_notifications,
        "success_details": created_results
    }

    if failed_notifications:
        print(f"\n  ⚠️ 일부 알림 생성에 실패했습니다 (실패: {len(failed_notifications)}개).")
        for f in failed_notifications:
            print(f"    - [{f['project_key']}] 에러: {f.get('error') or f.get('status_code')}")

    return created_results, success_reset_flags


def get_notifications():
    print("\n[RETRIEVE] 알림 목록 조회 중...")
    resp = request_with_retry("GET", API_BASE, headers=build_headers())
    if resp.ok:
        try:
            return resp.json()
        except ValueError:
            return []
    else:
        print(f"  ❌ 알림 목록 조회 실패: {resp.text}")
        return []


def delete_notification(notification_id: int, dry_run: bool = False):
    print(f"  🗑️ 알림 ID={notification_id} 삭제 중...")
    if dry_run:
        print(f"    🔍 [DRY-RUN] DELETE {API_BASE}/{notification_id}")
        return True
    resp = request_with_retry("DELETE", f"{API_BASE}/{notification_id}", headers=build_headers())
    if resp.ok:
        print(f"    ✅ 알림 ID={notification_id} 삭제 성공")
        return True
    else:
        print(f"    ❌ 알림 ID={notification_id} 삭제 실패: {resp.text}")
        return False



# ──────────────────────────────────────────────
# 템플릿 로드 & 벌크 가공 함수
# ──────────────────────────────────────────────
def load_template_row(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        print(f"❌ 템플릿 파일 없음: {file_path}")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("noti_template", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"❌ Python 템플릿 파일 로드 오류: {e}")
        sys.exit(1)
    
    raw_notifications = getattr(module, "NOTIFICATIONS", getattr(module, "notifications", None))
    if not raw_notifications or not isinstance(raw_notifications, list):
        print(f"❌ Python 템플릿 파일에 올바른 'NOTIFICATIONS' 리스트가 정의되어 있지 않습니다.")
        sys.exit(1)
    return raw_notifications[0]


def build_notification_payload(template_row: dict, project_key: str, is_confluence: bool) -> dict:
    """
    템플릿 행과 프로젝트 키를 결합하여 개별 알림 페이로드를 구성합니다.
    """
    payload = {}

    # 1. 템플릿 텍스트 내 플레이스홀더 치환 대상 탐색 (targetProject 또는 targetSpace 값 검출)
    template_target_key = ""
    target_vals = template_row.get("targetProject") or template_row.get("targetSpace")
    if target_vals:
        if isinstance(target_vals, list):
            template_target_key = str(target_vals[0]).strip() if target_vals else ""
        else:
            template_target_key = str(target_vals).strip()

    # 2. 플레이스홀더 치환 정의
    def replace_placeholder(text: str) -> str:
        if not text:
            return text
        # $ProjectKey, $SpaceKey 명시적 치환
        text = text.replace("$ProjectKey", project_key)
        text = text.replace("$SpaceKey", project_key)
        # template_target_key 치환
        if template_target_key:
            text = text.replace(template_target_key, project_key)
        return text

    # 3. 필수 및 선택 공통 필드들 복사 & 치환 (템플릿 우선 -> env 순으로 값 조회)
    for tmpl_key, api_key, cast_type, required in COLUMN_SCHEMA:
        raw_val = get_val(template_row, tmpl_key)
        if raw_val is None:
            raw_val = ""
        elif isinstance(raw_val, str):
            raw_val = raw_val.strip()
        
        # description은 빈 값이라도 복사
        if tmpl_key == "description":
            payload[api_key] = replace_placeholder(str(raw_val))
            continue
            
        if (raw_val == "" or raw_val is None) and required:
            print(f"  ⚠️ 템플릿 오류: 필수 필드 [{tmpl_key}] 값이 비어있습니다.")
            continue

        value = cast_value(raw_val, cast_type, tmpl_key)
        if value is not None:
            if isinstance(value, str):
                payload[api_key] = replace_placeholder(value)
            else:
                payload[api_key] = value

    # reset_button은 스키마 외 별도 처리
    raw_rb = get_val(template_row, "reset_button")
    if raw_rb is not None:
        if isinstance(raw_rb, bool):
            payload["reset_button"] = "true" if raw_rb else "false"
        elif str(raw_rb).strip().lower() in ("true", "1", "yes"):
            payload["reset_button"] = "true"
        elif str(raw_rb).strip().lower() in ("false", "0", "no"):
            payload["reset_button"] = "false"
        else:
            payload["reset_button"] = str(raw_rb).strip()

    # recurrence 처리
    rec = None
    if "recurrence" in template_row and isinstance(template_row["recurrence"], dict):
        rec = template_row["recurrence"].copy()
        from_dt = rec.get("fromDateTime")
        to_dt = rec.get("toDateTime")
        if from_dt and to_dt:
            errors = []
            from_dt = normalize_dt(from_dt, "fromDateTime", errors)
            to_dt = normalize_dt(to_dt, "toDateTime", errors)
            if from_dt and to_dt and not errors:
                try:
                    now_server = get_server_time()
                    now_plus_1m_10s = now_server + timedelta(minutes=1, seconds=10)
                    parsed_from = datetime.strptime(from_dt, "%Y-%m-%d %H:%M")
                    parsed_to = datetime.strptime(to_dt, "%Y-%m-%d %H:%M")
                    if parsed_from < now_plus_1m_10s:
                        delta = parsed_to - parsed_from
                        new_from = now_plus_1m_10s
                        new_to = new_from + delta
                        from_dt = new_from.strftime("%Y-%m-%d %H:%M")
                        to_dt = new_to.strftime("%Y-%m-%d %H:%M")
                        print(f"  ℹ️  날짜 보정 (recurrence dict): {parsed_from} ~ {parsed_to} -> {from_dt} ~ {to_dt}")
                except Exception as e:
                    print(f"  ⚠️  날짜 자동 보정 실패: {e}")
            rec["fromDateTime"] = from_dt
            rec["toDateTime"] = to_dt
    else:
        # 개별 필드(템플릿 또는 환경변수)에서 recurrence 구성 시도
        rec_from = get_val(template_row, "recurrence_from", fallback_keys=["fromDateTime", "from_date_time"])
        rec_to = get_val(template_row, "recurrence_to", fallback_keys=["toDateTime", "to_date_time"])
        rec_dur = get_val(template_row, "recurrence_duration", fallback_keys=["duration"])
        rec_freq = get_val(template_row, "recurrence_frequency", fallback_keys=["frequency", "frequencyType", "frequency_type"])
        if any([rec_from, rec_to, rec_dur, rec_freq]):
            rec = build_recurrence({
                "recurrence_from": rec_from,
                "recurrence_to": rec_to,
                "recurrence_duration": rec_dur,
                "recurrence_frequency": rec_freq,
            })
            if not rec:
                print(f"  ⚠️ [DEBUG] build_recurrence 실패 (입력값: from='{rec_from}', to='{rec_to}', dur='{rec_dur}', freq='{rec_freq}')")

    if not rec:
        print(f"  ⚠️ [주의] 최종 recurrence 페이로드가 설정되지 않아 null로 전송됩니다. (템플릿/env 모두 미지정 또는 빌드 실패)")
    else:
        print(f"  ✅ [Recurrence 성공] 최종 전송할 recurrence: from='{rec.get('fromDateTime')}', to='{rec.get('toDateTime')}'")
        # API 실행 시점 기준 시작 시각 과거 검증 및 +1분 10초 보정 (start date in past 오류 예방)
        from_dt = rec.get("fromDateTime")
        to_dt = rec.get("toDateTime")
        if from_dt and to_dt:
            try:
                now_server = get_server_time()
                now_plus_1m = now_server + timedelta(minutes=1, seconds=10)
                parsed_from = datetime.strptime(from_dt, "%Y-%m-%d %H:%M")
                parsed_to = datetime.strptime(to_dt, "%Y-%m-%d %H:%M")
                if parsed_from < now_plus_1m:
                    delta = parsed_to - parsed_from
                    new_from = now_plus_1m
                    new_to = new_from + delta
                    rec["fromDateTime"] = new_from.strftime("%Y-%m-%d %H:%M")
                    rec["toDateTime"] = new_to.strftime("%Y-%m-%d %H:%M")
                    print(f"  ℹ️  스케줄 시작시각 보정 (API 실행시점+1분 적용): {parsed_from} -> {rec['fromDateTime']}")
            except Exception as e:
                print(f"  ⚠️  최종 날짜 보정 예외: {e}")
        payload["recurrence"] = rec

    # Target User: "Show announcement upon every login" 옵션 설정 보장 (filterUser=False, targetUser=[], reset_button="true")
    if "filterUser" not in payload:
        payload["filterUser"] = False
    if "targetUser" not in payload:
        payload["targetUser"] = []
    if "reset_button" not in payload:
        payload["reset_button"] = "true"

    # 4. 앱 타입별 프로젝트/스페이스 필터 설정
    # 서버의 NotificationUpsertDTO 클래스는 Jira와 Confluence 관계없이 항상 filterProjects와 targetProject 필드를 사용합니다.
    payload["filterProjects"] = "true"
    payload["targetProject"]  = [project_key]
    
    # 불필요한 filterSpaces/targetSpace 필드는 전송에서 제외합니다.
    payload.pop("filterSpaces", None)
    payload.pop("targetSpace", None)

    return payload


# ──────────────────────────────────────────────
# 메인 실행 로직
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Announcer - project.txt를 읽어 각 프로젝트/스페이스에 대한 벌크 알림 생성"
    )
    parser.add_argument("--project-file", default=DEFAULT_PROJECT_FILE, help="프로젝트 키 목록 파일 경로")
    parser.add_argument("--template", help="템플릿 Python 파일 경로 (미지정시 앱 타입 기본 템플릿 사용)")
    parser.add_argument("--dry-run", action="store_true", help="실제 API 요청 없이 결과 분석 및 검증만 진행")
    parser.add_argument("--app-type", choices=["jira", "confluence"], help="앱 타입 지정 (기본: 환경변수 APP_TYPE)")
    parser.add_argument("--env", choices=["stg", "ops"], help="실행 환경 지정 (stg | ops)")
    parser.add_argument("--delete", action="store_true", help="project.txt의 프로젝트/스페이스 키가 지정된 알림 삭제")
    parser.add_argument("--delete-file", help="생성 결과 JSON 파일 경로를 지정하여 알림 삭제")
    args = parser.parse_args()

    # 앱 타입 결정
    app_type = args.app_type or APP_TYPE
    is_confluence = app_type.lower() == "confluence"
    
    # 템플릿 결정
    default_template = DEFAULT_CONF_TEMPLATE if is_confluence else DEFAULT_JIRA_TEMPLATE
    template_path = args.template or default_template

    server_now = get_server_time().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 65)
    print(f"⚙️  [실행 전 파라미터 및 스케줄링 설정 확인 - 알림 {'삭제' if args.delete else '벌크 생성'}]")
    print("=" * 65)
    print(f"  • 대상 서버 URL    : {APP_BASE_URL}")
    print(f"  • 대상 애플리케이션: {app_type.upper()}")
    print(f"  • 실행 환경 (ENV)   : {'OPS (Production)' if IS_OPS_ENV else 'STG (Staging)'}")
    print(f"  • 실시간 서버 시각 : {server_now} {'(KST)' if IS_OPS_ENV else '(UTC)'}")
    print(f"  • 키 목록 파일     : {args.project_file}")
    if not args.delete:
        print(f"  • 적용 템플릿 파일 : {template_path}")
    print(f"  • 사용자 계정     : {APP_USERNAME if APP_USERNAME else 'PAT Bearer Token'}")
    print(f"  • 실행 모드        : {'🔍 DRY-RUN (실제 요청 없음)' if args.dry_run else '🚀 실제 API 요청'}")
    print("=" * 65 + "\n")

    # 1. project.txt 읽기 (delete_file 모드가 아닐 때만 프로젝트 키 파일을 읽음)
    project_keys = []
    if not args.delete_file:
        proj_path = Path(args.project_file)
        if not proj_path.exists():
            print(f"❌ 프로젝트 키 파일이 존재하지 않습니다: {args.project_file}")
            print("   샘플 project.txt 파일을 생성하거나 올바른 경로를 지정해주세요.")
            sys.exit(1)

        encoding = detect_encoding(proj_path)
        with open(proj_path, "r", encoding=encoding) as f:
            for line in f:
                line_val = line.strip()
                # 빈 줄 및 주석 제외
                if line_val and not line_val.startswith("#"):
                    project_keys.append(line_val)

        if not project_keys:
            print(f"❌ '{args.project_file}' 파일에 유효한 프로젝트/스페이스 키가 없습니다.")
            sys.exit(1)

        print(f"  총 {len(project_keys)}개의 키를 로드했습니다: {project_keys}")

    # 삭제 모드 또는 JSON 파일 삭제 모드일 경우 삭제 로직 진행
    if args.delete or args.delete_file:
        print("\n── 알림 삭제 모드 실행 ─────────────────────────────────")
        to_delete = []

        if args.delete_file:
            # JSON 파일에서 ID 목록 추출하여 삭제 대상 구성
            file_path = Path(args.delete_file)
            if not file_path.exists():
                print(f"❌ 삭제 대상 JSON 파일이 존재하지 않습니다: {file_path}")
                sys.exit(1)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                file_ids = extract_ids_from_json(data)
            except Exception as e:
                print(f"❌ JSON 파일 로드 중 오류 발생: {e}")
                sys.exit(1)

            if not file_ids:
                print(f"❌ 파일 '{file_path}'에서 추출할 수 있는 알림 ID가 없습니다.")
                sys.exit(1)

            print(f"  파일에서 {len(file_ids)}개의 알림 ID를 추출했습니다: {file_ids}")

            # 서버에서 전체 알림 목록을 가져와 제목 매칭 (매칭되지 않는 경우도 삭제는 시도)
            existing_notifications = get_notifications()
            existing_dict = {str(item.get("id")): item for item in existing_notifications if item.get("id")}

            for nid in file_ids:
                nid_str = str(nid)
                if nid_str in existing_dict:
                    title = existing_dict[nid_str].get("title", "")
                    to_delete.append((nid, title, "JSON 파일 매칭"))
                else:
                    to_delete.append((nid, f"ID: {nid} (서버에서 조회되지 않음)", "JSON 파일 지정"))
        else:
            # 기존 project.txt / space.txt 기반 삭제 로직
            existing_notifications = get_notifications()
            if not existing_notifications:
                print("  서버에서 조회된 알림이 없습니다.")
                return

            project_keys_set = set(project_keys)

            def extract_keys(val):
                if not val:
                    return []
                if isinstance(val, list):
                    return [str(v).strip() for v in val if str(v).strip()]
                if isinstance(val, str):
                    clean = val.strip("[]").replace("'", "").replace('"', "")
                    return [v.strip() for v in clean.split(",") if v.strip()]
                return []

            for item in existing_notifications:
                nid = item.get("id")
                title = item.get("title", "")
                
                targets = []
                if is_confluence:
                    targets.extend(extract_keys(item.get("target_space", item.get("targetSpace"))))
                else:
                    targets.extend(extract_keys(item.get("target_project", item.get("targetProject"))))
                
                matched_keys = [k for k in targets if k in project_keys_set]
                
                # 제목에서 "(키)" 패턴 백업 매칭
                if not matched_keys:
                    for pkey in project_keys:
                        if f"({pkey})" in title:
                            matched_keys.append(pkey)
                            break
                
                if matched_keys:
                    to_delete.append((nid, title, matched_keys))

        if not to_delete:
            print("  삭제할 일치하는 알림을 찾지 못했습니다.")
            return

        print(f"  일치하는 알림 {len(to_delete)}개를 찾았습니다:")
        for nid, title, source in to_delete:
            print(f"    - ID={nid} | Title='{title}' | 출처/매칭={source}")

        if args.dry_run:
            print("\n  🔍 [DRY-RUN] 삭제 처리를 생략합니다.")
            for nid, title, source in to_delete:
                delete_notification(nid, dry_run=True)
            return

        print("\n  삭제를 시작합니다...")
        success_count = 0
        for nid, title, source in to_delete:
            if delete_notification(nid, dry_run=False):
                success_count += 1
        
        print(f"\n✅ 완료. 총 {len(to_delete)}개 중 {success_count}개 삭제 완료.")
        return

    # 2. 템플릿 로드
    template_row = load_template_row(template_path)
    print(f"  템플릿 로드 완료. (Title: '{template_row.get('title')}')")

    # ──────────────────────────────────────────────
    # 이미 등록된 활성화(Active) 상태의 키 조회 및 중복 제거 로직
    # ──────────────────────────────────────────────
    print("\n  🔍 기존 등록된 활성화(Active) 상태의 알림을 조회합니다...")
    existing_notifications = get_notifications()
    active_existing_keys = set()
    
    print(f"  (서버에서 총 {len(existing_notifications)}개의 기존 알림을 조회함)")
    
    # 기존 등록된 알림 목록을 상세히 로그로 출력
    if existing_notifications:
        print("\n  📋 [조회된 기존 알림 목록]")
        for item in existing_notifications:
            nid = item.get("id", "Unknown ID")
            title = item.get("title", "No Title")
            is_active = item.get("isActive")
            if is_active is None:
                is_active = item.get("is_active")
            if is_active is None:
                is_active = item.get("active")
            active_str = "Active" if (is_active is True or str(is_active).strip().lower() == "true" or is_active is None) else "Inactive"
            
            # 타겟 정보 추출
            targets = []
            for target_field in ["targetProject", "target_project", "targetSpace", "target_space", "targetProjects", "targetSpaces"]:
                val = item.get(target_field)
                if val:
                    if isinstance(val, list):
                        targets.extend([str(v).strip() for v in val if v])
                    elif isinstance(val, str):
                        clean = val.strip("[]").replace("'", "").replace('"', "")
                        targets.extend([t.strip() for t in clean.split(",") if t.strip()])
            
            print(f"    - ID: {nid:<5} | Status: {active_str:<8} | Targets: {str(list(set(targets))):<30} | Title: {title}")
        print("")
    else:
        print("  📋 (기존 등록된 알림이 없습니다)\n")

    # 중복 제거 타겟 수집
    for item in existing_notifications:
        # isActive 상태 확인 (다양한 필드 표기 방식 대비: isActive, is_active, active)
        is_active = item.get("isActive")
        if is_active is None:
            is_active = item.get("is_active")
        if is_active is None:
            is_active = item.get("active")
            
        # 활성 상태 판별 (필드가 아예 누락된 경우 기본적으로 활성으로 간주)
        if is_active is True or str(is_active).strip().lower() == "true" or is_active is None:
            # 1) API 스키마 필드 기반 타겟 키 추출
            targets = []
            for target_field in ["targetProject", "target_project", "targetSpace", "target_space", "targetProjects", "targetSpaces"]:
                val = item.get(target_field)
                if val:
                    if isinstance(val, list):
                        targets.extend([str(v).strip() for v in val if v])
                    elif isinstance(val, str):
                        clean = val.strip("[]").replace("'", "").replace('"', "")
                        targets.extend([t.strip() for t in clean.split(",") if t.strip()])
            
            for t in targets:
                active_existing_keys.add(t)

            # 2) 백업 검증: 제목(title) 문자열 분석 매칭
            title = item.get("title", "")
            if title:
                for pkey in project_keys:
                    if f"({pkey})" in title or title.endswith(f" {pkey}"):
                        active_existing_keys.add(pkey)

    # 3. 프로젝트별 알림 페이로드 구성 및 시뮬레이션
    skipped_keys = []
    to_create_keys = []
    for pkey in project_keys:
        if pkey in active_existing_keys:
            skipped_keys.append(pkey)
        else:
            to_create_keys.append(pkey)

    # 📊 생성 예측 분석 리포트 출력
    print("\n" + "=" * 60)
    print(" 📊 [생성 예측 분석 리포트]")
    print("=" * 60)
    print(f"  • 총 처리 대상 키   : {len(project_keys)}개")
    print(f"  • 건너뜀 (중복 존재) : {len(skipped_keys)}개")
    if skipped_keys:
        print(f"    - 건너뛸 키 목록: {skipped_keys}")
    print(f"  • 신규 생성 예정    : {len(to_create_keys)}개")
    if to_create_keys:
        print(f"    - 생성할 키 목록: {to_create_keys}")
    print("=" * 60 + "\n")

    if not to_create_keys:
        print("  💡 새로 추가할 알림이 없습니다. 실행을 종료합니다.")
        return

    # 리포트 출력 후 계속 진행 여부 y/n 확인 프롬프트
    if sys.stdin.isatty():
        try:
            choice = input(f"▶ 신규 {len(to_create_keys)}개 알림을 실시간으로 즉시 생성하시겠습니까? (y/N): ").strip().lower()
            if choice not in ("y", "yes"):
                print("실행을 취소하고 프로그램을 종료합니다.")
                sys.exit(0)
        except KeyboardInterrupt:
            print("\n실행이 중단되었습니다.")
            sys.exit(0)
    else:
        print("▶ 비대화형 환경이므로 알림 생성을 진행합니다.")

    # 4. 배치 사전 구성 없이 각 키마다 실시간 조립 즉시 CREATE API 전송!
    print(f"\n🚀 알림 개별 실시간 전송 시작 (총 {len(to_create_keys)}개, DRY-RUN: {args.dry_run})")
    
    created_results = []
    success_reset_flags = []
    failed_notifications = []

    for idx, pkey in enumerate(to_create_keys, 1):
        print(f"\n──────────────────────────────────────────────")
        print(f"📌 [{idx}/{len(to_create_keys)}] 프로젝트 [{pkey}] 알림 생성 중...")
        
        # API 전송 0.001초 직전 실시간 시각 기반으로 페이로드 동적 조립
        payload = build_notification_payload(template_row, pkey, is_confluence)
        errors = validate_payload(payload)
        if errors:
            print(f"  ❌ [{pkey}] 페이로드 검증 실패: {errors}")
            failed_notifications.append({"project_key": pkey, "error": f"페이로드 검증 실패: {errors}"})
            continue

        rb_flag = payload.pop("reset_button", None)
        single_payload = {k: v for k, v in payload.items() if k != "id"}

        # 🚀 CREATE API 발송 0.001초 직전 시작시각(+1분 10초) 실시간 강제 보정
        rec = single_payload.get("recurrence")
        if isinstance(rec, dict):
            from_dt = rec.get("fromDateTime")
            to_dt = rec.get("toDateTime")
            try:
                now_server = get_server_time()
                now_plus_1m = now_server + timedelta(minutes=1, seconds=10)
                
                if from_dt and to_dt:
                    parsed_from = datetime.strptime(from_dt, "%Y-%m-%d %H:%M")
                    parsed_to = datetime.strptime(to_dt, "%Y-%m-%d %H:%M")
                    delta = parsed_to - parsed_from
                    new_from = now_plus_1m
                    new_to = new_from + delta
                    rec["fromDateTime"] = new_from.strftime("%Y-%m-%d %H:%M")
                    rec["toDateTime"] = new_to.strftime("%Y-%m-%d %H:%M")
                else:
                    rec["fromDateTime"] = now_plus_1m.strftime("%Y-%m-%d %H:%M")
                print(f"  🚀 [{pkey}] CREATE API 전송 직전 시작시각(+1분) 실시간 반영: {rec['fromDateTime']}")
            except Exception as e:
                print(f"  ⚠️ CREATE API 날짜 보정 예외: {e}")

        payload_list = [single_payload]

        if args.dry_run:
            print(f"  🔍 [DRY-RUN] POST {API_BASE} | [{pkey}] 생성 전송 예정")
            print(json.dumps(payload_list, ensure_ascii=False, indent=2))
            created_results.append({"id": f"dry_{idx}", "targetProject": [pkey]})
            success_reset_flags.append(rb_flag)
            continue

        # 페이로드 조립 즉시 0.001초 만에 CREATE API 단건 실시간 전송!
        try:
            resp = request_with_retry("POST", API_BASE, headers=build_headers(), json=payload_list)
            print(f"  ▶ 서버 응답 상태 코드: {resp.status_code}")
            if resp.ok:
                print(f"  ✅ [{pkey}] 알림 생성 성공!")
                try:
                    result_json = resp.json()
                    item = result_json[0] if isinstance(result_json, list) and result_json else result_json
                except Exception:
                    item = {"id": "created", "targetProject": [pkey]}
                created_results.append(item)
                success_reset_flags.append(rb_flag)
            else:
                print(f"  ❌ [{pkey}] 알림 생성 실패: {resp.text}")
                failed_notifications.append({"project_key": pkey, "status_code": resp.status_code, "error": resp.text})
        except Exception as e:
            print(f"  ❌ [{pkey}] 요청 예외 발생: {e}")
            failed_notifications.append({"project_key": pkey, "error": str(e)})

    # 전체 실행 요약에 저장
    EXECUTION_SUMMARY["create_results"] = {
        "success_count": len(created_results),
        "failed_count": len(failed_notifications),
        "failed_details": failed_notifications,
        "success_details": created_results
    }

    # 5. ScriptRunner reset_button API 요청
    if created_results:
        _apply_reset_button(created_results, success_reset_flags, dry_run=args.dry_run)

    # 6. 실행 결과 요약 파일 저장
    save_execution_summary()

    print(f"\n✅ 완료. 총 {len(to_create_keys)}개 중 {len(created_results)}개 성공, {len(failed_notifications)}개 실패.")


if __name__ == "__main__":
    main()
