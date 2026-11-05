import os
import json
import re
import csv
import html
from urllib.parse import quote
from datetime import datetime
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if __file__ else '.'
MIGRATION_BOX_FILE = os.path.join(SCRIPT_DIR, "migration_box.json")
BOXES_FILE = os.path.join(SCRIPT_DIR, "boxes.json")
HIERARCHY_FILE = os.path.join(SCRIPT_DIR, "hierarchy.json")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "step1_box_report.html")
LOG_FILE = os.path.join(SCRIPT_DIR, "migration_run.log")
PROJECTS_FILE = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "projects.txt"))
ALL_BOXES_CACHE_FILE = os.path.join(SCRIPT_DIR, "all_boxes_cache.json")
TSV_EXPORT_FILE = os.path.join(SCRIPT_DIR, "exported_boxes.tsv")
CSV_EXPORT_FILE = os.path.join(SCRIPT_DIR, "exported_boxes.csv")

def get_hierarchy_prefix(depth):
    """Returns a visual tree prefix for hierarchy display (e.g. │  ⎿  )."""
    if depth <= 0:
        return ""
    elif depth == 1:
        return "│  ⎿  "
    else:
        return ("│  " * (depth - 1)) + "│  ⎿  "

def build_hierarchy_map(hierarchy_file):
    """
    Parses hierarchy.json and builds a mapping of boxId -> {parentBoxId, depth}.
    Used as fallback when cached boxes do not have hierarchy metadata.
    """
    if not os.path.exists(hierarchy_file):
        return {}
    try:
        with open(hierarchy_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            cargo = data
        elif isinstance(data, dict):
            cargo = data.get("cargo", data)
        else:
            cargo = {}
        hierarchy_map = {}

        def traverse(node, parent_id="ROOT", depth=0):
            if isinstance(node, list):
                for item in node:
                    traverse(item, parent_id=parent_id, depth=depth)
                return
            if not isinstance(node, dict):
                return
            b_id = str(node.get("boxId") or "")
            if b_id.upper() == "ROOT":
                for child in node.get("boxChildren") or []:
                    traverse(child, parent_id="ROOT", depth=0)
                return
            if b_id:
                eff_parent = str(parent_id or "ROOT")
                if eff_parent == "None":
                    eff_parent = "ROOT"
                hierarchy_map[b_id] = {
                    "parentBoxId": eff_parent,
                    "depth": depth
                }
            for child in node.get("boxChildren") or []:
                traverse(child, parent_id=b_id, depth=depth + 1)

        traverse(cargo)
        return hierarchy_map
    except Exception as e:
        print(f"Warning: Failed to parse hierarchy.json for hierarchy map: {e}")
        return {}

def sort_boxes_by_hierarchy(boxes):
    """
    Sorts boxes so children appear directly under their parent in DFS order.
    Root boxes are prioritized by migration status while preserving tree coherence.
    """
    box_by_id = {str(b.get("boxId")): b for b in boxes if b.get("boxId") is not None}
    children_by_parent = {}
    roots = []

    for b in boxes:
        bid = str(b.get("boxId"))
        pid = str(b.get("parentBoxId") or "ROOT")
        if pid == "ROOT" or pid not in box_by_id or pid == bid:
            roots.append(b)
        else:
            children_by_parent.setdefault(pid, []).append(b)

    def root_sort_key(box):
        if box.get("is_migration_target"):
            tier = 0
        elif not box.get("is_closed"):
            tier = 1
        else:
            tier = 2
        raw_id = box.get("boxId", "")
        try:
            num_id = int(raw_id)
        except (ValueError, TypeError):
            num_id = float("inf")
        return (tier, num_id, str(raw_id))

    roots.sort(key=root_sort_key)

    ordered = []
    def dfs(box):
        ordered.append(box)
        bid = str(box.get("boxId"))
        for child in children_by_parent.get(bid, []):
            dfs(child)

    for r in roots:
        dfs(r)

    seen_ids = {str(b.get("boxId")) for b in ordered}
    for b in boxes:
        if str(b.get("boxId")) not in seen_ids:
            ordered.append(b)
            seen_ids.add(str(b.get("boxId")))

    return ordered

def export_boxes_for_excel(boxes, tsv_path, csv_path):
    """Exports the box list into Excel-friendly TSV and CSV (UTF-8 with BOM) files."""
    headers = [
        "순번", "Box ID", "Box Name", "Box Type", "Box 상태 (Status)",
        "시작일 (Start Date)", "종료일 (End Date)", 
        "소유자 (Owner Name)", "소유자 이메일 (Owner Email)", 
        "연동 Jira 프로젝트 (Jira Projects)", "Scope 상태 (Scope Status)", 
        "JQL 설정 (Narrowing Query)", "JQL 검증 상태 (JQL Status)",
        "검증 상세 / 비고 (Validation Detail)", 
        "BigGantt DC URL", "BigPicture DC URL",
        "상위 Box ID (Parent Box ID)", "계층 레벨 (Depth)"
    ]
    
    rows = []
    for idx, b in enumerate(boxes, 1):
        box_id = b.get("boxId", "")
        parent_id = b.get("parentBoxId") or "ROOT"
        depth = b.get("depth", 0)
        prefix = b.get("hierarchy_prefix")
        if prefix is None and depth > 0:
            prefix = get_hierarchy_prefix(depth)
        raw_name = str(b.get("boxName", "") or "")
        display_name = f"{prefix}{raw_name}" if prefix else raw_name

        base_url = b.get("source_base_url", "")
        gantt_url = f"{base_url.rstrip('/')}/plugins/servlet/softwareplant-biggantt/#/box/{quote(str(box_id), safe='')}/g" if base_url and box_id else ""
        bp_url = f"{base_url}/secure/BigPicture.jspa?boxId={box_id}#box/{box_id}" if base_url and box_id else ""
        
        scope_status = b.get("scope_status", "VALID" if b.get("jira_projects") else "EMPTY_SCOPE")
        scope_label = {
            "VALID": "정상 (Active)",
            "PARTIAL_VALID": "부분 정상 (일부 404 제외)",
            "EMPTY_SCOPE": "Scope 누락 (프로젝트 없음)",
            "PROJECT_NOT_FOUND": "프로젝트 404 (Jira에 없음)",
            "ALL_ARCHIVED": "프로젝트 보관됨 (Archived)",
            "NON_PROJECT_SCOPE": "비프로젝트 Scope (Filter/Board)"
        }.get(scope_status, scope_status)
        
        box_st = str(b.get("effective_box_status") or b.get("boxStatus") or "-").upper()
        is_jql_empty = b.get("is_jql_empty") or (bool(b.get("narrowing_query") and str(b.get("narrowing_query")).strip()) and b.get("jql_total_matched", 0) == 0)
        if box_st == "CLOSED":
            status_label = "CLOSED (제외)"
        elif not b.get("is_scope_valid", True):
            status_label = f"OPEN ({scope_label} 제외)"
        elif is_jql_empty:
            status_label = "OPEN (JQL 0건 제외)"
        elif b.get("jql_status") in ("INVALID", "ERROR"):
            status_label = "OPEN (JQL 오류 제외)"
        elif b.get("migration_selected", True):
            status_label = "OPEN (이관 대상)"
        else:
            status_label = f"OPEN ({scope_label} 제외)"

        jql_query = b.get("narrowing_query") or "-"
        jql_status = b.get("jql_status", "NONE")
        jql_error = b.get("jql_error")
        if jql_status == "VALID":
            total_issues = b.get("jql_total_matched", 0)
            if total_issues == 0 and b.get("narrowing_query"):
                jql_status_label = "정상 (Matched: 0건 - 제외)"
            else:
                jql_status_label = f"정상 (Matched: {total_issues}건)"
        elif jql_status == "WARNING":
            total_issues = b.get("jql_total_matched", 0)
            if total_issues == 0 and b.get("narrowing_query"):
                jql_status_label = f"경고: 0건 Matched (제외) - {jql_error}" if jql_error else "경고 (0건 Matched - 제외)"
            else:
                jql_status_label = f"경고: {jql_error}" if jql_error else "JQL 검증 경고"
        elif jql_status == "INVALID":
            jql_status_label = f"오류: {jql_error}" if jql_error else "문법/필드 오류"
        elif jql_status == "ERROR":
            jql_status_label = f"검증실패: {jql_error}"
        else:
            jql_status_label = "미설정 (None)"
            
        validation_detail = b.get("filter_reason") or b.get("validation_status") or "정상"
        if b.get("not_found_projects"):
            validation_detail += f" [제외된 404 프로젝트: {', '.join(b['not_found_projects'])}]"
        if jql_status == "INVALID":
            validation_detail += f" [JQL 오류: {jql_error}]"
            
        projects_str = ", ".join(b.get("jira_projects", []))
        clean_name = display_name.replace("\t", " ").replace("\r", "").replace("\n", " ")
        clean_jql = str(jql_query).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        clean_detail = str(validation_detail).replace("\t", " ").replace("\r", "").replace("\n", " ")
        
        rows.append([
            str(b.get("index") or idx),
            str(box_id),
            clean_name,
            str(b.get("boxType", "") or "-"),
            status_label,
            str(b.get("boxStartDate", "") or "-"),
            str(b.get("boxEndDate", "") or "-"),
            str(b.get("owner_name", "") or "-"),
            str(b.get("owner_email", "") or "-"),
            projects_str,
            scope_label,
            clean_jql,
            jql_status_label,
            clean_detail,
            gantt_url,
            bp_url,
            str(parent_id),
            str(depth)
        ])

    try:
        with open(tsv_path, "w", encoding="utf-8-sig") as f:
            f.write("\t".join(headers) + "\n")
            for r in rows:
                f.write("\t".join(r) + "\n")
    except Exception as e:
        print(f"Failed to save TSV export: {e}")

    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
    except Exception as e:
        print(f"Failed to save CSV export: {e}")

def load_target_projects():
    if not os.path.exists(PROJECTS_FILE):
        return set()
    with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
        return {line.strip().upper() for line in f if line.strip()}

def sync_and_refilter_boxes():
    target_projects = load_target_projects()
    if not target_projects:
        print("ℹ️  No projects.txt found or empty. Using existing migration_box.json as-is.")
        return False, 0

    cache_source = None
    if os.path.exists(ALL_BOXES_CACHE_FILE):
        cache_source = ALL_BOXES_CACHE_FILE
    elif os.path.exists(MIGRATION_BOX_FILE):
        cache_source = MIGRATION_BOX_FILE

    if not cache_source:
        print("⚠️  No cached box json files found for re-filtering.")
        return False, len(target_projects)

    with open(cache_source, "r", encoding="utf-8") as f:
        all_cached_boxes = json.load(f)

    matched_boxes = []
    for box in all_cached_boxes:
        # Check scope validity
        is_scope_valid = box.get("is_scope_valid", True)
        scope_status = box.get("scope_status", "VALID")
        box_status = str(box.get("effective_box_status") or box.get("boxStatus") or "").upper()
        if (box_status == "CLOSED" or not is_scope_valid or
                scope_status in ("CLOSED", "EMPTY_SCOPE", "PROJECT_NOT_FOUND", "ALL_ARCHIVED")):
            continue

        box_projects = [p.upper() for p in (box.get("active_projects") or box.get("jira_projects", []))]
        matched_keys = [p for p in box_projects if p in target_projects]
        if matched_keys:
            updated_box = dict(box)
            updated_box["jira_projects"] = matched_keys
            matched_boxes.append(updated_box)

    # Update migration_box.json instantly
    with open(MIGRATION_BOX_FILE, "w", encoding="utf-8") as f:
        json.dump(matched_boxes, f, indent=2, ensure_ascii=False)

    print(f"⚡ [Fast Re-filter] Loaded {len(target_projects)} target projects from '{PROJECTS_FILE}'.")
    print(f"⚡ [Fast Re-filter] Re-filtered {len(matched_boxes)} matching boxes from cache '{os.path.basename(cache_source)}'.")
    print(f"⚡ [Fast Re-filter] Updated '{os.path.basename(MIGRATION_BOX_FILE)}' instantly (No API calls required)!")
    return True, len(target_projects)

def configured_project_references(box):
    """Keep the original Box settings visible even after migration filtering."""
    configured = box.get("configured_projects")
    if configured is None:
        configured = list(dict.fromkeys(
            (box.get("jira_projects") or []) +
            (box.get("not_found_projects") or []) +
            (box.get("archived_projects") or [])
        ))
    recorded = {str(ref.get("id")): ref for ref in box.get("scope_references", [])
                if ref.get("type") == "PROJECT"}
    missing = set(box.get("not_found_projects", []))
    archived = set(box.get("archived_projects", []))
    result = []
    for key in configured:
        ref = recorded.get(str(key))
        status = (ref.get("status") if ref else
                  "NOT_FOUND" if key in missing else
                  "ARCHIVED" if key in archived else "UNVERIFIED")
        result.append({"key": str(key), "status": status,
                       "resolved_key": ref.get("resolved_key") if ref else None})
    return result

def parse_log_file(log_path):
    """Parse migration_run.log for API failures and runtime issues if available."""
    issues = []
    if not os.path.exists(log_path):
        return issues
    
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Look for EXECUTION ISSUE REPORT section or error logs
    issue_section = re.search(r"EXECUTION ISSUE REPORT.*?\n(=+\n|\Z)", content, re.DOTALL)
    if issue_section:
        lines = issue_section.group(0).splitlines()
        for line in lines:
            # Match lines like "1. [HTTP 404] Resource not found: ... (x2)"
            match = re.match(r"^\d+\.\s+\[(.*?)\]\s+(.*)$", line.strip())
            if match:
                cat, msg = match.groups()
                issues.append({"category": cat, "message": msg})
    
    # Fallback to direct HTTP / Network error log parsing if issue report is absent
    if not issues:
        for line in content.splitlines():
            line_str = line.strip()
            # Match explicit HTTP status errors, network failures, timeouts, exceptions
            if re.search(r"Error:\s*(4\d\d|5\d\d)|\[HTTP\s*(4\d\d|5\d\d)\]|ConnectionError|Timeout|Exception|Unauthorized|Rate limited", line_str, re.IGNORECASE):
                if not any(skip_kw in line_str for skip_kw in ["Fetching:", "Users not found (404): 0", "BigGantt URL", "BigPicture URL", "Jira Projects:"]):
                    issues.append({"category": "API_FAIL", "message": line_str})
    
    return issues

def generate_report():
    print(f"Generating Step 1 HTML Report...")
    is_refiltered, target_proj_count = sync_and_refilter_boxes()
    
    target_projects = load_target_projects()

    scanned_boxes = []
    if os.path.exists(ALL_BOXES_CACHE_FILE):
        with open(ALL_BOXES_CACHE_FILE, "r", encoding="utf-8") as f:
            scanned_boxes = json.load(f)
    elif os.path.exists(MIGRATION_BOX_FILE):
        with open(MIGRATION_BOX_FILE, "r", encoding="utf-8") as f:
            scanned_boxes = json.load(f)
    elif os.path.exists(BOXES_FILE):
        with open(BOXES_FILE, "r", encoding="utf-8") as f:
            scanned_boxes = json.load(f)

    # Filter to target boxes:
    # 1) Exclude boxes without project scope (EMPTY_SCOPE or empty projects)
    # 2) If target_projects configured, filter to boxes whose projects match target_projects
    boxes = []
    for b in scanned_boxes:
        proj_keys = [p.upper() for p in (b.get("configured_projects") or b.get("jira_projects") or [])]
        scope_st = b.get("scope_status")
        if not proj_keys or scope_st == "EMPTY_SCOPE":
            continue
        if target_projects and not any(k in target_projects for k in proj_keys):
            continue
        boxes.append(b)

    if not boxes and os.path.exists(MIGRATION_BOX_FILE):
        with open(MIGRATION_BOX_FILE, "r", encoding="utf-8") as f:
            raw_mb = json.load(f)
            boxes = [b for b in raw_mb if (b.get("jira_projects") or b.get("configured_projects")) and b.get("scope_status") != "EMPTY_SCOPE"]

    # 1. Scope, Date, Owner & JQL Validation Rules
    def is_owner_failed(box):
        email = str(box.get("owner_email", "") or "").lower()
        name = str(box.get("owner_name", "") or "").lower()
        if not email or not name:
            return True
        if "failed" in email or "failed" in name or "not found" in email or "not found" in name:
            return True
        return False

    def is_date_anomaly(box):
        start = box.get("boxStartDate")
        end = box.get("boxEndDate")
        if not start or not end:
            return True
        if "99" in end or "9999" in end: # temporary placeholder dates like 31/DEC/99
            return True
        return False

    def is_scope_anomaly(box):
        if not box.get("jira_projects") and not box.get("configured_projects"):
            return False
        if box.get("is_scope_valid") is False and str(box.get("effective_box_status") or "").upper() != "CLOSED":
            return True
        scope_st = box.get("scope_status")
        if scope_st in ("PROJECT_NOT_FOUND", "ALL_ARCHIVED", "PARTIAL_VALID",
                        "SCOPE_CHECK_FAILED", "SCOPE_FETCH_FAILED"):
            return True
        if any(ref["status"] not in ("ACTIVE", "UNVERIFIED")
               for ref in configured_project_references(box)):
            return True
        return False

    def is_jql_invalid(box):
        return box.get("jql_status") in ("INVALID", "ERROR")

    def is_jql_empty(box):
        nq = box.get("narrowing_query")
        if not nq or not str(nq).strip():
            return False
        return box.get("jql_total_matched", 0) == 0 or box.get("is_jql_empty") is True

    def is_migration_target(box):
        if str(box.get("effective_box_status") or box.get("boxStatus") or "").upper() == "CLOSED" or box.get("is_closed"):
            return False
        if is_scope_anomaly(box) or box.get("is_scope_valid") is False:
            return False
        if is_jql_empty(box):
            return False
        if is_jql_invalid(box):
            return False
        return True

    # Box Counts (Open, Closed, Migration Targets, JQL 0 results)
    closed_boxes = [b for b in boxes if str(b.get("effective_box_status") or b.get("boxStatus") or "").upper() == "CLOSED" or b.get("is_closed")]
    closed_box_count = len(closed_boxes)
    migration_boxes = [b for b in boxes if is_migration_target(b)]
    migration_box_count = len(migration_boxes)
    open_box_count = migration_box_count
    total_boxes = len(boxes)

    owner_failed_count = sum(1 for b in boxes if is_owner_failed(b))
    date_anomaly_count = sum(1 for b in boxes if is_date_anomaly(b))
    scope_anomaly_count = sum(1 for b in boxes if is_scope_anomaly(b))
    jql_invalid_count = sum(1 for b in boxes if is_jql_invalid(b))
    jql_empty_count = sum(1 for b in boxes if is_jql_empty(b))
    jql_warning_count = sum(1 for b in boxes if b.get("jql_status") == "WARNING")
    has_jql_count = sum(1 for b in boxes if b.get("narrowing_query"))
    no_project_count = 0
    # Duplicate Box Names & Deduplication Reduction Metrics
    box_names = [b.get("boxName", "").strip() for b in boxes if b.get("boxName")]
    name_counts = Counter(box_names)
    unique_box_count = len(name_counts)
    duplicate_names = {name for name, count in name_counts.items() if count > 1}
    duplicate_box_count = sum(1 for b in boxes if b.get("boxName", "").strip() in duplicate_names)
    
    reducible_count = total_boxes - unique_box_count
    reduction_rate = round((reducible_count / total_boxes * 100), 2) if total_boxes > 0 else 0.0
    remaining_rate = round((unique_box_count / total_boxes * 100), 2) if total_boxes > 0 else 0.0

    # Top duplicate breakdown
    top_duplicates = sorted(
        [{"name": name, "count": count, "reduce": count - 1} for name, count in name_counts.items() if count > 1],
        key=lambda x: x["count"],
        reverse=True
    )

    # Jira Project Distribution Statistics
    all_projects = []
    for b in boxes:
        all_projects.extend(b.get("jira_projects", []))
    
    project_counts = Counter(all_projects)
    total_project_links = len(all_projects)
    unique_projects_count = len(project_counts)
    top_projects = sorted(
        [{"key": k, "count": v, "rate": round((v / total_boxes * 100), 1) if total_boxes > 0 else 0.0} for k, v in project_counts.items()],
        key=lambda x: x["count"],
        reverse=True
    )

    # Box Type Distribution
    type_counts = Counter([str(b.get("boxType", "Unknown")) for b in boxes])

    # Parse Request Fail API issues from log
    api_issues = parse_log_file(LOG_FILE)

    # Build Top Duplicates HTML rows
    dup_table_rows = ""
    if top_duplicates:
        for item in top_duplicates:
            dup_table_rows += f"""
                <tr>
                    <td style="font-weight: 500;">{item['name']}</td>
                    <td style="text-align: center;"><span class="badge badge-warning">{item['count']}개</span></td>
                    <td style="text-align: center; color: #fcd34d; font-weight: 700;">-{item['reduce']}개</td>
                </tr>
            """
    else:
        dup_table_rows = '<tr><td colspan="3" style="text-align:center; color:var(--success); padding: 12px;">✅ 중복된 Box 이름이 없습니다.</td></tr>'

    # Build Top Projects HTML rows
    project_list_rows = ""
    if top_projects:
        for p in top_projects:
            project_list_rows += f"""
                <div class="project-item">
                    <span class="project-key-badge">{p['key']}</span>
                    <div class="project-bar-wrap">
                        <div class="project-bar-bg">
                            <div class="project-bar-fill" style="width: {p['rate']}%;"></div>
                        </div>
                    </div>
                    <span class="project-count">{p['count']}개 Box ({p['rate']}%)</span>
                </div>
            """
    else:
        project_list_rows = '<div style="color:var(--text-muted); font-size:0.8rem; padding:12px;">연동된 Jira 프로젝트가 없습니다.</div>'

    # Enhance box data with status flags and hierarchy metadata
    hierarchy_map = build_hierarchy_map(HIERARCHY_FILE)
    processed_boxes = []
    for idx, b in enumerate(boxes, 1):
        name = b.get("boxName", "").strip()
        bid_str = str(b.get("boxId", "-"))
        h_info = hierarchy_map.get(bid_str, {})

        parent_id = b.get("parentBoxId") or h_info.get("parentBoxId", "ROOT")
        if str(parent_id) == "None":
            parent_id = "ROOT"

        depth = b.get("depth")
        if depth is None:
            depth = h_info.get("depth", 0)

        prefix = b.get("hierarchy_prefix")
        if prefix is None and depth > 0:
            prefix = get_hierarchy_prefix(depth)
        elif prefix is None:
            prefix = ""

        display_name = f"{prefix}{name}"

        is_dup = name in duplicate_names
        owner_failed = is_owner_failed(b)
        date_anomaly = is_date_anomaly(b)
        no_proj = not (b.get("jira_projects") or b.get("configured_projects"))
        scope_err = is_scope_anomaly(b)
        jql_err = is_jql_invalid(b)
        jql_empty = is_jql_empty(b)
        box_st = str(b.get("effective_box_status") or b.get("boxStatus") or "").upper()
        is_closed = box_st == "CLOSED" or b.get("is_closed") is True
        is_mig = is_migration_target(b)
        
        scope_st = b.get("scope_status", "VALID")
        is_scope_valid = b.get("is_scope_valid", True if not scope_err else False)
        
        scope_label = {
            "VALID": "정상 (Active)",
            "PARTIAL_VALID": "부분 정상 (설정 참조 확인 필요)",
            "EMPTY_SCOPE": "Scope 누락 (프로젝트 없음)",
            "PROJECT_NOT_FOUND": "프로젝트 404 (Jira에 없음)",
            "ALL_ARCHIVED": "프로젝트 보관됨 (Archived)",
            "NON_PROJECT_SCOPE": "비프로젝트 Scope (Filter/Board)",
            "CLOSED": "Closed (마이그레이션 제외)"
        }.get(scope_st, scope_st)
        
        val_statuses = []
        if is_closed:
            val_statuses.append("Closed(제외)")
        if is_dup:
            val_statuses.append(f"중복({name_counts.get(name, 1)}회)")
        if scope_err:
            val_statuses.append(f"Scope이상({scope_label})")
        elif scope_st == "PARTIAL_VALID":
            val_statuses.append(f"일부404제외({len(b.get('not_found_projects', []))}개)")
        if jql_empty:
            val_statuses.append("JQL결과0건(제외)")
        elif jql_err:
            val_statuses.append("JQL오류(제외)")
        elif b.get("jql_status") == "WARNING":
            val_statuses.append("JQL경고")
        if owner_failed:
            val_statuses.append("Owner실패")
        if date_anomaly:
            val_statuses.append("날짜이상")
        overall_status = ", ".join(val_statuses) if val_statuses else "정상"

        processed_boxes.append({
            "index": idx,
            "boxId": b.get("boxId", "-"),
            "parentBoxId": parent_id,
            "depth": depth,
            "hierarchy_prefix": prefix,
            "displayName": display_name,
            "boxName": name or "(Unnamed Box)",
            "boxType": b.get("boxType", "-"),
            "boxStatus": box_st,
            "effective_box_status": box_st,
            "is_closed": is_closed,
            "is_migration_target": is_mig,
            "migration_selected": is_mig,
            "is_jql_empty": jql_empty,
            "boxStartDate": b.get("boxStartDate", "-"),
            "boxEndDate": b.get("boxEndDate", "-"),
            "owner_email": b.get("owner_email", "-"),
            "owner_name": b.get("owner_name", "-"),
            "jira_projects": b.get("jira_projects", []),
            "configured_projects": [ref["key"] for ref in configured_project_references(b)],
            "project_references": configured_project_references(b),
            "source_environment": b.get("source_environment", "STG"),
            "source_base_url": b.get("source_base_url", ""),
            "narrowing_query": b.get("narrowing_query"),
            "jql_status": b.get("jql_status", "NONE"),
            "jql_error": b.get("jql_error"),
            "jql_total_matched": b.get("jql_total_matched", 0),
            "not_found_projects": b.get("not_found_projects", []),
            "saved_filters": b.get("saved_filters", []),
            "boards": b.get("boards", []),
            "filter_reason": b.get("filter_reason"),
            "is_duplicate": is_dup,
            "duplicate_count": name_counts.get(name, 1),
            "owner_failed": owner_failed,
            "date_anomaly": date_anomaly,
            "no_project": no_proj,
            "scope_anomaly": scope_err,
            "jql_invalid": jql_err,
            "scope_status": scope_st,
            "scope_status_label": scope_label,
            "is_scope_valid": is_scope_valid,
            "validation_status": overall_status
        })

    # Sort boxes hierarchically (children appear directly under their parent in DFS tree order)
    processed_boxes = sort_boxes_by_hierarchy(processed_boxes)

    # Map parentBoxId -> direct children and descendants for tree collapse/expand
    children_map = {}
    for b in processed_boxes:
        pid = str(b.get("parentBoxId") or "ROOT")
        bid = str(b.get("boxId"))
        children_map.setdefault(pid, []).append(bid)

    def count_all_descendants(bid):
        total = 0
        for child_id in children_map.get(bid, []):
            total += 1 + count_all_descendants(child_id)
        return total

    # Re-assign 1-based sequential index and compute child counts
    for new_idx, box in enumerate(processed_boxes, 1):
        box["index"] = new_idx
        bid = str(box.get("boxId"))
        direct_children = children_map.get(bid, [])
        box["has_children"] = len(direct_children) > 0
        box["child_count"] = len(direct_children)
        box["descendant_count"] = count_all_descendants(bid)

    scope_audit_rows = ""
    scope_issue_count = 0
    for box in scanned_boxes:
        if str(box.get("effective_box_status") or box.get("boxStatus") or "").upper() == "CLOSED":
            continue
        # Exclude empty scope boxes
        if not box.get("jira_projects") and not box.get("configured_projects"):
            continue
        if box.get("scope_status") == "EMPTY_SCOPE":
            continue
        project_refs = configured_project_references(box)
        problematic = [ref for ref in project_refs if ref["status"] not in ("ACTIVE", "UNVERIFIED")]
        if not problematic and box.get("is_scope_valid", True):
            continue
        scope_issue_count += 1
        base_url = str(box.get("source_base_url") or "").rstrip("/")
        box_id = str(box.get("boxId") or "")
        box_id_html = html.escape(box_id)
        box_url = f"{base_url}/plugins/servlet/softwareplant-biggantt/#/box/{quote(box_id, safe='')}/g"
        box_link = (f'<a href="{html.escape(box_url, quote=True)}" target="_blank" rel="noopener noreferrer">{box_id_html}</a>'
                    if base_url else box_id_html)
        project_labels = ", ".join(
            f"{ref['key']} ({ref['status']}{' → ' + ref['resolved_key'] if ref.get('resolved_key') and ref['resolved_key'] != ref['key'] else ''})"
            for ref in project_refs
        ) or "확인 필요"
        scope_audit_rows += (
            f"<tr><td>{box_link}</td><td>{html.escape(str(box.get('boxName') or ''))}</td>"
            f"<td>{html.escape(project_labels)}</td>"
            f"<td>{html.escape(str(box.get('filter_reason') or box.get('scope_status') or ''))}</td></tr>"
        )
    if not scope_audit_rows:
        scope_audit_rows = '<tr><td colspan="4" style="text-align:center; color:var(--success); padding:16px;">✅ 검출된 Project 설정 이상이 없습니다.</td></tr>'

    closed_rows = ""
    closed_count = 0
    for box in scanned_boxes:
        if str(box.get("effective_box_status") or box.get("boxStatus") or "").upper() != "CLOSED":
            continue
        # Exclude empty scope boxes
        if not box.get("jira_projects") and not box.get("configured_projects"):
            continue
        if box.get("scope_status") == "EMPTY_SCOPE":
            continue
        closed_count += 1
        box_id = str(box.get("boxId") or "")
        base_url = str(box.get("source_base_url") or "").rstrip("/")
        box_link = html.escape(box_id)
        if base_url:
            url = f"{base_url}/plugins/servlet/softwareplant-biggantt/#/box/{quote(box_id, safe='')}/g"
            box_link = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{box_link}</a>'
        proj_str = ", ".join(box.get("configured_projects") or box.get("jira_projects") or []) or "-"
        jql_str = box.get("narrowing_query") or "-"
        jql_st = box.get("jql_status", "NONE")
        jql_badge_mini = (f'<span class="badge badge-success">정상</span>' if jql_st == "VALID"
                          else f'<span class="badge badge-danger">오류</span>' if jql_st == "INVALID"
                          else f'<span class="badge" style="background:#334155; color:var(--text-muted);">-</span>')
        closed_rows += (
            f"<tr><td>{box_link}</td>"
            f"<td>{html.escape(str(box.get('boxName') or ''))}</td>"
            f"<td>{html.escape(proj_str)}</td>"
            f"<td>{jql_badge_mini} <code style='font-size:0.75rem;'>{html.escape(jql_str)}</code></td>"
            f"<td>{html.escape(str(box.get('filter_reason') or 'Closed'))}</td></tr>"
        )
    if not closed_rows:
        closed_rows = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:16px;">Closed Box가 없습니다.</td></tr>'

    # Automatically save updated TSV and CSV export files
    export_boxes_for_excel(processed_boxes, TSV_EXPORT_FILE, CSV_EXPORT_FILE)

    box_type_options = "".join(
        f'<option value="{html.escape(box_type, quote=True)}">{html.escape(box_type)}</option>'
        for box_type in sorted({str(b["boxType"]) for b in processed_boxes})
    )

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BigGantt DC Box Export Verification Report</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-body: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border-color: #334155;
            
            --primary: #3b82f6;
            --danger: #ef4444;
            --danger-bg: rgba(239, 68, 68, 0.15);
            --warning: #f59e0b;
            --warning-bg: rgba(245, 158, 11, 0.15);
            --success: #10b981;
            --success-bg: rgba(16, 185, 129, 0.15);
            --info: #06b6d4;
            --purple: #8b5cf6;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-main);
            line-height: 1.5;
            padding: 24px;
            max-width: 1400px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 12px;
        }}

        header h1 {{
            font-size: 1.5rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
            background: linear-gradient(135deg, #60a5fa, #a78bfa);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .meta-info {{
            font-size: 0.85rem;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 16px;
        }}

        .meta-badge {{
            background: #1e293b;
            border: 1px solid var(--border-color);
            padding: 4px 10px;
            border-radius: 6px;
            font-weight: 500;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}

        .stat-card {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 18px 20px;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            position: relative;
            overflow: hidden;
        }}

        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.3);
        }}

        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--card-accent, var(--primary));
        }}

        .stat-card.blue {{ --card-accent: var(--primary); }}
        .stat-card.amber {{ --card-accent: var(--warning); }}
        .stat-card.red {{ --card-accent: var(--danger); }}
        .stat-card.purple {{ --card-accent: var(--purple); }}
        .stat-card.cyan {{ --card-accent: var(--info); }}

        .stat-label {{
            font-size: 0.825rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }}

        .stat-value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--text-main);
            display: flex;
            align-items: baseline;
            gap: 8px;
        }}

        .stat-desc {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        /* Analytics Dashboard Grid */
        .analytics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(480px, 1fr));
            gap: 20px;
            margin-bottom: 28px;
        }}

        .analytics-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            flex-direction: column;
        }}

        .analytics-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-color);
        }}

        .analytics-icon {{
            font-size: 1.5rem;
            background: #0f172a;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        .analytics-header h3 {{
            font-size: 1.05rem;
            font-weight: 700;
            color: var(--text-main);
        }}

        .analytics-header p {{
            font-size: 0.775rem;
            color: var(--text-muted);
        }}

        .reduction-summary {{
            display: flex;
            align-items: center;
            justify-content: space-around;
            background: #0f172a;
            padding: 14px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            margin-bottom: 16px;
        }}

        .reduction-metric {{
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        .reduction-metric .metric-num {{
            font-size: 1.5rem;
            font-weight: 700;
            color: #6ee7b7;
        }}

        .reduction-metric.highlight .metric-num {{
            color: #fcd34d;
        }}

        .reduction-metric .metric-lbl {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 2px;
        }}

        .reduction-arrow {{
            font-size: 1.2rem;
            color: var(--text-muted);
        }}

        .progress-bar-container {{
            margin-bottom: 16px;
        }}

        .progress-bar-bg {{
            height: 12px;
            background: #0f172a;
            border-radius: 6px;
            overflow: hidden;
            display: flex;
            border: 1px solid var(--border-color);
        }}

        .progress-bar-fill {{
            height: 100%;
            transition: width 0.3s ease;
        }}

        .progress-bar-fill.green {{ background: #10b981; }}
        .progress-bar-fill.amber {{ background: #f59e0b; }}

        .progress-bar-legend {{
            display: flex;
            justify-content: space-between;
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 6px;
        }}

        .progress-bar-legend .dot {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 4px;
        }}

        .progress-bar-legend .dot.green {{ background: #10b981; }}
        .progress-bar-legend .dot.amber {{ background: #f59e0b; }}

        .sub-table-title {{
            font-size: 0.825rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .table-scroll {{
            max-height: 210px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 8px;
        }}

        .dup-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.8rem;
        }}

        .dup-table th {{
            background: #0f172a;
            color: var(--text-muted);
            padding: 8px 12px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 1;
            border-bottom: 1px solid var(--border-color);
        }}

        .dup-table td {{
            padding: 8px 12px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-main);
        }}

        .dup-table tr:last-child td {{
            border-bottom: none;
        }}

        /* Project Distribution Component */
        .project-list-container {{
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-height: 330px;
            overflow-y: auto;
            padding-right: 4px;
        }}

        .project-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            background: #0f172a;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
        }}

        .project-key-badge {{
            font-family: 'JetBrains Mono', monospace;
            font-weight: 700;
            font-size: 0.8rem;
            color: #60a5fa;
            min-width: 90px;
        }}

        .project-bar-wrap {{
            flex: 1;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .project-bar-bg {{
            flex: 1;
            height: 8px;
            background: #1e293b;
            border-radius: 4px;
            overflow: hidden;
        }}

        .project-bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, #3b82f6, #8b5cf6);
            border-radius: 4px;
        }}

        .project-count {{
            font-size: 0.775rem;
            font-weight: 600;
            color: var(--text-main);
            min-width: 75px;
            text-align: right;
        }}

        .recommendation-header {{
            font-weight: 600;
            color: #60a5fa;
            font-size: 0.95rem;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .recommendation-list {{
            font-size: 0.85rem;
            color: #cbd5e1;
            padding-left: 20px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 6px 20px;
        }}

        .controls-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 16px;
            flex-wrap: wrap;
            background: var(--bg-card);
            padding: 14px 18px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
        }}

        .search-box {{
            position: relative;
            flex: 1;
            min-width: 260px;
        }}

        .search-box input {{
            width: 100%;
            padding: 8px 12px 8px 36px;
            background: #0f172a;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-main);
            font-size: 0.875rem;
            outline: none;
            transition: border-color 0.2s;
        }}

        .search-box input:focus {{
            border-color: var(--primary);
        }}

        .search-box svg {{
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            fill: var(--text-muted);
            width: 16px;
            height: 16px;
        }}

        .filter-buttons {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .btn-filter {{
            background: #0f172a;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .btn-filter:hover {{
            background: var(--bg-card-hover);
            color: var(--text-main);
        }}

        .btn-filter.active {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}

        .sort-box {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}

        .sort-select {{
            background: #0f172a;
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            outline: none;
            cursor: pointer;
        }}

        .sort-select:focus {{
            border-color: var(--primary);
        }}

        .section-title {{
            font-size: 1.15rem;
            font-weight: 600;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .section-badge {{
            font-size: 0.75rem;
            background: #334155;
            padding: 2px 8px;
            border-radius: 12px;
            color: var(--text-muted);
        }}

        /* Box ID Batch Selector Panel */
        .box-id-selector-panel, .project-key-selector-panel {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
        }}

        .box-id-panel-header, .project-key-panel-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.95rem;
            color: var(--text-main);
            flex-wrap: wrap;
            gap: 8px;
        }}

        .box-id-panel-body, .project-key-panel-body {{
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }}

        .box-ids-input, .project-keys-input {{
            flex: 1;
            min-width: 320px;
            padding: 9px 14px;
            border-radius: 6px;
            border: 1px solid var(--border-color);
            background: #0f172a;
            color: var(--text-main);
            font-size: 0.85rem;
            font-family: 'JetBrains Mono', monospace;
            transition: border-color 0.15s ease;
        }}

        .box-ids-input:focus, .project-keys-input:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }}

        .btn-primary-action {{
            background: #2563eb;
            color: #ffffff;
            border: 1px solid #3b82f6;
            border-radius: 6px;
            padding: 8px 16px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .btn-primary-action:hover {{
            background: #1d4ed8;
            transform: translateY(-1px);
        }}

        .btn-sub-action {{
            background: #334155;
            color: #e2e8f0;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}

        .btn-sub-action:hover {{
            background: #475569;
            color: #ffffff;
        }}

        .box-list-column-headers {{
            display: grid;
            grid-template-columns: 36px 80px 130px 1fr auto 100px 32px;
            align-items: center;
            padding: 8px 16px;
            gap: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            background: #0f172a;
            border-radius: 6px 6px 0 0;
            border: 1px solid var(--border-color);
            border-bottom: none;
            margin-bottom: 4px;
            user-select: none;
        }}

        .col-sortable {{
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            transition: color 0.15s ease;
        }}

        .col-sortable:hover {{
            color: var(--primary);
        }}

        .col-sortable.active-sort {{
            color: #60a5fa;
            font-weight: 700;
        }}

        .sort-icon {{
            font-size: 0.7rem;
            opacity: 0.6;
        }}

        .box-list {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 36px;
        }}

        .box-item {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            transition: border-color 0.15s ease;
        }}

        .box-item:hover {{
            border-color: #475569;
        }}

        .box-item.has-issue {{
            border-left: 3px solid var(--warning);
        }}

        .box-item.jql-err {{
            border-left: 3px solid var(--danger);
        }}

        .box-item.scope-err {{
            border-left: 3px solid var(--warning);
        }}

        .box-item.is-closed {{
            opacity: 0.82;
            border-left: 3px solid #64748b;
        }}

        .box-item.is-closed:hover {{
            opacity: 1;
        }}

        .box-item.owner-err {{
            border-left: 3px solid var(--danger);
        }}

        .box-header {{
            display: grid;
            grid-template-columns: 36px 80px 130px 1fr auto 100px 32px;
            align-items: center;
            padding: 10px 16px;
            cursor: pointer;
            user-select: none;
            gap: 12px;
            font-size: 0.875rem;
            transition: background 0.15s ease;
        }}

        .box-header:hover {{
            background: var(--bg-card-hover);
        }}

        .box-id {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            font-weight: 600;
            color: #60a5fa;
        }}

        a.box-id:hover {{
            text-decoration: underline;
        }}

        .box-name {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            color: var(--text-main);
            user-select: text;
            cursor: text;
            min-width: 0;
        }}

        .box-name-text {{
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .tree-table-cell {{
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
        }}

        .tree-prefix {{
            color: #64748b;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.825rem;
            font-weight: 600;
            white-space: pre;
            user-select: none;
            letter-spacing: -0.5px;
        }}

        .tree-toggle-btn {{
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid rgba(59, 130, 246, 0.35);
            color: #60a5fa;
            width: 22px;
            height: 22px;
            border-radius: 4px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.65rem;
            line-height: 1;
            padding: 0;
            transition: all 0.15s ease;
            flex-shrink: 0;
            user-select: none;
        }}

        .tree-toggle-btn:hover {{
            background: rgba(59, 130, 246, 0.3);
            border-color: #60a5fa;
            color: #ffffff;
            transform: scale(1.08);
        }}

        .tree-toggle-btn.is-collapsed {{
            background: rgba(148, 163, 184, 0.15);
            border-color: rgba(148, 163, 184, 0.35);
            color: #cbd5e1;
        }}

        .tree-toggle-btn.is-collapsed:hover {{
            background: rgba(148, 163, 184, 0.3);
            color: #ffffff;
        }}

        .tree-toggle-icon {{
            transition: transform 0.2s ease;
            display: inline-block;
        }}

        .tree-leaf-spacer {{
            width: 22px;
            height: 22px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            color: #475569;
            font-size: 0.75rem;
            user-select: none;
        }}

        .child-count-pill {{
            background: rgba(99, 102, 241, 0.18);
            color: #a5b4fc;
            border: 1px solid rgba(99, 102, 241, 0.35);
            font-size: 0.7rem;
            font-weight: 600;
            padding: 1px 7px;
            border-radius: 10px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 3px;
            transition: all 0.15s ease;
            user-select: none;
            white-space: nowrap;
            flex-shrink: 0;
        }}

        .child-count-pill:hover {{
            background: rgba(99, 102, 241, 0.35);
            color: #ffffff;
            border-color: #818cf8;
        }}

        .child-count-pill.is-collapsed {{
            background: rgba(245, 158, 11, 0.18);
            color: #fcd34d;
            border-color: rgba(245, 158, 11, 0.4);
        }}

        .child-count-pill.is-collapsed::before {{
            content: '📁 ';
        }}

        .child-count-pill:not(.is-collapsed)::before {{
            content: '📂 ';
        }}

        .parent-id-pill {{
            color: #64748b;
            font-size: 0.72rem;
            background: rgba(51, 65, 85, 0.4);
            border: 1px solid rgba(71, 85, 105, 0.4);
            border-radius: 4px;
            padding: 1px 6px;
            margin-left: 4px;
            white-space: nowrap;
            font-family: 'JetBrains Mono', monospace;
            user-select: none;
            flex-shrink: 0;
        }}

        .box-item.tree-node-collapsed {{
            border-left: 3px solid #6366f1;
        }}

        .excel-table-row.tree-node-collapsed td {{
            background: rgba(99, 102, 241, 0.05);
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.725rem;
            font-weight: 600;
            width: fit-content;
        }}

        .badge-type {{
            background: rgba(139, 92, 246, 0.2);
            color: #c084fc;
            border: 1px solid rgba(139, 92, 246, 0.3);
        }}

        .badge-project {{
            background: rgba(59, 130, 246, 0.15);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.25);
            margin-right: 4px;
        }}

        .badge-warning {{
            background: var(--warning-bg);
            color: #fcd34d;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }}

        .badge-danger {{
            background: var(--danger-bg);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }}

        .badge-success {{
            background: var(--success-bg);
            color: #6ee7b7;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }}

        .chevron {{
            width: 16px;
            height: 16px;
            fill: var(--text-muted);
            transition: transform 0.2s ease;
            justify-self: center;
        }}

        .box-item.open .chevron {{
            transform: rotate(180deg);
        }}

        .box-details {{
            display: none;
            padding: 16px 20px;
            background: #111827;
            border-top: 1px solid var(--border-color);
            font-size: 0.85rem;
        }}

        .box-item.open .box-details {{
            display: block;
        }}

        .details-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 12px 24px;
            margin-bottom: 12px;
        }}

        .detail-group {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .detail-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            font-weight: 500;
        }}

        .detail-val {{
            font-weight: 500;
            color: var(--text-main);
            word-break: break-all;
        }}

        .url-links {{
            display: flex;
            gap: 12px;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px dashed var(--border-color);
        }}

        .btn-link {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border-radius: 6px;
            background: #1e293b;
            border: 1px solid var(--border-color);
            color: #60a5fa;
            text-decoration: none;
            font-size: 0.8rem;
            font-weight: 500;
            transition: background 0.15s ease;
        }}

        .btn-link:hover {{
            background: #334155;
            color: #93c5fd;
        }}

        .fail-api-section {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-top: 36px;
        }}

        .collapsible-section {{
            transition: all 0.2s ease;
        }}

        .collapsible-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            user-select: none;
            padding: 4px 0;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .collapsible-header:hover h2 {{
            color: #93c5fd;
        }}

        .collapsible-icon {{
            font-size: 0.85rem;
            color: var(--text-muted);
            display: inline-block;
            transition: transform 0.2s ease;
        }}

        .fail-api-section h2 {{
            font-size: 1.1rem;
            color: var(--danger);
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 16px;
        }}

        .fail-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }}

        .fail-table th {{
            background: #0f172a;
            color: var(--text-muted);
            text-align: left;
            padding: 10px 14px;
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
        }}

        .fail-table td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-main);
            vertical-align: top;
        }}

        .fail-table tr:last-child td {{
            border-bottom: none;
        }}

        .no-failures {{
            padding: 20px;
            text-align: center;
            color: var(--success);
            background: rgba(16, 185, 129, 0.05);
            border: 1px dashed rgba(16, 185, 129, 0.3);
            border-radius: 8px;
            font-weight: 500;
        }}

        @media (max-width: 900px) {{
            .box-header {{
                grid-template-columns: 36px 70px 100px 1fr auto 32px;
            }}
            .box-header .projects-col {{
                display: none;
            }}
        }}

        @media (max-width: 600px) {{
            body {{ padding: 12px; }}
            .box-header {{
                grid-template-columns: 36px 1fr auto 32px;
            }}
            .box-id, .box-type-col {{
                display: none;
            }}
        }}

        .toolbar-actions {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .btn-excel {{
            background: linear-gradient(135deg, #107c41, #15803d);
            border: 1px solid #16a34a;
            color: #ffffff;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(16, 124, 65, 0.4);
        }}

        .btn-excel:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
        }}

        .btn-excel:hover:not(:disabled) {{
            background: linear-gradient(135deg, #15803d, #166534);
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(16, 124, 65, 0.5);
        }}

        .btn-tool {{
            background: #1e293b;
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            text-decoration: none;
            transition: all 0.15s ease;
        }}

        .btn-tool:hover {{
            background: var(--bg-card-hover);
            color: #60a5fa;
        }}

        .btn-tool.active {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}

        /* Excel-like Table View */
        .excel-table-container {{
            overflow-x: auto;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background: var(--bg-card);
            margin-bottom: 36px;
        }}

        .excel-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.8rem;
        }}

        .excel-table th {{
            background: #0f172a;
            color: var(--text-muted);
            padding: 10px 12px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 2;
            border-bottom: 1px solid var(--border-color);
            border-right: 1px solid rgba(51, 65, 85, 0.5);
            font-weight: 600;
            user-select: none;
            white-space: nowrap;
        }}

        .excel-table td {{
            padding: 8px 12px;
            border-bottom: 1px solid var(--border-color);
            border-right: 1px solid rgba(51, 65, 85, 0.5);
            color: var(--text-main);
            vertical-align: middle;
            white-space: nowrap;
        }}

        .excel-table tr:hover td {{
            background: rgba(59, 130, 246, 0.08);
        }}

        /* Toast notification */
        .toast {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #107c41;
            color: white;
            padding: 14px 22px;
            border-radius: 8px;
            font-size: 0.875rem;
            font-weight: 600;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4);
            display: none;
            z-index: 1000;
            transition: opacity 0.3s ease;
        }}

        /* Modal Overlay */
        .modal-overlay {{
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.7);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 999;
        }}

        .modal-content {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            width: 90%;
            max-width: 850px;
            padding: 24px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
        }}

        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }}

        .modal-close {{
            font-size: 1.5rem;
            color: var(--text-muted);
            cursor: pointer;
        }}
        .modal-close:hover {{ color: var(--text-main); }}
    </style>
