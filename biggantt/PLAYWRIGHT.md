# BigGantt Playwright 실행 준비

## 대화형 실행

일반 사용자는 개별 스크립트의 파라미터를 입력하지 않고 다음 통합 메뉴를 사용한다.

```bash
python biggantt.py
```

`Start/End Field Mapping` 메뉴에서 ID Mapping 검증, 로그인, 단일 프로젝트,
배치 및 Resume를 선택할 수 있다. 아래 개별 명령은 동작 설명과 자동화 용도로 남겨 둔다.

현재 AGENTS.md에 따라 로컬에서는 코드 수정·정적 분석·전송 준비만 수행한다.
아래 설치·기능 테스트·연동 명령은 별도 테스트 환경에서 실행한다.
Jira API 토큰과 브라우저 로그인은 서로 다른 인증이다.

## 설치

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-playwright.txt
```

설치된 일반 Google Chrome을 사용하므로 별도 Chromium 다운로드는 필요하지 않다.

## 최초 로그인 및 대기

```bash
.venv/bin/python biggantt_browser_auth.py \
  --site-url https://rootseyo.atlassian.net \
  --app-url https://rootseyo.atlassian.net/jira/apps/bf8faa39-5ae4-4289-ad2f-cc0cfdd1245a/bb4f6f47-1446-4a8e-8312-e03f32029cf5 \
  --login-timeout 1800
```

1. 자동 제어하지 않는 일반 Chrome이 전용 프로필로 열린다.
2. 사용자가 Google/SSO/MFA를 포함해 로그인한다. 비밀번호를 코드에 저장하지 않는다.
3. BigGantt 화면이 보이면 `로그인 완료 후 ok 입력 (취소: cancel):` 프롬프트에 `ok`를 입력하고 Enter를 누른다. 에이전트 실행 중에는 대화에서 `ok`를 알려도 된다.
4. 도구가 자신이 시작한 전용 Chrome만 정상 종료하고 Playwright로 같은 프로필을 연다.
5. 브라우저 세션으로 Jira `/myself`가 HTTP 200 및 accountId를 반환하는지 확인한다.
6. 인증 성공 시 storageState를 권한 0600으로 저장한다. 로그인 화면·401·timeout은 성공으로 처리하지 않는다.

빈 입력이나 다른 문자열은 계속 대기한다. `cancel` 또는 입력 스트림 종료는 취소로 처리한다.
`ok`는 로그인 검증을 시작하는 신호이며, 실제 브라우저 인증 확인을 생략하지 않는다.

Google에서 자동화 브라우저 로그인을 거부하면 기다리거나 재시도만 반복하지 않는다.
위 일반 Chrome 로그인 절차를 사용한다. 일반 Chrome에서도 거부되면 계정/SSO 정책을 해결해야 한다.

전용 프로필 `.playwright/manual-ops-profile`을 재사용하므로 매 프로젝트마다 로그인하지 않는다.
동일 프로필은 한 프로세스만 사용해야 한다. 개인 Chrome 프로필은 복사하거나 직접 열지 않는다.
`.playwright/`의 프로필과 state 파일은 인증정보이므로 Git·Confluence·테스트 Artifact에 포함하지 않는다.

## 테스트용 ID 자료

`tests/fixtures/id-mappings.fixture.html`은 DC ID `990001`, `990002`를 사용한 합성 자료다.
Cloud ID `customfield_10015`, `customfield_10070`은 대상 사이트에서 REST로 다시 검증해야 한다.
이 fixture는 실제 마이그레이션 결과나 DC의 현재 Field Mapping을 증명하지 않는다.

```bash
.venv/bin/python validate_field_mapping.py --env ops --project CFMT \
  --id-mappings tests/fixtures/id-mappings.fixture.html \
  --dc-start-field 990001 --dc-end-field 990002 \
  --output test-results/CFMT.mapping.json
```

이 명령은 HTML을 정적으로 읽고 Jira 필드 존재·Date Picker 타입·Project/Issue Type
Context를 검증한다. `VALIDATED`는 이 사전 검증의 성공이며 UI 저장이나 화면 구성 검증을
뜻하지 않는다. Screen 검증은 결과에 `NOT_CHECKED`로 명시한다.

## Field Mapping 실행

로그인 후 관찰한 BigGantt UI를 사용하는 단일 프로젝트 실행기다.
`CFMT.mapping.json`의 프로젝트·Cloud 필드를 실행 직전에 Jira REST로 다시 검증한다.

```bash
.venv/bin/python -u biggantt_field_mapping.py dry-run \
  --manifest test-results/CFMT.mapping.json --box AGILE-69 \
  --app-url https://rootseyo.atlassian.net/jira/apps/bf8faa39-5ae4-4289-ad2f-cc0cfdd1245a/bb4f6f47-1446-4a8e-8312-e03f32029cf5
