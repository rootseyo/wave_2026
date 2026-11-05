#!/usr/bin/env python
"""
Jira / Confluence Announcer - 전체 알림 삭제 스크립트

사용법:
    # 대화형으로 환경과 앱 타입을 선택하며 전체 삭제 진행
    python delete_all_notifications.py

    # 특정 환경/앱 타입을 지정하여 전체 삭제 진행
    python delete_all_notifications.py --env stg --app-type jira

    # 드라이런 (실제 삭제하지 않고 대상만 확인)
    python delete_all_notifications.py --dry-run
"""

import argparse
import base64
import os
import sys
import time
import requests
from pathlib import Path

# 전역 설정 변수 선언
APP_TYPE = None
IS_CONFLUENCE = False
APP_BASE_URL = ""
APP_USERNAME = ""
APP_PASSWORD = ""
APP_TOKEN = ""
APP_COOKIE = ""
API_BASE = ""

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

def select_app_type(app_type_arg=None):
    global APP_TYPE, IS_CONFLUENCE
    
    selected_app = app_type_arg
    if not selected_app:
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
            selected_app = (os.getenv("APP_TYPE") or "jira").lower()

    APP_TYPE = selected_app
    IS_CONFLUENCE = (APP_TYPE == "confluence")
    print(f"▶ 대상 애플리케이션: {APP_TYPE.upper()}")

def select_environment(env_arg=None):
    global APP_BASE_URL, APP_USERNAME, APP_PASSWORD, APP_TOKEN, APP_COOKIE, API_BASE
    
    selected_env = env_arg
    if not selected_env:
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
        stg_password = os.getenv("CONF_DC_STG_PASSWORD") or os.getenv("CONF_CONF_PASSWORD")
        stg_cookie = os.getenv("CONF_DC_STG_COOKIE") or os.getenv("STG_CONF_COOKIE")

        ops_url = os.getenv("CONF_DC_OPS_URL") or os.getenv("CONF_OPS_URL") or os.getenv("OPS_CONF_URL")
        ops_token = os.getenv("CONF_DC_OPS_TOKEN") or os.getenv("CONF_DC_OPS_API_TOKEN")
        ops_user = os.getenv("CONF_DC_OPS_USERNAME") or os.getenv("OPS_CONF_USERNAME")
        ops_password = os.getenv("CONF_DC_OPS_PASSWORD") or os.getenv("CONF_CONF_PASSWORD")
        ops_cookie = os.getenv("CONF_DC_OPS_COOKIE") or os.getenv("OPS_CONF_COOKIE")

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
    else:
        APP_BASE_URL = ops_url
        APP_TOKEN = ops_token
        APP_USERNAME = ops_user
        APP_PASSWORD = ops_password
        APP_COOKIE = ops_cookie

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
    return choice_env.lower()

# ──────────────────────────────────────────────
# API 처리 함수
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

def get_all_notifications(api_base, headers):
    print("\n[RETRIEVE] 서버에 등록된 전체 알림 목록을 가져오는 중...")
    try:
        resp = request_with_retry("GET", api_base, headers=headers)
        if resp.ok:
            return resp.json()
        else:
            print(f"  ❌ 목록 조회 실패: {resp.status_code} - {resp.text}")
            sys.exit(1)
    except Exception as e:
        print(f"  ❌ 목록 조회 실패 (네트워크 에러): {e}")
        sys.exit(1)

def delete_notification(api_base, headers, notification_id, title, dry_run=False):
    url = f"{api_base}/{notification_id}"
    if dry_run:
        print(f"  🔍 [DRY-RUN] DELETE {url} (제목: {title})")
        return True

    try:
        resp = request_with_retry("DELETE", url, headers=headers)
        if resp.ok:
            print(f"  ✅ 삭제 성공: ID={notification_id} (제목: {title})")
            return True
        else:
            print(f"  ❌ 삭제 실패: ID={notification_id} - 상태코드: {resp.status_code}, 응답: {resp.text}")
            return False
    except Exception as e:
        print(f"  ❌ 삭제 실패 (네트워크 에러): ID={notification_id} - {e}")
        return False

