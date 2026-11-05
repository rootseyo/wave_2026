# Jira Data Center ➡️ Cloud BigGantt / BigPicture 박스 마이그레이션 가이드

본 프로젝트는 Atlassian **Jira Data Center (DC)** 환경의 **BigGantt / BigPicture** 플러그인에 구성된 박스(Box) 데이터, 계층 구조(Hierarchy), 스코프(Jira Project Scope), 작업 설정(Task Configuration)을 안전하게 추출하여 **Jira Cloud (Sandbox / Production)** 환경으로 이관하고 검증하는 마이그레이션 자동화 도구입니다.

## 대화형 실행

명령행 파라미터 없이 통합 메뉴를 실행합니다.

```bash
python biggantt.py
```

메뉴에서 Box Export/Import/검증 또는 Start/End Field Mapping의 사전 검증,
브라우저 로그인, 단일·배치 실행과 Resume를 선택합니다. 실제 Cloud 저장이 발생하는
Migrate 작업은 실행 직전에 다시 확인합니다. 기존 개별 스크립트의 인자는 내부 호출과
자동화 호환성을 위해 유지됩니다.

---

## 📌 목차
1. [프로젝트 개요 및 역할](#1-프로젝트-개요-및-역할)
2. [사전 준비 사항 및 환경 변수 설정](#2-사전-준비-사항-및-환경-변수-설정)
3. [마이그레이션 실행 절차 (Step 1 ➡️ Step 2 ➡️ Step 3)](#3-마이그레이션-실행-절차-step-1-️-step-2-️-step-3)
4. [산출물 및 리포트 파일 안내](#4-산출물-및-리포트-파일-안내)
5. [추가 유틸리티 및 롤백 도구](#5-추가-유틸리티-및-롤백-도구)
6. [오류 발생 시 대처 가이드 (FAQ & Troubleshooting)](#6-오류-발생-시-대처-가이드-faq--troubleshooting)

---

## 1. 프로젝트 개요 및 역할

Jira Data Center에서 Jira Cloud로 전환 시, Atlassian JCMA(Jira Cloud Migration Assistant)는 BigGantt / BigPicture 전용 박스(Box) 설정 및 계층 구조 데이터를 직접 이관하지 못합니다.

본 도구는 다음과 같은 핵심 역할을 수행합니다:
- **DC 박스 메타데이터 추출**: DC BigPicture/BigGantt REST API를 통해 전체 박스 계층, 연계 Jira 프로젝트, 뷰 설정, 시작/종료일, 상태 등을 수집.
- **프로젝트 기반 정밀 필터링**: `projects.txt`에 명시된 마이그레이션 대상 Jira 프로젝트에 속한 박스만 선별.
- **Cloud 환경으로의 자동 임포트**: BigPicture Cloud REST API(`https://api.bigpicture.one/public/biggantt/rest`)를 사용하여 부모-자식 계층 관계를 유지하며 Cloud에 박스 자동 생성.
- **소유권 및 보안 권한 매핑**: Cloud 사용자 Account ID(`SCOPE_OWNER_KEY`)를 적용하여 박스 관리 권한 부여.
- **시각적 검증 대시보드 제공**: Step 1 추출 리포트, Step 2 임포트 리포트, Step 3 1:1 비교 검증 페이지를 HTML로 자동 생성.

---

## 2. 사전 준비 사항 및 환경 변수 설정

### 2.1 Python 환경 구성
- Python 3.8 이상 권장
- 필수 라이브러리 설치:
```bash
# 가상 환경 활성화 (권장)
source ../venv/bin/activate  # macOS / Linux
# Windows: ..\venv\Scripts\activate

# 필요한 패키지 설치
pip install requests python-dotenv urllib3
```

### 2.2 환경 변수 파일 (`../env`)
마이그레이션 스크립트는 보안 및 운영 격리를 위해 프로젝트 상위 디렉터리의 `../env` 파일을 참조합니다.

```env
# ==========================================
# 1. Jira Data Center (Source) 설정
# ==========================================
# Staging (STG) 환경
JIRA_DC_STG_URL=https://stg-jira.your-company.com
JIRA_DC_STG_API_TOKEN=your_jira_dc_stg_pat_token
JIRA_DC_STG_BIGGANTT_API_TOKEN=your_biggantt_dc_stg_jwt_token

# Production / Operations (OPS) 환경
JIRA_DC_OPS_URL=https://jira.your-company.com
JIRA_DC_OPS_API_TOKEN=your_jira_dc_ops_pat_token
JIRA_DC_OPS_BIGGANTT_API_TOKEN=your_biggantt_dc_ops_jwt_token

# ==========================================
# 2. Jira Cloud (Target) 설정
# ==========================================
# Sandbox 환경
JIRA_CLOUD_SANDBOX_URL=https://your-company-sandbox.atlassian.net
JIRA_CLOUD_SANDBOX_ADMIN_EMAIL=admin@your-company.com
JIRA_CLOUD_SANDBOX_API_TOKEN=your_atlassian_api_token
JIRA_BIGGANTT_SANDBOX_API_TOKEN=your_cloud_bigpicture_api_token

# Production / Operations (OPS) 환경
JIRA_CLOUD_OPS_URL=https://your-company.atlassian.net
JIRA_CLOUD_OPS_ADMIN_EMAIL=admin@your-company.com
JIRA_CLOUD_OPS_API_TOKEN=your_atlassian_api_token
JIRA_BIGGANTT_OPS_API_TOKEN=your_cloud_bigpicture_api_token

# ==========================================
# 3. 공통 매핑 및 SSL 설정
# ==========================================
# Cloud 상에서 박스 소유자로 지정할 Atlassian Account ID
SCOPE_OWNER_KEY=557058:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 사내 사설 인증서(Self-signed) 사용 시 False 권장 (기본값: False)
JIRA_SSL_VERIFY=False
```

### 2.3 대상 프로젝트 목록 (`../projects.txt`)
추출 및 마이그레이션 대상이 되는 Jira Project Key를 한 줄에 하나씩 기입합니다.
```text
PROJ_KEY_A
PROJ_KEY_B
TEAM_PROJECT_C
```

---

## 3. 마이그레이션 실행 절차 (Step 1 ➡️ Step 2 ➡️ Step 3)

> ⚠️ **주의 (반드시 순서대로 실행)**  
> **Step 1(추출)** 실행 후 `step1_box_report.html`에서 이관할 대상을 확인/체크하고, **CSV 내보내기(`migration_boxes.csv`)** 또는 `migration_box.json`을 준비한 후 **Step 2(임포트)**를 실행하고, **Step 3(검증 페이지 생성)**으로 결과를 확인합니다.

```
[ Step 1: DC Export ]               [ Manual Check / Selection ]               [ Step 2: Cloud Import ]
   step1_export_dc_boxes.py   ➡️        step1_box_report.html             ➡️       step2_import_cloud_boxes.py
         ⬇                           (체크박스 / 프로젝트 키 일괄 선택)                      ⬇
   step1_box_report.html                      ⬇                                  Cloud Box 생성 완료
   migration_box.json                migration_boxes.csv 내보내기                step2_import_cloud_boxes_report.html
                                                                                         ⬇
                                                                        [ Step 3: Verification ]
                                                                       step3_generate_verification_page.py
                                                                                         ⬇
                                                                               box_verification.html
```

---

### Step 1: Data Center 박스 데이터 추출 및 검증
Data Center의 BigPicture/BigGantt에서 대상 박스들을 추출하고 캐시 및 분석 리포트를 생성합니다.

```bash
python step1_export_dc_boxes.py
```

#### 대화형 프롬프트 안내:
1. **Source Environment 선택**: `1` (STG) 또는 `2` (OPS) 입력
2. **Dry-run 모드 선택**: `y` (실제 파일 쓰기 전 테스트) 또는 `n` (정상 추출)

#### 주요 동작:
- Jira DC PAT 및 BigGantt 토큰 유효성 Preflight 검사
- `../projects.txt`와 매칭되는 박스 선별 및 부모-자식 트리 구조화
- `migration_box.json`, `boxes.json`, `hierarchy.json`, `all_boxes_cache.json` 저장
- `step1_box_report.html` 시각화 보고서 자동 빌드

---

### Step 1.5: 대화형 메뉴얼 체크 및 CSV 내보내기 (선택)
생성된 `step1_box_report.html`을 웹 브라우저에서 열어 이관 대상 박스를 검토하고 선별합니다.

1. **체크박스 개별 선택**: 카드 뷰 또는 테이블 뷰에서 원하는 박스 행의 체크박스를 직접 체크
2. **프로젝트 키 일괄 선택**: 상단 패널에 `KEY1, KEY2, KEY3` 형태로 프로젝트 키들을 붙여넣고 `[🎯 프로젝트 키로 선택]` 클릭 시 해당 프로젝트가 연동된 Box들이 일괄 체크됨
3. **CSV 내보내기**: `[📥 선택 Box CSV 내보내기 (Step 2용)]` 버튼을 클릭하여 `migration_boxes.csv` 다운로드
4. 다운로드된 `migration_boxes.csv`를 스크립트 실행 경로(프로젝트 폴더)에 배치

---

### Step 2: Jira Cloud로 박스 데이터 임포트
Step 1(또는 리포트에서 내보낸 `migration_boxes.csv`)을 읽어 Jira Cloud BigGantt에 박스를 순서대로 생성합니다.

```bash
python step2_import_cloud_boxes.py
# 또는 특정 파일 지정 실행:
# python step2_import_cloud_boxes.py --csv migration_boxes.csv
```

#### 대화형 프롬프트 안내:
1. **Target Cloud Environment 선택**: `1` (SANDBOX) 또는 `2` (OPS Production) 입력

#### 주요 동작:
- `migration_boxes.csv` (또는 `migration_box.json`) 자동 감지 및 유효성 검사
- 상위 박스(Parent Box) 우선 생성 후 하위 박스(Child Box) 계층 연결
- Cloud 박스 소유자(`SCOPE_OWNER_KEY`) 및 프로젝트 스코프 등록
- 생성 완료된 Cloud Box ID를 파일에 갱신 기록
- 결과 리포트 `step2_import_cloud_boxes_report.html` 생성

---

## 4. 산출물 및 리포트 파일 안내

| 산출물 파일 | 역할 및 설명 |
| :--- | :--- |
| `step1_box_report.html` | Step 1 검증 리포트 (체크박스 선별, 프로젝트 키 일괄 선택, CSV 내보내기 기능 탑재) |
| `migration_boxes.csv` | `step1_box_report.html`에서 선택한 박스들을 내보낸 CSV 파일 (Step 2 자동 우선 인식) |
| `migration_box.json` | Step 1 기본 추출 데이터 (Step 2 호환 입력 데이터로도 계속 지원) |
| `all_boxes_cache.json` | DC 전체 박스 원본 캐시. `projects.txt` 수정 시 DC 재호출 없이 고속 재필터링 지원 |
| `hierarchy.json` | Data Center 원본 박스들의 계층적 트리 구조 JSON |
| `boxes.json` | Data Center 원본 박스 상세 목록 JSON |
| `step1_box_report.html` | Step 1 추출 결과 대시보드 (박스 수, 프로젝트 분포, 중복 분석 등) |
| `step2_import_cloud_boxes_report.html` | Step 2 임포트 결과 대시보드 (DC 박스 ID ↔ Cloud 박스 ID 1:1 매핑 결과) |
| `box_verification.html` | Step 3에서 생성하는 양측 환경 바로가기 검증 페이지 |

---

## 5. 추가 유틸리티 및 롤백 도구

### 5.1 Step 3: 최종 검증 페이지 생성 (`step3_generate_verification_page.py`)
Data Center와 Cloud의 박스 화면을 새 탭에서 즉시 1:1로 비교 검증할 수 있는 인터랙티브 HTML을 생성합니다.
```bash
python step3_generate_verification_page.py
# -> box_verification.html 생성
```

### 5.2 Cloud 박스 롤백 / 일괄 삭제 (`delete_cloud_boxes.py`)
테스트 마이그레이션 후 초기화가 필요하거나 재작업 시 Cloud에 생성된 박스를 일괄 삭제합니다.
```bash
python delete_cloud_boxes.py
```

### 5.3 Task Configuration 백업 및 복원

과거 내부 API 진단 스크립트는 `back-up/`으로 이동했다. 공식 Public API가 확인되지 않은
Start/End Field Mapping은 루트의 대화형 실행기와 Playwright UI 흐름을 사용한다.
내부 API 직접 저장 스크립트는 운영 마이그레이션에 사용하지 않는다.

---

## 6. 오류 발생 시 대처 가이드 (FAQ & Troubleshooting)

### Q1. `Error: env file not found` 또는 `Missing environment variables` 에러
- **원인**: 프로젝트 상위 디렉터리(`../env`)에 환경 변수 파일이 없거나, 선택한 환경(STG/OPS)의 필수 토큰이 비어있습니다.
- **해결**:
  1. `biggantt` 폴더의 상위 디렉터리(`..`)에 `env` 파일이 존재하는지 확인합니다.
  2. `JIRA_DC_xxx_URL`, `JIRA_DC_xxx_API_TOKEN`, `JIRA_DC_xxx_BIGGANTT_API_TOKEN` 값이 정상 입력되었는지 확인합니다.

---

### Q2. `401 Unauthorized` 또는 `403 Forbidden` 에러 발생
- **원인**: Jira PAT 토큰 또는 BigPicture/BigGantt API 토큰이 만료되었거나 권한이 부족합니다.
- **해결**:
  - **Data Center**: Jira 프로필 > Personal Access Tokens에서 PAT 재발급
  - **Jira Cloud**: [Atlassian API Token 발급 페이지](https://id.atlassian.com/manage-profile/security/api-tokens)에서 토큰 재발급
  - **BigGantt Cloud**: Jira Cloud > Apps > BigPicture/BigGantt Configuration > Public API 에서 API Token 확인

---

### Q3. `404 Not Found` (DC BigPicture REST API 호출 시)
- **원인**: Data Center BigPicture/BigGantt 버전(v7, v8 등)에 따라 REST 엔드포인트 URL 구조가 다를 수 있습니다.
- **해결**:
  - 스크립트 v1.5.0에는 자동 감지 로직이 포함되어 있습니다.
  - Jira DC 인스턴스에서 BigPicture/BigGantt 플러그인이 비활성화되어 있는지 관리자 콘솔에서 확인합니다.

---

### Q4. Step 2 실행 중 `400 Bad Request` 또는 `scopeOwnerKey` 관련 오류
- **원인**: `SCOPE_OWNER_KEY`에 지정된 사용자가 Jira Cloud에 존재하지 않거나, accountId 형식이 올바르지 않습니다.
- **해결**:
  1. Jira Cloud 관리 콘솔 > 사용자 관리(User Management)로 이동합니다.
  2. 마이그레이션 관리자 계정 프로필 URL의 맨 뒷부분(예: `712020:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)을 복사합니다.
  3. `../env` 파일의 `SCOPE_OWNER_KEY` 값을 해당 Account ID로 수정 후 재실행합니다.

---

### Q5. `SSL: CERTIFICATE_VERIFY_FAILED` 오류 발생
- **원인**: 사내 Jira Data Center 서버가 사설 SSL 인증서를 사용하여 Python `requests`가 인증서를 신뢰하지 못함.
- **해결**:
  - `../env` 파일에 `JIRA_SSL_VERIFY=False`를 지정하여 SSL 검증을 비활성화하거나, 사내 루트 인증서 경로를 `JIRA_CA_BUNDLE=/path/to/ca-bundle.crt`로 지정합니다.

---

### Q6. 박스가 0개 추출되거나 대상 박스가 누락됨
- **원인**: `../projects.txt`의 프로젝트 키와 Data Center 박스 스코프의 Jira 프로젝트 키가 일치하지 않음.
- **해결**:
  1. `../projects.txt`의 프로젝트 키 대소문자 및 오탈자를 점검합니다.
  2. 이미 1회 실행 후라면 `all_boxes_cache.json`에 전체 박스가 캐시되어 있으므로, `projects.txt` 수정 후 `python generate_step1_report.py`를 실행하여 즉시 재필터링 결과를 확인합니다.