```

- `dry-run`: 현재 프로젝트 Mapping을 읽어 `SKIP/NO_CHANGE` 또는 `PENDING/WOULD_UPDATE`를 기록한다.
- `migrate`: 동일하면 `SKIP`, 다르면 지정한 Field ID를 선택하고 UI Save 후 재진입해 검증한다.
- `verify`: 저장 없이 현재 Field ID를 비교한다. 불일치는 `FAILED`다.
- `--interactive-login`: 세션 만료 시 일반 Chrome 로그인으로 전환하고 `ok` 프롬프트에서 기다린다.
- `--exercise-save`: `migrate`에서만 사용한다. 이미 값이 일치할 때 동일 값을 재선택·저장해 Save 경로를 테스트한다. 일상 마이그레이션에는 사용하지 않는다.

새로 실행할 때마다 현재 상태를 읽으므로 같은 프로젝트를 재실행하면 불필요한 저장을 하지 않는다.
Box는 프로젝트 Mapping 화면에 접근하기 위한 문맥이다. 동일 프로젝트를 사용하는 모든 Box에 영향을 주므로
프로젝트별로 한 번 실행한다. 단일 실행기를 순차 호출하는 배치 실행기는 아래에 설명한다.

### 저장 안전장치

- 실제 UI의 안정적인 control ID와 option에 표시된 `customfield_…` ID를 사용한다.
- UI가 로드한 설정 응답의 프로젝트 ID·Key와 화면의 ID를 교차 확인한다.
- 내부 API를 직접 호출하지 않는다. UI가 보내는 저장 요청을 검사하고 그대로 통과시키거나 차단한다.
- Start/End의 primary 필드 이외 Mapping, secondary 필드, 부가 설정을 변경하지 않는다.
- 관찰된 UI는 기본값으로 확장된 일부 Mapping을 저장 요청에서 생략한다. 코드에 지정된 비편집 항목이며 현재 값과 UI가 조회한 기본값이 정확히 같을 때만 생략을 허용한다. 저장 후에는 전체 Mapping과 보호 설정을 다시 비교한다.
- Jira 값 덮어쓰기 옵션(`migrateValues`)이 꺼져 있어야 한다.
- 저장 응답을 잃어도 Save를 반복하지 않고 UI를 새로 읽는다.
- 성공·SKIP은 JSON/CSV로 기록한다. 실패 시에만 Mapping dialog screenshot 및 URL·script·불필요한 속성을 제거한 HTML을 저장한다.
- App URL에 토큰이 포함될 수 있으므로 원본 DOM/HAR와 브라우저 state를 일반 결과에 기록하지 않는다.

현재 지원 범위는 단일 Jira 프로젝트로 접근할 수 있고 이미 Custom configuration이 존재하는 Mapping이다.
기본값 상속 프로젝트, Board-only Scope, Date Time Picker 등 확인하지 않은 형태는 자동 저장하지 않는다.
실행 결과는 `test-results/field-mapping-<run-id>/`에 저장하며 Box 생성이나 Jira issue 값 이관은 수행하지 않는다.

## 확인된 결과와 남은 검증

기존 결과 `test-results/field-mapping-20260911T134112864251Z/result.json`에는
CFMT / AGILE-69의 `SAVED_UNCHANGED`, HTTP 200, 허용된 저장 요청 1회,
재조회 Start `customfield_10015` / End `customfield_10070` 일치가 기록되어 있다.
이는 동일 값 저장 테스트이며 다른 필드로 변경하는 마이그레이션이나 배치 검증은 아니다.
별도 `verify` 및 일반 `migrate` 재실행의 SKIP 검증은 아직 남아 있다.

## 테스트 환경 인계

전달 대상은 아래 소스·문서·테스트 fixture다. `../env`, `.playwright/`, 브라우저 state,
실제 마이그레이션 HTML, 실행 결과는 전달 묶음에 넣지 않는다.
대상 환경에서 `../env`를 별도로 설정하고 Chrome을 설치한 뒤 직접 로그인한다.
현재 통합 실행기의 수동 로그인 Chrome 경로는 macOS 기준이다. 다른 OS에서는
통합 `--interactive-login`을 사용하기 전에 경로 지원을 추가해야 한다.
인증 도구 자체에는 `--chrome-path` 인자가 있다.

전달 파일: `biggantt_browser_auth.py`, `biggantt_field_mapping.py`,
`biggantt_field_mapping_batch.py`, `tests/test_field_mapping_batch.py`,
`field_mapping_data.py`, `validate_field_mapping.py`, `requirements-playwright.txt`,
`PLAYWRIGHT.md`, `tests/test_browser_auth.py`, `tests/test_field_mapping_data.py`,
`tests/test_field_mapping_guard.py`, `tests/fixtures/id-mappings.fixture.html`.

전송 준비된 묶음: `dist/biggantt-playwright-handoff.zip`.
승인된 전달 대상 페이지를 확인한 뒤 인자 없이 실행한다.

```bash
python send_file.py
```

스크립트가 전송 파일, Confluence 페이지 ID, 최종 전송 여부를 차례로 묻는다.
파일과 페이지 ID에서 Enter를 누르면 `dist/biggantt-playwright-handoff.zip`과
기본 페이지를 사용한다. 마지막 질문에는 `yes`를 입력해야 업로드를 시작한다.

테스트 환경에서는 압축 해제·의존성 설치 후 다음 순서로 실행한다.

1. `.venv/bin/python -m unittest discover -s tests -v`
2. 위 ID 자료 검증 명령으로 해당 환경의 manifest를 생성한다.
3. 최초 로그인 명령을 실행하고 로그인 후 터미널에 `ok`를 입력한다.
4. 위 `dry-run` 명령으로 예상 Field ID와 실제 값을 확인한다.
5. 같은 명령의 `dry-run`을 `migrate`로 바꿔 실행한다. 현재 값이 같다면 `SKIP`, 저장 요청 0회가 예상된다.
6. 같은 명령의 `dry-run`을 `verify`로 바꿔 독립 검증한다. `SUCCESS`와 저장 요청 0회를 확인한다.

같은 전용 Chrome 프로필을 사용하는 명령은 순차 실행한다. 값이 다르면 일반 `migrate`는
실제로 변경하므로 먼저 dry-run 결과를 검토한다. 기존의 동일 값 저장 테스트를 반복하기 위해
`--exercise-save`를 추가할 필요는 없다.

## 다중 프로젝트 / Resume (신규, 기능 테스트 대기)

`biggantt_field_mapping_batch.py`는 기존 단일 실행기를 프로젝트마다 순차 호출한다.
브라우저는 프로젝트마다 다시 열지만 같은 전용 프로필로 로그인 세션을 재사용한다.
로그인이 필요하면 `--interactive-login`이 각 실행기의 터미널 `ok` 프롬프트를 활성화한다.
프로젝트마다 브라우저를 재사용하는 최적화는 아직 포함하지 않았다.

다음 형식의 `batch.json`을 준비한다. manifest 경로는 batch 파일의 디렉터리 기준이다.

```json
{
  "schema_version": 1,
  "projects": [
    {"project_key": "CFMT", "box_id": "AGILE-69", "manifest": "test-results/CFMT.mapping.json"},
    {"project_key": "CFMT", "box_id": "AGILE-67", "manifest": "test-results/CFMT.mapping.json"}
  ]
}
```

위 입력은 CFMT를 한 번만 처리한다. 중복 항목의 site/project ID/Start/End ID가 다르거나
하나라도 잘못된 manifest이면 해당 프로젝트 전체를 `NEEDS_REVIEW`로 처리한다.
중복 Box 중 첫 Box를 탐색 문맥으로 사용하며, 나머지 Box의 Scope를 검증하는 기능은 아니다.

별도 테스트 환경에서 실행:

```bash
.venv/bin/python -u biggantt_field_mapping_batch.py dry-run \
  --batch batch.json --output-dir test-results/batch-dry-run \
  --app-url https://rootseyo.atlassian.net/jira/apps/bf8faa39-5ae4-4289-ad2f-cc0cfdd1245a/bb4f6f47-1446-4a8e-8312-e03f32029cf5 \
  --interactive-login
