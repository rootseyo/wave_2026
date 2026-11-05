# -*- coding: utf-8 -*-
import urllib.request
import json
import base64
import ssl
import sys

EMAIL = "seyeon.kim@osci.kr"
API_TOKEN = "ATATT3xFfGF0O5NI20CZCCuF_oOX91r3crK81YMdUN6c0X8AKpJXBrvu4GKQ_1tSyaOzvONtgSVbFLtHtXat7dsDl8Y8mQe9noplWBB2p6U-LF1CZUKLnDwKKlWfH5dzKrL0TyjWZZRzWXh-t5-J8ftrkzgevvAdAbp8bF6qM5-hXAmynvr0Cfs=F14829E3"
DOMAIN = "cloud-osci.atlassian.net"
PAGE_ID = "24427561136"

ctx = ssl._create_unverified_context()
auth_str = f"{EMAIL}:{API_TOKEN}"
auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
headers = {
    "Authorization": f"Basic {auth_encoded}",
    "Content-Type": "application/json"
}

# 1. Get current page details (version, title)
get_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req = urllib.request.Request(get_url, headers=headers)

with urllib.request.urlopen(req, context=ctx) as res:
    page_data = json.loads(res.read().decode("utf-8"))

current_version = page_data["version"]["number"]
page_title = page_data["title"]
print(f"Current Page: '{page_title}', Version: {current_version}")

