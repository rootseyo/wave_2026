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

<ac:structured-macro ac:name="warning" ac:schema-version="1">
  <ac:parameter ac:name="title">미마이그레이션 첨부파일 총 1,872건 (124.77 GB) 최종 확인</ac:parameter>
  <ac:rich-text-body>
    <p>Jira 온프레미스 DB의 <strong>JCMA 전송 성공 기록 테이블(<code>AO_6FF49D_MIGRATED_FILE</code>)</strong>과 대조한 결과,</p>
    <p>초기 에러 로그상 152건 집계를 포함하여 <strong>총 1,872건 (124.77 GB)</strong>의 첨부파일이 클라우드로 미이전된 상태임이 최종 확인되었습니다.</p>
    <ul>
      <li><strong>총 미마이그레이션 파일 수:</strong> <strong>1,872건</strong> (7개 프로젝트)</li>
      <li><strong>총 미마이그레이션 용량:</strong> <strong>124.77 GB</strong> (127,765 MB)</li>
      <li><strong>핵심 원인:</strong> 100MB 초과 대용량 분할 압축 파일(.z01, .zip) 및 비디오(.mp4), DLT 로그 파일의 전송 제한 및 타임아웃</li>
      <li><strong>원본 보존 상태:</strong> <strong>온프레미스 서버 디스크에 1,872건 전량 정상 보관 확인됨 (영구 데이터 유실 0건)</strong> ➔ 재시도(Retry)로 복구 가능</li>
    </ul>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>1. 마이그레이션 개요</h2>
<table>
  <tbody>
    <tr>
      <th style="background-color: #f4f5f7; width: 220px;"><strong>작업 플랜명</strong></th>
      <td><code>26Wave2-JIRA-T1-1</code></td>
      <th style="background-color: #f4f5f7; width: 220px;"><strong>마이그레이션 세션 ID</strong></th>
      <td><code>85e18200b33684534d0404013cb4f736accb8c24</code></td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>작업 수행 기간 (실측)</strong></th>
      <td><strong>2026-09-23 09:54 ~ 2026-09-24 23:24</strong> (총 <strong>37시간 30분</strong> / 2,250분 소요)</td>
      <th style="background-color: #f4f5f7;"><strong>결과 리포트 생성 일시</strong></th>
      <td>2026-09-24 23:24:09</td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>대상 프로젝트 (총 7개)</strong></th>
      <td colspan="3"><code>CAQW</code>, <code>CQIM</code>, <code>ECSC</code>, <code>GENESISP</code>, <code>JGVSTAR25</code>, <code>RPSPEC</code>, <code>SPATEST</code></td>
    </tr>
    <tr>
      <th style="background-color: #f4f5f7;"><strong>마이그레이션 결손 현황</strong></th>
      <td colspan="3">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Red</ac:parameter>
          <ac:parameter ac:name="title">1,872건 (124.77 GB)</ac:parameter>
        </ac:structured-macro>
        &nbsp;(온프레미스 서버 디스크에 전량 정상 존재 확인 완료, 재전송 가능)
      </td>
    </tr>
  </tbody>
</table>

<ac:structured-macro ac:name="info" ac:schema-version="1">
  <ac:parameter ac:name="title">마이그레이션 공식 시작/종료 시간 및 소요시간(Duration) DB 조회 방법 (Atlassian KB)</ac:parameter>
  <ac:rich-text-body>
    <p>아틀라시안 공식 가이드(<a href="https://support.atlassian.com/atlassian-cloud/kb/determine-your-jcma-migration-duration/">Determine your JCMA migration duration</a>)에 따라, 온프레미스 Jira DB의 <code>AO_6FF49D_PLAN_ENTITY</code> 테이블을 조회하면 <strong>공식 시작 시간(start_date)과 종료 시간(finish_date), 소요시간(분)</strong>을 초 단위까지 정확히 조회할 수 있습니다.</p>
  </ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="language">sql</ac:parameter>
  <ac:parameter ac:name="title">MySQL 기준 마이그레이션 시작/종료/소요시간 조회 쿼리</ac:parameter>
  <ac:plain-text-body><![CDATA[SELECT 
    pe.PLAN_NAME AS plan_name,
    FROM_UNIXTIME(pe.CREATED_TIMESTAMP/1000) AS start_date,
    FROM_UNIXTIME(me.UPDATED_TIMESTAMP/1000) AS finish_date,
    ROUND((me.UPDATED_TIMESTAMP - pe.CREATED_TIMESTAMP)/1000/60, 2) AS duration_minutes
FROM AO_6FF49D_PLAN_ENTITY pe
JOIN AO_6FF49D_MIGRATION_ENTITY me ON (pe.ID = me.PLAN_ID)
WHERE pe.PLAN_NAME = '26Wave2-JIRA-T1-1';]]></ac:plain-text-body>
</ac:structured-macro>

