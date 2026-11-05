#!/usr/bin/env python3
"""대화형 BigGantt 마이그레이션 실행기."""

import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT.parent / "env"


def ask(prompt, default=None, required=False, input_func=input):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input_func(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default not in (None, ""):
            return str(default)
        if not required:
            return ""
        print("값을 입력해 주세요.")


def choose(prompt, options, default="1", input_func=input):
    print(f"\n{prompt}")
    for key, label in options:
        print(f"  {key}. {label}")
    allowed = {key for key, _ in options}
    while True:
        value = input_func(f"선택 [{default}]: ").strip() or default
        if value in allowed:
            return value
        print("메뉴 번호를 다시 입력해 주세요.")


def confirm(prompt, default=False, input_func=input):
    hint = "Y/n" if default else "y/N"
    value = input_func(f"{prompt} ({hint}): ").strip().casefold()
    if not value:
        return default
    return value in {"y", "yes", "예", "네"}


def path_value(prompt, default, must_exist=True, input_func=input):
    while True:
        value = Path(ask(prompt, default, required=True, input_func=input_func)).expanduser()
        if not value.is_absolute():
            value = ROOT / value
        value = value.resolve()
        if not must_exist or value.exists():
            return value
        print(f"파일 또는 디렉터리를 찾을 수 없습니다: {value}")


def environment(input_func=input):
    selected = choose("Cloud 환경", [("1", "OPS"), ("2", "SANDBOX")], input_func=input_func)
    return "ops" if selected == "1" else "sandbox"


def configuration():
    if not ENV_FILE.is_file():
        raise ValueError(f"환경 파일이 없습니다: {ENV_FILE}")
    return dotenv_values(ENV_FILE)


def app_url(env_name, config, input_func=input):
    prefix = "JIRA_CLOUD_" + env_name.upper()
    site = str(config.get(prefix + "_URL") or "").rstrip("/")
    configured = (config.get(prefix + "_BIGGANTT_UI_URL")
                  or config.get(prefix + "_BIGGANTT_APP_URL") or "")
    value = ask("BigGantt Cloud 화면 URL", configured, required=True, input_func=input_func)
    site_parts, app_parts = urlsplit(site), urlsplit(value)
    if (site_parts.scheme != "https" or app_parts.scheme != "https"
            or not site_parts.hostname or site_parts.hostname != app_parts.hostname):
        raise ValueError("BigGantt 화면 URL은 선택한 Jira Cloud와 같은 HTTPS 호스트여야 합니다.")
    return value


def execute(script, arguments=None):
    command = [sys.executable, str(ROOT / script)] + list(arguments or [])
    print(f"\n실행: {script}")
    print("-" * 60)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    print("-" * 60)
    print(f"종료 코드: {completed.returncode}")
    return completed.returncode


def validate_mapping(input_func=input):
    env_name = environment(input_func)
    project = ask("Project Key", required=True, input_func=input_func).upper()
    mappings = path_value("id-mappings.html", "id-mappings.html", input_func=input_func)
    start = ask("DC Start Date Custom Field ID", required=True, input_func=input_func)
    end = ask("DC End Date Custom Field ID", required=True, input_func=input_func)
    output = path_value("Manifest 저장 경로", f"test-results/{project}.mapping.json",
                        must_exist=False, input_func=input_func)
    return execute("validate_field_mapping.py", ["--env", env_name, "--project", project,
                   "--id-mappings", str(mappings), "--dc-start-field", start,
                   "--dc-end-field", end, "--output", str(output)])


def browser_login(input_func=input):
    env_name = environment(input_func)
    config = configuration()
    prefix = "JIRA_CLOUD_" + env_name.upper()
    site = config.get(prefix + "_URL") or ""
    url = app_url(env_name, config, input_func)
    timeout = ask("로그인 대기 시간(초)", "1800", input_func=input_func)
    if not timeout.isdigit() or int(timeout) <= 0:
        raise ValueError("로그인 대기 시간은 양의 정수여야 합니다.")
    return execute("biggantt_browser_auth.py", ["--site-url", site, "--app-url", url,
                   "--login-timeout", timeout])


def field_mapping_single(input_func=input):
    action_key = choose("실행 방식", [("1", "Dry-run"), ("2", "Migrate"), ("3", "Verify")],
                        input_func=input_func)
    action = {"1": "dry-run", "2": "migrate", "3": "verify"}[action_key]
    env_name = environment(input_func)
    config = configuration()
    project = ask("Project Key", required=True, input_func=input_func).upper()
    manifest = path_value("검증된 Manifest", f"test-results/{project}.mapping.json",
                          input_func=input_func)
    box = ask("BigGantt Box ID", required=True, input_func=input_func)
    url = app_url(env_name, config, input_func)
    login = confirm("세션 만료 시 일반 Chrome을 열고 로그인 완료 입력을 기다릴까요?",
                    True, input_func)
    if action == "migrate" and not confirm("Cloud Field Mapping을 실제 저장할까요?", False, input_func):
        print("저장을 취소했습니다.")
        return 0
    arguments = [action, "--manifest", str(manifest), "--box", box,
                 "--app-url", url, "--env", env_name]
    if login:
        arguments.append("--interactive-login")
    return execute("biggantt_field_mapping.py", arguments)


def field_mapping_batch(input_func=input):
    action_key = choose("배치 실행 방식", [("1", "Dry-run"), ("2", "Migrate"), ("3", "Verify")],
                        input_func=input_func)
    action = {"1": "dry-run", "2": "migrate", "3": "verify"}[action_key]
    env_name = environment(input_func)
    config = configuration()
    batch = path_value("Batch Manifest", "batch.json", input_func=input_func)
    resume = confirm("기존 실행 결과에서 Resume할까요?", False, input_func)
    if resume:
        output = path_value("기존 결과 디렉터리", "test-results/batch", input_func=input_func)
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = path_value("새 결과 디렉터리", f"test-results/batch-{action}-{run_id}",
                            must_exist=False, input_func=input_func)
    projects = ask("특정 Project Key만 실행(쉼표 구분, 전체면 Enter)", "", input_func=input_func)
    url = app_url(env_name, config, input_func)
    login = confirm("세션 만료 시 일반 Chrome 로그인 완료를 기다릴까요?", True, input_func)
    if action == "migrate" and not confirm("선택된 프로젝트의 Cloud Mapping을 실제 저장할까요?",
                                            False, input_func):
        print("저장을 취소했습니다.")
        return 0
    arguments = [action, "--batch", str(batch), "--output-dir", str(output),
                 "--app-url", url, "--env", env_name]
    if resume:
        arguments.append("--resume")
    if projects:
        arguments += ["--projects", projects]
    if login:
        arguments.append("--interactive-login")
    return execute("biggantt_field_mapping_batch.py", arguments)


def field_mapping_menu(input_func=input):
    choice = choose("Field Mapping", [
        ("1", "id-mappings.html 검증 및 Manifest 생성"),
        ("2", "브라우저 로그인"),
        ("3", "단일 프로젝트 실행"),
        ("4", "다중 프로젝트 실행 / Resume"),
        ("0", "이전 메뉴"),
    ], input_func=input_func)
    if choice == "1":
        return validate_mapping(input_func)
    if choice == "2":
        return browser_login(input_func)
    if choice == "3":
        return field_mapping_single(input_func)
    if choice == "4":
        return field_mapping_batch(input_func)
    return 0


def box_menu(input_func=input):
    choice = choose("Box Migration", [
        ("1", "DC Box 조사 / Export"),
        ("2", "Cloud Box Import"),
        ("3", "Migration 검증 페이지 생성"),
        ("0", "이전 메뉴"),
    ], input_func=input_func)
    scripts = {"1": "step1_export_dc_boxes.py", "2": "step2_import_cloud_boxes.py",
               "3": "step3_generate_verification_page.py"}
    return execute(scripts[choice]) if choice in scripts else 0


def main(input_func=input):
    os.umask(0o077)
    while True:
        choice = choose("BigGantt Migration", [
            ("1", "Box Migration"),
            ("2", "Start/End Field Mapping"),
            ("0", "종료"),
        ], input_func=input_func)
        if choice == "0":
            print("종료합니다.")
            return 0
        try:
            if choice == "1":
                box_menu(input_func)
            else:
                field_mapping_menu(input_func)
        except (EOFError, KeyboardInterrupt):
            print("\n현재 작업을 취소하고 메뉴로 돌아갑니다.")
        except (OSError, ValueError) as exc:
            print(f"실행 준비 오류: {exc}")
        if not confirm("다른 작업을 계속할까요?", False, input_func):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
