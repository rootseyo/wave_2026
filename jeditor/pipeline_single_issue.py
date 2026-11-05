#!/usr/bin/env python3
"""Single Issue End-to-End Pipeline (Step 1 to Step 4).

Takes a single Jira issue key (e.g. AAOSFRWDEV-543) and runs:
  [Step 1] Project SQL generation (step1_update_sql_projects)
  [Step 2] Data extraction from Jira DB (step2_query_runner)
  [Step 3] Data analysis & dashboard generation (step3_analyze_data_html)
  [Step 4] Jira Cloud ADF conversion & attachment migration (step4_convert_adf)
"""

import argparse
import html
import os
from pathlib import Path
import re
import sys
import time
from typing import Optional, Set
import warnings

# Suppress terminal library warnings
warnings.filterwarnings("ignore")
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

from export_data import has_html_elements, read_export
from step1_update_sql_projects import build_projects_cte
from step2_query_runner import get_db_config, pymysql, write_html_header, write_html_row
from step3_analyze_data_html import (
    generate_html_dashboard,
    generate_text_report,
    parse_html_table_stream,
)
import convert_adf


ISSUE_KEY_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)-(\d+)$")
DEFAULT_SQL_TEMPLATE = Path("jeditor_mysql_queries.sql")


def parse_issue_key(issue_key: str) -> tuple[str, int]:
    """Validates issue key format and extracts project key and issue number."""
    cleaned = issue_key.strip().upper()
    match = ISSUE_KEY_PATTERN.match(cleaned)
    if not match:
        raise ValueError(
            f"올바른 Jira 이슈 키 형식이 아닙니다: '{issue_key}' (예: AAOSFRWDEV-543, PROJ-100)"
        )
    return match.group(1), int(match.group(2))


def parse_steps(steps_str: str) -> Set[int]:
    """Parses step ranges like '1,2,3,4' or '1-4' into a set of integers."""
    result = set()
    for part in steps_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            result.update(range(int(start), int(end) + 1))
        elif part.isdigit():
            result.add(int(part))
        else:
            raise ValueError(f"지원하지 않는 스텝 형식: {part}")
    return result


