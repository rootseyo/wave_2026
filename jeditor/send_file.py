#!/usr/bin/env python3
import urllib.request
import os
import mimetypes
import json
import base64
import ssl

# ================= 변수 설정 구역 =================
# 환경 변수가 있으면 사용하고 없으면 기본값 사용
EMAIL = os.getenv("CONFLUENCE_EMAIL") or "seyeon.kim@osci.kr"
API_TOKEN = "ATATT3xFfGF0jM1WZ_jKnK5hUHrMcjg8-SeHFT4iYxLRd_KzSbrosMQBv5OmEbjZx9CinsCueZsdq8XPZF6jbL3CiZYZwveY2rSocdzS09UH7bJ9ddcz4CK67iXIsFxj5SdTrt0EVC0Lg18d54HaIhKXfDoqGuegwJeV3A7yyenFvOzFeUJaZdQ=19AFFA8E"
DOMAIN = os.getenv("CONFLUENCE_DOMAIN") or "cloud-osci.atlassian.net"
DEFAULT_PAGE_ID = "24460984448"  # 기본 Confluence 페이지 ID
# =================================================

def create_multipart_formdata(file_path):
    """
    내장 라이브러리만을 사용하여 Confluence API용 multipart/form-data 바디를 직접 구성합니다.
    """
    boundary = "----ConfluenceMultipartBoundary"
    filename = os.path.basename(file_path)
    
    # MIME 타입 감지
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"
        
    with open(file_path, "rb") as f:
        file_content = f.read()
        
    body = []
    
    # 1. 파일 필드 추가
    body.append(f"--{boundary}".encode("utf-8"))
    body.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode("utf-8"))
    body.append(f"Content-Type: {mime_type}".encode("utf-8"))
    body.append(b"")
    body.append(file_content)
    
    # 2. 코멘트 필드 추가
    body.append(f"--{boundary}".encode("utf-8"))
    body.append('Content-Disposition: form-data; name="comment"'.encode("utf-8"))
    body.append(b"")
    body.append("Uploaded via send_file.py script".encode("utf-8"))
    
    # 3. 종료 경계선 추가
    body.append(f"--{boundary}--".encode("utf-8"))
    body.append(b"")
    
    payload = b"\r\n".join(body)
    content_type = f"multipart/form-data; boundary={boundary}"
    
    return payload, content_type

def upload_file_to_confluence(file_path, page_id):
    if not os.path.exists(file_path):
        print(f"오류: 업로드할 파일('{file_path}')이 존재하지 않습니다.")
        return

    filename = os.path.basename(file_path)
    
    # Basic Authentication 인코딩
    auth_str = f"{EMAIL}:{API_TOKEN}"
    auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    ssl_context = ssl._create_unverified_context()
    
    # 1. 기존 파일 존재 여부 및 ID 조회 (v2 API 사용)
    headers_get = {
        "Authorization": f"Basic {auth_encoded}"
    }
    list_url = f"https://{DOMAIN}/wiki/api/v2/pages/{page_id}/attachments?limit=250"
    req_list = urllib.request.Request(list_url, headers=headers_get)
    
    attachment_id = None
    print(f"[{page_id}] 페이지에서 기존 '{filename}' 파일 존재 여부 확인 중...")
    try:
        with urllib.request.urlopen(req_list, context=ssl_context) as res:
            data = json.loads(res.read().decode("utf-8"))
            results = data.get("results", [])
            for att in results:
                if att.get("title") == filename:
                    attachment_id = att.get("id")
                    break
    except Exception as e:
        print("기존 첨부파일 조회 실패 (신규 파일로 간주하고 진행):", e)

    # 2. 업로드 엔드포인트 URL 결정 (Confluence Cloud PUT create-or-update 지원 + 기존 삭제 후 재업로드 폴백)
    if attachment_id:
        print(f"기존 첨부파일 ID({attachment_id})를 확인했습니다. 기존 파일을 교체합니다...")
        delete_url = f"https://{DOMAIN}/wiki/rest/api/content/{attachment_id}"
        req_del = urllib.request.Request(
            delete_url,
            headers={"Authorization": f"Basic {auth_encoded}", "X-Atlassian-Token": "nocheck"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(req_del, context=ssl_context):
                pass
        except Exception:
            pass
    else:
        print("기존에 등록된 동일 파일명이 없습니다. 신규 첨부파일로 업로드합니다...")
    url = f"https://{DOMAIN}/wiki/rest/api/content/{page_id}/child/attachment"

    # 3. Multipart 데이터 본문 생성
    payload, content_type = create_multipart_formdata(file_path)
    
    headers_post = {
        "Authorization": f"Basic {auth_encoded}",
        "Content-Type": content_type,
        "X-Atlassian-Token": "nocheck"  # XSRF 체크 바이패스 필수 헤더
    }
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers_post,
        method="POST"
    )
    
    print(f"Confluence 페이지로 '{filename}' 업로드 시작...")
    try:
        with urllib.request.urlopen(req, context=ssl_context) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            results = res_data.get("results", [])
            if not results and "id" in res_data:
                results = [res_data]
                
            if results and len(results) > 0:
                attachment = results[0]
                print("\n========================================")
                print("Confluence 파일 업로드 성공!")
                print(f"- 파일명: {attachment.get('title')}")
                print(f"- 첨부파일 ID: {attachment.get('id')}")
                print(f"- 페이지 ID: {page_id}")
                
                # 버전 정보 출력
                ver = attachment.get("version", {})
                ver_num = ver.get("number", 1) if isinstance(ver, dict) else "N/A"
                print(f"- 업로드 버전: {ver_num}")
                print("========================================")
            else:
                print("성공 응답은 받았으나 상세 파일 정보가 없습니다.")
    except Exception as e:
        error_msg = e.read().decode("utf-8") if hasattr(e, "read") else e
        print("Confluence 업로드 실패:", error_msg)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        print("사용법: ./send_file.py <전송할_파일1> [파일2 ...] [--page-id <페이지ID>]")
        print(f"예시: ./send_file.py step2_query_runner.py jeditor_mysql_queries.sql")
        print(f"예시: ./send_file.py test.txt (기본 페이지 {DEFAULT_PAGE_ID}로 업로드)")
        print(f"예시: ./send_file.py test.txt 12345678 (커스텀 페이지 12345678로 업로드)")
        sys.exit(1)

    target_page = DEFAULT_PAGE_ID
    target_files = []

    args = sys.argv[1:]
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg in ("--page-id", "-p") and idx + 1 < len(args):
            target_page = args[idx + 1]
            idx += 2
        elif arg.startswith("--page-id="):
            target_page = arg.split("=", 1)[1]
            idx += 1
        else:
            target_files.append(arg)
            idx += 1

    # 마지막 인자가 파일이 아니고 숫자라면 페이지 ID로 처리 (기존 2개 인자 호환: send_file.py <file> <page_id>)
    if len(target_files) >= 2 and target_files[-1].isdigit() and not os.path.exists(target_files[-1]):
        target_page = target_files.pop()

    for fpath in target_files:
        upload_file_to_confluence(fpath, target_page)
