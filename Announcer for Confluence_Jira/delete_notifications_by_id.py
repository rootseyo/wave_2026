#!/usr/bin/env python
"""
Jira / Confluence Announcer - ID 범위 기반 알림 삭제 스크립트

사용법:
    # 대화형으로 ID 범위를 입력받아 삭제 진행
    python delete_notifications_by_id.py

    # 특정 환경 및 ID 범위를 지정하여 삭제 진행 (예: STG의 272~349 ID 삭제)
    python delete_notifications_by_id.py 272~349 --env stg --app-type jira

    # 드라이런 (실제 삭제하지 않고 대상만 확인)
    python delete_notifications_by_id.py 272~349 --dry-run
"""

import argparse
import base64
import os
import sys
import requests
from pathlib import Path

# ──────────────────────────────────────────────
# 설정 로드 및 환경 선택 (announcer_by_projects.py와 동일하게 구현)
# ──────────────────────────────────────────────
def load_env_file(filepath="env"):
    possible_paths = [filepath, f"../{filepath}"]
    path = None
    for p in possible_paths:
        if Path(p).exists():
            path = Path(p)
            break
            
    if path and path.exists():
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

def select_environment(env_arg=None):
    selected_env = env_arg.strip().lower() if env_arg else None
    
    # sys.argv에서 --env 옵션 확인 (인자로 전달되지 않은 경우)
    if not selected_env:
        for i, arg in enumerate(sys.argv):
            if arg == "--env" and i + 1 < len(sys.argv):
                selected_env = sys.argv[i + 1].strip().lower()
            elif arg.startswith("--env="):
                selected_env = arg.split("=", 1)[1].strip().lower()

    stg_jira = os.getenv("STG_JIRA_URL")
    stg_conf = os.getenv("STG_CONF_URL")
    ops_jira = os.getenv("OPS_JIRA_URL")
    ops_conf = os.getenv("OPS_CONF_URL")

    if not any([stg_jira, stg_conf, ops_jira, ops_conf]):
        return ""

    if selected_env in ("stg", "ops"):
        env_prefix = "STG_" if selected_env == "stg" else "OPS_"
    else:
        print("──────────────────────────────────────────────")
        print("실행할 환경을 선택해주세요:")
        print(f"1) STG (Staging)     - Jira: {stg_jira or '설정 없음'}, Confluence: {stg_conf or '설정 없음'}")
        print(f"2) OPS (Production)  - Jira: {ops_jira or '설정 없음'}, Confluence: {ops_conf or '설정 없음'}")
        print("──────────────────────────────────────────────")
        
        if not sys.stdin.isatty():
            print("비대화형 환경이므로 기본값으로 STG를 선택합니다.")
            env_prefix = "STG_"
        else:
            while True:
                try:
                    choice = input("선택 (1 또는 2): ").strip()
                    if choice == "1":
                        env_prefix = "STG_"
                        break
                    elif choice == "2":
                        env_prefix = "OPS_"
                        break
                    else:
                        print("올바른 선택이 아닙니다. 1 또는 2를 입력해주세요.")
                except KeyboardInterrupt:
                    print("\n실행이 중단되었습니다.")
                    sys.exit(0)

    # 선택된 환경의 환경변수 복사
    for key in [
        "JIRA_URL", "JIRA_USERNAME", "JIRA_PASSWORD", "JIRA_COOKIE",
        "CONF_URL", "CONF_USERNAME", "CONF_PASSWORD", "CONF_COOKIE",
        "APP_TYPE"
    ]:
        val = os.getenv(f"{env_prefix}{key}")
        if val is not None:
            os.environ[key] = val

    print(f"▶ {env_prefix[:-1]} 환경이 선택되었습니다.")
    return env_prefix[:-1]

