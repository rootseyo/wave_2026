# Back-up 파일

이 디렉터리는 현재 대화형 BigGantt 마이그레이션 흐름에서 사용하지 않는 파일을 보관한다.

| 파일 | 이동 이유 |
| --- | --- |
| `biggantt_get_task_config.py` | 특정 환경과 내부 API에 고정된 진단용 스크립트 |
| `biggantt_save_task_config.py` | 공식 Public API가 아닌 내부 API 직접 저장 실험 스크립트 |
| `test_create_sample_box.py` | 개발 중 사용한 단일 샘플 Box 생성 도구 |
| `readme.html` | 현재 `README.md`에서 생성된 과거 렌더링 산출물 |
| `step2_import_cloud_boxes.log` | 과거 실행 로그 |
| `.DS_Store` | macOS 생성 파일 |
| `__pycache__/` | Python 생성 캐시 |

Field Mapping은 루트의 `biggantt.py` → `biggantt_field_mapping.py` 흐름을 사용한다.
내부 API 직접 저장 스크립트는 운영 마이그레이션에 사용하지 않는다.
