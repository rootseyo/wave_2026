#!/usr/bin/env python
"""
Announcer for Jira / Confluence - 제목 조건 기반 알림 내용 최신화 (Update) 스크립트

사용법:
    python update_notifications_by_title.py [--app-type jira|confluence] [--env stg|ops] [--dry-run]
"""

import argparse
import base64
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import requests

IS_OPS_ENV = False

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
SAVE_DIR = os.getenv("SAVE_DIR") or "results"

REQUEST_DELAY = float(os.getenv("ANNOUNCER_REQUEST_DELAY", "0.5"))
MAX_RETRIES = int(os.getenv("ANNOUNCER_MAX_RETRIES", "3"))
REQUEST_TIMEOUT = float(os.getenv("ANNOUNCER_REQUEST_TIMEOUT", "30"))
RETRY_STATUS_CODES = {400, 429, 502, 503, 504}
_last_request_at = 0.0


def request_with_retry(method, url, **kwargs):
    """요청 간 간격과 일시 오류에 대한 지수 백오프를 적용한다."""
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
            print(f"  ⚠️ 네트워크 오류: {exc}. {delay}초 후 재시도 ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)
            continue

        if response.status_code not in RETRY_STATUS_CODES or attempt == MAX_RETRIES:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 2 ** attempt
        except ValueError:
            delay = 2 ** attempt
        print(f"  ⚠️ HTTP {response.status_code}: {response.text[:300]}. "
              f"{delay:g}초 후 재시도 ({attempt + 1}/{MAX_RETRIES})")
        time.sleep(delay)

COLUMN_SCHEMA = [
    ("title",          "title",          "str",      True),
    ("description",    "description",    "str",      False),
    ("contents",       "contents",       "str",      True),
    ("type",           "type",           "str",      True),
    ("displayType",    "displayType",    "str",      True),
    ("showButton",     "showButton",     "str",      True),
    ("isActive",       "isActive",       "str",      True),
    ("format",         "format",         "str",      True),
    ("buttonText",     "buttonText",     "str",      False),
    ("delayButton",    "delayButton",    "str",      False),
    ("anonymous",      "anonymous",      "str",      False),
    ("filterGroups",   "filterGroups",   "str",      False),
    ("targetGroup",    "targetGroup",    "str",      False),
    ("filterUser",     "filterUser",     "str",      False),
    ("targetUser",     "targetUser",     "str_list", False),
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
# 설정 로드 & 전역 환경 초기화
# ──────────────────────────────────────────────
def load_env_file(filepath="../env"):
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
    
    selected_app = None
    for i, arg in enumerate(sys.argv):
        if arg == "--app-type" and i + 1 < len(sys.argv):
            selected_app = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--app-type="):
            selected_app = arg.split("=", 1)[1].strip().lower()

    if selected_app not in ("jira", "confluence"):
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
            selected_app = (os.getenv("APP_TYPE") or "").lower()

    APP_TYPE = selected_app
    IS_CONFLUENCE = (APP_TYPE == "confluence")
    DEFAULT_PROJECT_FILE = "space.txt" if IS_CONFLUENCE else "project.txt"
    print(f"▶ 대상 애플리케이션: {APP_TYPE.upper()} (키 목록 파일: {DEFAULT_PROJECT_FILE})")

def select_environment():
    global IS_OPS_ENV, APP_BASE_URL, APP_USERNAME, APP_PASSWORD, APP_TOKEN, APP_COOKIE, API_BASE
    
    selected_env = None
    for i, arg in enumerate(sys.argv):
        if arg == "--env" and i + 1 < len(sys.argv):
            selected_env = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--env="):
            selected_env = arg.split("=", 1)[1].strip().lower()

    if not IS_CONFLUENCE:
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
        stg_url = os.getenv("CONF_DC_STG_URL") or os.getenv("CONF_STG_URL") or os.getenv("STG_CONF_URL")
        stg_token = os.getenv("CONF_DC_STG_TOKEN") or os.getenv("CONF_DC_STG_API_TOKEN")
        stg_user = os.getenv("CONF_DC_STG_USERNAME") or os.getenv("STG_CONF_USERNAME")
        stg_password = os.getenv("CONF_DC_STG_PASSWORD") or os.getenv("STG_CONF_PASSWORD")
        stg_cookie = os.getenv("CONF_DC_STG_COOKIE") or os.getenv("STG_CONF_COOKIE")

        ops_url = os.getenv("CONF_DC_OPS_URL") or os.getenv("CONF_OPS_URL") or os.getenv("OPS_CONF_URL")
        ops_token = os.getenv("CONF_DC_OPS_TOKEN") or os.getenv("CONF_DC_OPS_API_TOKEN")
        ops_user = os.getenv("CONF_DC_OPS_USERNAME") or os.getenv("CONF_OPS_USERNAME")
        ops_password = os.getenv("CONF_DC_OPS_PASSWORD") or os.getenv("CONF_OPS_PASSWORD")
        ops_cookie = os.getenv("CONF_DC_OPS_COOKIE") or os.getenv("CONF_OPS_COOKIE")

    if selected_env in ("stg", "ops"):
        choice_env = selected_env.upper()
    else:
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

# 초기화 실행
load_env_file()
select_app_type()
select_environment()

# ──────────────────────────────────────────────
# 인증 및 공통 유틸리티
# ──────────────────────────────────────────────
def make_basic_token() -> str:
    return base64.b64encode(f"{APP_USERNAME}:{APP_PASSWORD}".encode()).decode()

def build_headers() -> dict:
    h = {
        "Content-Type":  "application/json",
    }
    if APP_TOKEN and not APP_USERNAME:
        h["Authorization"] = f"Bearer {APP_TOKEN}"
    elif APP_USERNAME and APP_PASSWORD:
        h["Authorization"] = f"Basic {make_basic_token()}"
    elif APP_TOKEN:
        h["Authorization"] = f"Bearer {APP_TOKEN}"
        
    if APP_COOKIE:
        h["Cookie"] = APP_COOKIE
    return h

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

def extract_target_keys(item: dict) -> list:
    """알림 아이템에서 프로젝트 또는 스페이스 키 목록을 추출한다."""
    targets = []
    for target_field in ["targetProject", "target_project", "targetSpace", "target_space", "targetProjects", "targetSpaces"]:
        val = item.get(target_field)
        if val:
            if isinstance(val, list):
                targets.extend([str(v).strip() for v in val if str(v).strip()])
            elif isinstance(val, str):
                clean = val.strip("[]").replace("'", "").replace('"', "")
                targets.extend([t.strip() for t in clean.split(",") if t.strip()])
    return list(dict.fromkeys(targets))

def extract_is_active(item: dict) -> bool:
    """알림 아이템의 활성화 상태를 추출한다."""
    is_active = item.get("isActive")
    if is_active is None:
        is_active = item.get("is_active")
    if is_active is None:
        is_active = item.get("active")
    return is_active is True or str(is_active).strip().lower() == "true"

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

def normalize_dt(raw: str, field: str, errors: list) -> str:
    for fmt in DATE_FORMATS:
        try:
            dt_kst = datetime.strptime(raw, fmt)
            if IS_OPS_ENV:
                return dt_kst.strftime("%Y-%m-%d %H:%M")
            else:
                dt_utc = dt_kst - timedelta(hours=9)
                return dt_utc.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    errors.append(f"{field}='{raw}' 날짜 형식 오류 (예: 2025-10-31 00:00)")
    return raw

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
            print(f"  ⚠️ Recurrence 빌드 에러: {e}")
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
            return None
    if cast_type == "bool":
        if isinstance(raw, bool):
            return raw
        val_str = str(raw).strip().lower()
        if val_str in ("true", "1", "yes"):
            return True
        if val_str in ("false", "0", "no"):
            return False
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
    return errors

def build_notification_payload(template_row: dict, project_key: str, is_confluence: bool, existing_item: dict = None) -> dict:
    payload = {}
    template_target_key = ""
    target_vals = template_row.get("targetProject") or template_row.get("targetSpace")
    if target_vals:
        if isinstance(target_vals, list):
            template_target_key = str(target_vals[0]).strip() if target_vals else ""
        else:
            template_target_key = str(target_vals).strip()

    def replace_placeholder(text: str) -> str:
        if not text:
            return text
        text = text.replace("$ProjectKey", project_key)
        text = text.replace("$SpaceKey", project_key)
        if template_target_key:
            text = text.replace(template_target_key, project_key)
        return text

    for tmpl_key, api_key, cast_type, required in COLUMN_SCHEMA:
        raw_val = get_val(template_row, tmpl_key)
        if raw_val is None:
            raw_val = ""
        elif isinstance(raw_val, str):
            raw_val = raw_val.strip()
        
        if tmpl_key == "description":
            payload[api_key] = replace_placeholder(str(raw_val))
            continue
            
        if (raw_val == "" or raw_val is None) and required:
            continue

        value = cast_value(raw_val, cast_type, tmpl_key)
        if value is not None:
            if isinstance(value, str):
                payload[api_key] = replace_placeholder(value)
            else:
                payload[api_key] = value

    raw_rb = get_val(template_row, "reset_button")
    if raw_rb is not None:
        if isinstance(raw_rb, bool):
            payload["reset_button"] = "true" if raw_rb else "false"
        elif str(raw_rb).strip().lower() in ("true", "1", "yes"):
            payload["reset_button"] = "true"
        elif str(raw_rb).strip().lower() in ("false", "0", "no"):
            payload["reset_button"] = "false"

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

    # 템플릿/환경변수에 recurrence 설정이 지정되지 않은 경우, 기존 알림의 recurrence 보존
    if not rec and existing_item and existing_item.get("recurrence"):
        rec = existing_item.get("recurrence")
        if rec:
            print(f"  ℹ️  기존 알림(ID={existing_item.get('id')})의 recurrence 설정을 로드하여 보존합니다.")

    if not rec:
        print(f"  ⚠️ [주의] 최종 recurrence 페이로드가 설정되지 않아 null로 전송됩니다. (템플릿/env/기존알림 모두 미지정 또는 빌드 실패)")
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

    payload["filterProjects"] = "true"
    payload["targetProject"]  = [project_key]
    payload.pop("filterSpaces", None)
    payload.pop("targetSpace", None)

    return payload

def scriptrunner_reset_button(notification_id, dry_run: bool = False):
    endpoint = "/rest/scriptrunner/latest/custom/everyLoginNotificationButton"
    url      = f"{APP_BASE_URL}{endpoint}"
    params   = {"id": notification_id}

    if dry_run:
        print(f"  🔍 [DRY-RUN] ScriptRunner POST {url}?id={notification_id}")
        return True

    print(f"  🔧 ScriptRunner reset_button 설정 중... (id={notification_id})")
    try:
        resp = request_with_retry("POST", url, headers=build_headers(), params=params)
        return resp.ok
    except Exception as e:
        print(f"  ❌ ScriptRunner 요청 오류: {e}")
        return False

def update_notification_by_id(nid: int, payload: dict, reset_button_flag: str = None, dry_run: bool = False) -> bool:
    """단일 알림 객체를 PUT API로 수정 요청하고 디버깅 정보를 상세 출력한다."""
    single_payload = {k: v for k, v in payload.items() if k != "id"}
    target_key = payload.get("targetProject", ["Unknown"])[0]

    # 🛡️ API 전송 직전 0.001초 최종 안전 보정: 시작 시각이 과거/현재 시각이면 현재 서버 시각+1분 10초로 동적 조정
    rec = single_payload.get("recurrence")
    if isinstance(rec, dict):
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
                    print(f"  🛡️ [{target_key}] API 전송 직전 실시간 스케줄 보정: {parsed_from} -> {rec['fromDateTime']} (서버시각+1분 적용)")
            except Exception as e:
                print(f"  ⚠️ 전송 직전 날짜 보정 예외: {e}")

    put_url = f"{API_BASE}/{nid}"
    target_key = payload.get("targetProject", ["Unknown"])[0]

    print(f"\n──────────────────────────────────────────────")
    print(f"🛠️  [DEBUG] 알림 업데이트 시도 (ID={nid}, Target={target_key})")
    print(f"   ▶ PUT URL : {put_url}")
    print(f"   ▶ 전송 Recurrence 페이로드:")
    print(json.dumps(single_payload.get("recurrence"), ensure_ascii=False, indent=4))

    if dry_run:
        print(f"  🔍 [DRY-RUN] 실제 요청 생략 (DRY-RUN 모드)")
        print(f"──────────────────────────────────────────────")
        return True

    try:
        resp = request_with_retry("PUT", put_url, headers=build_headers(), json=single_payload)
        try:
            body_json = resp.json()
            body_formatted = json.dumps(body_json, ensure_ascii=False, indent=4)
        except Exception:
            body_formatted = resp.text if resp.text else '(빈 응답)'

        print(f"   ▶ 서버 응답 본문 전체 (Response Body Full):")
        print(body_formatted)

        if resp.ok:
            print(f"  ✅ ID={nid} | [{target_key}] PUT 수정 요청 성공")
            
            # 🔍 [핵심 디버그] 수정 직후 서버에 실제 저장된 저장 상태를 GET으로 검증 조회
            try:
                verify_resp = request_with_retry("GET", f"{API_BASE}/{nid}", headers=build_headers())
                if verify_resp.ok:
                    saved_item = verify_resp.json()
                    saved_rec = saved_item.get("recurrence")
                    print(f"   🔍 [VERIFY] 서버에 실제 반영된 Recurrence 저장 상태:")
                    print(json.dumps(saved_rec, ensure_ascii=False, indent=4))
                    if not saved_rec:
                        print(f"   ⚠️ [경고] 서버 응답은 OK(200)였으나, 서버 DB의 recurrence 필드가 null/empty 입니다!")
            except Exception as ve:
                print(f"   ⚠️ 검증 GET 조회 중 오류: {ve}")

            if str(reset_button_flag).lower() == "true":
                scriptrunner_reset_button(nid, dry_run=dry_run)
            print(f"──────────────────────────────────────────────")
            return True
        else:
            print(f"  ❌ ID={nid} | [{target_key}] 업데이트 실패 (상태코드 {resp.status_code}): {resp.text}")
            print(f"──────────────────────────────────────────────")
            return False
    except Exception as e:
        print(f"  ❌ ID={nid} | [{target_key}] 수정 요청 중 예외 발생: {e}")
        print(f"──────────────────────────────────────────────")
        return False

# ──────────────────────────────────────────────
# 메인 업데이트 로직
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="제목 조건 기반 알림 내용 최신화 (Update) 스크립트"
    )
    parser.add_argument("--template", help="템플릿 Python 파일 경로 (미지정시 앱 타입 기본 사용)")
    parser.add_argument("--title-pattern", help="매칭할 제목 패턴 (지정하지 않으면 대화식 입력)")
    parser.add_argument("--dry-run", action="store_true", help="실제 API 요청 없이 대상 매칭 결과만 확인")
    parser.add_argument("--app-type", choices=["jira", "confluence"], help="앱 타입 지정 (jira | confluence)")
    parser.add_argument("--env", choices=["stg", "ops"], help="실행 환경 지정 (stg | ops)")
    args = parser.parse_args()

    default_template = DEFAULT_CONF_TEMPLATE if IS_CONFLUENCE else DEFAULT_JIRA_TEMPLATE
    template_path = args.template or default_template
    
    # 1. 템플릿 로드
    template_row = load_template_row(template_path)
    print(f"  템플릿 로드 완료. (Title: '{template_row.get('title')}')")

    # 2. 매칭할 제목 패턴 결정
    title_pattern = args.title_pattern
    if not title_pattern:
        default_pattern = "Confluence SaaS 전환 안내 - 26년 Wave 1" if IS_CONFLUENCE else "Jira Saas 전환 안내 - 26년 Wave 1"
        if sys.stdin.isatty():
            print("──────────────────────────────────────────────")
            user_input = input(f"조회/매칭할 알림 제목의 조건 문자열을 입력하세요\n(기본값: '{default_pattern}'): ").strip()
            title_pattern = user_input if user_input else default_pattern
            print("──────────────────────────────────────────────")
        else:
            title_pattern = default_pattern

    print(f"🔎 매칭 조건: 제목에 '{title_pattern}' 포함")

    # 3. 기존 알림 조회 및 필터링
    existing_notifications = get_notifications()
    matched_notifications = []
    
    active_count = 0
    deactive_count = 0

    for item in existing_notifications:
        title = item.get("title", "")
        if title_pattern.lower() in title.lower():
            if extract_is_active(item):
                active_count += 1
                matched_notifications.append(item)
            else:
                deactive_count += 1

    print(f"📊 조회 결과: 총 {active_count + deactive_count}개 매칭 (Active: {active_count}개, Deactive: {deactive_count}개)")
    print(f"📊 업데이트 대상 알림(Active) 개수: {len(matched_notifications)}개")

    if not matched_notifications:
        print("💡 업데이트할 활성(Active) 상태의 알림이 없습니다. 종료합니다.")
        return

    # 상세 목록 출력
    print("\n📋 [업데이트 대상 알림 목록]")
    for item in matched_notifications:
        nid = item.get("id")
        title = item.get("title")
        targets = extract_target_keys(item)
        targets_str = str(targets) if targets else "Unknown"
        print(f"  - ID: {nid:<5} | Targets: {targets_str:<18} | Title: {title}")
    print("")

    # 4. 실행 전 파라미터 사전 확인 요약 카드 출력
    noti_type = get_val(template_row, "type") or "dialog"
    display_type = get_val(template_row, "displayType") or "2"
    show_button = get_val(template_row, "showButton") or "true"
    is_active = get_val(template_row, "isActive") or "true"
    noti_format = get_val(template_row, "format") or "1"
    reset_button = get_val(template_row, "reset_button") or "true"
    
    rec_from = get_val(template_row, "recurrence_from", fallback_keys=["fromDateTime"]) or "자동계산 (API 실행시점 +1분)"
    rec_to = get_val(template_row, "recurrence_to", fallback_keys=["toDateTime"]) or "자동계산"
    rec_dur = get_val(template_row, "recurrence_duration", fallback_keys=["duration"]) or "3"
    rec_freq = get_val(template_row, "recurrence_frequency", fallback_keys=["frequency", "frequencyType"]) or "months"
    freq_kr = {"day": "일", "days": "일", "week": "주", "weeks": "주", "month": "개월", "months": "개월"}.get(str(rec_freq).lower(), str(rec_freq))
    rec_info = f"{rec_from} ~ {rec_to} ({rec_dur}{freq_kr} 간격으로 반복 노출)"
        
    server_now = get_server_time().strftime("%Y-%m-%d %H:%M:%S")

    print("\n" + "=" * 65)
    print("⚙️  [실행 전 파라미터 및 스케줄링 설정 확인]")
    print("=" * 65)
    print(f"  • 대상 서버 URL    : {APP_BASE_URL}")
    print(f"  • 대상 애플리케이션: {APP_TYPE.upper()}")
    print(f"  • 실행 환경 (ENV)   : {'OPS (Production)' if IS_OPS_ENV else 'STG (Staging)'}")
    print(f"  • 실시간 서버 시각 : {server_now} {'(KST)' if IS_OPS_ENV else '(UTC)'}")
    print(f"  • 매칭 제목 조건   : '{title_pattern}'")
    print(f"  • 적용 템플릿 파일 : {template_path}")
    print(f"  • 공지 타입 / 표시 : {noti_type} (displayType: {display_type}, format: {noti_format})")
    print(f"  • 버튼 및 활성 상태: showButton={show_button}, isActive={is_active}, reset_button={reset_button}")
    print(f"  • 스케줄링(Recurrence): {rec_info}")
    print(f"  • 업데이트 대상    : 총 {len(matched_notifications)}개 활성 알림")
    print(f"  • 실행 모드        : {'🔍 DRY-RUN (실제 요청 없음)' if args.dry_run else '🚀 실제 API 요청'}")
    print("=" * 65 + "\n")

    # 계속 진행 여부 확인
    if sys.stdin.isatty():
        try:
            choice = input(f"▶ 위 설정 파라미터로 {len(matched_notifications)}개 알림을 최신 템플릿 내용으로 업데이트하시겠습니까? (y/N): ").strip().lower()
            if choice not in ("y", "yes"):
                print("업데이트를 취소하고 종료합니다.")
                sys.exit(0)
        except KeyboardInterrupt:
            print("\n실행이 중단되었습니다.")
            sys.exit(0)
    else:
        print("▶ 비대화형 환경이므로 설정 확인 후 진행합니다.")

    # 5. 업데이트 수행
    success_count = 0
    fail_count = 0

    print(f"\n🚀 업데이트를 시작합니다... (DRY-RUN: {args.dry_run})")
    for item in matched_notifications:
        nid = item.get("id")
        targets = extract_target_keys(item)
        
        target_key = targets[0] if targets else None
        if not target_key:
            title = item.get("title", "")
            if "(" in title and ")" in title:
                target_key = title.split("(")[-1].split(")")[0].strip()

        if not target_key:
            print(f"  ❌ ID={nid} | 제목 '{item.get('title')}'에서 프로젝트/스페이스 키를 추출할 수 없어 건너뜁니다.")
            fail_count += 1
            continue

        payload = build_notification_payload(template_row, target_key, IS_CONFLUENCE, existing_item=item)
        
        errors = validate_payload(payload)
        if errors:
            print(f"  ❌ ID={nid} | [{target_key}] 페이로드 검증 실패: {errors}")
            fail_count += 1
            continue

        reset_button_flag = payload.pop("reset_button", None)

        if update_notification_by_id(nid, payload, reset_button_flag=reset_button_flag, dry_run=args.dry_run):
            success_count += 1
        else:
            fail_count += 1

    print("\n" + "=" * 60)
    print(f"🎉 완료. 총 {len(matched_notifications)}개 중 {success_count}개 성공, {fail_count}개 실패.")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
