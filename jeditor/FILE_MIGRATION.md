# data.html → Jira Cloud ADF / 첨부파일 이전

입력은 `step2_query_runner.py`가 생성한 `data.html`입니다. 각 행의 issue_key/field_id와
content를 읽어 대상 Cloud 이슈의 같은 필드를 **전체 교체**합니다.
Cloud 현재 내용은 원본 백업과 기존 첨부 조회에 사용하며, 변환 원문은 data.html입니다.

## 배포 및 실행

이슈 번호만 뒤에 붙이면 `data.html`에서 해당 이슈를 찾아 **SANDBOX 단건 실제 반영**을 수행합니다.
별도 확인 질문 없이 워커 1개로 실행하며, 기존 완료 기록을 무시합니다.
해당 이슈가 파일에 없거나 선택한 필드가 없으면 Cloud 요청 전에 오류를 표시하고 종료합니다.

```bash
python3 step4_convert_adf.py ESTRACK-14285
# 같은 방식으로 실행 가능
python3 convert_adf.py ESTRACK-14285
# 미리보기만 실행할 때
python3 step4_convert_adf.py ESTRACK-14285 --dry-run
```

이 단축 명령은 SANDBOX 전용입니다. `--issue KEY` 형식은 기존대로 기본 미리보기이며,
필요한 환경과 실제 반영 옵션을 명시해 사용할 수 있습니다.

> **💡 단건 통합 테스트 파이프라인 (Step 1 ~ Step 4)**
> Step 1(SQL 생성)부터 Step 2(DB 추출), Step 3(분석/대시보드), Step 4(ADF 변환)까지 단일 이슈로 일괄 테스트하려면 `pipeline_single_issue.py`를 사용합니다.
> ```bash
> # 단건 원클릭 미리보기
> python3 pipeline_single_issue.py AAOSFRWDEV-543
> 
> # Cloud 테스트 이슈(CFMT-3)로 실제 반영
> python3 pipeline_single_issue.py AAOSFRWDEV-543 --target-issue CFMT-3 --real-run
> ```


같은 디렉터리에 다음 파일을 배포합니다.

```bash
./send_file.py step4_convert_adf.py convert_adf.py export_data.py migration_content.py migration_attachments.py markdown_adf.py terminal_status.py requirements-adf.txt
```

테스트 환경에서:

```bash
pip install -r requirements-adf.txt
python3 step4_convert_adf.py
```

별도 옵션 없이 다음 사항을 선택합니다.

1. 입력 파일 (기본 data.html)
2. 대상 Cloud SANDBOX/OPS
3. 원본 이슈 한 건 / 원본 프로젝트 / 파일 전체
4. 필드 필터 및 source→Cloud 필드 ID 매핑 (description/environment는 동일 ID, 커스텀 필드는 매핑 필요)
5. 원본 Jira 및 Cloud SSL: 인증서 검증 생략 안내
6. 파일명 참조에 사용할 원본 Jira OPS/STG 및 동일 인증을 사용할 URL 별칭
7. 이전 SKIPPED 재평가, 워커 수, 결과 디렉터리
8. 미리보기 / 실제 반영
9. 실행 환경 요약 → Cloud `/rest/api/3/myself` 토큰 인증 테스트 → `y/n` 최종 확인 (기본 n)

인증 테스트가 실패하면 변환을 시작하지 않습니다. `y`를 입력해야 선택한 모드로 시작합니다.
이 테스트는 Cloud 인증만 확인하며 원본 Jira 인증과 개별 필드/첨부 수정 권한은 확인하지 않습니다.

단건은 원본 이슈와 다른 **대상 Cloud 테스트 이슈 번호**도 지정할 수 있습니다.
예: 원본 `AAOSFRWDEV-543`, 대상 `CFMT-3`. 해당 원본의 모든 선택 필드 행이
CFMT-3에 저장되므로 description이 교체됩니다. 파일명 첨부 조회는 원본 이슈로,
첨부 업로드는 대상 이슈로 수행합니다.

단건은 기존 `Success (Already Migrated)`/완료 journal/SKIPPED 상태를 무시합니다.
전체 행 수를 세거나 전체 행을 DOM으로 파싱하지 않고 이슈 키를 바이트 검색한 후
일치하는 행만 파싱합니다. 인덱스가 없는 파일이므로 모든 비연속 필드 행을 찾으려면
파일 끝까지 바이트 검색이 필요할 수 있습니다. 없는 이슈는 FAILED로 표시합니다.

