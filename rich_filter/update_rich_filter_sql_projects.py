#!/usr/bin/env python3
"""Confluence 프로젝트 목록으로 rich-filter SQL의 프로젝트 키를 갱신한다."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_DOMAIN = "cloud-osci.atlassian.net"
DEFAULT_PAGE_ID = "24497258510"
DEFAULT_SQL_FILES = (
    "rich_filter.sql",
    "rich_filter_jira_filters.sql",
)

# 두 SQL 파일 모두 프로젝트 대체 목록은 AAMSW부터 YSEC까지 한 줄에 있다.
PROJECT_ALTERNATION = re.compile(r"AAMSW\|[^']*?\|YSEC")
PROJECT_KEY = re.compile(r"^[A-Z0-9_-]+$")


def fetch_project_keys(domain: str, page_id: str, email: str, api_token: str) -> list[str]:
    """Confluence 페이지 첫 번째 표의 3번째 열에서 프로젝트 키를 추출한다."""
    credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    url = f"https://{domain}/wiki/api/v2/pages/{page_id}?body-format=storage"
    request = Request(
        url,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
    )

    with urlopen(request) as response:
        data = json.loads(response.read().decode("utf-8"))

    body = data.get("body", {}).get("storage", {}).get("value", "")
    first_table = re.split(r"</table\s*>", body, maxsplit=1)[0]
    keys: list[str] = []

    for row in re.findall(r"<tr.*?>(.*?)</tr>", first_table, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<td.*?>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if len(cells) < 3:
            continue

        clean_cells = [re.sub(r"<[^>]+>", "", cell).strip() for cell in cells]
        project_key = clean_cells[2]
        if PROJECT_KEY.fullmatch(project_key) and project_key not in keys:
            keys.append(project_key)

    return keys


def read_project_keys(path: Path) -> list[str]:
    """로컬 projects.txt를 읽고 중복/빈 줄을 제거한다."""
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key = line.strip()
        if key and PROJECT_KEY.fullmatch(key) and key not in keys:
            keys.append(key)
    return keys


def update_sql_file(path: Path, project_keys: list[str], write: bool) -> bool:
    """SQL 내 프로젝트 대체 목록 2개를 모두 교체한다."""
    content = path.read_text(encoding="utf-8")
    replacement = "|".join(project_keys)
    updated, count = PROJECT_ALTERNATION.subn(
        lambda match: replacement,
        content,
    )

    if count != 2:
        raise ValueError(f"{path}: 프로젝트 목록을 2개 찾지 못했습니다 (found: {count})")

    changed = updated != content
    if changed and write:
        path.write_text(updated, encoding="utf-8")
    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default=os.getenv("CONFLUENCE_DOMAIN", DEFAULT_DOMAIN))
    parser.add_argument("--page-id", default=os.getenv("CONFLUENCE_PAGE_ID", DEFAULT_PAGE_ID))
    parser.add_argument(
        "--projects-file",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "projects.txt",
        help="프로젝트 키 입력/출력 파일 (기본값: ../projects.txt)",
    )
    parser.add_argument(
        "--sql",
        type=Path,
        nargs="+",
        default=[Path(__file__).resolve().parent / name for name in DEFAULT_SQL_FILES],
        help="갱신할 SQL 파일 목록",
    )
    parser.add_argument(
        "--from-local",
        action="store_true",
        help="Confluence를 호출하지 않고 projects.txt의 목록을 사용",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="변경 예정 파일만 출력하고 파일은 수정하지 않음",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.from_local:
        project_keys = read_project_keys(args.projects_file)
    else:
        email = os.getenv("JIRA_EMAIL")
        api_token = os.getenv("JIRA_API_TOKEN")
        if not email or not api_token:
            print("JIRA_EMAIL과 JIRA_API_TOKEN 환경변수가 필요합니다.", file=sys.stderr)
            print("인증 없이 실행하려면 --from-local 옵션을 사용하세요.", file=sys.stderr)
            return 1
        try:
            project_keys = fetch_project_keys(args.domain, args.page_id, email, api_token)
        except Exception as error:
            print(f"Confluence 프로젝트 목록 가져오기 실패: {error}", file=sys.stderr)
            return 1

    if not project_keys:
        print("추출된 프로젝트 키가 없습니다.", file=sys.stderr)
        return 1

    print(f"프로젝트 키 {len(project_keys)}개를 사용합니다.")
    if not args.dry_run and not args.from_local:
        args.projects_file.write_text("\n".join(project_keys) + "\n", encoding="utf-8")
        print(f"프로젝트 목록 갱신: {args.projects_file}")

    try:
        for sql_file in args.sql:
            changed = update_sql_file(sql_file, project_keys, write=not args.dry_run)
            status = "변경" if changed else "변경 없음"
            print(f"{status}: {sql_file}")
    except (OSError, ValueError) as error:
        print(f"SQL 갱신 실패: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
