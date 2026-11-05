# Exalate Migration Audit Tool (Jira Data Center)

Jira Data Center에서 Jira Cloud로의 마이그레이션 사전 조사를 위해, Jira Data Center에 설치된 **Exalate**의 Connection / Trigger / Sync Rule을 보안 관점에서 점검하는 안전한 Read-Only 감사 도구입니다. Queue 및 오류 조사는 별도 옵션으로 제공합니다.

프로젝트 루트의 `../env` 설정 파일 및 변수 규칙(`JIRA_DC_OPS_*`, `JIRA_DC_STG_*`, `JIRA_SSL_VERIFY`)을 기본으로 탐색하여 동작합니다.

---

## 🎯 조사 목적

이 도구의 기본 조사 대상은 Queue 건수가 아니라 **Jira Data Center의 Exalate 설정을 Jira Cloud에 적용하기 전 보안 검토**입니다.

1. Connection의 Outgoing / Incoming Sync Rule과 Trigger 설정을 읽기 전용으로 확인합니다.
2. Jira Cloud로 반출될 수 있는 첨부파일, 댓글/워크로그, 사용자정보, 보안등급, Custom Field, 비밀정보 및 광범위한 필드 매핑을 식별합니다.
3. 설정 REST API가 실제 환경에서 제공되는지 확인합니다. 공개 문서에 명시된 Exalate REST API는 모니터링(`syncevents`, `syncrequests`, `errors`) 범위이므로 설정 endpoint는 배포 버전별로 검증해야 합니다.
4. 설정 REST 조회가 불가능하면 DB에 바로 쓰지 않고, DBA가 실행할 수 있는 **읽기 전용 `AO_08F1AF_*` 조사계획**을 결과에 남깁니다.

정적 Groovy 분석은 잠재 반출 항목을 식별하는 보조 수단입니다. 실제 이슈 값의 분류·건수·런타임 분기까지 안전하다고 판정하지는 않습니다.

보안성 검토의 주체는 보안팀입니다. 이 저장소에는 내부 실행 환경으로 복사해 사용할 수 있는 표준 라이브러리 기반 단일 스크립트 `exalate_security_evidence.py`도 포함되어 있습니다. 이 스크립트는 Exalate 통신 서버와 Jira Cloud 사이의 데이터 흐름·endpoint·권한·설정 증적을 수집하지만 보안 승인/차단 판단은 하지 않습니다.

## ⚠️ CONFIDENTIALITY & SECURITY WARNING

```text
WARNING

Generated JSON/CSV files may contain:
- Jira issue metadata
- User information
- Remote system information
- Exalate synchronization metadata

Treat output files as confidential migration assessment data.
```

---

## 📌 핵심 주의사항: 설정 조사와 Queue 조사의 구분

`python main.py`는 설정 보안점검을 실행합니다. Queue 조사는 `--all`, `--summary`, `--outgoing`, `--incoming`, `--errors`를 명시했을 때만 실행합니다.

Queue API 결과만으로는 어떤 필드가 Jira Cloud로 전송되는지 판단할 수 없습니다. 반출 범위 판단에는 활성 Connection의 Outgoing Sync Rule, 수신 측 Incoming Sync Rule, Trigger JQL, 외부 호출 및 Custom Field 정의가 필요합니다.

기본 모드에서 설정 REST endpoint를 찾지 못하면 `exalate_configuration_audit.*`에 DB 조사 필요 여부와 안전한 SELECT 계획이 기록됩니다.

### Queue와 Sync Relation의 구분

이 도구에서 수집하는 REST API(`syncevents`, `syncrequests`) 데이터는 **현재 처리 대기 중이거나 처리 중인 트랜잭션(Queue)** 입니다.

```text
* Outgoing Queue != 전체 Outgoing Sync Issue
* Incoming Queue != 전체 Incoming Sync Issue
```