def run_step1(
    issue_key: str,
    project_key: str,
    issue_num: int,
    output_dir: Path,
    sql_template_path: Path = DEFAULT_SQL_TEMPLATE,
    all_fields: bool = True,
) -> Path:
    """[Step 1] Generates single-issue optimized SQL query with project CTE and issuenum filter.

    When all_fields=True (default), relaxes strict 'LIKE %jeditor%' content filters so that
    all target columns/fields (Description, Environment, Comments, Custom Fields) for this issue
    are extracted without being dropped.
    """
    print("\n" + "=" * 80)
    print(f"▶ [Step 1/4] 대상 프로젝트 SQL 생성 (step1_update_sql_projects)")
    print("=" * 80)

    if not sql_template_path.exists():
        raise FileNotFoundError(f"기본 SQL 템플릿 파일이 없습니다: {sql_template_path}")

    raw_sql = sql_template_path.read_text(encoding="utf-8")

    # 1. Replace target_projects CTE with single project
    cte_pattern = re.compile(
        r"WITH\s+target_projects\s+AS\s*\(.*?\)", re.IGNORECASE | re.DOTALL
    )
    if not cte_pattern.search(raw_sql):
        raise ValueError(f"{sql_template_path.name}에 target_projects CTE가 없습니다.")
    single_cte = build_projects_cte([project_key])
    updated_sql = cte_pattern.sub(single_cte, raw_sql, count=1)

    # 2. Inject issue number condition for fast single-issue execution
    where_pattern = re.compile(
        r"WHERE\s+\(i\.archived\s+IS\s+NULL\s+OR\s+i\.archived\s*<>\s*'Y'\)",
        re.IGNORECASE,
    )
    filtered_sql, count = where_pattern.subn(
        f"WHERE (i.archived IS NULL OR i.archived <> 'Y')\n  AND i.issuenum = {issue_num}",
        updated_sql,
    )

    # 3. Target only fields containing HTML elements or JEditor markup when all_fields is True
    if all_fields:
        # Description: HTML elements or JEditor only
        desc_pattern = re.compile(
            r"(AND\s+i\.description\s+IS\s+NOT\s+NULL\s+AND\s+)(?:i\.description\s+LIKE\s+'%jeditor%'|\(.*?\))",
            re.IGNORECASE | re.DOTALL,
        )
        replacement_desc = """AND i.description IS NOT NULL
  AND (
      i.description LIKE '%jeditor%'
      OR i.description LIKE '%</%'
      OR i.description LIKE '%<p%'
      OR i.description LIKE '%<table%'
      OR i.description LIKE '%<div%'
      OR i.description LIKE '%<span%'
      OR i.description LIKE '%<br%'
      OR i.description LIKE '%<ul%'
      OR i.description LIKE '%<ol%'
      OR i.description LIKE '%<h1%'
      OR i.description LIKE '%<h2%'
      OR i.description LIKE '%<h3%'
      OR i.description LIKE '%<a %'
      OR i.description LIKE '%<img%'
  )"""
        filtered_sql, _ = desc_pattern.subn(replacement_desc, filtered_sql, count=1)

        # Environment: HTML elements or JEditor only
        env_pattern = re.compile(
            r"(AND\s+i\.environment\s+IS\s+NOT\s+NULL\s+AND\s+)(?:i\.environment\s+LIKE\s+'%jeditor%'|\(.*?\))",
            re.IGNORECASE | re.DOTALL,
        )
        replacement_env = """AND i.environment IS NOT NULL
  AND (
      i.environment LIKE '%jeditor%'
      OR i.environment LIKE '%</%'
      OR i.environment LIKE '%<p%'
      OR i.environment LIKE '%<table%'
      OR i.environment LIKE '%<div%'
      OR i.environment LIKE '%<span%'
      OR i.environment LIKE '%<br%'
      OR i.environment LIKE '%<ul%'
      OR i.environment LIKE '%<ol%'
      OR i.environment LIKE '%<h1%'
      OR i.environment LIKE '%<h2%'
      OR i.environment LIKE '%<h3%'
      OR i.environment LIKE '%<a %'
      OR i.environment LIKE '%<img%'
  )"""
        filtered_sql, _ = env_pattern.subn(replacement_env, filtered_sql, count=1)

        # Comment: HTML elements or JEditor only
        comment_pattern = re.compile(
            r"(AND\s+ja\.actionbody\s+IS\s+NOT\s+NULL\s+AND\s+)(?:ja\.actionbody\s+LIKE\s+'%jeditor%'|\(.*?\))",
            re.IGNORECASE | re.DOTALL,
        )
        replacement_comment = """AND ja.actionbody IS NOT NULL
  AND (
      ja.actionbody LIKE '%jeditor%'
      OR ja.actionbody LIKE '%</%'
      OR ja.actionbody LIKE '%<p%'
      OR ja.actionbody LIKE '%<table%'
      OR ja.actionbody LIKE '%<div%'
      OR ja.actionbody LIKE '%<span%'
      OR ja.actionbody LIKE '%<br%'
      OR ja.actionbody LIKE '%<ul%'
      OR ja.actionbody LIKE '%<ol%'
      OR ja.actionbody LIKE '%<h1%'
      OR ja.actionbody LIKE '%<h2%'
      OR ja.actionbody LIKE '%<h3%'
      OR ja.actionbody LIKE '%<a %'
      OR ja.actionbody LIKE '%<img%'
  )"""
        filtered_sql, _ = comment_pattern.subn(replacement_comment, filtered_sql, count=1)

        # Custom Fields: HTML elements or JEditor only
        cf_pattern = re.compile(
            r"AND\s+\(\s*(?:--[^\n]*\n\s*)*(?:cfv\.textvalue\s+LIKE|\(cfv\.textvalue).*?\)\s*(?=\n\s*(?:--|ORDER BY|\Z))",
            re.IGNORECASE | re.DOTALL,
        )
        replacement_cf = """AND (
      cfv.textvalue LIKE '%jeditor%'
      OR cfv.stringvalue LIKE '%jeditor%'
      OR cfv.textvalue LIKE '%</%'
      OR cfv.stringvalue LIKE '%</%'
      OR cfv.textvalue LIKE '%<p%'
      OR cfv.stringvalue LIKE '%<p%'
      OR cfv.textvalue LIKE '%<div%'
      OR cfv.stringvalue LIKE '%<div%'
      OR cfv.textvalue LIKE '%<span%'
      OR cfv.stringvalue LIKE '%<span%'
      OR cfv.textvalue LIKE '%<table%'
      OR cfv.stringvalue LIKE '%<table%'
      OR cfv.textvalue LIKE '%<br%'
      OR cfv.stringvalue LIKE '%<br%'
      OR cfv.textvalue LIKE '%<ul%'
      OR cfv.stringvalue LIKE '%<ul%'
      OR cfv.textvalue LIKE '%<ol%'
      OR cfv.stringvalue LIKE '%<ol%'
      OR cfv.textvalue LIKE '%<h1%'
      OR cfv.stringvalue LIKE '%<h1%'
      OR cfv.textvalue LIKE '%<h2%'
      OR cfv.stringvalue LIKE '%<h2%'
      OR cfv.textvalue LIKE '%<h3%'
      OR cfv.stringvalue LIKE '%<h3%'
      OR cfv.textvalue LIKE '%<a %'
      OR cfv.stringvalue LIKE '%<a %'
      OR cfv.textvalue LIKE '%<img%'
      OR cfv.stringvalue LIKE '%<img%'
      OR cfv.textvalue LIKE '%<b%'
      OR cfv.stringvalue LIKE '%<b%'
      OR cfv.textvalue LIKE '%<font%'
      OR cfv.stringvalue LIKE '%<font%'
  )"""
        filtered_sql, _ = cf_pattern.subn(replacement_cf, filtered_sql, count=1)

    query_out_path = output_dir / f"step1_query_{issue_key}.sql"
    query_out_path.write_text(filtered_sql, encoding="utf-8")

    print(f"  • 대상 프로젝트: {project_key} (이슈 번호: {issue_num})")
    print(f"  • CTE 설정 완료: target_projects ({project_key})")
    print(f"  • 단건 조건 반영: i.issuenum = {issue_num} (총 {count}개 서브쿼리 최적화)")
    if all_fields:
        print(f"  • 대상 필드 필터: HTML 요소(태그/마크업) 또는 JEditor 포함 필드만 선별 추출")
    print(f"  ✓ 저장된 SQL: {query_out_path}")
    return query_out_path


