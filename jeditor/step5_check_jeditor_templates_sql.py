#!/usr/bin/env python3
"""Check JEditor templates via MySQL SQL query or imported SQL HTML dump,
and generate an interactive dashboard report (template_report.html)
optimized for copying and pasting into Confluence.

Replaces the slow browser-based crawling of check_jeditor_templates_browser.py
by directly querying the Active Objects table `AO_BF4748_PROJECT_TEMPLATE` in Jira MySQL DB,
or parsing query runner SQL HTML dumps.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from default_value_policy import Policy, load_snapshot

try:
    import pymysql
except ImportError:
    pymysql = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECTS_FILE = SCRIPT_DIR.parent / "projects.txt"
DEFAULT_OUTPUT_REPORT = SCRIPT_DIR / "template_report.html"
DEFAULT_CONFLUENCE_REPORT = SCRIPT_DIR / "template_report_confluence.html"
DEFAULT_SQL_FILE = SCRIPT_DIR / "jeditor_template_queries.sql"

# Built-in Jira Data Center standard issue types (IDs 1-7, 10000-10004, and common enterprise/Korean names)
DEFAULT_ISSUE_TYPES: dict[str, str] = {
    # Default Jira DC Issue Types
    "1": "Bug",
    "2": "New Feature",
    "3": "Task",
    "4": "Improvement",
    "5": "Sub-task",
    "6": "Epic",
    "7": "Story",
    "10000": "Epic",
    "10001": "Story",
    "10002": "Sub-task",
    "10003": "Bug",
    "10004": "Task",
    # Common Enterprise & Korean Jira Issue Types
    "10100": "결함",
    "10101": "사양",
    "10102": "개선",
    "10103": "요구사항",
    "10104": "개발이슈",
    "10105": "기술지원",
    "10200": "Defect",
    "10201": "Release",
    "10202": "Spec",
}


def load_issue_type_mappings(
    custom_file: str | Path | None = None,
    custom_map_str: str | None = None,
    base_jira_url: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    """Loads issue type ID-to-Name mappings from built-in defaults, local files, and optional API."""
    mapping = dict(DEFAULT_ISSUE_TYPES)

    # 1. Check local JSON files (issue_types.json, jira_issue_types_cache.json)
    search_paths = []
    if custom_file:
        search_paths.append(Path(custom_file))
    search_paths.extend([
        SCRIPT_DIR / "issue_types.json",
        SCRIPT_DIR.parent / "issue_types.json",
        SCRIPT_DIR / "jira_issue_types_cache.json",
        SCRIPT_DIR.parent / "jira_issue_types_cache.json",
    ])

    for p in search_paths:
        if p and p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if k and v:
                            mapping[str(k).strip()] = str(v).strip()
                    break
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "id" in item and "name" in item:
                            mapping[str(item["id"]).strip()] = str(item["name"]).strip()
                    break
            except Exception:
                pass

    # 2. Parse inline CLI map string (e.g. "10000=Bug, 10001=Task")
    if custom_map_str:
        for pair in custom_map_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                mapping[k.strip()] = v.strip()
            elif ":" in pair:
                k, v = pair.split(":", 1)
                mapping[k.strip()] = v.strip()

    # 3. Optional Jira REST API fetch if credentials provided and cache not present
    if base_jira_url and token:
        api_cache = SCRIPT_DIR / "jira_issue_types_cache.json"
        if not api_cache.exists():
            try:
                import urllib.request
                req_url = f"{base_jira_url.rstrip('/')}/rest/api/2/issuetype"
                req = urllib.request.Request(req_url)
                req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    api_data = json.loads(resp.read().decode())
                    if isinstance(api_data, list):
                        api_map = {}
                        for it in api_data:
                            if "id" in it and "name" in it:
                                it_id = str(it["id"]).strip()
                                it_name = str(it["name"]).strip()
                                mapping[it_id] = it_name
                                api_map[it_id] = it_name
                        if api_map:
                            api_cache.write_text(json.dumps(api_map, ensure_ascii=False, indent=2), encoding="utf-8")
                            print(f"🌐 Jira REST API에서 {len(api_map)}개 이슈 타입 자동 동기화 완료")
            except Exception:
                pass

    return mapping


def resolve_issue_types(
    raw_str: str,
    issue_type_map: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Resolves raw issue types (numeric IDs or mixed names) into '숫자(문자)' format.

    Examples:
        '10000' -> ('10000(Epic)', ['10000(Epic)'])
        '10000, 10001' -> ('10000(Epic), 10001(Story)', ['10000(Epic)', '10001(Story)'])
        '10000(Epic)' -> ('10000(Epic)', ['10000(Epic)'])
        '10000 (Epic)' -> ('10000(Epic)', ['10000(Epic)'])
        'Bug, Defect' -> ('Bug, Defect', ['Bug', 'Defect'])
        '99999' -> ('99999', ['99999'])  (unknown ID kept as is)
    """
    if not raw_str or raw_str.strip() in ("", "EMPTY_VALUE", "null", "None", "-"):
        return "", []

    mapping = issue_type_map if issue_type_map is not None else DEFAULT_ISSUE_TYPES

    items = [it.strip() for it in re.split(r"[,;]+", raw_str) if it.strip()]
    if not items:
        return "", []

    resolved_items = []
    for item in items:
        # 1. Already in format ID(Name) or ID (Name)
        m = re.match(r"^(\d+)\s*\((.*?)\)$", item)
        if m:
            num_id = m.group(1).strip()
            name = m.group(2).strip()
            if not name and num_id in mapping:
                name = mapping[num_id]
            resolved_items.append(f"{num_id}({name})" if name else num_id)
            continue

        # 2. Pure numeric ID (e.g. 10000, 1) -> convert to 10000(Name)
        if item.isdigit():
            name = mapping.get(item)
            if name:
                resolved_items.append(f"{item}({name})")
            else:
                resolved_items.append(item)
            continue

        # 3. Non-numeric or custom text name -> keep as is
        resolved_items.append(item)

    display_str = ", ".join(resolved_items)
    return display_str, resolved_items


# Built-in Jira Data Center common custom field names
DEFAULT_CUSTOM_FIELDS: dict[str, str] = {
    "customfield_10010": "Release Notes",
    "customfield_10011": "Sprint Notes",
    "customfield_10020": "사양서 내용",
    "customfield_10500": "장르",
    "customfield_18908": "계획 완료일",
    "customfield_18909": "시작일",
    "customfield_18910": "완료일",
}