* **Outgoing Queue**: 현재 로컬 Jira에서 원격 인스턴스로 전송 대기/진행 중인 이벤트 트랜잭션
* **Incoming Queue**: 원격 인스턴스에서 도착하여 로컬 Jira에 적용 대기/진행 중인 요청 트랜잭션
* **Error**: 동기화 처리 실패 및 오류 항목
* **전체 동기화된 이슈 목록(Synced Issues / Relations)**: 대기열이 아닌 이미 동기화되어 유지되고 있는 Issue Mapping 데이터이며, 이는 **Phase 2 (Jira ActiveObjects DB 조회)** 단계에서 수집합니다.

---

## 🚀 주요 기능

1. **설정 중심 보안 감사**
   * `python main.py` 또는 `python main.py --config-audit`로 실행
   * Connection, Trigger, Outgoing/Incoming Rule의 Cloud 반출 위험 정적 분석
   * 결과: `exalate_configuration_audit.html/json`, `exalate_configuration_findings.csv`
2. **REST 설정 조회 가능 여부 확인**
   * `EXALATE_CONNECTIONS_ENDPOINT`, `EXALATE_TRIGGERS_ENDPOINT`로 실제 설치 버전의 endpoint를 명시 가능
   * 명시하지 않으면 후보 endpoint를 GET으로만 점검
   * `--config-json`으로 DBA/벤더가 제공한 설정 snapshot을 비밀정보 제거 후 분석 가능
3. **DB fallback 조사계획**
   * 설정 endpoint가 없으면 `AO_08F1AF_TRIGGER`, `AO_08F1AF_TRIGGER_CONFIG_DTO`, `AO_08F1AF_INSTANCE`, `AO_08F1AF_RELATION` 등 확인 대상과 읽기 전용 SELECT 예시를 출력
   * DB 연결/변경 작업은 자동 수행하지 않음
4. **`../env` 완벽 연동 및 타겟 환경 스위칭**
   * 상위 디렉토리의 `../env` 파일을 자동으로 탐지하여 로드합니다.
   * `JIRA_DC_OPS` (운영 환경) 및 `JIRA_DC_STG` (스테이징 환경)을 `--target` 옵션으로 간편하게 전환 가능
   * `JIRA_SSL_VERIFY=False` 설정 시 SSL 경고(`InsecureRequestWarning`)를 자동으로 억제
5. **안전한 운영 환경 Read-Only 보장**
   * HTTP `GET` 요청만 허용하며, `POST`, `PUT`, `PATCH`, `DELETE` 요청은 코드 레벨에서 엄격히 차단됩니다.
   * Jira DB에 쓰기 작업을 수행하지 않으며, 향후 DB 연동 시에도 Read-Only 원칙을 준수합니다.
6. **다양한 인증 방식 분리 지원**
   * Jira Data Center **Personal Access Token (PAT, Bearer)** 지원 (우선 적용)
   * HTTP Basic Authentication (Username + Password) 지원
7. **HTTP 통신 안정성 및 방어적 에러 핸들링**
   * Rate Limit (HTTP 429) 및 서버 일시 오류 (HTTP 500, 502, 503, 504) 시 Exponential Backoff를 적용한 자동 재시도
   * HTTP 401(인증 실패), 403(권한 부족), 404(앱 미설치/경로 오류)에 대한 명확한 진단 메시지
8. **민감정보 보호 (Credential Masking)**
   * 로그 및 콘솔 출력 시 패스워드, PAT 토큰, Authorization 헤더, 쿠키, 세션 ID 자동 마스킹 (`***REDACTED***`)
9. **Exalate 버전에 따른 탐색적 Schema 감지 (Exploratory Schema Logging)**
   * 최초 API 호출 시 Response의 Status Code, Content-Type, JSON Root Keys, Result Item Keys를 `DEBUG` 로그에 기록