def run_step2(
    issue_key: str,
    project_key: str,
    sql_path: Path,
    output_dir: Path,
    fallback_input: Optional[Path] = None,
) -> tuple[Path, int]:
    """[Step 2] Executes single-issue SQL against Jira DB or falls back to existing export."""
    print("\n" + "=" * 80)
    print(f"▶ [Step 2/4] Jira DB에서 데이터 추출 (step2_query_runner)")
    print("=" * 80)

    target_html_path = output_dir / f"step2_data_{issue_key}.html"
    db_config = get_db_config()
    db_available = False
    connection = None

    if db_config.get("host") and pymysql is not None:
        try:
            print(f"  • Jira DB 연결 시도: {db_config['host']}:{db_config['port']}/{db_config['database']}...")
            connection = pymysql.connect(
                host=db_config["host"],
                port=db_config["port"],
                user=db_config["user"],
                password=db_config["password"],
                database=db_config["database"],
                charset="utf8mb4",
                cursorclass=pymysql.cursors.SSDictCursor,
                connect_timeout=10,
            )
            db_available = True
            print("  ✓ Jira DB 연결 성공!")
        except Exception as e:
            print(f"  ! DB 연결 실패 ({e}). Fallback 데이터 소스를 탐색합니다.")

    row_count = 0

    if db_available and connection is not None:
        print(f"  • 단건 쿼리 실행 중: {sql_path.name}...")
        query_text = sql_path.read_text(encoding="utf-8")
        with target_html_path.open("w", encoding="utf-8") as out:
            write_html_header(out, query_text)
            headers = None
            with connection.cursor() as cursor:
                cursor.execute(query_text)
                for row in cursor:
                    if headers is None:
                        headers = list(row.keys())
                        th_cols = "".join(f"<th>{html.escape(c)}</th>" for c in headers)
                        out.write(f"<tr>{th_cols}</tr>\n")
                    write_html_row(out, row, headers, row_count)
                    row_count += 1
            out.write("</table></body></html>\n")
        connection.close()
        print(f"  ✓ DB 추출 완료: {row_count}건")
    else:
        # Fallback to local HTML export if DB is offline or not configured
        source_file = None
        candidates = [fallback_input, Path("data.html"), Path("examples/data_sample.html")]
        for cand in candidates:
            if cand and Path(cand).is_file():
                source_file = Path(cand)
                break

        if not source_file:
            raise RuntimeError(
                "Jira DB에 접속할 수 없고, 추출에 사용할 기존 data.html 파일도 찾을 수 없습니다.\n"
                "  1) ../env 또는 ./env에 JIRA_DB_HOST, JIRA_DB_USER, JIRA_DB_PASSWORD를 설정하거나\n"
                "  2) --input <data.html> 옵션으로 기존 내보내기 파일을 지정하세요."
            )

        print(f"  • [Fallback 모드] 기존 파일에서 '{issue_key}' 추출: {source_file}")
        extracted_rows = list(read_export(source_file, issue_key=issue_key))
        total_raw = len(extracted_rows)
        # Filter: only keep rows that contain actual HTML elements or JEditor
        extracted_rows = [r for r in extracted_rows if has_html_elements(r.get("content"))]
        row_count = len(extracted_rows)
        if total_raw > row_count:
            print(f"  • HTML 요소 필터링: 총 {total_raw}개 중 HTML/JEditor 포함 {row_count}개 필드만 추출 (비-HTML {total_raw - row_count}개 제외)")

        headers = [
            "project_key",
            "project_name",
            "issue_key",
            "issue_status",
            "field_id",
            "field_name",
            "detection_type",
            "content",
            "migration_status",
        ]
        with target_html_path.open("w", encoding="utf-8") as out:
            out.write("<!DOCTYPE html><html><head><meta charset=\"UTF-8\"/></head><body><table>\n")
            out.write("<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>\n")
            for idx, r in enumerate(extracted_rows):
                write_html_row(out, r, headers, idx)
            out.write("</table></body></html>\n")

        print(f"  ✓ 단건 데이터 파일 생성 완료: {row_count}건 (소스: {source_file.name})")

    print(f"  ✓ 생성된 HTML: {target_html_path}")
    return target_html_path, row_count