전체 실행은 메모리맵으로 한 행씩 파싱하며 동일 Cloud 이슈의 작업은 직렬화합니다.
다른 이슈들은 선택한 워커 수로 병렬 처리합니다. `issue_status` 등 열이 추가되어도
헤더 이름으로 셀을 매핑하며, content 내부 HTML 표의 td를 바깥 행의 열로 세지 않습니다.

## content 복원 및 ADF 변환

바깥 export의 HTML escape와 표시용 `<br>\n`을 먼저 원문으로 복원합니다.
원문은 다음 형식이 섞여 있어도 처리합니다.

- Jira wiki `h2.`, `h3.`, `h4.`, `*굵게*`, `{*}굵게{*}`, `+밑줄+`
- `{code:java}...{code}` (같은 줄에 닫는 표식이 있어도 처리)
- `|셀|셀|`, `|*헤더*|*헤더*|`, `||헤더||`, Markdown 헤더 구분선
- 1열 표, 빈 셀, 한글/영문/특수문자
- 실제 `<p>`, `<h4>`, `<ul>`, 중첩 목록, 링크, HTML table, colspan/rowspan/배경색
- wiki `!URL|width=...,height=...!`, `!파일명.png!`, `[^파일.zip]`, Markdown `![alt](URL)`, HTML `<img>`와 JEditor 파일 링크

원본 Markdown 파서 토큰을 ADF로 매핑하고 HTML은 BeautifulSoup으로 파싱합니다.
HTML 목록 내부 표는 텍스트와 표의 순서를 유지해 문서 수준으로 올립니다.
셀 내부의 중첩 표는 셀의 블록 내용으로 펼치고 경고를 기록합니다.
지원하지 않는 CSS 레이아웃은 적용하지 않습니다. script/style/숨김 워터마크/주석은
제거하고 이벤트 핸들러나 위험한 링크 스킴을 ADF에 복사하지 않습니다.
Jira 계정의 숫자 mention ID는 Cloud accountId로 추측 변환하지 않고 원문으로 보존합니다.

댓글은 source comment ID가 없는 행이므로 새 댓글을 생성하지 않습니다.
전체 실행에서는 SKIPPED와 이유를, 단건 테스트에서는 FAILED와 이유를 표시합니다.
원본 댓글과 Cloud 댓글을 안전하게 대응시키는 정보 없이 기존 댓글을 임의로 교체하지 않습니다.

## 첨부파일 처리

실제 반영에서는 Cloud의 기존 attachment 목록을 조회합니다.
같은 파일명이 하나 있으면 재사용하고, 둘 이상이면 임의 선택하지 않고 실패합니다.
없으면 원본 JEditor URL 또는 원본 Jira 표준 첨부파일 URL에서 내려받아
`POST /rest/api/3/issue/{targetKey}/attachments`로 multipart 업로드합니다.
`X-Atlassian-Token: no-check`를 사용합니다. 동일 이슈/참조는 실행 중 캐시합니다.

파일명만 있는 `!image-....png!`/`[^파일.zip]`는 원본 이슈의 attachment 목록에서
정확한 파일명을 찾아 다운로드합니다. 공백/한글/퍼센트 인코딩/URL query의 `&amp;`를
복원합니다. 같은 파일명이 여러 개인 경우 임의의 파일을 고르지 않습니다.

이미지는 Cloud attachment content redirect의 Media Services UUID를 확인해 ADF
mediaSingle/media 노드로 넣습니다. 숫자 attachment ID를 media UUID로 사용하거나
임의 UUID를 만들지 않습니다. UUID를 확인하지 못하면 본문 저장은 실패합니다.
UUID 확인 방식은 Cloud의 redirect에 의존하므로 실제 환경에서 반드시 확인해야 합니다.
PNG/GIF/JPEG 원본 다운로드의 크기를 읽고, 원문 width/height가 있으면 우선 적용합니다.
기존 Cloud 이미지에서 크기를 알 수 없으면 표시용 기본 크기 640×480을 사용합니다.
일반 파일은 업로드 후 Cloud 파일 링크로 넣습니다.