# Load parsed migration data
with open("parsed_migration_data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Build Confluence Storage Format Body
storage_body = f"""<h1>26Wave2-JIRA-T1-1 마이그레이션 진단 &amp; 첨부파일 종합 분석 보고서</h1>

<ac:structured-macro ac:name="warning" ac:schema-version="1">
  <ac:parameter ac:name="title">마이그레이션 결손 주의 (총 152건 첨부파일 미이관)</ac:parameter>
  <ac:rich-text-body>
    <p>본 마이그레이션 작업(<code>26Wave2-JIRA-T1-1</code>) 결과, 7개 대상 프로젝트에서 <strong>총 152건의 첨부파일 미마이그레이션</strong>이 확인되었습니다.</p>
    <ul>
      <li><strong>DB 타임아웃 Export 실패 (JCMA 401): 10건</strong> ➔ MySQL 커넥션 단절로 인한 실패 (<strong>JCMA '실패 첨부파일 재시도'로 즉시 복구 가능</strong>)</li>
      <li><strong>서버 물리 파일 유실 (JCMA 402): 142건</strong> ➔ Jira Server 디스크 내 원본 바이너리 부재 (<strong>과거 유실된 고아 레코드</strong>)</li>
    </ul>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>1. 마이그레이션 개요</h2>
<table>
  <tbody>
    <tr>
      <th style="background-color: #f4f5f7; width: 200px;"><strong>작업 플랜명</strong></th>
      <td><code>26Wave2-JIRA-T1-1</code></td>
      <th style="background-color: #f4f5f7; width: 200px;"><strong>마이그레이션 세션 ID</strong></th>
      <td><code>{data['plan_id']}</code></td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>작업 수행 기간 (추정)</strong></th>
      <td>2026-09-23 18:29:25 ~ 2026-09-24 11:49:11 (약 18~24시간 소요)</td>
      <th style="background-color: #f4f5f7;"><strong>결과 리포트 생성 일시</strong></th>
      <td>{data['report_date']}</td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>대상 프로젝트 (총 7개)</strong></th>
      <td colspan="3"><code>CAQW</code>, <code>CQIM</code>, <code>ECSC</code>, <code>GENESISP</code>, <code>JGVSTAR25</code>, <code>RPSPEC</code>, <code>SPATEST</code></td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>첨부파일 마이그레이션 결과</strong></th>
      <td colspan="3">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Red</ac:parameter>
          <ac:parameter ac:name="title">총 152건 결손</ac:parameter>
        </ac:structured-macro>
        &nbsp;(Export 실패 10건 + 물리 유실 142건)
      </td>
    </tr>
  </tbody>
</table>

<ac:structured-macro ac:name="info" ac:schema-version="1">
  <ac:parameter ac:name="title">마이그레이션 시작 / 종료 시간에 관한 안내</ac:parameter>
  <ac:rich-text-body>
    <p>다운로드된 로그 파일(<code>.log</code>)은 JCMA의 <strong>오류 전용 로그(Error logs)</strong>이므로 마이그레이션의 공식적인 시작/종료 시점은 로그에 직접 기록되지 않습니다.</p>
    <ul>
      <li><strong>최초 에러 감지:</strong> 2026-09-23 18:29:25 (CAQW)</li>
      <li><strong>최종 에러 감지:</strong> 2026-09-24 11:49:11 (RPSPEC)</li>
      <li><strong>최종 리포트 생성:</strong> 2026-09-24 23:24:09</li>
      <li><em>정확한 초 단위 공식 시작/종료 일시는 온프레미스 Jira의 <strong>시스템 &gt; 클라우드 마이그레이션 어시스턴트</strong> 웹 화면에서 플랜의 'Started' 및 'Finished' 항목으로 확인하실 수 있습니다.</em></li>
    </ul>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>2. 프로젝트별 첨부파일 마이그레이션 결과 요약</h2>
<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트 키</th>
      <th style="text-align: center; background-color: #f4f5f7;">Export 실패 (JCMA 401)</th>
      <th style="text-align: center; background-color: #f4f5f7;">디스크 유실 (JCMA 402)</th>
      <th style="text-align: center; background-color: #f4f5f7;">총 결손 건수</th>
      <th style="text-align: center; background-color: #f4f5f7;">최초/최종 오류 시각</th>
      <th style="text-align: center; background-color: #f4f5f7;">상태</th>
    </tr>
  </thead>
  <tbody>
"""

for proj, stat in data['project_stats'].items():
    storage_body += f"""    <tr>
      <td style="text-align: center;"><strong>{proj}</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>{stat['export_errors']}건</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>{stat['missing_count']}건</strong></td>
      <td style="text-align: center;"><strong>{stat['total_issues']}건</strong></td>
      <td style="text-align: center; font-size: 12px;">{stat['first_ts'].split('.')[0] if stat['first_ts'] else '-'}</td>
      <td style="text-align: center;">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Yellow</ac:parameter>
          <ac:parameter ac:name="title">조치 필요</ac:parameter>
        </ac:structured-macro>
      </td>
    </tr>
"""

storage_body += f"""    <tr style="background-color: #fafbfc;">
      <td style="text-align: center;"><strong>합계 (7개 프로젝트)</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>{data['total_failed_export']}건</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>{data['total_missing']}건</strong></td>
      <td style="text-align: center;"><strong>{data['total_problem_attachments']}건</strong></td>
      <td style="text-align: center;">-</td>
      <td style="text-align: center;">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Red</ac:parameter>
          <ac:parameter ac:name="title">152건 결손</ac:parameter>
        </ac:structured-macro>
      </td>
    </tr>
  </tbody>
</table>

<h2>3. [상세] DB 타임아웃 Export 실패 첨부파일 리스트 (JCMA 401 - 총 10건)</h2>
<p>아래 10건은 Jira Server MySQL 데이터베이스의 <code>wait_timeout</code>(유휴 커넥션 단절)으로 인해 메타데이터 조회 중 연결이 끊어져 Export에 실패한 첨부파일 목록입니다. <strong>파일 자체는 디스크에 정상 존재하므로 JCMA에서 '실패 첨부파일 재시도'를 실행하면 즉시 마이그레이션이 완료됩니다.</strong></p>

<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">#</th>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트</th>
      <th style="text-align: center; background-color: #f4f5f7;">이슈 키</th>
      <th style="text-align: center; background-color: #f4f5f7;">첨부파일 ID</th>
      <th style="background-color: #f4f5f7;">파일명</th>
      <th style="text-align: center; background-color: #f4f5f7;">파일 유형</th>
      <th style="text-align: center; background-color: #f4f5f7;">타임아웃 지연시간</th>
      <th style="text-align: center; background-color: #f4f5f7;">오류 발생 일시</th>
    </tr>
  </thead>
  <tbody>
"""

for item in data['failed_attachments']:
    storage_body += f"""    <tr>
      <td style="text-align: center;">{item['id']}</td>
      <td style="text-align: center;"><code>{item['project']}</code></td>
      <td style="text-align: center;"><strong>{item['issue_key']}</strong></td>
      <td style="text-align: center;"><code>{item['attachment_id']}</code></td>
      <td>{item['file_name']}</td>
      <td style="text-align: center;"><code>.{item['extension']}</code></td>
      <td style="text-align: center;">{item['timeout_sec']}초 ({round(item['timeout_sec']/60, 1)}분)</td>
      <td style="text-align: center; font-size: 12px;">{item['timestamp'].split('.')[0]}</td>
    </tr>
"""

storage_body += """  </tbody>
</table>

<h2>4. [상세] 서버 물리 파일 유실 현황 (JCMA 402 - 총 142건)</h2>
<p>Jira DB(<code>fileattachment</code> 테이블)에는 첨부파일 메타데이터가 등록되어 있으나, 온프레미스 Jira 서버 디스크(<code>$JIRA_HOME/data/attachments/</code>)에 실제 파일이 존재하지 않는 건수입니다.</p>

<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트 키</th>
      <th style="text-align: center; background-color: #f4f5f7;">유실 파일 수</th>
      <th style="background-color: #f4f5f7;">발생 일시 및 현황</th>
      <th style="background-color: #f4f5f7;">권장 조치</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="text-align: center;"><strong>CAQW</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>63개</strong></td>
      <td>2026-09-23 18:29:25 감지 / 디스크 내 물리 파일 부재</td>
      <td>과거 NAS/스냅샷 백업 확인 또는 유실 확정 처리</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>CQIM</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>54개</strong></td>
      <td>2026-09-24 03:53:27 감지 / 디스크 내 물리 파일 부재</td>
      <td>과거 NAS/스냅샷 백업 확인 또는 유실 확정 처리</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>GENESISP</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>22개</strong></td>
      <td>2026-09-24 03:37:44 감지 / 디스크 내 물리 파일 부재</td>
      <td>과거 NAS/스냅샷 백업 확인 또는 유실 확정 처리</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>SPATEST</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>3개</strong></td>
      <td>2026-09-24 05:01:18 감지 / 디스크 내 물리 파일 부재</td>
      <td>과거 NAS/스냅샷 백업 확인 또는 유실 확정 처리</td>
    </tr>
  </tbody>
</table>

<ac:structured-macro ac:name="note" ac:schema-version="1">
  <ac:parameter ac:name="title">142건의 이슈 번호 존재 여부 및 확인 방법</ac:parameter>
  <ac:rich-text-body>
    <p><strong>이슈 번호 존재 여부:</strong> Jira 데이터베이스 구조상 첨부파일은 특정 이슈(<code>issueid</code>)에 종속되므로 <strong>142건 모두 온프레미스 Jira DB 상에서는 특정 이슈 번호(예: CAQW-1234 등)가 100% 정상 연결</strong>되어 있습니다.</p>
    <p>다만, JCMA 플러그인이 대량의 로그 발생을 방지하기 위해 개별 이슈 번호 대신 프로젝트 단위의 건수만 <code>Found X missing attachments</code> 경고로 출력했습니다.</p>
    <p>온프레미스 Jira DB에서 142건의 정확한 이슈 번호와 파일명을 확인하려면 아래 SQL을 실행하시면 됩니다:</p>
  </ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="language">sql</ac:parameter>
  <ac:parameter ac:name="title">온프레미스 Jira DB 첨부파일 및 이슈 번호 매핑 쿼리</ac:parameter>
  <ac:plain-text-body><![CDATA[SELECT 
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    fa.ID AS attachment_id,
    fa.FILENAME AS file_name,
    fa.FILESIZE AS file_size_bytes,
    fa.CREATED AS created_date,
    fa.AUTHOR AS author
FROM fileattachment fa
JOIN jiraissue i ON fa.issueid = i.ID
JOIN project p ON i.PROJECT = p.ID
WHERE p.pkey IN ('CAQW', 'CQIM', 'GENESISP', 'SPATEST')
ORDER BY p.pkey, i.issuenum;]]></ac:plain-text-body>
</ac:structured-macro>

<h2>5. 원인 분석 및 해결 방안 (Action Plan)</h2>

<h3>① [JCMA 401] MySQL Connection Timeout 해결 및 10건 재마이그레이션</h3>
<ul>
  <li><strong>원인:</strong> 비디오(.mp4), DLT 로그(.gz) 등 대용량 파일 또는 대기열 처리 중 약 10~17분간 유휴 상태가 발생하여 MySQL의 <code>wait_timeout</code>에 의해 커넥션이 강제 종료됨.</li>
  <li><strong>조치 방안 (전체 재마이그레이션 불필요):</strong>
    <ol>
      <li>온프레미스 MySQL 설정 증설 (<code>my.cnf</code>):
        <pre><code>wait_timeout = 28800
interactive_timeout = 28800
net_read_timeout = 600
net_write_timeout = 600
max_allowed_packet = 256M</code></pre>
      </li>
      <li>Jira <code>dbconfig.xml</code> 커넥션 풀 유효성 검사 활성화 (<code>&lt;validation-query&gt;SELECT 1&lt;/validation-query&gt;</code>)</li>
      <li><strong>JCMA 콘솔에서 실패분만 재시도:</strong> Jira 관리자 &gt; <strong>시스템 &gt; 클라우드 마이그레이션 어시스턴트(JCMA)</strong> &gt; 해당 플랜 우측 <code>...</code> 메뉴 &gt; <strong>"Retry failed attachments (실패한 첨부 파일 재시도)"</strong> 클릭 ➔ <em>전체 마이그레이션을 다시 할 필요 없이 실패했던 10개 파일만 Cloud로 고속 재전송됩니다.</em></li>
    </ol>
  </li>
</ul>

<h3>② [JCMA 402] Missing Attachments (142건) 점검 및 처리 방향</h3>
<ul>
  <li><strong>원인:</strong> 과거 서버 운영 중 스토리지 마운트 분리, 디스크 공간 정리 시 수동 삭제, 과거 백업 복원 누락 등으로 발생한 고아 레코드(Orphan record).</li>
  <li><strong>조치 방안:</strong>
    <ol>
      <li>온프레미스 Jira <strong>시스템 &gt; Support Tools &gt; Instance Health Check</strong>의 <em>"Attachments integrity"</em> 항목 검사 실행</li>
      <li>과거 스냅샷 또는 백업 저장소에서 해당 파일 복원 가능 여부 검토</li>
      <li>복원이 불가능한 경우: 이미 수년 전부터 온프레미스에서도 다운로드가 안 되던 파일이므로, 이슈 메타데이터(본문/댓글/히스토리)는 정상 이전되었음을 확인하고 최종 '유실 승인' 처리 후 마무리</li>
    </ol>
  </li>
</ul>

<h2>6. Global Entities 마이그레이션 결과 (Summary CSV 요약)</h2>
<table>
  <thead>
    <tr>
      <th style="background-color: #f4f5f7;">엔티티 종류 (Entity Type)</th>
      <th style="text-align: center; background-color: #f4f5f7;">마이그레이션 대상</th>
      <th style="text-align: center; background-color: #f4f5f7;">성공 이관</th>
      <th style="text-align: center; background-color: #f4f5f7;">부분 손실</th>
      <th style="text-align: center; background-color: #f4f5f7;">미이관</th>
    </tr>
  </thead>
  <tbody>
"""

for row in data['summary_data']:
    storage_body += f"""    <tr>
      <td><strong>{row['type']}</strong></td>
      <td style="text-align: center;">{row['num_mig']}</td>
      <td style="text-align: center;">{row['success']}</td>
      <td style="text-align: center;">{row['partial_loss']}</td>
      <td style="text-align: center;">{row['not_mig']}</td>
    </tr>
"""

storage_body += """  </tbody>
</table>

<hr />
<h2>7. 첨부파일 목록</h2>
<p local-id="446f8c0f1500" />
<ac:structured-macro ac:name="attachments" ac:schema-version="1" data-layout="wide" ac:local-id="5e74de2e-2f04-421d-8b04-c916b01e9976" ac:macro-id="85f675b3-2fa4-43ef-84cf-06902cc08b9b" />
<p local-id="67d646560244" />
"""

# Update page via REST API v2
update_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}"
payload = {
    "id": PAGE_ID,
    "status": "current",
    "title": page_title,
    "body": {
        "representation": "storage",
        "value": storage_body
    },
    "version": {
        "number": current_version + 1,
        "message": "JCMA 마이그레이션 결과 및 첨부파일 결손 분석 종합 보고서 업데이트"
    }
}

json_data = json.dumps(payload).encode("utf-8")
update_req = urllib.request.Request(update_url, data=json_data, headers=headers, method="PUT")

try:
    with urllib.request.urlopen(update_req, context=ctx) as res:
        res_data = json.loads(res.read().decode("utf-8"))
        print("Successfully updated Confluence page!")
        print("New Version:", res_data.get("version", {}).get("number"))
        print("Page URL:", f"https://{DOMAIN}/wiki/spaces/AC2/pages/{PAGE_ID}")
except urllib.error.HTTPError as e:
    print("HTTP Error:", e.code)
    print(e.read().decode("utf-8"))
    sys.exit(1)
except Exception as e:
    print("Error:", e)
    sys.exit(1)
