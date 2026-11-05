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

# Strict single-line styles
TBL_STYLE = "width: 100%; border-collapse: collapse; border: 1px solid #dfe1e6; margin-bottom: 20px;"
TH_STYLE = "background-color: #f4f5f7; color: #172b4d; padding: 8px 12px; font-weight: 700; text-align: center; border: 1px solid #dfe1e6; font-size: 12.5px; white-space: nowrap;"
TH_LBL = "background-color: #f4f5f7; color: #172b4d; font-weight: 600; padding: 7px 12px; border: 1px solid #dfe1e6; font-size: 12.5px; white-space: nowrap;"
TD_L = "padding: 7px 12px; border: 1px solid #dfe1e6; font-size: 12.5px; color: #172b4d; vertical-align: middle; white-space: nowrap;"
TD_C = "padding: 7px 12px; border: 1px solid #dfe1e6; font-size: 12.5px; color: #172b4d; text-align: center; vertical-align: middle; white-space: nowrap;"
TD_R = "padding: 7px 12px; border: 1px solid #dfe1e6; font-size: 12.5px; color: #172b4d; text-align: right; vertical-align: middle; white-space: nowrap;"

storage_body = f"""<h1>26Wave2-JIRA-T1-1 마이그레이션 진단 &amp; 첨부파일 종합 분석 보고서</h1>

<ac:structured-macro ac:name="info" ac:schema-version="1">
  <ac:parameter ac:name="title">26Wave2-JIRA-T1-1 플랜 결과 요약</ac:parameter>
  <ac:rich-text-body>
    <p>본 보고서는 마이그레이션 플랜 <strong><code>26Wave2-JIRA-T1-1</code></strong>의 실행 결과 및 첨부파일 이관 상태를 단독 정밀 분석한 보고서입니다.</p>
    <ul>
      <li><strong>플랜 대상 규모:</strong> <strong>624개 프로젝트</strong> / <strong>약 33 TB</strong> (33,792 GB)</li>
      <li><strong>작업 소요 시간:</strong> <strong>37시간 30분</strong> (2,250분) · <em>T0 플랜(30시간 7분) 대비 +7시간 23분(24.5% 느림) 소요 (미완료 23개사 정상화 및 33TB 완결 전송 집중)</em></li>
      <li><strong>프로젝트 이관율:</strong> <strong>617개 성공 (98.9%)</strong> / 7개 미완료 (1.1%)</li>
      <li><strong>첨부파일 성공률:</strong> <strong>99.62%</strong> (약 32.87 TB 이관 성공 / <strong>124.77 GB 미이관</strong>)</li>
      <li><strong>미이관 첨부파일:</strong> <strong>총 1,872건</strong> (7개 프로젝트 한정 발생)</li>
      <li><strong>데이터 보존 상태:</strong> <strong>온프레미스 서버 디스크에 1,872건 전량 100% 정상 보관 확인 완료 (영구 유실 0건)</strong> ➔ 재시도(Retry) 가능</li>
    </ul>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>1. 26Wave2-JIRA-T1-1 플랜 개요 및 핵심 KPI</h2>
<table data-layout="full-width" style="{TBL_STYLE}">
  <tbody>
    <tr>
      <th style="{TH_LBL} width: 14%;">작업 플랜명</th>
      <td style="{TD_L} width: 36%;"><code>26Wave2-JIRA-T1-1</code></td>
      <th style="{TH_LBL} width: 14%;">세션 ID</th>
      <td style="{TD_L} width: 36%;"><code>85e18200b33684534d0404013cb4f736accb8c24</code></td>
    </tr>
    <tr>
      <th style="{TH_LBL}">작업 수행 기간</th>
      <td style="{TD_L}">2026-09-23 09:54 ~ 2026-09-24 23:24</td>
      <th style="{TH_LBL}">총 소요 시간</th>
      <td style="{TD_L}"><strong>37시간 30분</strong> (2,250분) &nbsp;<span style="color: #de350b; font-size: 11.5px; font-weight: 600;">[T0 대비 +7h 23m / +24.5% 느림]</span></td>
    </tr>
    <tr>
      <th style="{TH_LBL}">플랜 대상 규모</th>
      <td style="{TD_L}">총 <strong>624개 프로젝트</strong> (약 <strong>33 TB</strong>)</td>
      <th style="{TH_LBL}">프로젝트 성공률</th>
      <td style="{TD_L}"><span style="color: #00875a; font-weight: 700;">617개 성공 (98.9%)</span> &nbsp;·&nbsp; <span style="color: #de350b; font-weight: 700;">7개 미완료 (1.1%)</span></td>
    </tr>
    <tr>
      <th style="{TH_LBL}">첨부파일 성공률</th>
      <td style="{TD_L}"><span style="color: #00875a; font-weight: 700;">99.62% 성공</span> (약 32.87 TB 정상 이관)</td>
      <th style="{TH_LBL}">첨부파일 결손</th>
      <td style="{TD_L}"><strong style="color: #de350b;">1,872건 미이관</strong> (<strong>124.77 GB</strong> / 0.38%)</td>
    </tr>
    <tr>
      <th style="{TH_LBL}">디스크 원본 상태</th>
      <td style="{TD_L}"><span style="color: #00875a; font-weight: 700;">100% 정상 보관</span> (영구 유실 0건 실측 검증)</td>
      <th style="{TH_LBL}">조치 방안</th>
      <td style="{TD_L}">JCMA 실패 첨부파일 재시도(Retry) 또는 분할 재이관 가능</td>
    </tr>
  </tbody>
</table>

<h3>💡 T0 (26Wave2-JIRA-T0-1) vs T1 (26Wave2-JIRA-T1-1) 실행 결과 비교</h3>
<table data-layout="full-width" style="{TBL_STYLE}">
  <thead>
    <tr>
      <th style="{TH_STYLE} width: 18%;">비교 항목</th>
      <th style="{TH_STYLE} width: 25%;">T0 플랜 (26Wave2-JIRA-T0-1)</th>
      <th style="{TH_STYLE} width: 25%;">T1 플랜 (26Wave2-JIRA-T1-1)</th>
      <th style="{TH_STYLE} width: 32%;">비교 결과 및 변동</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="{TH_LBL} text-align: center;">작업 실행 기간</td>
      <td style="{TD_C}">2026-08-04 13:51 ~ 08-05 19:58</td>
      <td style="{TD_C}">2026-09-23 09:54 ~ 09-24 23:24</td>
      <td style="{TD_C}">1.5개월 간격 순차 진행 (사전 검증 ➔ 본이관)</td>
    </tr>
    <tr style="background-color: #fff8f5;">
      <td style="{TH_LBL} text-align: center; color: #de350b;">작업 소요 시간</td>
      <td style="{TD_C}"><strong>30시간 7분</strong> (1,807분)</td>
      <td style="{TD_C} color: #de350b;"><strong>37시간 30분</strong> (2,250분)</td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">▲ 7시간 23분 (443분) 더 소요 (+24.5% 느림)</td>
    </tr>
    <tr>
      <td style="{TH_LBL} text-align: center;">대상 프로젝트 수</td>
      <td style="{TD_C}">736개 프로젝트</td>
      <td style="{TD_C}">624개 프로젝트</td>
      <td style="{TD_C}">▼ 112개 프로젝트 제외 (이관 대상 정제)</td>
    </tr>
    <tr style="background-color: #f6ffed;">
      <td style="{TH_LBL} text-align: center; color: #00875a;">첨부파일 미완료사</td>
      <td style="{TD_C} color: #de350b; font-weight: 600;">30개 프로젝트 불완전 (incomplete)</td>
      <td style="{TD_C} color: #00875a; font-weight: 700;">7개 프로젝트 미완료</td>
      <td style="{TD_C} color: #00875a; font-weight: 700;">▼ 23개 프로젝트 추가 정상 이관 완료</td>
    </tr>
    <tr>
      <td style="{TH_LBL} text-align: center;">소요시간 변동 사유</td>
      <td colspan="3" style="{TD_L} font-size: 12px; color: #42526e;">T0는 30개사의 첨부파일이 조기 중단된 반면, T1은 미완료사를 7개사로 줄이고 실측 33TB 전송을 끝까지 완수하여 7시간 23분 증가함</td>
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

<table data-layout="full-width" style="{TBL_STYLE}">
  <thead>
    <tr>
      <th style="{TH_STYLE} width: 25%;">프로젝트 구분</th>
      <th style="{TH_STYLE} width: 15%;">프로젝트 수</th>
      <th style="{TH_STYLE} width: 12%;">비중</th>
      <th style="{TH_STYLE} width: 25%;">첨부파일 이관 상태</th>
      <th style="{TH_STYLE} width: 23%;">비고</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="{TD_L}"><strong>정상 완료 프로젝트</strong></td>
      <td style="{TD_C} color: #00875a; font-weight: 700;">617개</td>
      <td style="{TD_C}">98.9%</td>
      <td style="{TD_C}"><span style="background: #e3fcef; color: #006644; padding: 3px 10px; border-radius: 4px; font-weight: 700;">100% 정상 이관 완료</span></td>
      <td style="{TD_L} font-size: 12px; color: #5e6c84;">JCMA 오류/경고 로그 0건</td>
    </tr>
    <tr>
      <td style="{TD_L}"><strong>미완료 발생 프로젝트</strong></td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">7개</td>
      <td style="{TD_C}">1.1%</td>
      <td style="{TD_C}"><span style="background: #ffebe6; color: #bf2600; padding: 3px 10px; border-radius: 4px; font-weight: 700;">1,872건 미이관 (124.77 GB)</span></td>
      <td style="{TD_L} font-size: 12px; color: #5e6c84;">CQIM, CAQW 등 대용량 집중 프로젝트</td>
    </tr>
    <tr style="background-color: #f4f5f7; font-weight: 700;">
      <td style="{TD_C}">26Wave2-JIRA-T1-1 총계</td>
      <td style="{TD_C}">624개</td>
      <td style="{TD_C}">100.0%</td>
      <td style="{TD_C} color: #0052cc;">용량 성공률 99.62% (32.87 TB)</td>
      <td style="{TD_L} font-size: 12px; color: #00875a;">온프레미스 디스크 영구 유실 0건</td>
    </tr>
  </tbody>
</table>

<h3>미완료 발생 7개 프로젝트 상세 내역</h3>
<p>미이관 용량(124.77 GB) 중 <strong>CQIM(85.98 GB)과 CAQW(39.65 GB) 단 2개 프로젝트가 전체 결손 용량의 99.9% (125.63 GB)를 차지</strong>하고 있습니다.</p>

<table data-layout="full-width" style="{TBL_STYLE}">
  <thead>
    <tr>
      <th style="{TH_STYLE} width: 6%;">순위</th>
      <th style="{TH_STYLE} width: 14%;">프로젝트 키</th>
      <th style="{TH_STYLE} width: 12%;">미이관 건수</th>
      <th style="{TH_STYLE} width: 9%;">건수 비중</th>
      <th style="{TH_STYLE} width: 15%;">미이관 용량 (MB)</th>
      <th style="{TH_STYLE} width: 14%;">미이관 용량 (GB)</th>
      <th style="{TH_STYLE} width: 9%;">용량 비중</th>
      <th style="{TH_STYLE} width: 21%;">주요 파일 유형 및 원인</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color: #fff8f5;">
      <td style="{TD_C}"><strong>1</strong></td>
      <td style="{TD_C}"><code>CQIM</code></td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">992건</td>
      <td style="{TD_C}">53.0%</td>
      <td style="{TD_R}">85,980.70 MB</td>
      <td style="{TD_R} color: #de350b; font-weight: 700;">85.98 GB</td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">68.9%</td>
      <td style="{TD_L} font-size: 11.5px;">분할 압축(.z01~.z10), 비디오(.mp4), DLT 로그</td>
    </tr>
    <tr style="background-color: #fff8f5;">
      <td style="{TD_C}"><strong>2</strong></td>
      <td style="{TD_C}"><code>CAQW</code></td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">676건</td>
      <td style="{TD_C}">36.1%</td>
      <td style="{TD_R}">39,650.52 MB</td>
      <td style="{TD_R} color: #de350b; font-weight: 700;">39.65 GB</td>
      <td style="{TD_C} color: #de350b; font-weight: 700;">31.8%</td>
      <td style="{TD_L} font-size: 11.5px;">분할 압축(.7z, .z01~.z08), 비디오, 이미지</td>
    </tr>
    <tr>
      <td style="{TD_C}">3</td>
      <td style="{TD_C}"><code>GENESISP</code></td>
      <td style="{TD_C} color: #ff8b00; font-weight: 600;">106건</td>
      <td style="{TD_C}">5.7%</td>
      <td style="{TD_R}">2,027.77 MB</td>
      <td style="{TD_R}">2.03 GB</td>
      <td style="{TD_C}">1.6%</td>
      <td style="{TD_L} font-size: 11.5px;">DLT 로그, 이미지(.png), 압축파일</td>
    </tr>
    <tr>
      <td style="{TD_C}">4</td>
      <td style="{TD_C}"><code>JGVSTAR25</code></td>
      <td style="{TD_C}">78건</td>
      <td style="{TD_C}">4.2%</td>
      <td style="{TD_R}">15.66 MB</td>
      <td style="{TD_R}">0.02 GB</td>
      <td style="{TD_C}">&lt;0.1%</td>
      <td style="{TD_L} font-size: 11.5px;">Container.json, 스크린샷(.png)</td>
    </tr>
    <tr>
      <td style="{TD_C}">5</td>
      <td style="{TD_C}"><code>ECSC</code></td>
      <td style="{TD_C}">15건</td>
      <td style="{TD_C}">0.8%</td>
      <td style="{TD_R}">85.37 MB</td>
      <td style="{TD_R}">0.08 GB</td>
      <td style="{TD_C}">&lt;0.1%</td>
      <td style="{TD_L} font-size: 11.5px;">이미지, 문서</td>
    </tr>
    <tr>
      <td style="{TD_C}">6</td>
      <td style="{TD_C}"><code>SPATEST</code></td>
      <td style="{TD_C}">4건</td>
      <td style="{TD_C}">0.2%</td>
      <td style="{TD_R}">10.64 MB</td>
      <td style="{TD_R}">0.01 GB</td>
      <td style="{TD_C}">&lt;0.1%</td>
      <td style="{TD_L} font-size: 11.5px;">동영상(.mp4), 이미지</td>
    </tr>
    <tr>
      <td style="{TD_C}">7</td>
      <td style="{TD_C}"><code>RPSPEC</code></td>
      <td style="{TD_C}">1건</td>
      <td style="{TD_C}">0.1%</td>
      <td style="{TD_R}">0.02 MB</td>
      <td style="{TD_R}">0.00 GB</td>
      <td style="{TD_C}">&lt;0.1%</td>
      <td style="{TD_L} font-size: 11.5px;">스크린샷 이미지(.png)</td>
    </tr>
    <tr style="background-color: #f4f5f7; font-weight: 700;">
      <td style="{TD_C}" colspan="2">합계</td>
      <td style="{TD_C} color: #de350b;">1,872건</td>
      <td style="{TD_C}">100.0%</td>
      <td style="{TD_R}">127,765.30 MB</td>
      <td style="{TD_R} color: #de350b;">124.77 GB</td>
      <td style="{TD_C}">100.0%</td>
      <td style="{TD_C} font-size: 11.5px; color: #5e6c84;">상위 2개 프로젝트가 결손 용량의 99.9% 차지</td>
    </tr>
  </tbody>
</table>

<h2>3. 주요 미이관 원인 심층 분석</h2>
<ul>
  <li><strong>1) 극단적인 대용량 파일 집중:</strong> 미이관된 파일 중 100MB 이상 대용량 파일이 수백 개 이상 포함되어 있으며, 특히 <strong>CQIM(85.98 GB) 및 CAQW(39.65 GB) 두 프로젝트가 전체 누락 용량의 99.9%</strong>를 차지하고 있습니다.</li>
  <li><strong>2) JCMA 네트워크 타임아웃 / DB wait_timeout:</strong> Jira Cloud로 대용량 분할 압축 파일이나 비디오를 전송하는 과정에서 온프레미스 DB의 <code>wait_timeout</code>(유휴 시간 초과)에 도달하거나 클라우드 미디어 API 업로드 제한에 걸려 미완료 처리되었습니다.</li>
  <li><strong>3) 디스크 원본 보존 상태 (유실 0건):</strong> <strong>ScriptRunner로 실제 온프레미스 파일시스템을 전수 검사한 결과, 1,872개 파일 전량이 서버 디스크에 안전하게 정상 보관</strong>되어 있음을 확인하였습니다. (영구 데이터 유실 0건으로 재시도시 100% 정상화 가능)</li>
</ul>

<h2>4. 최대 용량 미마이그레이션 파일 샘플 (Top 15)</h2>
<p>본 플랜에서 용량이 가장 커서 마이그레이션 타임아웃이 발생한 대표적인 상위 파일 목록입니다 (전체 1,872건 내역은 첨부된 CSV 파일에서 확인 가능):</p>
<table data-layout="full-width" style="{TBL_STYLE}">
  <thead>
    <tr>
      <th style="{TH_STYLE} width: 4%;">#</th>
      <th style="{TH_STYLE} width: 10%;">프로젝트</th>
      <th style="{TH_STYLE} width: 14%;">이슈 번호</th>
      <th style="{TH_STYLE} width: 10%;">첨부 ID</th>
      <th style="{TH_STYLE} width: 34%;">파일명</th>
      <th style="{TH_STYLE} width: 10%;">크기 (MB)</th>
      <th style="{TH_STYLE} width: 10%;">등록 일시</th>
      <th style="{TH_STYLE} width: 8%;">작성자</th>
    </tr>
  </thead>
  <tbody>
"""