10. **안전한 Pagination**
   * 대량 데이터 수집 시 limit/offset 기반 페이지네이션 및 무한루프 방지 안전 가드(`MAX_PAGES`) 제공
11. **확장 가능한 아키텍처**
   * `AuditCollector` 추상 클래스를 기반으로 REST Collector(`ExalateRestCollector`)와 향후 DB 수집기(`ExalateDatabaseCollector`) 지원

---

## 📁 프로젝트 구조

```text
.
├── README.md                           # 프로젝트 설명서 및 가이드
├── requirements.txt                    # 의존성 패키지 목록
├── .env.example                        # 환경 변수 예시 파일
├── main.py                             # CLI 실행 엔트리포인트
│
├── exalate/                            # Exalate 전용 패키지
│   ├── __init__.py
│   ├── config.py                       # ../env 설정 로더 및 OPS/STG 타겟 해석기
│   ├── auth.py                         # 인증 공급자 (BasicAuth, PAT 분리)
│   ├── client.py                       # Strict Read-Only HTTP 클라이언트 (Retry, Backoff)
│   ├── collector.py                    # Collector 추상화 (REST 및 향후 DB 수집기)
│   ├── monitoring.py                   # Monitoring REST API 구현체 (syncevents, syncrequests, errors)
│   ├── config_audit.py                 # 설정 REST 탐색 및 Rule/Trigger 보안 정적 분석
│   └── models.py                       # 요약 데이터 모델 및 방어적 필드 추출기
│
├── report/                             # 결과물 리포트 패키지
│   ├── __init__.py
│   └── exporter.py                     # Summary 및 상세 데이터 JSON/CSV 내보내기 (pandas)
│
├── util/                               # 유틸리티 패키지
│   ├── __init__.py
│   └── logging.py                      # 민감정보 마스킹 로거 및 응답 스키마 분석기
│
├── tests/                              # pytest 단위/통합 테스트 (27개 테스트)
│   ├── __init__.py
│   ├── conftest.py                     # 테스트 fixture 및 mock 정의
│   ├── test_config.py                  # ../env 설정 및 타겟 파싱 테스트
│   ├── test_client.py                  # HTTP 에러, retry, 인증, 마스킹 테스트
│   ├── test_monitoring.py              # API 카운트, 페이지네이션 테스트
│   ├── test_collector_and_exporter.py  # Collector 및 JSON/CSV 익스포터 테스트
│   └── test_cli.py                     # CLI End-to-End 실행 테스트
│
└── output/                             # 조사 결과 파일 생성 디렉토리
```

---

## ⚙️ 환경변수 (`../env`) 설정 안내

실행 위치 기준 상위 디렉토리의 `../env` 파일에 정의된 변수를 자동으로 읽어옵니다. 별도의 `.env` 복사 없이 기존 `../env` 파일을 그대로 사용하실 수 있습니다.

### 매핑되는 변수 목록

#### 1. Operations (운영 환경: `JIRA_DC_OPS`)
* URL: `JIRA_DC_OPS_URL` (또는 `OPS_JIRA_URL`)
* 토큰: `JIRA_DC_OPS_API_TOKEN` (또는 `JIRA_DC_OPS_TOKEN`)
* 계정: `JIRA_DC_OPS_USERNAME` (또는 `OPS_JIRA_USERNAME`)
* 비밀번호: `JIRA_DC_OPS_PASSWORD` (또는 `OPS_JIRA_PASSWORD`)

#### 2. Staging (검증 환경: `JIRA_DC_STG`)
* URL: `JIRA_DC_STG_URL` (또는 `STG_JIRA_URL`)
* 토큰: `JIRA_DC_STG_API_TOKEN` (또는 `JIRA_DC_STG_TOKEN`)
* 계정: `JIRA_DC_STG_USERNAME` (또는 `STG_JIRA_USERNAME`)
* 비밀번호: `JIRA_DC_STG_PASSWORD` (또는 `STG_JIRA_PASSWORD`)

