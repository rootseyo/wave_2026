#!/usr/bin/env python3
import urllib.request
import json
import base64
import os
import ssl

# ================= 변수 설정 구역 =================
# 환경 변수가 있으면 사용하고 없으면 기본값 사용
EMAIL = os.getenv("CONFLUENCE_EMAIL") or "seyeon.kim@osci.kr"
API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN") or "ATATT3xFfGF0jM1WZ_jKnK5hUHrMcjg8-SeHFT4iYxLRd_KzSbrosMQBv5OmEbjZx9CinsCueZsdq8XPZF6jbL3CiZYZwveY2rSocdzS09UH7bJ9ddcz4CK67iXIsFxj5SdTrt0EVC0Lg18d54HaIhKXfDoqGuegwJeV3A7yyenFvOzFeUJaZdQ=19AFFA8E"
DOMAIN = os.getenv("CONFLUENCE_DOMAIN") or "cloud-osci.atlassian.net"
DEFAULT_PAGE_ID = "24460984448"  # 기본 Confluence 페이지 ID
# =================================================

def download_attachment(target_filename, page_id, download_path):
    # SSL 검증 우회 컨텍스트
    ssl_context = ssl._create_unverified_context()

    # Basic Authentication 인코딩
    auth_str = f"{EMAIL}:{API_TOKEN}"
    auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    # 403 차단 회피를 위한 User-Agent 헤더 추가
    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. 페이지의 첨부파일 목록 조회 (v2 API 사용)
    list_url = f"https://{DOMAIN}/wiki/api/v2/pages/{page_id}/attachments?limit=250"
    req_list = urllib.request.Request(list_url, headers=headers)
    
    download_url = None
    attachment_id = None
    print(f"[{page_id}] 페이지에서 '{target_filename}' 첨부파일을 찾는 중...")
    
    try:
        with urllib.request.urlopen(req_list, context=ssl_context) as res:
            data = json.loads(res.read().decode("utf-8"))
            results = data.get("results", [])
            for att in results:
                if att.get("title") == target_filename:
                    download_url = att.get("downloadLink")
                    attachment_id = att.get("id")
                    break
    except Exception as e:
        print("첨부파일 목록 조회 실패:", e)
        return

    if not download_url or not attachment_id:
        print(f"오류: 페이지에서 '{target_filename}' 첨부파일을 찾을 수 없습니다.")
        return

    # 2. 절대 다운로드 URL 작성
    if not download_url.startswith("/wiki"):
        full_download_url = f"https://{DOMAIN}/wiki{download_url}"
    else:
        full_download_url = f"https://{DOMAIN}{download_url}"
        
    print("다운로드 URL 확인 완료. 파일 다운로드 시작...")

    # 3. 파일 다운로드 및 로컬 저장
    download_success = False
    req_download = urllib.request.Request(full_download_url, headers=headers)
    try:
        with urllib.request.urlopen(req_download, context=ssl_context) as res_file:
            file_data = res_file.read()
            with open(download_path, "wb") as f:
                f.write(file_data)
            print(f"다운로드 성공! 로컬에 '{download_path}' 파일이 저장/덮어쓰기되었습니다.")
            download_success = True
    except Exception as e:
        print("파일 다운로드 실패:", e)

    # 4. 다운로드 성공 시 Confluence에서 첨부파일 삭제
    if download_success:
        delete_url = f"https://{DOMAIN}/wiki/rest/api/content/{attachment_id}"
        del_headers = {**headers, "X-Atlassian-Token": "nocheck"}
        req_delete = urllib.request.Request(delete_url, headers=del_headers, method="DELETE")
        print(f"Confluence 페이지에서 원본 첨부파일 '{target_filename}'(ID: {attachment_id}) 삭제 중...")
        try:
            with urllib.request.urlopen(req_delete, context=ssl_context) as res_del:
                # 204 No Content 혹은 200 OK 등으로 떨어짐
                print("Confluence에서 첨부파일 삭제 완료! (휴지통으로 이동되었습니다.)")
        except Exception as e:
            print("Confluence 첨부파일 삭제 실패:", e)

