import urllib.request
import json
import base64
import re
import os
import ssl
import html

# ================= 변수 설정 구역 =================
try:
    from auth import EMAIL, API_TOKEN, DOMAIN
except ImportError:
    EMAIL = "seyeon.kim@osci.kr"
    # 주의: API 토큰은 노출되지 않도록 안전하게 관리해 주세요.
    API_TOKEN = "ATATT3xFfGF0jM1WZ_jKnK5hUHrMcjg8-SeHFT4iYxLRd_KzSbrosMQBv5OmEbjZx9CinsCueZsdq8XPZF6jbL3CiZYZwveY2rSocdzS09UH7bJ9ddcz4CK67iXIsFxj5SdTrt0EVC0Lg18d54HaIhKXfDoqGuegwJeV3A7yyenFvOzFeUJaZdQ=19AFFA8E"
    DOMAIN = "cloud-osci.atlassian.net"
PAGE_ID = "24510726184"
# =================================================


def create_ssl_context():
    """Create an SSL context without certificate verification."""
    return ssl._create_unverified_context()


SSL_CONTEXT = create_ssl_context()

# 1. 인증 헤더 구성
auth_str = f"{EMAIL}:{API_TOKEN}"
auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
headers = {
    "Authorization": f"Basic {auth_encoded}",
    "Content-Type": "application/json"
}

# 2. Confluence 페이지 본문 가져오기
print(f"[{PAGE_ID}] 페이지 정보를 가져오는 중...")
page_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req_page = urllib.request.Request(page_url, headers=headers)

first_table_keys = []
try:
    with urllib.request.urlopen(req_page, context=SSL_CONTEXT) as res:
        data = json.loads(res.read().decode("utf-8"))
        body = data.get("body", {}).get("storage", {}).get("value", "")
        
        # 첫 번째 테이블만 분리
        tables = re.split(r"</table\s*>", body)
        if tables:
            first_table = tables[0]
            # 행(tr) 단위 추출
            tr_blocks = re.findall(r"<tr.*?>(.*?)</tr>", first_table, re.DOTALL)
            for tr in tr_blocks:
                # 셀(td) 단위 추출
                tds = re.findall(r"<td.*?>(.*?)</td>", tr, re.DOTALL)
                if not tds:
                    continue
                # HTML 태그 제거 및 공백 정돈
                clean_tds = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
                
                # 3번째 열(인덱스 2)에서 프로젝트 키 추출
                if len(clean_tds) >= 3:
                    proj_key = clean_tds[2]
                    # 대문자 영문, 숫자, 하이픈, 언더바로 구성된 프로젝트 키 패턴 검증
                    if proj_key and re.match(r"^[A-Z0-9_-]+$", proj_key):
                        # 테이블 헤더 등 불필요한 값 제외 필터
                        if proj_key not in ["환경", "PROG Boxes", "AGILE Boxes", "ITER Boxes", "DC", "Cloud", "차이"]:
                            first_table_keys.append(proj_key)
except Exception as e:
    print("페이지 가져오기 실패:", e)
    exit(1)

if not first_table_keys:
    print("추출된 프로젝트 키가 없습니다.")
    exit(0)

print(f"총 {len(first_table_keys)}개의 프로젝트 키 추출 성공.")

def extract_keys_from_comment_body(body_value):
    """댓글 본문(storage 포맷)에서 프로젝트 키 목록을 추출합니다."""
    if not body_value:
        return []
    # CDATA 블록 내부 텍스트 추출 시도
    cdata_match = re.search(r"<!\[CDATA\[(.*?)\]\]>", body_value, re.DOTALL)
    if cdata_match:
        text = cdata_match.group(1)
    else:
        text = html.unescape(re.sub(r"<[^>]+>", "", body_value))

    keys = []
    for item in re.split(r"[\r\n,]+", text):
        clean_item = item.strip().strip("'\"").strip()
        if clean_item and re.match(r"^[A-Z0-9_-]+$", clean_item):
            if clean_item not in ["환경", "PROG Boxes", "AGILE Boxes", "ITER Boxes", "DC", "Cloud", "차이", "KEY", "PROJECT_KEY", "Project Key"]:
                keys.append(clean_item)
    return keys


# 3. 기존 등록된 댓글(footer-comments) 조회, 이전 프로젝트 키 추출 및 삭제
prev_keys = []
comments_to_delete = []
next_comments_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}/footer-comments?body-format=storage&limit=50"

