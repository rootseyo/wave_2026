import urllib.request
import json
import base64
import re
import ssl
import html

# 1. Confluence API 설정
EMAIL = "seyeon.kim@osci.kr"
API_TOKEN = "ATATT3xFfGF0O5NI20CZCCuF_oOX91r3crK81YMdUN6c0X8AKpJXBrvu4GKQ_1tSyaOzvONtgSVbFLtHtXat7dsDl8Y8mQe9noplWBB2p6U-LF1CZUKLnDwKKlWfH5dzKrL0TyjWZZRzWXh-t5-J8ftrkzgevvAdAbp8bF6qM5-hXAmynvr0Cfs=F14829E3"
DOMAIN = "cloud-osci.atlassian.net"
PAGE_ID = "24570527907"

REPORT_PATH = "/Users/sean/Downloads/step1_box_report (1).html"

# 2. HTML 리포트 파싱
with open(REPORT_PATH, "r", encoding="utf-8") as f:
    report_html = f.read()

tables = re.findall(r'<table[^>]*>(.*?)</table>', report_html, re.DOTALL)
t3 = tables[3]
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', t3, re.DOTALL)

target_boxes = []
for r in rows[1:]:
    tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
    links = re.findall(r'<a\s+href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', r)
    
    verify_status = re.sub(r'<[^>]+>', '', tds[13]).strip()
    status = re.sub(r'<[^>]+>', '', tds[4]).strip()
    
    if "이관 대상" in verify_status or "OPEN (이관)" in status:
        box_id = re.sub(r'<[^>]+>', '', tds[1]).strip()
        box_name = re.sub(r'<[^>]+>', '', tds[2]).strip()
        btype = re.sub(r'<[^>]+>', '', tds[3]).strip()
        start_date = re.sub(r'<[^>]+>', '', tds[5]).strip()
        end_date = re.sub(r'<[^>]+>', '', tds[6]).strip()
        owner_name = re.sub(r'<[^>]+>', '', tds[7]).strip()
        owner_email = re.sub(r'<[^>]+>', '', tds[8]).strip()
        
        proj_badges = re.findall(r'<span[^>]*class=[\"\'][^\"\']*badge-project[^\"\']*[\"\'][^>]*>(.*?)</span>', tds[9])
        if not proj_badges:
            proj_str = re.sub(r'<[^>]+>', '', tds[9]).strip()
        else:
            proj_str = ", ".join(proj_badges)
            
        jql_status = re.sub(r'<[^>]+>', '', tds[12]).strip()
        v_status = re.sub(r'<[^>]+>', '', tds[13]).strip()
        
        gantt_url = links[0][0] if len(links) > 0 else f"https://jira.hmg-corp.io/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/g"
        bp_url = links[1][0] if len(links) > 1 else f"https://jira.hmg-corp.io/secure/BigPicture.jspa?boxId={box_id}#box/{box_id}"
        
        type_label = "Program" if box_id.startswith("PROG") else ("Iteration" if box_id.startswith("ITER") else "Agile")
        
        target_boxes.append({
            "no": len(target_boxes) + 1,
            "box_id": box_id,
            "box_name": box_name,
            "type_label": type_label,
            "start_date": start_date,
            "end_date": end_date,
            "owner_name": owner_name,
            "owner_email": owner_email,
            "projects": proj_str,
            "jql_status": jql_status,
            "verify_status": v_status,
            "gantt_url": gantt_url,
            "bp_url": bp_url
        })

print(f"추출된 이관 대상 Box: {len(target_boxes)}개")

