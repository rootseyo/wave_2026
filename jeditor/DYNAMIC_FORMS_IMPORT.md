# JEditor → Dynamic Forms Cloud

Windows 터미널에서 jeditor 폴더로 이동한 뒤 실행합니다.

```powershell
python build_dynamic_forms_import.py
```

입력은 실제 데이터의 `template_report.html`과 대상 Cloud의 `id-mappings-jira-classic-customField.html`입니다. 매핑 파일은 현재 폴더에서 먼저 찾고, 없으면 상위 폴더에서 찾습니다. Export 샘플 JSON은 필요 없습니다.

출력은 `dynamic_forms_import/프로젝트/Cloud필드ID/값해시/import.json`입니다. 각 파일에 필드 하나와 ADF 본문을 설정하는 SV 규칙 하나를 넣습니다. 같은 프로젝트·필드·값은 하나로 합치며 다른 값은 별도 폴더에 저장합니다. 이슈 유형별로 파일을 나누지는 않지만, 최신 Step 5의 settings_validation.version=1, status=PASS 및 is_migration_target=true인 항목만 변환합니다. 기존 리포트는 최신 Step 5로 다시 생성해야 합니다. 필드 없는 템플릿은 제외하고 매핑 누락·중복 또는 ADF 오류가 있으면 생성을 중단합니다.

```powershell
python build_dynamic_forms_import.py actual-template-report.html --mapping "id-mappings-jira-classic-customField.html" --project ATCU --output-dir dynamic_forms_atcu
```

출력 폴더가 이미 있으면 교체 안내 후 동일 경로의 import.json과 mapping-report.json을 덮어씁니다. 이번에 생성하지 않는 이전 파일은 유지되므로 실제 적용 파일 목록은 최신 mapping-report.json의 files를 기준으로 확인하세요. 기존 저장소의 template_report.html은 예제이므로 실제 이전에는 실제 리포트를 사용하세요.

`mapping-report.json`에 파일별 원본 템플릿, 필드 매핑, 이슈 유형·가시성·자동 채우기 정보를 기록합니다. 이 파일은 확인용이며 import하지 않습니다.

필드 ID는 매핑 파일로 변환하며 필드 타입은 JEditor 본문용 textarea로 가정합니다. 실제 Cloud 필드 타입과 지원 여부를 export 정보로 검증하려면 `--sample "export.json"`을 추가하세요. 기본 envelope는 제공된 샘플 구조를 사용하며 currentVersion은 40, sourceUrl은 빈 문자열입니다. 실제 import 호환성은 테스트 환경에서 확인해야 합니다.

대상 Dynamic Forms 설정에서 필요한 import.json을 Import → Replace fields로 불러오고 draft를 검토한 뒤 APPLY CHANGES로 적용합니다. 포함된 필드의 기존 규칙은 교체됩니다. 같은 필드에 여러 파일을 순차 import하면 마지막 파일의 규칙으로 교체되므로 필요한 값과 적용 범위를 먼저 선택하세요.

이슈 유형·가시성 조건은 JSON에 넣지 않습니다. 원본 조건은 매핑 리포트에서 확인하고 대상 설정에 직접 지정하세요. 이미지·첨부·멘션은 별도 이전이 필요하므로 해당 ADF는 생성하지 않습니다. 기본 ADF 구조만 확인하며 전체 Atlassian 스키마 검증은 하지 않습니다.

상위 ../AGENTS.md에 따라 로컬에서는 실제 변환 및 연동 테스트를 실행하지 않습니다.

댓글(comment) 템플릿 변환은 아직 구현하지 않았습니다. 댓글 항목만 제외하고 나머지 필드는 계속 생성하며, 제외 내역은 mapping-report.json의 excluded에 남깁니다. 댓글만 있는 입력도 제외 보고서를 생성합니다.

Step 5는 기본적으로 스크립트 상위 폴더의 projects.txt에 있는 프로젝트만 처리합니다(DB 조회 및 HTML 덤프 모드 모두). --project ATCU,BDD로 목록을 직접 지정할 수 있으며 이 값이 우선합니다. 기본 파일이 없거나 유효한 프로젝트가 없으면 실행을 중단합니다.

Visibility의 All/ALL은 대소문자와 관계없이 전체 범위를 뜻합니다. 필드·이슈 유형 비교에서 ALL은 모든 구체적인 Autofill ID를 포함합니다. Visibility 사용자 그룹 값 전체가 ALL이면 그룹 제한 없음으로 처리합니다. 빈 값·누락은 ALL로 추정하지 않습니다. Autofill 필드에는 실제 대상 ID가 필요합니다. Autofill 이슈 유형의 All/ALL은 전체 이슈 유형으로 처리하며, Visibility 이슈 유형도 ALL일 때 범위를 충족합니다. 최신 Step 5로 리포트를 다시 생성한 뒤 import 생성기를 실행하세요.