def get_target_app_config(app_type_arg=None):
    selected_app = app_type_arg.strip().lower() if app_type_arg else None
    
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
            default_app = os.getenv("APP_TYPE") or "jira"
            selected_app = default_app.lower()

    # URL 및 계정 설정 결정
    if selected_app == "confluence":
        base_url = os.getenv("CONF_URL")
        username = os.getenv("CONF_USERNAME")
        password = os.getenv("CONF_PASSWORD")
        cookie   = os.getenv("CONF_COOKIE")
    else:
        base_url = os.getenv("JIRA_URL")
        username = os.getenv("JIRA_USERNAME")
        password = os.getenv("JIRA_PASSWORD")
        cookie   = os.getenv("JIRA_COOKIE")

    return selected_app, base_url, username, password, cookie

# ──────────────────────────────────────────────
# ID 범위 파싱 로직
# ──────────────────────────────────────────────
def parse_id_range(range_str: str) -> list:
    range_str = range_str.strip()
    if not range_str:
        return []
    
    # 272~349 또는 272-349 형태 파싱
    for sep in ("~", "-"):
        if sep in range_str:
            parts = range_str.split(sep)
            if len(parts) == 2:
                try:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                    if start <= end:
                        return list(range(start, end + 1))
                    else:
                        return list(range(end, start + 1))
                except ValueError:
                    pass
    
    # 쉼표 구분자 형태 파싱 (예: 272,273,274)
    if "," in range_str:
        try:
            return [int(x.strip()) for x in range_str.split(",") if x.strip()]
        except ValueError:
            pass

    # 단일 ID 형태 파싱
    try:
        return [int(range_str)]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"올바르지 않은 ID 형식입니다: '{range_str}'\n"
            f"지원 형식 예: 272~349 (범위), 272,274 (목록), 272 (단일)"
        )

# ──────────────────────────────────────────────
# API 처리 함수
# ──────────────────────────────────────────────
def build_headers(username, password, cookie):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    h = {
        "Content-Type":  "application/json",
        "Authorization": f"Basic {token}",
    }
    if cookie:
        h["Cookie"] = cookie
    return h

