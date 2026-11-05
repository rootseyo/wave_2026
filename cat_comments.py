#!/usr/bin/env python3
"""
Confluence 페이지 댓글 조회(cat) 및 삭제 스크립트

사용법:
    1) 페이지 댓글 목록 조회 (cat):
       python cat_comments.py [Confluence_페이지_ID]
       
    2) 특정 댓글 삭제:
       python cat_comments.py --delete <댓글_ID> [Confluence_페이지_ID]
       
    3) 페이지의 모든 댓글 삭제:
       python cat_comments.py --delete-all [Confluence_페이지_ID]
"""

import argparse
import base64
import json
import os
import re
import ssl
import sys
import urllib.request

# Windows CMD / PowerShell 한글 및 유니코드 인코딩 호환 처리
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ================= 변수 설정 구역 =================
EMAIL = os.getenv("CONFLUENCE_EMAIL") or "seyeon.kim@osci.kr"
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN") or "ATATT3xFfGF0O5NI20CZCCuF_oOX91r3crK81YMdUN6c0X8AKpJXBrvu4GKQ_1tSyaOzvONtgSVbFLtHtXat7dsDl8Y8mQe9noplWBB2p6U-LF1CZUKLnDwKKlWfH5dzKrL0TyjWZZRzWXh-t5-J8ftrkzgevvAdAbp8bF6qM5-hXAmynvr0Cfs=F14829E3"
DOMAIN = os.getenv("CONFLUENCE_DOMAIN") or "cloud-osci.atlassian.net"
DEFAULT_PAGE_ID = "24460984448"  # 기본 Confluence 페이지 ID
# =================================================

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

def strip_html(html_text: str) -> str:
    """HTML 태그 및 Confluence storage 포맷 태그를 깔끔한 평문 텍스트로 변환"""
    if not html_text:
        return ""
    # <p>, <br> 등을 줄바꿈으로 변환
    text = re.sub(r'</p>|<br\s*/?>', '\n', html_text)
    # 나머지 HTML 태그 제거
    text = re.sub(r'<[^>]+>', '', text)
    # 연속 줄바꿈 정돈
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

def fetch_comments(page_id: str) -> list:
    """페이지의 모든 footer 댓글을 조회하여 반환"""
    ssl_context = get_ssl_context()
    headers = get_headers()
    
    # 1. v2 API로 footer comments 조회 시도
    url_v2 = f"https://{DOMAIN}/wiki/api/v2/pages/{page_id}/footer-comments?body-format=storage"
    req = urllib.request.Request(url_v2, headers=headers)
    
    comments = []
    try:
        with urllib.request.urlopen(req, context=ssl_context) as res:
            data = json.loads(res.read().decode("utf-8"))
            results = data.get("results", [])
            for item in results:
                c_id = item.get("id")
                created_at = item.get("createdAt", "N/A")
                body_val = item.get("body", {}).get("storage", {}).get("value", "")
                plain_body = strip_html(body_val)
                version = item.get("version", {}).get("number", 1)
                
                comments.append({
                    "id": c_id,
                    "created_at": created_at,
                    "body_html": body_val,
                    "body_plain": plain_body,
                    "version": version
                })
            return comments
    except Exception:
        pass

    # 2. v1 API fallback
    url_v1 = f"https://{DOMAIN}/wiki/rest/api/content/{page_id}/child/comment?expand=body.storage,version,history"
    req_v1 = urllib.request.Request(url_v1, headers=headers)
    try:
        with urllib.request.urlopen(req_v1, context=ssl_context) as res:
            data = json.loads(res.read().decode("utf-8"))
            results = data.get("results", [])
            for item in results:
                c_id = item.get("id")
                created_at = item.get("history", {}).get("createdDate", "N/A")
                body_val = item.get("body", {}).get("storage", {}).get("value", "")
                plain_body = strip_html(body_val)
                
                comments.append({
                    "id": c_id,
                    "created_at": created_at,
                    "body_html": body_val,
                    "body_plain": plain_body,
                })
    except Exception as e:
        print(f"❌ 댓글 목록 조회 실패: {e}")
        
    return comments

def cat_comments(page_id: str):
    """페이지의 댓글 목록을 cat 명령어 스타일로 터미널에 표시"""
    print(f"💬 [{page_id}] 페이지 댓글 조회 중 (Domain: {DOMAIN})...\n")
    comments = fetch_comments(page_id)
    
    if not comments:
        print("📭 해당 페이지에 작성된 댓글이 없습니다.")
        return

    print("=" * 70)
    print(f"📑 [{page_id}] 페이지 댓글 목록 (총 {len(comments)}개)")
    print("=" * 70)

    for idx, c in enumerate(comments, 1):
        print(f"----------------------------------------------------------------------")
        print(f"📌 [댓글 #{idx}]  ID: {c['id']}  |  생성일시: {c['created_at']}")
        print(f"----------------------------------------------------------------------")
        print(c['body_plain'] if c['body_plain'] else c['body_html'])
        print("")

    print("=" * 70)