def run_step3(data_html_path: Path, output_dir: Path) -> dict:
    """[Step 3] Analyzes the generated HTML table and produces report/dashboard."""
    print("\n" + "=" * 80)
    print(f"▶ [Step 3/4] 추출 데이터 스트리밍 분석 (step3_analyze_data_html)")
    print("=" * 80)

    stats = parse_html_table_stream(str(data_html_path))
    text_report = generate_text_report(stats)

    report_out_path = output_dir / "step3_analysis_report.txt"
    report_out_path.write_text(text_report, encoding="utf-8")

    dashboard_out_path = output_dir / "step3_data_dashboard.html"
    generate_html_dashboard(stats, str(dashboard_out_path))

    # Display key highlights
    print(f"  • 분석 대상: {data_html_path.name} (총 {stats['total_rows']}개 행)")
    fields = stats.get("global_field_names", {})
    if fields:
        field_summary = ", ".join(f"{fname}: {len(issues)}건" for fname, issues in fields.items())
        print(f"  • 감지된 필드: {field_summary}")
    detections = stats.get("global_detections", {})
    if detections:
        det_summary = ", ".join(f"{dtype}: {len(issues)}건" for dtype, issues in detections.items())
        print(f"  • JEditor 감지 유형: {det_summary}")

    print(f"  ✓ 텍스트 리포트: {report_out_path}")
    print(f"  ✓ 인터랙티브 대시보드: {dashboard_out_path}")
    return stats