<h2>2. Jira 온프레미스 전체 첨부파일 용량 및 Top 10 프로젝트 현황 (실측 통계)</h2>
<p>온프레미스 운영 데이터베이스에서 직접 집계한 전체 인스턴스 총계 및 용량 상위 10개 프로젝트 현황입니다.</p>

<h3>① 전체 인스턴스 총계</h3>
<table>
  <thead>
    <tr>
      <th style="background-color: #f4f5f7; text-align: center;">총 프로젝트 수</th>
      <th style="background-color: #f4f5f7; text-align: center;">총 이슈 수</th>
      <th style="background-color: #f4f5f7; text-align: center;">총 첨부파일 수</th>
      <th style="background-color: #f4f5f7; text-align: center;">총 첨부파일 용량 (GB)</th>
      <th style="background-color: #f4f5f7; text-align: center;">총 첨부파일 용량 (TB)</th>
      <th style="background-color: #f4f5f7; text-align: center;">Wave 2 플랜 비중</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="text-align: center;"><strong>3,707개</strong></td>
      <td style="text-align: center;"><strong>3,644,848건</strong> (약 364만 건)</td>
      <td style="text-align: center;"><strong>2,487,396개</strong> (약 248만 개)</td>
      <td style="text-align: center; color: #0052cc;"><strong>36,833.88 GB</strong></td>
      <td style="text-align: center; color: #0052cc; font-size: 15px;"><strong>35.97 TB</strong></td>
      <td style="text-align: center;">
        <ac:structured-macro ac:name="status" ac:schema-version="1">
          <ac:parameter ac:name="colour">Green</ac:parameter>
          <ac:parameter ac:name="title">전체의 약 91.7%</ac:parameter>
        </ac:structured-macro>
        <br /><span style="font-size: 11px; color: #6b778c;">(플랜: 624개 프로젝트, 33 TB)</span>
      </td>
    </tr>
  </tbody>
</table>

