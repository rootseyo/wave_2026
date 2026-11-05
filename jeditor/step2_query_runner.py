#!/usr/bin/env python3
"""Jira SQL to HTML Query Runner with Interactive Menu & Project-based Splitting.

Paginates project keys in small batches to optimize MySQL performance,
prevents server memory issues and query timeouts, and streams data directly to HTML.
Supports splitting output into individual project HTML files (data_projects/{PKEY}.html)
along with an interactive master index dashboard (data_projects/index.html).
"""

import os
import sys
import gc
import html
import json
import re
import time
import queue
import threading
import argparse
import warnings
from collections import Counter, defaultdict

# Suppress all library warnings (PyMySQL, MySQL server warnings, urllib3, deprecations)
warnings.filterwarnings("ignore")
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

try:
    import pymysql
    try:
        warnings.filterwarnings("ignore", category=pymysql.Warning)
        from pymysql.err import Warning as PyMySQLWarning
        warnings.filterwarnings("ignore", category=PyMySQLWarning)
    except Exception:
        pass
except ImportError:
    pymysql = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        pass

try:
    from terminal_status import TerminalStatus
except ImportError:
    import shutil
    import threading
    import unicodedata

    class _StatusOutput:
        def __init__(self, owner, stream):
            self.owner, self.stream = owner, stream

        def write(self, text):
            with self.owner.lock:
                self.owner._resize()
                return self.stream.write(text)

        def flush(self):
            with self.owner.lock:
                self.stream.flush()

        def __getattr__(self, name):
            return getattr(self.stream, name)

    class TerminalStatus:
        """Fallback ANSI terminal footer when terminal_status.py is not present."""
        def __init__(self):
            self.lock = threading.RLock()
            self.stream = None
            self.size = None
            self.text = "SQL 데이터 추출 준비 중..."
            self.enabled = False
            self.stop = threading.Event()
            self.thread = None

        def _resize(self):
            size = shutil.get_terminal_size((80, 24))
            if size != self.size:
                self.size = size
                self.stream.write(f"\033[1;{max(1, size.lines - 1)}r\033[{max(1, size.lines - 1)};1H")
                self._draw()

        def _draw(self):
            width = 0
            chars = []
            for char in self.text:
                if unicodedata.category(char).startswith("C"):
                    continue
                cells = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ("W", "F") else 1)
                if width + cells >= self.size.columns:
                    break
                chars.append(char)
                width += cells
            self.stream.write(f"\0337\033[{self.size.lines};1H\033[2K" + "".join(chars) + "\0338")
            self.stream.flush()

        def update(self, text):
            if not self.enabled:
                return
            with self.lock:
                self.text = text.strip()
                self._resize()
                self._draw()

        def _refresh(self):
            while not self.stop.wait(0.25):
                with self.lock:
                    self._resize()
                    self._draw()

        def __enter__(self):
            self.stream = sys.stdout
            self.enabled = self.stream.isatty() and os.environ.get("TERM", "") != "dumb"
            if self.enabled:
                self.original_stdout, self.original_stderr = sys.stdout, sys.stderr
                with self.lock:
                    self._resize()
                sys.stdout = _StatusOutput(self, sys.stdout)
                if sys.stderr.isatty():
                    sys.stderr = _StatusOutput(self, sys.stderr)
                self.thread = threading.Thread(target=self._refresh, daemon=True)
                self.thread.start()
            return self

        def __exit__(self, *exc):
            if self.enabled:
                self.stop.set()
                self.thread.join()
                with self.lock:
                    sys.stdout, sys.stderr = self.original_stdout, self.original_stderr
                    self.stream.write(f"\033[r\033[{self.size.lines};1H\033[2K\n")
                    self.stream.flush()
                    self.enabled = False

# Load environment variables
possible_paths = ['env', '../env']
env_loaded = False
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        env_loaded = True
        break


def get_db_config():
    """Extracts DB configuration from environment variables with defaults."""
    host = os.getenv("JIRA_DB_HOST", "").strip().strip('"').strip("'")
    port_str = os.getenv("JIRA_DB_PORT", "3306").strip().strip('"').strip("'")
    user = os.getenv("JIRA_DB_USER", "").strip().strip('"').strip("'")
    password = os.getenv("JIRA_DB_PASSWORD", "").strip().strip('"').strip("'")
    database = os.getenv("JIRA_DB_NAME", "").strip().strip('"').strip("'")

    try:
        port = int(port_str)
    except ValueError:
        port = 3306

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database
    }