</head>
<body>

    <header>
        <div>
            <h1>📦 BigGantt DC Box Export Verification Report</h1>
            <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 4px;">
                <code>step1_export_dc_boxes.py</code> 실행 결과 분석 대시보드
            </div>
        </div>
        <div class="meta-info">
            <div class="meta-badge" style="color: #60a5fa;">⚡ Fast Re-filtered ({target_proj_count} Projects)</div>
            <div class="meta-badge">📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div class="meta-badge">🎯 Total Processed: {total_boxes} Boxes</div>
        </div>
    </header>

    <!-- Section: Box List -->
    <div class="section-title">
        <div>
            🚀 박스 이관 대상 선택 
            <span class="section-badge" id="visibleCount">{migration_box_count} / {total_boxes}</span>
        </div>
        <div class="toolbar-actions">
            <button class="btn-tool" onclick="expandAllTree()" title="모든 하위 Box 트리를 완전히 펼칩니다.">
                <span>🌳</span> <b>전체 펼치기</b>
            </button>
            <button class="btn-tool" onclick="collapseAllTree()" title="모든 하위 Box 트리를 접어 상위 Box만 표시합니다.">
                <span>📁</span> <b>전체 접기</b>
            </button>
            <button id="exportSelectedButton" class="btn-excel" disabled onclick="exportSelectedBoxesToCSV()" style="background: linear-gradient(135deg, #2563eb, #1d4ed8); border-color: #3b82f6; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);" title="현재 체크박스로 선택된 Box들을 Step 2 마이그레이션용 CSV 파일(migration_boxes.csv)로 다운로드합니다.">
                <span>📥</span> <b>선택 Box CSV 내보내기 (Step 2용)</b> <span id="exportSelectedCount">(0개)</span>
            </button>
            <button class="btn-excel" onclick="copyToExcel()" title="엑셀에 그대로 붙여넣을 수 있는 TSV 형식으로 클립보드에 복사합니다.">
                <span>📋</span> 엑셀 복사 (TSV)
            </button>
            <button class="btn-tool" onclick="openCopyModal()" title="복사용 텍스트 창을 열어 직접 선택/복사합니다.">
                <span>📄</span> 텍스트 보기
            </button>
            <div style="display:flex; border:1px solid var(--border-color); border-radius:6px; overflow:hidden;">
                <button class="btn-tool active" id="btnViewCard" onclick="setViewMode('card')" style="border:none; border-radius:0;">📋 카드 뷰</button>
                <button class="btn-tool" id="btnViewTable" onclick="setViewMode('table')" style="border:none; border-radius:0;">📊 엑셀 표(Table) 뷰</button>
            </div>
            <button class="btn-tool" onclick="downloadBoxes('tsv')" title="현재 필터 결과를 TSV로 다운로드">📥 TSV</button>
            <button class="btn-tool" onclick="downloadBoxes('csv')" title="현재 필터 결과를 CSV로 다운로드">📥 CSV</button>
            <a href="box_scope_audit.csv" download="box_scope_audit.csv" class="btn-tool" title="선택되지 않은 Box를 포함한 Project, Filter, Board 설정 검사 결과">📥 Scope 설정 검사</a>
        </div>
    </div>

    <!-- Box ID Batch Selector Panel -->
    <div class="box-id-selector-panel">
        <div class="box-id-panel-header">
            <div>
                🎯 <b>Box ID 일괄 선택</b> 
                <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 8px;">(예: <code>1001, 1002, 1003</code> 형식이나 줄바꿈/공백으로 붙여넣으면 해당 Box가 일괄 체크됩니다)</span>
            </div>
            <div class="selection-status">
                선택된 Box: <b id="selectedCountDisplay" style="color:#60a5fa; font-size:1.15rem;">0</b> / {total_boxes}개
            </div>
        </div>
        <div class="box-id-panel-body">
            <input type="text" id="boxIdsInput" class="box-ids-input" placeholder="Box ID 입력 또는 붙여넣기 (예: 1001, 1002, 1003 또는 공백/줄바꿈 구분)..." onkeydown="if(event.key==='Enter') selectByBoxIds()">
            <button class="btn-primary-action" onclick="selectByBoxIds()">🎯 Box ID로 선택</button>
            <button class="btn-sub-action" onclick="selectMigrationTargetsOnly()">🚀 이관 대상만 선택</button>
            <button class="btn-sub-action" onclick="toggleSelectAll(true)">화면 목록 전체 선택</button>
            <button class="btn-sub-action" onclick="clearSelection()">모든 선택 해제</button>
        </div>
    </div>

    <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:16px;">
        먼저 이관 대상 Box를 체크하세요. <b>Shift + 체크박스 클릭</b>으로 마지막 클릭부터 현재 클릭까지 화면에 보이는 범위를 선택하거나 해제할 수 있습니다.
        하위 Box는 자동 선택되지 않습니다. 필터·정렬·뷰를 바꿔도 선택은 유지되며, CSV에는 숨겨진 항목을 포함해 체크된 Box만 저장됩니다.
        다운로드한 <code>migration_boxes.csv</code>를 Step 2 스크립트와 같은 폴더에 두고 사용하세요.
    </p>

    <!-- Controls & Filters Bar -->
    <div class="controls-bar">
        <div class="search-box">
            <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
            <input type="text" id="searchInput" placeholder="Box ID, Box Name, Owner, Project 키, JQL 검색..." oninput="filterBoxes()">
        </div>
        <input type="text" id="boxIdFilter" class="sort-select" placeholder="Box ID 필터" oninput="filterBoxes()" aria-label="Box ID 필터">
        <select id="boxTypeFilter" class="sort-select" onchange="filterBoxes()" aria-label="Box Type 필터">
            <option value="">전체 Box Type</option>
            {box_type_options}
        </select>
        <div class="sort-box">
            <span>🔃 정렬 기준:</span>
            <select id="sortSelect" class="sort-select" onchange="onSortSelectChange()">
                <option value="default-asc">기본 순서 (이관 대상 우선 ↑)</option>
                <option value="default-desc">기본 순서 (Closed 우선 ↓)</option>
                <option value="id-asc">Box ID (오름차순 ↑)</option>
                <option value="id-desc">Box ID (내림차순 ↓)</option>
                <option value="name-asc">Box Name (오름차순 ↑)</option>
                <option value="name-desc">Box Name (내림차순 ↓)</option>
                <option value="projects-asc">Project Keys (오름차순 ↑)</option>
                <option value="projects-desc">Project Keys (내림차순 ↓)</option>
                <option value="dup-desc">중복 컬럼 (중복 우선 ↓)</option>
                <option value="dup-asc">중복 컬럼 (정상 우선 ↑)</option>
            </select>
        </div>
        <div class="filter-buttons">
            <button class="btn-filter active" onclick="setFilter('open', this)">🚀 이관 대상 ({migration_box_count})</button>
            <button class="btn-filter" onclick="setFilter('all', this)">전체 ({total_boxes})</button>
            <button class="btn-filter" onclick="setFilter('closed', this)">🔒 Closed ({closed_box_count})</button>
            <button class="btn-filter" onclick="setFilter('jql_empty', this)">📭 JQL 0건 ({jql_empty_count})</button>
            <button class="btn-filter" onclick="setFilter('jql_err', this)">⚡ JQL 오류 ({jql_invalid_count})</button>
            <button class="btn-filter" onclick="setFilter('scope_err', this)">⚠️ Scope 이상 ({scope_anomaly_count})</button>
            <button class="btn-filter" onclick="setFilter('owner_err', this)">👤 Owner 실패 ({owner_failed_count})</button>
            <button class="btn-filter" onclick="setFilter('dup', this)">🏷️ 중복 이름 ({duplicate_box_count})</button>
            <button class="btn-filter" onclick="setFilter('date_err', this)">📅 날짜 이상 ({date_anomaly_count})</button>
        </div>
    </div>

    <!-- Box List Column Headers -->
    <div class="box-list-column-headers">
        <div style="display:flex; justify-content:center; align-items:center;">
            <input type="checkbox" id="selectAllCardHeader" onchange="toggleSelectAll(this.checked)" title="화면에 보이는 Box 전체 선택/해제" style="width:16px; height:16px; cursor:pointer;">
        </div>
        <div>Box Type</div>
        <div class="col-sortable" id="col-head-id" onclick="sortByColumn('id')">Box ID <span class="sort-icon" id="sort-icon-id">↕</span></div>
        <div class="col-sortable" id="col-head-name" onclick="sortByColumn('name')">Box Name <span class="sort-icon" id="sort-icon-name">↕</span></div>
        <div class="col-sortable" id="col-head-projects" onclick="sortByColumn('projects')">설정 Projects <span class="sort-icon" id="sort-icon-projects">↕</span></div>
        <div class="col-sortable" id="col-head-dup" onclick="sortByColumn('dup')">중복 / 상태 <span class="sort-icon" id="sort-icon-dup">↕</span></div>
        <div></div>
    </div>

    <!-- Box List Container (Thin Accordion Rows) -->
    <div class="box-list" id="boxList">