def run_step4(
    data_html_path: Path,
    issue_key: str,
    target_issue: Optional[str],
    env: str,
    real_run: bool,
    insecure: bool,
    output_dir: Path,
    field_map: Optional[str] = None,
    allow_same_field_ids: bool = False,
) -> int:
    """[Step 4] Converts data_single.html content to Cloud ADF and migrates attachments."""
    print("\n" + "=" * 80)
    print(f"▶ [Step 4/4] data.html → Cloud ADF 변환 (step4_convert_adf)")
    print("=" * 80)

    adf_dir = output_dir / "step4_adf_report"
    adf_dir.mkdir(parents=True, exist_ok=True)

    argv = [
        "--input",
        str(data_html_path),
        "--issue",
        issue_key,
        "--output-dir",
        str(adf_dir),
        "--env",
        env,
        "--no-prompt",
        "--retry-skipped",
        "--html-only",
    ]
    if target_issue:
        argv.extend(["--target-issue", target_issue])
    if field_map:
        argv.extend(["--field-map", field_map])
    if allow_same_field_ids:
        argv.append("--allow-same-field-ids")
    if real_run:
        argv.append("--real-run")
    else:
        argv.append("--dry-run")
    if insecure:
        argv.append("--insecure")

    mode_label = "실제 반영 (REAL-RUN)" if real_run else "미리보기 (DRY-RUN)"
    target_label = target_issue or issue_key
    print(f"  • 원본 이슈: {issue_key} → 대상 Cloud 이슈: {target_label}")
    print(f"  • 실행 모드: {mode_label} | 환경: {env}")
    print(f"  • 결과 저장: {adf_dir}")

    ret_code = convert_adf.main(argv)
    if ret_code == 0:
        print("  ✓ Step 4 ADF 변환 성공 완료!")
    else:
        print(f"  ! Step 4 완료 (반환 코드: {ret_code})")
    return ret_code


def interactive_prompt() -> tuple[str, Optional[str], str, bool]:
    """Guides user through interactive input when no CLI arguments are given."""
    print("\n" + "=" * 80)
    print("  JEditor 마이그레이션 단건 테스트 파이프라인 (Step 1 ~ Step 4)")
    print("=" * 80)

    while True:
        issue = input("\n1. 테스트할 원본 Jira 이슈 키를 입력하세요 (예: AAOSFRWDEV-543): ").strip().upper()
        if ISSUE_KEY_PATTERN.match(issue):
            break
        print("   [오류] 올바른 형식의 이슈 키를 입력하세요 (예: PROJ-123).")

    target = input(f"2. 대상 Cloud 이슈 키 [Enter: 원본({issue})과 동일]: ").strip().upper() or None
    if target and not ISSUE_KEY_PATTERN.match(target):
        print(f"   [주의] 대상 이슈 키 '{target}' 형식이 올바르지 않아 원본과 동일하게 진행합니다.")
        target = None

    print("\n3. 실행 모드:")
    print("   1) 미리보기 (DRY-RUN) - Cloud 변경 없음 (권장)")
    print("   2) 실제 반영 (REAL-RUN) - 첨부파일 업로드 및 Cloud 필드 덮어쓰기")
    mode_choice = input("   선택 [기본 1]: ").strip()
    real_run = mode_choice == "2"

    print("\n4. 대상 Jira Cloud 환경:")
    print("   1) SANDBOX (기본값)")
    print("   2) OPS")
    env_choice = input("   선택 [기본 1]: ").strip()
    env = "OPS" if env_choice == "2" else "SANDBOX"

    return issue, target, env, real_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Step 1부터 Step 4까지 단 건 이슈로 테스트하는 통합 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 기본 미리보기 (Dry-Run)
  python3 pipeline_single_issue.py AAOSFRWDEV-543

  # Cloud 대상 테스트 이슈 매핑 (Sandbox의 CFMT-3로 대상 변경)
  python3 pipeline_single_issue.py AAOSFRWDEV-543 --target-issue CFMT-3

  # Cloud 실제 반영 (Real-Run)
  python3 pipeline_single_issue.py AAOSFRWDEV-543 --target-issue CFMT-3 --real-run

  # 특정 Step만 실행 (예: Step 3, 4만 실행)
  python3 pipeline_single_issue.py AAOSFRWDEV-543 --steps 3,4 --input data.html

  # 인자 없이 대화형 프롬프트로 실행
  python3 pipeline_single_issue.py
