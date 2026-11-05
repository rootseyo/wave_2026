#!/usr/bin/env python3
"""Generate an active connection report with INCOMING_PROCESSOR comment audit."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ConnectionReportItem:
    id: str
    name: str
    status: str
    instance_url: str
    matched_keys: List[str]
    incoming_status: str  # 'VALID', 'ALL_COMMENT', 'EMPTY'
    incoming_count: int   # 1 if VALID, 0 if ALL_COMMENT or EMPTY
    code_lines_count: int
    total_chars: int
    incoming_code: str
    incoming_preview: str


def strip_groovy_comments(code: str) -> str:
    """Strip Groovy comments (/*...*/ and //...) while preserving string literals."""
    pattern = re.compile(
        r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|'
        r'(/\*[\s\S]*?\*/|//[^\r\n]*)'
    )
    return pattern.sub(lambda m: m.group(1) or '', code)


def analyze_incoming_processor(raw_html: str) -> tuple[str, int, int, str]:
    """Analyze incoming processor HTML content.

    Returns:
        tuple of (status, count, code_lines_count, clean_code)
        status: 'VALID', 'ALL_COMMENT', or 'EMPTY'
        count: 1 if VALID, 0 if ALL_COMMENT or EMPTY
    """
    if not raw_html:
        return 'EMPTY', 0, 0, ''

    text = re.sub(r'<br\s*/?>', '\n', raw_html)
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '', text).strip()

    if not text:
        return 'EMPTY', 0, 0, ''

    stripped = strip_groovy_comments(text)
    code_lines = [line.strip() for line in stripped.splitlines() if line.strip()]

    if code_lines:
        return 'VALID', 1, len(code_lines), text
    return 'ALL_COMMENT', 0, 0, text


def parse_connections_from_html(html_path: Path) -> list[dict]:
    """Parse AO_08F1AF_RELATION table rows from DBeaver HTML export."""
    content = html_path.read_text(encoding="utf-8")
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.S)
    data_rows = [tr for tr in trs if '<td' in tr]

    parsed = []
    for tr in data_rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
        if len(tds) < 14:
            continue

        conn_id = html.unescape(re.sub(r'<[^>]+>', '', tds[0])).strip()
        conn_name = html.unescape(re.sub(r'<[^>]+>', '', tds[1])).strip()
        status = html.unescape(re.sub(r'<[^>]+>', '', tds[2])).strip()
        inst_url = html.unescape(re.sub(r'<[^>]+>', '', tds[6])).strip()

        # Text across key cells to search for project key references
        # 1: name, 9: data_filter, 10: field_values, 11: create_proc, 12: change_proc, 13: incoming_proc
        searchable_cells = [tds[1], tds[9], tds[10], tds[11], tds[12], tds[13]]
        clean_searchable = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', c)) for c in searchable_cells)

        parsed.append({
            'id': conn_id,
            'name': conn_name,
            'status': status,
            'instance_url': inst_url,
            'incoming_raw': tds[13],
            'searchable_text': clean_searchable
        })
    return parsed


def filter_active_connections(
    connections: list[dict],
    project_keys: list[str]
) -> list[ConnectionReportItem]:
    """Filter connections to those matching project_keys and having ACTIVE status."""
    norm_keys = [k.strip().upper() for k in project_keys if k.strip()]
    results = []

    for conn in connections:
        # Check ACTIVE status
        if conn['status'].upper() != 'ACTIVE':
            continue

        # Check project key match
        matched = []
        for key in norm_keys:
            pattern = rf'(^|[^A-Z0-9_]){re.escape(key)}(?=$|[^A-Z0-9_])'
            if re.search(pattern, conn['searchable_text'], re.I):
                matched.append(key)

        if not matched:
            continue

        inc_status, inc_count, code_lines, clean_code = analyze_incoming_processor(conn['incoming_raw'])

        if inc_status == 'VALID':
            preview = '\n'.join([l for l in clean_code.splitlines() if l.strip()][:4])
        elif inc_status == 'ALL_COMMENT':
            preview = '(모든 코드가 주석 처리되어 있습니다. 유효 코드 0건)'
        else:
            preview = '(내용 없음 / 0건)'

        results.append(ConnectionReportItem(
            id=conn['id'],
            name=conn['name'],
            status=conn['status'],
            instance_url=conn['instance_url'],
            matched_keys=matched,
            incoming_status=inc_status,
            incoming_count=inc_count,
            code_lines_count=code_lines,
            total_chars=len(clean_code),
            incoming_code=clean_code,
            incoming_preview=preview
        ))

    return results