"""

    table_rows_html = ""
    for b in processed_boxes:
        issue_classes = []
        if b.get("is_closed"):
            issue_classes.append("is-closed")
        if b["jql_invalid"]:
            issue_classes.append("jql-err")
        if b["scope_anomaly"]:
            issue_classes.append("scope-err")
        elif b["owner_failed"]:
            issue_classes.append("owner-err")
        elif b["is_duplicate"]:
            issue_classes.append("has-issue")
            
        issue_class_str = " ".join(issue_classes)
        
        status_badges = []
        if b.get("is_closed"):
            status_badges.append('<span class="badge" style="background:#475569; color:#cbd5e1;">🔒 Closed</span>')
        elif b.get("is_migration_target"):
            status_badges.append('<span class="badge badge-success">🚀 이관 대상</span>')
        if b.get("is_jql_empty"):
            status_badges.append('<span class="badge badge-danger">📭 JQL 0건 (제외)</span>')
        elif b["jql_invalid"]:
            status_badges.append('<span class="badge badge-danger">⚡ JQL 오류 (제외)</span>')
        elif b["jql_status"] == "WARNING":
            status_badges.append('<span class="badge badge-warning">JQL 경고</span>')
        if b["scope_status"] == "PARTIAL_VALID":
            status_badges.append('<span class="badge badge-warning">Scope 설정 확인</span>')
        elif b["scope_anomaly"]:
            status_badges.append(f'<span class="badge badge-danger">{b["scope_status_label"]}</span>')
        if b["owner_failed"]:
            status_badges.append('<span class="badge badge-danger">Owner 실패</span>')
        if b["is_duplicate"]:
            status_badges.append(f'<span class="badge badge-warning">중복 ({b["duplicate_count"]}회)</span>')
        if not status_badges:
            status_badges.append('<span class="badge badge-success">정상</span>')
        status_badge = " ".join(status_badges)

        # JQL display info
        jql_query = b.get("narrowing_query") or ""
        jql_status = b.get("jql_status", "NONE")
        jql_error = b.get("jql_error") or ""
        if jql_status == "VALID":
            matched = b.get("jql_total_matched", 0)
            if matched == 0 and jql_query:
                jql_badge = f'<span class="badge badge-danger" title="매칭 결과 0건으로 Box에 아무 작업도 보이지 않아 이관 대상에서 제외됨">0건 (제외)</span>'
            else:
                jql_badge = f'<span class="badge badge-success">정상 ({matched}건)</span>'
        elif jql_status == "WARNING":
            matched = b.get("jql_total_matched", 0)
            if matched == 0 and jql_query:
                jql_badge = f'<span class="badge badge-danger" title="{html.escape(jql_error, quote=True)}">경고 0건 (제외)</span>'
            else:
                jql_badge = f'<span class="badge badge-warning" title="{html.escape(jql_error, quote=True)}">경고 ({matched}건)</span>'
        elif jql_status == "INVALID":
            jql_badge = f'<span class="badge badge-danger" title="{html.escape(jql_error, quote=True)}">오류 (제외)</span>'
        elif jql_status == "ERROR":
            jql_badge = f'<span class="badge badge-warning" title="{html.escape(jql_error, quote=True)}">실패</span>'
        else:
            jql_badge = '<span class="badge" style="background:#334155; color:var(--text-muted);">None</span>'

        status_labels = {
            "ACTIVE": "정상", "NOT_FOUND": "404/접근 불가", "ARCHIVED": "보관됨",
            "ERROR": "조회 실패", "KEY_CHANGED": "키 변경", "UNVERIFIED": "미검증"
        }
        projects_html = "".join(
            f'<span class="badge {"badge-project" if ref["status"] == "ACTIVE" else "badge-danger" if ref["status"] == "NOT_FOUND" else "badge-warning"}" '
            f'title="{html.escape(status_labels.get(ref["status"], ref["status"]), quote=True)}">'
            f'{html.escape(ref["key"])}'
            f'{" (" + html.escape(status_labels.get(ref["status"], ref["status"])) + ")" if ref["status"] != "ACTIVE" else ""}'
            '</span>' for ref in b["project_references"]
        ) if b["project_references"] else '<span style="color:var(--text-muted); font-size:0.75rem;">None</span>'

        gantt_url = (f"{b['source_base_url'].rstrip('/')}/plugins/servlet/"
                     f"softwareplant-biggantt/#/box/{quote(str(b['boxId']), safe='')}/g") if b['source_base_url'] else "#"
        bp_url = f"{b['source_base_url']}/secure/BigPicture.jspa?boxId={b['boxId']}#box/{b['boxId']}" if b['source_base_url'] else "#"
        box_id_text = html.escape(str(b['boxId']))
        box_id_url = (f"{b['source_base_url'].rstrip('/')}/plugins/servlet/"
                      f"softwareplant-biggantt/#/box/{quote(str(b['boxId']), safe='')}/g")
        box_id_link = (f'<a class="box-id" href="{html.escape(box_id_url, quote=True)}" '
                       f'target="_blank" rel="noopener noreferrer">{box_id_text}</a>'
                       if b['source_base_url'] else f'<span class="box-id">{box_id_text}</span>')

        dup_sort_val = b["duplicate_count"] if b["is_duplicate"] else 0

        # Scope detail strings for accordion
        jql_detail_text = f'<code style="font-size:0.75rem; color:#fcd34d;">{jql_query}</code>' if jql_query else '<span style="color:var(--text-muted);">미설정 (None)</span>'
        if jql_error:
            detail_color = 'var(--warning)' if jql_status == 'WARNING' else 'var(--danger)'
            detail_icon = '⚠️' if jql_status == 'WARNING' else '❌'
            jql_detail_text += f'<div style="color:{detail_color}; font-size:0.75rem; margin-top:4px;">{detail_icon} {html.escape(jql_error)}</div>'

        # Hierarchy formatting for Box Name
        depth = b.get("depth", 0)
        prefix = b.get("hierarchy_prefix", "")
        parent_id = b.get("parentBoxId", "ROOT")
        has_children = b.get("has_children", False)
        child_count = b.get("child_count", 0)
        descendant_count = b.get("descendant_count", 0)
        bid_str = html.escape(str(b.get("boxId", "")))

        tree_prefix_html = f'<span class="tree-prefix">{html.escape(prefix)}</span>' if prefix else ''
        parent_badge_html = f'<span class="parent-id-pill" title="상위 Box ID: {html.escape(str(parent_id))}">상위: {html.escape(str(parent_id))}</span>' if depth > 0 else ''

        if has_children:
            tree_toggle_btn = (
                f'<button type="button" class="tree-toggle-btn" data-tree-id="{bid_str}" '
                f'onclick="toggleTreeItem(\'{bid_str}\', event)" '
                f'title="하위 Box 접기/펼치기 (직속 {child_count}개, 전체 {descendant_count}개)">'
                f'<span class="tree-toggle-icon">▼</span></button>'
            )
            child_count_pill = (
                f'<span class="child-count-pill" data-tree-id="{bid_str}" '
                f'onclick="toggleTreeItem(\'{bid_str}\', event)" '
                f'title="하위 Box 총 {descendant_count}개 (직속 {child_count}개) 접기/펼치기">'
                f'하위 {descendant_count}개</span>'
            )
        else:
            tree_toggle_btn = '<span class="tree-leaf-spacer"></span>' if depth > 0 else ''
            child_count_pill = ''

        box_name_card_html = (
            f'{tree_prefix_html}{tree_toggle_btn}'
            f'<span class="box-name-text" style="font-weight: {600 if depth == 0 else 500};">{html.escape(str(b["boxName"]))}</span>'
            f'{child_count_pill}{parent_badge_html}'
        )

        box_name_table_html = (
            f'<div class="tree-table-cell">'
            f'{tree_prefix_html}{tree_toggle_btn}'
            f'<span class="box-name-text" style="font-weight: {600 if depth == 0 else 500};">{html.escape(str(b["boxName"]))}</span>'
            f'{child_count_pill}{parent_badge_html}'
            f'</div>'
        )

        html_content += f"""
        <div class="box-item {issue_class_str} {'tree-parent' if has_children else 'tree-leaf'} {'tree-child' if depth > 0 else 'tree-root'}" 
             data-id="{str(b.get('boxId', '')).lower()}" 
             data-type="{html.escape(str(b.get('boxType', '')), quote=True)}"
             data-name="{str(b.get('boxName', '')).lower()}" 
             data-owner="{str(b.get('owner_name', '')).lower()} {str(b.get('owner_email', '')).lower()}" 
             data-projects="{html.escape(' '.join(b.get('configured_projects', [])).lower(), quote=True)}"
             data-jql="{html.escape(jql_query.lower(), quote=True)}"
             data-closed="{str(b.get('is_closed', False)).lower()}"
             data-jqlempty="{str(b.get('is_jql_empty', False)).lower()}"
             data-migration="{str(b.get('is_migration_target', False)).lower()}"
             data-dup="{str(b['is_duplicate']).lower()}"
             data-ownererr="{str(b['owner_failed']).lower()}"
             data-dateerr="{str(b['date_anomaly']).lower()}"
             data-scopeerr="{str(b['scope_anomaly']).lower()}"
             data-jqlerr="{str(b['jql_invalid']).lower()}"
             data-raw-index="{b['index']}"
             data-raw-id="{b['boxId']}"
             data-raw-name="{html.escape(str(b['boxName']), quote=True)}"
             data-raw-projects="{html.escape(','.join(b.get('configured_projects', [])), quote=True)}"
             data-raw-dup="{dup_sort_val}"
             data-parent-id="{html.escape(str(parent_id))}"
             data-depth="{depth}"
             data-has-children="{'true' if has_children else 'false'}"
             data-child-count="{child_count}"
             data-descendant-count="{descendant_count}">
            
            <div class="box-header" onclick="toggleAccordion(this, event)">
                <div style="display:flex; justify-content:center; align-items:center;" onclick="event.stopPropagation()">
                    <input type="checkbox" class="box-select-checkbox" data-raw-id="{b['boxId']}" onclick="onBoxCheckboxChange(this, event)" aria-label="Box {html.escape(str(b['boxId']), quote=True)} 선택" style="width:16px; height:16px; cursor:pointer;">
                </div>
                <span class="badge badge-type">Type: {b['boxType']}</span>
                {box_id_link}
                <span class="box-name" title="{html.escape(str(b['boxName']), quote=True)}">{box_name_card_html}</span>
                <div class="projects-col">{projects_html}</div>
                <div>{status_badge}</div>
                <svg class="chevron" viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>
            </div>
            
            <div class="box-details">
                <div class="details-grid">
                    <div class="detail-group">
                        <span class="detail-label">Box ID</span>
                        <span class="detail-val">{b['boxId']}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">상위 Box ID (Parent Box ID)</span>
                        <span class="detail-val">{html.escape(str(parent_id))} (Level {depth})</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Box Name</span>
                        <span class="detail-val">{html.escape(str(b['boxName']))}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Box Type</span>
                        <span class="detail-val">{b['boxType']}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Start Date ~ End Date</span>
                        <span class="detail-val">{b['boxStartDate']} ~ {b['boxEndDate']}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Owner Display Name</span>
                        <span class="detail-val" style="color: {'var(--danger)' if b['owner_failed'] else 'var(--text-main)'}">{html.escape(str(b['owner_name']))}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Owner Email</span>
                        <span class="detail-val" style="color: {'var(--danger)' if b['owner_failed'] else 'var(--text-main)'}">{html.escape(str(b['owner_email']))}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Box에 설정된 Jira Projects</span>
                        <span class="detail-val">{projects_html}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">이관에 사용할 활성 Projects</span>
                        <span class="detail-val">{html.escape(', '.join(b['jira_projects'])) if b['jira_projects'] else '없음'}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Scope 상태</span>
                        <span class="detail-val" style="color: {'var(--danger)' if b['scope_anomaly'] else 'var(--success)'}">
                            {b['scope_status_label']}
                        </span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">JQL (Narrowing Query) 및 검증</span>
                        <div class="detail-val">{jql_badge} {jql_detail_text}</div>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Scope 검사 상세</span>
                        <span class="detail-val">{html.escape(str(b.get('filter_reason') or '이상 없음'))}</span>
                    </div>
                    <div class="detail-group">
                        <span class="detail-label">Source Environment</span>
                        <span class="detail-val">{b['source_environment']}</span>
                    </div>
                </div>

                <div class="url-links">
                    <a href="{gantt_url}" target="_blank" class="btn-link">🔗 Open in BigGantt DC</a>
                    <a href="{bp_url}" target="_blank" class="btn-link">🔗 Open in BigPicture DC</a>
                </div>
            </div>
        </div>
