import os
import sys

# Windows 환경에서 Unix 전용 pwd 모듈 임포트 에러 방지를 위한 Mocking
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

# 현재 스크립트 디렉토리 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import argparse
from dotenv import load_dotenv

env_path = os.path.abspath(os.path.join(script_dir, "..", "env"))
load_dotenv(dotenv_path=env_path)

# CLI 환경 인자 파싱
temp_parser = argparse.ArgumentParser(add_help=False)
temp_parser.add_argument("--env", choices=["stg", "ops"], help="대상 소스 환경 선택 (stg 또는 ops)")
temp_args, _ = temp_parser.parse_known_args()

selected_env = temp_args.env
if not selected_env and any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    selected_env = "stg"

if not selected_env:
    print("=== 대상 Jira 환경 선택 ===")
    print("1. STG (Stage 환경)")
    print("2. OPS (운영/Production 환경)")
    choice = input("환경을 선택하세요 (1 또는 2, 기본값: 1): ").strip()
    selected_env = "ops" if choice == "2" else "stg"


os.environ["COMPONENT_WATCHER_SOURCE_ENV"] = selected_env.upper()
os.environ["COMPONENT_WATCHER_ENV"] = selected_env.upper()
if not os.getenv("COMPONENT_WATCHER_TARGET_ENV"):
    os.environ["COMPONENT_WATCHER_TARGET_ENV"] = "SANDBOX"

import migrate


def main():
    parser = argparse.ArgumentParser(
        description="Jira DC 지정된 프로젝트 대상 Component Watcher 설정 스캔/조회 도구 (안전 모드 - 실제 변경 없음)"
    )
    default_projects_file = os.path.abspath(os.path.join(script_dir, "..", "projects.txt"))
    parser.add_argument(
        "--file", default=default_projects_file, help="대상 프로젝트 키 목록 파일 경로 (기본값: ../projects.txt)"
    )
    parser.add_argument("-y", "--yes", action="store_true", help="스캔 동의 프롬프트 없이 즉시 진행")
    parser.add_argument("--env", choices=["stg", "ops"], help="대상 환경 선택 (stg 또는 ops)")
    args = parser.parse_args()

    # 1. 대상 프로젝트 파일 확인 및 대화형 선택
    file_path = args.file
    if sys.stdin.isatty() and not any(arg.startswith("--file") for arg in sys.argv[1:]):
        print(f"\n대상 프로젝트 파일 경로 (기본값: {default_projects_file})")
        try:
            custom_file = input("파일 경로를 입력하세요 (엔터 시 기본값 사용): ").strip()
            if custom_file:
                file_path = custom_file
        except (KeyboardInterrupt, EOFError):
            pass

    if not os.path.isabs(file_path) and not os.path.exists(file_path):
        resolved_path = os.path.join(script_dir, file_path)
        if os.path.exists(resolved_path):
            file_path = resolved_path

    if not os.path.exists(file_path):
        print(f"오류: 대상 프로젝트 파일 '{file_path}'이 존재하지 않습니다.")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        projects = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    total_projects = len(projects)
    print(f"'{file_path}' 파일에서 총 {total_projects}개의 프로젝트를 로드했습니다.")

    # 2. 동의 여부 프롬프트 (안전한 스캔 전용)
    if not args.yes:
        print("\n=== Jira Component Watcher 프로젝트 안전 스캔 ===")
        print("이 스크립트는 Jira DC에서 설정만 조회하는 '스캔(조회)' 기능만 수행하며, Jira Cloud에 데이터를 전혀 전송하거나 생성하지 않습니다.")
        try:
            confirm = input(f"총 {total_projects}개 프로젝트의 컴포넌트 설정을 조회(스캔)하시겠습니까? (Y/N, 기본값: Y): ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            confirm = "N"

        if confirm and confirm != "Y":
            print("취소되었습니다.")
            sys.exit(0)


    # 3. migrate 엔진의 공통 run_scan 호출 (중복 제거)
    with open(migrate.LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== Scan Log Started at {migrate.datetime.now()} ===\n")

    migrate.run_scan(projects)


if __name__ == "__main__":
    main()