# ──────────────────────────────────────────────
# 메인 실행 함수
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Announcer - Jira/Confluence 서버의 알림 삭제 도구"
    )
    parser.add_argument("--env", choices=["stg", "ops"], help="실행 환경 지정 (stg | ops)")
    parser.add_argument("--app-type", choices=["jira", "confluence"], help="앱 타입 지정 (jira | confluence)")
    parser.add_argument("--query", help="삭제할 알림 제목의 검색어 패턴")
    parser.add_argument("--dry-run", action="store_true", help="실제 삭제를 진행하지 않고 대상 목록만 출력")
    parser.add_argument("-y", "--yes", action="store_true", help="삭제 확인 확인 대화창을 건너뛰고 즉시 삭제")
    args = parser.parse_args()

    load_env_file()
    
    # 1. 앱 타입 및 환경 선택
    select_app_type(args.app_type)
    select_environment(args.env)

    print("=" * 60)
    print(f"  Announcer - 알림 검색 및 삭제")
    print("=" * 60)
    print(f"  서버 주소 : {APP_BASE_URL}")
    print(f"  앱 타입   : {APP_TYPE.upper()}")
    if APP_USERNAME:
        print(f"  계정 정보 : {APP_USERNAME}")
    print(f"  DRY-RUN   : {'예 (삭제되지 않음)' if args.dry_run else '아니오 (실제 삭제 진행)'}")
    print("=" * 60)

    # 2. 전체 알림 목록 조회
    all_notifications = get_all_notifications(API_BASE, build_headers())
    if not all_notifications:
        print("✨ 서버에 등록된 알림이 없습니다. 삭제할 대상이 없습니다.")
        return

    # 2-1. 삭제 모드(검색/필터 삭제 vs 중복 키 삭제) 결정
    mode = "search"
    if sys.stdin.isatty() and not args.yes:
        print("\n──────────────────────────────────────────────")
        print("수행할 알림 관리 모드를 선택해주세요:")
        print("1) 제목 패턴 검색으로 삭제 (기존 기능)")
        print("2) 활성(Active) 알림 중 중복 키(프로젝트/스페이스) 제거")
        print("──────────────────────────────────────────────")
        try:
            choice = input("선택 (1 또는 2, 기본값 1): ").strip()
            if choice == "2":
                mode = "duplicate"
        except KeyboardInterrupt:
            print("\n❌ 실행이 중단되었습니다.")
            sys.exit(0)

    targets_to_delete = []

    if mode == "search":
        # 3. 검색 질의어(Query) 결정
        query = args.query
        if query is None and sys.stdin.isatty():
            print("──────────────────────────────────────────────")
            query_input = input("검색할 알림 제목 패턴 (빈 값 입력 시 전체 목록 조회): ").strip()
            query = query_input if query_input else None
            print("──────────────────────────────────────────────")

        # 필터링 진행
        if query:
            filtered_notifications = [n for n in all_notifications if query.lower() in n.get("title", "").lower()]
            print(f"\n🔍 제목에 '{query}'가 포함된 알림 {len(filtered_notifications)}개를 찾았습니다.")
        else:
            filtered_notifications = all_notifications
            print(f"\n📢 총 {len(filtered_notifications)}개의 알림이 조회되었습니다.")

        if not filtered_notifications:
            print("💡 조건에 부합하는 알림이 없습니다. 종료합니다.")
            return

        # 필터링 결과 목록 출력
        print("\n📋 [알림 목록]")
        for idx, noti in enumerate(filtered_notifications, 1):
            nid = noti.get("id")
            title = noti.get("title", "제목 없음")
            is_active = "활성" if noti.get("isActive") else "비활성"
            print(f"  [{idx}] ID: {nid:<5} | 상태: {is_active:<4} | 제목: {title}")

        # 4. 삭제 대상 및 방식 선택
        if not args.dry_run and not args.yes:
            if sys.stdin.isatty():
                try:
                    print("\n──────────────────────────────────────────────")
                    print("수행할 삭제 범위를 선택해주세요:")
                    print("  - 'all' 입력      : 필터링된 알림 전체 삭제")
                    print("  - 숫자 입력       : 특정 번호 개별 삭제 (예: 1, 3, 5)")
                    print("  - 범위 입력       : 특정 구간 삭제 (예: 1-3, 5-8)")
                    print("  - 'q' 또는 Enter  : 취소 및 종료")
                    print("──────────────────────────────────────────────")
                    selection = input("선택: ").strip().lower()

                    if not selection or selection == 'q':
                        print("❌ 삭제 작업이 취소되었습니다.")
                        sys.exit(0)
                    elif selection == 'all':
                        targets_to_delete = filtered_notifications
                    else:
                        # 번호 분석 및 파싱
                        selected_indices = []
                        parts = selection.replace(" ", "").split(",")
                        for part in parts:
                            if "~" in part:
                                subparts = part.split("~")
                                if len(subparts) == 2:
                                    start, end = int(subparts[0]), int(subparts[1])
                                    selected_indices.extend(range(min(start, end), max(start, end) + 1))
                            elif "-" in part:
                                subparts = part.split("-")
                                if len(subparts) == 2:
                                    start, end = int(subparts[0]), int(subparts[1])
                                    selected_indices.extend(range(min(start, end), max(start, end) + 1))
                            else:
                                selected_indices.append(int(part))
                        
                        for index in selected_indices:
                            if 1 <= index <= len(filtered_notifications):
                                targets_to_delete.append(filtered_notifications[index - 1])
                            else:
                                print(f"  ⚠️ 리스트 범위를 벗어난 번호 [{index}]는 무시합니다.")
                except ValueError:
                    print("❌ 오류: 유효한 형식(숫자 또는 범위)으로 입력해 주세요.")
                    sys.exit(1)
                except KeyboardInterrupt:
                    print("\n❌ 작업이 취소되었습니다.")
                    sys.exit(0)
            else:
                print("❌ 비대화형 실행 모드이므로 동의를 구할 수 없어 종료합니다. 즉시 삭제하려면 -y 옵션을 제공하세요.")
                sys.exit(1)
        else:
            # dry-run이거나 -y(강제 삭제)일 때는 조회된 리스트 전체를 대상 목록으로 선택
            targets_to_delete = filtered_notifications
    else:
        # 중복 키 삭제 모드
        # active 된 알림들만 대상
        active_notifications = []
        for item in all_notifications:
            is_active = item.get("isActive")
            if is_active is None:
                is_active = item.get("is_active")
            if is_active is None:
                is_active = item.get("active")
            is_active_bool = (is_active is True or str(is_active).strip().lower() == "true")
            if is_active_bool:
                active_notifications.append(item)

        if not active_notifications:
            print("💡 활성화(Active) 상태의 알림이 없습니다. 종료합니다.")
            return

        # 타겟 프로젝트/스페이스 키 추출 및 매핑
        key_map = {}
        for item in active_notifications:
            targets = []
            for target_field in ["targetProject", "target_project", "targetSpace", "target_space", "targetProjects", "targetSpaces"]:
                val = item.get(target_field)
                if val:
                    if isinstance(val, list):
                        targets.extend([str(v).strip() for v in val if v])
                    elif isinstance(val, str):
                        clean = val.strip("[]").replace("'", "").replace('"', "")
                        targets.extend([t.strip() for t in clean.split(",") if t.strip()])
            
            # 타겟 정보가 없을 때 제목 괄호 백업 추출
            target_key = None
            if targets:
                target_key = list(set(targets))[0]
            else:
                title = item.get("title", "")
                if "(" in title and ")" in title:
                    target_key = title.split("(")[-1].split(")")[0].strip()

            if not target_key:
                target_key = "UNKNOWN"

            if target_key not in key_map:
                key_map[target_key] = []
            key_map[target_key].append(item)

        # 중복된 키들만 색출
        dup_keys = {k: v for k, v in key_map.items() if len(v) > 1 and k != "UNKNOWN"}

        if not dup_keys:
            print("✨ 중복된 프로젝트/스페이스 키를 가진 활성 알림이 없습니다. 종료합니다.")
            return

        print(f"\n📊 중복 기재가 발견된 활성 프로젝트/스페이스 키: {len(dup_keys)}개")
        for key_name, items in dup_keys.items():
            print(f"\n[🔑 키: {key_name}] (총 {len(items)}개 알림)")
            for item in items:
                print(f"  - ID: {item.get('id'):<5} | 제목: {item.get('title')}")

        print("\n──────────────────────────────────────────────")
        print("중복된 알림 중 어떤 ID를 남겨두시겠습니까?")
        print("1) 가장 높은 ID를 남기기 (최신 ID 유지)")
        print("2) 가장 낮은 ID를 남기기 (이전 ID 유지)")
        print("q) 취소 및 종료")
        print("──────────────────────────────────────────────")
        try:
            keep_choice = input("선택 (1 또는 2): ").strip()
        except KeyboardInterrupt:
            print("\n❌ 작업이 취소되었습니다.")
            sys.exit(0)

        if keep_choice not in ("1", "2"):
            print("❌ 작업이 취소되었습니다.")
            sys.exit(0)

        for key_name, items in dup_keys.items():
            # ID 기준 정렬
            try:
                sorted_items = sorted(items, key=lambda x: int(x.get("id")))
            except Exception:
                sorted_items = sorted(items, key=lambda x: str(x.get("id")))

            if keep_choice == "1":
                # 가장 높은 ID(마지막)를 남기고 나머지 삭제 대상
                keep_item = sorted_items[-1]
                to_delete = sorted_items[:-1]
            else:
                # 가장 낮은 ID(첫번째)를 남기고 나머지 삭제 대상
                keep_item = sorted_items[0]
                to_delete = sorted_items[1:]

            targets_to_delete.extend(to_delete)
            print(f"  ✓ {key_name} ➔ ID {keep_item.get('id')} 보존 | 삭제 예정 ID: {[x.get('id') for x in to_delete]}")

    if not targets_to_delete:
        print("💡 선택된 삭제 대상 알림이 없습니다. 종료합니다.")
        return

    # 최종 삭제 목록 출력 및 2차 확인
    print("\n📋 [최종 삭제 대상 알림]")
    for idx, noti in enumerate(targets_to_delete, 1):
        print(f"  - ID: {noti.get('id'):<5} | 제목: {noti.get('title')}")

    if not args.dry_run and not args.yes and sys.stdin.isatty():
        try:
            confirm = input(f"\n⚠️ 정말로 위 {len(targets_to_delete)}개의 알림들을 영구 삭제하시겠습니까? (yes 입력 시 진행): ").strip()
            if confirm != "yes":
                print("❌ 삭제 작업이 취소되었습니다.")
                sys.exit(0)
        except KeyboardInterrupt:
            print("\n❌ 작업이 취소되었습니다.")
            sys.exit(0)

    # 5. 삭제 작업 진행
    print(f"\n🚀 삭제 처리를 시작합니다... (DRY-RUN: {args.dry_run})")
    success_count = 0
    fail_count = 0

    for noti in targets_to_delete:
        nid = noti.get("id")
        title = noti.get("title", "제목 없음")
        if delete_notification(API_BASE, build_headers(), nid, title, dry_run=args.dry_run):
            success_count += 1
        else:
            fail_count += 1

    print("\n" + "=" * 60)
    print(f"  삭제 작업 결과 ({'DRY-RUN' if args.dry_run else '실제 수행'})")
    print("=" * 60)
    print(f"  성공: {success_count}개")
    print(f"  실패: {fail_count}개")
    print("=" * 60)

if __name__ == "__main__":
    main()