다운로드는 8MB 이후 임시파일로 넘기는 spool을 사용하며 완료 후 정리합니다.
로그인/오류 HTML, 빈 다운로드를 첨부파일로 업로드하지 않습니다.
업로드 POST는 중복 생성을 막기 위해 모호한 오류 이후 자동 반복하지 않습니다.
업로드가 일부 성공하고 본문 변환이 실패하면 해당 사실을 snapshot에 기록합니다.
다음 실행은 Cloud에 남은 첨부를 재사용하며, 실패한 요청을 성공으로 간주하지 않습니다.

미리보기는 Cloud의 현재 필드/첨부 메타데이터만 GET합니다.
원본 파일 다운로드, Cloud 첨부 업로드, 필드 PUT은 하지 않습니다.
대신 첨부 위치에 미리보기 텍스트를 넣고 이전 예정 목록을 기록합니다.
따라서 미리보기 결과가 실제 다운로드 인증 성공이나 media 렌더링을 입증하지는 않습니다.

## 인증/SSL

기존 `env` 또는 `../env`를 읽으며 프로세스 환경변수가 우선합니다.
Cloud URL/ADMIN_EMAIL/API_TOKEN은 선택된 환경의 한 세트만 사용합니다.

```text
JIRA_CLOUD_OPS_URL=https://rootseyo.atlassian.net
JIRA_CLOUD_OPS_ADMIN_EMAIL=<email>
JIRA_CLOUD_OPS_API_TOKEN=<cloud token>
JIRA_DC_OPS_URL=https://jira.hmg-corp.io
JIRA_DC_OPS_API_TOKEN=<DC PAT>
# 같은 PAT로 접근 가능한 원본 별칭 URL은 명시적으로 등록
JIRA_DC_OPS_URL_ALIASES=https://jira.ccos.dev
# JEditor가 PAT를 받지 않고 세션 인증을 요구하는 경우
JIRA_DC_SESSION_COOKIE=JSESSIONID=<session>
```

STG는 JIRA_DC_STG_URL/API_TOKEN으로 별도 지정할 수 있습니다.
두 호스트에 다른 인증을 써야 하면 별도 OPS/STG 설정을 사용하세요.
DC PAT는 항상 Bearer이며, 설정된 세션 쿠키 및 USER/PASSWORD가 있으면 401/403/
로그인 리다이렉트 후에만 다음 인증 방식을 시도합니다. PAT를 Basic으로 바꾸지 않습니다.
원본 인증은 설정한 호스트/명시적 별칭에만 보내고 다른 호스트로 자동 전달하지 않습니다.
별칭을 등록하면 선택한 원본 인증을 해당 호스트에도 사용하므로 정확한 URL을 입력하세요.

Cloud와 원본 Jira 양쪽에서 인증서 및 호스트 이름 검증을 생략합니다.
SSL 관련 환경변수는 검증을 활성화하지 않으며 `--ca-bundle`은 사용할 수 없습니다.
URL/토큰/쿠키를 오류 메시지에 그대로 출력하지 않습니다.

기본 매핑 파일은 실행 폴더의 `../id-mappings-jira-classic-customField.html`입니다.
다른 HTML/JSON 파일은 `--field-map`으로 지정합니다. 커스텀 필드의 매핑이 누락되면
Cloud 조회·필드 저장 전에 해당 행을 FAILED로 처리합니다. 원본과 Cloud의 ID가 같음을
확인한 환경에서만 `--allow-same-field-ids`로 매핑 없는 커스텀 필드의 동일 ID 사용을 허용합니다.
`description`, `environment`는 기본적으로 같은 필드 ID를 사용합니다. metadata 자동 조회는 없습니다.

```json
{"customfield_12345": "customfield_67890"}
```

## 상태 기록

원본 data.html은 수정하지 않습니다. 각 실행은 `convert_adf_reports/<실행ID>/`에
원문 content, source/target 필드, Cloud 기존 내용, 변환 ADF, 첨부 이전 결과를 기록합니다.
원본 snapshot은 다운로드/업로드/PUT 전에 저장합니다. 첨부 처리 중 일부 성공 후
오류가 발생하면 본문 PUT 없이 FAILED로 기록합니다.

전체 실행은 export의 Success와 완료 journal을 재개합니다. SKIPPED는 대화형 선택에
따라 재평가합니다. 실패는 다음 실행에서 다시 처리합니다. 단건은 기록을 무시합니다.
Checkpoint는 Cloud URL/입력 파일 경로·크기·mtime/필드 필터·매핑/변환기 버전별로 분리합니다.
GET→PUT 사이 다른 사용자의 동일 필드 편집은 원자적으로 보호하지 못합니다.