## Default Value 우선 처리

다음 순서로 실행합니다 (기본 ops, stg는 두 실행기에 --env stg 지정).

```powershell
python run_default_values.py
python step5_check_jeditor_templates_sql.py --env ops
python build_dynamic_forms_import.py
```

필수 추가 파일: default_value_policy.py, jeditor_default_values_all.sql. 기본값 실행기는 이제 Step 2를 호출하지 않고 DB 설정 메타데이터를 직접 조회합니다. --batch-size(기본 20), --cooldown(기본 0.5초), --query-timeout(기본 120초)을 지원합니다. 기타 Step 2 옵션은 전달하지 않습니다. 기존 HTML 전용 SQL은 보존하며 새 SQL을 기본으로 사용합니다.

default_values.json은 정상 조회 완료 프로젝트와 원본 DB 식별 해시, 컨텍스트·원본 XML·Renderer를 기록합니다. 조회 실패 시 complete=false로 남기므로 이전 성공 결과를 사용하지 않습니다. Step 5는 환경/원본 DB를 확인합니다. HTML 덤프 모드도 이 JSON이 필요합니다.

기본값 전체 겹침은 EXCLUDED_DEFAULT_VALUE_PRIORITY, 일부 겹침은 EXCLUDED_PARTIAL_DEFAULT_VALUE로 템플릿 전체를 제외합니다. 기본값 없음과 JEditor Renderer 확인까지 통과해야 migration_policy.status=PASS가 됩니다. 복수의 충돌 컨텍스트·해석 불가 XML·누락 설정은 UNKNOWN으로 제외합니다. XML string의 빈 문자열/빈 HTML은 기본값 없음으로 처리하고, 이미지·표는 값이 있는 것으로 봅니다. 그 외 XML 타입은 검토 대상으로 보수적으로 제외합니다.

ALL 이슈 유형의 Renderer/기본값 비교는 전체 DC 이슈 유형을 사용합니다. 프로젝트에서 사용하지 않는 유형도 포함할 수 있어 보수적으로 제외될 수 있습니다. 원본 범위를 임의 축소해 import하지 않습니다.

Renderer는 fieldlayoutschemeentity의 이슈 유형별 설정 → 기본 설정 순으로 확인합니다. 프로젝트에 스킴 연결이 없으면 기본 Field Configuration을 조회합니다. 기본 JEditor renderer 키는 jeditor-renderer이며 실제 DB 값이 다르면 확인 후 --jeditor-renderer-key 실제키로 지정하세요. 상세 비교 근거는 Step 5 Export JSON의 migration_policy.checks에 포함됩니다.

로컬은 문법·정적 검토만 수행합니다. 별도 테스트 환경에서 SQL 스키마와 실제 renderer 키를 검증해야 합니다. 기본값 JSON은 조회 당시 스냅샷이므로 Jira 설정 변경 시 다시 추출하세요.

## 진행 로그와 조회 부하

실행기는 시각·단계·배치 번호·수신 행 수·소요 시간을 즉시 출력합니다. SQL이 진행 중이면 10초마다 로그를 출력하며 로그를 위해 추가 SQL을 보내지 않습니다. DB는 한 연결로 순차 조회하고, 프로젝트 및 Renderer 레이아웃을 배치 단위로 조회합니다. 각 배치 후 읽기 트랜잭션을 종료하고 대기합니다. 전체 실행은 단일 시점 스냅샷이 아니므로 조회 중 Jira 설정 변경을 피하세요.

MySQL은 max_execution_time(밀리초), MariaDB는 max_statement_time(초)로 서버 측 SELECT 실행 시간을 제한합니다. 해당 설정을 지원하지 않거나 권한이 없으면 중단합니다. 네트워크 read_timeout도 설정하지만 서버 제한이 즉각적인 종료를 보장하는 것은 아닙니다. SQL에서 큰 XML 결과의 DISTINCT/ORDER BY를 제거했습니다. 수신은 500행씩 수행하지만 최종 JSON 생성을 위해 전체 결과는 클라이언트 메모리에 보관합니다.

jiraissue/customfieldvalue를 조회하지 않고 설정 메타데이터만 읽습니다. 전역 컨텍스트는 프로젝트마다 반복될 수 있으며 OR/EXISTS 조인 비용은 실제 데이터량과 인덱스에 따라 달라집니다. 실행계획과 운영 DB 측정 전에는 무부하를 보장할 수 없습니다.

```powershell
python run_default_values.py --batch-size 5 --cooldown 2 --query-timeout 60
```

기본 Field Configuration 조회는 Jira DB의 fieldlayout.layout_type 컬럼을 사용합니다. 기본 레이아웃과 Renderer 컬럼을 컨텍스트 배치 조회 전에 확인하여 스키마 오류를 초기에 보고합니다.