def get_all_notifications(api_base, headers):
    print("\n[RETRIEVE] 서버에 등록된 전체 알림 목록을 가져오는 중...")
    try:
        resp = requests.get(api_base, headers=headers)
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
        resp = requests.delete(url, headers=headers)
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
        description="Announcer - ID 범위 지정 Jira/Confluence 알림 삭제 도구"
    )
    parser.add_argument("range", nargs="?", help="삭제할 ID 범위 (예: 272~349, 272-349, 272)")
    parser.add_argument("--env", choices=["stg", "ops"], help="실행 환경 지정 (stg | ops)")
    parser.add_argument("--app-type", choices=["jira", "confluence"], help="앱 타입 지정 (jira | confluence)")
    parser.add_argument("--dry-run", action="store_true", help="실제 삭제를 진행하지 않고 대상 목록만 출력")
    parser.add_argument("-y", "--yes", action="store_true", help="삭제 확인 프롬프트를 건너뛰고 즉시 삭제")
    args = parser.parse_args()

    load_env_file()
    
    # 1. ID 범위 입력 및 파싱
    id_range_str = args.range
    if not id_range_str:
        if sys.stdin.isatty():
            try:
                start_str = input("시작번호: ").strip()
                end_str = input("끝번호: ").strip()
                if not start_str or not end_str:
                    print("❌ 오류: 시작번호와 끝번호를 모두 입력해야 합니다.")
                    sys.exit(1)
                
                start_id = int(start_str)
                end_id = int(end_str)
                
                if start_id == end_id:
                    id_list = [start_id]
                    id_range_str = str(start_id)
                else:
                    if start_id <= end_id:
                        id_list = list(range(start_id, end_id + 1))
                        id_range_str = f"{start_id}~{end_id}"
                    else:
                        id_list = list(range(end_id, start_id + 1))
                        id_range_str = f"{end_id}~{start_id}"
            except ValueError:
                print("❌ 오류: 시작번호와 끝번호는 정수여야 합니다.")
                sys.exit(1)
            except KeyboardInterrupt:
                print("\n❌ 실행이 중단되었습니다.")
                sys.exit(0)
        else:
            print("❌ 오류: 비대화형 환경이므로 ID 범위를 인자로 제공해야 합니다.")
            sys.exit(1)
    else:
        try:
            id_list = parse_id_range(id_range_str)
        except Exception as e:
            print(f"❌ {e}")
            sys.exit(1)

    if not id_list:
        print("❌ 유효한 ID가 지정되지 않았습니다.")
        sys.exit(1)

    # 2. 환경 및 앱 타입 선택
    env_name = select_environment(args.env)
    app_type, base_url, username, password, cookie = get_target_app_config(args.app_type)

    if not base_url or not username or not password:
        print("❌ 필수 환경변수 설정(URL, Username, Password)이 누락되었습니다. env 파일을 확인하세요.")
        sys.exit(1)

    api_base = f"{base_url}/rest/announcer/1.0/admin/notifications"
    headers = build_headers(username, password, cookie)

    print("=" * 60)
    print(f"  Announcer - ID 범위 지정 알림 삭제")
    print("=" * 60)
    print(f"  서버 주소 : {base_url}")
    print(f"  앱 타입   : {app_type.upper()}")
    print(f"  계정 정보 : {username}")
    print(f"  요청 범위 : {id_range_str} (총 {len(id_list)}개 번호)")
    print(f"  DRY-RUN   : {'예 (삭제되지 않음)' if args.dry_run else '아니오 (실제 삭제 진행)'}")
    print("=" * 60)

    # 3. 전체 알림 조회 및 범위 필터링
    all_notifications = get_all_notifications(api_base, headers)
    
    id_set = set(id_list)
    to_delete = []
    
    for noti in all_notifications:
        try:
            nid = int(noti.get("id"))
            if nid in id_set:
                to_delete.append(noti)
        except (ValueError, TypeError):
            continue

    found_ids = {int(n.get("id")) for n in to_delete}
    not_found = [nid for nid in id_list if nid not in found_ids]

    if not to_delete:
        print(f"\n✨ 입력하신 범위 ({id_range_str})에 해당하여 서버에서 찾은 알림이 없습니다.")
        if not_found:
            # 5개까지만 축약 출력
            not_found_subset = not_found[:5]
            suffix = "..." if len(not_found) > 5 else ""
            print(f"  (대상 ID 중 서버에 존재하지 않는 ID 예시: {not_found_subset}{suffix})")
        return

    print(f"\n📢 범위 내 서버에 존재하는 삭제 대상 알림 {len(to_delete)}개를 조회했습니다:")
    for idx, noti in enumerate(to_delete, 1):
        nid = noti.get("id")
        title = noti.get("title", "제목 없음")
        is_active = "활성" if noti.get("isActive") else "비활성"
        print(f"  [{idx}] ID: {nid} | 상태: {is_active} | 제목: {title}")

    if not_found:
        print(f"  ℹ️  서버에 존재하지 않는 ID ({len(not_found)}개)는 건너뜁니다.")

    # 4. 삭제 확인 프롬프트
    if not args.dry_run and not args.yes:
        print("\n" + "!" * 60)
        print("  ⚠️ 주의: 위 목록의 알림이 복구 불가능하게 영구 삭제됩니다.")
        print("!" * 60)
        
        if sys.stdin.isatty():
            try:
                confirm = input("\n정말로 위 알림들을 삭제하시겠습니까? (yes 입력 시 진행): ").strip()
                if confirm != "yes":
                    print("❌ 삭제 작업이 취소되었습니다.")
                    sys.exit(0)
            except KeyboardInterrupt:
                print("\n❌ 작업이 취소되었습니다.")
                sys.exit(0)
        else:
            print("❌ 비대화형 실행 모드이므로 동의를 구할 수 없어 종료합니다. 즉시 삭제하려면 -y 또는 --yes 옵션을 제공하세요.")
            sys.exit(1)

    # 5. 삭제 작업 진행
    print(f"\n🚀 삭제 처리를 시작합니다... (DRY-RUN: {args.dry_run})")
    success_count = 0
    fail_count = 0

    for noti in to_delete:
        nid = noti.get("id")
        title = noti.get("title", "제목 없음")
        if delete_notification(api_base, headers, nid, title, dry_run=args.dry_run):
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
