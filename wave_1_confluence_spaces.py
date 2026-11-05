import urllib.request
import json
import base64
import re
import os

# ================= 변수 설정 구역 =================
EMAIL = "seyeon.kim@osci.kr"
# 주의: API 토큰은 노출되지 않도록 안전하게 관리해 주세요.
API_TOKEN = "ATATT3xFfGF0O5NI20CZCCuF_oOX91r3crK81YMdUN6c0X8AKpJXBrvu4GKQ_1tSyaOzvONtgSVbFLtHtXat7dsDl8Y8mQe9noplWBB2p6U-LF1CZUKLnDwKKlWfH5dzKrL0TyjWZZRzWXh-t5-J8ftrkzgevvAdAbp8bF6qM5-hXAmynvr0Cfs=F14829E3"
DOMAIN = "cloud-osci.atlassian.net"
PAGE_ID = "24497094680"
# =================================================

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
    with urllib.request.urlopen(req_page) as res:
        data = json.loads(res.read().decode("utf-8"))
        body = data.get("body", {}).get("storage", {}).get("value", "")
        
        # 첫 번째 테이블만 분리
        tables = re.split(r"</table\s*>", body)
        if tables:
            first_table = tables[0]
            # 행(tr) 단위 추출
            tr_blocks = re.findall(r"<tr.*?>(.*?)</tr>", first_table, re.DOTALL)
            for tr in tr_blocks:
                # 셀(td 또는 th) 단위 추출
                tds = re.findall(r"<t[dh].*?>(.*?)</t[dh]>", tr, re.DOTALL)
                if not tds:
                    continue
                # HTML 태그 제거 및 공백 정돈
                clean_tds = [re.sub(r"<[^>]+>", "", td).strip() for td in tds]
                
                # 3번째 열(인덱스 2)에서 공간 키 추출
                if len(clean_tds) >= 3:
                    space_key = clean_tds[2]
                    # 영문 대소문자, 숫자, 하이픈, 언더바, 물결표로 구성된 공간키 패턴 검증
                    if space_key and re.match(r"^[a-zA-Z0-9_~-]+$", space_key):
                        # 테이블 헤더 등 불필요한 값 제외 필터
                        if space_key not in ["SPACEID", "space_id", "공간키", "Key", "SPACE_ID"]:
                            first_table_keys.append(space_key)
except Exception as e:
    print("페이지 가져오기 실패:", e)
    exit(1)

if not first_table_keys:
    print("추출된 공간 키가 없습니다.")
    exit(0)

print(f"총 {len(first_table_keys)}개의 공간 키 추출 성공.")

# 3. 기존 등록된 댓글(footer-comments) 삭제
comments_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}/footer-comments"
req_comments = urllib.request.Request(comments_url, headers=headers)

try:
    with urllib.request.urlopen(req_comments) as res:
        comments_data = json.loads(res.read().decode("utf-8"))
        for comment in comments_data.get("results", []):
            comment_id = comment.get("id")
            del_url = f"https://{DOMAIN}/wiki/api/v2/footer-comments/{comment_id}"
            req_del = urllib.request.Request(del_url, method="DELETE", headers=headers)
            try:
                with urllib.request.urlopen(req_del) as del_res:
                    print(f"기존 댓글 삭제 완료 (ID: {comment_id})")
            except Exception as e_del:
                print(f"기존 댓글 삭제 실패 (ID: {comment_id}):", e_del)
except Exception as e_c:
    print("기존 댓글 확인 중 오류 발생:", e_c)

# 4. 코드 블록 스타일로 새 댓글 등록
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
        with urllib.request.urlopen(req_post) as post_res:
            if post_res.status in [200, 201]:
                print(f"성공적으로 새 댓글이 등록되었습니다. (내용 일부: {content[:30]}...)")
    except Exception as e_post:
        error_msg = e_post.read().decode("utf-8") if hasattr(e_post, "read") else e_post
        print("새 댓글 등록 실패:", error_msg)

post_url = f"https://{DOMAIN}/wiki/api/v2/footer-comments"

# 1) 줄바꿈(new line) 형태 댓글
keys_text_nl = "\n".join(first_table_keys)
print("1. 줄바꿈 형태 댓글 등록 중...")
post_comment(keys_text_nl)

# 2) 'key', 'key' 형태 댓글
keys_text_comma = ", ".join(f"'{key}'" for key in first_table_keys)
print("2. 'key', 'key' 형태 댓글 등록 중...")
post_comment(keys_text_comma)

# 3) confluence_spaces.txt 파일 업데이트
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    spaces_txt_path = os.path.join(script_dir, "confluence_spaces.txt")
    with open(spaces_txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(first_table_keys) + "\n")
    print(f"성공적으로 confluence_spaces.txt 파일에 {len(first_table_keys)}개의 공간 키가 업데이트되었습니다.")
except Exception as e:
    print("confluence_spaces.txt 파일 업데이트 실패:", e)