def format_markdown_report(keys: list[str], items: list[ConnectionReportItem]) -> str:
    """Generate a clean Markdown report."""
    valid_incoming = sum(item.incoming_count for item in items)
    commented_incoming = sum(1 for item in items if item.incoming_status == 'ALL_COMMENT')
    empty_incoming = sum(1 for item in items if item.incoming_status == 'EMPTY')

    lines = []
    lines.append(f"# Exalate Active Connection 마이그레이션 리포트")
    lines.append("")
    lines.append("## 1. 요약 통계 (Summary)")
    lines.append("")
    lines.append(f"- **검색 프로젝트 키**: `{', '.join(keys)}`")
    lines.append(f"- **대상 ACTIVE Connection 수**: **{len(items)}건**")
    lines.append(f"- **유효 INCOMING_PROCESSOR 수**: **{valid_incoming}건** (실행 코드 존재)")
    lines.append(f"- **주석/미작성 INCOMING_PROCESSOR**: **{commented_incoming + empty_incoming}건** (**0건으로 카운트**)")
    lines.append(f"  - 전체 주석 처리: {commented_incoming}건 (0건 산정)")
    lines.append(f"  - 내용 없음: {empty_incoming}건 (0건 산정)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. 대상 Connection 상세 목록")
    lines.append("")

    if not items:
        lines.append("> ⚠️ 입력한 프로젝트 키와 일치하는 **ACTIVE** 상태의 Connection이 없습니다.")
        return '\n'.join(lines)

    lines.append("| ID | Connection Name | 일치 프로젝트 키 | INSTANCE_URL | INCOMING_PROCESSOR 상태 | 카운트 |")
    lines.append("|:---|:----------------|:-----------------|:-------------|:------------------------|:------:|")

    for item in items:
        if item.incoming_status == 'VALID':
            status_badge = f"🟢 유효 코드 ({item.code_lines_count}행)"
        elif item.incoming_status == 'ALL_COMMENT':
            status_badge = "🟠 전체 주석 처리 (0건)"
        else:
            status_badge = "⚪ 내용 없음 (0건)"

        keys_str = ', '.join(item.matched_keys)
        url_str = f"`{item.instance_url}`" if item.instance_url else "*(미설정)*"
        lines.append(f"| {item.id} | **{item.name}** | `{keys_str}` | {url_str} | {status_badge} | **{item.incoming_count}** |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. INCOMING_PROCESSOR 상세 분석")
    lines.append("")

    for item in items:
        lines.append(f"### Connection ID {item.id}: {item.name}")
        lines.append(f"- **INSTANCE_URL**: `{item.instance_url or 'N/A'}`")
        lines.append(f"- **일치 키**: `{', '.join(item.matched_keys)}`")

        if item.incoming_status == 'VALID':
            lines.append(f"- **INCOMING_PROCESSOR 판정**: ✅ **유효 코드 존재 (총 {item.code_lines_count}라인 실행 코드)** → **1건 카운트**")
            lines.append("")
            lines.append("```groovy")
            lines.append(item.incoming_code)
            lines.append("```")
        elif item.incoming_status == 'ALL_COMMENT':
            lines.append(f"- **INCOMING_PROCESSOR 판정**: ⚠️ **전체 주석 처리됨 (유효 코드 0라인)** → **0건 카운트**")
            lines.append("")
            lines.append("<details><summary>주석 처리된 원본 스크립트 접기/펼치기</summary>")
            lines.append("")
            lines.append("```groovy")
            lines.append(item.incoming_code)
            lines.append("```")
            lines.append("</details>")
        else:
            lines.append(f"- **INCOMING_PROCESSOR 판정**: ℹ️ **스크립트 내용 없음** → **0건 카운트**")

        lines.append("")

    return '\n'.join(lines)