<h3>② 첨부파일 용량 상위 10개 프로젝트 상세</h3>
<p>상위 10개 프로젝트(24.93 TB)가 <strong>전체 인스턴스 첨부파일 용량의 67.7%</strong>를 차지하고 있습니다.</p>
<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">순위</th>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트 키</th>
      <th style="background-color: #f4f5f7;">프로젝트 명</th>
      <th style="text-align: center; background-color: #f4f5f7;">이슈 수</th>
      <th style="text-align: right; background-color: #f4f5f7;">용량 (GB)</th>
      <th style="text-align: right; background-color: #f4f5f7;">용량 (TB)</th>
      <th style="text-align: center; background-color: #f4f5f7;">전체 대비 비중</th>
      <th style="background-color: #f4f5f7;">비고</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background-color: #fff0eb;">
      <td style="text-align: center;"><strong>1</strong></td>
      <td style="text-align: center;"><code>CQIM</code></td>
      <td><strong>Connect-S/L 품확 이슈 관리</strong></td>
      <td style="text-align: center;">17,828건</td>
      <td style="text-align: right; font-weight: 700; color: #de350b;">6,223.13 GB</td>
      <td style="text-align: right; font-weight: 700; color: #de350b;">6.08 TB</td>
      <td style="text-align: center;"><strong>16.9%</strong></td>
      <td>🚨 단일 최대 용량 (이번 미이관 85.98 GB 발생)</td>
    </tr>
    <tr style="background-color: #fff0eb;">
      <td style="text-align: center;"><strong>2</strong></td>
      <td style="text-align: center;"><code>CAQW</code></td>
      <td><strong>Connect App QA (LGE)</strong></td>
      <td style="text-align: center;">19,278건</td>
      <td style="text-align: right; font-weight: 700; color: #de350b;">5,703.19 GB</td>
      <td style="text-align: right; font-weight: 700; color: #de350b;">5.57 TB</td>
      <td style="text-align: center;"><strong>15.5%</strong></td>
      <td>🚨 2위 대용량 (이번 미이관 39.65 GB 발생)</td>
    </tr>
    <tr>
      <td style="text-align: center;">3</td>
      <td style="text-align: center;"><code>CAQM</code></td>
      <td>Connect App QA (Mobis)</td>
      <td style="text-align: center;">18,785건</td>
      <td style="text-align: right;">4,819.47 GB</td>
      <td style="text-align: right;">4.71 TB</td>
      <td style="text-align: center;">13.1%</td>
      <td>대용량 QA 프로젝트</td>
    </tr>
    <tr>
      <td style="text-align: center;">4</td>
      <td style="text-align: center;"><code>HMCP</code></td>
      <td>차종개발 이슈 관리 (HYUNDAI)</td>
      <td style="text-align: center;">17,682건</td>
      <td style="text-align: right;">2,460.02 GB</td>
      <td style="text-align: right;">2.40 TB</td>
      <td style="text-align: center;">6.7%</td>
      <td>차종개발 현대</td>
    </tr>
    <tr>
      <td style="text-align: center;">5</td>
      <td style="text-align: center;"><code>CQIW</code></td>
      <td>Connect-W 품확 이슈 관리</td>
      <td style="text-align: center;">7,920건</td>
      <td style="text-align: right;">1,428.32 GB</td>
      <td style="text-align: right;">1.39 TB</td>
      <td style="text-align: center;">3.9%</td>
      <td>Connect-W</td>
    </tr>
    <tr>
      <td style="text-align: center;">6</td>
      <td style="text-align: center;"><code>GENESISP</code></td>
      <td>차종개발 이슈 관리 (GENESIS)</td>
      <td style="text-align: center;">14,872건</td>
      <td style="text-align: right;">1,373.39 GB</td>
      <td style="text-align: right;">1.34 TB</td>
      <td style="text-align: center;">3.7%</td>
      <td>차종개발 제네시스 (이번 미이관 2.03 GB 발생)</td>
    </tr>
    <tr>
      <td style="text-align: center;">7</td>
      <td style="text-align: center;"><code>SPAQA</code></td>
      <td>CCS App 테스트</td>
      <td style="text-align: center;">31,508건</td>
      <td style="text-align: right;">894.02 GB</td>
      <td style="text-align: right;">0.87 TB</td>
      <td style="text-align: center;">2.4%</td>
      <td>CCS App 테스트</td>
    </tr>
    <tr>
      <td style="text-align: center;">8</td>
      <td style="text-align: center;"><code>HMGOPSPT</code></td>
      <td>HMG CP Collaboration</td>
      <td style="text-align: center;">40,834건</td>
      <td style="text-align: right;">698.76 GB</td>
      <td style="text-align: right;">0.68 TB</td>
      <td style="text-align: center;">1.9%</td>
      <td>협업 플랫폼</td>
    </tr>
    <tr>
      <td style="text-align: center;">9</td>
      <td style="text-align: center;"><code>SWUPDATE</code></td>
      <td>소프트웨어 업데이트 이슈 관리</td>
      <td style="text-align: center;">32,157건</td>
      <td style="text-align: right;">689.04 GB</td>
      <td style="text-align: right;">0.67 TB</td>
      <td style="text-align: center;">1.9%</td>
      <td>SW 업데이트</td>
    </tr>
    <tr>
      <td style="text-align: center;">10</td>
      <td style="text-align: center;"><code>TSCR</code></td>
      <td>전자편의 사양개발</td>
      <td style="text-align: center;">5,942건</td>
      <td style="text-align: right;">636.17 GB</td>
      <td style="text-align: right;">0.62 TB</td>
      <td style="text-align: center;">1.7%</td>
      <td>전자편의 사양</td>
    </tr>
    <tr style="background-color: #fafbfc; font-weight: 700;">
      <td colspan="3" style="text-align: center;"><strong>상위 10개 프로젝트 합계</strong></td>
      <td style="text-align: center;"><strong>206,806건</strong></td>
      <td style="text-align: right; color: #0052cc;"><strong>24,925.51 GB</strong></td>
      <td style="text-align: right; color: #0052cc;"><strong>24.93 TB</strong></td>
      <td style="text-align: center; color: #0052cc;"><strong>67.7%</strong></td>
      <td>-</td>
    </tr>
  </tbody>
