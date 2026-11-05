"""Tests for report_active_connections.py."""

from pathlib import Path
from report_active_connections import (
    strip_groovy_comments,
    analyze_incoming_processor,
    parse_connections_from_html,
    filter_active_connections,
    format_markdown_report,
    format_terminal_report,
)


def test_strip_groovy_comments():
    code = (
        "// line comment\n"
        "/* block comment */\n"
        'def x = "https://example.com" // inline comment\n'
        "/* multi\n"
        "line\n"
        "comment */\n"
        "issue.summary = replica.summary\n"
    )
    stripped = strip_groovy_comments(code)
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    assert len(lines) == 2
    assert lines[0] == 'def x = "https://example.com"'
    assert lines[1] == "issue.summary = replica.summary"


def test_analyze_incoming_processor_all_comment():
    raw_html = (
        "// if (firstSync) {<br>"
        "//     issue.projectKey = 'HMCP'<br>"
        "// }<br>"
        "/* block comment */"
    )
    status, count, code_lines, clean_code = analyze_incoming_processor(raw_html)
    assert status == "ALL_COMMENT"
    assert count == 0
    assert code_lines == 0


def test_analyze_incoming_processor_valid_code():
    raw_html = (
        "// comment<br>"
        "issue.projectKey = 'TEST'<br>"
        "issue.summary = replica.summary"
    )
    status, count, code_lines, clean_code = analyze_incoming_processor(raw_html)
    assert status == "VALID"
    assert count == 1
    assert code_lines == 2


def test_analyze_incoming_processor_empty():
    status, count, code_lines, clean_code = analyze_incoming_processor("")
    assert status == "EMPTY"
    assert count == 0
    assert code_lines == 0


def test_filter_active_connections_only_active(tmp_path: Path):
    html_content = (
        "<table><tbody>"
        "<tr><th>ID</th><th>NAME</th><th>STATUS</th><th>VER</th><th>LOCAL</th><th>LURL</th>"
        "<th>INSTANCE_URL</th><th>SP</th><th>RP</th><th>DF</th><th>FV</th><th>CP</th><th>CHP</th><th>INC</th><th>POST</th></tr>"
        "<tr><td>1</td><td>Active Conn 1</td><td>ACTIVE</td><td>2</td><td>0</td><td></td>"
        "<td>https://jira1.test.com</td><td></td><td></td><td></td><td></td>"
        "<td>issue.projectKey = 'AAA'</td><td></td><td>issue.summary = replica.summary</td><td></td></tr>"
        "<tr><td>2</td><td>Deactivated Conn</td><td>DEACTIVATED</td><td>2</td><td>0</td><td></td>"
        "<td>https://jira2.test.com</td><td></td><td></td><td></td><td></td>"
        "<td>issue.projectKey = 'AAA'</td><td></td><td>issue.summary = replica.summary</td><td></td></tr>"
        "<tr><td>3</td><td>Active Conn Commented</td><td>ACTIVE</td><td>2</td><td>0</td><td></td>"
        "<td>https://jira3.test.com</td><td></td><td></td><td></td><td></td>"
        "<td>issue.projectKey = 'AAA'</td><td></td><td>// issue.summary = replica.summary</td><td></td></tr>"
        "</tbody></table>"
    )
    file_path = tmp_path / "test.html"
    file_path.write_text(html_content, encoding="utf-8")

    connections = parse_connections_from_html(file_path)
    assert len(connections) == 3

    items = filter_active_connections(connections, ["AAA"])
    assert len(items) == 2  # Only active ones (Conn 1 and Conn 3)

    assert items[0].id == "1"
    assert items[0].incoming_status == "VALID"
    assert items[0].incoming_count == 1
    assert items[0].instance_url == "https://jira1.test.com"

    assert items[1].id == "3"
    assert items[1].incoming_status == "ALL_COMMENT"
    assert items[1].incoming_count == 0
    assert items[1].instance_url == "https://jira3.test.com"

    # Terminal report
    term_report = format_terminal_report(["AAA"], items)
    assert "대상 ACTIVE Connection 수: 2건" in term_report
    assert "유효 INCOMING_PROCESSOR 수: 1건" in term_report
    assert "주석/미설정 INCOMING_PROCESSOR: 1건 (0건으로 카운트)" in term_report

    # Markdown report
    md_report = format_markdown_report(["AAA"], items)
    assert "대상 ACTIVE Connection 수**: **2건**" in md_report
    assert "유효 INCOMING_PROCESSOR 수**: **1건**" in md_report
    assert "전체 주석 처리: 1건 (0건 산정)" in md_report
