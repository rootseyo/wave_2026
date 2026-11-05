#!/usr/bin/env python3
"""Update project filters in the JEditor MySQL query files.

Project keys are read from ../projects.txt, one key per line. Blank lines and
lines beginning with '#' are ignored.
"""

from pathlib import Path
import argparse
import re


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECTS_PATH = SCRIPT_DIR.parent / "projects.txt"
DEFAULT_SQL_PATHS = (
    SCRIPT_DIR / "jeditor_mysql_queries.sql",
    SCRIPT_DIR / "jeditor_template_queries.sql",
)


def load_project_keys(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"프로젝트 목록 파일이 없습니다: {path}")

    keys = []
    seen = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        key = raw_line.split("#", 1)[0].strip().upper()
        if not key:
            continue
        if not re.fullmatch(r"[A-Z0-9_-]+", key):
            raise ValueError(
                f"잘못된 프로젝트 키 형식 ({path}:{line_number}): {raw_line!r}"
            )
        if key not in seen:
            keys.append(key)
            seen.add(key)

    if not keys:
        raise ValueError(f"프로젝트 키가 없습니다: {path}")
    return keys


def build_projects_cte(keys: list[str]) -> str:
    lines = ["WITH target_projects AS ("]
    for index, key in enumerate(keys):
        suffix = " UNION ALL" if index < len(keys) - 1 else ""
        lines.append(f"    SELECT '{key}' AS pkey{suffix}")
    lines.append(")")
    return "\n".join(lines)


def build_project_literals(keys: list[str]) -> str:
    return ",\n".join(f"    '{key}'" for key in keys)


def update_sql_file(path: Path, keys: list[str]) -> bool:
    if not path.exists():
        print(f"건너뜀: SQL 파일이 없습니다: {path}")
        return False

    content = path.read_text(encoding="utf-8")
    cte = build_projects_cte(keys)

    # Replace an existing target_projects CTE, or match PROJECT_KEYS marker
    cte_pattern = re.compile(
        r"WITH\s+target_projects\s+AS\s*\(.*?\)",
        re.IGNORECASE | re.DOTALL,
    )
    if cte_pattern.search(content):
        updated = cte_pattern.sub(cte, content, count=1)
    elif "-- PROJECT_KEYS_START" in content:
        project_literals = build_project_literals(keys)
        marker_pattern = re.compile(
            r"(-- PROJECT_KEYS_START\n).*?(-- PROJECT_KEYS_END)",
            re.DOTALL,
        )
        updated, count = marker_pattern.subn(
            rf"\1{project_literals}\n\2", content
        )
        if count == 0:
            raise ValueError(f"프로젝트 키 마커를 처리할 수 없습니다: {path}")
    else:
        raise ValueError(
            f"{path.name}에 target_projects CTE 또는 PROJECT_KEYS 마커가 없습니다."
        )

    if updated == content:
        print(f"변경 없음: {path.name}")
        return False

    path.write_text(updated, encoding="utf-8")
    print(f"성공: {path.name} 프로젝트 목록을 {len(keys)}개 키로 업데이트했습니다.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projects",
        type=Path,
        default=DEFAULT_PROJECTS_PATH,
        help=f"프로젝트 키 파일 (기본값: {DEFAULT_PROJECTS_PATH})",
    )
    parser.add_argument(
        "--sql",
        type=Path,
        action="append",
        dest="sql_paths",
        help="갱신할 SQL 파일. 여러 번 지정 가능하며, 기본값은 두 JEditor SQL 파일.",
    )
    args = parser.parse_args()

    keys = load_project_keys(args.projects.resolve())
    sql_paths = args.sql_paths or list(DEFAULT_SQL_PATHS)
    for sql_path in sql_paths:
        update_sql_file(sql_path.resolve(), keys)


if __name__ == "__main__":
    main()