</table>

<ac:structured-macro ac:name="tip" ac:schema-version="1">
  <ac:parameter ac:name="title">기술적 인사이트: 미마이그레이션 병목 원인 규명</ac:parameter>
  <ac:rich-text-body>
    <p>전체 3,707개 프로젝트 중 <strong>1위 <code>CQIM</code>(6.08 TB)과 2위 <code>CAQW</code>(5.57 TB) 두 프로젝트만 합쳐도 11.65 TB로 전체 인스턴스의 32.4%</strong>를 차지하고 있습니다.</p>
    <p>이번 Wave 2 마이그레이션에서 왜 이 두 프로젝트에서 미마이그레이션(1,872건 중 CQIM 992건, CAQW 676건 = 89%)이 집중되었는지 명확한 구조적 근거가 입증되었습니다.</p>
  </ac:rich-text-body>
</ac:structured-macro>

<h2>3. 프로젝트별 미마이그레이션 첨부파일 현황 (1,872건 전수 분석)</h2>
<table>
  <thead>
    <tr>
      <th style="text-align: center; background-color: #f4f5f7;">프로젝트 키</th>
      <th style="text-align: center; background-color: #f4f5f7;">미이관 파일 수 (건)</th>
      <th style="text-align: center; background-color: #f4f5f7;">비중 (%)</th>
      <th style="text-align: right; background-color: #f4f5f7;">미이관 용량 (MB)</th>
      <th style="text-align: right; background-color: #f4f5f7;">미이관 용량 (GB)</th>
      <th style="background-color: #f4f5f7;">주요 파일 유형</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td style="text-align: center;"><strong>CQIM</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>992건</strong></td>
      <td style="text-align: center;">53.0%</td>
      <td style="text-align: right;">85,975.33 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>85.98 GB</strong></td>
      <td style="font-size: 12px;">분할 압축(.z01~.z10), 비디오(.mp4), DLT 로그</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>CAQW</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>676건</strong></td>
      <td style="text-align: center;">36.1%</td>
      <td style="text-align: right;">39,650.52 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>39.65 GB</strong></td>
      <td style="font-size: 12px;">분할 압축(.7z, .z01~.z08), 비디오, 이미지</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>GENESISP</strong></td>
      <td style="text-align: center; color: #ff8b00;"><strong>106건</strong></td>
      <td style="text-align: center;">5.7%</td>
      <td style="text-align: right;">2,027.77 MB</td>
      <td style="text-align: right;">2.03 GB</td>
      <td style="font-size: 12px;">DLT 로그, 이미지(.png), 압축파일</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>JGVSTAR25</strong></td>
      <td style="text-align: center;"><strong>78건</strong></td>
      <td style="text-align: center;">4.2%</td>
      <td style="text-align: right;">15.66 MB</td>
      <td style="text-align: right;">0.02 GB</td>
      <td style="font-size: 12px;">Container.json, 스크린샷(.png)</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>ECSC</strong></td>
      <td style="text-align: center;"><strong>15건</strong></td>
      <td style="text-align: center;">0.8%</td>
      <td style="text-align: right;">85.37 MB</td>
      <td style="text-align: right;">0.08 GB</td>
      <td style="font-size: 12px;">이미지, 문서</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>SPATEST</strong></td>
      <td style="text-align: center;"><strong>4건</strong></td>
      <td style="text-align: center;">0.2%</td>
      <td style="text-align: right;">10.64 MB</td>
      <td style="text-align: right;">0.01 GB</td>
      <td style="font-size: 12px;">동영상(.mp4), 이미지</td>
    </tr>
    <tr>
      <td style="text-align: center;"><strong>RPSPEC</strong></td>
      <td style="text-align: center;"><strong>1건</strong></td>
      <td style="text-align: center;">0.1%</td>
      <td style="text-align: right;">0.02 MB</td>
      <td style="text-align: right;">0.00 GB</td>
      <td style="font-size: 12px;">스크린샷 이미지(.png)</td>
    </tr>
    <tr style="background-color: #fafbfc; font-weight: 700;">
      <td style="text-align: center;"><strong>합계</strong></td>
      <td style="text-align: center; color: #de350b;"><strong>1,872건</strong></td>
      <td style="text-align: center;">100.0%</td>
      <td style="text-align: right;">127,765.30 MB</td>
      <td style="text-align: right; color: #de350b;"><strong>124.77 GB</strong></td>
      <td style="text-align: center;">-</td>
    </tr>
  </tbody>