#### 3. SSL 및 일반 설정
* SSL 검증: `JIRA_SSL_VERIFY` (또는 `VERIFY_SSL`)
* 결과 저장 경로: `OUTPUT_DIR` (기본값: `./output`)
* 타임아웃: `REQUEST_TIMEOUT` (기본값: `30`)
* 최대 재시도: `MAX_RETRIES` (기본값: `5`)
* 페이지당 요청 수: `PAGE_SIZE` (기본값: `50`)
* 최대 페이지 수: `MAX_PAGES` (기본값: `1000`)
* 설정 endpoint(선택): `EXALATE_CONNECTIONS_ENDPOINT`, `EXALATE_TRIGGERS_ENDPOINT`

---

## 💻 실행 방법 (CLI)

### 1. 기본 실행 (설정 보안점검)
기본적으로 `JIRA_DC_OPS` 설정을 사용하며 `../env` 파일을 자동 로드합니다. Connection/Trigger 설정 REST endpoint가 없으면 DB 조사계획을 리포트합니다.

```bash
python main.py
```

독립 실행형 증적 수집:

```bash
python3 exalate_security_evidence.py --target STG
```

이 명령은 기본 `../env`에서 `CONF_DC_STG_*`/`STG_CONF_*`, `JIRA_DC_STG_*`, `JIRA_CLOUD_SANDBOX_*`를 읽습니다. 운영 환경은 `--target OPS`를 사용합니다. 실시간 설정 REST 확인이 필요한 경우에는 `--rest-endpoint KIND=URL`을 사용합니다. 인증정보는 환경변수로만 전달하며 모든 HTTP 요청은 GET입니다.

설정 본문이 없는 경우에는 다음처럼 보안팀/DBA가 redacted snapshot을 제공해야 합니다.

```bash
python3 exalate_security_evidence.py \
  --target STG \
  --input ./exalate-config-redacted.json
```

또는 명시적으로:

```bash
python main.py --config-audit
```

DBA/벤더가 제공한 설정 snapshot을 분석할 때:

```bash
python main.py --config-json ./exalate-config-snapshot.json
```

### 2. 타겟 환경 지정 (OPS vs STG)
`--target` (또는 `--env`) 플래그를 통해 대상을 지정할 수 있습니다:

```bash
# Staging DC 조사
python main.py --target STG
# 또는
python main.py --target JIRA_DC_STG

# Operations DC 조사
python main.py --target OPS
# 또는
python main.py --target JIRA_DC_OPS
```

### 3. Queue 요약(Summary)만 빠르게 조회 (`limit=0` 쿼리)
상세 데이터를 가져오지 않고 현재 큐 및 에러 건수만 빠르게 확인합니다:

```bash
python main.py --summary
```

### 4. Queue 개별 대상만 조사

```bash
# Outgoing Queue만 조사
python main.py --outgoing

# Incoming Queue만 조사
python main.py --incoming

# Errors만 조사
python main.py --errors
```

### 5. 디버그 모드 (실제 Exalate API 응답 스키마 확인)
운영 중인 Exalate 플러그인의 실제 API 응답 키 및 구조를 확인할 수 있습니다:

```bash
python main.py --all --debug
```

### 6. 특정 env 파일 지정

```bash
python main.py --env-file ../env --target STG
```

---

## 📊 콘솔 출력 예시