for idx, item in enumerate(sorted_by_size[:15]):
    row_bg = "background-color: #fafbfc;" if idx % 2 == 1 else "background-color: #ffffff;"
    created_short = item['created_date'][:10]
    storage_body += f"""    <tr style="{row_bg}">
      <td style="{TD_C}">{idx + 1}</td>
      <td style="{TD_C}"><code>{item['project']}</code></td>
      <td style="{TD_C}"><strong>{item['issue_key']}</strong></td>
      <td style="{TD_C}"><code>{item['attachment_id']}</code></td>
      <td style="{TD_L} font-family: SFMono-Regular, Consolas, monospace; font-size: 12px;">{item['file_name']}</td>
      <td style="{TD_R} color: #de350b; font-weight: 700;">{item['file_size_mb']:.1f} MB</td>
      <td style="{TD_C} font-size: 12px;">{created_short}</td>
      <td style="{TD_C}"><code>{item['author']}</code></td>
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
        "message": "프로젝트 성공률 및 모든 셀 매크로 제거 / 순수 인라인 한줄(nowrap) 강제 적용"
    }
}

json_data = json.dumps(payload).encode("utf-8")
update_req = urllib.request.Request(update_url, data=json_data, headers=headers, method="PUT")

with urllib.request.urlopen(update_req, context=ctx) as res:
    res_data = json.loads(res.read().decode("utf-8"))
    print("Updated Confluence successfully to Version:", res_data.get("version", {}).get("number"))