print(f"[{PAGE_ID}] 기존 댓글 확인 및 이전 프로젝트 키 수집 중...")
try:
    while next_comments_url:
        req_comments = urllib.request.Request(next_comments_url, headers=headers)
        with urllib.request.urlopen(req_comments, context=SSL_CONTEXT) as res:
            comments_data = json.loads(res.read().decode("utf-8"))
            results = comments_data.get("results", [])
            for comment in results:
                comment_id = comment.get("id")
                if comment_id:
                    comments_to_delete.append(comment_id)
                body_val = comment.get("body", {}).get("storage", {}).get("value", "")
                extracted = extract_keys_from_comment_body(body_val)
                for k in extracted:
                    if k not in prev_keys:
                        prev_keys.append(k)

            # 페이징 처리 (_links.next)
            next_link = comments_data.get("_links", {}).get("next")
            if next_link:
                if next_link.startswith("http"):
                    next_comments_url = next_link
                else:
                    next_comments_url = f"https://{DOMAIN}{next_link}"
            else:
                next_comments_url = None

    if prev_keys:
        print(f"기존 댓글에서 총 {len(prev_keys)}개의 이전 프로젝트 키 확인됨.")
    else:
        print("기존 댓글에서 확인된 이전 프로젝트 키가 없습니다.")

    for comment_id in comments_to_delete:
        del_url = f"https://{DOMAIN}/wiki/api/v2/footer-comments/{comment_id}"
        req_del = urllib.request.Request(del_url, method="DELETE", headers=headers)
        try:
            with urllib.request.urlopen(req_del, context=SSL_CONTEXT) as del_res:
                print(f"기존 댓글 삭제 완료 (ID: {comment_id})")
        except Exception as e_del:
            print(f"기존 댓글 삭제 실패 (ID: {comment_id}):", e_del)
except Exception as e_c:
    print("기존 댓글 확인 중 오류 발생:", e_c)

# 4. 코드 블록 스타일로 새 댓글 등록
post_url = f"https://{DOMAIN}/wiki/api/v2/footer-comments"


def post_comment(content):
    comment_body = f"""<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="theme">RText</ac:parameter>
  <ac:parameter ac:name="linenumbers">true</ac:parameter>
  <ac:plain-text-body><![CDATA[{content}]]></ac:plain-text-body>
</ac:structured-macro>"""

    payload = {
        "pageId": PAGE_ID,
        "body": {
            "representation": "storage",
            "value": comment_body
        }
    }

    req_post = urllib.request.Request(
        post_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req_post, context=SSL_CONTEXT) as post_res:
            if post_res.status in [200, 201]:
                print(f"성공적으로 새 댓글이 등록되었습니다. (내용 일부: {content[:30]}...)")
    except Exception as e_post:
        error_msg = e_post.read().decode("utf-8") if hasattr(e_post, "read") else e_post
        print("새 댓글 등록 실패:", error_msg)

# 1) 줄바꿈(new line) 형태 댓글
keys_text_nl = "\n".join(first_table_keys)
print("1. 줄바꿈 형태 댓글 등록 중...")
post_comment(keys_text_nl)

# 2) key, key 형태 댓글
keys_text_comma = ", ".join(first_table_keys)
print("2. key, key 형태 댓글 등록 중...")
post_comment(keys_text_comma)

# 3) projects.txt 파일 업데이트
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    projects_txt_path = os.path.join(script_dir, "projects.txt")
    with open(projects_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(first_table_keys) + "\n")
    print(f"성공적으로 projects.txt 파일에 {len(first_table_keys)}개의 프로젝트 키가 업데이트되었습니다.")
except Exception as e:
    print("projects.txt 파일 업데이트 실패:", e)

# 5. 이전 댓글과 업데이트된 댓글 간 프로젝트 차이 비교 및 콘솔 로그 출력
unique_new_keys = list(dict.fromkeys(first_table_keys))
prev_keys_set = set(prev_keys)
new_keys_set = set(unique_new_keys)

added_keys = [k for k in unique_new_keys if k not in prev_keys_set]
removed_keys = [k for k in prev_keys if k not in new_keys_set]

print("\n" + "=" * 60)
print("📊 [이전 댓글 vs 업데이트된 댓글 프로젝트 비교 결과]")
print("=" * 60)
if not comments_to_delete and not prev_keys:
    print("ℹ️  이전 댓글이 존재하지 않았습니다.")
    print(f"- 새로 등록된 프로젝트 총 {len(unique_new_keys)}개")
else:
    print(f"- 이전 댓글 프로젝트 수      : {len(prev_keys)}개")
    print(f"- 업데이트된 댓글 프로젝트 수: {len(unique_new_keys)}개")
    print(f"- 추가된 프로젝트 (+)        : {len(added_keys)}개")
    print(f"- 제외(삭제)된 프로젝트 (-)  : {len(removed_keys)}개")
    print("-" * 60)

    if not added_keys and not removed_keys:
        print("✅ 변경 사항 없음: 이전 댓글과 업데이트된 댓글의 프로젝트 목록이 동일합니다.")
    else:
        if added_keys:
            print(f"➕ 새로 추가된 프로젝트 ({len(added_keys)}개):")
            for k in added_keys:
                print(f"  + {k}")
        else:
            print("➕ 새로 추가된 프로젝트: 없음")

        if removed_keys:
            print(f"\n➖ 제외(삭제)된 프로젝트 ({len(removed_keys)}개):")
            for k in removed_keys:
                print(f"  - {k}")
        else:
            print("\n➖ 제외(삭제)된 프로젝트: 없음")
print("=" * 60 + "\n")