def format_time(seconds):
    """Formats seconds into human-readable string (e.g. '02m 15s')."""
    if seconds is None or seconds < 0:
        return "N/A"
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def format_duration(seconds):
    """Formats elapsed seconds into a clean, human-readable string (e.g. '01분 23초 (83.45초)' or '14.20초')."""
    if seconds is None or seconds < 0:
        return "0.00초"
    if seconds < 60:
        return f"{seconds:.2f}초"
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes:02d}분 {rem_seconds:04.1f}초 ({seconds:.2f}초)"
    hours = int(minutes // 60)
    rem_minutes = minutes % 60
    return f"{hours:02d}시간 {rem_minutes:02d}분 {rem_seconds:02.0f}초 ({seconds:.2f}초)"


def format_size(size_bytes):
    """Formats bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def is_jsonl_path(file_path: str | None) -> bool:
    """Returns True if the target output file path is a JSONL (.jsonl / .ndjson) file."""
    return bool(file_path) and str(file_path).lower().endswith((".jsonl", ".ndjson"))


def get_default_output_file(sql_file: str) -> str:
    """Returns the smart default output filename based on the SQL query file."""
    if not sql_file:
        return "data.jsonl"
    base_name = os.path.splitext(os.path.basename(sql_file))[0].lower()
    if "template" in base_name:
        return "templates_output.html"
    elif "default_value" in base_name:
        return "default_values_report.html"
    elif "mysql_queries" in base_name or base_name == "data":
        return "data.jsonl"
    return f"{base_name}.jsonl"


def get_default_output_dir(sql_file: str) -> str:
    """Returns the default directory for split project files."""
    if not sql_file:
        return "data_projects"
    base_name = os.path.splitext(os.path.basename(sql_file))[0].lower()
    if "template" in base_name:
        return "template_projects"
    return "data_projects"


def remove_sql_comments(raw_query):
    """Removes single-line comments from query string, preserving project key markers."""
    lines = []
    for line in raw_query.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and "PROJECT_KEYS_" in stripped:
            lines.append(line)
        elif stripped.startswith("--"):
            continue
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def load_project_keys_from_file(file_path):
    """Loads project keys from a text file, ignoring comments and blanks."""
    if not os.path.exists(file_path):
        return []
    keys = []
    seen = set()
    with open(file_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            key = line.split("#", 1)[0].strip().upper()
            if not key:
                continue
            if re.fullmatch(r"[A-Z0-9_-]+", key) and key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def extract_project_keys_from_query(query):
    """Extracts project keys from a target_projects CTE or marker in query."""
    cte_match = re.search(
        r"(?is)(WITH\s+target_projects\s+AS\s*\()(.*?)(\)\s*(?=SELECT))",
        query,
    )
    if cte_match:
        cte_body = cte_match.group(2)
        project_keys = re.findall(r"'((?:''|[^'])*)'", cte_body)
        return [key.replace("''", "'") for key in project_keys]

    marker_match = re.search(
        r"(?s)--\s*PROJECT_KEYS_START\s*\n(.*?)\n\s*--\s*PROJECT_KEYS_END",
        query,
    )
    if marker_match:
        project_keys = re.findall(r"'((?:''|[^'])*)'", marker_match.group(1))
        return [key.replace("''", "'") for key in project_keys]

    return []


def build_batch_query(query, keys):
    """Replaces target_projects CTE or PROJECT_KEYS marker in query with given project keys."""
    def escape_literal(k):
        return k.replace("'", "''")

    # 1. Target_projects CTE replacement (MySQL 8.0+)
    cte_match = re.search(
        r"(?is)(WITH\s+target_projects\s+AS\s*\()(.*?)(\)\s*(?=SELECT))",
        query,
    )
    if cte_match:
        values = "\n        UNION ALL\n        ".join(
            f"SELECT '{escape_literal(key)}' AS pkey"
            for key in keys
        )
        replacement = cte_match.group(1) + "\n        " + values + "\n    " + cte_match.group(3)
        return query[:cte_match.start()] + replacement + query[cte_match.end():]

    # 2. -- PROJECT_KEYS_START ... -- PROJECT_KEYS_END marker replacement (MySQL 5.7+ / 8.0+)
    marker_match = re.search(
        r"(?s)(--\s*PROJECT_KEYS_START\s*\n)(.*?)(\n\s*--\s*PROJECT_KEYS_END)",
        query,
    )
    if marker_match:
        values = ",\n    ".join(f"'{escape_literal(key)}'" for key in keys)
        return query[:marker_match.start()] + marker_match.group(1) + f"    {values}" + marker_match.group(3) + query[marker_match.end():]

    return query


def is_default_values_report(raw_query):
    return "default_value_xml" in raw_query and "fieldconfigschemeissuetype" in raw_query.lower()


def write_report_columns(output, headers):
    if "default_value_xml" in headers:
        output.write('<thead><tr><th>프로젝트</th><th>필드</th><th>컨텍스트</th><th>유형</th><th>기본값</th></tr></thead><tbody>\n')
    else:
        output.write("<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in headers) + "</tr>\n")


def write_html_header(output, raw_query):
    """Writes the HTML header and CSS style block."""
    if is_default_values_report(raw_query):
        output.write('''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>HTML 기본값 리포트</title><style>
        *{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:#172b4d;font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
        main{max-width:1500px;margin:auto;padding:36px 24px}h1{margin:0;font-size:28px}p{color:#52647c} .top{display:flex;gap:18px;justify-content:space-between;align-items:end;flex-wrap:wrap;margin-bottom:20px}
        .metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{background:white;border:1px solid #dce3ed;border-radius:10px;padding:12px 18px}.metric strong{font-size:20px;margin-left:8px}
        .toolbar{display:flex;align-items:center;gap:12px;margin:20px 0}input{width:min(100%,430px);padding:11px 14px;border:1px solid #c4d0df;border-radius:8px;font:inherit} .hint{color:#65758b}
        .panel{overflow:auto;background:white;border:1px solid #dce3ed;border-radius:12px}table{border-collapse:collapse;width:100%;min-width:850px}th{text-align:left;background:#edf2f8;color:#4b5e76;font-size:13px}th,td{padding:14px 16px;border-bottom:1px solid #e7ecf2;vertical-align:top}tr:last-child td{border:0}tr:hover td{background:#f9fbff}
        .sub{display:block;color:#66788f;font-size:12px;overflow-wrap:anywhere}.badge{display:inline-block;background:#e8f1ff;color:#1756a9;border-radius:20px;padding:3px 9px;font-size:12px}details summary{cursor:pointer;color:#1557a4;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:#f4f6fa;padding:12px;border-radius:7px;font:12px/1.5 monospace} .empty{padding:24px;color:#66788f}
        </style></head><body><main><div class="top"><div><h1>HTML 기본값 리포트</h1><p>프로젝트별 커스텀 필드의 HTML 포함 기본값 후보입니다. 전역 컨텍스트는 여러 프로젝트에 표시될 수 있습니다.</p></div><div class="metrics"><div class="metric">프로젝트<strong id="projectCount">0</strong></div><div class="metric">필드<strong id="fieldCount">0</strong></div><div class="metric">결과<strong id="rowCount">0</strong></div></div></div><div class="toolbar"><input id="search" type="search" placeholder="프로젝트, 필드, ID 검색" aria-label="프로젝트 또는 필드 검색"><span class="hint" id="visibleCount"></span></div><div class="panel"><table>''')
        output.write('''<script>document.addEventListener("DOMContentLoaded",()=>{const rows=[...document.querySelectorAll("tr.result")], q=document.getElementById("search");document.getElementById("rowCount").textContent=rows.length;document.getElementById("projectCount").textContent=new Set(rows.map(r=>r.dataset.project)).size;document.getElementById("fieldCount").textContent=new Set(rows.map(r=>r.dataset.field)).size;function filter(){let n=0;for(const r of rows){const show=r.dataset.search.includes(q.value.toLowerCase().trim());r.hidden=!show;if(show)n++}document.getElementById("visibleCount").textContent=`${n}건 표시`}q.addEventListener("input",filter);filter()});</script>''')
        return
    output.write("<!DOCTYPE html><html><head>")
    output.write('<meta charset="UTF-8"/><style>')
    output.write('table {border: medium solid #6495ed;border-collapse: collapse;width: 100%;}'
                 'th{font-family: monospace;border: thin solid #6495ed;padding: 5px;background-color: #D0E3FA;}'
                 'th{text-align: left;}td{font-family: sans-serif;border: thin solid #6495ed;padding: 5px;text-align: center;}'
                 '.odd{background:#e8edff;}img{padding:5px; border:solid; border-color: #dddddd #aaaaaa #aaaaaa #dddddd;'
                 'border-width: 1px 2px 2px 1px; background-color:white;}</style>')
    output.write("</head><body><table>\n")
    output.write('<tr><th colspan="1"><pre><code>')
    output.write(html.escape(raw_query))
    output.write("</code></pre></th></tr>\n")


def write_html_footer(output, raw_query):
    output.write('</table></div></main></body></html>\n' if is_default_values_report(raw_query)
                 else '</table></body></html>\n')


def _fast_escape_cell(value) -> str:
    """Fast HTML text escaping for non-attribute table cells (quote=False)."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    if "&" in s or "<" in s or ">" in s:
        return html.escape(s, quote=False)
    return s


def format_html_row(row, headers, index) -> str:
    """Formats a single HTML table row into a string with fast escaping (quote=False for <td> content)."""
    if "default_value_xml" in headers:
        def esc_attr(key):
            val = row.get(key)
            return html.escape("" if val is None else str(val), quote=True)

        def esc_text(key):
            return _fast_escape_cell(row.get(key))

        search = " ".join(str(row.get(k) or "") for k in ("project_key", "project_name", "field_id", "field_name", "context_name", "context_id")).lower()
        return (
            f'<tr class="result" data-project="{esc_attr("project_key")}" data-field="{esc_attr("field_id")}" data-search="{html.escape(search, quote=True)}">'
            f'<td><strong>{esc_text("project_key")}</strong><span class="sub">{esc_text("project_name")}</span></td>'
            f'<td><strong>{esc_text("field_name")}</strong><span class="sub">{esc_text("field_id")}</span><span class="sub">{esc_text("field_type")}</span></td>'
            f'<td><span class="badge">{esc_text("context_scope")}</span><span class="sub">{esc_text("context_name")}</span><span class="sub">컨텍스트 ID: {esc_text("context_id")} · 설정 ID: {esc_text("field_configuration_id")}</span></td>'
            f'<td>{esc_text("detection_type")}<span class="sub">기본값 ID: {esc_text("default_value_id")}</span></td>'
            f'<td><details><summary>원본 XML 보기</summary><pre>{esc_text("default_value_xml")}</pre></details></td></tr>\n'
        )
    row_class = ' class="odd"' if index % 2 == 0 else ""
    cells = []
    for column in headers:
        value = row.get(column)
        if column == "content":
            if value is None:
                escaped_value = ""
            else:
                s = value if isinstance(value, str) else str(value)
                escaped_value = html.escape(s, quote=False).replace("\n", "<br>\n")
        else:
            escaped_value = _fast_escape_cell(value)
        cells.append(escaped_value)
    return f"<tr{row_class}><td>{'</td><td>'.join(cells)}</td></tr>\n"


def format_jsonl_row(row, headers) -> str:
    """Formats a single row dictionary into a JSONL line."""
    payload = {}
    for column in headers:
        val = row.get(column)
        payload[column.lower()] = "" if val is None else (val if isinstance(val, str) else str(val))
    return json.dumps(payload, ensure_ascii=False) + "\n"


def write_html_row(output, row, headers, index):
    """Writes a single HTML table row directly to output file stream with minimal memory overhead."""
    output.write(format_html_row(row, headers, index))


def write_html_rows(output, rows, headers, start_index):
    """Writes multiple HTML table rows in a single buffered write call."""
    if not rows:
        return
    output.write("".join(format_html_row(row, headers, idx) for idx, row in enumerate(rows, start=start_index)))


def _compact_issue_id(ikey: str) -> int:
    """Converts an issue key (e.g. 'PROJ-12345') into a compact integer to prevent memory bloat."""
    dash = ikey.rfind("-")
    if dash != -1:
        num_part = ikey[dash + 1:]
        if num_part.isdigit():
            return (hash(ikey[:dash]) << 32) ^ int(num_part)
    return hash(ikey)


def generate_project_index_html(output_dir, project_meta, total_projects_scanned, elapsed_time):
    """Generates an intuitive, fast-loading master index dashboard in output_dir/index.html."""
    index_file = os.path.join(output_dir, "index.html")
    projects_with_data = len(project_meta)
    total_records = sum(m["rows"] for m in project_meta.values())
    total_unique_issues = sum(len(m["issues"]) for m in project_meta.values())
    total_bytes = sum(m.get("size", 0) for m in project_meta.values())

    # Sort projects alphabetically by key
    sorted_projects = sorted(project_meta.items(), key=lambda x: x[0])

    rows_html = []
    for pkey, meta in sorted_projects:
        pname = html.escape(meta.get("name") or pkey)
        rows_count = meta["rows"]
        issues_count = len(meta["issues"])
        file_size_str = format_size(meta.get("size", 0))

        # Field breakdown badges
        field_badges = []
        for fld, cnt in meta["fields"].most_common():
            short_fld = fld.replace("Custom Field", "CF").replace("customfield_", "CF ")
            field_badges.append(f'<span class="badge badge-field">{html.escape(short_fld)}: {cnt:,}</span>')
        field_html = " ".join(field_badges) if field_badges else "-"

        # Detection breakdown badges
        det_badges = []
        for dt, cnt in meta["detections"].most_common():
            cls = "badge-att" if dt == "Attachment" else "badge-ren"
            det_badges.append(f'<span class="badge {cls}">{html.escape(dt)}: {cnt:,}</span>')
        det_html = " ".join(det_badges) if det_badges else "-"

        rows_html.append(f"""
        <tr>
            <td><a class="pkey-link" href="{pkey}.html" target="_blank">{pkey}</a></td>
            <td>{pname}</td>
            <td style="text-align: right; font-weight: bold; color: #0052cc;">{rows_count:,}</td>
            <td style="text-align: right;">{issues_count:,}</td>
            <td>{field_html}</td>
            <td>{det_html}</td>
            <td style="text-align: right; color: #64748b;">{file_size_str}</td>
            <td style="text-align: center;"><a class="open-btn" href="{pkey}.html" target="_blank">열기 (Open)</a></td>
        </tr>
        """)

    index_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Jira JEditor Data Projects Index</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f8fafc; margin: 0; padding: 24px; color: #1e293b; }}
  .container {{ max-width: 1280px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); padding: 32px; border: 1px solid #e2e8f0; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #f1f5f9; padding-bottom: 20px; margin-bottom: 24px; }}
  h1 {{ margin: 0; color: #0f172a; font-size: 24px; display: flex; align-items: center; gap: 10px; }}
  .subtitle {{ color: #64748b; font-size: 14px; margin-top: 6px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .stat-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 18px; text-align: center; }}
  .stat-val {{ font-size: 26px; font-weight: 700; color: #0052cc; }}
  .stat-lbl {{ font-size: 13px; color: #64748b; margin-top: 4px; font-weight: 500; }}
  .search-wrapper {{ position: relative; margin-bottom: 20px; }}
  .search-box {{ width: 100%; box-sizing: border-box; padding: 12px 18px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 15px; outline: none; transition: border-color 0.2s; }}
  .search-box:focus {{ border-color: #0052cc; box-shadow: 0 0 0 3px rgba(0,82,204,0.15); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ background: #f1f5f9; padding: 12px 14px; text-align: left; border-bottom: 2px solid #cbd5e1; font-weight: 600; color: #334155; }}
  td {{ padding: 12px 14px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }}
  tr:hover {{ background: #f8fafc; }}
  .pkey-link {{ font-family: ui-monospace, monospace; font-weight: 700; color: #0052cc; text-decoration: none; padding: 4px 8px; background: #e0f2fe; border-radius: 6px; }}
  .pkey-link:hover {{ background: #bae6fd; text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 2px 8px; font-size: 11px; border-radius: 12px; margin: 2px; font-weight: 500; }}
  .badge-field {{ background: #e2e8f0; color: #334155; }}
  .badge-att {{ background: #fee2e2; color: #991b1b; }}
  .badge-ren {{ background: #fef3c7; color: #92400e; }}
  .open-btn {{ display: inline-block; padding: 6px 14px; background: #0052cc; color: #ffffff; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 600; }}
  .open-btn:hover {{ background: #0747a6; }}
  .empty-state {{ text-align: center; padding: 48px; color: #64748b; font-size: 16px; }}
</style>
<script>
function filterTable() {{
  const query = document.getElementById("search").value.toLowerCase();
  const rows = document.querySelectorAll("#project-table tbody tr");
  let visible = 0;
  rows.forEach(r => {{
    const text = r.innerText.toLowerCase();
    if (text.includes(query)) {{
      r.style.display = "";
      visible++;
    }} else {{
      r.style.display = "none";
    }}
  }});
  document.getElementById("visible-count").innerText = visible;
}}
</script>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>📁 JEditor 프로젝트별 데이터 목차 (Project Index)</h1>
      <div class="subtitle">총 {total_projects_scanned:,}개 대상 프로젝트 조회 완료 • 소요 시간: {format_time(elapsed_time)}</div>
    </div>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-val">{projects_with_data:,} / {total_projects_scanned:,}</div>
      <div class="stat-lbl">JEditor 발견 프로젝트</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">{total_records:,}</div>
      <div class="stat-lbl">추출된 총 JEditor 레코드</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">{total_unique_issues:,}</div>
      <div class="stat-lbl">영향받는 고유 이슈 수</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">{format_size(total_bytes)}</div>
      <div class="stat-lbl">전체 파일 용량 합계</div>
    </div>
  </div>

  <div class="search-wrapper">
    <input type="text" id="search" class="search-box" placeholder="🔍 프로젝트 키 또는 이름으로 검색... (Search project key or name)" onkeyup="filterTable()"/>
  </div>

  <div style="font-size: 13px; color: #64748b; margin-bottom: 8px;">
    표시 중인 프로젝트: <span id="visible-count" style="font-weight: bold; color: #0052cc;">{projects_with_data}</span>개
  </div>

  <table id="project-table">
    <thead>
      <tr>
        <th style="width: 130px;">프로젝트 키</th>
        <th>프로젝트명</th>
        <th style="width: 100px; text-align: right;">레코드 수</th>
        <th style="width: 100px; text-align: right;">고유 이슈</th>
        <th>필드 분포</th>
        <th>감지 유형</th>
        <th style="width: 100px; text-align: right;">용량</th>
        <th style="width: 110px; text-align: center;">파일 열기</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html) if rows_html else '<tr><td colspan="8" class="empty-state">JEditor 데이터가 발견된 프로젝트가 없습니다.</td></tr>'}
    </tbody>
  </table>
</div>
</body>
</html>
"""
    with open(index_file, "w", encoding="utf-8") as f:
        f.write(index_content)
    return index_file


def print_summary(total_projects, projects_with_data, total_rows, unique_issues,
                  field_counts, detection_counts, elapsed_time, output_info, is_split=False):
    """Prints a clear, structured execution summary table."""
    print("\n" + "=" * 68)
    print("📊 JEditor Query Execution Summary")
    print("=" * 68)
    print(f"  • Total Projects Processed : {total_projects:,}")
    print(f"  • Projects with JEditor    : {len(projects_with_data):,} / {total_projects:,}")
    print(f"  • Total Records Extracted  : {total_rows:,}")
    print(f"  • Unique Issues Found      : {len(unique_issues):,}")
    print(f"  • Total Elapsed Time       : {format_duration(elapsed_time)} ({format_time(elapsed_time)})")
    if is_split:
        print(f"  • Output Directory         : {output_info['dir']} ({len(projects_with_data)} individual HTML files)")
        print(f"  • Master Index Dashboard   : {output_info['index_file']}")
    else:
        file_size_str = "0 B"
        if os.path.exists(output_info["file"]):
            file_size_str = format_size(os.path.getsize(output_info["file"]))
        print(f"  • Output File Generated    : {output_info['file']} ({file_size_str})")
    print("-" * 68)
    print("  📌 Breakdown by Field:")
    for field, count in field_counts.most_common():
        pct = (count / total_rows * 100) if total_rows > 0 else 0
        print(f"     - {field:<22} : {count:>7,} ({pct:5.1f}%)")
    print("-" * 68)
    print("  📌 Breakdown by Detection Type:")
    for dtype, count in detection_counts.most_common():
        pct = (count / total_rows * 100) if total_rows > 0 else 0
        print(f"     - {dtype:<22} : {count:>7,} ({pct:5.1f}%)")
    print("=" * 68 + "\n")


def prompt_choice(title, options, default=0):
    """Displays a numbered menu and returns the selected option's value."""
    print(f"\n  {title}")
    print("  " + "-" * 55)
    for i, (label, _) in enumerate(options, 1):
        default_mark = " (기본값 / Recommended)" if i - 1 == default else ""
        print(f"  [{i}] {label}{default_mark}")
    print()

    while True:
        try:
            suffix = f" [1-{len(options)}, Enter={options[default][0]}]: "
            raw = input("  선택 (Choice)" + suffix).strip()
        except EOFError:
            print(f"  → 기본값 [{options[default][0]}] 자동 선택")
            return options[default][1]

        if raw == "":
            print(f"  → [{options[default][0]}] 선택됨")
            return options[default][1]

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                print(f"  → [{options[idx][0]}] 선택됨")
                return options[idx][1]

        print(f"  [!] 1부터 {len(options)} 사이의 숫자를 입력해 주세요.")


def prompt_input(title, default=""):
    """Prompts for free-form input with a default value."""
    try:
        suffix = f" [기본값: {default}]: " if default else ": "
        raw = input(f"  {title}{suffix}").strip()
        if not raw:
            return default
        return raw
    except EOFError:
        return default


def run_interactive_wizard(default_sql="jeditor_mysql_queries.sql"):
    """Guides the user through an interactive step-by-step configuration workflow."""
    print("\n" + "=" * 68)
    print("🐘 Jira JEditor 데이터 추출 마법사 (Interactive Query Runner)")
    print("=" * 68)

    # 1. SQL File Selection
    sql_options = [
        ("jeditor_mysql_queries.sql (메인: 본문/댓글/커스텀필드 JEditor 추출)", "jeditor_mysql_queries.sql"),
        ("jeditor_template_queries.sql (JEditor 프로젝트 템플릿 AO_BF4748 추출)", "jeditor_template_queries.sql"),
        ("직접 SQL 파일 경로 입력 (Custom SQL file path)", "CUSTOM")
    ]
    sql_choice = prompt_choice("Step 1/5. 실행할 SQL 파일 선택", sql_options, default=0)
    if sql_choice == "CUSTOM":
        sql_file = prompt_input("SQL 파일 경로 입력", default=default_sql)
    else:
        sql_file = sql_choice

    if not os.path.exists(sql_file):
        print(f"\n  [ERROR] SQL 파일 '{sql_file}'을(를) 찾을 수 없습니다.")
        sys.exit(1)

    with open(sql_file, "r", encoding="utf-8") as sf:
        raw_query = sf.read()
    cleaned_query = remove_sql_comments(raw_query)
    sql_keys = extract_project_keys_from_query(cleaned_query)

    # Detect projects.txt
    default_projects_txt = None
    for candidate in ["../projects.txt", "projects.txt"]:
        if os.path.exists(candidate):
            default_projects_txt = candidate
            break

    file_keys = load_project_keys_from_file(default_projects_txt) if default_projects_txt else []

    # 2. Target Projects Selection
    proj_options = []
    if file_keys:
        proj_options.append((f"전체 프로젝트 조회 ({default_projects_txt}: {len(file_keys)}개 프로젝트)", "ALL_FILE"))
    if sql_keys:
        preview = ", ".join(sql_keys[:3])
        if len(sql_keys) > 3:
            preview += f", ... (+{len(sql_keys) - 3}개)"
        proj_options.append((f"SQL 파일에 정의된 프로젝트 키 사용 ({len(sql_keys)}개: {preview})", "SQL_CTE"))
    proj_options.append(("특정 프로젝트만 직접 입력 (쉼표 구분 필터링, 예: AA, BDD, CFMT)", "FILTER"))

    proj_choice = prompt_choice("Step 2/5. 대상 프로젝트 선택", proj_options, default=0)

    if proj_choice == "ALL_FILE":
        project_keys = file_keys
    elif proj_choice == "SQL_CTE":
        project_keys = sql_keys
    elif proj_choice == "FILTER":
        custom_keys_str = prompt_input("프로젝트 키 목록 입력 (쉼표 구분, 예: AA,BDD)", default="CFMT")
        project_keys = [k.strip().upper() for k in custom_keys_str.split(",") if k.strip()]

    total_projects = len(project_keys)
    print(f"  → 대상 프로젝트 수: {total_projects}개")

    # 3. Batch Size & DB Load Selection
    batch_options = [
        (f"1개씩 분할 (초저부하 모드: DB CPU/메모리 부하 최소화, 약 {total_projects}개 배치)", 1),
        (f"5개씩 분할 (소형 배치: 타임아웃 방지 및 안정적 추출, 약 {(total_projects + 4) // 5}개 배치)", 5),
        (f"10개씩 분할 (표준 배치: 권장 기본값, 약 {(total_projects + 9) // 10}개 배치)", 10),
        ("직접 입력 (Custom batch size)", "CUSTOM")
    ]
    batch_choice = prompt_choice("Step 3/5. 페이지네이션 배치 크기 설정", batch_options, default=2)
    if batch_choice == "CUSTOM":
        raw_bs = prompt_input("배치당 프로젝트 수 입력 (1 이상 정수)", default="10")
        try:
            batch_size = max(1, int(raw_bs))
        except ValueError:
            batch_size = 10
    else:
        batch_size = batch_choice

    default_cd = "2" if batch_size == 1 else "0"
    raw_cd = prompt_input("  배치 간 DB 쿨다운 대기시간(초) [부하 최소화 권장: 1~3초, 0=대기 없음]", default=default_cd)
    try:
        cooldown_seconds = max(0.0, float(raw_cd))
    except ValueError:
        cooldown_seconds = 0.0

    total_batches = (total_projects + batch_size - 1) // batch_size if total_projects > 0 else 1

    # 4. Execution Range Selection
    range_options = [
        (f"전체 실행 (배치 1 ~ {total_batches})", "ALL"),
        ("테스트 실행: 처음 N개 배치만 실행 (빠른 샘플 검증)", "TEST"),
        ("이어하기 / 범위 지정: 특정 배치 번호부터 시작", "RESUME")
    ]
    range_choice = prompt_choice("Step 4/5. 실행 범위 선택", range_options, default=0)

    start_batch = 1
    max_batches = None

    if range_choice == "TEST":
        default_test_n = min(2, total_batches)
        raw_test = prompt_input(f"실행할 테스트 배치 수 (1 ~ {total_batches})", default=str(default_test_n))
        try:
            max_batches = max(1, min(int(raw_test), total_batches))
        except ValueError:
            max_batches = default_test_n
    elif range_choice == "RESUME":
        raw_start = prompt_input(f"시작할 배치 번호 (1 ~ {total_batches})", default="1")
        try:
            start_batch = max(1, min(int(raw_start), total_batches))
        except ValueError:
            start_batch = 1
        raw_count = prompt_input("실행할 배치 수 (Enter=끝까지)", default="")
        if raw_count:
            try:
                max_batches = max(1, int(raw_count))
            except ValueError:
                max_batches = None

    # 5. Output Storage Mode Selection (Single JSONL vs Project Split vs Single HTML)
    storage_options = [
        ("단일 JSONL 파일 저장 (data.jsonl - 대용량 초고속 스트리밍 / Step 3·4 권장)", "SINGLE_JSONL"),
        ("프로젝트별 분할 저장 (data_projects/{PKEY}.html + index.html 생성)", "SPLIT"),
        ("단일 HTML 파일 저장 (data.html - 기존 테이블 방식)", "SINGLE_HTML")
    ]
    default_storage_idx = 1 if "template" in os.path.basename(sql_file).lower() else 0
    storage_choice = prompt_choice("Step 5/5. 결과 저장 방식 선택", storage_options, default=default_storage_idx)

    if storage_choice == "SPLIT":
        split_projects = True
        rec_dir = get_default_output_dir(sql_file)
        output_dir = prompt_input("프로젝트 파일 저장 디렉토리", default=rec_dir)
        output_file = None
    elif storage_choice == "SINGLE_HTML":
        split_projects = False
        output_dir = None
        rec_output = get_default_output_file(sql_file)
        if rec_output.endswith(".jsonl"):
            rec_output = rec_output[:-6] + ".html"
        output_file = prompt_input("출력 HTML 파일명", default=rec_output)
    else:
        split_projects = False
        output_dir = None
        rec_output = get_default_output_file(sql_file)
        output_file = prompt_input("출력 파일명 (.jsonl 권장, .html 가능)", default=rec_output)

    # Confirmation
    calc_run_batches = max_batches if max_batches else (total_batches - start_batch + 1)
    end_batch = start_batch + calc_run_batches - 1

    preview_keys = ", ".join(project_keys[:5])
    if len(project_keys) > 5:
        preview_keys += f", ... (+{len(project_keys) - 5}개 더 있음)"

    print("\n" + "=" * 68)
    print("📋 실행 설정 요약 (Configuration Summary)")
    print("=" * 68)
    print(f"  • SQL 파일 경로   : {sql_file}")
    print(f"  • 대상 프로젝트   : 총 {total_projects}개 [{preview_keys}]")
    print(f"  • 배치 분할 크기 : {batch_size}개 프로젝트 / 배치")
    if cooldown_seconds > 0:
        print(f"  • 배치 간 쿨다운 : {cooldown_seconds:g}초 대기 (DB 부하 완화)")
    print(f"  • 실행 배치 범위 : 배치 {start_batch} ~ {end_batch} (총 {calc_run_batches}개 배치)")
    if split_projects:
        print(f"  • 데이터 저장 방식 : 프로젝트별 분할 저장 ({output_dir}/{{PKEY}}.html)")
        print(f"  • 대시보드 목차   : {output_dir}/index.html (자동 생성)")
    else:
        print(f"  • 데이터 저장 방식 : 단일 파일 통합 저장 ({output_file})")
    print("=" * 68)

    try:
        confirm = input("\n  위 설정으로 쿼리 실행을 시작하시겠습니까? [Y/n]: ").strip().lower()
    except EOFError:
        confirm = "y"

    if confirm in ["n", "no"]:
        print("\n  [INFO] 사용자에 의해 실행이 취소되었습니다.\n")
        sys.exit(0)

    return {
        "sql_file": sql_file,
        "output_file": output_file,
        "output_dir": output_dir,
        "split_projects": split_projects,
        "project_keys": project_keys,
        "project_batch_size": batch_size,
        "cooldown_seconds": cooldown_seconds,
        "start_batch": start_batch,
        "max_batches": max_batches,
        "html_batch_size": 2000,
        "append": False,
        "raw_query": raw_query,
        "cleaned_query": cleaned_query
    }


def main():
    script_start_time = time.time()
    parser = argparse.ArgumentParser(
        description="Extract Jira JEditor data into HTML using project pagination",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("sql_file", nargs="?", default="jeditor_mysql_queries.sql",
                        help="Path to SQL query file (default: jeditor_mysql_queries.sql)")
    parser.add_argument("output_file", nargs="?", default=None,
                        help="Path to output HTML file (default: templates_output.html for template queries, data.html for main queries)")
    parser.add_argument("--project-batch-size", "-b", type=int, default=10,
                        help="Number of projects per pagination batch (default: 10)")
    parser.add_argument("--cooldown-seconds", "-c", "--cooldown", type=float, default=0.0,
                        help="Cool-down delay in seconds between pagination batches to let DB cool down (default: 0)")
    parser.add_argument("--html-batch-size", type=int, default=2000,
                        help="Number of rows per streaming fetch/write buffer (default: 2000)")
    parser.add_argument("--projects", "-p", type=str,
                        help="Path to projects.txt file (default: auto-detected ../projects.txt)")
    parser.add_argument("--start-batch", type=int, default=1,
                        help="1-indexed batch number to start execution from (default: 1)")
    parser.add_argument("--max-batches", type=int, default=None,
                        help="Limit execution to a maximum number of batches (for testing)")
    parser.add_argument("--filter-projects", type=str, default=None,
                        help="Comma-separated list of specific project keys to run (e.g. 'AA,BDD')")
    parser.add_argument("--split-projects", action="store_true", default=None,
                        help="Split output into individual project HTML files (data_projects/{PKEY}.html)")
    parser.add_argument("--single-file", action="store_true",
                        help="Force single file output mode (data.html)")
    parser.add_argument("--output-dir", type=str, default="data_projects",
                        help="Directory to save split project HTML files (default: data_projects)")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing HTML file instead of overwriting")
    parser.add_argument("--no-prompt", action="store_true",
                        help="Bypass interactive menu and run directly with parameters")

    args = parser.parse_args()

    # Determine execution mode: Interactive wizard is primary when no non-interactive flags passed
    is_interactive = not args.no_prompt and len(sys.argv) == 1

    if is_interactive:
        cfg = run_interactive_wizard(default_sql=args.sql_file)
        sql_file = cfg["sql_file"]
        output_file = cfg["output_file"]
        output_dir = cfg["output_dir"]
        split_projects = cfg["split_projects"]
        project_keys = cfg["project_keys"]
        project_batch_size = cfg["project_batch_size"]
        cooldown_seconds = cfg.get("cooldown_seconds", 0.0)
        start_batch_num = cfg["start_batch"]
        max_batches = cfg["max_batches"]
        html_batch_size = cfg["html_batch_size"]
        append_mode = cfg["append"]
        raw_query = cfg["raw_query"]
        cleaned_query = cfg["cleaned_query"]
    else:
        # Parameter-based non-interactive mode
        print("=== Jira SQL to HTML Query Runner (Paginated & Streaming) ===")
        if args.project_batch_size <= 0:
            parser.error("--project-batch-size must be greater than 0.")
        if args.html_batch_size <= 0:
            parser.error("--html-batch-size must be greater than 0.")
        if args.start_batch < 1:
            parser.error("--start-batch must be 1 or greater.")
        if args.cooldown_seconds < 0:
            parser.error("--cooldown-seconds must be 0 or greater.")

        sql_file = args.sql_file
        if not os.path.exists(sql_file):
            print(f"[ERROR] SQL file '{sql_file}' not found.")
            sys.exit(1)

        split_projects = args.split_projects if args.split_projects is not None else False
        if args.single_file:
            split_projects = False

        if args.output_dir == "data_projects" and "template" in os.path.basename(sql_file).lower():
            output_dir = "template_projects"
        else:
            output_dir = args.output_dir

        if args.output_file:
            output_file = args.output_file
        else:
            output_file = get_default_output_file(sql_file)
            print(f"[INFO] 출력 파일명이 생략되어 자동으로 '{output_file}'(으)로 지정되었습니다.")

        print(f"[INFO] Reading query file: {sql_file}")
        with open(sql_file, "r", encoding="utf-8") as sf:
            raw_query = sf.read()

        cleaned_query = remove_sql_comments(raw_query)

        project_keys = []
        if args.filter_projects:
            project_keys = [k.strip().upper() for k in args.filter_projects.split(",") if k.strip()]
            print(f"[INFO] Filtered by command-line project keys ({len(project_keys)} keys): {', '.join(project_keys)}")
        elif args.projects:
            project_keys = load_project_keys_from_file(args.projects)
            print(f"[INFO] Loaded {len(project_keys)} project keys from: {args.projects}")
        else:
            keys_in_sql = extract_project_keys_from_query(cleaned_query)
            default_projects_txt = None
            for candidate in ["../projects.txt", "projects.txt"]:
                if os.path.exists(candidate):
                    default_projects_txt = candidate
                    break

            if len(keys_in_sql) > 1:
                project_keys = keys_in_sql
                print(f"[INFO] Extracted {len(project_keys)} project keys from SQL CTE.")
            elif default_projects_txt:
                project_keys = load_project_keys_from_file(default_projects_txt)
                print(f"[INFO] Auto-detected {len(project_keys)} project keys from: {default_projects_txt}")
            elif keys_in_sql:
                project_keys = keys_in_sql
                print(f"[INFO] Using {len(project_keys)} project key(s) from SQL: {', '.join(project_keys)}")
            else:
                print("[INFO] No project keys found; running single un-batched query.")

        project_batch_size = args.project_batch_size
        cooldown_seconds = max(0.0, args.cooldown_seconds)
        start_batch_num = args.start_batch
        max_batches = args.max_batches
        html_batch_size = args.html_batch_size
        append_mode = args.append

    if not env_loaded and not os.getenv("JIRA_DB_HOST"):
        print("[INFO] 'env' 파일이 감지되지 않아 시스템 환경 변수를 확인합니다.")

    db_config = get_db_config()
    if not db_config["host"] or not db_config["user"] or not db_config["database"]:
        print("\n[ERROR] Missing Database credentials in your 'env' configuration.")
        print("Please configure the following keys in your env file:")
        print("  JIRA_DB_HOST=your_host")
        print("  JIRA_DB_PORT=3306")
        print("  JIRA_DB_USER=your_user")
        print("  JIRA_DB_PASSWORD=your_password")
        print("  JIRA_DB_NAME=your_database_name\n")
        sys.exit(1)

    # Create pagination batches (store only batch_idx & chunk_keys; build SQL lazily per batch to save memory)
    query_batches = []
    if project_keys:
        total_projects = len(project_keys)
        batch_idx = 1
        for start in range(0, total_projects, project_batch_size):
            chunk_keys = project_keys[start:start + project_batch_size]
            query_batches.append((batch_idx, chunk_keys))
            batch_idx += 1
    else:
        query_batches.append((1, []))
        total_projects = 0

    total_batches = len(query_batches)

    if start_batch_num > total_batches:
        print(f"[ERROR] --start-batch ({start_batch_num}) exceeds total batches ({total_batches}).")
        sys.exit(1)

    active_batches = query_batches[start_batch_num - 1:]
    if max_batches:
        active_batches = active_batches[:max_batches]

    if split_projects:
        print(f"\n[INFO] Output Target: Split by Project into '{output_dir}/{{PKEY}}.html'")
        os.makedirs(output_dir, exist_ok=True)
    else:
        print(f"\n[INFO] Output Target: Single file '{output_file}'")

    if total_projects > 0:
        print(f"[INFO] Pagination: {total_projects} projects in {total_batches} batch(es) "
              f"({project_batch_size} projects/batch).")
        if start_batch_num > 1 or max_batches:
            print(f"[INFO] Active run: batches {active_batches[0][0]} to {active_batches[-1][0]} "
                  f"({len(active_batches)} batch(es) to execute).")
    else:
        print("[INFO] Executing single standard query.")

    # Connect to MySQL Database
    if pymysql is None:
        print("\n[CRITICAL ERROR] 'pymysql' package is not installed.")
        print("Please activate your virtual environment or install pymysql:")
        print("  source ../venv/bin/activate")
        print("  pip install pymysql\n")
        sys.exit(1)

    print(f"[INFO] Connecting to database {db_config['host']}:{db_config['port']}/{db_config['database']}...")
    try:
        connection = pymysql.connect(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.SSDictCursor,
            connect_timeout=20
        )
        print("[INFO] Database connected successfully.")

        # Optimize MySQL session for minimal DB load and safe long-running streaming
        try:
            with connection.cursor() as session_cur:
                session_cur.execute("SET SESSION TRANSACTION READ ONLY;")
                session_cur.execute("SET SESSION transaction_isolation = 'READ-COMMITTED';")
                session_cur.execute("SET sql_notes = 0;")
                session_cur.execute("SET SESSION net_read_timeout = 300, net_write_timeout = 300;")
        except Exception:
            try:
                with connection.cursor() as session_cur:
                    session_cur.execute("SET SESSION tx_isolation = 'READ-COMMITTED';")
                    session_cur.execute("SET sql_notes = 0;")
            except Exception:
                pass
    except Exception as e:
        print(f"[CRITICAL ERROR] Database connection failed: {e}")
        sys.exit(1)

    # Metrics & State tracking
    total_rows = 0
    headers = None
    projects_with_data = set()
    unique_issues = set()
    field_counts = Counter()
    detection_counts = Counter()

    # Project-split tracking: pkey -> {"name": str, "rows": int, "issues": set, "fields": Counter, "detections": Counter, "file": str}
    project_meta = {}
    project_files_initialized = set()

    overall_start_time = time.time()
    batch_durations = []
    single_output_handle = None
    single_is_jsonl = not split_projects and is_jsonl_path(output_file)

    try:
        if not split_projects:
            write_mode = "a" if (append_mode and os.path.exists(output_file)) else "w"
            single_output_handle = open(output_file, write_mode, buffering=4 * 1024 * 1024, encoding="utf-8")
            if write_mode == "w" and not single_is_jsonl:
                write_html_header(single_output_handle, raw_query)

        print("\n" + "-" * 75)
        print("🚀 Starting paginated execution...")
        print("-" * 75)

        total_active_batches = len(active_batches)
        footer_state = {
            "batch_idx": 0,
            "phase": "쿼리 준비 중..",
            "detail": "",
            "eta_override": None,
        }
        ticker_stop = threading.Event()

        def update_footer(cur_batch_idx: int | None = None, phase: str | None = None,
                          detail: str | None = None, eta_override: str | None = None):
            if cur_batch_idx is not None:
                footer_state["batch_idx"] = cur_batch_idx
            if phase is not None:
                footer_state["phase"] = phase
            if detail is not None:
                footer_state["detail"] = detail
            if eta_override is not None or phase is not None:
                footer_state["eta_override"] = eta_override

            if not status_bar.enabled:
                return

            b_idx = footer_state["batch_idx"]
            cur_phase = footer_state["phase"]
            cur_detail = footer_state["detail"]
            pct = min(100.0, (b_idx / max(1, total_active_batches)) * 100.0)
            elapsed = max(time.time() - overall_start_time, 0.001)
            elapsed_str = format_time(elapsed)
            rate = total_rows / elapsed

            if footer_state["eta_override"] is not None:
                eta_val = footer_state["eta_override"]
            elif batch_durations:
                avg_d = sum(batch_durations) / len(batch_durations)
                rem_b = max(0, total_active_batches - b_idx)
                # Estimate total expected time = elapsed + remaining
                rem_sec = rem_b * avg_d
                total_est_sec = elapsed + rem_sec
                eta_val = format_time(total_est_sec) if rem_b > 0 else elapsed_str
            else:
                eta_val = "계산 중..."

            detail_suffix = f" ({cur_detail})" if cur_detail else ""
            status_bar.update(
                f"[{cur_phase}]{detail_suffix} | "
                f"진행률 {pct:5.1f}% ({b_idx}/{total_active_batches}) | "
                f"진행 시간: {elapsed_str} / 예상 소요 시간: {eta_val} | "
                f"누적 {total_rows:,}건 (이슈 {len(unique_issues):,}건, {rate:.0f}건/s)"
            )

        def _live_ticker():
            while not ticker_stop.wait(0.5):
                update_footer()

        with TerminalStatus() as status_bar:
            ticker_thread = threading.Thread(target=_live_ticker, daemon=True)
            ticker_thread.start()
            try:
                update_footer(0, "쿼리 준비 중..", "")

                for i, (b_num, b_keys) in enumerate(active_batches, start=1):
                    if cooldown_seconds > 0 and i > 1:
                        update_footer(i - 1, "DB 쿨다운 대기 중..", f"{cooldown_seconds:g}초")
                        if not status_bar.enabled:
                            print(f"  ☕ DB 쿨다운 대기 중 ({cooldown_seconds:g}초)...", end="", flush=True)
                        time.sleep(cooldown_seconds)
                        if not status_bar.enabled:
                            print("\r" + " " * 45 + "\r", end="", flush=True)

                    b_query = build_batch_query(cleaned_query, b_keys) if b_keys else cleaned_query
                    batch_start_time = time.time()
                    progress_pct = (b_num / total_batches) * 100

                    if b_keys:
                        first_few = ", ".join(b_keys[:4])
                        if len(b_keys) > 4:
                            first_few += f", ... (+{len(b_keys) - 4} more)"
                        p_start = (b_num - 1) * project_batch_size + 1
                        p_end = min(b_num * project_batch_size, total_projects)
                        batch_title = f"[Batch {b_num}/{total_batches}] ({progress_pct:4.1f}%) Projects {p_start}-{p_end}/{total_projects}: [{first_few}]"
                        short_target = ", ".join(b_keys[:2]) + (f"+{len(b_keys)-2}" if len(b_keys) > 2 else "")
                    else:
                        batch_title = f"[Batch {b_num}/{total_batches}] ({progress_pct:4.1f}%) Single Query"
                        short_target = "전체"

                    print(batch_title)
                    update_footer(i - 1, "쿼리 추출 중..", short_target)
                    if not status_bar.enabled:
                        print("  -> 쿼리 추출 중...", end="", flush=True)

                    try:
                        connection.ping(reconnect=True)
                    except Exception:
                        pass

                    with connection.cursor() as cursor:
                        cursor.execute(b_query)

                        batch_rows = 0
                        batch_write_sec = 0.0
                        batch_proj_counts = Counter()
                        active_streams = {}

                        def get_project_stream(pk, pname_def):
                            if pk in active_streams:
                                return active_streams[pk]

                            p_file = os.path.join(output_dir, f"{pk}.html")
                            is_new = pk not in project_files_initialized
                            project_files_initialized.add(pk)
                            projects_with_data.add(pk)

                            mode = "w" if is_new else "a"
                            stream = open(p_file, mode, buffering=512 * 1024, encoding="utf-8")

                            if is_new:
                                project_meta[pk] = {
                                    "name": pname_def,
                                    "rows": 0,
                                    "issues": set(),
                                    "fields": Counter(),
                                    "detections": Counter(),
                                    "file": p_file
                                }
                                write_html_header(stream, b_query)
                                write_report_columns(stream, headers)

                            active_streams[pk] = stream
                            return stream

                        write_queue = queue.Queue(maxsize=8)
                        writer_errors = []

                        def _writer_worker():
                            nonlocal headers, total_rows, batch_rows, batch_write_sec
                            single_chunk_buf = []
                            proj_chunk_bufs = defaultdict(list)
                            try:
                                while True:
                                    rows_chunk = write_queue.get()
                                    if rows_chunk is None:
                                        break
                                    w_start = time.time()
                                    last_pkey = short_target
                                    update_footer(i - 1, "파일 쓰기 중..", f"{last_pkey} +{batch_rows:,}건")
                                    for row in rows_chunk:
                                        if headers is None:
                                            headers = list(row.keys())
                                            if single_output_handle and not single_is_jsonl:
                                                write_report_columns(single_output_handle, headers)

                                        pkey = sys.intern(str(row.get("project_key") or "UNKNOWN"))
                                        last_pkey = pkey
                                        pname = row.get("project_name") or pkey
                                        ikey = row.get("issue_key")
                                        fname = sys.intern(str(row.get("field_name"))) if row.get("field_name") else None
                                        dtype = sys.intern(str(row.get("detection_type"))) if row.get("detection_type") else None
                                        compact_iid = _compact_issue_id(ikey) if ikey else None

                                        if split_projects:
                                            get_project_stream(pkey, pname)
                                            proj_chunk_bufs[pkey].append(
                                                format_html_row(row, headers, project_meta[pkey]["rows"])
                                            )
                                            project_meta[pkey]["rows"] += 1
                                            if compact_iid is not None:
                                                project_meta[pkey]["issues"].add(compact_iid)
                                            if fname:
                                                project_meta[pkey]["fields"][fname] += 1
                                            if dtype:
                                                project_meta[pkey]["detections"][dtype] += 1
                                        else:
                                            if single_is_jsonl:
                                                single_chunk_buf.append(format_jsonl_row(row, headers))
                                            else:
                                                single_chunk_buf.append(
                                                    format_html_row(row, headers, total_rows)
                                                )
                                            projects_with_data.add(pkey)

                                        if compact_iid is not None:
                                            unique_issues.add(compact_iid)
                                        if fname:
                                            field_counts[fname] += 1
                                        if dtype:
                                            detection_counts[dtype] += 1

                                        row.clear()
                                        total_rows += 1
                                        batch_rows += 1
                                        batch_proj_counts[pkey] += 1

                                    if split_projects:
                                        for pk, lines_buf in proj_chunk_bufs.items():
                                            if lines_buf:
                                                active_streams[pk].write("".join(lines_buf))
                                                lines_buf.clear()
                                    elif single_chunk_buf:
                                        single_output_handle.write("".join(single_chunk_buf))
                                        single_chunk_buf.clear()

                                    rows_chunk.clear()
                                    batch_write_sec += (time.time() - w_start)
                                    if write_queue.empty():
                                        update_footer(i - 1, "쿼리 추출 중..", f"{last_pkey} +{batch_rows:,}건")
                                    else:
                                        update_footer(i - 1, "파일 쓰기 중..", f"{last_pkey} +{batch_rows:,}건")
                                    if not status_bar.enabled:
                                        print(f"\r  -> 추출/기록 중... {batch_rows:,}건 (누적: {total_rows:,}건)", end="", flush=True)
                            except BaseException as exc:
                                writer_errors.append(exc)

                        writer_thread = threading.Thread(target=_writer_worker, daemon=True)
                        writer_thread.start()
                        try:
                            while True:
                                if writer_errors:
                                    raise writer_errors[0]
                                rows_chunk = cursor.fetchmany(500)
                                if not rows_chunk:
                                    break
                                write_queue.put(rows_chunk)
                        finally:
                            while writer_thread.is_alive():
                                try:
                                    write_queue.put(None, timeout=0.1)
                                    break
                                except queue.Full:
                                    if writer_errors:
                                        try:
                                            write_queue.get_nowait()
                                        except queue.Empty:
                                            pass
                            writer_thread.join()
                            update_footer(i - 1, "파일 쓰기 중..", "버퍼 기록 중")
                            # Flush and close active project streams for this batch
                            if split_projects:
                                for s in active_streams.values():
                                    try:
                                        s.flush()
                                        s.close()
                                    except Exception:
                                        pass
                                active_streams.clear()
                            elif single_output_handle:
                                single_output_handle.flush()
                            if hasattr(cursor, '_finish_unbuffered_query'):
                                try:
                                    cursor._finish_unbuffered_query()
                                except Exception:
                                    pass
                        if writer_errors:
                            raise writer_errors[0]
                    del b_query
                    gc.collect()

                    batch_duration = time.time() - batch_start_time
                    batch_durations.append(batch_duration)

                    avg_duration = sum(batch_durations) / len(batch_durations)
                    remaining_batches = len(active_batches) - i
                    eta_seconds = remaining_batches * avg_duration
                    eta_str = "완료" if remaining_batches == 0 else format_time(eta_seconds)

                    if batch_rows > 0:
                        if split_projects:
                            proj_detail_parts = [f"{pk}({cnt})" for pk, cnt in batch_proj_counts.most_common(3)]
                            if len(batch_proj_counts) > 3:
                                proj_detail_parts.append(f"+{len(batch_proj_counts)-3} more")
                            proj_detail = " -> " + ", ".join(proj_detail_parts)
                        else:
                            proj_detail = ""
                        row_status = f"+{batch_rows:,} row(s) found{proj_detail}"
                    else:
                        row_status = "0 rows (clean)"

                    prefix = "  ✓ " if status_bar.enabled else "\r  ✓ "
                    db_sec = max(0.0, batch_duration - batch_write_sec)
                    print(f"{prefix}{row_status} in {batch_duration:.2f}s (DB {db_sec:.2f}s / 쓰기 {batch_write_sec:.2f}s) | Total: {total_rows:,} rows | 남은 시간: {eta_str}")
                    update_footer(i, "배치 완료", f"배치 {b_num} (+{batch_rows:,}건)")
            finally:
                ticker_stop.set()
                ticker_thread.join(timeout=1.0)

        # Finalize HTML files
        if split_projects:
            # Close all individual project HTML files
            for pkey, meta in project_meta.items():
                p_file = meta["file"]
                try:
                    with open(p_file, "a", encoding="utf-8") as pf:
                        write_html_footer(pf, raw_query)
                    meta["size"] = os.path.getsize(p_file)
                except Exception:
                    pass

            # Generate index.html dashboard
            index_path = generate_project_index_html(output_dir, project_meta, total_projects, time.time() - overall_start_time)
        else:
            if headers is None and single_output_handle and not single_is_jsonl:
                single_output_handle.write('<tr><td>No records found.</td></tr>\n')
            if single_output_handle:
                if not single_is_jsonl:
                    write_html_footer(single_output_handle, raw_query)
                single_output_handle.close()
                single_output_handle = None

        total_elapsed = time.time() - overall_start_time
        total_script_elapsed = time.time() - script_start_time
        print("-" * 75)
        print(f"[SUCCESS] All batches completed successfully in {format_duration(total_elapsed)} ({format_time(total_elapsed)}).")

        # Print summary
        if split_projects:
            output_info = {"dir": output_dir, "index_file": index_path}
        else:
            output_info = {"file": output_file}

        print_summary(
            total_projects=total_projects if total_projects > 0 else 1,
            projects_with_data=projects_with_data,
            total_rows=total_rows,
            unique_issues=unique_issues,
            field_counts=field_counts,
            detection_counts=detection_counts,
            elapsed_time=total_elapsed,
            output_info=output_info,
            is_split=split_projects
        )

        print("=" * 68)
        print("🎉 [완료] Jira SQL 데이터 추출 및 보고서 생성 완료!")
        print(f"  • ⏱️  SQL 쿼리 실행 소요 시간 : {format_duration(total_elapsed)}")
        if abs(total_script_elapsed - total_elapsed) > 3.0:
            print(f"  • ⏱️  전체 스크립트 실행 시간 : {format_duration(total_script_elapsed)} (대화형 마법사 포함)")
        print("=" * 68 + "\n")

    except KeyboardInterrupt:
        print("\n\n[INFO] 사용자에 의해 실행이 중단되었습니다. 추출된 데이터를 안전하게 저장합니다...")
        if split_projects:
            for pkey, meta in project_meta.items():
                try:
                    with open(meta["file"], "a", encoding="utf-8") as pf:
                        write_html_footer(pf, raw_query)
                except Exception:
                    pass
            generate_project_index_html(output_dir, project_meta, total_projects, time.time() - overall_start_time)
            print(f"[INFO] Saved partial data for {len(project_meta)} projects in '{output_dir}/'.")
        else:
            if single_output_handle:
                try:
                    if not single_is_jsonl:
                        write_html_footer(single_output_handle, raw_query)
                    single_output_handle.close()
                except Exception:
                    pass
                print(f"[INFO] '{output_file}' safely closed with {total_rows:,} records recorded.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[CRITICAL ERROR] Execution failed: {e}")
        if single_output_handle:
            try:
                if not single_is_jsonl:
                    write_html_footer(single_output_handle, raw_query)
                single_output_handle.close()
            except Exception:
                pass
        sys.exit(1)
    finally:
        if single_output_handle:
            try:
                single_output_handle.close()
            except Exception:
                pass
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