"""
        clean_jql_cell = jql_query if len(jql_query) <= 45 else (jql_query[:42] + "...")
        if b.get("is_closed"):
            status_tag = '<span class="badge" style="background:#475569; color:#cbd5e1;">🔒 CLOSED</span>'
        elif b.get("is_migration_target"):
            status_tag = '<span class="badge badge-success">🚀 OPEN (이관)</span>'
        elif b.get("is_jql_empty"):
            status_tag = '<span class="badge badge-danger">📭 OPEN (JQL 0건 제외)</span>'
        elif b.get("jql_invalid"):
            status_tag = '<span class="badge badge-danger">⚡ OPEN (JQL 오류 제외)</span>'
        else:
            status_tag = '<span class="badge badge-warning">⚠️ OPEN (Scope 제외)</span>'
        table_rows_html += f"""
        <tr class="excel-table-row {issue_class_str} {'tree-parent' if has_children else 'tree-leaf'} {'tree-child' if depth > 0 else 'tree-root'}"
            data-id="{str(b.get('boxId', '')).lower()}"
            data-type="{html.escape(str(b.get('boxType', '')), quote=True)}"
            data-name="{str(b.get('boxName', '')).lower()}"
            data-owner="{str(b.get('owner_name', '')).lower()} {str(b.get('owner_email', '')).lower()}"
            data-projects="{html.escape(' '.join(b.get('configured_projects', [])).lower(), quote=True)}"
            data-jql="{html.escape(jql_query.lower(), quote=True)}"
            data-closed="{str(b.get('is_closed', False)).lower()}"
            data-jqlempty="{str(b.get('is_jql_empty', False)).lower()}"
            data-migration="{str(b.get('is_migration_target', False)).lower()}"
            data-dup="{str(b['is_duplicate']).lower()}"
            data-ownererr="{str(b['owner_failed']).lower()}"
            data-dateerr="{str(b['date_anomaly']).lower()}"
            data-scopeerr="{str(b['scope_anomaly']).lower()}"
            data-jqlerr="{str(b['jql_invalid']).lower()}"
            data-raw-index="{b['index']}"
            data-raw-id="{b['boxId']}"
            data-raw-name="{html.escape(str(b['boxName']), quote=True)}"
            data-raw-projects="{html.escape(','.join(b.get('configured_projects', [])), quote=True)}"
            data-raw-dup="{dup_sort_val}"
            data-parent-id="{html.escape(str(parent_id))}"
            data-depth="{depth}"
            data-has-children="{'true' if has_children else 'false'}"
            data-child-count="{child_count}"
            data-descendant-count="{descendant_count}">
            <td style="text-align: center;">
                <input type="checkbox" class="box-select-checkbox" data-raw-id="{b['boxId']}" onclick="onBoxCheckboxChange(this, event)" aria-label="Box {html.escape(str(b['boxId']), quote=True)} 선택" style="width:16px; height:16px; cursor:pointer;">
            </td>
            <td style="text-align: center; color: var(--text-muted);">{b['index']}</td>
            <td>{box_id_link}</td>
            <td style="font-weight: 500;" title="{html.escape(str(b['boxName']), quote=True)}">{box_name_table_html}</td>
            <td><span class="badge badge-type">{html.escape(str(b['boxType']))}</span></td>
            <td style="text-align: center;">{status_tag}</td>
            <td>{b['boxStartDate']}</td>
            <td>{b['boxEndDate']}</td>
            <td style="color: {'var(--danger)' if b['owner_failed'] else 'inherit'};">{html.escape(str(b['owner_name']))}</td>
            <td style="color: {'var(--danger)' if b['owner_failed'] else 'var(--text-muted)'}; font-size:0.75rem;">{html.escape(str(b['owner_email']))}</td>
            <td>{projects_html}</td>
            <td><span class="badge {'badge-warning' if b['scope_status'] == 'PARTIAL_VALID' else 'badge-danger' if b['scope_anomaly'] else 'badge-success'}">{b['scope_status_label']}</span></td>
            <td title="{html.escape(jql_query, quote=True)}"><code style="font-size:0.725rem;">{html.escape(clean_jql_cell or '-')}</code></td>
            <td>{jql_badge}</td>
            <td>{status_badge}</td>
            <td style="text-align: center;"><a href="{gantt_url}" target="_blank" class="btn-tool" style="padding: 2px 8px; font-size: 0.75rem;">열기 🔗</a></td>
            <td style="text-align: center;"><a href="{bp_url}" target="_blank" class="btn-tool" style="padding: 2px 8px; font-size: 0.75rem;">열기 🔗</a></td>
        </tr>