```text
====================================================
        Exalate Migration Audit (Jira DC)        
====================================================
Target Environment : JIRA_DC_OPS
Jira Base URL      : https://dev-jira.osci.kr
Config File        : ../env
Authentication     : PersonalAccessToken (Bearer ***)
SSL Verification   : Disabled
----------------------------------------------------
Outgoing Queue : 10
Incoming Queue : 3
Errors         : 2
----------------------------------------------------

OUTGOING
Collected: 10

INCOMING
Collected: 3

ERRORS
Collected: 2

----------------------------------------------------
Reports Generated:
  output/exalate_summary.json
  output/exalate_summary.csv
  output/exalate_outgoing.json
  output/exalate_outgoing.csv
  output/exalate_incoming.json
  output/exalate_incoming.csv
  output/exalate_errors.json
  output/exalate_errors.csv
====================================================

[IMPORTANT MIGRATION NOTE]
  * Outgoing Queue != Total Outgoing Synced Issues
  * Incoming Queue != Total Incoming Synced Issues
  * These metrics reflect in-flight / pending queue transactions.
  * Synced Issue Relations / Connections require separate assessment (Phase 2).
====================================================
```

---

## 📄 생성되는 결과 파일

`OUTPUT_DIR` (기본값 `./output`)에 HTML, JSON, CSV 형태로 모든 리포트가 생성됩니다:

| 파일명 | 형식 | 설명 |
| :--- | :--- | :--- |
| **`exalate_report.html`** | **HTML (Master)** | **통합 인터랙티브 대시보드 (Summary + Outgoing + Incoming + Errors 탭 및 실시간 검색)** |
| `exalate_summary.html` | HTML | 요약 대시보드 카드 및 메트릭 현황 웹 리포트 |
| `exalate_summary.json` | JSON | 대상 Jira URL, 수집 일시, 큐 및 에러 총계 / 수집 건수 |
| `exalate_summary.csv` | CSV | `OUTGOING`, `INCOMING`, `ERROR` 유형별 건수 테이블 |
| `exalate_outgoing.html` | HTML | Outgoing Sync Queue 테이블 웹 리포트 (실시간 검색 지원) |
| `exalate_outgoing.json` | JSON | Outgoing Sync Event API 원본 응답 목록 (무손실 보존) |
| `exalate_outgoing.csv` | CSV | Outgoing 이벤트 정규화 테이블 (`id`, `issueKey`, `status`, `connectionName` 등) |
| `exalate_incoming.html` | HTML | Incoming Sync Queue 테이블 웹 리포트 (실시간 검색 지원) |
| `exalate_incoming.json` | JSON | Incoming Sync Request API 원본 응답 목록 (무손실 보존) |
| `exalate_incoming.csv` | CSV | Incoming 요청 정규화 테이블 (`id`, `issueKey`, `status`, `connectionName` 등) |
| `exalate_errors.html` | HTML | Exalate Error 테이블 웹 리포트 (오류 유형별 배지 및 검색 지원) |
| `exalate_errors.json` | JSON | Exalate Error API 원본 응답 목록 (무손실 보존) |
| `exalate_errors.csv` | CSV | 에러 상세 정규화 테이블 (`id`, `issueKey`, `connection`, `errorType`, `message` 등) |
| `exalate_configuration_audit.html` | HTML | Connection/Trigger/Sync Rule 보안점검 및 DB 조사계획 |
| `exalate_configuration_audit.json` | JSON | 설정 조회 근거, 정규화된 Connection/Trigger, 보안 finding |
| `exalate_configuration_findings.csv` | CSV | 반출 위험 후보 필드 및 Cloud 호환성 검토 목록 |

---

## 🧪 테스트 실행

```bash
pytest -v
```

총 27개 단위/통합 테스트:
* `../env` 설정 파일 로드 및 `JIRA_DC_OPS`, `JIRA_DC_STG` 매핑 테스트
* HTTP 401, 403, 404 예외 처리
* HTTP 429 및 500 지수 백오프 자동 재시도
* JSON 파싱 예외 처리
* Strict Read-Only 원칙 강제 (POST, PUT, PATCH, DELETE 차단)
* 민감정보 마스킹 (토큰, 패스워드, Authorization 헤더)
* 카운트 조회, 페이지네이션 및 MAX_PAGES 가드
* Summary 및 상세 데이터 JSON/CSV 익스포터
* CLI 전체 실행 End-to-End 파이프라인
