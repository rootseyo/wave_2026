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

get_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req = urllib.request.Request(get_url, headers=headers)

with urllib.request.urlopen(req, context=ctx) as res:
    page_data = json.loads(res.read().decode("utf-8"))

current_version = page_data["version"]["number"]
page_title = page_data["title"]
print(f"Current Page Version: {current_version}")

with open('unmigrated_1872_data.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

sorted_by_size = sorted(d['items'], key=lambda x: x['file_size_bytes'], reverse=True)

storage_body = f"""<h1>26Wave2-JIRA-T1-1 마이그레이션 진단 &amp; 첨부파일 종합 분석 보고서</h1>

<ac:structured-macro ac:name="info" ac:schema-version="1">
  <ac:parameter ac:name="title">26Wave2-JIRA-T1-1 플랜 결과 요약 (성공률 99.62% / T0 대비 작업시간 비교 반영)</ac:parameter>
  <ac:rich-text-body>
    <p>본 보고서는 마이그레이션 플랜 <strong><code>26Wave2-JIRA-T1-1</code></strong>의 실행 결과 및 첨부파일 이관 상태를 단독 정밀 분석한 보고서입니다.</p>
    <ul>
      <li><strong>플랜 전체 규모:</strong> <strong>624개 프로젝트</strong> / <strong>약 33 TB</strong> (33,792 GB)</li>
      <li><strong>작업 소요 시간:</strong> <strong>37시간 30분</strong> (2026-09-23 09:54 ~ 2026-09-24 23:24)
        <br />
        ➔ <em><strong>T0 플랜(30시간 7분) 대비 7시간 23분(443분) 더 소요(약 24.5% 느림)</strong>: T0 당시 첨부파일이 불완전(incomplete)하게 중단되었던 30개 프로젝트 중 23개사가 T1에서 대폭 정상화(미완료 7개사로 축소)되며 실측 33TB 대용량 전송이 끝까지 수행된 영향입니다.</em>
      </li>
      <li><strong>프로젝트 이관율:</strong> <strong>617개 프로젝트 100% 정상 이관</strong> (98.9%), 7개 프로젝트 일부 결손 (1.1%)</li>
      <li><strong>첨부파일 용량 성공률:</strong> <strong>99.62%</strong> (약 32.87 TB 이관 성공 / <strong>124.77 GB 미이관</strong>)</li>
      <li><strong>미마이그레이션 파일 건수:</strong> <strong>총 1,872건</strong> (7개 프로젝트 발생)</li>
      <li><strong>데이터 보존 상태:</strong> <strong>온프레미스 서버 디스크에 1,872건 전량 보관 확인 완료 (영구 유실 0건)</strong> ➔ 재시도(Retry) 가능</li>
    </ul>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>1. 26Wave2-JIRA-T1-1 플랜 개요 및 핵심 KPI</h2>
<table>
  <tbody>
    <tr>
      <th style="background-color: #f4f5f7; width: 220px;"><strong>작업 플랜명</strong></th>
      <td><code>26Wave2-JIRA-T1-1</code></td>
      <th style="background-color: #f4f5f7; width: 220px;"><strong>세션 ID (Migration ID)</strong></th>
      <td><code>85e18200b33684534d0404013cb4f736accb8c24</code></td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>작업 수행 기간 (실측)</strong></th>
      <td><strong>2026-09-23 09:54 ~ 2026-09-24 23:24</strong></td>
      <th style="background-color: #f4f5f7;"><strong>총 소요 시간 (Duration)</strong></th>
      <td>
        <strong>37시간 30분</strong> (2,250분 소요)
        <br />
        <span style="font-size: 11.5px; color: #de350b; font-weight: 700;">
          ▼ T0 플랜(30시간 7분) 대비 7시간 23분(443분) 더 소요 (+24.5% 느림)
        </span>
        <br />
        <span style="font-size: 11px; color: #6b778c;">
          (T0 시 미완료 30개사 ➔ T1 시 7개사로 23개사 정상화 및 실측 33TB 대용량 완결 전송 집중)
        </span>
      </td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>플랜 대상 프로젝트 수</strong></th>
      <td><strong>총 624개 프로젝트</strong></td>
      <th style="background-color: #f4f5f7;"><strong>플랜 전체 대상 용량</strong></th>
      <td><strong>약 33 TB</strong> (33,792 GB)</td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>프로젝트 이관 결과</strong></th>
      <td>
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Green</ac:parameter>
          <ac:parameter ac:name="title">617개 성공 (98.9%)</ac:parameter>
        </ac:structured-macro>
        &nbsp;/&nbsp;
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Yellow</ac:parameter>
          <ac:parameter ac:name="title">7개 미완료 (1.1%)</ac:parameter>
        </ac:structured-macro>
        <br />
        <span style="font-size: 11px; color: #00875a;">(T0 당시 미완료 30개 프로젝트 대비 23개 프로젝트 추가 정상화)</span>
      </td>
      <th style="background-color: #f4f5f7;"><strong>용량 기준 이관 성공률</strong></th>
      <td>
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Green</ac:parameter>
          <ac:parameter ac:name="title">99.62% 성공</ac:parameter>
        </ac:structured-macro>
        <span style="font-size: 11px; color: #6b778c;">(미이관: 124.77 GB / 0.38%)</span>
      </td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>첨부파일 결손 현황</strong></th>
      <td><strong style="color: #de350b;">총 1,872건 (124.77 GB)</strong></td>
      <th style="background-color: #f4f5f7;"><strong>디스크 원본 무결성</strong></th>
      <td>
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Green</ac:parameter>
          <ac:parameter ac:name="title">영구 유실 0건 (100% 보존)</ac:parameter>
        </ac:structured-macro>
      </td>
    </tr>
  </tbody>
</table>

<h3>💡 [비교] T0 (`26Wave2-JIRA-T0-1`) vs T1 (`26Wave2-JIRA-T1-1`) 실행 결과 비교</h3>
<table>
  <thead>
    <tr>
      <th style="background-color: #f4f5f7; text-align: center;">비교 항목</th>
      <th style="background-color: #f4f5f7; text-align: center;">T0 플랜 (26Wave2-JIRA-T0-1)</th>
      <th style="background-color: #f4f5f7; text-align: center;">T1 플랜 (26Wave2-JIRA-T1-1)</th>
      <th style="background-color: #f4f5f7; text-align: center;">비교 결과 및 변동</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="text-align: center;"><strong>실행 기간</strong></td>
      <td style="text-align: center;">2026-08-04 13:51 ~ 2026-08-05 19:58</td>
      <td style="text-align: center;">2026-09-23 09:54 ~ 2026-09-24 23:24</td>
      <td style="text-align: center;">-</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>작업 소요 시간</strong></td>
      <td style="text-align: center;"><strong>30시간 7분</strong> (1,807분)</td>
      <td style="text-align: center; color: #de350b;"><strong>37시간 30분</strong> (2,250분)</td>
      <td style="text-align: center; color: #de350b; font-weight: 700;">
        ▲ 7시간 23분 (443분) 증가 (+24.5% 느림)
      </td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>대상 프로젝트 수</strong></td>
      <td style="text-align: center;">736개 프로젝트</td>
      <td style="text-align: center;">624개 프로젝트</td>
      <td style="text-align: center;">▼ 112개 프로젝트 정제</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>첨부파일 미완료 프로젝트</strong></td>
      <td style="text-align: center; color: #de350b; font-weight: 700;">30개 프로젝트 미완료</td>
      <td style="text-align: center; color: #00875a; font-weight: 700;">7개 프로젝트 미완료</td>
      <td style="text-align: center; color: #00875a; font-weight: 700;">
        ▼ 23개 프로젝트 추가 정상 이관 완료
      </td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>소요시간 증가 원인 분석</strong></td>
      <td colspan="3" style="font-size: 12px; color: #42526e;">
        T0 플랜에서는 30개 프로젝트의 첨부파일 전송이 불완전(incomplete) 상태로 중단되어 시간이 상대적으로 적게 소요된 반면, 
        <strong>T1 플랜에서는 미완료 대상이 7개사로 대폭 줄어들고 실제 약 33 TB의 대용량 첨부파일 전송을 끝까지 수행</strong>함에 따라 작업 시간이 7시간 23분 증가(전송 완결성 향상)하였습니다.
      </td>
    </tr>
  </tbody>
</table>

<ac:structured-macro ac:name="tip" ac:schema-version="1">
  <ac:parameter ac:name="title">플랜 공식 시작/종료/소요시간 DB 직접 조회 쿼리 (Atlassian KB)</ac:parameter>
  <ac:rich-text-body>
    <p>아틀라시안 공식 가이드에 따라 온프레미스 Jira DB의 <code>AO_6FF49D_PLAN_ENTITY</code>를 조회하면 본 플랜의 공식 작업 시간을 초 단위로 확인할 수 있습니다:</p>
  </ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="language">sql</ac:parameter>
  <ac:parameter ac:name="title">26Wave2-JIRA-T1-1 플랜 작업 시간 확인 쿼리</ac:parameter>
  <ac:plain-text-body><![CDATA[SELECT 
    pe.PLAN_NAME AS plan_name,
    FROM_UNIXTIME(pe.CREATED_TIMESTAMP/1000) AS start_date,
    FROM_UNIXTIME(me.UPDATED_TIMESTAMP/1000) AS finish_date,
    ROUND((me.UPDATED_TIMESTAMP - pe.CREATED_TIMESTAMP)/1000/60, 2) AS duration_minutes
FROM AO_6FF49D_PLAN_ENTITY pe
JOIN AO_6FF49D_MIGRATION_ENTITY me ON (pe.ID = me.PLAN_ID)
WHERE pe.PLAN_NAME = '26Wave2-JIRA-T1-1';]]></ac:plain-text-body>
</ac:structured-macro>

<h2>2. 26Wave2-JIRA-T1-1 플랜 내 프로젝트별 첨부파일 이관 현황</h2>
<p>플랜에 포함된 <strong>총 624개 프로젝트 중 617개(98.9%)는 첨부파일 오류 없이 100% 이관 완료</strong>되었으며, 아래 <strong>7개 프로젝트</strong>에서만 대용량 파일 타임아웃 등으로 인한 미이관(1,872건, 124.77 GB)이 발생하였습니다.</p>

<table>
  <thead>
    <tr>
      <th style="background-color: #f4f5f7; text-align: center;">프로젝트 구분</th>
      <th style="background-color: #f4f5f7; text-align: center;">프로젝트 수</th>
      <th style="background-color: #f4f5f7; text-align: center;">첨부파일 이관 상태</th>
      <th style="background-color: #f4f5f7; text-align: center;">비고</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="text-align: center;"><strong>정상 완료 프로젝트</strong></td>
      <td style="text-align: center; color: #00875a; font-weight: 700;">617개</td>
      <td style="text-align: center;">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Green</ac:parameter>
          <ac:parameter ac:name="title">100% 이관 완료 (오류 0건)</ac:parameter>
        </ac:structured-macro>
      </td>
      <td style="font-size: 12px;">JCMA 상 오류/경고 로그 발생 없음</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>미완료 발생 프로젝트</strong></td>
      <td style="text-align: center; color: #de350b; font-weight: 700;">7개</td>
      <td style="text-align: center;">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Red</ac:parameter>
          <ac:parameter ac:name="title">1,872건 미이관 (124.77 GB)</ac:parameter>
        </ac:structured-macro>
      </td>
      <td style="font-size: 12px;">CQIM, CAQW 등 대용량 집중 프로젝트</td>
    </tr>
    <tr style="background-color: #fafbfc; font-weight: 700;">
      <td style="text-align: center;"><strong>26Wave2-JIRA-T1-1 총계</strong></td>
      <td style="text-align: center;"><strong>624개</strong></td>
      <td style="text-align: center; color: #0052cc;"><strong>성공률 99.62%</strong></td>
      <td style="text-align: center;">-</td>
    </tr>
  </tbody>
</table>

<h3>미완료 발생 7개 프로젝트 상세 내역</h3>
<p>미이관 용량(124.77 GB) 중 <strong>CQIM(85.98 GB)과 CAQW(39.65 GB) 단 2개 프로젝트가 전체 결손 용량의 99.9% (125.63 GB)를 차지</strong>하고 있습니다.</p>

<table>
  <thead>
    <tr>
      <th style="background-color: #f4f5f7; text-align: center;">프로젝트 키</th>
      <th style="background-color: #f4f5f7; text-align: center;">미이관 건수</th>
      <th style="background-color: #f4f5f7; text-align: center;">건수 비중</th>
      <th style="background-color: #f4f5f7; text-align: right;">미이관 용량 (MB)</th>
      <th style="background-color: #f4f5f7; text-align: right;">미이관 용량 (GB)</th>
      <th style="background-color: #f4f5f7; text-align: center;">용량 비중</th>
      <th style="background-color: #f4f5f7;">주요 파일 유형 및 원인</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color: #fff0eb;">
      <td style="text-align: center;"><strong>CQIM</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>992건</strong></td>
      <td style="text-align: center;">53.0%</td>
      <td style="text-align: right;">85,980.70 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>85.98 GB</strong></td>
      <td style="text-align: center;"><strong>68.9%</strong></td>
      <td style="font-size: 12px;">분할 압축(.z01~.z10), 비디오(.mp4), DLT 로그</td>
    </tr>
    <tr style="background-color: #fff0eb;">
      <td style="text-align: center;"><strong>CAQW</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>676건</strong></td>
      <td style="text-align: center;">36.1%</td>
      <td style="text-align: right;">39,650.52 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>39.65 GB</strong></td>
      <td style="text-align: center;"><strong>31.8%</strong></td>
      <td style="font-size: 12px;">분할 압축(.7z, .z01~.z08), 비디오, 이미지</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>GENESISP</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>106건</strong></td>
      <td style="text-align: center;">5.7%</td>
      <td style="text-align: right;">2,027.77 MB</td>
      <td style="text-align: right;">2.03 GB</td>
      <td style="text-align: center;">1.6%</td>
      <td style="font-size: 12px;">DLT 로그, 이미지(.png), 압축파일</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>JGVSTAR25</strong></td>
      <td style="text-align: center;"><strong>78건</strong></td>
      <td style="text-align: center;">4.2%</td>
      <td style="text-align: right;">15.66 MB</td>
      <td style="text-align: right;">0.02 GB</td>
      <td style="text-align: center;">&lt;0.1%</td>
      <td style="font-size: 12px;">Container.json, 스크린샷(.png)</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>ECSC</strong></td>
      <td style="text-align: center;"><strong>15건</strong></td>
      <td style="text-align: center;">0.8%</td>
      <td style="text-align: right;">85.37 MB</td>
      <td style="text-align: right;">0.08 GB</td>
      <td style="text-align: center;">&lt;0.1%</td>
      <td style="font-size: 12px;">이미지, 문서</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>SPATEST</strong></td>
      <td style="text-align: center;"><strong>4건</strong></td>
      <td style="text-align: center;">0.2%</td>
      <td style="text-align: right;">10.64 MB</td>
      <td style="text-align: right;">0.01 GB</td>
      <td style="text-align: center;">&lt;0.1%</td>
      <td style="font-size: 12px;">동영상(.mp4), 이미지</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>RPSPEC</strong></td>
      <td style="text-align: center;"><strong>1건</strong></td>
      <td style="text-align: center;">0.1%</td>
      <td style="text-align: right;">0.02 MB</td>
      <td style="text-align: right;">0.00 GB</td>
      <td style="text-align: center;">&lt;0.1%</td>
      <td style="font-size: 12px;">스크린샷 이미지(.png)</td>
    </tr>
    <tr style="background-color: #fafbfc; font-weight: 700;">
      <td style="text-align: center;"><strong>합계</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>1,872건</strong></td>
      <td style="text-align: center;">100.0%</td>
      <td style="text-align: right;">127,765.30 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>124.77 GB</strong></td>
      <td style="text-align: center;"><strong>100.0%</strong></td>
      <td style="text-align: center;">-</td>
    </tr>
  </tbody>
</table>

<h2>3. 주요 미이관 원인 심층 분석</h2>
<ul>
  <li><strong>1) 극단적인 대용량 파일 집중:</strong> 미이관된 파일 중 100MB 이상 대용량 파일이 수백 개 이상 포함되어 있으며, 특히 <strong>CQIM(85.98 GB) 및 CAQW(39.65 GB) 두 프로젝트가 전체 누락 용량의 99.9%</strong>를 차지하고 있습니다.</li>
  <li><strong>2) JCMA 네트워크 타임아웃 / DB wait_timeout:</strong> Jira Cloud로 수십~수백 MB의 분할 압축 파일이나 비디오를 전송하는 과정에서 온프레미스 DB의 <code>wait_timeout</code>(유휴 시간 초과)에 도달하거나 클라우드 미디어 API 업로드 제한에 걸려 미완료 처리되었습니다.</li>
  <li><strong>3) 디스크 원본 보존 상태 (유실 0건):</strong> <strong>ScriptRunner로 실제 온프레미스 파일시스템을 전수 검사한 결과, 1,872개 파일 전량이 서버 디스크에 안전하게 정상 보관</strong>되어 있음을 확인하였습니다. (영구 데이터 유실 0건으로 재시도시 100% 정상화 가능)</li>
</ul>

<h2>4. 최대 용량 미마이그레이션 파일 샘플 (Top 15)</h2>
<p>본 플랜에서 용량이 가장 커서 마이그레이션 타임아웃이 발생한 대표적인 상위 파일 목록입니다 (전체 1,872건 내역은 첨부된 CSV 파일에서 확인 가능):</p>
<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">#</th>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트</th>
      <th style="text-align: center; background-color: #f4f5f7;">이슈 번호</th>
      <th style="text-align: center; background-color: #f4f5f7;">첨부 ID</th>
      <th style="background-color: #f4f5f7;">파일명</th>
      <th style="text-align: center; background-color: #f4f5f7;">크기 (MB)</th>
      <th style="text-align: center; background-color: #f4f5f7;">등록 일시</th>
      <th style="text-align: center; background-color: #f4f5f7;">작성자</th>
    </tr>
  </thead>
  <tbody>
"""

for idx, item in enumerate(sorted_by_size[:15]):
    storage_body += f"""    <tr>
      <td style="text-align: center;">{idx + 1}</td>
      <td style="text-align: center;"><code>{item['project']}</code></td>
      <td style="text-align: center;"><strong>{item['issue_key']}</strong></td>
      <td style="text-align: center;"><code>{item['attachment_id']}</code></td>
      <td>{item['file_name']}</td>
      <td style="text-align: center; color: #de350b; font-weight: 700;">{item['file_size_mb']} MB</td>
      <td style="text-align: center; font-size: 12px;">{item['created_date'].replace('T', ' ')}</td>
      <td style="text-align: center;"><code>{item['author']}</code></td>
    </tr>
"""

storage_body += """  </tbody>
</table>

<h2>5. 온프레미스 DB 조회용 SQL (1,872건 추출 쿼리)</h2>
<p>온프레미스 Jira DB에서 본 1,872건의 전체 이슈 키와 파일명을 직접 조회할 때 사용한 공식 쿼리입니다:</p>

<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="language">sql</ac:parameter>
  <ac:parameter ac:name="title">26Wave2-JIRA-T1-1 미마이그레이션 첨부파일 1,872건 추출 쿼리</ac:parameter>
  <ac:plain-text-body><![CDATA[SELECT 
    p.pkey AS project_key,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    fa.ID AS attachment_id,
    fa.FILENAME AS file_name,
    fa.FILESIZE AS file_size_bytes,
    ROUND(fa.FILESIZE / (1024*1024), 2) AS file_size_mb,
    fa.CREATED AS created_date,
    fa.AUTHOR AS author
FROM fileattachment fa
JOIN jiraissue i ON fa.issueid = i.ID
JOIN project p ON i.PROJECT = p.ID
LEFT JOIN AO_6FF49D_MIGRATED_FILE mf ON mf.FILE_SERVER_ID = fa.ID
WHERE p.pkey IN ('CAQW', 'CQIM', 'ECSC', 'GENESISP', 'JGVSTAR25', 'RPSPEC', 'SPATEST')
  AND (mf.ID IS NULL OR mf.MIGRATED = 0 OR mf.MIGRATED = false)
ORDER BY p.pkey, fa.ID;]]></ac:plain-text-body>
</ac:structured-macro>

<h2>6. 사후 조치 및 재이관(Remediation) 권고 방안</h2>
<ul>
  <li><strong>JCMA 실패 첨부파일 재시도:</strong> Jira 관리자 콘솔 > 마이그레이션 도우미에서 <code>26Wave2-JIRA-T1-1</code> 플랜의 <em>[Retry failed attachments]</em> 기능을 통해 재전송 시도.</li>
  <li><strong>대용량 프로젝트(CQIM, CAQW) 전용 플랜:</strong> CQIM과 CAQW의 분할 압축 및 비디오 파일에 대해 별도 마이그레이션 플랜을 구성하여 네트워크 대역폭 및 DB 타임아웃(wait_timeout 증가) 최적화 상태에서 분할 이관.</li>
  <li><strong>영구 유실 위험 없음:</strong> 원본 파일이 100% 보존되어 있으므로 사전 백업 후 안전하게 재이관 작업 진행 가능.</li>
</ul>

<hr />
<h2>7. 첨부파일 목록 (1,872건 CSV 다운로드)</h2>
<p>26Wave2-JIRA-T1-1 플랜의 전체 1,872건 상세 리스트는 아래 첨부파일 중 <code>jcma_unmigrated_attachments_1872.csv</code>를 다운로드하여 엑셀에서 확인하실 수 있습니다.</p>
<p local-id="446f8c0f1500" />
<ac:structured-macro ac:name="attachments" ac:schema-version="1" data-layout="wide" ac:local-id="5e74de2e-2f04-421d-8b04-c916b01e9976" ac:macro-id="85f675b3-2fa4-43ef-84cf-06902cc08b9b" />
<p local-id="67d646560244" />
"""

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
        "message": "T0-1(30시간 7분) 대비 T1-1 소요시간(+7시간 23분, 24.5% 증가) 및 미완료 프로젝트 비교 내용 추가"
    }
}

json_data = json.dumps(payload).encode("utf-8")
update_req = urllib.request.Request(update_url, data=json_data, headers=headers, method="PUT")

with urllib.request.urlopen(update_req, context=ctx) as res:
    res_data = json.loads(res.read().decode("utf-8"))
    print("Updated Confluence successfully to Version:", res_data.get("version", {}).get("number"))