# 3. Confluence Storage Format 블록 생성
summary_html = """
<p />
<h4>1. BigGantt Box 이관 검증 및 대상 요약</h4>
<table data-layout="default">
  <colgroup>
    <col style="width: 220.0px;" />
    <col style="width: 100.0px;" />
    <col style="width: 440.0px;" />
  </colgroup>
  <thead>
    <tr>
      <th><p><strong>항목 구분</strong></p></th>
      <th><p><strong>Box 수</strong></p></th>
      <th><p><strong>세부 설명 및 조치 사항</strong></p></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><p><strong>검증 대상 전체 Box</strong></p></td>
      <td><p><strong>137건</strong></p></td>
      <td><p>projects.txt (626개 연동 프로젝트) 기준 BigGantt 검증 대상 전체 Box</p></td>
    </tr>
    <tr>
      <td><p><strong><span style="color: rgb(0, 135, 90);">🚀 정상 이관 대상 Box</span></strong></p></td>
      <td><p><strong>51건</strong></p></td>
      <td><p>OPEN 상태 Box (Program: 25건, Iteration: 23건, Agile: 3건)</p></td>
    </tr>
    <tr>
      <td><p><strong><span style="color: rgb(222, 53, 11);">🔒 Closed Box (제외)</span></strong></p></td>
      <td><p><strong>85건</strong></p></td>
      <td><p>종료(Closed) 상태로 마이그레이션 대상에서 제외 (Iteration: 84건, Program: 1건)</p></td>
    </tr>
    <tr>
      <td><p><strong><span style="color: rgb(222, 53, 11);">📭 JQL 0건 Box (제외)</span></strong></p></td>
      <td><p><strong>1건</strong></p></td>
      <td><p>OPEN 상태이나 JQL 검색 결과 0건으로 내용이 없어 이관 제외 (PROG-125)<br /><em>* 참고: Closed Box 중 PROG-292도 JQL 0건이나 이미 Closed(85건)에 포함</em></p></td>
    </tr>
    <tr>
      <td><p><strong><span style="color: rgb(255, 153, 0);">⚠️ JQL 오류 및 이상</span></strong></p></td>
      <td><p><strong>0건 (오류)<br />1건 (경고)</strong></p></td>
      <td><p>• JQL 문법 오류: <strong>0건</strong><br />• JQL 경고: <strong>1건</strong> (PROG-277: 타 프로젝트 CVDEV1 참조, 실제 검색 58건 정상 조회되어 이관 대상 포함)<br />• Scope 이상(404 / 프로젝트 보관됨): <strong>0건</strong><br />• Owner 조회 실패: <strong>0건</strong> / 날짜 이상: <strong>0건</strong></p></td>
    </tr>
    <tr>
      <td><p><strong><span style="color: rgb(0, 82, 204);">✂️ 중복 Box (정제 대상)</span></strong></p></td>
      <td><p><strong>5건</strong></p></td>
      <td><p>2개 명칭에 5개 Box 존재 (모두 이관 대상 51건에 포함)<br />• <code>new Program</code>: 3건 (PROG-397, PROG-533, PROG-587) → 1개 통합 시 2건 감축<br />• <code>New Program</code>: 2건 (PROG-242, PROG-609) → 1개 통합 시 1건 감축<br />→ 명칭 기준 1개로 통합 시 <strong>최대 3건 감축 가능</strong> (유니크 Box: 134건)</p></td>
    </tr>
  </tbody>
</table>

<p />
<h4>2. 중복 Box 세부 감축 분석</h4>
<table data-layout="default">
  <colgroup>
    <col style="width: 180.0px;" />
    <col style="width: 80.0px;" />
    <col style="width: 100.0px;" />
    <col style="width: 400.0px;" />
  </colgroup>
  <thead>
    <tr>
      <th><p><strong>중복 Box 이름</strong></p></th>
      <th><p><strong>현재 수</strong></p></th>
      <th><p><strong>감축 가능</strong></p></th>
      <th><p><strong>해당 Box ID 및 담당자</strong></p></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><p><code>new Program</code></p></td>
      <td><p>3개</p></td>
      <td><p>-2개</p></td>
      <td><p>• PROG-397 (박세연 책임)<br />• PROG-533 (정태훈 책임)<br />• PROG-587 (최수연 연구원)</p></td>
    </tr>
    <tr>
      <td><p><code>New Program</code></p></td>
      <td><p>2개</p></td>
      <td><p>-1개</p></td>
      <td><p>• PROG-242 (김태현 파트장)<br />• PROG-609 (은종민 책임)</p></td>
    </tr>
  </tbody>
</table>

<p />
<h4>3. BigGantt 이관 대상 Box 상세 목록 (총 51건)</h4>
<table data-layout="default">
  <colgroup>
    <col style="width: 45.0px;" />
    <col style="width: 95.0px;" />
    <col style="width: 250.0px;" />
    <col style="width: 85.0px;" />
    <col style="width: 160.0px;" />
    <col style="width: 170.0px;" />
    <col style="width: 150.0px;" />
    <col style="width: 110.0px;" />
    <col style="width: 130.0px;" />
  </colgroup>
  <thead>
    <tr>
      <th><p><strong>No</strong></p></th>
      <th><p><strong>Box ID</strong></p></th>
      <th><p><strong>Box Name</strong></p></th>
      <th><p><strong>Type</strong></p></th>
      <th><p><strong>기간</strong></p></th>
      <th><p><strong>담당자</strong></p></th>
      <th><p><strong>연동 Projects</strong></p></th>
      <th><p><strong>검증 상태</strong></p></th>
      <th><p><strong>Box URL</strong></p></th>
    </tr>
  </thead>
  <tbody>
"""