```

- 특정 프로젝트: `--project CFMT` 또는 `--projects CFMT,ABC`.
- 변경 적용: `dry-run`을 `migrate`로, 결과 디렉터리를 새 경로로 바꾼다.
- 독립 검증: `verify`와 새 결과 디렉터리를 사용한다.
- 중단 후 재개: 같은 명령·결과 디렉터리에 `--resume`을 추가한다.
- 결과: `checkpoint.json`, `field_mapping_result.csv`, `attempts/<project>/<attempt>/`.

Resume은 같은 모드·환경·프로필·프로젝트 선택에만 허용한다. SUCCESS/SKIP도 현재 Mapping을
다시 읽으며, 동일하면 기존 실행기가 저장 없이 SKIP한다. 변경이 있으면 `migrate`는 입력대로
복구하므로 저장 없는 재확인에는 별도 `verify`를 사용한다.
PENDING/FAILED는 새 시도로 실행하고, 입력이 바뀌지 않은 NEEDS_REVIEW는 자동 재시도하지 않는다.
검토 후 manifest를 수정하거나 새 결과 디렉터리로 다시 실행한다.

프로젝트 실행 전 PENDING을 원자적으로 기록하며, 중단 시 남은 PENDING은 재실행 때 실제 상태로
판단한다. child가 성공 종료해도 결과 파일과 입력 식별 정보가 맞아야 성공으로 인정한다.
한 프로젝트의 실패는 다음 프로젝트 처리를 막지 않는다. 잘못된 batch 전체 형식, 기존 checkpoint
불일치, 결과 저장 실패는 전체 작업을 중단한다. 동일 결과 디렉터리의 동시 실행은 파일 잠금으로
차단한다. 다른 디렉터리라도 같은 Chrome 프로필을 동시에 사용하지 않는다.

현재 잠금 구현은 Unix `fcntl`을 사용하므로 macOS/Linux 대상이다. 배치 기능은 소스 정적 분석만
수행한 상태이며, 새 테스트 6개와 실제 실패 격리·중단/Resume·로그인 만료 시나리오는 테스트
환경에서 확인해야 한다. 자동 Save 재시도나 임의의 고정 지연은 추가하지 않았다.
