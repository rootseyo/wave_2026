#!/usr/bin/env python3
import sys
import os
import re
import html
import time
import json
import argparse
from collections import defaultdict, Counter

def format_size(size_bytes):
    """Formats bytes to human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

def _is_jsonl_file(file_path: str) -> bool:
    low = file_path.lower()
    if low.endswith((".jsonl", ".ndjson")):
        return True
    if low.endswith((".html", ".htm")):
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(512).lstrip().startswith(b"{")
    except Exception:
        return False


def parse_html_table_stream(file_path):
    """
    Streams a large JSONL (data.jsonl) or HTML table (data.html) file with minimal memory footprint.
    Processes rows on the fly without loading the whole file into memory.
    """
    if not os.path.exists(file_path):
        print(f"[ERROR] File '{file_path}' does not exist.")
        sys.exit(1)

    file_size = os.path.getsize(file_path)
    is_jsonl = _is_jsonl_file(file_path)
    print(f"[INFO] Reading '{file_path}' ({format_size(file_size)}, format={'JSONL' if is_jsonl else 'HTML'})...")
    
    start_time = time.time()
    last_progress_time = start_time
    
    chunk_size = 1024 * 1024  # 1MB buffer chunks
    buffer = ""
    
    headers = None
    header_indices = {}
    
    total_rows = 0
    read_bytes = 0

    # Metrics collection
    projects = set()
    global_issues = set()
    
    project_issues = defaultdict(set)       # proj -> set(issue_keys)
    project_rows = Counter()                # proj -> row_count
    
    # Types tracking (supports issue_type, field_name, issue_status, detection_type)
    project_issue_types = defaultdict(lambda: defaultdict(set))
    global_issue_types = defaultdict(set)
    
    project_field_names = defaultdict(lambda: defaultdict(set))
    global_field_names = defaultdict(set)
    
    project_statuses = defaultdict(lambda: defaultdict(set))
    global_statuses = defaultdict(set)
    
    project_detections = defaultdict(lambda: defaultdict(set))
    global_detections = defaultdict(set)

    if is_jsonl:
        with open(file_path, "rb", buffering=4 * 1024 * 1024) as f:
            for raw_line in f:
                read_bytes += len(raw_line)
                stripped = raw_line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped.decode("utf-8", errors="replace"))
                if headers is None and isinstance(row, dict):
                    headers = list(row.keys())
                    print(f"\n[INFO] Detected JSONL columns: {headers}")
                total_rows += 1

                proj_key = sys.intern(str(row.get("project_key") or row.get("project_name") or "UNKNOWN"))
                issue_key = sys.intern(str(row.get("issue_key") or f"ROW_{total_rows}"))
                issue_type = sys.intern(str(row["issue_type"])) if row.get("issue_type") else None
                field_name = sys.intern(str(row["field_name"])) if row.get("field_name") else None
                issue_status = sys.intern(str(row["issue_status"])) if row.get("issue_status") else None
                detection_type = sys.intern(str(row["detection_type"])) if row.get("detection_type") else None

                projects.add(proj_key)
                global_issues.add(issue_key)
                project_issues[proj_key].add(issue_key)
                project_rows[proj_key] += 1

                if issue_type:
                    project_issue_types[proj_key][issue_type].add(issue_key)
                    global_issue_types[issue_type].add(issue_key)
                if field_name:
                    project_field_names[proj_key][field_name].add(issue_key)
                    global_field_names[field_name].add(issue_key)
                if issue_status:
                    project_statuses[proj_key][issue_status].add(issue_key)
                    global_statuses[issue_status].add(issue_key)
                if detection_type:
                    project_detections[proj_key][detection_type].add(issue_key)
                    global_detections[detection_type].add(issue_key)

                if total_rows % 2000 == 0:
                    now = time.time()
                    if now - last_progress_time > 1.0:
                        pct = (read_bytes / file_size * 100) if file_size > 0 else 0
                        print(f"  [PROGRESS] Read {format_size(read_bytes)} / {format_size(file_size)} ({pct:.1f}%), {total_rows:,} rows parsed...", end="\r")
                        last_progress_time = now
    else:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                
                read_bytes += len(chunk.encode("utf-8", errors="replace"))
                buffer += chunk
                
                # Show progress every 2 seconds
                now = time.time()
                if now - last_progress_time > 2.0:
                    pct = (read_bytes / file_size * 100) if file_size > 0 else 0
                    print(f"  [PROGRESS] Read {format_size(read_bytes)} / {format_size(file_size)} ({pct:.1f}%), {total_rows:,} rows parsed...", end="\r")
                    last_progress_time = now

                while True:
                    tr_end = buffer.find("</tr>")
                    if tr_end == -1:
                        break
                    
                    tr_start = buffer.rfind("<tr", 0, tr_end)
                    if tr_start != -1:
                        row_content = buffer[tr_start:tr_end + 5]
                        
                        if headers is None:
                            ths = re.findall(r'<th[^>]*>(.*?)</th>', row_content, re.DOTALL | re.IGNORECASE)
                            if len(ths) > 1:
                                headers = [html.unescape(re.sub(r'<[^>]+>', '', th)).strip() for th in ths]
                                for idx, h in enumerate(headers):
                                    header_indices[h.lower()] = idx
                                print(f"\n[INFO] Detected HTML table columns: {headers}")
                        else:
                            tds = re.findall(r'<td[^>]*>(.*?)</td>', row_content, re.DOTALL | re.IGNORECASE)
                            if tds:
                                total_rows += 1
                                row_vals = [html.unescape(re.sub(r'<br\s*/?>', '\n', td, flags=re.IGNORECASE)).strip() for td in tds]
                                
                                def get_col(name):
                                    idx = header_indices.get(name.lower())
                                    if idx is not None and idx < len(row_vals):
                                        return row_vals[idx]
                                    return None

                                proj_key = get_col("project_key") or get_col("project_name") or "UNKNOWN"
                                issue_key = get_col("issue_key") or f"ROW_{total_rows}"
                                
                                issue_type = get_col("issue_type")
                                field_name = get_col("field_name")
                                issue_status = get_col("issue_status")
                                detection_type = get_col("detection_type")
                                
                                projects.add(proj_key)
                                global_issues.add(issue_key)
                                
                                project_issues[proj_key].add(issue_key)
                                project_rows[proj_key] += 1
                                
                                if issue_type:
                                    project_issue_types[proj_key][issue_type].add(issue_key)
                                    global_issue_types[issue_type].add(issue_key)
                                    
                                if field_name:
                                    project_field_names[proj_key][field_name].add(issue_key)
                                    global_field_names[field_name].add(issue_key)
                                    
                                if issue_status:
                                    project_statuses[proj_key][issue_status].add(issue_key)
                                    global_statuses[issue_status].add(issue_key)
                                    
                                if detection_type:
                                    project_detections[proj_key][detection_type].add(issue_key)
                                    global_detections[detection_type].add(issue_key)

                    buffer = buffer[tr_end + 5:]

    elapsed = time.time() - start_time
    print(f"\n[SUCCESS] Completed parsing in {elapsed:.2f} seconds. Parsed {total_rows:,} rows.\n")
    
    return {
        "file_path": file_path,
        "file_size": file_size,
        "total_rows": total_rows,
        "elapsed_sec": elapsed,
        "headers": headers,
        "projects": projects,
        "global_issues": global_issues,
        "project_issues": project_issues,
        "project_rows": project_rows,
        "project_issue_types": project_issue_types,
        "global_issue_types": global_issue_types,
        "project_field_names": project_field_names,
        "global_field_names": global_field_names,
        "project_statuses": project_statuses,
        "global_statuses": global_statuses,
        "project_detections": project_detections,
        "global_detections": global_detections,
    }

def generate_text_report(stats):
    lines = []
    lines.append("================================================================================")
    lines.append("                      data.html 통계 분석 리포트")
    lines.append("================================================================================")
    lines.append(f"분석 대상 파일  : {stats['file_path']} ({format_size(stats['file_size'])})")
    lines.append(f"소요 시간       : {stats['elapsed_sec']:.2f} 초")
    lines.append(f"전체 레코드 수  : {stats['total_rows']:,} 행")
    lines.append("--------------------------------------------------------------------------------")
    lines.append(f"1. 전체 프로젝트 개수 (중복 제거) : {len(stats['projects']):,} 개")
    lines.append(f"2. 전체 이슈 개수     (중복 제거) : {len(stats['global_issues']):,} 개")
    lines.append("================================================================================\n")

    if stats["global_issue_types"]:
        lines.append("--------------------------------------------------------------------------------")
        lines.append("[전체 이슈 타입별 이슈 개수]")
        lines.append("--------------------------------------------------------------------------------")
        for itype, keys in sorted(stats["global_issue_types"].items(), key=lambda x: len(x[1]), reverse=True):
            lines.append(f"  - {itype:<25}: {len(keys):,} 개")
        lines.append("")

    if stats["global_field_names"]:
        lines.append("--------------------------------------------------------------------------------")
        lines.append("[전체 필드(Field Name)별 이슈 개수]")
        lines.append("--------------------------------------------------------------------------------")
        for fname, keys in sorted(stats["global_field_names"].items(), key=lambda x: len(x[1]), reverse=True):
            lines.append(f"  - {fname:<25}: {len(keys):,} 개")
        lines.append("")

    if stats["global_detections"]:
        lines.append("--------------------------------------------------------------------------------")
        lines.append("[전체 감지 유형(Detection Type)별 이슈 개수]")
        lines.append("--------------------------------------------------------------------------------")
        for dtype, keys in sorted(stats["global_detections"].items(), key=lambda x: len(x[1]), reverse=True):
            lines.append(f"  - {dtype:<25}: {len(keys):,} 개")
        lines.append("")

    lines.append("================================================================================")
    lines.append("                         프로젝트별 세부 통계")
    lines.append("================================================================================")
    
    sorted_projects = sorted(stats["projects"])
    for idx, proj in enumerate(sorted_projects, start=1):
        proj_issues_cnt = len(stats["project_issues"][proj])
        p_rows = stats["project_rows"][proj]
        lines.append(f"[{idx}] 프로젝트: {proj}")
        lines.append(f"    - 이슈 개수 (중복 제거): {proj_issues_cnt:,} 개 (총 레코드: {p_rows:,} 건)")
        
        itypes = stats["project_issue_types"][proj]
        if itypes:
            lines.append("    - 이슈 타입별 개수:")
            for t_name, t_keys in sorted(itypes.items(), key=lambda x: len(x[1]), reverse=True):
                lines.append(f"        * {t_name}: {len(t_keys):,} 개")

        fnames = stats["project_field_names"][proj]
        if fnames:
            lines.append("    - 필드별 개수:")
            for f_name, f_keys in sorted(fnames.items(), key=lambda x: len(x[1]), reverse=True):
                lines.append(f"        * {f_name}: {len(f_keys):,} 개")

        detections = stats["project_detections"][proj]
        if detections:
            lines.append("    - 감지 유형(Detection Type)별 개수:")
            for d_name, d_keys in sorted(detections.items(), key=lambda x: len(x[1]), reverse=True):
                lines.append(f"        * {d_name}: {len(d_keys):,} 개")

        statuses = stats["project_statuses"][proj]
        if statuses:
            lines.append("    - 이슈 상태(Status)별 개수:")
            for s_name, s_keys in sorted(statuses.items(), key=lambda x: len(x[1]), reverse=True):
                lines.append(f"        * {s_name}: {len(s_keys):,} 개")

        lines.append("-" * 60)

    return "\n".join(lines)

def generate_html_dashboard(stats, output_file="report.html"):
    """
    Generates a modern, interactive HTML Executive Dashboard with Chart.js visualizations.
    """
    # Prepare JSON data payload
    sorted_projects = []
    for proj in stats["projects"]:
        issue_cnt = len(stats["project_issues"][proj])
        row_cnt = stats["project_rows"][proj]
        fields = {k: len(v) for k, v in stats["project_field_names"][proj].items()}
        detections = {k: len(v) for k, v in stats["project_detections"][proj].items()}
        statuses = {k: len(v) for k, v in stats["project_statuses"][proj].items()}
        itypes = {k: len(v) for k, v in stats["project_issue_types"][proj].items()}
        
        sorted_projects.append({
            "key": proj,
            "issue_count": issue_cnt,
            "row_count": row_cnt,
            "fields": fields,
            "detections": detections,
            "statuses": statuses,
            "issue_types": itypes
        })
    
    sorted_projects.sort(key=lambda x: x["issue_count"], reverse=True)
    
    global_fields = {k: len(v) for k, v in stats["global_field_names"].items()}
    global_detections = {k: len(v) for k, v in stats["global_detections"].items()}
    global_itypes = {k: len(v) for k, v in stats["global_issue_types"].items()}
    
    data_payload = {
        "summary": {
            "fileName": os.path.basename(stats["file_path"]),
            "fileSize": format_size(stats["file_size"]),
            "elapsedSec": round(stats["elapsed_sec"], 2),
            "totalProjects": len(stats["projects"]),
            "totalIssues": len(stats["global_issues"]),
            "totalRows": stats["total_rows"]
        },
        "projects": sorted_projects,
        "globalFields": global_fields,
        "globalDetections": global_detections,
        "globalIssueTypes": global_itypes
    }

    json_data_str = json.dumps(data_payload, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jira Data Analytics & Executive Dashboard</title>
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- Chart.js CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-dark: #0f172a;
            --card-bg: rgba(30, 41, 59, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --accent-primary: #6366f1;
            --accent-secondary: #a855f7;
            --accent-success: #10b981;
            --accent-warning: #f59e0b;
            --accent-info: #06b6d4;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-dark);
            background-image: 
                radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(168, 85, 247, 0.15) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
            padding: 2rem 1.5rem;
            line-height: 1.5;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}

        /* Header */
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--card-border);
        }}

        .header-title h1 {{
            font-size: 1.875rem;
            font-weight: 800;
            background: linear-gradient(135deg, #818cf8 0%, #c084fc 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.02em;
        }}

        .header-title p {{
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-top: 0.25rem;
        }}

        .meta-badges {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
        }}

        .badge {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            padding: 0.35rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.8rem;
            color: var(--text-muted);
            backdrop-filter: blur(8px);
        }}
        .badge span {{
            color: var(--text-main);
            font-weight: 600;
        }}

        /* KPI Cards Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.25rem;
            margin-bottom: 2rem;
        }}

        .kpi-card {{
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--card-border);
            border-radius: 1rem;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}

        .kpi-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        }}

        .kpi-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--accent-primary);
        }}

        .kpi-card.purple::before {{ background: var(--accent-secondary); }}
        .kpi-card.emerald::before {{ background: var(--accent-success); }}
        .kpi-card.amber::before {{ background: var(--accent-warning); }}

        .kpi-title {{
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 0.5rem;
        }}

        .kpi-value {{
            font-size: 2.25rem;
            font-weight: 800;
            color: var(--text-main);
            letter-spacing: -0.03em;
        }}

        .kpi-subtext {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}

        /* Charts Section */
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}

        .chart-card {{
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--card-border);
            border-radius: 1rem;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
        }}

        .chart-header {{
            margin-bottom: 1.25rem;
        }}

        .chart-title {{
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--text-main);
        }}

        .chart-subtitle {{
            font-size: 0.8rem;
            color: var(--text-muted);
        }}

        .chart-container {{
            position: relative;
            flex-grow: 1;
            min-height: 300px;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        /* Recommendations & Banner */
        .insight-banner {{
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.15) 0%, rgba(168, 85, 247, 0.15) 100%);
            border: 1px solid rgba(129, 140, 248, 0.3);
            border-radius: 1rem;
            padding: 1.25rem 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            align-items: center;
            gap: 1rem;
        }}

        .insight-icon {{
            font-size: 1.75rem;
            background: rgba(99, 102, 241, 0.2);
            padding: 0.6rem;
            border-radius: 0.75rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .insight-text h3 {{
            font-size: 1rem;
            font-weight: 700;
            color: #a5b4fc;
        }}

        .insight-text p {{
            font-size: 0.875rem;
            color: var(--text-muted);
        }}

        /* Table Section */
        .table-card {{
            background: var(--card-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--card-border);
            border-radius: 1rem;
            padding: 1.5rem;
            overflow: hidden;
        }}

        .table-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
            margin-bottom: 1.25rem;
        }}

        .search-box {{
            position: relative;
            min-width: 250px;
        }}

        .search-box input {{
            width: 100%;
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            padding: 0.6rem 1rem 0.6rem 2.25rem;
            border-radius: 0.5rem;
            color: var(--text-main);
            font-size: 0.875rem;
            outline: none;
            transition: border-color 0.2s;
        }}

        .search-box input:focus {{
            border-color: var(--accent-primary);
        }}

        .search-box::before {{
            content: '🔍';
            position: absolute;
            left: 0.75rem;
            top: 50%;
            transform: translateY(-50%);
            font-size: 0.8rem;
            opacity: 0.6;
        }}

        .table-wrapper {{
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.875rem;
        }}

        th {{
            background: rgba(15, 23, 42, 0.5);
            padding: 0.875rem 1rem;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--card-border);
        }}

        td {{
            padding: 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-main);
        }}

        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}

        .project-badge {{
            font-weight: 700;
            color: #818cf8;
            font-family: monospace;
            font-size: 0.95rem;
        }}

        .progress-bar-container {{
            width: 100%;
            max-width: 120px;
            background: rgba(255, 255, 255, 0.1);
            height: 6px;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 4px;
        }}

        .progress-bar {{
            height: 100%;
            background: linear-gradient(90deg, #6366f1, #a855f7);
            border-radius: 3px;
        }}

        .pill {{
            display: inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.75rem;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.08);
            margin: 2px;
        }}
        .pill.attach {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
        .pill.text {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}

        @media (max-width: 768px) {{
            .charts-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <div class="header-title">
                <h1>JIRA Data & JEditor Analysis Dashboard</h1>
                <p>Data Migration Executive Summary & Issue Distribution Analysis</p>
            </div>
            <div class="meta-badges">
                <div class="badge">Analyzed File: <span id="meta-filename">data.html</span></div>
                <div class="badge">Size: <span id="meta-filesize">0 MB</span></div>
                <div class="badge">Execution Time: <span id="meta-time">0s</span></div>
            </div>
        </header>

        <!-- KPI Grid -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-title">총 프로젝트 수 (Distinct)</div>
                <div class="kpi-value" id="kpi-projects">0</div>
                <div class="kpi-subtext">조회 대상 전체 Jira 프로젝트</div>
            </div>
            <div class="kpi-card purple">
                <div class="kpi-title">전체 이슈 수 (Distinct)</div>
                <div class="kpi-value" id="kpi-issues">0</div>
                <div class="kpi-subtext">Unique Issue Key 기준 개수</div>
            </div>
            <div class="kpi-card emerald">
                <div class="kpi-title">전체 레코드(행) 수</div>
                <div class="kpi-value" id="kpi-rows">0</div>
                <div class="kpi-subtext">추출된 Field & Comment 데이터 건수</div>
            </div>
            <div class="kpi-card amber">
                <div class="kpi-title">첨부파일(Attachment) 감지 건수</div>
                <div class="kpi-value" id="kpi-attachments">0</div>
                <div class="kpi-subtext">마이그레이션 우선 점검 대상</div>
            </div>
        </div>

        <!-- Actionable Insights Banner -->
        <div class="insight-banner">
            <div class="insight-icon">💡</div>
            <div class="insight-text">
                <h3>의사결정 핵심 인사이트</h3>
                <p id="insight-summary">데이터 분석 중...</p>
            </div>
        </div>

        <!-- Charts Grid -->
        <div class="charts-grid">
            <!-- Pie Chart: Top Projects -->
            <div class="chart-card">
                <div class="chart-header">
                    <div class="chart-title">프로젝트별 이슈 점유율 (Pie Chart)</div>
                    <div class="chart-subtitle">가장 많은 이슈를 보유한 상위 프로젝트 비율</div>
                </div>
                <div class="chart-container">
                    <canvas id="projectPieChart"></canvas>
                </div>
            </div>

            <!-- Bar Chart: Top 10 Projects -->
            <div class="chart-card">
                <div class="chart-header">
                    <div class="chart-title">상위 10개 프로젝트 이슈 건수 (Top 10 Projects)</div>
                    <div class="chart-subtitle">이슈 수 기준 Ranking</div>
                </div>
                <div class="chart-container">
                    <canvas id="projectBarChart"></canvas>
                </div>
            </div>

            <!-- Donut Chart: Detection Types -->
            <div class="chart-card">
                <div class="chart-header">
                    <div class="chart-title">감지 유형별 이슈 비율 (Detection Types)</div>
                    <div class="chart-subtitle">Renderer/Text vs Attachment 비중</div>
                </div>
                <div class="chart-container">
                    <canvas id="detectionDonutChart"></canvas>
                </div>
            </div>

            <!-- Bar Chart: Field Types -->
            <div class="chart-card">
                <div class="chart-header">
                    <div class="chart-title">필드(Field Name)별 발생 건수</div>
                    <div class="chart-subtitle">Description, Comment, Custom Field 비중</div>
                </div>
                <div class="chart-container">
                    <canvas id="fieldBarChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Table Card -->
        <div class="table-card">
            <div class="table-header">
                <div>
                    <div class="chart-title">프로젝트별 세부 데이터 현황</div>
                    <div class="chart-subtitle">전체 프로젝트 검색 및 이슈/필드 세부 내역</div>
                </div>
                <div class="search-box">
                    <input type="text" id="tableSearch" placeholder="프로젝트 키 검색..." onkeyup="filterTable()">
                </div>
            </div>
            <div class="table-wrapper">
                <table id="projectTable">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>프로젝트 키</th>
                            <th>이슈 수 (Unique)</th>
                            <th>점유율</th>
                            <th>총 레코드 수</th>
                            <th>필드 구성</th>
                            <th>감지 유형 (Detection)</th>
                        </tr>
                    </thead>
                    <tbody id="tableBody">
                        <!-- Dynamic Rows -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- JavaScript Data & Logic -->
    <script>
        const DASHBOARD_DATA = {json_data_str};

        document.addEventListener('DOMContentLoaded', () => {{
            renderSummary();
            renderCharts();
            renderTable();
        }});

        function renderSummary() {{
            const s = DASHBOARD_DATA.summary;
            document.getElementById('meta-filename').textContent = s.fileName;
            document.getElementById('meta-filesize').textContent = s.fileSize;
            document.getElementById('meta-time').textContent = s.elapsedSec + 's';

            document.getElementById('kpi-projects').textContent = s.totalProjects.toLocaleString();
            document.getElementById('kpi-issues').textContent = s.totalIssues.toLocaleString();
            document.getElementById('kpi-rows').textContent = s.totalRows.toLocaleString();

            const attachCount = DASHBOARD_DATA.globalDetections['Attachment'] || 0;
            document.getElementById('kpi-attachments').textContent = attachCount.toLocaleString();

            // Insight Banner Text
            const topProj = DASHBOARD_DATA.projects[0];
            if (topProj) {{
                const topPct = ((topProj.issue_count / s.totalIssues) * 100).toFixed(1);
                document.getElementById('insight-summary').innerHTML = 
                    `가장 많은 이슈를 보유한 프로젝트는 <strong>${{topProj.key}}</strong> (<strong>${{topProj.issue_count.toLocaleString()}}개</strong>, 전체의 <strong>${{topPct}}%</strong>) 입니다. ` +
                    `첨부파일(Attachment)이 포함된 이슈는 총 <strong>${{attachCount.toLocaleString()}}건</strong>으로, 마이그레이션 시 최우선 검수 작업이 필요합니다.`;
            }}
        }}

        function renderCharts() {{
            const projects = DASHBOARD_DATA.projects;
            const totalIssues = DASHBOARD_DATA.summary.totalIssues;

            // Palette
            const colors = [
                '#6366f1', '#a855f7', '#ec4899', '#3b82f6', '#10b981', 
                '#f59e0b', '#06b6d4', '#84cc16', '#f97316', '#64748b'
            ];

            // 1. Pie Chart Data (Top 7 + Others)
            let pieLabels = [];
            let pieData = [];
            let otherCount = 0;

            projects.forEach((p, idx) => {{
                if (idx < 7) {{
                    pieLabels.push(p.key);
                    pieData.push(p.issue_count);
                }} else {{
                    otherCount += p.issue_count;
                }}
            }});

            if (otherCount > 0) {{
                pieLabels.push('기타 (' + (projects.length - 7) + '개)');
                pieData.push(otherCount);
            }}

            new Chart(document.getElementById('projectPieChart'), {{
                type: 'pie',
                data: {{
                    labels: pieLabels,
                    datasets: [{{
                        data: pieData,
                        backgroundColor: colors,
                        borderWidth: 2,
                        borderColor: '#1e293b'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ family: 'Inter' }} }} }}
                    }}
                }}
            }});

            // 2. Bar Chart Data (Top 10)
            const top10 = projects.slice(0, 10);
            new Chart(document.getElementById('projectBarChart'), {{
                type: 'bar',
                data: {{
                    labels: top10.map(p => p.key),
                    datasets: [{{
                        label: '이슈 수',
                        data: top10.map(p => p.issue_count),
                        backgroundColor: '#818cf8',
                        borderRadius: 6
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
                        y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
                    }},
                    plugins: {{
                        legend: {{ display: false }}
                    }}
                }}
            }});

            // 3. Donut Chart (Detection Types)
            const detections = DASHBOARD_DATA.globalDetections;
            const detLabels = Object.keys(detections);
            const detData = Object.values(detections);
            new Chart(document.getElementById('detectionDonutChart'), {{
                type: 'doughnut',
                data: {{
                    labels: detLabels,
                    datasets: [{{
                        data: detData,
                        backgroundColor: ['#34d399', '#fbbf24', '#f87171', '#60a5fa'],
                        borderWidth: 2,
                        borderColor: '#1e293b'
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ color: '#94a3b8' }} }}
                    }}
                }}
            }});

            // 4. Field Names Bar Chart
            const fields = DASHBOARD_DATA.globalFields;
            new Chart(document.getElementById('fieldBarChart'), {{
                type: 'bar',
                data: {{
                    labels: Object.keys(fields),
                    datasets: [{{
                        label: '이슈 수',
                        data: Object.values(fields),
                        backgroundColor: '#c084fc',
                        borderRadius: 6
                    }}]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                        y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }}
                    }},
                    plugins: {{ legend: {{ display: false }} }}
                }}
            }});
        }}

        function renderTable() {{
            const tbody = document.getElementById('tableBody');
            tbody.innerHTML = '';

            const totalIssues = DASHBOARD_DATA.summary.totalIssues;

            DASHBOARD_DATA.projects.forEach((p, idx) => {{
                const pct = totalIssues > 0 ? ((p.issue_count / totalIssues) * 100).toFixed(1) : '0.0';
                
                // Field pill render
                let fieldPills = Object.entries(p.fields).map(([fname, cnt]) => 
                    `<span class="pill">${{fname}}: ${{cnt}}</span>`
                ).join('');

                // Detection pill render
                let detPills = Object.entries(p.detections).map(([dname, cnt]) => {{
                    const cls = dname.includes('Attachment') ? 'attach' : 'text';
                    return `<span class="pill ${{cls}}">${{dname}}: ${{cnt}}</span>`;
                }}).join('');

                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="color: #64748b; font-weight: 600;">${{idx + 1}}</td>
                    <td class="project-badge">${{p.key}}</td>
                    <td style="font-weight: 700; font-size: 1rem;">${{p.issue_count.toLocaleString()}} 개</td>
                    <td>
                        <div>${{pct}}%</div>
                        <div class="progress-bar-container">
                            <div class="progress-bar" style="width: ${{pct}}%"></div>
                        </div>
                    </td>
                    <td>${{p.row_count.toLocaleString()}} 건</td>
                    <td>${{fieldPills || '-'}}</td>
                    <td>${{detPills || '-'}}</td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function filterTable() {{
            const query = document.getElementById('tableSearch').value.toLowerCase();
            const rows = document.querySelectorAll('#tableBody tr');
            rows.forEach(row => {{
                const projKey = row.children[1].textContent.toLowerCase();
                if (projKey.includes(query)) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }}
    </script>
</body>
</html>
"""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"[SUCCESS] Generated Interactive HTML Dashboard: '{output_file}'")

