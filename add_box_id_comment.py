#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Confluence 페이지 내 '3. BigGantt 이관 대상 Box 상세 목록' 테이블에서
Box ID 목록을 추출하여 해당 페이지에 댓글(footer comment)로 등록하는 스크립트.

형식: Box ID, Box ID, ...
"""

import argparse
import base64
import html
from html.parser import HTMLParser
import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.error

# Windows 터미널 한글 및 유니코드 인코딩 호환 처리
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# auth.py 파일에서 인증 정보 로드
try:
    from auth import EMAIL, API_TOKEN, DOMAIN
except ImportError:
    EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
    API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
    DOMAIN = os.getenv("CONFLUENCE_DOMAIN", "cloud-osci.atlassian.net")

DEFAULT_PAGE_ID = "24570527907"


def get_headers():
    auth_str = f"{EMAIL}:{API_TOKEN}"
    auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }


def get_ssl_context():
    return ssl._create_unverified_context()


def fetch_page_content(page_id: str):
    """Confluence v2 API를 통해 페이지의 제목과 본문(storage format)을 조회합니다."""
    url = f"https://{DOMAIN}/wiki/api/v2/pages/{page_id}?body-format=storage"
    headers = get_headers()
    ctx = get_ssl_context()
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req, context=ctx) as res:
        data = json.loads(res.read().decode("utf-8"))
        title = data.get("title", "")
        body = data.get("body", {}).get("storage", {}).get("value", "")
        return title, body


class TableParser(HTMLParser):
    """HTML 테이블 파서 (self-closing 태그 및 중첩 태그 완벽 지원)"""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.table_count = 0
        self.rows = []
        self.current_row = []
        self.current_cell = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table" and self.table_count == 0:
            self.in_table = True
            self.table_count += 1
        elif self.in_table:
            if tag == "tr":
                self.current_row = []
            elif tag in ("th", "td"):
                self.in_cell = True
                self.current_cell = []

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag == "table":
            self.in_table = False
        elif tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
        elif tag in ("th", "td"):
            self.in_cell = False
            self.current_row.append("".join(self.current_cell).strip())

    def handle_startendtag(self, tag, attrs):
        # <th class="numberingColumn" /> 등 self-closing 태그 대응
        if self.in_table and tag in ("th", "td"):
            self.current_row.append("")

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)


def extract_box_ids(body_html: str):
    """
    페이지 본문에서 '3. BigGantt 이관 대상 Box 상세 목록' 섹션을 찾아
    해당 테이블의 Box ID들을 순서대로 추출합니다.
    """
    # 1. 대상 섹션 헤딩 위치 탐색
    heading_pattern = r"(3\.\s*BigGantt\s*이관\s*대상\s*Box\s*상세\s*목록)"
    match = re.search(heading_pattern, body_html)
    
    search_scope = body_html[match.start():] if match else body_html
    if not match:
        print("⚠️ '3. BigGantt 이관 대상 Box 상세 목록' 헤딩을 명시적으로 찾지 못하여 전체 본문에서 테이블을 탐색합니다.")

    # 2. HTMLParser를 사용하여 테이블 파싱
    parser = TableParser()
    parser.feed(search_scope)

    if not parser.rows:
        raise ValueError("대상 테이블 또는 행(tr)을 찾을 수 없습니다.")

    # 헤더 행에서 'Box ID' 컬럼 인덱스 찾기
    header_row = parser.rows[0]
    box_id_col_idx = None
    for idx, cell in enumerate(header_row):
        clean_cell = cell.replace(" ", "").upper()
        if "BOXID" in clean_cell:
            box_id_col_idx = idx
            break

    # 컬럼 인덱스를 찾지 못했을 경우 기본 인덱스 1 (0은 번호 컬럼)
    if box_id_col_idx is None:
        print("⚠️ 헤더에서 'Box ID' 컬럼을 특정하지 못해 기본 인덱스 1을 사용합니다.")
        box_id_col_idx = 1

    box_ids = []
    for row in parser.rows[1:]:
        if box_id_col_idx < len(row):
            val = row[box_id_col_idx].strip()
            # 빈 셀 또는 헤더 재반복 방지
            if val and val.replace(" ", "").upper() != "BOXID":
                box_ids.append(val)

    return box_ids


def create_footer_comment(page_id: str, comment_text: str):
    """Confluence 페이지에 footer 댓글을 추가합니다. (v2 -> v1 fallback)"""
    headers = get_headers()
    ctx = get_ssl_context()
    escaped_text = html.escape(comment_text)
    storage_value = f"<p>{escaped_text}</p>"

    # 1. Confluence REST API v2 시도
    url_v2 = f"https://{DOMAIN}/wiki/api/v2/footer-comments"
    payload_v2 = {
        "pageId": str(page_id),
        "body": {
            "representation": "storage",
            "value": storage_value
        }
    }
    
    req_v2 = urllib.request.Request(
        url_v2,
        data=json.dumps(payload_v2).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req_v2, context=ctx) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            comment_id = res_data.get("id")
            web_ui = res_data.get("_links", {}).get("webui", "")
            return comment_id, web_ui
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        print(f"⚠️ Confluence v2 댓글 등록 실패 (HTTP {e.code}): {err_msg}")
        print("➡️ Confluence v1 API로 재시도합니다...")

    # 2. Confluence REST API v1 Fallback
    url_v1 = f"https://{DOMAIN}/wiki/rest/api/content"
    payload_v1 = {
        "type": "comment",
        "container": {
            "id": str(page_id),
            "type": "page"
        },
        "body": {
            "storage": {
                "value": storage_value,
                "representation": "storage"
            }
        }
    }
    req_v1 = urllib.request.Request(
        url_v1,
        data=json.dumps(payload_v1).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req_v1, context=ctx) as res:
        res_data = json.loads(res.read().decode("utf-8"))
        comment_id = res_data.get("id")
        web_ui = res_data.get("_links", {}).get("webui", "")
        return comment_id, web_ui


def main():
    parser = argparse.ArgumentParser(
        description="Confluence '3. BigGantt 이관 대상 Box 상세 목록' 테이블에서 Box ID를 추출하여 댓글을 추가합니다."
    )
    parser.add_argument(
        "--page-id",
        default=DEFAULT_PAGE_ID,
        help=f"Confluence 페이지 ID (기본값: {DEFAULT_PAGE_ID})"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 댓글을 등록하지 않고 추출된 Box ID 목록만 출력합니다."
    )
    args = parser.parse_args()

    page_id = args.page_id

    print("=" * 60)
    print(f"📌 Confluence 페이지 조회 중... (Page ID: {page_id})")
    print(f"🌐 도메인: {DOMAIN}")
    print("=" * 60)

    try:
        title, body = fetch_page_content(page_id)
        print(f"📄 페이지 제목: {title}")
    except Exception as e:
        print(f"❌ 페이지 조회 실패: {e}")
        sys.exit(1)

    print("\n🔍 테이블에서 Box ID 목록 추출 중...")
    try:
        box_ids = extract_box_ids(body)
        print(f"✅ 총 {len(box_ids)}개의 Box ID 추출 완료!")
    except Exception as e:
        print(f"❌ Box ID 추출 실패: {e}")
        sys.exit(1)

    if not box_ids:
        print("⚠️ 추출된 Box ID가 없습니다.")
        return

    # 'Box ID, Box ID, ...' 형식으로 결합
    comment_content = ", ".join(box_ids)

    print("\n📋 [생성된 댓글 내용]")
    print(comment_content)
    print("-" * 60)

    if args.dry_run:
        print("💡 --dry-run 모드이므로 댓글을 등록하지 않고 종료합니다.")
        return

    print("\n💬 Confluence 댓글 등록 진행 중...")
    try:
        comment_id, web_ui = create_footer_comment(page_id, comment_content)
        full_comment_url = f"https://{DOMAIN}/wiki{web_ui}" if web_ui else f"https://{DOMAIN}/wiki/pages/viewpage.action?pageId={page_id}"
        print(f"🎉 댓글이 성공적으로 등록되었습니다!")
        print(f"   - 댓글 ID: {comment_id}")
        print(f"   - 바로가기: {full_comment_url}")
    except Exception as e:
        print(f"❌ 댓글 등록 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