## 테스트 환경 검증

이번 수정은 AST/컴파일 정적 검사만 수행했습니다. 프로젝트 AGENTS.md 지침에 따라
단위 테스트, 원본 Jira 다운로드, Cloud 업로드/필드 저장, 실제 media 표시를 로컬에서
실행하지 않았습니다. 이전 버전의 테스트 결과를 이번 버전의 성공으로 간주하지 않습니다.

```bash
python3 -m unittest discover -s tests -v
python3 step4_convert_adf.py
```

샘플: `examples/data_sample.html`은 형식 검증용이며 일부 첨부 참조는 placeholder입니다.
실제 이전은 실제 추출한 data.html로 진행하세요. 먼저 단건+미리보기로 source→target을
확인한 뒤 실제 반영을 선택해 이미지·파일·표·코드가 Jira에서 표시되는지 확인하세요.

[Cloud attachment API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-attachments/),
[ADF media](https://developer.atlassian.com/cloud/jira/platform/apis/document/nodes/media/).

## 입력 오류·중단 시 재시작

디버깅 실행 예 (Windows 가상환경):

```powershell
.\venv\Scripts\python.exe step4_convert_adf.py ESTRACK-14285 --debug
```

이슈 조회 시 `expand=names,schema`로 실제 Cloud 필드 이름과 필드 형식을 확인합니다.
콘솔의 `[INPUT]`은 변환 원문이 data.html임을 표시하고, `[BEFORE]`는 기존 Cloud 값,
`[ADF]`는 전송 문서의 제목·문단·목록·이미지·표·강조·색상 개수를 표시합니다.
`[PUT]`은 저장 응답, `[VERIFY]`는 재조회 값의 형식, `[DIFF]`는 처음 다른 경로입니다.
`--debug`에서는 원문 일부와 전송/조회 차이의 값도 콘솔에 표시합니다.
완전한 ADF 비교 결과는 스냅샷의 `verification.history`와 `verification.fields`에 저장됩니다.
재조회 비교는 내용·서식을 기준으로 하며, Jira가 추가하는 빈 `attrs: {}`와
빈 문단의 `content: []` 생략은 같은 것으로 처리합니다. 텍스트, 표 구조,
색상·강조, 셀 병합, 이미지 ID·크기 등의 실제 차이는 계속 경고로 남깁니다.
보정 차이만 있으면 `[NORMALIZED]`로 표시하고 재조회 일치로 기록합니다.
원본 JSON은 변경하지 않으며 `verification.exact_matched`에는 완전 일치 여부도 기록합니다.

실제 반영에서는 PUT 응답 이후 같은 Cloud 필드를 다시 조회하여 변환 ADF와 일치하는지
최대 3회 확인합니다. DC→Cloud 단방향 이전이므로 Cloud 값과의 비교 결과에 관계없이
선택된 필드를 원본 data.html의 변환 ADF로 덮어씁니다. PUT 요청이 수락되면 SUCCEEDED로
기록하고, 재조회 차이나 재조회 오류는 경고로 남깁니다. PUT 자체가 실패하면 FAILED입니다.
기존 값도 동일했다면 `기존 ADF와 동일 (변경 없음)`을 표시합니다. 이 경우에도 PUT을 수행합니다.
스냅샷의 `original_fields`, `fields`, `verification.fields`에서 기존 값,
전송 ADF, 재조회 값을 비교할 수 있습니다. 스냅샷에는 대상 Cloud URL도 기록됩니다.

중첩된 span/link/강조 태그 안의 이미지도 첨부 처리하며 주변 텍스트의 서식을 유지합니다.
Jira 마이그레이션 완료 후의 첨부파일 재사용은 기존과 같이 파일명으로 판단합니다.
같은 이름의 첨부파일이 여러 개이면 모호성 오류로 처리합니다.

입력 파싱 오류나 Ctrl+C가 발생하면 아직 시작하지 않은 작업은 취소하고,
진행 중인 작업이 종료된 후 결과와 체크포인트를 저장하고 연결을 닫습니다.
진행 중인 Cloud 요청은 완료될 때까지 기다릴 수 있습니다.
기존 성공 체크포인트는 유지합니다. 과거 변환 결과를 다시 확인할 때는
`--issue <원본 이슈 키>`로 단건 재처리할 수 있습니다.

이번 변경 배포 파일:

```bash
./send_file.py convert_adf.py migration_content.py
```