def main():
    default_input = "data.jsonl" if os.path.exists("data.jsonl") else ("data.html" if os.path.exists("data.html") else "data.jsonl")
    parser = argparse.ArgumentParser(description="Large data.jsonl / data.html Statistics Analyzer & Dashboard Generator")
    parser.add_argument("html_file", nargs="?", default=default_input, help="Path to input data.jsonl or data.html file (default: auto-detect data.jsonl / data.html)")
    parser.add_argument("-o", "--output", help="Path to save text summary report file")
    parser.add_argument("--html-output", default="data_dashboard.html", help="Path to save interactive HTML dashboard (default: data_dashboard.html)")
    parser.add_argument("--no-html", action="store_true", help="Disable HTML dashboard generation")
    args = parser.parse_args()

    if not os.path.exists(args.html_file):
        # Fallback check between data.jsonl and data.html
        if args.html_file == "data.html" and os.path.exists("data.jsonl"):
            args.html_file = "data.jsonl"
        elif args.html_file == "data.jsonl" and os.path.exists("data.html"):
            args.html_file = "data.html"
        else:
            print(f"\n[ERROR] '{args.html_file}' 파일을 찾을 수 없습니다.")
            print("쿼리 실행기(step2_query_runner.py)를 먼저 실행하여 data.jsonl (또는 data.html) 파일을 생성했는지 확인해주세요.\n")
            print("사용법:")
            print("  python3 step3_analyze_data_html.py\n")
            sys.exit(1)

    stats = parse_html_table_stream(args.html_file)
    
    # 1. Terminal & Text Report
    report_text = generate_text_report(stats)
    print(report_text)
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"[INFO] Saved text summary report to '{args.output}'.")

    # 2. HTML Executive Dashboard Generation
    if not args.no_html:
        generate_html_dashboard(stats, args.html_output)
        print(f"[안내] 브라우저에서 '{args.html_output}' 파일을 열어 대시보드를 확인하세요!")

if __name__ == "__main__":
    main()