def load_custom_field_mappings(
    custom_file: str | Path | None = None,
    custom_map_str: str | None = None,
    base_jira_url: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    """Loads custom field ID-to-Name mappings from built-in defaults, local files, and optional API."""
    mapping = dict(DEFAULT_CUSTOM_FIELDS)

    # 1. Check local JSON files (custom_fields.json, jira_custom_fields_cache.json)
    search_paths = []
    if custom_file:
        search_paths.append(Path(custom_file))
    search_paths.extend([
        SCRIPT_DIR / "custom_fields.json",
        SCRIPT_DIR.parent / "custom_fields.json",
        SCRIPT_DIR / "jira_custom_fields_cache.json",
        SCRIPT_DIR.parent / "jira_custom_fields_cache.json",
    ])

    for p in search_paths:
        if p and p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if k and v:
                            k_str = str(k).strip()
                            v_str = str(v).strip()
                            mapping[k_str] = v_str
                            if k_str.startswith("customfield_"):
                                mapping[k_str.replace("customfield_", "")] = v_str
                            elif k_str.isdigit():
                                mapping[f"customfield_{k_str}"] = v_str
                    break
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "id" in item and "name" in item:
                            k_str = str(item["id"]).strip()
                            v_str = str(item["name"]).strip()
                            mapping[k_str] = v_str
                            if k_str.startswith("customfield_"):
                                mapping[k_str.replace("customfield_", "")] = v_str
                    break
            except Exception:
                pass

    # 2. Parse inline CLI map string (e.g. "customfield_10010=Release Notes, 10020=Spec")
    if custom_map_str:
        for pair in custom_map_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                k_s, v_s = k.strip(), v.strip()
                mapping[k_s] = v_s
                if k_s.startswith("customfield_"):
                    mapping[k_s.replace("customfield_", "")] = v_s
                elif k_s.isdigit():
                    mapping[f"customfield_{k_s}"] = v_s
            elif ":" in pair:
                k, v = pair.split(":", 1)
                k_s, v_s = k.strip(), v.strip()
                mapping[k_s] = v_s
                if k_s.startswith("customfield_"):
                    mapping[k_s.replace("customfield_", "")] = v_s
                elif k_s.isdigit():
                    mapping[f"customfield_{k_s}"] = v_s

    # 3. Optional Jira REST API fetch if credentials provided and cache not present
    if base_jira_url and token:
        api_cache = SCRIPT_DIR / "jira_custom_fields_cache.json"
        if not api_cache.exists():
            try:
                import urllib.request
                req_url = f"{base_jira_url.rstrip('/')}/rest/api/2/field"
                req = urllib.request.Request(req_url)
                req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    api_data = json.loads(resp.read().decode())
                    if isinstance(api_data, list):
                        api_map = {}
                        for f in api_data:
                            fid = str(f.get("id") or "").strip()
                            fname = str(f.get("name") or "").strip()
                            if fid.startswith("customfield_") and fname:
                                mapping[fid] = fname
                                mapping[fid.replace("customfield_", "")] = fname
                                api_map[fid] = fname
                                api_map[fid.replace("customfield_", "")] = fname
                        if api_map:
                            api_cache.write_text(json.dumps(api_map, ensure_ascii=False, indent=2), encoding="utf-8")
                            print(f"🌐 Jira REST API에서 {len(api_map)//2}개 커스텀 필드 자동 동기화 완료")
            except Exception:
                pass

    return mapping


def resolve_field_display(
    fields_raw: str,
    field_name: str = "",
    custom_field_map: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Resolves applied field(s) into 'customfield_ID(Name)' format or standard system field names.

    Examples:
        'customfield_10010', 'Release Notes' -> ('customfield_10010(Release Notes)', ['customfield_10010(Release Notes)'])
        'customfield_10010', '' (mapped)     -> ('customfield_10010(Release Notes)', ['customfield_10010(Release Notes)'])
        'customfield_10010(Release Notes)'   -> ('customfield_10010(Release Notes)', ['customfield_10010(Release Notes)'])
        'description', 'Description'         -> ('Description', ['Description'])
        'environment', 'Environment'         -> ('Environment', ['Environment'])
        '', ''                               -> ('', [])
    """
    raw_clean = str(fields_raw or "").strip()
    if raw_clean in ("EMPTY_VALUE", "null", "None", "-"):
        raw_clean = ""

    name_clean = str(field_name or "").strip()
    if name_clean in ("EMPTY_VALUE", "null", "None", "-"):
        name_clean = ""

    if not raw_clean and not name_clean:
        return "", []

    mapping = custom_field_map if custom_field_map is not None else DEFAULT_CUSTOM_FIELDS

    # If raw_clean is empty but name_clean is provided:
    if not raw_clean and name_clean:
        m = re.match(r"^(customfield_\d+|\d+)\s*\((.*?)\)$", name_clean)
        if m:
            cid, cname = m.group(1), m.group(2).strip()
            if cid.isdigit():
                cid = f"customfield_{cid}"
            formatted = f"{cid}({cname})" if cname else cid
            return formatted, [formatted]
        if name_clean.lower() == "description":
            return "Description", ["Description"]
        if name_clean.lower() == "environment":
            return "Environment", ["Environment"]
        if name_clean.startswith("customfield_"):
            cid = name_clean
            lookup_name = mapping.get(cid) or mapping.get(cid.replace("customfield_", ""))
            formatted = f"{cid}({lookup_name})" if lookup_name else cid
            return formatted, [formatted]
        # Reverse lookup in mapping if name_clean matches custom field name
        reverse_cf = {v.lower(): k for k, v in mapping.items() if k.startswith("customfield_")}
        if name_clean.lower() in reverse_cf:
            cid = reverse_cf[name_clean.lower()]
            formatted = f"{cid}({name_clean})"
            return formatted, [formatted]
        return name_clean, [name_clean]

    raw_items = [it.strip() for it in re.split(r"[,;]+", raw_clean) if it.strip()]
    name_items = [it.strip() for it in re.split(r"[,;]+", name_clean) if it.strip()]

    resolved_items = []
    for idx, item in enumerate(raw_items):
        item_lower = item.lower()

        # Standard Jira system fields
        if item_lower == "description":
            resolved_items.append("Description")
            continue
        if item_lower == "environment":
            resolved_items.append("Environment")
            continue

        # Check if item is already in format ID(Name)
        m = re.match(r"^(customfield_\d+|\d+)\s*\((.*?)\)$", item)
        if m:
            cid, cname = m.group(1), m.group(2).strip()
            if cid.isdigit():
                cid = f"customfield_{cid}"
            if not cname:
                cname = mapping.get(cid) or mapping.get(cid.replace("customfield_", "")) or ""
            resolved_items.append(f"{cid}({cname})" if cname else cid)
            continue

        # Custom field ID (e.g. customfield_10010 or 10010)
        is_cf_id = item.startswith("customfield_") or item.isdigit()
        if is_cf_id:
            cid = item if item.startswith("customfield_") else f"customfield_{item}"
            cname = ""
            if idx < len(name_items) and name_items[idx] and not name_items[idx].startswith("customfield_"):
                cname = name_items[idx]
            if not cname:
                cname = mapping.get(cid) or mapping.get(cid.replace("customfield_", "")) or ""
            m_name = re.match(r"^(?:customfield_\d+|\d+)\s*\((.*?)\)$", cname)
            if m_name:
                cname = m_name.group(1).strip()

            if cname:
                resolved_items.append(f"{cid}({cname})")
            else:
                resolved_items.append(cid)
            continue

        # If item is not ID but a field name
        reverse_cf = {v.lower(): k for k, v in mapping.items() if k.startswith("customfield_")}
        if item_lower in reverse_cf:
            cid = reverse_cf[item_lower]
            resolved_items.append(f"{cid}({item})")
            continue

        resolved_items.append(item)

    display_str = ", ".join(resolved_items)
    return display_str, resolved_items


def load_env_file() -> None:
    """Load environment variables from ../env or ./env file."""
    for env_path in [SCRIPT_DIR.parent / "env", SCRIPT_DIR / "env"]:
        if not env_path.exists():
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_path)
            return
        except ModuleNotFoundError:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return


load_env_file()


def get_db_config() -> dict[str, str | int]:
    """Extracts DB configuration from environment variables."""
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
        "database": database,
    }


def format_duration(seconds: float) -> str:
    """Formats elapsed seconds into a clean, human-readable string."""
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


def load_projects_list() -> list[str]:
    """Load project keys from projects.txt if present."""
    if not PROJECTS_FILE.exists():
        return []
    projects = []
    seen = set()
    for line in PROJECTS_FILE.read_text(encoding="utf-8-sig").splitlines():
        p = line.split("#", 1)[0].strip().upper()
        if p and re.fullmatch(r"[A-Z0-9_-]+", p) and p not in seen:
            projects.append(p)
            seen.add(p)
    return projects


def clean_visibility(val: str | None) -> str:
    """Format visibility strings into clean, user-friendly labels."""
    if not val or val.strip() in ("", "EMPTY_VALUE", "null", "None"):
        return "전체 (All)"
    return val.strip()


def clean_template_content_html(source: str) -> str:
    """Clean and normalize JEditor template HTML into safe, Confluence-friendly rich HTML.
    Converts JEditor tabs into clear headings, normalizes breaks, and strips scripts/interactive tags.
    Preserves bolding, paragraphs, lists, tables, and text structure.
    """
    if not source or not source.strip():
        return '<span style="color:#6b778c;">(내용 없음)</span>'

    raw = source.strip()
    raw = re.sub(r"</br>", "<br/>", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<br\s*>", "<br/>", raw, flags=re.IGNORECASE)

    # Convert JEditor tab markup if present
    raw = re.sub(
        r'<div[^>]*class=["\'][^"\']*jeditor-tab-panel[^"\']*["\'][^>]*(?:data-tab-title|data-title)=["\']([^"\']+)["\'][^>]*>(.*?)</div>',
        r'<div style="margin:4px 0;"><strong style="color:#0052cc;">[\1]</strong><div style="padding-left:6px;">\2</div></div>',
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    raw = re.sub(r'<div[^>]*class=["\'][^"\']*jeditor-tabs[^"\']*["\'][^>]*>', "<div>", raw, flags=re.IGNORECASE)

    # Strip dangerous / interactive tags
    raw = re.sub(r"<(script|style|iframe|object|embed|form|input|button|svg)[^>]*>.*?</\1>", "", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<(script|style|iframe|object|embed|form|input|button|svg)[^>]*/>", "", raw, flags=re.IGNORECASE)

    return raw.strip()


def simple_html_to_adf(source: str) -> dict:
    """Fallback parser that converts basic HTML into valid Atlassian Document Format (ADF) JSON.
    Used when bs4 or migration_content dependencies are not available.
    """
    if not source or not source.strip():
        return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": []}]}

    content_blocks = []
    paragraphs = re.split(r"</p>|<br\s*/?>|</div>|</h1>|</h2>|</h3>|</li>", source, flags=re.IGNORECASE)
    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        inline_nodes = []
        parts = re.split(r"(<b>.*?</b>|<strong>.*?</strong>)", p_clean, flags=re.IGNORECASE)
        for part in parts:
            if not part:
                continue
            is_bold = bool(re.match(r"<(b|strong)>.*?</(b|strong)>", part, re.IGNORECASE))
            text = html.unescape(re.sub(r"<[^>]+>", "", part)).strip()
            if text:
                node = {"type": "text", "text": text}
                if is_bold:
                    node["marks"] = [{"type": "strong"}]
                inline_nodes.append(node)
        if inline_nodes:
            content_blocks.append({"type": "paragraph", "content": inline_nodes})

    if not content_blocks:
        plain = html.unescape(re.sub(r"<[^>]+>", "", source)).strip()
        if plain:
            content_blocks.append({"type": "paragraph", "content": [{"type": "text", "text": plain}]})
        else:
            content_blocks.append({"type": "paragraph", "content": []})

    return {"type": "doc", "version": 1, "content": content_blocks}


def to_adf_document(source: str) -> dict:
    """Convert HTML template source to Atlassian Document Format (ADF) document dict."""
    if not source or not source.strip():
        return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": []}]}

    def attachment(reference: str, *, image: bool, width=None, height=None):
        label = Path(reference.split("?", 1)[0]).name or reference or "attachment"
        if not image:
            return {"type": "paragraph", "content": [{"type": "text", "text": f"[Attachment: {label}]"}]}
        attrs = {
            "type": "file",
            "id": str(uuid.uuid4()),
            "collection": "",
            "alt": label,
            "width": int(width) if str(width).isdigit() and int(width) > 0 else 640,
            "height": int(height) if str(height).isdigit() and int(height) > 0 else 480,
        }
        return {
            "type": "mediaSingle",
            "attrs": {"layout": "center"},
            "content": [{"type": "media", "attrs": attrs}],
        }

    try:
        from migration_content import source_to_adf
        return source_to_adf(source, attachment)
    except Exception:
        return simple_html_to_adf(source)


def attach_content_and_adf(records: list[dict]) -> None:
    """Ensure every template record has cleaned_content, adf_doc dict, and adf_json string."""
    for r in records:
        if "cleaned_content" not in r:
            r["cleaned_content"] = clean_template_content_html(r.get("code", ""))
        if "adf_json" not in r or "adf_doc" not in r:
            doc = to_adf_document(r.get("code", ""))
            r["adf_doc"] = doc
            r["adf_json"] = json.dumps(doc, ensure_ascii=False, indent=2)


def validate_template_settings(row: dict) -> dict:
    """Compare DC ID sets with explicit Visibility ALL as an unrestricted scope."""
    aliases = {
        'autofill_fields': ('PREPOPULATION_FIELDS', 'FIELD_ID'),
        'autofill_types': ('PREPOPULATION_TYPES', 'ISSUE_TYPES_RAW'),
        'visibility_fields': ('VISIBILITY_FIELDS',),
        'visibility_types': ('VISIBILITY_TYPES',),
        'visibility_groups': ('VISIBILITY_USER_GROUPS', 'VISIBILITY_GROUPS'),
    }
    raw, present, normalized, reasons = {}, {}, {}, []
    unknown = mismatch = review = False
    for name, keys in aliases.items():
        key = next((k for k in keys if k in row), None)
        present[name] = key is not None
        value = row.get(key) if key else None
        raw[name] = value
        if key is None or value is None:
            unknown = True
            reasons.append(f'{name}: 원본 설정 누락/NULL')
            normalized[name] = []
            continue
        value = str(value).strip()
        if name == 'visibility_groups':
            if value not in ('', 'EMPTY_VALUE') and value.upper() != 'ALL':
                review = True
                reasons.append('사용자 그룹 가시성 제한: Cloud 조건 별도 검토')
            normalized[name] = value
            continue
        if value in ('', 'EMPTY_VALUE'):
            normalized[name] = []
            continue
        ids = set()
        for token in re.split(r'[,;]', value):
            token = token.strip().lower()
            if token == 'all' and (name.startswith('visibility_') or name == 'autofill_types'):
                ids.add('ALL')
                continue
            if name.endswith('fields') and re.fullmatch(r'(?:customfield_)?\d+', token):
                token = 'customfield_' + token.removeprefix('customfield_')
            pattern = r'(?:customfield_\d+|description|environment|comment)' if name.endswith('fields') else r'\d+'
            if not re.fullmatch(pattern, token):
                unknown = True
                reasons.append(f'{name}: 해석 불가 {token!r}')
            else:
                ids.add(token)
        normalized[name] = sorted(ids)
    for dimension in ('fields', 'types'):
        auto = set(normalized['autofill_' + dimension])
        visible = set(normalized['visibility_' + dimension])
        if not auto:
            mismatch = True
            reasons.append(f'autofill_{dimension}: 대상 미지정')
        elif 'ALL' in visible:
            # Explicit ALL covers every concrete Autofill ID in this dimension.
            continue
        elif not visible:
            review = True
            reasons.append(f'visibility_{dimension}: 제한 미지정, Autofill과 범위 확인 필요')
        elif auto - visible:
            mismatch = True
            reasons.append(f'{dimension}: Visibility 밖의 Autofill ID {sorted(auto - visible)}')
        elif auto != visible:
            review = True
            reasons.append(f'{dimension}: Visibility 범위가 Autofill보다 넓음')
    status = 'UNKNOWN' if unknown else 'MISMATCH' if mismatch else 'REVIEW' if review else 'PASS'
    return dict(version=1, status=status, reasons=reasons or ['Visibility가 Autofill 범위를 포함함 (ID 일치 또는 ALL), 그룹 제한 없음'],
                raw=raw, present=present, normalized=normalized)


def build_template_record(
    row: dict,
    env_name: str,
    base_jira_url: str,
    source_name: str = "SQL DB",
    issue_type_map: dict[str, str] | None = None,
    custom_field_map: dict[str, str] | None = None,
) -> dict:
    """Constructs a standardized template record dict from row mapping."""
    pkey = (
        row.get("PROJECT_KEY")
        or row.get("PROJECT")
        or row.get("PKEY")
        or (f"PROJ_{row['PROJECT_ID']}" if row.get("PROJECT_ID") else "UNKNOWN")
    )
    pname = row.get("PROJECT_NAME") or row.get("PNAME") or pkey
    tmpl_id = str(row.get("TEMPLATE_ID") or row.get("ID") or "")
    name = row.get("TEMPLATE_NAME") or row.get("NAME") or ""
    desc = row.get("DESCRIPTION") or ""
    body = row.get("BODY") or row.get("CONTENT") or ""

    # 1. Field Resolution
    fields_raw = str(
        row.get("FIELD_ID")
        or row.get("PREPOPULATION_FIELDS")
        or ""
    ).strip()
    if fields_raw in ("EMPTY_VALUE", "null", "None", "-"):
        fields_raw = ""

    field_name_raw = str(
        row.get("FIELD_NAME_RESOLVED")
        or row.get("CUSTOM_FIELD_NAME")
        or row.get("FIELD_NAME")
        or ""
    ).strip()
    if field_name_raw in ("EMPTY_VALUE", "null", "None", "-"):
        field_name_raw = ""

    field_display, field_list = resolve_field_display(fields_raw, field_name_raw, custom_field_map)
    has_field = bool((fields_raw or field_name_raw) and field_display)
    if not has_field:
        field_display = "미지정 (필드 없음)"
        field_list = []

    # 2. Issue Types Resolution
    it_raw = str(
        row.get("PREPOPULATION_TYPES")
        or row.get("ISSUE_TYPES_RAW")
        or row.get("ISSUE_TYPES_RESOLVED")
        or row.get("ISSUE_TYPES")
        or ""
    ).strip()
    if it_raw in ("EMPTY_VALUE", "null", "None", "-"):
        it_raw = ""

    it_display, it_list = resolve_issue_types(it_raw, issue_type_map)
    has_issue_type = bool(it_raw and it_display)
    if not has_issue_type:
        it_display = "미지정 (이슈 타입 없음)"
        it_list = []

    # 3. Autofill & Migration Target Detection
    autofill_val = str(row.get("AUTOFILL_ENABLED") or "").strip().upper()
    if autofill_val in ("YES", "Y", "TRUE", "1"):
        has_autofill = True
    elif autofill_val in ("NO", "N", "FALSE", "0"):
        has_autofill = False
    else:
        has_autofill = bool(has_field and has_issue_type)

    has_content = bool(body and body.strip())
    validation = validate_template_settings(row)
    if not has_autofill:
        validation['reasons'].append('Autofill 비활성 또는 대상 미지정')
    if not has_content:
        validation['reasons'].append('템플릿 본문 없음')
    is_migration_target = bool(has_field and has_issue_type and has_autofill and has_content
                               and validation['status'] == 'PASS')
    migration_status = 'TARGET' if is_migration_target else 'EXCLUDED_' + validation['status']
    if not is_migration_target and validation['status'] == 'PASS':
        migration_status = 'EXCLUDED_INACTIVE_OR_EMPTY'
    migration_status_label = '마이그레이션 대상' if is_migration_target else '제외: ' + '; '.join(validation['reasons'])

    autofill_display = (
        f"Issue Types [{it_display}], Fields [{field_display}]"
        if has_autofill
        else "None"
    )

    vis_display = ' / '.join(f"{name}: {validation['raw'][key] if validation['present'][key] else '설정 누락'}"
                             for name, key in [('Fields', 'visibility_fields'), ('Types', 'visibility_types'), ('Groups', 'visibility_groups')])

    url = (
        f"{base_jira_url}/plugins/servlet/jeditor-project-config/{pkey}/templates"
        if base_jira_url
        else "#"
    )

    return {
        "environment": env_name.lower(),
        "project": pkey,
        "project_name": pname,
        "template_id": tmpl_id,
        "name": name,
        "description": desc,
        "autofill": autofill_display,
        "fields_str": field_display,
        "field_name": field_name_raw or field_display,
        "fields": field_list,
        "has_field": has_field,
        "issue_types_str": it_display,
        "issue_types_raw": it_raw,
        "issue_types": it_list,
        "has_issue_type": has_issue_type,
        "has_content": has_content,
        "settings_validation": validation,
        "is_migration_target": is_migration_target,
        "migration_status": migration_status,
        "migration_status_label": migration_status_label,
        "visibility": vis_display,
        "code": body,
        "cleaned_content": clean_template_content_html(body),
        "has_autofill": has_autofill,
        "autofill_status": "AUTO_FILLED" if has_autofill else "NOT_CHECKED",
        "verification_msg": f"{source_name} 추출 / {validation['status']}: " + "; ".join(validation["reasons"]),
        "url": url,
    }


def fetch_templates_from_db(
    db_config: dict[str, str | int],
    target_projects: list[str] | None = None,
    env_name: str = "ops",
    issue_type_map: dict[str, str] | None = None,
    custom_field_map: dict[str, str] | None = None,
) -> list[dict]:
    """Directly fetch templates from MySQL AO_BF4748_PROJECT_TEMPLATE table."""
    if pymysql is None:
        raise RuntimeError("pymysql 패키지가 설치되어 있지 않습니다. pip install pymysql")

    print(f"[DB] {db_config['host']}:{db_config['port']}/{db_config['database']} 에 연결 중...")
    conn = pymysql.connect(
        host=db_config["host"],
        port=db_config["port"],
        user=db_config["user"],
        password=db_config["password"],
        database=db_config["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=15,
    )

    base_jira_url = (
        os.getenv(f"JIRA_DC_{env_name.upper()}_URL")
        or os.getenv(f"{env_name.upper()}_JIRA_URL")
        or ""
    ).rstrip("/")

    if issue_type_map is None:
        issue_type_map = dict(DEFAULT_ISSUE_TYPES)
    if custom_field_map is None:
        custom_field_map = dict(DEFAULT_CUSTOM_FIELDS)

    # 1. Fetch live issue types from issuetype table to enrich mapping
    try:
        with conn.cursor() as it_cursor:
            it_cursor.execute("SELECT id, pname FROM issuetype;")
            it_count = 0
            for r in it_cursor.fetchall():
                i_id = str(r.get("id") or r.get("ID") or "").strip()
                i_name = str(r.get("pname") or r.get("PNAME") or "").strip()
                if i_id and i_name:
                    issue_type_map[i_id] = i_name
                    it_count += 1
            if it_count > 0:
                cache_file = SCRIPT_DIR / "jira_issue_types_cache.json"
                cache_file.write_text(json.dumps(issue_type_map, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[DB] issuetype 테이블에서 {it_count}개 이슈 타입 맵핑을 동기화했습니다.")
    except Exception as e:
        print(f"[DB] issuetype 테이블 조회 건너뜀: {e}")

    # 2. Fetch live custom fields from customfield table to enrich mapping
    try:
        with conn.cursor() as cf_cursor:
            cf_cursor.execute("SELECT id, cfname FROM customfield;")
            cf_count = 0
            for r in cf_cursor.fetchall():
                c_id = str(r.get("id") or r.get("ID") or "").strip()
                c_name = str(r.get("cfname") or r.get("CFNAME") or "").strip()
                if c_id and c_name:
                    custom_field_map[f"customfield_{c_id}"] = c_name
                    custom_field_map[c_id] = c_name
                    cf_count += 1
            if cf_count > 0:
                cf_cache_file = SCRIPT_DIR / "jira_custom_fields_cache.json"
                cf_cache_file.write_text(json.dumps(custom_field_map, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[DB] customfield 테이블에서 {cf_count}개 커스텀 필드 맵핑을 동기화했습니다.")
    except Exception as e:
        print(f"[DB] customfield 테이블 조회 건너뜀: {e}")

    records: list[dict] = []
    try:
        where_clause = ""
        params: list[str] = []
        if target_projects:
            placeholders = ",".join(["%s"] * len(target_projects))
            where_clause = f"WHERE p.pkey IN ({placeholders})"
            params = target_projects

        sql = f"""
        SELECT
            p.pkey AS project_key,
            p.pname AS project_name,
            pt.ID AS template_id,
            pt.NAME AS template_name,
            COALESCE(pt.DESCRIPTION, '') AS description,
            COALESCE(pt.BODY, '') AS content,
            COALESCE(pt.PREPOPULATION_FIELDS, '') AS fields_raw,
            COALESCE(pt.PREPOPULATION_TYPES, '') AS issue_types_raw,
            COALESCE(pt.SHARED, 0) AS is_shared,
            COALESCE(pt.VISIBILITY_FIELDS, '') AS visibility_fields,
            COALESCE(pt.VISIBILITY_TYPES, '') AS visibility_types,
            COALESCE(pt.VISIBILITY_USER_GROUPS, '') AS visibility_user_groups,
            cf.cfname AS custom_field_name,
            CASE
                WHEN pt.PREPOPULATION_FIELDS IS NULL OR pt.PREPOPULATION_FIELDS IN ('', 'EMPTY_VALUE') THEN ''
                WHEN pt.PREPOPULATION_FIELDS = 'description' THEN 'Description'
                WHEN pt.PREPOPULATION_FIELDS = 'environment' THEN 'Environment'
                WHEN cf.cfname IS NOT NULL THEN CONCAT(pt.PREPOPULATION_FIELDS, '(', cf.cfname, ')')
                ELSE COALESCE(pt.PREPOPULATION_FIELDS, '')
            END AS field_name_resolved,
            CASE
                WHEN pt.PREPOPULATION_TYPES IS NULL OR pt.PREPOPULATION_TYPES IN ('', 'EMPTY_VALUE') THEN ''
                ELSE COALESCE(
                    (
                        SELECT GROUP_CONCAT(CONCAT(it.id, '(', it.pname, ')') ORDER BY it.id SEPARATOR ', ')
                        FROM issuetype it
                        WHERE FIND_IN_SET(it.id, REPLACE(pt.PREPOPULATION_TYPES, ' ', '')) > 0
                    ),
                    pt.PREPOPULATION_TYPES
                )
            END AS issue_types_resolved
        FROM ao_bf4748_project_template pt
        JOIN project p ON p.id = pt.PROJECT_ID
        LEFT JOIN customfield cf ON pt.PREPOPULATION_FIELDS = CONCAT('customfield_', cf.id)
        {where_clause}
        ORDER BY p.pkey, pt.ID;
        """

        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            print(f"[DB] 총 {len(rows)}개의 템플릿 행(row)을 조회했습니다.")

            for r in rows:
                records.append(
                    build_template_record(
                        {
                            "PROJECT_KEY": r["project_key"],
                            "PROJECT_NAME": r["project_name"],
                            "TEMPLATE_ID": r["template_id"],
                            "TEMPLATE_NAME": r["template_name"],
                            "DESCRIPTION": r["description"],
                            "BODY": r["content"],
                            "PREPOPULATION_FIELDS": r["fields_raw"],
                            "CUSTOM_FIELD_NAME": r["custom_field_name"],
                            "FIELD_NAME_RESOLVED": r.get("field_name_resolved"),
                            "PREPOPULATION_TYPES": r["issue_types_raw"],
                            "ISSUE_TYPES_RESOLVED": r.get("issue_types_resolved"),
                            "VISIBILITY_FIELDS": r["visibility_fields"],
                            "VISIBILITY_TYPES": r["visibility_types"],
                            "VISIBILITY_USER_GROUPS": r["visibility_user_groups"],
                        },
                        env_name=env_name,
                        base_jira_url=base_jira_url,
                        source_name="AO_BF4748_PROJECT_TEMPLATE",
                        issue_type_map=issue_type_map,
                        custom_field_map=custom_field_map,
                    )
                )

    finally:
        conn.close()

    attach_content_and_adf(records)
    return records


def parse_html_table_dump(
    html_path: str | Path,
    env_name: str = "ops",
    issue_type_map: dict[str, str] | None = None,
    custom_field_map: dict[str, str] | None = None,
) -> list[dict]:
    """Parse template records from an HTML table dump (e.g. from step2_query_runner.py or DBeaver export).
    Supports BeautifulSoup with resilient regex fallback when bs4 is not installed.
    """
    p = Path(html_path)
    if not p.exists():
        raise FileNotFoundError(f"HTML 파일을 찾을 수 없습니다: {html_path}")

    raw_html = p.read_text(encoding="utf-8", errors="replace")
    base_jira_url = (
        os.getenv(f"JIRA_DC_{env_name.upper()}_URL")
        or os.getenv(f"{env_name.upper()}_JIRA_URL")
        or ""
    ).rstrip("/")

    token = (
        os.getenv(f"JIRA_DC_{env_name.upper()}_API_TOKEN")
        or os.getenv(f"{env_name.upper()}_JIRA_PASSWORD")
        or os.getenv(f"JIRA_DC_{env_name.upper()}_TOKEN")
        or ""
    )
    if issue_type_map is None:
        issue_type_map = load_issue_type_mappings(base_jira_url=base_jira_url, token=token)
    if custom_field_map is None:
        custom_field_map = load_custom_field_mappings(base_jira_url=base_jira_url, token=token)

    records = []

    # 1. BeautifulSoup parsing if available
    if BeautifulSoup:
        soup = BeautifulSoup(raw_html, "html.parser")
        headers = []
        for th in soup.find_all("th"):
            txt = th.get_text(strip=True).upper()
            if txt and not th.find("pre") and not th.find("code"):
                headers.append(txt)

        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            row = {}
            for i in range(min(len(headers), len(tds))):
                col_name = headers[i]
                if col_name in ("BODY", "CONTENT"):
                    content_val = tds[i].decode_contents().strip()
                    content_val = re.sub(r"<br\s*/?>\n?", "\n", content_val)
                    content_val = html.unescape(content_val)
                    row[col_name] = content_val
                else:
                    row[col_name] = tds[i].get_text(strip=True)

            if row:
                records.append(
                    build_template_record(
                        row,
                        env_name,
                        base_jira_url,
                        p.name,
                        issue_type_map=issue_type_map,
                        custom_field_map=custom_field_map,
                    )
                )
    else:
        # 2. Resilient standard library regex fallback (Zero external dependency)
        th_matches = re.findall(r"<th[^>]*>(.*?)</th>", raw_html, re.DOTALL | re.IGNORECASE)
        headers = [
            re.sub(r"<[^>]+>", "", th).strip().upper()
            for th in th_matches
            if th.strip() and "<pre" not in th.lower()
        ]

        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", raw_html, re.DOTALL | re.IGNORECASE):
            td_matches = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL | re.IGNORECASE)
            if not td_matches:
                continue
            row = {}
            for i in range(min(len(headers), len(td_matches))):
                col_name = headers[i]
                td_raw = td_matches[i]
                if col_name in ("BODY", "CONTENT"):
                    val = re.sub(r"<br\s*/?>\n?", "\n", td_raw)
                    val = html.unescape(val.strip())
                    row[col_name] = val
                else:
                    clean_txt = re.sub(r"<[^>]+>", "", td_raw)
                    clean_txt = html.unescape(clean_txt).strip()
                    row[col_name] = clean_txt
            if row:
                records.append(
                    build_template_record(
                        row,
                        env_name,
                        base_jira_url,
                        p.name,
                        issue_type_map=issue_type_map,
                        custom_field_map=custom_field_map,
                    )
                )

    attach_content_and_adf(records)
    return records


# ==============================================================================
# Confluence-Friendly Table Content Generation (8-Column Layout)
# Columns: # | 프로젝트 | 템플릿명 | 설명 | 적용 필드 | 적용 이슈 유형 | 템플릿 내용 | Jira 설정
# ==============================================================================

def generate_confluence_table_html(
    records: list[dict],
    title: str = "JEditor 템플릿 현황 리포트",
) -> str:
    """Generate a clean, self-contained HTML table specifically designed for copying and pasting
    directly into Confluence page editors (Atlassian Fabric and Classic editor).
    Excluded columns: 환경, 템플릿 ID, 공개 범위, Auto-fill.
    Included at former Auto-fill position: 템플릿 내용 (rendered template content).
    """
    rows = []
    for idx, r in enumerate(records, 1):
        pkey = html.escape(r.get("project") or "")
        pname = html.escape(r.get("project_name") or "")
        pname_sub = (
            f"<br/><span style=\"color:#6b778c;font-size:11px;\">{pname}</span>"
            if pname and pname != pkey
            else ""
        )

        tmpl_name = html.escape(r.get("name") or "")
        desc = html.escape(r.get("description") or "-")
        field = html.escape(r.get("fields_str") or "미지정 (필드 없음)")
        has_field = r.get("has_field", True)
        if has_field:
            field_badge = f'<span style="background-color:#deebff;color:#0747a6;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">{field}</span>'
        else:
            field_badge = f'<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">{field}</span>'

        itypes = html.escape(r.get("issue_types_str") or "미지정 (이슈 타입 없음)")
        has_itype = r.get("has_issue_type", True)
        if has_itype:
            itypes_badge = f'<span style="font-family:monospace;font-size:12px;color:#172b4d;">{itypes}</span>'
        else:
            itypes_badge = f'<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">{itypes}</span>'

        content_html = r.get("cleaned_content") or clean_template_content_html(r.get("code", ""))

        url = r.get("url") or "#"
        link_html = (
            f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" style="color:#0052cc;text-decoration:none;font-weight:bold;">설정 열기 ↗</a>'
            if url and url != "#"
            else "-"
        )

        rows.append(f"""    <tr style="border-bottom:1px solid #dfe1e6;">
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;font-family:monospace;color:#6b778c;">{idx}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;"><strong>{pkey}</strong>{pname_sub}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;font-weight:bold;color:#172b4d;">{tmpl_name}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;color:#172b4d;">{desc}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">{field_badge}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">{itypes_badge}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;vertical-align:top;color:#172b4d;">
        <div style="line-height:1.45;font-size:12.5px;">
          {content_html}
        </div>
      </td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;">{link_html}</td>
    </tr>""")

    tbody_content = "\n".join(rows) if rows else """<tr><td colspan="8" style="text-align:center;padding:24px;color:#6b778c;">조회된 JEditor 템플릿이 없습니다.</td></tr>"""

    return f"""<table class="confluenceTable" border="1" style="border-collapse:collapse;width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;color:#172b4d;border:1px solid #dfe1e6;">
  <thead>
    <tr style="background-color:#f4f5f7;border-bottom:2px solid #dfe1e6;text-align:left;">
      <th style="border:1px solid #dfe1e6;padding:10px 8px;text-align:center;width:40px;background-color:#f4f5f7;font-weight:bold;">#</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:140px;background-color:#f4f5f7;font-weight:bold;">프로젝트</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:180px;background-color:#f4f5f7;font-weight:bold;">템플릿명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:150px;background-color:#f4f5f7;font-weight:bold;">설명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:130px;background-color:#f4f5f7;font-weight:bold;">적용 필드</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:110px;background-color:#f4f5f7;font-weight:bold;">적용 이슈 유형</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:260px;background-color:#f4f5f7;font-weight:bold;">템플릿 내용</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:center;width:90px;background-color:#f4f5f7;font-weight:bold;">Jira 설정</th>
    </tr>
  </thead>
  <tbody>
{tbody_content}
  </tbody>
</table>"""


def generate_confluence_table_markdown(records: list[dict]) -> str:
    """Generate a clean GFM / Confluence-compatible Markdown table."""
    lines = [
        "| # | 프로젝트 | 템플릿명 | 설명 | 적용 필드 | 적용 이슈 유형 | 템플릿 내용 | Jira 설정 |",
        "|:---:|:---|:---|:---|:---|:---|:---|:---:|",
    ]
    for idx, r in enumerate(records, 1):
        pkey = r.get("project") or ""
        pname = r.get("project_name") or ""
        proj_cell = f"{pkey} ({pname})" if pname and pname != pkey else pkey

        tmpl_name = (r.get("name") or "").replace("|", "\\|")
        desc = (r.get("description") or "-").replace("|", "\\|").replace("\n", " ")
        field = (r.get("fields_str") or "미지정 (필드 없음)").replace("|", "\\|")
        if not r.get("has_field", True):
            field = f"⚠️ {field}"

        itypes = (r.get("issue_types_str") or "미지정 (이슈 타입 없음)").replace("|", "\\|")
        if not r.get("has_issue_type", True):
            itypes = f"⚠️ {itypes}"

        content = r.get("cleaned_content") or clean_template_content_html(r.get("code", ""))
        content_cell = content.replace("|", "\\|").replace("\r\n", "<br/>").replace("\n", "<br/>")

        url = r.get("url") or "#"
        link_md = f"[설정 열기]({url})" if url and url != "#" else "-"

        lines.append(
            f"| {idx} | {proj_cell} | {tmpl_name} | {desc} | {field} | {itypes} | {content_cell} | {link_md} |"
        )
    return "\n".join(lines)


def write_confluence_report(
    records: list[dict],
    output_path: Path = DEFAULT_CONFLUENCE_REPORT,
) -> None:
    """Writes a dedicated, clean standalone HTML page with the Confluence-ready table.
    Opening this file in any browser allows 1-click clipboard copying of the full or filtered table to Confluence.
    """
    total_templates = len(records)
    target_records = [r for r in records if r.get("is_migration_target", False)]
    excluded_records = [r for r in records if not r.get("is_migration_target", False)]
    target_count = len(target_records)
    excluded_count = len(excluded_records)

    unique_projects = sorted(list({r.get("project") for r in records if r.get("project")}))
    projects_count = len(unique_projects)

    project_options = ['<option value="">모든 프로젝트 (전체)</option>']
    for p in unique_projects:
        project_options.append(f'<option value="{html.escape(p)}">{html.escape(p)}</option>')
    project_options_html = "\n        ".join(project_options)

    table_html = generate_confluence_table_html(records)
    json_records_str = json.dumps(records, ensure_ascii=False)

    page_html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>JEditor 템플릿 현황 리포트 (Confluence 복사용)</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: #f4f5f7;
      color: #172b4d;
      margin: 0;
      padding: 32px 40px;
    }}
    .container {{
      max-width: 1400px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #dfe1e6;
      border-radius: 8px;
      box-shadow: 0 1px 3px rgba(9, 30, 66, 0.08);
      padding: 28px 36px;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2px solid #ebecf0;
      padding-bottom: 18px;
      margin-bottom: 20px;
    }}
    .header h1 {{
      font-size: 22px;
      margin: 0 0 6px 0;
      color: #172b4d;
    }}
    .header p {{
      margin: 0;
      font-size: 13px;
      color: #6b778c;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      align-items: center;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      font-size: 13px;
      font-weight: 600;
      border-radius: 4px;
      border: 1px solid #dfe1e6;
      background: #ffffff;
      color: #172b4d;
      cursor: pointer;
      transition: all 0.15s ease;
    }}
    .btn:hover {{ background: #ebecf0; }}
    .btn-primary {{
      background: #0052cc;
      color: #ffffff;
      border-color: #0052cc;
    }}
    .btn-primary:hover {{ background: #0747a6; }}
    .guide-banner {{
      background: #deebff;
      border-left: 4px solid #0052cc;
      padding: 12px 16px;
      border-radius: 4px;
      margin-bottom: 20px;
      font-size: 13px;
      line-height: 1.5;
    }}
    .guide-banner strong {{ color: #0747a6; }}
    .filter-card {{
      background: #f4f5f7;
      border: 1px solid #dfe1e6;
      border-radius: 6px;
      padding: 12px 16px;
      margin-bottom: 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }}
    .search-box {{
      flex: 1 1 220px;
    }}
    .search-box input {{
      width: 100%;
      padding: 8px 12px;
      font-size: 13px;
      border: 1px solid #dfe1e6;
      border-radius: 4px;
      box-sizing: border-box;
      outline: none;
      background: #ffffff;
    }}
    .search-box input:focus {{
      border-color: #0052cc;
    }}
    .filter-select {{
      padding: 7px 12px;
      font-size: 13px;
      border: 1px solid #dfe1e6;
      border-radius: 4px;
      background: #ffffff;
      cursor: pointer;
      color: #172b4d;
      outline: none;
    }}
    .filter-select:focus {{
      border-color: #0052cc;
    }}
    .stats-info {{
      font-size: 13px;
      color: #42526e;
    }}
    .stats-info strong {{
      color: #0052cc;
    }}
    .filter-btns {{
      display: flex;
      gap: 8px;
      margin-left: auto;
    }}
    .filter-chips-row {{
      display: flex;
      gap: 8px;
      margin-bottom: 16px;
      align-items: center;
    }}
    .chip-btn {{
      padding: 5px 12px;
      font-size: 12px;
      font-weight: 600;
      border-radius: 14px;
      border: 1px solid #dfe1e6;
      background: #ffffff;
      color: #42526e;
      cursor: pointer;
      transition: all 0.15s;
    }}
    .chip-btn:hover {{ background: #ebecf0; }}
    .chip-btn.active {{
      background: #0052cc;
      color: #ffffff;
      border-color: #0052cc;
    }}
    .chip-btn.chip-target.active {{
      background: #00875a;
      color: #ffffff;
      border-color: #00875a;
    }}
    .chip-btn.chip-excluded.active {{
      background: #de350b;
      color: #ffffff;
      border-color: #de350b;
    }}
    .table-wrapper {{
      overflow-x: auto;
      margin-top: 10px;
      border: 1px solid #dfe1e6;
      border-radius: 4px;
    }}
    .toast {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #172b4d;
      color: #ffffff;
      padding: 12px 20px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 4px 12px rgba(9, 30, 66, 0.25);
      z-index: 1000;
      opacity: 0;
      transform: translateY(10px);
      transition: all 0.25s ease;
      pointer-events: none;
    }}
    .toast.show {{
      opacity: 1;
      transform: translateY(0);
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <h1>📋 JEditor 템플릿 현황 리포트 (Confluence 복사용)</h1>
        <p>생성 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &middot; Jira Active Objects (AO_BF4748_PROJECT_TEMPLATE) 기반</p>
      </div>
      <div class="actions">
        <a href="template_report.html" class="btn" style="text-decoration:none;">
          📊 인터랙티브 대시보드 ↗
        </a>
      </div>
    </div>

    <div class="guide-banner">
      💡 <strong>Confluence 표 전체 복사 및 붙여넣기 팁:</strong><br>
      하단의 <strong>[🎯 마이그레이션 대상만 ({target_count}건)]</strong>을 선택한 후 <strong>[📋 필터 결과 표 전체 복사]</strong> 버튼을 누르고 Confluence 편집 창에서 <strong>붙여넣기(Ctrl+V / Cmd+V)</strong>하면 <strong>이슈 타입 및 필드가 정상 설정된 마이그레이션 대상 표만</strong> 즉시 들어갑니다.
    </div>

    <div class="filter-chips-row">
      <span style="font-size:12px;font-weight:bold;color:#6b778c;">빠른 필터:</span>
      <button type="button" class="chip-btn active" data-filter="all" onclick="setTargetFilter('all', this)">
        📋 전체 ({total_templates})
      </button>
      <button type="button" class="chip-btn chip-target" data-filter="target" onclick="setTargetFilter('target', this)">
        🎯 마이그레이션 대상만 ({target_count})
      </button>
      <button type="button" class="chip-btn chip-excluded" data-filter="excluded" onclick="setTargetFilter('excluded', this)">
        ⚠️ 미설정/제외 대상만 ({excluded_count})
      </button>
    </div>

    <div class="filter-card">
      <div class="search-box">
        <input type="text" id="searchInput" placeholder="프로젝트, 템플릿명, 필드명, 키워드 검색..." oninput="applyFilters()" />
      </div>
      <select id="targetFilter" class="filter-select font-bold" onchange="applyFilters()">
        <option value="all">📋 전체 템플릿 ({total_templates}건)</option>
        <option value="target">🎯 마이그레이션 대상만 ({target_count}건)</option>
        <option value="excluded">⚠️ 미설정/제외 대상만 ({excluded_count}건)</option>
        <option value="missing_field">🚫 필드 미지정 건만</option>
        <option value="missing_itype">🚫 이슈 타입 미지정 건만</option>
      </select>
      <select id="projectFilter" class="filter-select" onchange="applyFilters()">
        {project_options_html}
      </select>
      <div class="stats-info">
        표시: <strong id="visibleCount">{total_templates}</strong> / {total_templates} 건
      </div>
      <div class="filter-btns">
        <button type="button" class="btn btn-primary" id="btnCopyFiltered" onclick="copyConfluenceTable()">
          📋 필터 결과 표 전체 복사 (<span id="btnCopyCount">{total_templates}</span>건)
        </button>
        <button type="button" class="btn" onclick="copyMarkdownTable()">
          📝 Markdown 표 복사
        </button>
      </div>
    </div>

    <div class="table-wrapper" id="tableContainer">
      {table_html}
    </div>
  </div>

  <div id="toast" class="toast"></div>

  <script>
    const TEMPLATES_DATA = {json_records_str};

    function showToast(message) {{
      const t = document.getElementById('toast');
      t.textContent = message;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2800);
    }}

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
    }}

    function getVisibleTemplates() {{
      const search = (document.getElementById('searchInput')?.value || '').toLowerCase().trim();
      const proj = (document.getElementById('projectFilter')?.value || '').toLowerCase().trim();
      const target = (document.getElementById('targetFilter')?.value || 'all');

      return TEMPLATES_DATA.filter(t => {{
        const text = `${{t.project}} ${{t.project_name || ''}} ${{t.name}} ${{t.description || ''}} ${{t.fields_str || ''}} ${{t.issue_types_str || ''}} ${{t.code || ''}}`.toLowerCase();
        const matchSearch = !search || text.includes(search);
        const matchProj = !proj || (t.project || '').toLowerCase() === proj;

        let matchTarget = true;
        if (target === 'target') matchTarget = t.is_migration_target;
        else if (target === 'excluded') matchTarget = !t.is_migration_target;
        else if (target === 'missing_field') matchTarget = !t.has_field;
        else if (target === 'missing_itype') matchTarget = !t.has_issue_type;

        return matchSearch && matchProj && matchTarget;
      }});
    }}

    function generateConfluenceHtml(records) {{
      let rows = records.map((r, idx) => {{
        const pkey = r.project || '';
        const pname = r.project_name || '';
        const pnameSub = (pname && pname !== pkey) ? `<br/><span style="color:#6b778c;font-size:11px;">${{escapeHtml(pname)}}</span>` : '';
        const desc = r.description ? escapeHtml(r.description) : '-';
        const linkHtml = (r.url && r.url !== '#')
          ? `<a href="${{escapeHtml(r.url)}}" target="_blank" rel="noopener noreferrer" style="color:#0052cc;text-decoration:none;font-weight:bold;">설정 열기 ↗</a>`
          : '-';
        const contentHtml = r.cleaned_content || '(내용 없음)';

        const fieldHtml = r.has_field
          ? `<span style="background-color:#deebff;color:#0747a6;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.fields_str || 'Description')}}</span>`
          : `<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.fields_str || '미지정 (필드 없음)')}}</span>`;

        const itypesHtml = r.has_issue_type
          ? `<span style="font-family:monospace;font-size:12px;color:#172b4d;">${{escapeHtml(r.issue_types_str || 'all')}}</span>`
          : `<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.issue_types_str || '미지정 (이슈 타입 없음)')}}</span>`;

        return `    <tr style="border-bottom:1px solid #dfe1e6;">
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;font-family:monospace;color:#6b778c;">${{idx + 1}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;"><strong>${{escapeHtml(pkey)}}</strong>${{pnameSub}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;font-weight:bold;color:#172b4d;">${{escapeHtml(r.name)}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;color:#172b4d;">${{desc}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">${{fieldHtml}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">${{itypesHtml}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;vertical-align:top;color:#172b4d;">
        <div style="line-height:1.45;font-size:12.5px;">
          ${{contentHtml}}
        </div>
      </td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;">${{linkHtml}}</td>
    </tr>`;
      }}).join('\\n');

      return `<table class="confluenceTable" border="1" style="border-collapse:collapse;width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;color:#172b4d;border:1px solid #dfe1e6;">
  <thead>
    <tr style="background-color:#f4f5f7;border-bottom:2px solid #dfe1e6;text-align:left;">
      <th style="border:1px solid #dfe1e6;padding:10px 8px;text-align:center;width:40px;background-color:#f4f5f7;font-weight:bold;">#</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:140px;background-color:#f4f5f7;font-weight:bold;">프로젝트</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:180px;background-color:#f4f5f7;font-weight:bold;">템플릿명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:150px;background-color:#f4f5f7;font-weight:bold;">설명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:130px;background-color:#f4f5f7;font-weight:bold;">적용 필드</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:110px;background-color:#f4f5f7;font-weight:bold;">적용 이슈 유형</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:260px;background-color:#f4f5f7;font-weight:bold;">템플릿 내용</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:center;width:90px;background-color:#f4f5f7;font-weight:bold;">Jira 설정</th>
    </tr>
  </thead>
  <tbody>
${{rows || '<tr><td colspan="8" style="text-align:center;padding:24px;color:#6b778c;">필터 조건에 맞는 JEditor 템플릿이 없습니다.</td></tr>'}}
  </tbody>
</table>`;
    }}

    function generateConfluenceMarkdown(records) {{
      const lines = [
        '| # | 프로젝트 | 템플릿명 | 설명 | 적용 필드 | 적용 이슈 유형 | 템플릿 내용 | Jira 설정 |',
        '|:---:|:---|:---|:---|:---|:---|:---|:---:|'
      ];
      records.forEach((r, idx) => {{
        const pkey = r.project || '';
        const pname = r.project_name || '';
        const projCell = (pname && pname !== pkey) ? `${{pkey}} (${{pname}})` : pkey;
        const desc = (r.description || '-').replace(/\\|/g, '\\\\|').replace(/\\n/g, ' ');
        const name = (r.name || '').replace(/\\|/g, '\\\\|');
        const field = (r.has_field ? (r.fields_str || 'Description') : `⚠️ ${{r.fields_str || '미지정'}}`).replace(/\\|/g, '\\\\|');
        const itypes = (r.has_issue_type ? (r.issue_types_str || 'all') : `⚠️ ${{r.issue_types_str || '미지정'}}`).replace(/\\|/g, '\\\\|');
        const link = (r.url && r.url !== '#') ? `[설정 열기](${{r.url}})` : '-';
        const content = (r.cleaned_content || '(내용 없음)').replace(/\\|/g, '\\\\|').replace(/\\r?\\n/g, '<br/>');
        lines.push(`| ${{idx + 1}} | ${{projCell}} | ${{name}} | ${{desc}} | ${{field}} | ${{itypes}} | ${{content}} | ${{link}} |`);
      }});
      return lines.join('\\n');
    }}

    function applyFilters() {{
      const records = getVisibleTemplates();
      document.getElementById('visibleCount').textContent = records.length;
      document.getElementById('btnCopyCount').textContent = records.length;
      const btn = document.getElementById('btnCopyFiltered');
      if (btn) {{
        btn.disabled = (records.length === 0);
        btn.style.opacity = (records.length === 0) ? '0.5' : '1';
      }}
      document.getElementById('tableContainer').innerHTML = generateConfluenceHtml(records);

      const targetVal = document.getElementById('targetFilter')?.value || 'all';
      document.querySelectorAll('.chip-btn').forEach(c => {{
        c.classList.toggle('active', c.getAttribute('data-filter') === targetVal);
      }});
    }}

    function setTargetFilter(type, btn) {{
      const sel = document.getElementById('targetFilter');
      if (sel) sel.value = type;
      applyFilters();
    }}

    async function copyConfluenceTable() {{
      const records = getVisibleTemplates();
      if (!records.length) {{
        showToast('⚠️ 복사할 템플릿이 없습니다. (필터 조건에 맞는 행 없음)');
        return;
      }}
      const tableHtml = generateConfluenceHtml(records);
      const tableMd = generateConfluenceMarkdown(records);
      try {{
        if (navigator.clipboard && window.ClipboardItem) {{
          const blobHtml = new Blob([tableHtml], {{ type: 'text/html' }});
          const blobText = new Blob([tableMd], {{ type: 'text/plain' }});
          await navigator.clipboard.write([
            new ClipboardItem({{ 'text/html': blobHtml, 'text/plain': blobText }})
          ]);
          showToast(`✅ 필터 결과 ${{records.length}}개 템플릿의 전체 표가 복사되었습니다! Confluence 편집창에서 붙여넣기(Ctrl+V)하세요.`);
          return;
        }}
      }} catch (_) {{}}

      // Fallback
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = tableHtml;
      tempDiv.style.position = 'fixed';
      tempDiv.style.left = '-9999px';
      document.body.appendChild(tempDiv);
      const sel = window.getSelection();
      sel.removeAllRanges();
      const range = document.createRange();
      range.selectNodeContents(tempDiv);
      sel.addRange(range);
      try {{
        document.execCommand('copy');
        showToast(`✅ 필터 결과 ${{records.length}}개 템플릿의 전체 표가 복사되었습니다!`);
      }} catch (_) {{
        showToast('❌ 복사 실패');
      }}
      sel.removeAllRanges();
      document.body.removeChild(tempDiv);
    }}

    async function copyMarkdownTable() {{
      const records = getVisibleTemplates();
      if (!records.length) {{
        showToast('⚠️ 복사할 템플릿이 없습니다.');
        return;
      }}
      const tableMd = generateConfluenceMarkdown(records);
      try {{
        await navigator.clipboard.writeText(tableMd);
        showToast(`✅ 필터 결과 ${{records.length}}개 템플릿의 Markdown 표가 복사되었습니다.`);
      }} catch (_) {{
        showToast('❌ 복사 실패');
      }}
    }}
  </script>
</body>
</html>
"""
    output_path.write_text(page_html, encoding="utf-8")
    print(f"📄 Confluence 전용 복사 리포트 생성 완료: {output_path.resolve()}")


# ==============================================================================
# Interactive Dashboard Report Generation
# ==============================================================================

def write_template_report(
    records: list[dict],
    report_path: Path = DEFAULT_OUTPUT_REPORT,
    filter_autofill: bool = False,
    confluence_path: Path | None = None,
) -> None:
    """Generate the rich interactive dashboard HTML report with direct Confluence copy tools."""
    attach_content_and_adf(records)

    if filter_autofill:
        filtered_records = [r for r in records if r.get("has_autofill", False)]
    else:
        filtered_records = records

    total_candidates = len(filtered_records)
    projects_set = {r["project"] for r in filtered_records}
    total_projects = len(projects_set)

    target_records = [r for r in filtered_records if r.get("is_migration_target", False)]
    excluded_records = [r for r in filtered_records if not r.get("is_migration_target", False)]
    target_count = len(target_records)
    excluded_count = len(excluded_records)

    def esc(value: str | None) -> str:
        return html.escape(str(value or ""), quote=True)

    unique_projects = sorted(list({r.get("project") for r in filtered_records if r.get("project")}))
    project_options = ['<option value="">모든 프로젝트 (전체)</option>']
    for p in unique_projects:
        project_options.append(f'<option value="{esc(p)}">{esc(p)}</option>')
    project_options_html = "\n            ".join(project_options)

    rows_html = []
    for idx, rec in enumerate(filtered_records, 1):
        fields_str = rec.get("fields_str", "") or ", ".join(rec.get("fields", [])) or "미지정 (필드 없음)"
        it_str = rec.get("issue_types_str", "") or ", ".join(rec.get("issue_types", [])) or "미지정 (이슈 타입 없음)"
        cleaned_content = rec.get("cleaned_content") or clean_template_content_html(rec.get("code", ""))
        adf_json = rec.get("adf_json", "{}")

        has_field = rec.get("has_field", True)
        has_itype = rec.get("has_issue_type", True)
        is_target = rec.get("is_migration_target", False)

        if has_field:
            field_badge = f'<span class="badge badge-field font-bold">{esc(fields_str)}</span>'
        else:
            field_badge = f'<span class="badge badge-missing font-bold" title="적용 필드가 설정되어 있지 않습니다">⚠️ {esc(fields_str)}</span>'

        if has_itype:
            it_items = rec.get("issue_types") or [it_str]
            tags = [f'<span class="spec-tag">{esc(item)}</span>' for item in it_items if item]
            itype_badge = " ".join(tags) if tags else f'<span class="spec-tag">{esc(it_str)}</span>'
        else:
            itype_badge = f'<span class="badge badge-missing font-bold" title="적용 이슈 유형이 설정되어 있지 않습니다">⚠️ {esc(it_str)}</span>'

        target_badge = (
            '<span class="badge-target-pill">🎯 대상</span>'
            if is_target
            else f'<span class="badge-excluded-pill" title="{esc(rec.get("migration_status_label"))}">⚠️ 제외</span><div class="text-tiny text-muted">{esc(rec.get("migration_status_label"))}</div>'
        )

        pkey = esc(rec["project"])
        pname = esc(rec.get("project_name") or "")
        project_subtext = (
            f'<div class="text-tiny text-muted project-name">{pname}</div>'
            if pname and pname != pkey
            else ""
        )

        row = f"""
        <tr class="template-row" data-id="{esc(rec['template_id'])}" data-env="{esc(rec['environment'])}" data-project="{pkey}" data-target="{'1' if is_target else '0'}" data-has-field="{'1' if has_field else '0'}" data-has-itype="{'1' if has_itype else '0'}" data-status="{esc(rec.get('migration_status'))}">
            <td class="text-center font-mono text-muted">{idx}</td>
            <td>
                <strong class="font-bold text-primary">{pkey}</strong>
                {project_subtext}
            </td>
            <td>
                <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                    <strong class="template-name">{esc(rec['name'])}</strong>
                    {target_badge}
                </div>
            </td>
            <td>
                <div class="template-desc text-muted">{esc(rec['description'] or '-')}</div>
            </td>
            <td>
                {field_badge}
            </td>
            <td>
                {itype_badge}
            </td>
            <td class="content-cell" style="vertical-align: top;">
                <div class="content-cell-container">
                    <div class="content-cell-header no-confluence">
                        <span class="text-tiny text-muted font-mono">{len(rec.get('code', ''))} chars</span>
                        <button type="button" class="btn-copy-tiny" onclick="copyContentText('{esc(rec['template_id'])}', this)" title="템플릿 본문 내용 복사">Copy 내용</button>
                    </div>
                    <div class="template-content-preview">
                        {cleaned_content}
                    </div>
                </div>
            </td>
            <td class="text-center">
                <a href="{esc(rec['url'])}" target="_blank" rel="noopener noreferrer" class="btn-open-jira" title="Jira 템플릿 설정 화면 열기">
                    <span>열기 ↗</span>
                </a>
            </td>
            <td class="text-center no-confluence">
                <button type="button" class="btn-toggle-preview" onclick="togglePreview(this)" title="템플릿 원본 및 ADF 미리보기">
                    <span>Preview</span>
                    <svg class="chevron-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
                </button>
            </td>
            <td class="text-center no-confluence">
                <button type="button" class="btn-copy-row" onclick="copySingleRow('{esc(rec['template_id'])}')" title="이 템플릿 행을 Confluence용으로 복사">
                    📋
                </button>
            </td>
        </tr>
        <tr class="preview-row no-confluence" data-preview-for="{esc(rec['template_id'])}" data-open="false" style="display:none;">
            <td colspan="10">
                <div class="preview-expandable-container">
                    <div class="template-metadata-bar">
                        <span class="meta-item">🎯 <strong>Target Field:</strong> <span class="badge badge-field">{esc(fields_str)}</span></span>
                        <span class="meta-item">📋 <strong>Issue Types:</strong> {esc(it_str)}</span>
                        <span class="meta-item">⚡ <strong>Autofill:</strong> {esc(rec.get('autofill', 'None'))}</span>
                    </div>
                    <div class="code-preview-grid">
                        <section class="preview-panel">
                            <div class="panel-header">
                                <span>Original JEditor HTML ({len(rec.get('code', ''))} chars)</span>
                                <button type="button" class="btn-copy" onclick="copyPanelCode(this, '.raw-code')">Copy HTML</button>
                            </div>
                            <pre><code class="raw-code">{esc(rec.get('code', ''))}</code></pre>
                        </section>
                        <section class="preview-panel">
                            <div class="panel-header">
                                <span>Atlassian Document Format (ADF)</span>
                                <button type="button" class="btn-copy" onclick="copyAdfJson(this)">Copy ADF JSON</button>
                            </div>
                            <pre><code class="adf-json">{esc(adf_json)}</code></pre>
                        </section>
                        <section class="preview-panel">
                            <div class="panel-header">
                                <span>Rendered Preview</span>
                                <button type="button" class="btn-copy btn-copy-primary" onclick="copyTemplateBodyForConfluence('{esc(rec['template_id'])}')">📋 본문 HTML 복사</button>
                            </div>
                            <iframe class="preview-iframe" sandbox="" referrerpolicy="no-referrer" loading="lazy"></iframe>
                        </section>
                    </div>
                </div>
            </td>
        </tr>
        """
        rows_html.append(row)

    json_records_str = json.dumps(filtered_records, ensure_ascii=False)

    html_page = f"""<!doctype html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JEditor Template SQL Extraction Report</title>
    <style>
        :root {{
            --primary: #0052cc;
            --primary-light: #deebff;
            --primary-dark: #0747a6;
            --success: #36b37e;
            --success-bg: #e3fcef;
            --danger: #ff5630;
            --danger-bg: #ffebe6;
            --warning: #ffab00;
            --warning-bg: #fff0b3;
            --neutral: #6b778c;
            --neutral-bg: #ebecf0;
            --bg-page: #f4f5f7;
            --bg-surface: #ffffff;
            --border-color: #dfe1e6;
            --text-dark: #172b4d;
            --text-muted: #6b778c;
            --shadow-sm: 0 1px 2px rgba(9, 30, 66, 0.08);
            --shadow-md: 0 4px 12px rgba(9, 30, 66, 0.12);
            --shadow-lg: 0 12px 24px -6px rgba(9, 30, 66, 0.25);
            --radius-sm: 4px;
            --radius-md: 8px;
            --radius-lg: 12px;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-page);
            color: var(--text-dark);
            font-size: 13px;
            line-height: 1.5;
            padding: 24px 32px;
        }}

        .dashboard-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-surface);
            padding: 20px 24px;
            border-radius: var(--radius-lg);
            border: 1px solid var(--border-color);
            box-shadow: var(--shadow-sm);
            margin-bottom: 20px;
        }}
        .header-title-area h1 {{
            font-size: 20px;
            font-weight: 700;
            color: var(--text-dark);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .header-title-area p {{
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }}
        .header-actions {{
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .btn-action {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 7px 12px;
            font-size: 12px;
            font-weight: 600;
            border-radius: var(--radius-sm);
            border: 1px solid var(--border-color);
            background: var(--bg-surface);
            color: var(--text-dark);
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }}
        .btn-action:hover {{
            background: var(--neutral-bg);
            border-color: #c1c7d0;
        }}
        .btn-confluence {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
            box-shadow: 0 1px 2px rgba(0, 82, 204, 0.2);
        }}
        .btn-confluence:hover {{
            background: var(--primary-dark);
            border-color: var(--primary-dark);
            color: #ffffff;
        }}
        .btn-confluence-secondary {{
            background: var(--primary-light);
            color: var(--primary-dark);
            border-color: #b3d4ff;
        }}
        .btn-confluence-secondary:hover {{
            background: #cce0ff;
        }}

        .confluence-tip-banner {{
            background: #e6fcff;
            border: 1px solid #b3f5ff;
            border-left: 4px solid #00a3bf;
            padding: 10px 16px;
            border-radius: var(--radius-sm);
            margin-bottom: 18px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 12.5px;
            color: #0052cc;
        }}
        .confluence-tip-banner strong {{ color: #0049b0; }}
        .confluence-tip-banner .tip-links {{
            display: flex;
            gap: 10px;
        }}
        .confluence-tip-banner a {{
            color: var(--primary);
            font-weight: 600;
            text-decoration: underline;
            cursor: pointer;
        }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }}
        .kpi-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 16px 20px;
            box-shadow: var(--shadow-sm);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        .kpi-card.kpi-total {{ border-left: 4px solid var(--primary); }}
        .kpi-card.kpi-success {{ border-left: 4px solid var(--success); }}
        .kpi-card.kpi-warning {{ border-left: 4px solid var(--warning); }}

        .kpi-label {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .kpi-value {{
            font-size: 28px;
            font-weight: 700;
            color: var(--text-dark);
            margin: 6px 0 2px 0;
        }}
        .kpi-subtext {{
            font-size: 11px;
            color: var(--text-muted);
        }}

        .filter-bar {{
            background: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 12px 18px;
            box-shadow: var(--shadow-sm);
            margin-bottom: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            align-items: center;
        }}
        .search-box {{
            flex: 1 1 240px;
            position: relative;
        }}
        .search-box input {{
            width: 100%;
            padding: 8px 12px 8px 34px;
            font-size: 13px;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            background: var(--bg-surface);
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-light);
        }}
        .search-icon {{
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
        }}
        .filter-select {{
            padding: 7px 12px;
            font-size: 13px;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            background: var(--bg-surface);
            color: var(--text-dark);
            outline: none;
            cursor: pointer;
            transition: border-color 0.2s;
        }}
        .filter-select:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-light);
        }}
        .result-count {{
            font-size: 12.5px;
            color: var(--text-muted);
            font-weight: 500;
            padding: 0 4px;
        }}
        .filter-actions {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-left: auto;
        }}

        .table-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-sm);
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        thead th {{
            background: #fafbfc;
            border-bottom: 2px solid var(--border-color);
            padding: 12px 10px;
            font-size: 12px;
            font-weight: 700;
            color: var(--text-dark);
            text-transform: uppercase;
            letter-spacing: 0.4px;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        tbody td {{
            padding: 11px 10px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: middle;
        }}
        tbody tr.template-row:hover {{
            background-color: #f7f9fc;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }}
        .template-name {{
            font-size: 13px;
            color: var(--text-dark);
            display: block;
        }}
        .template-desc {{
            font-size: 11.5px;
            color: var(--text-muted);
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .spec-tag {{
            display: inline-block;
            background: #ebecf0;
            color: #42526e;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 11px;
            font-family: SFMono-Medium, Menlo, Consolas, monospace;
        }}
        .badge-field {{
            background: #deebff;
            color: #0747a6;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid #b3d4ff;
        }}
        .badge-missing {{
            background: #ffebe6;
            color: #de350b;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 11px;
            border: 1px dashed #ff8f73;
        }}
        .badge-target-pill {{
            display: inline-flex;
            align-items: center;
            padding: 1px 6px;
            font-size: 10px;
            font-weight: 700;
            border-radius: 10px;
            background: #e3fcef;
            color: #006644;
            border: 1px solid #abf5d1;
        }}
        .badge-excluded-pill {{
            display: inline-flex;
            align-items: center;
            padding: 1px 6px;
            font-size: 10px;
            font-weight: 700;
            border-radius: 10px;
            background: #ffebe6;
            color: #bf2600;
            border: 1px solid #ffbdad;
        }}
        .filter-chips-row {{
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
            align-items: center;
        }}
        .chip-btn {{
            padding: 5px 12px;
            font-size: 12px;
            font-weight: 600;
            border-radius: 14px;
            border: 1px solid var(--border-color);
            background: var(--bg-surface);
            color: var(--text-dark);
            cursor: pointer;
            transition: all 0.15s;
        }}
        .chip-btn:hover {{ background: var(--neutral-bg); }}
        .chip-btn.active {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}
        .chip-btn.chip-target.active {{
            background: #00875a;
            color: #ffffff;
            border-color: #00875a;
        }}
        .chip-btn.chip-excluded.active {{
            background: #de350b;
            color: #ffffff;
            border-color: #de350b;
        }}

        /* Content Cell Styles */
        .content-cell-container {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-width: 260px;
            max-width: 440px;
        }}
        .content-cell-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .btn-copy-tiny {{
            padding: 2px 6px;
            font-size: 10.5px;
            font-weight: 600;
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .btn-copy-tiny:hover {{ background: #ebecf0; }}
        .template-content-preview {{
            max-height: 160px;
            overflow-y: auto;
            font-size: 12px;
            line-height: 1.45;
            background: #fafbfc;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 8px 10px;
            color: var(--text-dark);
        }}
        .template-content-preview p {{
            margin: 0 0 6px 0;
        }}
        .template-content-preview p:last-child {{
            margin-bottom: 0;
        }}
        .template-content-preview ul, .template-content-preview ol {{
            padding-left: 18px;
            margin: 4px 0 6px 0;
        }}
        .template-content-preview table {{
            border-collapse: collapse;
            width: 100%;
            margin: 4px 0;
            font-size: 11px;
        }}
        .template-content-preview td, .template-content-preview th {{
            border: 1px solid #c1c7d0;
            padding: 3px 6px;
        }}

        .template-metadata-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            align-items: center;
            padding: 8px 12px;
            margin-bottom: 12px;
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            font-size: 12px;
            color: var(--text-dark);
        }}

        .btn-toggle-preview {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            border-radius: var(--radius-sm);
            border: 1px solid var(--border-color);
            background: var(--bg-surface);
            color: var(--text-dark);
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .btn-toggle-preview:hover {{
            background: var(--neutral-bg);
        }}
        .btn-toggle-preview .chevron-icon {{
            transition: transform 0.2s ease;
        }}
        .btn-toggle-preview.active .chevron-icon {{
            transform: rotate(180deg);
        }}

        .btn-copy-row {{
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            padding: 4px 8px;
            cursor: pointer;
            font-size: 12px;
            transition: background 0.15s;
        }}
        .btn-copy-row:hover {{
            background: var(--neutral-bg);
        }}

        .btn-open-jira {{
            display: inline-flex;
            align-items: center;
            color: var(--primary);
            text-decoration: none;
            font-weight: 600;
            font-size: 11.5px;
            padding: 3px 6px;
            border-radius: var(--radius-sm);
        }}
        .btn-open-jira:hover {{
            background: var(--primary-light);
            text-decoration: underline;
        }}

        .preview-row > td {{
            padding: 0 14px 14px;
            background: #f7f9fa;
        }}
        .preview-expandable-container {{
            width: 100%;
            padding: 14px;
            background: #f7f9fa;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
        }}
        .code-preview-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
        }}
        .preview-panel {{
            display: flex;
            flex-direction: column;
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            overflow: hidden;
        }}
        .panel-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: #f4f5f7;
            border-bottom: 1px solid var(--border-color);
            font-size: 11px;
            font-weight: 700;
            color: var(--text-dark);
        }}
        .btn-copy {{
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
            background: #ffffff;
            border: 1px solid var(--border-color);
            border-radius: 3px;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .btn-copy:hover {{ background: #ebecf0; }}
        .btn-copy-primary {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}
        .btn-copy-primary:hover {{
            background: var(--primary-dark);
            color: #ffffff;
        }}

        .preview-panel pre {{
            height: 320px;
            overflow: auto;
            padding: 10px;
            font-family: SFMono-Medium, Menlo, Consolas, monospace;
            font-size: 11.5px;
            line-height: 1.45;
            background: #fafbfc;
        }}
        .preview-iframe {{
            width: 100%;
            height: 320px;
            border: none;
            background: #ffffff;
        }}

        /* Toast Notifications */
        .toast-notification {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #172b4d;
            color: #ffffff;
            padding: 12px 20px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 600;
            box-shadow: var(--shadow-lg);
            z-index: 10000;
            opacity: 0;
            transform: translateY(12px);
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
            pointer-events: none;
            max-width: 420px;
            line-height: 1.4;
        }}
        .toast-notification.show {{
            opacity: 1;
            transform: translateY(0);
        }}

        /* Modal Styles */
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(9, 30, 66, 0.54);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }}
        .modal-card {{
            background: #ffffff;
            border-radius: var(--radius-lg);
            width: 90%;
            max-width: 840px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            box-shadow: var(--shadow-lg);
            overflow: hidden;
        }}
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-color);
        }}
        .modal-header h3 {{
            font-size: 16px;
            color: var(--text-dark);
            margin: 0;
        }}
        .modal-close {{
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            color: var(--text-muted);
        }}
        .modal-body {{
            padding: 20px;
            overflow-y: auto;
            flex: 1;
        }}
        .modal-tabs {{
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 8px;
        }}
        .modal-tab-btn {{
            padding: 6px 12px;
            font-size: 12px;
            font-weight: 600;
            border: 1px solid transparent;
            background: none;
            border-radius: var(--radius-sm);
            cursor: pointer;
            color: var(--text-muted);
        }}
        .modal-tab-btn.active {{
            background: var(--primary-light);
            color: var(--primary-dark);
            border-color: #b3d4ff;
        }}
        .modal-preview-box {{
            width: 100%;
            height: 280px;
            font-family: SFMono-Medium, Menlo, Consolas, monospace;
            font-size: 12px;
            padding: 12px;
            border: 1px solid var(--border-color);
            border-radius: var(--radius-sm);
            background: #fafbfc;
            overflow: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        .modal-footer {{
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            padding: 14px 20px;
            border-top: 1px solid var(--border-color);
            background: #fafbfc;
        }}

        .text-center {{ text-align: center; }}
        .font-mono {{ font-family: SFMono-Medium, Menlo, Consolas, monospace; }}
        .font-bold {{ font-weight: 700; }}
        .text-primary {{ color: var(--primary); }}
        .text-muted {{ color: var(--text-muted); }}
        .text-small {{ font-size: 11.5px; }}
        .text-tiny {{ font-size: 11px; }}

        /* When copying or printing, hide interactive buttons */
        .no-confluence {{
            user-select: none;
        }}
        @media print {{
            .no-confluence, .header-actions, .filter-bar, .confluence-tip-banner {{
                display: none !important;
            }}
        }}

        @media (max-width: 1024px) {{
            .code-preview-grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>

    <header class="dashboard-header">
        <div class="header-title-area">
            <h1>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
                JEditor Template SQL Extraction Report
            </h1>
            <p>Jira Active Objects (AO_BF4748_PROJECT_TEMPLATE) 기반 추출 리포트 &middot; 생성 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
        <div class="header-actions">
            <button type="button" class="btn-action btn-confluence" onclick="copyConfluenceTable('html')" title="현재 표시된 표(템플릿 내용 서식 포함)를 Confluence에 바로 붙여넣을 수 있는 네이티브 표로 복사합니다">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>📋 Confluence 표 복사</span>
            </button>
            <button type="button" class="btn-action btn-confluence-secondary" onclick="copyConfluenceTable('markdown')" title="마크다운(GFM) 표 형식으로 복사">
                <span>📝 Markdown 복사</span>
            </button>
            <button type="button" class="btn-action" onclick="openConfluenceModal()" title="포맷 선택 및 커스텀 컬럼 설정">
                <span>⚙️ 복사 옵션</span>
            </button>
            <button type="button" class="btn-action" id="toggleAllPreviews" onclick="toggleAllPreviews()">모든 Preview 펼치기</button>
            <button type="button" class="btn-action" onclick="exportJSON()">Export JSON</button>
            <button type="button" class="btn-action" onclick="window.print()">Print</button>
        </div>
    </header>

    <div class="confluence-tip-banner">
        <span>💡 <strong>Confluence 복사 안내:</strong> 상단의 <strong>[📋 Confluence 표 복사]</strong> 버튼을 클릭한 뒤, Confluence 편집창에서 <strong>Ctrl+V (Cmd+V)</strong>로 붙여넣으면 <strong>템플릿 내용 서식(굵은 글씨, 목록, 문단)</strong>이 적용된 완벽한 네이티브 표가 생성됩니다.</span>
        <div class="tip-links">
            <a onclick="openConfluenceModal()">세부 옵션 선택 ↗</a>
        </div>
    </div>

    <section class="kpi-grid">
        <div class="kpi-card kpi-total" onclick="setMigrationFilter('all')" style="cursor:pointer;" title="클릭 시 전체 템플릿 표시">
            <div class="kpi-label">Total Templates</div>
            <div class="kpi-value">{total_candidates}</div>
            <div class="kpi-subtext">추출된 전체 JEditor 템플릿 (클릭 시 전체)</div>
        </div>
        <div class="kpi-card kpi-success" onclick="setMigrationFilter('target')" style="cursor:pointer;" title="클릭 시 마이그레이션 대상만 필터링">
            <div class="kpi-label">🎯 Migration Targets</div>
            <div class="kpi-value">{target_count}</div>
            <div class="kpi-subtext">필드 및 이슈 타입 설정 완료 (클릭 필터)</div>
        </div>
        <div class="kpi-card kpi-warning" onclick="setMigrationFilter('excluded')" style="cursor:pointer;" title="클릭 시 제외/미설정 대상만 필터링">
            <div class="kpi-label">⚠️ Excluded / Incomplete</div>
            <div class="kpi-value">{excluded_count}</div>
            <div class="kpi-subtext">필드 또는 이슈 타입 미지정 (클릭 필터)</div>
        </div>
        <div class="kpi-card" style="border-left-color: #6554c0;">
            <div class="kpi-label">Target Projects</div>
            <div class="kpi-value">{total_projects}</div>
            <div class="kpi-subtext">템플릿 보유 프로젝트 수</div>
        </div>
    </section>

    <div class="filter-chips-row">
        <span style="font-size:12px;font-weight:700;color:var(--text-muted);">빠른 필터:</span>
        <button type="button" class="chip-btn active" data-filter="all" onclick="setMigrationFilter('all')">
            📋 전체 ({total_candidates})
        </button>
        <button type="button" class="chip-btn chip-target" data-filter="target" onclick="setMigrationFilter('target')">
            🎯 마이그레이션 대상만 ({target_count})
        </button>
        <button type="button" class="chip-btn chip-excluded" data-filter="excluded" onclick="setMigrationFilter('excluded')">
            ⚠️ 미설정/제외 대상만 ({excluded_count})
        </button>
    </div>

    <section class="filter-bar">
        <div class="search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input type="text" id="searchInput" placeholder="프로젝트, 템플릿명, 필드명, 본문 키워드 검색..." oninput="applyFilters()" />
        </div>
        <select id="targetFilter" class="filter-select font-bold" onchange="applyFilters()" title="마이그레이션 대상 여부 필터">
            <option value="all">📋 전체 템플릿 ({total_candidates}건)</option>
            <option value="target">🎯 마이그레이션 대상만 ({target_count}건)</option>
            <option value="excluded">⚠️ 미설정/제외 대상만 ({excluded_count}건)</option>
            <option value="missing_field">🚫 필드 미지정 건만</option>
            <option value="missing_itype">🚫 이슈 타입 미지정 건만</option>
        </select>
        <select id="projectFilter" class="filter-select" onchange="applyFilters()" title="프로젝트별 필터">
            {project_options_html}
        </select>
        <div class="result-count">
            표시: <strong id="visibleCount" class="text-primary">{len(filtered_records)}</strong> / {len(filtered_records)} 건
        </div>
        <div class="filter-actions">
            <button type="button" class="btn-action btn-confluence" id="btnCopyFilteredTable" onclick="copyConfluenceTable('html')" title="검색/필터된 결과 전체를 Confluence에 붙여넣을 수 있는 표로 복사합니다">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                <span>📋 필터 결과 표 전체 복사 (<span id="btnCopyFilteredCount">{len(filtered_records)}</span>건)</span>
            </button>
            <button type="button" class="btn-action btn-confluence-secondary" onclick="copyConfluenceTable('markdown')" title="마크다운(GFM) 표 형식으로 복사">
                <span>📝 Markdown 전체 복사</span>
            </button>
        </div>
    </section>

    <main class="table-card">
        <table id="templatesTable">
            <thead>
                <tr>
                    <th style="width: 44px;" class="text-center">#</th>
                    <th style="width: 140px;">프로젝트</th>
                    <th style="width: 180px;">템플릿명</th>
                    <th style="width: 150px;">설명</th>
                    <th style="width: 130px;">적용 필드</th>
                    <th style="width: 110px;">적용 이슈 유형</th>
                    <th style="min-width: 260px;">템플릿 내용</th>
                    <th style="width: 85px;" class="text-center">Jira 설정</th>
                    <th style="width: 90px;" class="text-center no-confluence">미리보기</th>
                    <th style="width: 50px;" class="text-center no-confluence" title="Confluence용 행 복사">복사</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows_html) if rows_html else '<tr><td colspan="10" class="text-center text-muted" style="padding: 40px;">추출된 JEditor 템플릿이 없습니다.</td></tr>'}
            </tbody>
        </table>
    </main>

    <!-- Confluence Copy Options Modal -->
    <div id="confluenceModal" class="modal-overlay" style="display:none;" onclick="handleModalBackdropClick(event)">
        <div class="modal-card" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h3>📋 Confluence 복사 마법사 (템플릿 내용 포함)</h3>
                <button type="button" class="modal-close" onclick="closeConfluenceModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="modal-tabs">
                    <button type="button" class="modal-tab-btn active" onclick="switchModalTab('html', this)">HTML (Confluence 에디터)</button>
                    <button type="button" class="modal-tab-btn" onclick="switchModalTab('markdown', this)">Markdown (마크다운 표)</button>
                    <button type="button" class="modal-tab-btn" onclick="switchModalTab('storage', this)">Storage Format (XHTML)</button>
                    <button type="button" class="modal-tab-btn" onclick="switchModalTab('tsv', this)">TSV (엑셀/스프레드시트)</button>
                </div>
                <div style="font-size:12px;color:var(--text-muted);margin-bottom:10px;">
                    현재 검색/필터가 적용된 <strong id="modalRowCount">0</strong>개의 템플릿을 선택한 서식으로 복사합니다.
                </div>
                <textarea id="modalPreviewBox" class="modal-preview-box" readonly></textarea>
            </div>
            <div class="modal-footer">
                <button type="button" class="btn-action" onclick="downloadModalContent()">💾 파일 다운로드</button>
                <button type="button" class="btn-action btn-confluence" onclick="copyModalContent()">📋 클립보드에 복사</button>
                <button type="button" class="btn-action" onclick="closeConfluenceModal()">닫기</button>
            </div>
        </div>
    </div>

    <!-- Toast Notification -->
    <div id="toastNotification" class="toast-notification"></div>

    <script>
    const TEMPLATES_DATA = {json_records_str};
    let currentModalFormat = 'html';

    function showToast(msg) {{
        const toast = document.getElementById('toastNotification');
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2800);
    }}

    function getVisibleTemplates() {{
        const visibleRows = [...document.querySelectorAll('#templatesTable tbody tr.template-row')]
            .filter(r => r.style.display !== 'none');
        return visibleRows.map(r => {{
            const id = r.getAttribute('data-id');
            return TEMPLATES_DATA.find(t => String(t.template_id) === String(id));
        }}).filter(Boolean);
    }}

    function generateConfluenceHtml(records) {{
        let rows = records.map((r, idx) => {{
            const pkey = r.project || '';
            const pname = r.project_name || '';
            const pnameSub = (pname && pname !== pkey) ? `<br/><span style="color:#6b778c;font-size:11px;">${{escapeHtml(pname)}}</span>` : '';
            const desc = r.description ? escapeHtml(r.description) : '-';
            const linkHtml = (r.url && r.url !== '#')
                ? `<a href="${{escapeHtml(r.url)}}" target="_blank" rel="noopener noreferrer" style="color:#0052cc;text-decoration:none;font-weight:bold;">설정 열기 ↗</a>`
                : '-';
            const contentHtml = r.cleaned_content || '(내용 없음)';

            const fieldHtml = r.has_field
                ? `<span style="background-color:#deebff;color:#0747a6;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.fields_str || 'Description')}}</span>`
                : `<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.fields_str || '미지정 (필드 없음)')}}</span>`;

            const itypesHtml = r.has_issue_type
                ? `<span style="font-family:monospace;font-size:12px;color:#172b4d;">${{escapeHtml(r.issue_types_str || 'all')}}</span>`
                : `<span style="background-color:#ffebe6;color:#de350b;padding:2px 6px;border-radius:3px;font-weight:bold;font-size:11px;">${{escapeHtml(r.issue_types_str || '미지정 (이슈 타입 없음)')}}</span>`;

            return `    <tr style="border-bottom:1px solid #dfe1e6;">
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;font-family:monospace;color:#6b778c;">${{idx + 1}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;"><strong>${{escapeHtml(pkey)}}</strong>${{pnameSub}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;font-weight:bold;color:#172b4d;">${{escapeHtml(r.name)}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;color:#172b4d;">${{desc}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">${{fieldHtml}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;">${{itypesHtml}}</td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;vertical-align:top;color:#172b4d;">
        <div style="line-height:1.45;font-size:12.5px;">
          ${{contentHtml}}
        </div>
      </td>
      <td style="border:1px solid #dfe1e6;padding:8px 10px;text-align:center;">${{linkHtml}}</td>
    </tr>`;
        }}).join('\\n');

        return `<table class="confluenceTable" border="1" style="border-collapse:collapse;width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:13px;color:#172b4d;border:1px solid #dfe1e6;">
  <thead>
    <tr style="background-color:#f4f5f7;border-bottom:2px solid #dfe1e6;text-align:left;">
      <th style="border:1px solid #dfe1e6;padding:10px 8px;text-align:center;width:40px;background-color:#f4f5f7;font-weight:bold;">#</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:140px;background-color:#f4f5f7;font-weight:bold;">프로젝트</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:180px;background-color:#f4f5f7;font-weight:bold;">템플릿명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:150px;background-color:#f4f5f7;font-weight:bold;">설명</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:130px;background-color:#f4f5f7;font-weight:bold;">적용 필드</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;width:110px;background-color:#f4f5f7;font-weight:bold;">적용 이슈 유형</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:left;min-width:260px;background-color:#f4f5f7;font-weight:bold;">템플릿 내용</th>
      <th style="border:1px solid #dfe1e6;padding:10px;text-align:center;width:90px;background-color:#f4f5f7;font-weight:bold;">Jira 설정</th>
    </tr>
  </thead>
  <tbody>
${{rows}}
  </tbody>
</table>`;
    }}

    function generateConfluenceMarkdown(records) {{
        const lines = [
            '| # | 프로젝트 | 템플릿명 | 설명 | 적용 필드 | 적용 이슈 유형 | 템플릿 내용 | Jira 설정 |',
            '|:---:|:---|:---|:---|:---|:---|:---|:---:|'
        ];
        records.forEach((r, idx) => {{
            const pkey = r.project || '';
            const pname = r.project_name || '';
            const projCell = (pname && pname !== pkey) ? `${{pkey}} (${{pname}})` : pkey;
            const desc = (r.description || '-').replace(/\\|/g, '\\\\|').replace(/\\n/g, ' ');
            const name = (r.name || '').replace(/\\|/g, '\\\\|');
            const field = (r.has_field ? (r.fields_str || 'Description') : `⚠️ ${{r.fields_str || '미지정'}}`).replace(/\\|/g, '\\\\|');
            const itypes = (r.has_issue_type ? (r.issue_types_str || 'all') : `⚠️ ${{r.issue_types_str || '미지정'}}`).replace(/\\|/g, '\\\\|');
            const link = (r.url && r.url !== '#') ? `[설정 열기](${{r.url}})` : '-';
            const content = (r.cleaned_content || '(내용 없음)').replace(/\\|/g, '\\\\|').replace(/\\r?\\n/g, '<br/>');
            lines.push(`| ${{idx + 1}} | ${{projCell}} | ${{name}} | ${{desc}} | ${{field}} | ${{itypes}} | ${{content}} | ${{link}} |`);
        }});
        return lines.join('\\n');
    }}

    function generateConfluenceStorageFormat(records) {{
        let xml = '<table class="wrapped"><colgroup>' + '<col/>'.repeat(8) + '</colgroup><tbody>\\n';
        xml += '<tr><th><p><strong>#</strong></p></th><th><p><strong>프로젝트</strong></p></th><th><p><strong>템플릿명</strong></p></th><th><p><strong>설명</strong></p></th><th><p><strong>적용 필드</strong></p></th><th><p><strong>이슈 유형</strong></p></th><th><p><strong>템플릿 내용</strong></p></th><th><p><strong>Jira 설정</strong></p></th></tr>\\n';
        records.forEach((r, idx) => {{
            const link = (r.url && r.url !== '#') ? `<a href="${{escapeXml(r.url)}}">설정 열기</a>` : '-';
            const content = r.cleaned_content || '<p>(내용 없음)</p>';
            xml += `<tr><td><p>${{idx + 1}}</p></td><td><p><strong>${{escapeXml(r.project)}}</strong>${{r.project_name && r.project_name !== r.project ? '<br/>' + escapeXml(r.project_name) : ''}}</p></td><td><p><strong>${{escapeXml(r.name)}}</strong></p></td><td><p>${{escapeXml(r.description || '-')}}</p></td><td><p>${{escapeXml(r.fields_str || 'Description')}}</p></td><td><p>${{escapeXml(r.issue_types_str || 'all')}}</p></td><td>${{content}}</td><td><p>${{link}}</p></td></tr>\\n`;
        }});
        xml += '</tbody></table>';
        return xml;
    }}

    function generateTsvFormat(records) {{
        const header = ['#', '프로젝트', '프로젝트명', '템플릿명', '설명', '적용 필드', '이슈 유형', '템플릿 내용', 'Jira URL'].join('\\t');
        const lines = [header];
        records.forEach((r, idx) => {{
            const textContent = (r.code || '').replace(/<[^>]+>/g, ' ').replace(/\\t|\\r|\\n/g, ' ').trim();
            lines.push([
                idx + 1,
                r.project || '',
                r.project_name || '',
                (r.name || '').replace(/\\t|\\r|\\n/g, ' '),
                (r.description || '').replace(/\\t|\\r|\\n/g, ' '),
                r.fields_str || 'Description',
                r.issue_types_str || 'all',
                JSON.stringify(textContent),
                r.url || ''
            ].join('\\t'));
        }});
        return lines.join('\\n');
    }}

    async function writeClipboardData(htmlText, plainText) {{
        if (navigator.clipboard && window.ClipboardItem) {{
            try {{
                const blobHtml = new Blob([htmlText], {{ type: 'text/html' }});
                const blobText = new Blob([plainText], {{ type: 'text/plain' }});
                await navigator.clipboard.write([
                    new ClipboardItem({{ 'text/html': blobHtml, 'text/plain': blobText }})
                ]);
                return true;
            }} catch (err) {{
                console.warn('ClipboardItem write failed, fallback to execCommand', err);
            }}
        }}

        // Fallback for rich text
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = htmlText;
        tempDiv.style.position = 'fixed';
        tempDiv.style.left = '-9999px';
        tempDiv.style.top = '-9999px';
        document.body.appendChild(tempDiv);
        const sel = window.getSelection();
        sel.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(tempDiv);
        sel.addRange(range);
        let ok = false;
        try {{
            ok = document.execCommand('copy');
        }} catch (_) {{}}
        sel.removeAllRanges();
        document.body.removeChild(tempDiv);

        if (!ok && navigator.clipboard && navigator.clipboard.writeText) {{
            await navigator.clipboard.writeText(plainText);
            ok = true;
        }}
        return ok;
    }}

    async function copyConfluenceTable(format) {{
        const records = getVisibleTemplates();
        if (!records.length) {{
            showToast('⚠️ 복사할 템플릿이 없습니다. (현재 필터 조건에 일치하는 결과 없음)');
            return;
        }}

        if (format === 'markdown') {{
            const md = generateConfluenceMarkdown(records);
            await navigator.clipboard.writeText(md);
            showToast(`✅ 필터 결과 ${{records.length}}개 템플릿의 전체 Markdown 표가 복사되었습니다!`);
            return;
        }}

        const htmlData = generateConfluenceHtml(records);
        const mdData = generateConfluenceMarkdown(records);
        const ok = await writeClipboardData(htmlData, mdData);
        if (ok) {{
            showToast(`✅ 필터 결과 ${{records.length}}개 템플릿의 전체 표가 복사되었습니다! Confluence 편집창에서 Ctrl+V(Cmd+V)로 붙여넣으세요.`);
        }} else {{
            showToast('❌ 클립보드 복사 실패');
        }}
    }}

    async function copySingleRow(templateId) {{
        const record = TEMPLATES_DATA.find(t => String(t.template_id) === String(templateId));
        if (!record) return;
        const htmlData = generateConfluenceHtml([record]);
        const mdData = generateConfluenceMarkdown([record]);
        await writeClipboardData(htmlData, mdData);
        showToast(`✅ [${{record.name}}] 템플릿 정보가 Confluence용으로 복사되었습니다.`);
    }}

    async function copyContentText(templateId, btn) {{
        const record = TEMPLATES_DATA.find(t => String(t.template_id) === String(templateId));
        if (!record || !record.cleaned_content) {{
            showToast('⚠️ 템플릿 내용이 없습니다.');
            return;
        }}
        try {{
            const plain = record.code ? record.code.replace(/<[^>]+>/g, '') : '';
            await writeClipboardData(record.cleaned_content, plain);
            if (btn) {{
                btn.textContent = 'Copied!';
                setTimeout(() => btn.textContent = 'Copy 내용', 1500);
            }}
            showToast(`✅ [${{record.name}}] 템플릿 본문 내용이 클립보드에 복사되었습니다.`);
        }} catch (_) {{
            showToast('❌ 복사 실패');
        }}
    }}

    async function copyTemplateBodyForConfluence(templateId) {{
        const record = TEMPLATES_DATA.find(t => String(t.template_id) === String(templateId));
        if (!record || !record.code) {{
            showToast('⚠️ 템플릿 본문이 없습니다.');
            return;
        }}
        const normalized = normalizeJeditorHtml(record.code);
        await writeClipboardData(normalized, record.code);
        showToast(`✅ [${{record.name}}] 템플릿 본문이 Confluence용 HTML로 복사되었습니다.`);
    }}

    function openConfluenceModal() {{
        const records = getVisibleTemplates();
        document.getElementById('modalRowCount').textContent = records.length;
        renderModalContent();
        document.getElementById('confluenceModal').style.display = 'flex';
    }}

    function closeConfluenceModal() {{
        document.getElementById('confluenceModal').style.display = 'none';
    }}

    function handleModalBackdropClick(e) {{
        if (e.target.id === 'confluenceModal') closeConfluenceModal();
    }}

    function switchModalTab(format, btn) {{
        currentModalFormat = format;
        document.querySelectorAll('.modal-tab-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        renderModalContent();
    }}

    function renderModalContent() {{
        const records = getVisibleTemplates();
        const box = document.getElementById('modalPreviewBox');
        if (currentModalFormat === 'html') {{
            box.value = generateConfluenceHtml(records);
        }} else if (currentModalFormat === 'markdown') {{
            box.value = generateConfluenceMarkdown(records);
        }} else if (currentModalFormat === 'storage') {{
            box.value = generateConfluenceStorageFormat(records);
        }} else if (currentModalFormat === 'tsv') {{
            box.value = generateTsvFormat(records);
        }}
    }}

    async function copyModalContent() {{
        const box = document.getElementById('modalPreviewBox');
        const text = box.value;
        if (currentModalFormat === 'html') {{
            const records = getVisibleTemplates();
            await writeClipboardData(text, generateConfluenceMarkdown(records));
            showToast('✅ Confluence 표 서식이 복사되었습니다! Confluence 편집창에 붙여넣으세요.');
        }} else {{
            await navigator.clipboard.writeText(text);
            showToast(`✅ ${{currentModalFormat.toUpperCase()}} 서식이 클립보드에 복사되었습니다.`);
        }}
    }}

    function downloadModalContent() {{
        const text = document.getElementById('modalPreviewBox').value;
        const ext = currentModalFormat === 'html' ? 'html' : (currentModalFormat === 'markdown' ? 'md' : (currentModalFormat === 'tsv' ? 'tsv' : 'xml'));
        const mime = currentModalFormat === 'html' ? 'text/html' : 'text/plain';
        const blob = new Blob([text], {{ type: mime }});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `jeditor_templates_confluence_${{new Date().toISOString().slice(0,10)}}.${{ext}}`;
        a.click();
    }}

    // Intercept native Ctrl+C / Cmd+C when selecting table contents
    document.addEventListener('copy', function(e) {{
        const selection = window.getSelection();
        if (!selection || !selection.rangeCount || selection.isCollapsed) return;
        const anchorNode = selection.anchorNode;
        const table = anchorNode ? (anchorNode.closest ? anchorNode.closest('#templatesTable') : anchorNode.parentElement?.closest('#templatesTable')) : null;
        if (!table) return;

        // User is selecting content inside templates table
        const range = selection.getRangeAt(0);
        const fragment = range.cloneContents();
        fragment.querySelectorAll('.no-confluence').forEach(el => el.remove());

        const div = document.createElement('div');
        div.appendChild(fragment);
        const cleanHtml = div.innerHTML;
        if (cleanHtml && cleanHtml.includes('<tr')) {{
            e.preventDefault();
            e.clipboardData.setData('text/html', cleanHtml);
            e.clipboardData.setData('text/plain', div.textContent);
            showToast('📋 선택된 표 내용이 Confluence용 서식으로 복사되었습니다.');
        }}
    }});

    function escapeHtml(value) {{
        return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
    }}

    function escapeXml(value) {{
        return escapeHtml(value);
    }}

    function applyFilters() {{
        const search = (document.getElementById('searchInput')?.value || '').toLowerCase().trim();
        const projectFilter = (document.getElementById('projectFilter')?.value || '').toLowerCase().trim();
        const targetFilter = (document.getElementById('targetFilter')?.value || 'all');

        const rows = document.querySelectorAll('#templatesTable tbody tr.template-row');
        let visible = 0;

        rows.forEach(r => {{
            const rText = r.textContent.toLowerCase();
            const rProject = (r.getAttribute('data-project') || '').toLowerCase();
            const isTarget = r.getAttribute('data-target') === '1';
            const hasField = r.getAttribute('data-has-field') === '1';
            const hasItype = r.getAttribute('data-has-itype') === '1';

            const matchSearch = !search || rText.includes(search);
            const matchProject = !projectFilter || rProject === projectFilter;

            let matchTarget = true;
            if (targetFilter === 'target') matchTarget = isTarget;
            else if (targetFilter === 'excluded') matchTarget = !isTarget;
            else if (targetFilter === 'missing_field') matchTarget = !hasField;
            else if (targetFilter === 'missing_itype') matchTarget = !hasItype;

            const shouldShow = matchSearch && matchProject && matchTarget;

            r.style.display = shouldShow ? '' : 'none';

            const nextRow = r.nextElementSibling;
            if (nextRow && nextRow.classList.contains('preview-row')) {{
                if (!shouldShow) {{
                    nextRow.style.display = 'none';
                    nextRow.dataset.open = 'false';
                    const btn = r.querySelector('.btn-toggle-preview');
                    if (btn) btn.classList.remove('active');
                }}
            }}

            if (shouldShow) visible++;
        }});

        document.getElementById('visibleCount').textContent = visible;
        const countSpan = document.getElementById('btnCopyFilteredCount');
        if (countSpan) countSpan.textContent = visible;
        const copyBtn = document.getElementById('btnCopyFilteredTable');
        if (copyBtn) {{
            copyBtn.disabled = (visible === 0);
            copyBtn.style.opacity = (visible === 0) ? '0.5' : '1';
        }}
        syncToggleAllPreviewsButton();

        // Sync filter chips
        document.querySelectorAll('.chip-btn').forEach(c => {{
            c.classList.toggle('active', c.getAttribute('data-filter') === targetFilter);
        }});
    }}

    function setMigrationFilter(type) {{
        const sel = document.getElementById('targetFilter');
        if (sel) {{
            sel.value = type;
            applyFilters();
        }}
    }}

    function normalizeJeditorHtml(rawHtml) {{
        if (!rawHtml) return '';
        const parser = new DOMParser();
        const doc = parser.parseFromString(rawHtml, 'text/html');

        doc.querySelectorAll('.jeditor-tabs').forEach(tabs => {{
            const expanded = doc.createElement('div');
            tabs.querySelectorAll('.jeditor-tab-panel').forEach(panel => {{
                const title = panel.getAttribute('data-tab-title') || panel.getAttribute('data-title');
                const heading = doc.createElement('h3');
                heading.textContent = title || 'Tab';
                expanded.append(heading);
                const content = doc.createElement('div');
                content.innerHTML = panel.innerHTML;
                expanded.append(content);
            }});
            if (expanded.children.length) tabs.replaceWith(expanded);
        }});

        doc.querySelectorAll('script,style,iframe,object,embed,form,input,button,select,textarea,svg').forEach(el => el.remove());
        return doc.body.innerHTML.trim();
    }}

    function togglePreview(btn) {{
        const mainRow = btn.closest('tr.template-row');
        const previewRow = mainRow ? mainRow.nextElementSibling : null;
        if (!previewRow || !previewRow.classList.contains('preview-row')) return;
        setPreviewOpen(btn, previewRow, previewRow.dataset.open !== 'true');
        syncToggleAllPreviewsButton();
    }}

    function setPreviewOpen(btn, previewRow, open) {{
        const container = previewRow.querySelector('.preview-expandable-container');
        previewRow.dataset.open = open ? 'true' : 'false';
        previewRow.style.display = open ? 'table-row' : 'none';
        btn.classList.toggle('active', open);
        if (open && !container.dataset.initialized) {{
            const adfCodeEl = container.querySelector('.adf-json');
            const iframeEl = container.querySelector('.preview-iframe');
            let doc = {{type: 'doc', version: 1, content: []}};
            try {{ doc = JSON.parse(adfCodeEl ? adfCodeEl.textContent : '{{}}'); }} catch (_) {{}}
            if (iframeEl) iframeEl.srcdoc = renderAdfDocument(doc);
            container.dataset.initialized = 'true';
        }}
    }}

    function toggleAllPreviews() {{
        const visibleRows = [...document.querySelectorAll('#templatesTable tbody tr.template-row')]
            .filter(row => row.style.display !== 'none');
        const expand = !visibleRows.length || visibleRows.some(row => row.nextElementSibling?.dataset.open !== 'true');
        visibleRows.forEach(row => {{
            const previewRow = row.nextElementSibling;
            const button = row.querySelector('.btn-toggle-preview');
            if (previewRow?.classList.contains('preview-row') && button) setPreviewOpen(button, previewRow, expand);
        }});
        syncToggleAllPreviewsButton();
    }}

    function syncToggleAllPreviewsButton() {{
        const button = document.getElementById('toggleAllPreviews');
        if (!button) return;
        const visibleRows = [...document.querySelectorAll('#templatesTable tbody tr.template-row')]
            .filter(row => row.style.display !== 'none');
        const allOpen = visibleRows.length > 0 && visibleRows.every(row => row.nextElementSibling?.dataset.open === 'true');
        button.textContent = allOpen ? '모든 Preview 접기' : '모든 Preview 펼치기';
    }}

    function escapePreviewText(value) {{
        return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[c]);
    }}

    function renderAdfNode(node) {{
        if (!node) return '';
        const children = (node.content || []).map(renderAdfNode).join('');
        const tag = (name, body, attrs='') => `<${{name}}${{attrs}}>${{body}}</${{name}}>`;
        if (node.type === 'text') {{
            let value = escapePreviewText(node.text || '');
            for (const mark of node.marks || []) {{
                const markTags = {{strong:'strong', em:'em', underline:'u', strike:'s', code:'code'}};
                if (mark.type === 'link') {{
                    const href = String(mark.attrs?.href || '');
                    value = /^(https?:|mailto:|tel:)/i.test(href) ? tag('a', value, ` href="${{escapePreviewText(href)}}" rel="noopener noreferrer"`) : value;
                }} else if (markTags[mark.type]) value = tag(markTags[mark.type], value);
            }}
            return value;
        }}
        switch (node.type) {{
            case 'doc': return children;
            case 'paragraph': return tag('p', children);
            case 'heading': return tag(`h${{Math.min(6, Math.max(1, Number(node.attrs?.level) || 1))}}`, children);
            case 'hardBreak': return '<br>';
            case 'bulletList': return tag('ul', children);
            case 'orderedList': return tag('ol', children, node.attrs?.order > 1 ? ` start="${{Number(node.attrs.order)}}"` : '');
            case 'listItem': return tag('li', children);
            case 'blockquote': return tag('blockquote', children);
            case 'codeBlock': return tag('pre', tag('code', children));
            case 'rule': return '<hr>';
            case 'table': return tag('table', tag('tbody', children));
            case 'tableRow': return tag('tr', children);
            case 'tableCell': return tag('td', children);
            case 'tableHeader': return tag('th', children);
            case 'mediaSingle':
                const media = (node.content || []).find(child => child.type === 'media');
                return tag('div', `🖼 ${{escapePreviewText(media?.attrs?.alt || 'Image attachment')}} (binary not included in preview)`, ' class="attachment-placeholder"');
            default: return children || (node.text ? escapePreviewText(node.text) : '');
        }}
    }}

    function renderAdfDocument(doc) {{
        const body = renderAdfNode(doc);
        return `<!doctype html><html><head><meta charset="utf-8"><style>body{{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#172b4d;padding:16px;margin:0}}p{{margin:0 0 12px}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #c1c7d0;padding:7px;text-align:left;vertical-align:top}}blockquote{{border-left:3px solid #8590a2;padding-left:12px;margin:10px 0;color:#505f79}}pre,code{{font-family:monospace;background:#f4f5f7;padding:2px 4px}}pre{{padding:10px;overflow:auto}}.attachment-placeholder{{border:1px dashed #97a0af;background:#f4f5f7;padding:10px;margin:8px 0;color:#505f79}}</style></head><body>${{body}}</body></html>`;
    }}

    async function copyPanelCode(btn, selector) {{
        const pre = btn.closest('.preview-panel').querySelector(selector);
        const text = pre ? pre.textContent : '';
        try {{
            await navigator.clipboard.writeText(text);
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = 'Copy HTML', 1500);
        }} catch (_) {{
            btn.textContent = 'Failed';
        }}
    }}

    async function copyAdfJson(button) {{
        const pre = button.closest('.preview-row').querySelector('.adf-json');
        const text = pre ? pre.textContent : '';
        try {{
            await navigator.clipboard.writeText(text);
            button.textContent = 'Copied!';
            setTimeout(() => button.textContent = 'Copy ADF JSON', 1500);
        }} catch (_) {{
            button.textContent = 'Copy Failed';
        }}
    }}

    function exportJSON() {{
        const records = getVisibleTemplates();
        const blob = new Blob([JSON.stringify(records, null, 2)], {{ type: 'application/json' }});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `jeditor_templates_report_${{new Date().toISOString().slice(0,10)}}.json`;
        a.click();
    }}
    </script>
</body>
</html>
"""

    report_path.write_text(html_page, encoding="utf-8")
    print(f"📊 최종 대시보드 리포트 생성 완료: {report_path.resolve()} (총 {total_candidates}개 템플릿)")

    # Also automatically generate the companion Confluence-dedicated table report
    if confluence_path is None:
        confluence_path = report_path.parent / f"{report_path.stem}_confluence.html"
    write_confluence_report(filtered_records, output_path=confluence_path)


def main():
    start_time = time.time()
    parser = argparse.ArgumentParser(
        description="Fast SQL-based JEditor Template Extractor & Confluence-Ready Report Generator",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=None,
        help="입력 HTML 파일 경로 (생략 시 templates_output.html 자동 감지 또는 DB 직접 조회)",
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default="",
        help="SQL 실행 결과 HTML 덤프 파일 경로 (예: templates_output.html 또는 ao_bf4748_project_template_*.html)\n"
             "지정하지 않으면 templates_output.html 자동 탐색 또는 MySQL DB에 직접 연결하여 쿼리합니다.",
    )
    parser.add_argument(
        "--project", "-p",
        type=str,
        default="",
        help="대상 프로젝트 키 지정 (쉼표 구분, 기본: 스크립트 상위 폴더의 projects.txt)",
    )
    parser.add_argument(
        "--env",
        choices=["ops", "stg"],
        default="ops",
        help="대상 Jira 환경 이름 (기본값: ops)",
    )
    parser.add_argument(
        "--all-templates",
        action="store_true",
        help="Autofill 설정 여부와 상관없이 모든 템플릿을 리포트에 포함합니다 (기본: 전체 포함)",
    )
    parser.add_argument(
        "--autofill-only",
        action="store_true",
        help="Prepopulation(Auto-fill) 설정이 활성화된 템플릿만 필터링하여 포함합니다.",
    )
    parser.add_argument(
        "--migration-only", "--target-only",
        action="store_true",
        help="적용 필드와 적용 이슈 타입이 모두 설정된 마이그레이션 대상 템플릿만 필터링하여 포함합니다.",
    )
    parser.add_argument(
        "--excluded-only",
        action="store_true",
        help="적용 필드 또는 이슈 타입이 미지정된 제외/미설정 대상 템플릿만 필터링하여 포함합니다.",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(DEFAULT_OUTPUT_REPORT),
        help=f"출력 HTML 대시보드 보고서 경로 (기본값: {DEFAULT_OUTPUT_REPORT.name})",
    )
    parser.add_argument(
        "--confluence", "-c",
        type=str,
        default="",
        help=f"Confluence 전용 복사 보고서 경로 (기본값: <output_name>_confluence.html)",
    )
    parser.add_argument(
        "--issue-types-file", "--types-file",
        type=str,
        default="",
        help="이슈 타입 ID-이름 매핑 파일 경로 (JSON/CSV 형식, 기본: issue_types.json 자동 감지)",
    )
    parser.add_argument(
        "--issue-types-map", "--types-map",
        type=str,
        default="",
        help="이슈 타입 ID-이름 인라인 매핑 (예: '1=Bug,2=New Feature,10000=Epic')",
    )
    parser.add_argument(
        "--custom-fields-file", "--fields-file",
        type=str,
        default="",
        help="커스텀 필드 ID-이름 매핑 파일 경로 (JSON/CSV 형식, 기본: custom_fields.json 자동 감지)",
    )
    parser.add_argument(
        "--custom-fields-map", "--fields-map",
        type=str,
        default="",
        help="커스텀 필드 ID-이름 인라인 매핑 (예: 'customfield_10010=Release Notes, 10020=사양서')",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="대화형 질문을 생략하고 인자 기본값으로 바로 실행합니다.",
    )

    parser.add_argument('--default-values', type=Path, default=SCRIPT_DIR / 'default_values.json', help='run_default_values.py로 생성한 비교용 JSON')
    parser.add_argument('--jeditor-renderer-key', action='append', help='실제 JEditor renderer 키 (기본: jeditor-renderer, 반복 지정 가능)')
    args = parser.parse_args()
    try:
        default_snapshot = load_snapshot(args.default_values, args.env, get_db_config())
    except (OSError, ValueError, KeyError) as error:
        parser.error(f'기본값 우선 검증 준비 실패: {error}. run_default_values.py를 먼저 실행하세요.')
    policy = Policy(default_snapshot, args.jeditor_renderer_key or ('jeditor-renderer',))

    if args.project:
        target_projects = list(dict.fromkeys(p.strip().upper() for p in args.project.split(",") if p.strip()))
        if not target_projects or any(not re.fullmatch(r"[A-Z0-9_-]+", p) for p in target_projects):
            parser.error("--project에 유효한 프로젝트 키를 지정하세요. 예: ATCU,BDD")
        project_source = "--project"
    else:
        try:
            target_projects = load_projects_list()
        except (OSError, UnicodeError) as error:
            parser.error(f"프로젝트 목록을 읽을 수 없습니다: {PROJECTS_FILE}: {error}")
        if not target_projects:
            parser.error(f"프로젝트 목록이 없거나 유효한 키가 없습니다: {PROJECTS_FILE}. 파일을 준비하거나 --project를 지정하세요.")
        project_source = str(PROJECTS_FILE)
    print(f"🎯 대상 프로젝트 ({project_source}): {', '.join(target_projects)}")

    print("\n" + "=" * 65)
    print("🐘 JEditor Template SQL Extractor & Confluence Reporter")
    print("=" * 65)

    base_jira_url = (
        os.getenv(f"JIRA_DC_{args.env.upper()}_URL")
        or os.getenv(f"{args.env.upper()}_JIRA_URL")
        or ""
    ).rstrip("/")
    token = (
        os.getenv(f"JIRA_DC_{args.env.upper()}_API_TOKEN")
        or os.getenv(f"{args.env.upper()}_JIRA_PASSWORD")
        or os.getenv(f"JIRA_DC_{args.env.upper()}_TOKEN")
        or ""
    )
    issue_type_map = load_issue_type_mappings(
        custom_file=args.issue_types_file,
        custom_map_str=args.issue_types_map,
        base_jira_url=base_jira_url,
        token=token,
    )
    custom_field_map = load_custom_field_mappings(
        custom_file=args.custom_fields_file,
        custom_map_str=args.custom_fields_map,
        base_jira_url=base_jira_url,
        token=token,
    )

    # 1. Determine Input Mode
    if not args.input and getattr(args, "input_file", None):
        args.input = args.input_file

    records: list[dict] = []
    output_path = Path(args.output).resolve()
    confluence_path = Path(args.confluence).resolve() if args.confluence else output_path.parent / f"{output_path.stem}_confluence.html"

    # Check for HTML dump candidates if not specified
    if not args.input:
        candidates = [
            SCRIPT_DIR / "templates_output.html",
            Path("templates_output.html"),
        ]
        for cand in candidates:
            if cand.exists() and (not os.getenv("JIRA_DB_HOST") or cand.name == "templates_output.html"):
                print(f"💡 템플릿 HTML 덤프 파일이 자동 감지되었습니다:")
                print(f"   -> {cand}")
                args.input = str(cand)
                break

    if args.input:
        input_file = Path(args.input).resolve()
        print(f"📂 [HTML 파일 덤프 모드] '{input_file}' 파싱 중...")
        records = parse_html_table_dump(
            input_file,
            env_name=args.env,
            issue_type_map=issue_type_map,
            custom_field_map=custom_field_map,
        )
        if target_projects:
            records = [
                r for r in records
                if r["project"] in target_projects
                or r["project"].replace("PROJ_", "") in target_projects
            ]
            print(f"🎯 선택 프로젝트 필터 적용: {len(records)}개 템플릿 매칭")
    else:
        db_config = get_db_config()
        if not db_config["host"] or not db_config["user"] or not db_config["database"]:
            print("\n[ERROR] DB 접속 정보가 env 파일에 설정되어 있지 않습니다.")
            print("다음 키를 env 파일에 설정하거나, --input <HTML덤프파일> 옵션을 사용하세요:")
            print("  JIRA_DB_HOST, JIRA_DB_PORT, JIRA_DB_USER, JIRA_DB_PASSWORD, JIRA_DB_NAME\n")
            print(f"참고: step2_query_runner.py로 '{DEFAULT_SQL_FILE.name}'을 실행하여 생성된 HTML 파일을 사용할 수도 있습니다.")
            sys.exit(1)

        print("🚀 [MySQL 직접 쿼리 모드] AO_BF4748_PROJECT_TEMPLATE 테이블 조회 중...")
        records = fetch_templates_from_db(
            db_config,
            target_projects=target_projects,
            env_name=args.env,
            issue_type_map=issue_type_map,
            custom_field_map=custom_field_map,
        )

    if not records:
        print("\n⚠️ 조회된 JEditor 템플릿 데이터가 없습니다.")
        sys.exit(0)

    policy.apply(records)

    # Apply migration-only or excluded-only filter if requested via CLI
    if args.migration_only:
        records = [r for r in records if r.get("is_migration_target", False)]
        print(f"🎯 [--migration-only 적용] 총 {len(records)}개의 마이그레이션 대상 템플릿만 추출합니다.")
    elif args.excluded_only:
        records = [r for r in records if not r.get("is_migration_target", False)]
        print(f"⚠️ [--excluded-only 적용] 총 {len(records)}개의 미설정/제외 대상 템플릿만 추출합니다.")

    if not records:
        print("\n⚠️ 필터 조건에 부합하는 JEditor 템플릿이 없습니다.")
        sys.exit(0)

    # 2. Generate Dashboard Report & Confluence Report
    filter_autofill = args.autofill_only
    write_template_report(
        records,
        report_path=output_path,
        filter_autofill=filter_autofill,
        confluence_path=confluence_path,
    )

    # 3. Print Summary & Duration
    elapsed = time.time() - start_time
    projects_with_tmpl = len({r["project"] for r in records})
    target_count = sum(1 for r in records if r.get("is_migration_target", False))
    excluded_count = len(records) - target_count

    print("\n" + "=" * 65)
    print("🎉 [완료] JEditor 템플릿 SQL 추출 및 Confluence 리포트 생성 완료!")
    print(f"  • 총 템플릿 수         : {len(records):,}개")
    print(f"  • 🎯 마이그레이션 대상 : {target_count:,}개 (설정·기본값 우선·Renderer 검증 통과)")
    print(f"  • ⚠️ 제외/미설정 대상  : {excluded_count:,}개 (검증 제외 또는 검토 필요)")
    print(f"  • 템플릿 보유 프로젝트 : {projects_with_tmpl:,}개")
    print(f"  • 대시보드 리포트 파일 : {output_path}")
    print(f"  • Confluence 복사 파일 : {confluence_path}")
    print(f"  • ⏱️  총 실행 소요 시간  : {format_duration(elapsed)}")
    print("  • 💡 Tip: 대시보드를 브라우저에서 열고 [📋 Confluence 표 복사] 버튼을 누르면")
    print("           템플릿 본문 내용이 포함된 표를 Confluence에 즉시 붙여넣기(Ctrl+V)할 수 있습니다!")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