def download_all_attachments(page_id):
    # SSL 검증 우회 컨텍스트
    ssl_context = ssl._create_unverified_context()

    # Basic Authentication 인코딩
    auth_str = f"{EMAIL}:{API_TOKEN}"
    auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {auth_encoded}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. 페이지의 첨부파일 목록 조회 (v2 API 사용)
    list_url = f"https://{DOMAIN}/wiki/api/v2/pages/{page_id}/attachments?limit=250"
    req_list = urllib.request.Request(list_url, headers=headers)
    
    print(f"[{page_id}] 페이지에서 전체 첨부파일 목록을 조회하는 중...")
    try:
        with urllib.request.urlopen(req_list, context=ssl_context) as res:
            data = json.loads(res.read().decode("utf-8"))
            results = data.get("results", [])
    except Exception as e:
        print("첨부파일 목록 조회 실패:", e)
        return

    if not results:
        print("오류: 페이지에 첨부파일이 존재하지 않습니다.")
        return

    print(f"총 {len(results)}개의 첨부파일을 찾았습니다. 다운로드 및 삭제 작업을 진행합니다.")
    
    for att in results:
        filename = att.get("title")
        download_url = att.get("downloadLink")
        attachment_id = att.get("id")
        
        if not filename or not download_url or not attachment_id:
            continue
            
        # 절대 다운로드 URL 작성
        if not download_url.startswith("/wiki"):
            full_download_url = f"https://{DOMAIN}/wiki{download_url}"
        else:
            full_download_url = f"https://{DOMAIN}{download_url}"
            
        print(f"\n[{filename}] 다운로드 시작...")
        
        # 파일 다운로드 및 로컬 저장
        download_success = False
        req_download = urllib.request.Request(full_download_url, headers=headers)
        try:
            with urllib.request.urlopen(req_download, context=ssl_context) as res_file:
                file_data = res_file.read()
                with open(filename, "wb") as f:
                    f.write(file_data)
                print(f"[{filename}] 다운로드 성공! 로컬에 저장되었습니다.")
                download_success = True
        except Exception as e:
            print(f"[{filename}] 다운로드 실패:", e)

        # 다운로드 성공 시 Confluence에서 첨부파일 삭제
        if download_success:
            delete_url = f"https://{DOMAIN}/wiki/rest/api/content/{attachment_id}"
            del_headers = {**headers, "X-Atlassian-Token": "nocheck"}
            req_delete = urllib.request.Request(delete_url, headers=del_headers, method="DELETE")
            print(f"[{filename}] Confluence 페이지에서 삭제 중 (ID: {attachment_id})...")
            try:
                with urllib.request.urlopen(req_delete, context=ssl_context) as res_del:
                    print(f"[{filename}] Confluence에서 삭제 완료! (휴지통으로 이동되었습니다.)")
            except Exception as e:
                print(f"[{filename}] Confluence 삭제 실패:", e)

if __name__ == "__main__":
    import sys
    
    page = DEFAULT_PAGE_ID
    if len(sys.argv) >= 3:
        page = sys.argv[2]
        
    if len(sys.argv) < 2:
        try:
            choice = input(f"[{page}] 페이지에서 모든 첨부파일을 다운로드하고 삭제하시겠습니까? (y/n) [Default: n]: ").strip().lower()
        except EOFError:
            choice = "n"
            
        if choice in ["y", "yes"]:
            download_all_attachments(page)
            sys.exit(0)
        else:
            print("사용법: ./download.py <다운로드할_첨부파일명> [Confluence_페이지_ID] [저장할_로컬_경로]")
            print(f"예시: ./download.py log.txt (기본 페이지 {DEFAULT_PAGE_ID}에서 다운로드 후 삭제)")
            sys.exit(1)
            
    filename = sys.argv[1]
    save_path = sys.argv[3] if len(sys.argv) >= 4 else filename
    
    download_attachment(filename, page, save_path)