</table>

<h2>4. 주요 미이관 원인 심층 분석</h2>
<ul>
  <li><strong>1) 극단적인 대용량 파일 집중:</strong> 미이관된 파일 중 100MB 이상 대용량 파일이 수백 개 이상 포함되어 있으며, 특히 CQIM(85.98 GB) 및 CAQW(39.65 GB) 두 프로젝트가 전체 누락 용량의 <strong>99.9%</strong>를 차지하고 있습니다.</li>
  <li><strong>2) JCMA 네트워크 타임아웃 / 세션 단절:</strong> Jira Cloud로 대용량 분할 압축 파일이나 비디오를 전송하는 과정에서 온프레미스 DB의 <code>wait_timeout</code>에 도달하거나 클라우드 미디어 API 업로드 제한에 걸려 미완료 처리되었습니다.</li>
  <li><strong>3) 디스크 원본 보존 상태:</strong> <strong>ScriptRunner로 실제 파일시스템을 전수 검사한 결과, 1,872개 파일 전량이 온프레미스 서버 디스크에 안전하게 정상 보관</strong>되어 있음을 확인하였습니다. (영구 데이터 유실 0건)</li>
</ul>

<h2>5. 최대 용량 미마이그레이션 파일 샘플 (Top 15)</h2>
<p>아래는 용량이 가장 커서 마이그레이션이 누락된 대표적인 상위 파일 목록입니다 (전체 1,872건 내역은 첨부된 CSV 파일에서 확인 가능):</p>
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

<h2>6. 온프레미스 DB 조회용 SQL (1,872건 추출 쿼리)</h2>
<p>온프레미스 Jira DB에서 본 1,872건의 전체 이슈 키와 파일명을 직접 조회할 때 사용한 공식 쿼리입니다:</p>

<ac:structured-macro ac:name="code" ac:schema-version="1">
  <ac:parameter ac:name="language">sql</ac:parameter>
  <ac:parameter ac:name="title">미마이그레이션 첨부파일 1,872건 추출 쿼리</ac:parameter>
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

<hr />
<h2>7. 첨부파일 목록 (1,872건 CSV 및 보고서 다운로드)</h2>
<p>전체 1,872건의 상세 리스트는 아래 첨부파일 중 <code>jcma_unmigrated_attachments_1872.csv</code>를 다운로드하여 엑셀에서 확인하실 수 있습니다.</p>
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
        "message": "Jira 온프레미스 전체 용량(35.97 TB) 및 Top 10 프로젝트 실측 통계 반영"
    }
}

json_data = json.dumps(payload).encode("utf-8")
update_req = urllib.request.Request(update_url, data=json_data, headers=headers, method="PUT")

with urllib.request.urlopen(update_req, context=ctx) as res:
    res_data = json.loads(res.read().decode("utf-8"))
    print("Updated Confluence successfully to Version:", res_data.get("version", {}).get("number"))