def format_terminal_report(keys: list[str], items: list[ConnectionReportItem]) -> str:
    """Generate a clean CLI terminal table report."""
    valid_incoming = sum(item.incoming_count for item in items)
    commented_incoming = sum(1 for item in items if item.incoming_status == 'ALL_COMMENT')
    empty_incoming = sum(1 for item in items if item.incoming_status == 'EMPTY')

    out = []
    out.append("=" * 80)
    out.append(" [Exalate Active Connection Migration Audit Report]")
    out.append("=" * 80)
    out.append(f" * 검색 프로젝트 키: {', '.join(keys)}")
    out.append(f" * 대상 ACTIVE Connection 수: {len(items)}건")
    out.append(f" * 유효 INCOMING_PROCESSOR 수: {valid_incoming}건 (실행 코드 존재)")
    out.append(f" * 주석/미설정 INCOMING_PROCESSOR: {commented_incoming + empty_incoming}건 (0건으로 카운트)")
    out.append(f"   - 전체 주석 처리: {commented_incoming}건 (0건 산정)")
    out.append(f"   - 내용 없음: {empty_incoming}건 (0건 산정)")
    out.append("-" * 80)

    if not items:
        out.append(" (일치하는 ACTIVE Connection이 없습니다)")
        out.append("=" * 80)
        return '\n'.join(out)

    fmt = "{:<4} | {:<32} | {:<28} | {:<16} | {:<5}"
    out.append(fmt.format("ID", "Connection Name", "INSTANCE_URL", "INCOMING 상태", "카운트"))
    out.append("-" * 80)

    for item in items:
        if item.incoming_status == 'VALID':
            status_text = f"유효 ({item.code_lines_count}행)"
        elif item.incoming_status == 'ALL_COMMENT':
            status_text = "전체주석(0건)"
        else:
            status_text = "내용없음(0건)"

        name_trunc = item.name[:30] + ".." if len(item.name) > 32 else item.name
        url_trunc = item.instance_url[:26] + ".." if len(item.instance_url) > 28 else item.instance_url
        out.append(fmt.format(item.id, name_trunc, url_trunc, status_text, str(item.incoming_count)))

    out.append("=" * 80)
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(
        description="프로젝트 키 입력 기반 Active Connection 및 Incoming Processor 분석 리포트 생성기"
    )
    parser.add_argument(
        "--keys", "-k",
        type=str,
        help="쉼표로 구분된 프로젝트 키 목록 (예: HMCP,KHD,MKWORK). 미입력 시 대화형으로 입력받습니다."
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path("20260904-183555.html"),
        help="DBeaver 원본 HTML 파일 경로 (기본값: 20260904-183555.html)"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="리포트 파일 저장 경로 (예: active_report.md 또는 active_report.html)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["terminal", "markdown", "json"],
        default="terminal",
        help="출력 포맷 (terminal, markdown, json. 기본값: terminal)"
    )

    args = parser.parse_args()

    raw_keys = args.keys
    if not raw_keys:
        try:
            raw_keys = input("프로젝트 키들을 입력하세요 (쉼표 구분, 예: HMCP,KHD,MKWORK): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n취소되었습니다.")
            sys.exit(1)

    project_keys = [k.strip().upper() for k in raw_keys.split(',') if k.strip()]
    if not project_keys:
        print("오류: 프로젝트 키를 1개 이상 입력해야 합니다.", file=sys.stderr)
        sys.exit(1)

    if not args.input.exists():
        print(f"오류: 입력 파일을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        sys.exit(1)

    connections = parse_connections_from_html(args.input)
    items = filter_active_connections(connections, project_keys)

    if args.format == "terminal":
        terminal_report = format_terminal_report(project_keys, items)
        print(terminal_report)
    elif args.format == "markdown":
        md_report = format_markdown_report(project_keys, items)
        print(md_report)
    elif args.format == "json":
        data = {
            "project_keys": project_keys,
            "total_active_connections": len(items),
            "valid_incoming_processors_count": sum(i.incoming_count for i in items),
            "commented_or_empty_incoming_count": sum(1 for i in items if i.incoming_count == 0),
            "items": [asdict(i) for i in items]
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))

    if args.output:
        md_content = format_markdown_report(project_keys, items)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md_content, encoding="utf-8")
        print(f"\n[저장 완료] 리포트 파일: {args.output.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    main()