for item in target_boxes:
    no = item["no"]
    bid = html.escape(item["box_id"])
    bname = html.escape(item["box_name"])
    btype = html.escape(item["type_label"])
    period = html.escape(f"{item['start_date']} ~ {item['end_date']}")
    owner = html.escape(f"{item['owner_name']}")
    projects = html.escape(item["projects"])
    vstatus = html.escape(item["verify_status"])
    gurl = html.escape(item["gantt_url"])
    bpurl = html.escape(item["bp_url"])
    
    row_html = f"""    <tr>
      <td><p>{no}</p></td>
      <td><p><a href="{gurl}">{bid}</a></p></td>
      <td><p>{bname}</p></td>
      <td><p>{btype}</p></td>
      <td><p>{period}</p></td>
      <td><p>{owner}</p></td>
      <td><p>{projects}</p></td>
      <td><p>{vstatus}</p></td>
      <td><p><a href="{gurl}">BigGantt 🔗</a> | <a href="{bpurl}">BP 🔗</a></p></td>
    </tr>\n"""
    summary_html += row_html

summary_html += """  </tbody>
</table>
<p />
"""

# 4. 기존 페이지 가져와서 업데이트
auth_str = f"{EMAIL}:{API_TOKEN}"
auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
headers = {
    "Authorization": f"Basic {auth_encoded}",
    "Content-Type": "application/json"
}
ctx = ssl._create_unverified_context()

get_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req_get = urllib.request.Request(get_url, headers=headers)

with urllib.request.urlopen(req_get, context=ctx) as res:
    page_data = json.loads(res.read().decode("utf-8"))

current_version = page_data.get("version", {}).get("number", 1)
current_title = page_data.get("title", "12-1.1.1-19. BigGantt")
current_body = page_data.get("body", {}).get("storage", {}).get("value", "")

print(f"현재 페이지 버전: {current_version}")

# 기존 세부분석 아래 리스트 교체
target_pattern = r'<ul local-id="418138c1d5ea">.*?</ul>'
if re.search(target_pattern, current_body, re.DOTALL):
    new_body = re.sub(target_pattern, summary_html, current_body, flags=re.DOTALL)
else:
    # 세부분석 헤딩 뒤에 삽입
    new_body = current_body.replace(
        '<h3 local-id="42afeaceaa75">세부분석</h3>',
        '<h3 local-id="42afeaceaa75">세부분석</h3>' + summary_html
    )

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
        "message": "BigGantt 이관 대상 분석 및 Box 목록 추가"
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
        print(f"✅ 성공적으로 페이지가 업데이트되었습니다! 새 버전: {result.get('version', {}).get('number')}")
except urllib.error.HTTPError as e:
    err_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
    print(f"❌ 페이지 업데이트 실패: HTTP {e.code} - {err_body}")
except Exception as e:
    print(f"❌ 오류 발생: {e}")