"""

    embedded_boxes_json = json.dumps(processed_boxes, ensure_ascii=False).replace("<", "\\u003c")

    html_content += f"""
    </div>

    <!-- Box Table Container (Excel-like Full Table View) -->
    <div class="excel-table-container" id="boxTableContainer" style="display: none;">
        <table class="excel-table" id="excelDataTable">
            <thead>
                <tr>
                    <th style="width: 40px; text-align: center;">
                        <input type="checkbox" id="selectAllHeader" onchange="toggleSelectAll(this.checked)" title="화면에 보이는 Box 전체 선택/해제" style="width:16px; height:16px; cursor:pointer;">
                    </th>
                    <th style="width: 50px; text-align: center;">No</th>
                    <th style="width: 100px;">Box ID</th>
                    <th style="min-width: 260px;" title="부모-자식 계층 구조 (│  ⎿  )">Box Name (계층 구조)</th>
                    <th style="width: 120px;">Box Type</th>
                    <th style="width: 100px; text-align: center;">상태</th>
                    <th style="width: 110px;">시작일</th>
                    <th style="width: 110px;">종료일</th>
                    <th style="width: 120px;">Owner Name</th>
                    <th style="width: 180px;">Owner Email</th>
                    <th style="min-width: 160px;">설정된 Jira Projects</th>
                    <th style="width: 130px;">Scope 상태</th>
                    <th style="min-width: 160px;">JQL (Narrowing Query)</th>
                    <th style="width: 110px;">JQL 상태</th>
                    <th style="width: 130px;">검증 상태</th>
                    <th style="width: 90px; text-align: center;">BigGantt</th>
                    <th style="width: 90px; text-align: center;">BigPicture</th>
                </tr>
            </thead>
            <tbody id="tableBody">
                {table_rows_html}
            </tbody>
        </table>
    </div>

    <!-- KPI Summary Stats -->
    <div class="stats-grid">
        <div class="stat-card blue">
            <div class="stat-label">전체 대상 Box 개수</div>
            <div class="stat-value">{total_boxes}</div>
            <div class="stat-desc">검증 대상 전체 Box (projects.txt 일치)</div>
        </div>
        <div class="stat-card {'blue' if migration_box_count > 0 else 'amber'}">
            <div class="stat-label">🚀 이관 대상 Box</div>
            <div class="stat-value">{migration_box_count}</div>
            <div class="stat-desc">정상 이관 대상 (Closed/JQL0건/Scope오류 제외)</div>
        </div>
        <div class="stat-card {'amber' if closed_box_count > 0 else 'blue'}">
            <div class="stat-label">🔒 Closed Box</div>
            <div class="stat-value">{closed_box_count}</div>
            <div class="stat-desc">종료 상태 (마이그레이션 제외)</div>
        </div>
        <div class="stat-card {'red' if jql_empty_count > 0 else 'blue'}">
            <div class="stat-label">📭 JQL 0건 (내용없음) Box</div>
            <div class="stat-value">{jql_empty_count}</div>
            <div class="stat-desc">JQL 검색 결과 0건 (마이그레이션 제외)</div>
        </div>
        <div class="stat-card {'red' if jql_invalid_count > 0 else 'blue'}">
            <div class="stat-label">⚡ JQL 오류 Box</div>
            <div class="stat-value">{jql_invalid_count}</div>
            <div class="stat-desc">총 {has_jql_count}개 JQL 중 오류 {jql_invalid_count}개 / 경고 {jql_warning_count}개</div>
        </div>
        <div class="stat-card {'red' if scope_anomaly_count > 0 else 'blue'}">
            <div class="stat-label">⚠️ Scope 이상 Box</div>
            <div class="stat-value">{scope_anomaly_count}</div>
            <div class="stat-desc">연동 프로젝트 404 Not Found / 보관됨</div>
        </div>
        <div class="stat-card red">
            <div class="stat-label">👤 Owner 조회 실패 Box</div>
            <div class="stat-value">{owner_failed_count}</div>
            <div class="stat-desc">Jira API 사용자 조회가 안 됨</div>
        </div>
        <div class="stat-card amber">
            <div class="stat-label">중복된 이름 Box</div>
            <div class="stat-value">{duplicate_box_count}</div>
            <div class="stat-desc">{len(top_duplicates)}개 명칭에 {duplicate_box_count}개 Box 포함</div>
        </div>
        <div class="stat-card cyan">
            <div class="stat-label">날짜 미설정 / 이상 Box</div>
            <div class="stat-value">{date_anomaly_count}</div>
            <div class="stat-desc">시작일/종료일 누락 또는 임시날짜</div>
        </div>
    </div>

    <!-- Analytics Dashboard: Deduplication Reduction & Jira Project Distribution -->
    <div class="analytics-grid">
        <!-- Card 1: 중복 박스 제거 감축 효과 분석 -->
        <div class="analytics-card">
            <div class="analytics-header">
                <span class="analytics-icon">✂️</span>
                <div>
                    <h3>중복 Box 제거 시 정제/감축 효과 분석</h3>
                    <p>동일 박스 이름을 1개로 통합할 경우의 리소스 절감 지표</p>
                </div>
            </div>
            
            <div class="reduction-summary">
                <div class="reduction-metric">
                    <span class="metric-num">{unique_box_count}개</span>
                    <span class="metric-lbl">유니크 Box ({remaining_rate}%)</span>
                </div>
                <div class="reduction-arrow">➔</div>
                <div class="reduction-metric highlight">
                    <span class="metric-num">-{reducible_count}개</span>
                    <span class="metric-lbl">감축 가능 Box ({reduction_rate}%)</span>
                </div>
            </div>

            <div class="progress-bar-container">
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill green" style="width: {remaining_rate}%;" title="유니크 Box {remaining_rate}%"></div>
                    <div class="progress-bar-fill amber" style="width: {reduction_rate}%;" title="감축 가능 Box {reduction_rate}%"></div>
                </div>
                <div class="progress-bar-legend">
                    <span><span class="dot green"></span> 유니크 Box ({unique_box_count}개 / {remaining_rate}%)</span>
                    <span><span class="dot amber"></span> 감축 대상 중복 Box ({reducible_count}개 / {reduction_rate}%)</span>
                </div>
            </div>

            <div class="sub-table-title">🔥 주요 중복 Box 명칭 및 감축 수 Top List</div>
            <div class="table-scroll">
                <table class="dup-table">
                    <thead>
                        <tr>
                            <th>중복 Box 이름</th>
                            <th style="width: 80px; text-align: center;">현재 수</th>
                            <th style="width: 90px; text-align: center;">감축 수</th>
                        </tr>
                    </thead>
                    <tbody>
                        {dup_table_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Card 2: 주요 연동 Jira Projects 분포 -->
        <div class="analytics-card">
            <div class="analytics-header">
                <span class="analytics-icon">📂</span>
                <div>
                    <h3>주요 연동 Jira Projects 분포</h3>
                    <p>Box Scope에 연결된 Jira 프로젝트 통계 ({unique_projects_count}개 키, 총 {total_project_links}개 매핑)</p>
                </div>
            </div>

            <div class="project-list-container">
                {project_list_rows}
            </div>
        </div>
    </div>

    <!-- Recommendations Section -->
    <div class="recommendation-box">
        <div class="recommendation-header">
            💡 마이그레이션 품질 향상을 위한 추가 권장 검증 항목
        </div>
        <div class="recommendation-list">
            <div>• <b>Box Type 분포 검증:</b> Type별(Program, Sub-program, Agile) 매핑 적절성 확인</div>
            <div>• <b>연동 Projects 및 Scope 검증:</b> 404 미존재 또는 보관된(Archived) 프로젝트 참조 여부 점검</div>
            <div>• <b>JQL(Narrow down) 유효성:</b> 문법 오류 또는 미존재 필드를 참조하는 JQL({jql_invalid_count}건) 사전 점검/수정 필요</div>
            <div>• <b>기간(Start/End Date) 임시값 점검:</b> 2099년/9999년 등 임시 종료일 설정 여부 확인</div>
            <div>• <b>부모-자식 계층 구조(Hierarchy):</b> Cloud 이관 후 상위/하위 관계 보존 여부 검증</div>
        </div>
    </div>

    <div class="fail-api-section">
        <h2>⚠️ Scope 설정 확인 필요 Box ({scope_issue_count})</h2>
        <p style="color:var(--text-muted); margin-bottom:12px;">설정된 Project 전체와 조회 상태를 표시합니다. 이관 대상에서 제외된 Box도 포함됩니다.</p>
        <table class="fail-table">
            <thead><tr><th>Box ID</th><th>Box Name</th><th>설정된 Projects (조회 상태)</th><th>Scope 상세</th></tr></thead>
            <tbody>{scope_audit_rows}</tbody>
        </table>
    </div>

    <div class="fail-api-section collapsible-section" id="closedBoxSection">
        <div class="collapsible-header" onclick="toggleClosedBoxSection()">
            <div style="display:flex; align-items:center; gap:10px;">
                <span class="collapsible-icon" id="closedBoxToggleIcon">▶</span>
                <h2 style="margin-bottom:0;">🔒 Closed Box — projects.txt 연동 Box ({closed_count})</h2>
                <span class="badge" style="background:#334155; color:#94a3b8;">제외 대상</span>
            </div>
            <button type="button" class="btn-tool" id="btnToggleClosed" onclick="toggleClosedBoxSection(); event.stopPropagation();">
                <span id="btnToggleClosedText">펼치기 ▼ ({closed_count}개)</span>
            </button>
        </div>
        <div id="closedBoxContent" style="display: none; margin-top: 14px;">
            <p style="color:var(--text-muted); margin-bottom:12px;">projects.txt에 포함된 프로젝트가 연결되어 있으나, Closed 상태로 마이그레이션 대상에서 제외된 Box 목록입니다.</p>
            <table class="fail-table">
                <thead><tr><th>Box ID</th><th>Box Name</th><th>연동 Projects</th><th>JQL 설정 / 상태</th><th>상태 상세</th></tr></thead>
                <tbody>{closed_rows}</tbody>
            </table>
        </div>
    </div>

    <!-- Toast Notification -->
    <div id="toast" class="toast"></div>

    <!-- Copy Modal -->
    <div id="copyModal" class="modal-overlay">
        <div class="modal-content">
            <div class="modal-header">
                <h3 style="display:flex; align-items:center; gap:8px;">📋 엑셀 복사용 데이터 (TSV)</h3>
                <span class="modal-close" onclick="closeCopyModal()">&times;</span>
            </div>
            <p style="font-size:0.825rem; color:var(--text-muted); margin-bottom:12px;">
                아래 영역의 텍스트를 복사(Ctrl+C)하여 엑셀(Excel) 시트에 붙여넣기(Ctrl+V) 하시면 각 셀에 정렬되어 입력됩니다.
            </p>
            <textarea id="copyTextarea" readonly style="width:100%; height:280px; background:#0f172a; color:#f8fafc; border:1px solid var(--border-color); border-radius:6px; padding:10px; font-family:'JetBrains Mono', monospace; font-size:0.75rem; line-height:1.4; outline:none;"></textarea>
            <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:14px;">
                <button class="btn-excel" onclick="copyFromModalTextarea()">선택 영역 클립보드 복사</button>
                <button class="btn-filter" onclick="closeCopyModal()">닫기</button>
            </div>
        </div>
    </div>

    <!-- Section: Request Fail API Log / Issues -->
    <div class="fail-api-section">
        <h2>⚠️ Request Fail API & Runtime Issue Log</h2>
"""

    if api_issues:
        html_content += """
        <table class="fail-table">
            <thead>
                <tr>
                    <th style="width: 30px;">#</th>
                    <th style="width: 180px;">Category</th>
                    <th>Error Message / Failed Target URL</th>
                </tr>
            </thead>
            <tbody>
"""
        for idx, issue in enumerate(api_issues, 1):
            html_content += f"""
                <tr>
                    <td>{idx}</td>
                    <td><span class="badge badge-danger">{issue['category']}</span></td>
                    <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;">{issue['message']}</td>
                </tr>
"""
        html_content += """
            </tbody>
        </table>
"""
    else:
        html_content += """
        <div class="no-failures">
            ✅ 모든 API 요청 및 데이터 수집 과정에서 Request Fail 오류가 발생하지 않았습니다.
        </div>
"""

    html_content += rf"""
    </div>

    <script>
        const allBoxData = {embedded_boxes_json};
        let currentFilter = 'open';
        let currentSortKey = 'default';
        let currentSortOrder = 'asc';
        let currentViewMode = 'card';

        // Selection state management
        const selectedBoxIds = new Set();
        let lastClickedBoxId = null;

        // Hierarchy tree state management
        const parentMap = new Map();
        const collapsedBoxIds = new Set();

        function initTreeHierarchy() {{
            allBoxData.forEach(box => {{
                const id = String(box.boxId);
                const pid = String(box.parentBoxId || 'ROOT');
                parentMap.set(id, pid);
            }});
        }}

        function isAncestorCollapsed(boxId) {{
            let visited = new Set();
            let curr = parentMap.get(String(boxId));
            while (curr && curr !== 'ROOT' && curr !== 'None' && curr !== '' && !visited.has(curr)) {{
                visited.add(curr);
                if (collapsedBoxIds.has(curr)) {{
                    return true;
                }}
                curr = parentMap.get(curr);
            }}
            return false;
        }}

        function toggleTreeItem(boxId, event) {{
            if (event) {{
                event.stopPropagation();
                event.preventDefault();
            }}
            boxId = String(boxId);
            if (collapsedBoxIds.has(boxId)) {{
                collapsedBoxIds.delete(boxId);
            }} else {{
                collapsedBoxIds.add(boxId);
            }}
            updateTreeUI();
            filterBoxes();
        }}

        function updateTreeUI() {{
            document.querySelectorAll('.tree-toggle-btn').forEach(btn => {{
                const id = String(btn.getAttribute('data-tree-id'));
                const icon = btn.querySelector('.tree-toggle-icon');
                const isCollapsed = collapsedBoxIds.has(id);
                if (icon) {{
                    icon.textContent = isCollapsed ? '▶' : '▼';
                }}
                btn.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
                btn.classList.toggle('is-collapsed', isCollapsed);
            }});

            document.querySelectorAll('.child-count-pill').forEach(pill => {{
                const id = String(pill.getAttribute('data-tree-id'));
                const isCollapsed = collapsedBoxIds.has(id);
                pill.classList.toggle('is-collapsed', isCollapsed);
            }});

            document.querySelectorAll('.box-item').forEach(item => {{
                const id = String(item.getAttribute('data-raw-id'));
                item.classList.toggle('tree-node-collapsed', collapsedBoxIds.has(id));
            }});
            document.querySelectorAll('.excel-table-row').forEach(row => {{
                const id = String(row.getAttribute('data-raw-id'));
                row.classList.toggle('tree-node-collapsed', collapsedBoxIds.has(id));
            }});
        }}

        function expandAllTree() {{
            collapsedBoxIds.clear();
            updateTreeUI();
            filterBoxes();
            showToast("🌳 모든 하위 Box 트리를 펼쳤습니다.");
        }}

        function collapseAllTree() {{
            collapsedBoxIds.clear();
            allBoxData.forEach(box => {{
                if (box.has_children) {{
                    collapsedBoxIds.add(String(box.boxId));
                }}
            }});
            updateTreeUI();
            filterBoxes();
            showToast("📁 모든 상위 Box 트리를 접었습니다.");
        }}

        function toggleClosedBoxSection() {{
            const content = document.getElementById('closedBoxContent');
            const icon = document.getElementById('closedBoxToggleIcon');
            const btnText = document.getElementById('btnToggleClosedText');
            if (!content) return;

            const isHidden = content.style.display === 'none';
            if (isHidden) {{
                content.style.display = 'block';
                if (icon) icon.textContent = '▼';
                if (btnText) btnText.textContent = '접기 ▲';
            }} else {{
                content.style.display = 'none';
                if (icon) icon.textContent = '▶';
                if (btnText) btnText.textContent = '펼치기 ▼ ({closed_count}개)';
            }}
        }}

        function getVisibleBoxIds() {{
            // Both views use the same sorted order and visibility as the cards.
            return Array.from(document.querySelectorAll('.box-item'))
                .filter(item => item.style.display !== 'none')
                .map(item => String(item.getAttribute('data-raw-id')));
        }}

        function syncSelectionUI() {{
            document.querySelectorAll('.box-select-checkbox').forEach(cb => {{
                cb.checked = selectedBoxIds.has(String(cb.getAttribute('data-raw-id')));
            }});
            updateSelectedCount();
        }}

        function onBoxCheckboxChange(checkboxEl, event) {{
            const boxId = String(checkboxEl.getAttribute('data-raw-id'));
            const isChecked = checkboxEl.checked;
            const visibleIds = getVisibleBoxIds();
            const anchorIndex = visibleIds.indexOf(lastClickedBoxId);
            const clickedIndex = visibleIds.indexOf(boxId);
            let affectedIds = [boxId];

            if (event && event.shiftKey && anchorIndex !== -1 && clickedIndex !== -1) {{
                const start = Math.min(anchorIndex, clickedIndex);
                const end = Math.max(anchorIndex, clickedIndex);
                affectedIds = visibleIds.slice(start, end + 1);
            }}

            affectedIds.forEach(id => {{
                if (isChecked) {{
                    selectedBoxIds.add(id);
                }} else {{
                    selectedBoxIds.delete(id);
                }}
            }});
            lastClickedBoxId = boxId;
            syncSelectionUI();
        }}

        function toggleSelectAll(checked) {{
            const visibleIds = getVisibleBoxIds();
            visibleIds.forEach(id => {{
                if (checked) {{
                    selectedBoxIds.add(id);
                }} else {{
                    selectedBoxIds.delete(id);
                }}
            }});
            lastClickedBoxId = null;
            syncSelectionUI();
            showToast(`화면 목록 ${{visibleIds.length}}개 Box ${{checked ? '선택' : '선택 해제'}} 완료 (전체 선택: ${{selectedBoxIds.size}}개)`);
        }}

        function clearSelection() {{
            selectedBoxIds.clear();
            lastClickedBoxId = null;
            syncSelectionUI();
            showToast("모든 Box 선택이 해제되었습니다.");
        }}

        function selectMigrationTargetsOnly() {{
            selectedBoxIds.clear();
            lastClickedBoxId = null;

            let count = 0;
            allBoxData.forEach(box => {{
                if (box.is_migration_target) {{
                    const id = String(box.boxId);
                    selectedBoxIds.add(id);
                    count++;
                }}
            }});

            syncSelectionUI();
            showToast(`🚀 이관 대상 Box ${{count}}개가 선택되었습니다!`);
        }}

        function selectByBoxIds() {{
            const inputEl = document.getElementById('boxIdsInput');
            const inputVal = inputEl ? inputEl.value : '';
            if (!inputVal.trim()) {{
                showToast("⚠️ Box ID를 입력해 주세요. (예: 1001, 1002, 1003)");
                return;
            }}

            const rawIds = inputVal.split(/[\s,;\t\r\n]+/)
                .map(k => k.trim().replace(/^["']|["']$/g, ''))
                .filter(k => k.length > 0);
            if (rawIds.length === 0) return;

            const uniqueInputIds = Array.from(new Set(rawIds));
            const validBoxIdMap = new Map();
            allBoxData.forEach(box => {{
                validBoxIdMap.set(String(box.boxId).trim(), box);
            }});

            const matchedIds = [];
            const notFoundIds = [];

            uniqueInputIds.forEach(id => {{
                if (validBoxIdMap.has(id)) {{
                    matchedIds.push(id);
                }} else {{
                    notFoundIds.push(id);
                }}
            }});

            if (matchedIds.length === 0) {{
                const preview = uniqueInputIds.slice(0, 5).join(', ') + (uniqueInputIds.length > 5 ? ' 외...' : '');
                showToast(`⚠️ 입력한 Box ID [${{preview}}]와 일치하는 Box가 없습니다.`);
                return;
            }}

            matchedIds.forEach(id => {{
                selectedBoxIds.add(id);
            }});

            lastClickedBoxId = null;
            syncSelectionUI();

            let msg = `🎯 Box ID 총 ${{matchedIds.length}}개가 선택되었습니다!`;
            if (notFoundIds.length > 0) {{
                const missedPreview = notFoundIds.slice(0, 3).join(', ') + (notFoundIds.length > 3 ? '...' : '');
                msg += ` (미일치: ${{notFoundIds.length}}개 [${{missedPreview}}])`;
            }}
            showToast(msg);
        }}

        function updateSelectedCount() {{
            const countEl = document.getElementById('selectedCountDisplay');
            if (countEl) {{
                countEl.textContent = selectedBoxIds.size;
            }}
            const exportButton = document.getElementById('exportSelectedButton');
            const exportCount = document.getElementById('exportSelectedCount');
            if (exportButton) exportButton.disabled = selectedBoxIds.size === 0;
            if (exportCount) exportCount.textContent = `(${{selectedBoxIds.size}}개)`;

            const visibleIds = getVisibleBoxIds();
            const selectedVisibleCount = visibleIds.filter(id => selectedBoxIds.has(id)).length;
            ['selectAllCardHeader', 'selectAllHeader'].forEach(headerId => {{
                const header = document.getElementById(headerId);
                if (!header) return;
                header.checked = visibleIds.length > 0 && selectedVisibleCount === visibleIds.length;
                header.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < visibleIds.length;
                header.disabled = visibleIds.length === 0;
            }});
        }}

        function exportSelectedBoxesToCSV() {{
            if (selectedBoxIds.size === 0) {{
                showToast("⚠️ 선택된 Box가 없습니다. 먼저 Box를 체크하거나 Box ID로 선택해 주세요.");
                return;
            }}

            const targetBoxes = allBoxData.filter(b => selectedBoxIds.has(String(b.boxId)));

            const headers = [
                "Box ID",
                "Box Name",
                "Box Type",
                "시작일 (Start Date)",
                "종료일 (End Date)",
                "소유자 (Owner Name)",
                "소유자 이메일 (Owner Email)",
                "연동 Jira 프로젝트 (Jira Projects)",
                "상위 Box ID (Parent Box ID)",
                "Narrowing Query (JQL)",
                "Source Base URL",
                "Source Environment",
                "New Box ID"
            ];

            const rows = targetBoxes.map(b => {{
                const pKeys = (b.jira_projects && b.jira_projects.length > 0) 
                    ? b.jira_projects 
                    : (b.configured_projects || []);
                return [
                    b.boxId || "",
                    b.boxName || "",
                    b.boxType || "",
                    b.boxStartDate || "",
                    b.boxEndDate || "",
                    b.owner_name || "",
                    b.owner_email || "",
                    pKeys.join(", "),
                    b.parentBoxId || "ROOT",
                    b.narrowing_query || "",
                    b.source_base_url || "",
                    b.source_environment || "STG",
                    b.newBoxId || ""
                ];
            }});

            const escapeCsv = value => {{
                const text = String(value == null ? '' : value).replace(/[\t\r\n]+/g, ' ');
                return `"${{text.replace(/"/g, '""')}}"`;
            }};

            const csvContent = [headers.map(escapeCsv).join(','), ...rows.map(r => r.map(escapeCsv).join(','))].join('\r\n');
            const blob = new Blob(['\ufeff', csvContent], {{ type: 'text/csv;charset=utf-8' }});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = "migration_boxes.csv";
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);

            showToast(`✅ ${{targetBoxes.length}}개 Box가 'migration_boxes.csv'로 다운로드되었습니다! step2_import_cloud_boxes.py 실행 시 사용할 수 있습니다.`);
        }}

        function toggleAccordion(headerElem, event) {{
            if (event.target.closest('a') || window.getSelection().toString()) return;
            const item = headerElem.parentElement;
            item.classList.toggle('open');
        }}

        function setFilter(filterType, btnElem) {{
            currentFilter = filterType;
            document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
            btnElem.classList.add('active');
            filterBoxes();
        }}

        function onSortSelectChange() {{
            const select = document.getElementById('sortSelect');
            const [key, order] = select.value.split('-');
            applySort(key, order);
        }}

        function sortByColumn(key) {{
            if (currentSortKey === key) {{
                currentSortOrder = (currentSortOrder === 'asc') ? 'desc' : 'asc';
            }} else {{
                currentSortKey = key;
                currentSortOrder = (key === 'dup') ? 'desc' : 'asc';
            }}
            const selectVal = `${{key}}-${{currentSortOrder}}`;
            const selectElem = document.getElementById('sortSelect');
            if (selectElem) selectElem.value = selectVal;
            applySort(key, currentSortOrder);
        }}

        function applySort(key, order) {{
            currentSortKey = key;
            currentSortOrder = order;

            // Reset column header styles and icons
            ['id', 'name', 'projects', 'dup'].forEach(k => {{
                const head = document.getElementById(`col-head-${{k}}`);
                const icon = document.getElementById(`sort-icon-${{k}}`);
                if (head) head.classList.remove('active-sort');
                if (icon) icon.textContent = '↕';
            }});

            if (key !== 'default') {{
                const activeHead = document.getElementById(`col-head-${{key}}`);
                const activeIcon = document.getElementById(`sort-icon-${{key}}`);
                if (activeHead) activeHead.classList.add('active-sort');
                if (activeIcon) activeIcon.textContent = (order === 'asc') ? '▲' : '▼';
            }}

            const container = document.getElementById('boxList');
            const items = Array.from(container.children);

            const tableBody = document.getElementById('tableBody');
            const tableRows = Array.from(tableBody ? tableBody.children : []);

            const sortComparator = (a, b) => {{
                if (key === 'default') {{
                    const idxA = parseInt(a.getAttribute('data-raw-index') || '0');
                    const idxB = parseInt(b.getAttribute('data-raw-index') || '0');
                    return order === 'asc' ? (idxA - idxB) : (idxB - idxA);
                }}

                if (key === 'dup') {{
                    const dupA = parseInt(a.getAttribute('data-raw-dup') || '0');
                    const dupB = parseInt(b.getAttribute('data-raw-dup') || '0');
                    return order === 'asc' ? (dupA - dupB) : (dupB - dupA);
                }}

                let valA = '', valB = '';
                if (key === 'id') {{
                    valA = a.getAttribute('data-raw-id') || '';
                    valB = b.getAttribute('data-raw-id') || '';
                }} else if (key === 'name') {{
                    valA = a.getAttribute('data-raw-name') || '';
                    valB = b.getAttribute('data-raw-name') || '';
                }} else if (key === 'projects') {{
                    valA = a.getAttribute('data-raw-projects') || '';
                    valB = b.getAttribute('data-raw-projects') || '';
                }}

                const cmp = valA.localeCompare(valB, 'ko-KR', {{ numeric: true, sensitivity: 'base' }});
                return order === 'asc' ? cmp : -cmp;
            }};

            items.sort(sortComparator);
            items.forEach(item => container.appendChild(item));

            if (tableBody && tableRows.length > 0) {{
                tableRows.sort(sortComparator);
                tableRows.forEach(row => tableBody.appendChild(row));
            }}
        }}

        function filterBoxes() {{
            const query = document.getElementById('searchInput').value.toLowerCase().trim();
            const boxIdQuery = document.getElementById('boxIdFilter').value.toLowerCase().trim();
            const boxType = document.getElementById('boxTypeFilter').value;
            const items = document.querySelectorAll('.box-item');
            const tableRows = document.querySelectorAll('.excel-table-row');
            const rowsById = new Map(Array.from(tableRows, row => [row.getAttribute('data-raw-id'), row]));
            let visibleCount = 0;

            items.forEach(item => {{
                const id = item.getAttribute('data-id') || '';
                const type = item.getAttribute('data-type') || '';
                const name = item.getAttribute('data-name') || '';
                const owner = item.getAttribute('data-owner') || '';
                const projects = item.getAttribute('data-projects') || '';
                const jql = item.getAttribute('data-jql') || '';
                
                const isClosed = item.getAttribute('data-closed') === 'true';
                const isMigration = item.getAttribute('data-migration') === 'true';
                const isJqlEmpty = item.getAttribute('data-jqlempty') === 'true';
                const isDup = item.getAttribute('data-dup') === 'true';
                const isOwnerErr = item.getAttribute('data-ownererr') === 'true';
                const isDateErr = item.getAttribute('data-dateerr') === 'true';
                const isScopeErr = item.getAttribute('data-scopeerr') === 'true';
                const isJqlErr = item.getAttribute('data-jqlerr') === 'true';

                const matchesSearch = !query || id.includes(query) || name.includes(query) || owner.includes(query) || projects.includes(query) || jql.includes(query) || type.toLowerCase().includes(query);
                const matchesColumns = id.includes(boxIdQuery) && (!boxType || type === boxType);

                let matchesFilter = true;
                if (currentFilter === 'open') matchesFilter = isMigration;
                else if (currentFilter === 'closed') matchesFilter = isClosed;
                else if (currentFilter === 'jql_empty') matchesFilter = isJqlEmpty;
                else if (currentFilter === 'dup') matchesFilter = isDup;
                else if (currentFilter === 'scope_err') matchesFilter = isScopeErr;
                else if (currentFilter === 'jql_err') matchesFilter = isJqlErr;
                else if (currentFilter === 'owner_err') matchesFilter = isOwnerErr;
                else if (currentFilter === 'date_err') matchesFilter = isDateErr;

                const isSearching = Boolean(query || boxIdQuery);
                const rawId = item.getAttribute('data-raw-id') || '';
                const isTreeHidden = !isSearching && isAncestorCollapsed(rawId);

                const isVisible = matchesSearch && matchesColumns && matchesFilter && !isTreeHidden;
                item.style.display = isVisible ? 'block' : 'none';
                const tableRow = rowsById.get(item.getAttribute('data-raw-id'));
                if (tableRow) {{
                    tableRow.style.display = isVisible ? '' : 'none';
                }}
                if (isVisible) visibleCount++;
            }});

            document.getElementById('visibleCount').textContent = visibleCount + ' / ' + items.length;
            updateSelectedCount();
        }}

        function getVisibleBoxes() {{
            const visibleIds = new Set(getVisibleBoxIds());
            return allBoxData.filter(box => visibleIds.has(String(box.boxId)));
        }}

        function downloadBoxes(format) {{
            const boxes = getVisibleBoxes();
            const headers = [
                "순번", "상위 Box ID (Parent Box ID)", "Box ID", "Box Name", "계층 레벨 (Depth)", "Box Type", "Box 상태 (Status)",
                "시작일", "종료일", "Owner Name", "Owner Email", 
                "Jira Projects", "Scope 상태", "JQL (Narrowing Query)", "JQL 상태", "검증 상태", "BigGantt URL", "BigPicture URL"
            ];
            const rows = boxes.map((box, index) => {{
                const base = (box.source_base_url || '').replace(/\/$/, '');
                let statusStr = 'OPEN (이관 대상)';
                if (box.is_closed) statusStr = 'CLOSED (제외)';
                else if (box.is_jql_empty) statusStr = 'OPEN (JQL 0건 제외)';
                else if (box.jql_status === 'INVALID' || box.jql_status === 'ERROR') statusStr = 'OPEN (JQL 오류 제외)';
                else if (box.scope_anomaly || !box.is_scope_valid) statusStr = `OPEN (${{box.scope_status_label || 'Scope제외'}} - 제외)`;
                else if (!box.is_migration_target) statusStr = 'OPEN (제외)';

                const parentId = box.parentBoxId || 'ROOT';
                const depth = box.depth != null ? box.depth : 0;
                const prefix = box.hierarchy_prefix || (depth > 0 ? ('│  '.repeat(depth - 1) + '│  ⎿  ') : '');
                const cleanName = (prefix + (box.boxName || '')).replace(/\t|\r|\n/g, ' ');

                return [
                    index + 1,
                    parentId,
                    box.boxId,
                    cleanName,
                    depth,
                    box.boxType,
                    statusStr,
                    box.boxStartDate,
                    box.boxEndDate,
                    box.owner_name,
                    box.owner_email,
                    (box.jira_projects || []).join(', '),
                    box.scope_status_label,
                    box.narrowing_query || '-',
                    box.jql_status === 'WARNING' ? `경고: ${{box.jql_error || ''}}` : box.jql_status,
                    box.validation_status,
                    base ? `${{base}}/plugins/servlet/softwareplant-biggantt/#/box/${{encodeURIComponent(box.boxId)}}/g` : '',
                    base ? `${{base}}/secure/BigPicture.jspa?boxId=${{encodeURIComponent(box.boxId)}}#box/${{encodeURIComponent(box.boxId)}}` : ''
                ];
            }});
            const separator = format === 'csv' ? ',' : '\t';
            const encode = value => {{
                const text = String(value == null ? '' : value).replace(/[\t\r\n]+/g, ' ');
                return format === 'csv' ? `"${{text.replace(/"/g, '""')}}"` : text;
            }};
            const content = [headers, ...rows].map(row => row.map(encode).join(separator)).join('\r\n');
            const blob = new Blob(['\ufeff', content], {{ type: format === 'csv' ? 'text/csv;charset=utf-8' : 'text/tab-separated-values;charset=utf-8' }});
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `exported_boxes_filtered.${{format}}`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        }}

        function copyToExcel(onlyVisible = true) {{
            const visibleIds = new Set();
            if (onlyVisible) {{
                document.querySelectorAll('.box-item').forEach(item => {{
                    if (item.style.display !== 'none') {{
                        visibleIds.add(item.getAttribute('data-raw-id'));
                    }}
                }});
            }}

            const targetBoxes = onlyVisible 
                ? allBoxData.filter(b => visibleIds.has(String(b.boxId)))
                : allBoxData;

            if (targetBoxes.length === 0) {{
                showToast("⚠️ 복사할 Box 데이터가 없습니다.");
                return;
            }}

            const headers = [
                "순번", "상위 Box ID (Parent Box ID)", "Box ID", "Box Name", "계층 레벨 (Depth)", "Box Type", "Box 상태 (Status)",
                "시작일", "종료일", "Owner Name", "Owner Email", 
                "Jira Projects", "Scope 상태", "JQL (Narrowing Query)", "JQL 상태", "검증 상태", "BigGantt URL", "BigPicture URL"
            ];

            const rows = targetBoxes.map((b, idx) => {{
                const ganttUrl = b.source_base_url ? `${{b.source_base_url.replace(/\/$/, '')}}/plugins/servlet/softwareplant-biggantt/#/box/${{encodeURIComponent(b.boxId)}}/g` : "";
                const bpUrl = b.source_base_url ? `${{b.source_base_url}}/secure/BigPicture.jspa?boxId=${{b.boxId}}#box/${{b.boxId}}` : "";
                
                const parentId = b.parentBoxId || 'ROOT';
                const depth = b.depth != null ? b.depth : 0;
                const prefix = b.hierarchy_prefix || (depth > 0 ? ('│  '.repeat(depth - 1) + '│  ⎿  ') : '');
                const cleanName = (prefix + (b.boxName || "")).replace(/\t|\r|\n/g, " ");
                const cleanJql = (b.narrowing_query || "-").replace(/\t|\r|\n/g, " ");

                let statusStr = 'OPEN (이관 대상)';
                if (b.is_closed) statusStr = 'CLOSED (제외)';
                else if (b.is_jql_empty) statusStr = 'OPEN (JQL 0건 제외)';
                else if (b.jql_status === 'INVALID' || b.jql_status === 'ERROR') statusStr = 'OPEN (JQL 오류 제외)';
                else if (b.scope_anomaly || !b.is_scope_valid) statusStr = `OPEN (${{b.scope_status_label || 'Scope제외'}} - 제외)`;
                else if (!b.is_migration_target) statusStr = 'OPEN (제외)';
                let jqlStatusText = b.jql_status || "NONE";
                if (b.jql_status === "VALID") jqlStatusText = `정상 (${{b.jql_total_matched || 0}}건)`;
                else if (b.jql_status === "WARNING") jqlStatusText = `경고: ${{b.jql_error || ''}}`;
                else if (b.jql_status === "INVALID") jqlStatusText = `오류: ${{b.jql_error || ''}}`;

                return [
                    idx + 1,
                    parentId,
                    b.boxId || "",
                    cleanName,
                    depth,
                    b.boxType || "",
                    statusStr,
                    b.boxStartDate || "",
                    b.boxEndDate || "",
                    b.owner_name || "",
                    b.owner_email || "",
                    (b.jira_projects || []).join(", "),
                    b.scope_status_label || "",
                    cleanJql,
                    jqlStatusText,
                    b.validation_status || "",
                    ganttUrl,
                    bpUrl
                ].join("\t");
            }});

            const tsvText = [headers.join("\t"), ...rows].join("\n");

            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(tsvText).then(() => {{
                    showToast(`✅ ${{targetBoxes.length}}개 Box 정보가 엑셀 형식(TSV)으로 복사되었습니다! 엑셀에서 Ctrl+V 하세요.`);
                }}).catch(err => {{
                    console.warn("Clipboard API failed, opening modal:", err);
                    openCopyModal(tsvText);
                }});
            }} else {{
                openCopyModal(tsvText);
            }}
        }}

        function showToast(msg) {{
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.style.display = 'block';
            toast.style.opacity = '1';
            clearTimeout(window._toastTimeout);
            window._toastTimeout = setTimeout(() => {{
                toast.style.opacity = '0';
                setTimeout(() => {{ toast.style.display = 'none'; }}, 300);
            }}, 3500);
        }}

        function openCopyModal(text) {{
            if (!text) {{
                const visibleIds = new Set();
                document.querySelectorAll('.box-item').forEach(item => {{
                    if (item.style.display !== 'none') visibleIds.add(item.getAttribute('data-raw-id'));
                }});
                const targetBoxes = allBoxData.filter(b => visibleIds.has(String(b.boxId)));
                const headers = [
                    "순번", "상위 Box ID (Parent Box ID)", "Box ID", "Box Name", "계층 레벨 (Depth)", "Box Type", "Box 상태 (Status)",
                    "시작일", "종료일", "Owner Name", "Owner Email", 
                    "Jira Projects", "Scope 상태", "JQL (Narrowing Query)", "JQL 상태", "검증 상태", "BigGantt URL", "BigPicture URL"
                ];
                const rows = targetBoxes.map((b, idx) => {{
                    const ganttUrl = b.source_base_url ? `${{b.source_base_url.replace(/\/$/, '')}}/plugins/servlet/softwareplant-biggantt/#/box/${{encodeURIComponent(b.boxId)}}/g` : "";
                    const bpUrl = b.source_base_url ? `${{b.source_base_url}}/secure/BigPicture.jspa?boxId=${{b.boxId}}#box/${{b.boxId}}` : "";
                    const parentId = b.parentBoxId || 'ROOT';
                    const depth = b.depth != null ? b.depth : 0;
                    const prefix = b.hierarchy_prefix || (depth > 0 ? ('│  '.repeat(depth - 1) + '│  ⎿  ') : '');
                    const cleanName = (prefix + (b.boxName || "")).replace(/\t|\r|\n/g, " ");
                    const cleanJql = (b.narrowing_query || "-").replace(/\t|\r|\n/g, " ");
                    let statusStr = 'OPEN (이관 대상)';
                    if (b.is_closed) statusStr = 'CLOSED (제외)';
                    else if (b.is_jql_empty) statusStr = 'OPEN (JQL 0건 제외)';
                    else if (b.jql_status === 'INVALID' || b.jql_status === 'ERROR') statusStr = 'OPEN (JQL 오류 제외)';
                    else if (b.scope_anomaly || !b.is_scope_valid) statusStr = `OPEN (${{b.scope_status_label || 'Scope제외'}} - 제외)`;
                    else if (!b.is_migration_target) statusStr = 'OPEN (제외)';
                    let jqlStatusText = b.jql_status || "NONE";
                    if (b.jql_status === "VALID") jqlStatusText = `정상 (${{b.jql_total_matched || 0}}건)`;
                    else if (b.jql_status === "WARNING") jqlStatusText = `경고: ${{b.jql_error || ''}}`;
                    else if (b.jql_status === "INVALID") jqlStatusText = `오류: ${{b.jql_error || ''}}`;

                    return [
                        idx + 1, parentId, b.boxId || "", cleanName, depth, b.boxType || "", statusStr,
                        b.boxStartDate || "", b.boxEndDate || "", b.owner_name || "", b.owner_email || "",
                        (b.jira_projects || []).join(", "), b.scope_status_label || "", cleanJql,
                        jqlStatusText, b.validation_status || "", ganttUrl, bpUrl
                    ].join("\t");
                }});
                text = [headers.join("\t"), ...rows].join("\n");
            }}
            const modal = document.getElementById('copyModal');
            const textarea = document.getElementById('copyTextarea');
            textarea.value = text;
            modal.style.display = 'flex';
            textarea.focus();
            textarea.select();
        }}

        function closeCopyModal() {{
            document.getElementById('copyModal').style.display = 'none';
        }}

        function copyFromModalTextarea() {{
            const textarea = document.getElementById('copyTextarea');
            textarea.focus();
            textarea.select();
            document.execCommand('copy');
            showToast("✅ 클립보드에 복사되었습니다! 엑셀에서 Ctrl+V 하세요.");
        }}

        function setViewMode(mode) {{
            currentViewMode = mode;
            const cardBtn = document.getElementById('btnViewCard');
            const tableBtn = document.getElementById('btnViewTable');
            const cardList = document.getElementById('boxList');
            const cardHeaders = document.querySelector('.box-list-column-headers');
            const tableContainer = document.getElementById('boxTableContainer');

            if (mode === 'card') {{
                cardBtn.classList.add('active');
                tableBtn.classList.remove('active');
                cardList.style.display = 'flex';
                cardHeaders.style.display = 'grid';
                tableContainer.style.display = 'none';
            }} else {{
                tableBtn.classList.add('active');
                cardBtn.classList.remove('active');
                cardList.style.display = 'none';
                cardHeaders.style.display = 'none';
                tableContainer.style.display = 'block';
            }}
        }}

        // Start with migration targets visible and no implicit selection.
        initTreeHierarchy();
        updateTreeUI();
        filterBoxes();
    </script>

</body>
</html>
"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ Report successfully generated at: {OUTPUT_HTML}")
    return OUTPUT_HTML

if __name__ == "__main__":
    generate_report()