def delete_comment_by_id(comment_id: str) -> bool:
    """댓글 ID로 단일 댓글 삭제"""
    ssl_context = get_ssl_context()
    headers = get_headers()
    
    # 1. REST v1 삭제 엔드포인트
    delete_url = f"https://{DOMAIN}/wiki/rest/api/content/{comment_id}"
    req = urllib.request.Request(delete_url, headers=headers, method="DELETE")
    
    print(f"🗑️ 댓글 삭제 중... (ID: {comment_id})")
    try:
        with urllib.request.urlopen(req, context=ssl_context) as res:
            print(f"✅ 댓글(ID: {comment_id}) 삭제 완료!")
            return True
    except Exception as e:
        # 2. REST v2 footer-comments 삭제 fallback
        try:
            delete_url_v2 = f"https://{DOMAIN}/wiki/api/v2/footer-comments/{comment_id}"
            req_v2 = urllib.request.Request(delete_url_v2, headers=headers, method="DELETE")
            with urllib.request.urlopen(req_v2, context=ssl_context) as res_v2:
                print(f"✅ 댓글(ID: {comment_id}) 삭제 완료! (v2 API)")
                return True
        except Exception as e2:
            print(f"❌ 댓글(ID: {comment_id}) 삭제 실패: {e2}")
            return False

def delete_all_comments(page_id: str):
    """페이지의 모든 댓글 일괄 삭제"""
    comments = fetch_comments(page_id)
    if not comments:
        print("📭 삭제할 댓글이 없습니다.")
        return

    print(f"⚠️ [{page_id}] 페이지의 총 {len(comments)}개 댓글을 일괄 삭제합니다.")
    success_count = 0
    for c in comments:
        if delete_comment_by_id(c['id']):
            success_count += 1
            
    print(f"\n🎉 삭제 작업 완료 (총 {len(comments)}개 중 {success_count}개 삭제 완료)")

def cat_and_delete_all(page_id: str, view_only: bool = False):
    """댓글 목록을 cat으로 먼저 출력한 후, 해당 페이지의 모든 댓글을 자동 삭제"""
    comments = fetch_comments(page_id)
    
    if not comments:
        print(f"📭 [{page_id}] 페이지에 존재하는 댓글이 없습니다.")
        return

    print("=" * 70)
    print(f"📑 [{page_id}] 페이지 댓글 목록 (총 {len(comments)}개)")
    print("=" * 70)

    for idx, c in enumerate(comments, 1):
        print(f"----------------------------------------------------------------------")
        print(f"📌 [댓글 #{idx}]  ID: {c['id']}  |  생성일시: {c['created_at']}")
        print(f"----------------------------------------------------------------------")
        print(c['body_plain'] if c['body_plain'] else c['body_html'])
        print("")

    print("=" * 70)

    if view_only:
        print("💡 --view-only 옵션이 지정되어 댓글 삭제를 건너뜁니다.")
        return

    print(f"\n🔥 [자동 삭제] 위 조회된 총 {len(comments)}개 댓글을 모두 삭제합니다...")
    success_count = 0
    for c in comments:
        if delete_comment_by_id(c['id']):
            success_count += 1
            
    print(f"\n🎉 댓글 출력 후 모두 삭제 완료! (총 {len(comments)}개 중 {success_count}개 삭제 성공)")

def main():
    parser = argparse.ArgumentParser(
        description="Confluence 페이지 댓글 cat 출력 후 자동 삭제 전용 스크립트"
    )
    parser.add_argument("page_id", nargs="?", default=DEFAULT_PAGE_ID, help=f"Confluence 페이지 ID (기본값: {DEFAULT_PAGE_ID})")
    parser.add_argument("--delete", metavar="COMMENT_ID", help="지정한 ID의 댓글 단건 삭제")
    parser.add_argument("--view-only", action="store_true", help="댓글 삭제 없이 cat으로 조회만 수행")
    
    args = parser.parse_args()
    page_id = args.page_id

    if args.delete:
        delete_comment_by_id(args.delete)
    else:
        cat_and_delete_all(page_id, view_only=args.view_only)

if __name__ == "__main__":
    main()
