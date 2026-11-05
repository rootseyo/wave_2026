import urllib.request
import json
import base64
import ssl
import re
import uuid
import auth

# 1. 설정
PAGE_ID = "24628985980"
DOMAIN = auth.DOMAIN
EMAIL = auth.EMAIL
API_TOKEN = auth.API_TOKEN

ctx = ssl._create_unverified_context()
auth_str = f"{EMAIL}:{API_TOKEN}"
headers = {
    "Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}",
    "Content-Type": "application/json"
}

# 2. 최신 페이지 조회
get_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req_get = urllib.request.Request(get_url, headers=headers)

with urllib.request.urlopen(req_get, context=ctx) as res:
    page_data = json.loads(res.read().decode("utf-8"))

current_version = page_data.get("version", {}).get("number", 1)
current_title = page_data.get("title", "12-1.1.1-21. Jeditor")
body = page_data.get("body", {}).get("storage", {}).get("value", "")

print(f"현재 페이지 버전: {current_version}")
print(f"페이지 제목: {current_title}")

# 3. 대상 섹션 및 테이블 추출
idx = body.find("대상</h2>")
if idx == -1:
    idx = body.find("대상</h2>")
if idx == -1:
    raise ValueError("'대상' 헤더를 찾을 수 없습니다.")

t_start = body.find("<table", idx)
if t_start == -1:
    raise ValueError("대상 헤더 하위 테이블을 찾을 수 없습니다.")

pos = t_start
depth = 0
t_end = -1
while pos < len(body):
    if body.startswith("<table", pos):
        depth += 1
        pos += 6
    elif body.startswith("</table>", pos):
        depth -= 1
        pos += 8
        if depth == 0:
            t_end = pos
            break
    else:
        pos += 1

if t_end == -1:
    raise ValueError("테이블 종료 태그(</table>)를 찾을 수 없습니다.")

table_html = body[t_start:t_end]

# 4. colgroup 및 direct tr 추출
cg_start = table_html.find("<colgroup>")
cg_end = table_html.find("</colgroup>") + len("</colgroup>")
colgroup_html = table_html[cg_start:cg_end]

trs = []
pos = 0
cur_tr_start = -1
tbl_depth = 0

while pos < len(table_html):
    if table_html.startswith("<table", pos):
        tbl_depth += 1
        pos += 6
    elif table_html.startswith("</table>", pos):
        tbl_depth -= 1
        pos += 8
    elif table_html.startswith("<tr", pos):
        if tbl_depth == 1:
            cur_tr_start = pos
        pos += 3
    elif table_html.startswith("</tr>", pos):
        if tbl_depth == 1 and cur_tr_start != -1:
            trs.append(table_html[cur_tr_start:pos+5])
            cur_tr_start = -1
        pos += 5
    else:
        pos += 1

if len(trs) < 2:
    raise ValueError(f"테이블 행 개수가 너무 적습니다: {len(trs)}")

header_tr = trs[0]
data_trs = trs[1:]
print(f"추출된 전체 행 개수: {len(data_trs)}행")

# 5. 세 그룹으로 분류
review_trs = []
o_trs = []
error_trs = []

for tr in data_trs:
    if "검토 필요" in tr:
        review_trs.append(tr)
    elif "설정 값 오류" in tr:
        error_trs.append(tr)
    else:
        o_trs.append(tr)

print(f"분류 결과 - 검토 필요: {len(review_trs)}건, 대상 O: {len(o_trs)}건, 설정 값 오류: {len(error_trs)}건")

# 6. 세 개의 테이블 HTML 생성
def build_table(title, rows):
    table_id = str(uuid.uuid4())
    res = f'<h3>{title}</h3>\n'
    res += f'<table data-table-width="1746" data-layout="align-start" ac:local-id="{table_id}">\n'
    res += f'{colgroup_html}\n<tbody>\n{header_tr}\n'
    for i, r in enumerate(rows, 1):
        # 행별 numberingColumn 순번 업데이트
        r_renumbered = re.sub(r'<td class="numberingColumn">.*?</td>', f'<td class="numberingColumn">{i}</td>', r)
        res += r_renumbered + '\n'
    res += '</tbody>\n</table>\n<p />\n'
    return res

new_tables_html = (
    build_table("1. Migration 검토 필요 (3개)", review_trs) +
    build_table("2. 마이그레이션 대상 O (43개)", o_trs) +
    build_table("3. 설정 값 오류 (13개)", error_trs)
)

new_body = body[:t_start] + new_tables_html + body[t_end:]

# 7. 페이지 업데이트 API 호출
put_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}"
payload = {
    "id": PAGE_ID,
    "status": "current",
    "title": current_title,
    "body": {
        "representation": "storage",
        "value": new_body
    },
    "version": {
        "number": current_version + 1,
        "message": "대상 테이블을 3개(검토 필요, 마이그레이션 대상 O, 설정 값 오류)로 분리 업데이트"
    }
}

req_put = urllib.request.Request(
    put_url,
    data=json.dumps(payload).encode("utf-8"),
    headers=headers,
    method="PUT"
)

try:
    with urllib.request.urlopen(req_put, context=ctx) as put_res:
        result = json.loads(put_res.read().decode("utf-8"))
        new_ver = result.get("version", {}).get("number")
        print(f"✅ Confluence 페이지가 성공적으로 업데이트되었습니다! (새 버전: v{new_ver})")
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
    print(f"❌ 페이지 업데이트 실패: HTTP {e.code} - {err_body}")
except Exception as e:
    print(f"❌ 오류 발생: {e}")
