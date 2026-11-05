#!/usr/bin/env python3
"""
High-Performance, Ultra-Low-Memory Dashboard Generator for 1GB+ Query Result HTML Files.

Designed specifically for multi-gigabyte (1GB ~ 10GB+) default_values.html / data.html exports:
- Streams in 8MB chunks using cursor-offset scanning (no O(N^2) string slicing).
- Zero-copy skipping of the large `content` / `default_value_xml` column (computes size via
  index arithmetic without allocating strings or running regex/unescape on HTML bodies).
- Interns metadata strings and tracks unique issues per project using compact integers.
- Automatically links Cloud custom field mappings from id-mappings.html if available.
"""

import argparse
from collections import Counter
import gc
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import zlib

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "default_values.html"
DEFAULT_OUTPUT = SCRIPT_DIR / "default_values_dashboard.html"
MAPPING_CANDIDATES = (
    SCRIPT_DIR.parent / "id-mappings.html",
    SCRIPT_DIR.parent / "id-mappings-jira-classic-customField.html",
    SCRIPT_DIR / "id-mappings.html",
    SCRIPT_DIR / "id-mappings-jira-classic-customField.html",
)

TAG_STRIP_RE = re.compile(r"<[^>]+>")


def format_size(size_bytes: float) -> str:
    """Formats byte size into human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}초"
    m, s = divmod(seconds, 60)
    return f"{int(m)}분 {s:.1f}초"


def load_cloud_field_mapping(mapping_path: Path | None = None) -> dict[str, str]:
    """Stream-parses Jira customField ID mappings (DC customfield_X -> Cloud customfield_Y)."""
    target_path = mapping_path
    if target_path is None:
        for cand in MAPPING_CANDIDATES:
            if cand.is_file():
                target_path = cand
                break
    if not target_path or not target_path.is_file():
        return {}

    mapping: dict[str, set[str]] = {}
    row_re = re.compile(
        r"jira/classic:customField\s*</td>\s*<td[^>]*>\s*(\d+)\s*</td>\s*<td[^>]*>\s*(\d+)\s*</td>",
        re.IGNORECASE | re.DOTALL,
    )
    try:
        with target_path.open("r", encoding="utf-8", errors="replace") as f:
            tail = ""
            while True:
                chunk = f.read(2 * 1024 * 1024)
                if not chunk:
                    break
                buf = tail + chunk
                for m in row_re.finditer(buf):
                    dc_id, cloud_id = m.group(1), m.group(2)
                    mapping.setdefault(f"customfield_{dc_id}", set()).add(f"customfield_{cloud_id}")
                tail = buf[-512:]
    except Exception:
        return {}

    result = {}
    for dc_field, cloud_set in mapping.items():
        if len(cloud_set) == 1:
            result[dc_field] = next(iter(cloud_set))
        elif len(cloud_set) > 1:
            result[dc_field] = ", ".join(sorted(cloud_set))
    return result


def load_cloud_env_urls() -> dict[str, str]:
    """Reads JIRA_CLOUD_SANDBOX_URL and JIRA_CLOUD_OPS_URL from ../env or ./env if available."""
    urls: dict[str, str] = {}
    for env_cand in (SCRIPT_DIR.parent / "env", SCRIPT_DIR / "env", SCRIPT_DIR.parent / ".env", SCRIPT_DIR / ".env"):
        if not env_cand.is_file():
            continue
        try:
            for line in env_cand.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, val = line.split("=", 1)
                k = k.strip()
                if k in ("JIRA_CLOUD_SANDBOX_URL", "JIRA_CLOUD_OPS_URL"):
                    val = val.strip().strip("'").strip('"').rstrip("/")
                    if val.startswith("https://"):
                        urls[k] = val
        except Exception:
            pass
    return urls



def _extract_small_cell(row_str: str, start: int, end: int) -> str:
    """Extracts and unescapes a small metadata cell."""
    raw = row_str[start:end]
    if "<" in raw:
        raw = TAG_STRIP_RE.sub("", raw)
    if "&" in raw:
        raw = html.unescape(raw)
    return raw.strip()


def _issue_compact_id(issue_key: str) -> int:
    """Converts 'PROJ-12345' into an integer for compact set storage."""
    dash = issue_key.rfind("-")
    if dash != -1:
        num_part = issue_key[dash + 1 :]
        if num_part.isdigit():
            return int(num_part)
    return zlib.crc32(issue_key.encode("utf-8", errors="ignore"))


def stream_analyze_html(file_path: Path, chunk_mb: int = 8) -> dict:
    """
    Streams a 1GB+ HTML export file with near-zero memory overhead.
    Skips allocating/unescaping the massive `content` column via index jumping.
    """
    file_size = file_path.stat().st_size
    chunk_size = max(1, chunk_mb) * 1024 * 1024

    print(f"[INFO] 대용량 스트리밍 분석 시작: '{file_path.name}' ({format_size(file_size)})")
    print(f"[INFO] 메모리 보호 모드: {chunk_mb}MB 청크 스트리밍 + 본문(content) Zero-Copy 스킵 활성")

    start_time = time.monotonic()
    last_log_time = start_time

    headers: list[str] | None = None
    col_map: dict[str, int] = {}

    # Column indices (populated once header <tr> is found)
    idx_proj_key = -1
    idx_proj_name = -1
    idx_issue_key = -1
    idx_status = -1
    idx_field_id = -1
    idx_field_name = -1
    idx_detection = -1
    idx_content = -1
    idx_context_scope = -1

    total_rows = 0
    total_content_chars = 0
    read_chars = 0

    # Compact aggregation structures
    proj_names: dict[str, str] = {}
    proj_row_counts: Counter[str] = Counter()
    proj_issue_sets: dict[str, set[int]] = {}
    proj_sample_issue: dict[str, str] = {}
    proj_fields: dict[str, Counter[str]] = {}
    proj_detections: dict[str, Counter[str]] = {}
    proj_statuses: dict[str, Counter[str]] = {}
    proj_content_chars: Counter[str] = Counter()

    field_names: dict[str, str] = {}
    field_row_counts: Counter[str] = Counter()
    field_projects: dict[str, Counter[str]] = {}
    field_detections: dict[str, Counter[str]] = {}
    field_content_chars: Counter[str] = Counter()
    field_sample_snippet: dict[str, str] = {}
    field_sample_issue: dict[str, str] = {}

    global_detections: Counter[str] = Counter()
    global_statuses: Counter[str] = Counter()

    buffer = ""
    # Safety limit if a file has huge non-row blocks
    max_buffer_chars = 128 * 1024 * 1024

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            read_chars += len(chunk)
            buffer += chunk

            now = time.monotonic()
            if now - last_log_time >= 1.5:
                # Approximate byte progress
                pct = min(99.9, (f.tell() / file_size * 100.0)) if file_size > 0 else 0.0
                elapsed = now - start_time
                speed_mb = (f.tell() / (1024 * 1024)) / max(elapsed, 0.001)
                print(
                    f"  -> 진행률: {pct:5.1f}% ({format_size(f.tell())} / {format_size(file_size)}) | "
                    f"파싱 행: {total_rows:,}건 | 속도: {speed_mb:.1f} MB/s",
                    end="\r",
                    flush=True,
                )
                last_log_time = now

            cursor = 0
            while True:
                tr_start = buffer.find("<tr", cursor)
                if tr_start == -1:
                    break

                tr_end = buffer.find("</tr>", tr_start + 3)
                if tr_end == -1:
                    break

                # Fast check for rare unescaped nested <tr> inside a cell
                nested_pos = buffer.find("<tr", tr_start + 3, tr_end)
                while nested_pos != -1:
                    next_end = buffer.find("</tr>", tr_end + 5)
                    if next_end == -1:
                        tr_end = -1
                        break
                    nested_pos = buffer.find("<tr", nested_pos + 3, next_end)
                    tr_end = next_end
                if tr_end == -1:
                    break

                if True:
                    if headers is None:
                        # Parse <th> headers
                        row_slice = buffer[tr_start:tr_end]
                        ths = re.findall(r"<th\b[^>]*>(.*?)</th>", row_slice, re.DOTALL | re.IGNORECASE)
                        if len(ths) >= 3:
                            cand_headers = [
                                html.unescape(TAG_STRIP_RE.sub("", th)).strip().lower() for th in ths
                            ]
                            if "field_id" in cand_headers or "issue_key" in cand_headers or "프로젝트" in cand_headers:
                                headers = cand_headers
                                col_map = {h: i for i, h in enumerate(headers)}
                                idx_proj_key = col_map.get("project_key", col_map.get("프로젝트", -1))
                                idx_proj_name = col_map.get("project_name", -1)
                                idx_issue_key = col_map.get("issue_key", -1)
                                idx_status = col_map.get("issue_status", -1)
                                idx_field_id = col_map.get("field_id", col_map.get("필드", -1))
                                idx_field_name = col_map.get("field_name", -1)
                                idx_detection = col_map.get("detection_type", col_map.get("유형", -1))
                                idx_content = col_map.get(
                                    "content",
                                    col_map.get("default_value_xml", col_map.get("기본값", -1)),
                                )
                                idx_context_scope = col_map.get("context_scope", col_map.get("컨텍스트", -1))
                                print(f"\n[INFO] 감지된 테이블 컬럼 ({len(headers)}개): {', '.join(headers)}")
                    else:
                        # Fast index-based <td> scan inside buffer[tr_start:tr_end] without slicing full row
                        cell_pos = tr_start
                        col_idx = 0

                        proj_key = "UNKNOWN"
                        proj_name = ""
                        issue_key = ""
                        status = ""
                        field_id = "UNKNOWN"
                        field_name = ""
                        detection = "HTML/Text"
                        content_len = 0
                        need_sample = False
                        is_five_col_report = headers == ["프로젝트", "필드", "컨텍스트", "유형", "기본값"]

                        while True:
                            td_open = buffer.find("<td", cell_pos, tr_end)
                            if td_open == -1:
                                break
                            td_gt = buffer.find(">", td_open + 3, tr_end)
                            if td_gt == -1:
                                break
                            td_content_start = td_gt + 1
                            td_close = buffer.find("</td>", td_content_start, tr_end)
                            if td_close == -1:
                                break

                            if is_five_col_report:
                                if col_idx == 0:
                                    # <strong>KEY</strong><span class="sub">NAME</span>
                                    raw_c = buffer[td_content_start:td_close]
                                    m_strong = re.search(r"<strong>(.*?)</strong>", raw_c)
                                    m_sub = re.search(r'<span class="sub">(.*?)</span>', raw_c)
                                    if m_strong:
                                        proj_key = sys.intern(html.unescape(m_strong.group(1)).strip() or "UNKNOWN")
                                    if m_sub:
                                        proj_name = html.unescape(m_sub.group(1)).strip()
                                elif col_idx == 1:
                                    raw_c = buffer[td_content_start:td_close]
                                    m_strong = re.search(r"<strong>(.*?)</strong>", raw_c)
                                    m_subs = re.findall(r'<span class="sub">(.*?)</span>', raw_c)
                                    if m_strong:
                                        field_name = sys.intern(html.unescape(m_strong.group(1)).strip())
                                    if m_subs:
                                        field_id = sys.intern(html.unescape(m_subs[0]).strip() or "UNKNOWN")
                                        need_sample = field_id not in field_sample_snippet
                                elif col_idx == 3:
                                    raw_c = buffer[td_content_start:td_close]
                                    det_part = raw_c.split("<span", 1)[0]
                                    val = html.unescape(TAG_STRIP_RE.sub("", det_part)).strip()
                                    if val:
                                        detection = sys.intern(val)
                                elif col_idx == 4:
                                    content_len = td_close - td_content_start
                                    if need_sample and content_len > 0:
                                        pre_start = buffer.find("<pre>", td_content_start, td_close)
                                        s_pos = pre_start + 5 if pre_start != -1 else td_content_start
                                        raw_snip = buffer[s_pos : min(td_close, s_pos + 600)]
                                        field_sample_snippet[field_id] = html.unescape(TAG_STRIP_RE.sub("", raw_snip)).strip()[:400]
                            elif col_idx == idx_content:
                                # ZERO-COPY SKIP: only compute length by subtracting offsets!
                                content_len = td_close - td_content_start
                                if need_sample and content_len > 0:
                                    raw_snip = buffer[td_content_start : min(td_close, td_content_start + 600)]
                                    raw_snip = raw_snip.replace("<br>", "\n").replace("<br/>", "\n")
                                    field_sample_snippet[field_id] = html.unescape(raw_snip).strip()[:400]
                                    if issue_key:
                                        field_sample_issue[field_id] = issue_key
                            elif col_idx == idx_proj_key:
                                val = _extract_small_cell(buffer, td_content_start, td_close)
                                if val:
                                    proj_key = sys.intern(val)
                            elif col_idx == idx_proj_name:
                                proj_name = _extract_small_cell(buffer, td_content_start, td_close)
                            elif col_idx == idx_issue_key:
                                issue_key = _extract_small_cell(buffer, td_content_start, td_close)
                            elif col_idx == idx_status:
                                val = _extract_small_cell(buffer, td_content_start, td_close)
                                if val:
                                    status = sys.intern(val)
                            elif col_idx == idx_field_id:
                                val = _extract_small_cell(buffer, td_content_start, td_close)
                                if val:
                                    field_id = sys.intern(val)
                                    need_sample = field_id not in field_sample_snippet
                            elif col_idx == idx_field_name:
                                val = _extract_small_cell(buffer, td_content_start, td_close)
                                if val:
                                    field_name = sys.intern(val)
                            elif col_idx == idx_detection:
                                val = _extract_small_cell(buffer, td_content_start, td_close)
                                if val:
                                    detection = sys.intern(val)

                            col_idx += 1
                            cell_pos = td_close + 5

                        if col_idx >= 3:
                            total_rows += 1
                            total_content_chars += content_len

                            if proj_name and proj_key not in proj_names:
                                proj_names[proj_key] = sys.intern(proj_name)
                            if field_name and field_id not in field_names:
                                field_names[field_id] = field_name

                            proj_row_counts[proj_key] += 1
                            proj_content_chars[proj_key] += content_len

                            if issue_key:
                                iset = proj_issue_sets.get(proj_key)
                                if iset is None:
                                    iset = set()
                                    proj_issue_sets[proj_key] = iset
                                iset.add(_issue_compact_id(issue_key))
                                if proj_key not in proj_sample_issue:
                                    proj_sample_issue[proj_key] = issue_key

                            f_label = f"{field_name} ({field_id})" if field_name and field_name != field_id else field_id
                            f_label = sys.intern(f_label)

                            p_f = proj_fields.get(proj_key)
                            if p_f is None:
                                p_f = Counter()
                                proj_fields[proj_key] = p_f
                            p_f[f_label] += 1

                            p_d = proj_detections.get(proj_key)
                            if p_d is None:
                                p_d = Counter()
                                proj_detections[proj_key] = p_d
                            p_d[detection] += 1

                            if status:
                                p_s = proj_statuses.get(proj_key)
                                if p_s is None:
                                    p_s = Counter()
                                    proj_statuses[proj_key] = p_s
                                p_s[status] += 1
                                global_statuses[status] += 1

                            field_row_counts[field_id] += 1
                            field_content_chars[field_id] += content_len

                            f_p = field_projects.get(field_id)
                            if f_p is None:
                                f_p = Counter()
                                field_projects[field_id] = f_p
                            f_p[proj_key] += 1

                            f_d = field_detections.get(field_id)
                            if f_d is None:
                                f_d = Counter()
                                field_detections[field_id] = f_d
                            f_d[detection] += 1

                            global_detections[detection] += 1

                cursor = tr_end + 5

            # Slice buffer tail only ONCE per 8MB chunk
            if cursor > 0:
                buffer = buffer[cursor:]
            elif len(buffer) > max_buffer_chars:
                # Safety valve against malformed unclosed tags
                buffer = buffer[-1024 * 1024 :]
                gc.collect()

    buffer = ""
    gc.collect()

    elapsed = max(time.monotonic() - start_time, 0.001)
    total_unique_issues = sum(len(s) for s in proj_issue_sets.values())
    if total_unique_issues == 0 and total_rows > 0:
        # Config-only table without issue_key
        total_unique_issues = 0

    print(
        f"\n[SUCCESS] 스트리밍 파싱 완료! 소요 시간: {format_duration(elapsed)} | "
        f"총 {total_rows:,}행 | 고유 이슈 {total_unique_issues:,}건 | 프로젝트 {len(proj_row_counts):,}개"
    )

    return {
        "file_name": file_path.name,
        "file_size": file_size,
        "elapsed": elapsed,
        "total_rows": total_rows,
        "total_unique_issues": total_unique_issues,
        "total_content_chars": total_content_chars,
        "proj_names": proj_names,
        "proj_row_counts": proj_row_counts,
        "proj_issue_counts": {k: len(v) for k, v in proj_issue_sets.items()},
        "proj_sample_issue": proj_sample_issue,
        "proj_fields": proj_fields,
        "proj_detections": proj_detections,
        "proj_statuses": proj_statuses,
        "proj_content_chars": proj_content_chars,
        "field_names": field_names,
        "field_row_counts": field_row_counts,
        "field_projects": field_projects,
        "field_detections": field_detections,
        "field_content_chars": field_content_chars,
        "field_sample_snippet": field_sample_snippet,
        "field_sample_issue": field_sample_issue,
        "global_detections": global_detections,
        "global_statuses": global_statuses,
    }


def generate_dashboard_html(stats: dict, output_path: Path, cloud_map: dict[str, str]) -> None:
    """Generates an interactive standalone HTML dashboard report."""
    total_rows = stats["total_rows"]
    total_issues = stats["total_unique_issues"]
    total_projects = len(stats["proj_row_counts"])
    total_fields = len(stats["field_row_counts"])

    # Build Field Summary List
    fields_data = []
    for fid, r_cnt in stats["field_row_counts"].most_common():
        fname = stats["field_names"].get(fid) or fid
        c_field = cloud_map.get(fid, "")
        p_counter = stats["field_projects"].get(fid, Counter())
        d_counter = stats["field_detections"].get(fid, Counter())
        chars = stats["field_content_chars"].get(fid, 0)
        avg_chars = int(chars / r_cnt) if r_cnt > 0 else 0
        fields_data.append(
            {
                "field_id": fid,
                "field_name": fname,
                "cloud_field_id": c_field,
                "row_count": r_cnt,
                "project_count": len(p_counter),
                "top_projects": dict(p_counter.most_common(12)),
                "detections": dict(d_counter),
                "total_chars": chars,
                "avg_chars": avg_chars,
                "sample_issue": stats["field_sample_issue"].get(fid, ""),
                "sample_snippet": stats["field_sample_snippet"].get(fid, ""),
            }
        )

    # Build Project Summary List
    projects_data = []
    sorted_projs = sorted(
        stats["proj_row_counts"].keys(),
        key=lambda k: (stats["proj_issue_counts"].get(k, 0), stats["proj_row_counts"][k]),
        reverse=True,
    )
    for pkey in sorted_projs:
        r_cnt = stats["proj_row_counts"][pkey]
        i_cnt = stats["proj_issue_counts"].get(pkey, 0)
        pname = stats["proj_names"].get(pkey) or pkey
        f_counter = stats["proj_fields"].get(pkey, Counter())
        d_counter = stats["proj_detections"].get(pkey, Counter())
        s_counter = stats["proj_statuses"].get(pkey, Counter())
        chars = stats["proj_content_chars"].get(pkey, 0)
        projects_data.append(
            {
                "key": pkey,
                "name": pname,
                "issue_count": i_cnt,
                "row_count": r_cnt,
                "fields": dict(f_counter.most_common(10)),
                "field_count": len(f_counter),
                "detections": dict(d_counter),
                "statuses": dict(s_counter.most_common(5)),
                "total_chars": chars,
                "sample_issue": stats["proj_sample_issue"].get(pkey, ""),
            }
        )

    cloud_urls = load_cloud_env_urls()
    payload = {
        "meta": {
            "fileName": stats["file_name"],
            "fileSize": format_size(stats["file_size"]),
            "contentSize": format_size(stats["total_content_chars"]),
            "elapsed": format_duration(stats["elapsed"]),
            "totalProjects": total_projects,
            "totalIssues": total_issues,
            "totalRows": total_rows,
            "totalFields": total_fields,
            "mappedFields": sum(1 for f in fields_data if f["cloud_field_id"]),
            "sandboxUrl": cloud_urls.get("JIRA_CLOUD_SANDBOX_URL", ""),
            "opsUrl": cloud_urls.get("JIRA_CLOUD_OPS_URL", ""),
        },
        "detections": dict(stats["global_detections"].most_common()),
        "statuses": dict(stats["global_statuses"].most_common(10)),
        "fields": fields_data,
        "projects": projects_data,
    }

    json_payload = json.dumps(payload, ensure_ascii=False)

    html_doc = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JEditor 기본값 이슈 추출 대시보드 - {html.escape(stats["file_name"])}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #0f172a;
            --card: #1e293b;
            --card-hover: #26354d;
            --text: #f8fafc;
            --muted: #94a3b8;
            --accent: #38bdf8;
            --purple: #c084fc;
            --emerald: #34d399;
            --amber: #fbbf24;
            --rose: #fb7185;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 28px 24px 60px;
            line-height: 1.5;
        }}
        .container {{ max-width: 1640px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 24px;
            padding-bottom: 18px;
            border-bottom: 1px solid var(--border);
        }}
        .header h1 {{
            font-size: 1.85rem;
            font-weight: 800;
            background: linear-gradient(90deg, #38bdf8, #818cf8, #c084fc);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header p {{ color: var(--muted); font-size: 0.92rem; margin-top: 4px; }}
        .meta-badges {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .meta-badge {{
            background: var(--card);
            border: 1px solid var(--border);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.82rem;
            color: var(--muted);
        }}
        .meta-badge strong {{ color: var(--text); margin-left: 4px; }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .kpi {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 20px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }}
        .kpi-label {{ font-size: 0.82rem; color: var(--muted); font-weight: 600; text-transform: uppercase; }}
        .kpi-val {{ font-size: 2rem; font-weight: 800; margin-top: 6px; }}
        .kpi-sub {{ font-size: 0.8rem; color: var(--muted); margin-top: 4px; }}
        .charts-grid {{
            display: grid;
            grid-template-columns: 1.2fr 1.2fr 0.8fr;
            gap: 18px;
            margin-bottom: 26px;
        }}
        @media (max-width: 1200px) {{
            .charts-grid {{ grid-template-columns: 1fr; }}
        }}
        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 20px;
        }}
        .card-title {{
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .chart-box {{ position: relative; height: 320px; width: 100%; }}
        .tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 16px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 12px;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
        }}
        .tab-buttons {{ display: flex; gap: 8px; }}
        .tab-btn {{
            background: #162032;
            color: var(--muted);
            border: 1px solid var(--border);
            padding: 10px 18px;
            border-radius: 10px;
            font-size: 0.92rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .tab-btn.active {{
            background: rgba(56, 189, 248, 0.15);
            color: var(--accent);
            border-color: var(--accent);
        }}
        .controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
        .search-input, .sort-select {{
            background: #0f172a;
            border: 1px solid var(--border);
            color: var(--text);
            padding: 9px 14px;
            border-radius: 8px;
            font-size: 0.88rem;
            outline: none;
        }}
        .search-input {{ width: 300px; }}
        .search-input:focus, .sort-select:focus {{ border-color: var(--accent); }}
        .table-wrap {{ overflow-x: auto; max-height: 750px; overflow-y: auto; border-radius: 10px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; text-align: left; }}
        th {{
            position: sticky;
            top: 0;
            background: #162032;
            color: var(--muted);
            padding: 12px 14px;
            font-size: 0.78rem;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border);
            z-index: 5;
        }}
        td {{
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            vertical-align: top;
        }}
        tr:hover td {{ background: rgba(255,255,255,0.02); }}
        .pill {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.76rem;
            font-weight: 600;
            margin: 2px 4px 2px 0;
            background: rgba(255,255,255,0.07);
            color: #e2e8f0;
        }}
        .pill.proj {{ background: rgba(56, 189, 248, 0.14); color: var(--accent); border: 1px solid rgba(56, 189, 248, 0.3); }}
        .pill.cloud {{ background: rgba(52, 211, 153, 0.14); color: var(--emerald); border: 1px solid rgba(52, 211, 153, 0.3); }}
        .pill.unmapped {{ background: rgba(251, 113, 133, 0.14); color: var(--rose); }}
        .pill.attach {{ background: rgba(251, 191, 36, 0.15); color: var(--amber); }}
        a.cfg-link {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 700;
            margin: 3px 4px 2px 0;
            text-decoration: none;
            background: rgba(56, 189, 248, 0.18);
            color: var(--accent);
            border: 1px solid rgba(56, 189, 248, 0.4);
        }}
        a.cfg-link:hover {{ background: rgba(56, 189, 248, 0.32); }}
        .sub {{ display: block; color: var(--muted); font-size: 0.78rem; margin-top: 2px; }}
        .bar-bg {{ width: 100%; height: 6px; background: #334155; border-radius: 3px; margin-top: 5px; overflow: hidden; }}
        .bar-fill {{ height: 100%; background: linear-gradient(90deg, #38bdf8, #818cf8); }}
        details summary {{ cursor: pointer; color: var(--accent); font-size: 0.8rem; font-weight: 600; }}
        pre.snip {{
            margin-top: 6px;
            background: #0b1120;
            border: 1px solid var(--border);
            padding: 8px 10px;
            border-radius: 6px;
            font-size: 0.75rem;
            color: #cbd5e1;
            white-space: pre-wrap;
            word-break: break-all;
            max-height: 160px;
            overflow: auto;
        }}
        .hidden {{ display: none !important; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <div>
            <h1 id="pageTitle">JEditor 커스텀 필드 기본값(Default Value) 점검 대시보드</h1>
            <p id="pageDesc">HTML 기본값이 설정된 커스텀 필드 목록 및 Jira Cloud 관리자 설정 변경 가이드</p>
        </div>
        <div class="meta-badges">
            <div class="meta-badge">원본 파일: <strong id="metaFile"></strong></div>
            <div class="meta-badge">파일 크기: <strong id="metaSize"></strong></div>
            <div class="meta-badge">파싱 소요시간: <strong id="metaElapsed"></strong></div>
        </div>
    </div>

    <div class="kpi-grid">
        <div class="kpi">
            <div class="kpi-label">대상 커스텀 필드 수</div>
            <div class="kpi-val" style="color: var(--amber)" id="kpiFields">0</div>
            <div class="kpi-sub">HTML 기본값이 설정된 필드 (Unique)</div>
        </div>
        <div class="kpi">
            <div class="kpi-label" id="kpiSecondLabel">Cloud 매핑 완료 필드</div>
            <div class="kpi-val" style="color: var(--emerald)" id="kpiIssues">0</div>
            <div class="kpi-sub" id="kpiMappedSub">Cloud 필드 ID 매핑 확인</div>
        </div>
        <div class="kpi">
            <div class="kpi-label">영향받는 프로젝트 수</div>
            <div class="kpi-val" style="color: var(--accent)" id="kpiProjects">0</div>
            <div class="kpi-sub">해당 기본값 컨텍스트가 연결된 프로젝트</div>
        </div>
        <div class="kpi">
            <div class="kpi-label" id="kpiRowsLabel">총 컨텍스트 매핑 건수</div>
            <div class="kpi-val" style="color: var(--purple)" id="kpiRows">0</div>
            <div class="kpi-sub" id="kpiRowsSub">프로젝트 × 필드 컨텍스트 조합</div>
        </div>
    </div>

    <div class="charts-grid">
        <div class="card">
            <div class="card-title"><span id="projChartTitle">프로젝트별 기본값 설정 필드 수 (Top 20)</span></div>
            <div class="chart-box"><canvas id="projChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-title"><span id="fieldChartTitle">커스텀 필드별 연결 프로젝트/컨텍스트 수 (Top 15)</span></div>
            <div class="chart-box"><canvas id="fieldChart"></canvas></div>
        </div>
        <div class="card">
            <div class="card-title"><span>기본값 컨텐츠 유형 비율</span></div>
            <div class="chart-box"><canvas id="detChart"></canvas></div>
        </div>
    </div>

    <div class="card">
        <div class="tabs">
            <div class="tab-buttons">
                <button class="tab-btn active" id="btnFields" onclick="switchTab('fields')">커스텀 필드별 기본값 설정 현황 (<span id="tabFieldCnt">0</span>)</button>
                <button class="tab-btn" id="btnProjects" onclick="switchTab('projects')">프로젝트별 연결 현황 (<span id="tabProjCnt">0</span>)</button>
            </div>
            <div class="controls">
                <input type="search" id="searchInput" class="search-input" placeholder="프로젝트 키, 필드명, customfield_ID 검색..." oninput="renderActiveTable()">
                <select id="sortSelect" class="sort-select" onchange="renderActiveTable()">
                    <option value="count-desc">건수 많은 순 ↓</option>
                    <option value="count-asc">건수 적은 순 ↑</option>
                    <option value="name-asc">이름 오름차순 ↑</option>
                </select>
                <span id="visibleInfo" style="color: var(--muted); font-size: 0.84rem;"></span>
            </div>
        </div>

        <div id="viewFields" class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th style="width:55px">#</th>
                        <th style="width:25%">커스텀 필드 (DC → Cloud 설정 링크)</th>
                        <th style="width:13%">연결 프로젝트/건수</th>
                        <th style="width:12%">비중 / 용량</th>
                        <th style="width:24%">연결된 프로젝트</th>
                        <th>HTML 기본값 원문 미리보기</th>
                    </tr>
                </thead>
                <tbody id="fieldBody"></tbody>
            </table>
        </div>

        <div id="viewProjects" class="table-wrap hidden">
            <table>
                <thead>
                    <tr>
                        <th style="width:55px">#</th>
                        <th style="width:18%">프로젝트</th>
                        <th style="width:13%" id="thProjCol3">연결된 필드 종류</th>
                        <th style="width:13%">컨텍스트/레코드 수</th>
                        <th style="width:14%">전체 대비 비중</th>
                        <th>연결된 HTML 기본값 커스텀 필드</th>
                        <th style="width:14%">유형</th>
                    </tr>
                </thead>
                <tbody id="projBody"></tbody>
            </table>
        </div>
    </div>
</div>

<script>
const DATA = {json_payload};
let currentTab = 'fields';
const isIssueExport = DATA.meta.totalIssues > 0;

function esc(s) {{
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
}}

function formatBytes(chars) {{
    const units = ['B', 'KB', 'MB', 'GB'];
    let v = chars;
    for (const u of units) {{
        if (v < 1024) return v.toFixed(1) + ' ' + u;
        v /= 1024;
    }}
    return v.toFixed(1) + ' TB';
}}

function init() {{
    document.getElementById('metaFile').textContent = DATA.meta.fileName;
    document.getElementById('metaSize').textContent = DATA.meta.fileSize;
    document.getElementById('metaElapsed').textContent = DATA.meta.elapsed;

    document.getElementById('kpiProjects').textContent = DATA.meta.totalProjects.toLocaleString();
    document.getElementById('kpiFields').textContent = DATA.meta.totalFields.toLocaleString();
    document.getElementById('kpiRows').textContent = DATA.meta.totalRows.toLocaleString();

    if (isIssueExport) {{
        document.getElementById('kpiSecondLabel').textContent = '고유 대상 이슈 수';
        document.getElementById('kpiIssues').textContent = DATA.meta.totalIssues.toLocaleString();
        document.getElementById('kpiMappedSub').textContent = `Cloud 매핑 확인 필드: ${{DATA.meta.mappedFields}} / ${{DATA.meta.totalFields}}개`;
        document.getElementById('thProjCol3').textContent = '고유 이슈 수';
    }} else {{
        document.getElementById('kpiSecondLabel').textContent = 'Cloud 매핑 완료 필드';
        document.getElementById('kpiIssues').textContent = `${{DATA.meta.mappedFields.toLocaleString()}} / ${{DATA.meta.totalFields.toLocaleString()}}`;
        document.getElementById('kpiMappedSub').textContent = 'id-mappings.html 기준 Cloud 커스텀 필드 매핑';
    }}

    document.getElementById('tabFieldCnt').textContent = DATA.fields.length.toLocaleString();
    document.getElementById('tabProjCnt').textContent = DATA.projects.length.toLocaleString();

    renderCharts();
    renderActiveTable();
}}

function renderCharts() {{
    const topProjs = DATA.projects.slice(0, 20);
    new Chart(document.getElementById('projChart'), {{
        type: 'bar',
        data: {{
            labels: topProjs.map(p => p.key),
            datasets: [{{
                label: isIssueExport ? '고유 이슈 수' : '설정된 필드/컨텍스트 수',
                data: topProjs.map(p => p.issue_count || p.row_count),
                backgroundColor: '#38bdf8',
                borderRadius: 5
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
                y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }}
            }}
        }}
    }});

    const topFields = DATA.fields.slice(0, 15);
    new Chart(document.getElementById('fieldChart'), {{
        type: 'bar',
        data: {{
            labels: topFields.map(f => f.field_name.length > 22 ? f.field_name.slice(0, 22) + '…' : f.field_name),
            datasets: [{{
                label: '연결 건수',
                data: topFields.map(f => f.row_count),
                backgroundColor: '#c084fc',
                borderRadius: 5
            }}]
        }},
        options: {{
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
                y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }}
            }}
        }}
    }});

    const detEntries = Object.entries(DATA.detections);
    new Chart(document.getElementById('detChart'), {{
        type: 'doughnut',
        data: {{
            labels: detEntries.map(e => e[0]),
            datasets: [{{
                data: detEntries.map(e => e[1]),
                backgroundColor: ['#34d399', '#fbbf24', '#38bdf8', '#fb7185'],
                borderWidth: 0
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{
                legend: {{ position: 'bottom', labels: {{ color: '#cbd5e1' }} }}
            }}
        }}
    }});
}}

function switchTab(tab) {{
    currentTab = tab;
    document.getElementById('btnFields').classList.toggle('active', tab === 'fields');
    document.getElementById('btnProjects').classList.toggle('active', tab === 'projects');
    document.getElementById('viewFields').classList.toggle('hidden', tab !== 'fields');
    document.getElementById('viewProjects').classList.toggle('hidden', tab !== 'projects');
    renderActiveTable();
}}

function buildAdminLinks(cloudFieldId) {{
    if (!cloudFieldId || cloudFieldId.includes(',')) return '';
    const numId = cloudFieldId.replace('customfield_', '').trim();
    if (!/^\\d+$/.test(numId)) return '';
    let links = '';
    if (DATA.meta.sandboxUrl) {{
        const u = `${{DATA.meta.sandboxUrl}}/secure/admin/ConfigureCustomField!default.jspa?customFieldId=${{numId}}`;
        links += `<a class="cfg-link" href="${{esc(u)}}" target="_blank" rel="noopener noreferrer">Sandbox 기본값 설정 ↗</a>`;
    }}
    if (DATA.meta.opsUrl) {{
        const u = `${{DATA.meta.opsUrl}}/secure/admin/ConfigureCustomField!default.jspa?customFieldId=${{numId}}`;
        links += `<a class="cfg-link" href="${{esc(u)}}" target="_blank" rel="noopener noreferrer">Cloud Ops 기본값 설정 ↗</a>`;
    }}
    return links ? `<div style="margin-top:4px">${{links}}</div>` : '';
}}

function renderActiveTable() {{
    const q = document.getElementById('searchInput').value.trim().toLowerCase();
    const sortMode = document.getElementById('sortSelect').value;
    const totalRows = Math.max(1, DATA.meta.totalRows);

    if (currentTab === 'fields') {{
        let list = DATA.fields.filter(f => {{
            if (!q) return true;
            const hay = `${{f.field_id}} ${{f.field_name}} ${{f.cloud_field_id}} ${{Object.keys(f.top_projects).join(' ')}}`.toLowerCase();
            return hay.includes(q);
        }});
        list.sort((a, b) => {{
            if (sortMode === 'count-asc') return a.row_count - b.row_count;
            if (sortMode === 'name-asc') return a.field_name.localeCompare(b.field_name, 'ko');
            return b.row_count - a.row_count;
        }});
        document.getElementById('visibleInfo').textContent = `${{list.length.toLocaleString()}}개 필드 표시`;

        const htmlRows = list.map((f, idx) => {{
            const pct = ((f.row_count / totalRows) * 100).toFixed(1);
            const cloudBadge = f.cloud_field_id
                ? `<span class="pill cloud">Cloud: ${{esc(f.cloud_field_id)}}</span>`
                : `<span class="pill unmapped">Cloud 매핑 미확인</span>`;
            const adminLinks = buildAdminLinks(f.cloud_field_id);
            const projPills = Object.entries(f.top_projects).map(([pk, cnt]) =>
                `<span class="pill proj">${{esc(pk)}}: ${{cnt.toLocaleString()}}</span>`
            ).join('');
            const moreProj = f.project_count > 12 ? `<span class="sub">+${{f.project_count - 12}}개 프로젝트 더 있음 (총 ${{f.project_count}}개)</span>` : '';
            const snip = f.sample_snippet
                ? `<details><summary>기본값 원문 보기</summary><pre class="snip">${{esc(f.sample_snippet)}}</pre></details>`
                : '-';

            return `<tr>
                <td style="color:var(--muted);font-weight:600">${{idx + 1}}</td>
                <td>
                    <strong style="font-size:0.95rem">${{esc(f.field_name)}}</strong>
                    <span class="sub">DC: ${{esc(f.field_id)}}</span>
                    <div style="margin-top:4px">${{cloudBadge}}</div>
                    ${{adminLinks}}
                </td>
                <td style="font-weight:700;font-size:1rem">${{f.project_count.toLocaleString()}}개 프로젝트<span class="sub">컨텍스트 ${{f.row_count.toLocaleString()}}건</span></td>
                <td>
                    <div>${{pct}}% <span style="color:var(--muted);font-size:0.78rem">(${{formatBytes(f.total_chars)}})</span></div>
                    <div class="bar-bg"><div class="bar-fill" style="width:${{Math.min(100, pct)}}%"></div></div>
                    <span class="sub">평균 ${{formatBytes(f.avg_chars)}} / 건</span>
                </td>
                <td>${{projPills}}${{moreProj}}</td>
                <td>${{snip}}</td>
            </tr>`;
        }});
        document.getElementById('fieldBody').innerHTML = htmlRows.join('');
    }} else {{
        const totalIssues = Math.max(1, DATA.meta.totalIssues || totalRows);
        let list = DATA.projects.filter(p => {{
            if (!q) return true;
            const hay = `${{p.key}} ${{p.name}} ${{Object.keys(p.fields).join(' ')}}`.toLowerCase();
            return hay.includes(q);
        }});
        list.sort((a, b) => {{
            const ca = a.issue_count || a.row_count;
            const cb = b.issue_count || b.row_count;
            if (sortMode === 'count-asc') return ca - cb;
            if (sortMode === 'name-asc') return a.key.localeCompare(b.key, 'ko');
            return cb - ca;
        }});
        document.getElementById('visibleInfo').textContent = `${{list.length.toLocaleString()}}개 프로젝트 표시`;

        const htmlRows = list.map((p, idx) => {{
            const metric = p.issue_count || p.row_count;
            const pct = ((metric / totalIssues) * 100).toFixed(1);
            const fPills = Object.entries(p.fields).map(([fn, cnt]) =>
                `<span class="pill">${{esc(fn)}}: ${{cnt.toLocaleString()}}</span>`
            ).join('');
            const dPills = Object.entries(p.detections).map(([dn, cnt]) => {{
                const cls = dn.includes('Attachment') ? 'pill attach' : 'pill';
                return `<span class="${{cls}}">${{esc(dn)}}: ${{cnt.toLocaleString()}}</span>`;
            }}).join('');
            const col3Val = isIssueExport ? `${{p.issue_count.toLocaleString()}} 개` : `${{p.field_count.toLocaleString()}} 개 필드`;
            return `<tr>
                <td style="color:var(--muted);font-weight:600">${{idx + 1}}</td>
                <td>
                    <span class="pill proj" style="font-size:0.86rem">${{esc(p.key)}}</span>
                    <span class="sub">${{esc(p.name)}}</span>
                </td>
                <td style="font-weight:700;font-size:1rem;color:var(--emerald)">${{col3Val}}</td>
                <td style="font-weight:600">${{p.row_count.toLocaleString()}} 건<span class="sub">${{formatBytes(p.total_chars)}}</span></td>
                <td>
                    <div>${{pct}}%</div>
                    <div class="bar-bg"><div class="bar-fill" style="width:${{Math.min(100, pct)}}%"></div></div>
                </td>
                <td>${{fPills || '-'}}</td>
                <td>${{dPills}}</td>
            </tr>`;
        }});
        document.getElementById('projBody').innerHTML = htmlRows.join('');
    }}
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
    print(f"[SUCCESS] 대시보드 리포트 생성 완료: '{output_path}' ({format_size(output_path.stat().st_size)})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="1GB+ 대용량 쿼리 결과 HTML을 저메모리 스트리밍으로 분석하여 대시보드 HTML로 변환합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""예시:
  python make_default_values_dashboard.py
  python make_default_values_dashboard.py default_values.html
  python make_default_values_dashboard.py default_values.html default_values_dashboard.html
""",
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"입력 쿼리 결과 HTML 파일 (기본값: {DEFAULT_INPUT.name})",
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"출력 대시보드 HTML 파일 (기본값: {DEFAULT_OUTPUT.name})",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Cloud 커스텀 필드 ID 매핑 HTML 파일 (기본값: ../id-mappings.html 자동 감지)",
    )
    parser.add_argument(
        "--chunk-mb",
        type=int,
        default=8,
        help="스트리밍 읽기 청크 크기(MB) (기본값: 8)",
    )

    args = parser.parse_args()

    if not args.input.is_file():
        print(f"[ERROR] 입력 파일을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        print("사용법: python make_default_values_dashboard.py <쿼리결과.html> [출력대시보드.html]", file=sys.stderr)
        return 1

    if args.input.resolve() == args.output.resolve():
        print("[ERROR] 입력 파일과 출력 파일 경로가 같을 수 없습니다.", file=sys.stderr)
        return 1

    cloud_map = load_cloud_field_mapping(args.mapping)
    if cloud_map:
        print(f"[INFO] Cloud 커스텀 필드 매핑 {len(cloud_map):,}건 로드 완료.")

    stats = stream_analyze_html(args.input, chunk_mb=args.chunk_mb)
    generate_dashboard_html(stats, args.output, cloud_map)
    return 0


if __name__ == "__main__":
    sys.exit(main())