""",
    )
    parser.add_argument(
        "issue_key",
        nargs="?",
        default=None,
        help="테스트할 Jira 이슈 키 (예: AAOSFRWDEV-543)",
    )
    parser.add_argument(
        "--target-issue",
        "-t",
        default=None,
        help="클라우드 대상 이슈 키 오버라이드 (기본값: 원본 이슈 키와 동일)",
    )
    parser.add_argument(
        "--env",
        "-e",
        choices=("SANDBOX", "OPS"),
        default="SANDBOX",
        help="대상 Jira Cloud 환경 (기본값: SANDBOX)",
    )
    parser.add_argument(
        "--real-run",
        action="store_true",
        help="Cloud Jira에 실제로 필드 및 첨부파일 반영 (기본값: 미리보기/dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="미리보기 전용 모드 (Cloud 저장 없음)",
    )
    parser.add_argument(
        "--steps",
        default="1,2,3,4",
        help="실행할 스텝 목록 (기본값: 1,2,3,4 또는 1-4)",
    )
    parser.add_argument(
        "--input",
        "-i",
        default=None,
        help="기존 HTML 내보내기 파일 (DB 미연결 시 fallback으로 사용하거나 특정 파일 지정)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="리포트 및 산출물 저장 디렉터리 (기본값: single_issue_reports/{ISSUE_KEY})",
    )
    parser.add_argument(
        "--strict-jeditor",
        action="store_true",
        help="'LIKE %%jeditor%%' 문자열이 포함된 필드만 엄격하게 필터링 (기본값: False, 본문/환경/커스텀필드 전체 대상 추출)",
    )
    parser.add_argument(
        "--field-map",
        default=None,
        help="Server customfield ID to Cloud customfield ID 매핑 JSON 파일 경로",
    )
    parser.add_argument(
        "--allow-same-field-ids",
        action="store_true",
        help="Cloud customfield ID가 Server와 동일하다고 가정하고 자동 매핑 허용",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=True,
        help="TLS 인증서 검증 비활성화 (기본값: True)",
    )
    parser.add_argument(
        "--no-insecure",
        dest="insecure",
        action="store_false",
        help="TLS 인증서 검증 활성화",
    )

    args = parser.parse_args(argv)

    if not args.issue_key:
        if sys.stdin.isatty():
            issue, target, env, real_run = interactive_prompt()
            args.issue_key = issue
            if target:
                args.target_issue = target
            args.env = env
            if real_run:
                args.real_run = True
        else:
            parser.error("이슈 키를 인자로 지정하세요. 예: python3 pipeline_single_issue.py AAOSFRWDEV-543")

    issue_key = args.issue_key.strip().upper()
    project_key, issue_num = parse_issue_key(issue_key)
    target_issue = args.target_issue.strip().upper() if args.target_issue else None
    steps = parse_steps(args.steps)
    real_run = bool(args.real_run and not args.dry_run)

    output_dir = Path(args.output_dir) if args.output_dir else Path("single_issue_reports") / issue_key
    output_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.monotonic()
    print("\n" + "=" * 80)
    print(f"🚀 [JEditor 단건 테스트 파이프라인] 대상 이슈: {issue_key}")
    print("=" * 80)
    print(f"  • 프로젝트: {project_key} | 이슈 번호: {issue_num}")
    print(f"  • 대상 이슈: {target_issue or '원본과 동일'} | 대상 환경: {args.env}")
    print(f"  • 실행 모드: {'실제 반영 (REAL-RUN)' if real_run else '미리보기 (DRY-RUN)'}")
    print(f"  • 실행 스텝: {sorted(steps)} | 결과 디렉터리: {output_dir}")

    sql_path = output_dir / f"step1_query_{issue_key}.sql"
    data_html_path = output_dir / f"step2_data_{issue_key}.html"
    fallback_input = Path(args.input) if args.input else None

    stage_times = {}

    # Step 1
    if 1 in steps:
        stage_start = time.monotonic()
        sql_path = run_step1(
            issue_key=issue_key,
            project_key=project_key,
            issue_num=issue_num,
            output_dir=output_dir,
            all_fields=not args.strict_jeditor,
        )
        stage_times[1] = time.monotonic() - stage_start
        print(f"  [TIME] Step 1 SQL 생성: {stage_times[1]:.2f}초", flush=True)
    else:
        print("\n[Step 1] 건너뜀 (--steps 제외됨)")

    # Step 2
    step2_rows = 0
    if 2 in steps:
        stage_start = time.monotonic()
        data_html_path, step2_rows = run_step2(
            issue_key, project_key, sql_path, output_dir, fallback_input=fallback_input
        )
        stage_times[2] = time.monotonic() - stage_start
        print(f"  [TIME] Step 2 DB 추출: {stage_times[2]:.2f}초", flush=True)
    else:
        print("\n[Step 2] 건너뜀 (--steps 제외됨)")
        if fallback_input and fallback_input.is_file():
            data_html_path = fallback_input
        elif not data_html_path.exists():
            # Check if default data.html exists
            if Path("data.html").exists():
                data_html_path = Path("data.html")

    # Step 3
    if 3 in steps:
        if not data_html_path.exists():
            raise FileNotFoundError(
                f"Step 3 분석에 필요한 HTML 파일이 없습니다: {data_html_path}\n"
                "Step 2를 실행하거나 --input으로 HTML 파일을 지정하세요."
            )
        stage_start = time.monotonic()
        run_step3(data_html_path, output_dir)
        stage_times[3] = time.monotonic() - stage_start
        print(f"  [TIME] Step 3 분석: {stage_times[3]:.2f}초", flush=True)
    else:
        print("\n[Step 3] 건너뜀 (--steps 제외됨)")

    # Step 4
    step4_code = 0
    if 4 in steps:
        if not data_html_path.exists():
            raise FileNotFoundError(
                f"Step 4 변환에 필요한 HTML 파일이 없습니다: {data_html_path}\n"
                "Step 2를 실행하거나 --input으로 HTML 파일을 지정하세요."
            )
        stage_start = time.monotonic()
        step4_code = run_step4(
            data_html_path=data_html_path,
            issue_key=issue_key,
            target_issue=target_issue,
            env=args.env,
            real_run=real_run,
            insecure=args.insecure,
            output_dir=output_dir,
            field_map=args.field_map,
            allow_same_field_ids=args.allow_same_field_ids,
        )
        stage_times[4] = time.monotonic() - stage_start
        print(f"  [TIME] Step 4 변환·Cloud 반영: {stage_times[4]:.2f}초", flush=True)
    else:
        print("\n[Step 4] 건너뜀 (--steps 제외됨)")

    elapsed = time.monotonic() - start_time

    print("\n" + "=" * 80)
    print(f"🎉 [단건 테스트 파이프라인 완료] 소요 시간: {elapsed:.2f}초")
    print("=" * 80)
    print(f"  • 대상 이슈: {issue_key} (Cloud 대상: {target_issue or issue_key})")
    print(f"  • 산출물 디렉터리: {output_dir.resolve()}")
    if 1 in steps and sql_path.exists():
        print(f"    - [Step 1] SQL 쿼리      : {sql_path.name}")
    if 2 in steps and data_html_path.exists():
        print(f"    - [Step 2] 추출 데이터   : {data_html_path.name}")
    if 3 in steps and (output_dir / "step3_data_dashboard.html").exists():
        print(f"    - [Step 3] HTML 대시보드 : step3_data_dashboard.html")
    if 4 in steps:
        print(f"    - [Step 4] ADF 결과 폴더 : step4_adf_report/")
    print("=" * 80 + "\n")

    return step4_code


if __name__ == "__main__":
    sys.exit(main())
