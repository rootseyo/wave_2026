# Jira SaaS 전환 안내 공지 설정 (Python 딕셔너리 형식)

NOTIFICATIONS = [
    {
        "title": "Jira Saas 전환 안내 - 26년 Wave 1 ($ProjectKey)",
        "contents": """안녕하세요.
2025년 하반기와 2026년 6월 Wave 0 단계 이관에 이어, **Confluence/Jira DC에서 경영지원/공통 부서에서 사용하는 공간을 HMG Atlassian Cloud로 이관할 예정** 입니다.
이에 따라 관련 조직에서는 아래 공지문을 확인하시어, 이관(Wave 1 단계)이 일정 내 원활하게 완료될 수 있도록 준비 및 협조를 요청드립니다.

### 사용자 요청 사항
- 아래 공지 사항 참조
- 공지 - [[2026년 8월 이관 공지] 경영지원/공통 부서 공간/프로젝트 (DC -> Cloud) - Atlassian Cloud 정책서 - HMG-Confluence](https://cloud-osci.atlassian.net/wiki/spaces/AC2/pages/24518855614/Wave+1+-+Announcer)

### 전환 일자: 8/24(월) 9시 부터

### 기존 시스템 프리징 : 8/21(금) 17시~

### SaaS 시스템 접속 테스트: 7/20(월) ~ 7/31(금)
- [https://hmg.atlassian.net/jira](https://hmg.atlassian.net/jira) 접속
- 오토웨이 계정 정보(e-mail/PW) 입력
- Home 화면 노출 시 접속 성공

### 접속 되지 않는 사용자
- [서비스데스크 접수](https://hmg.atlassian.net/servicedesk/customer/portal/102)
- 또는 HAE 홍권혁/김휘동/송재룡 책임에게 문의""",

        # ── 개별 알림 옵션 설정 (미지정시 ../env 환경변수 기본값 사용) ──
        # camelCase 및 snake_case 표기법 모두 지원됩니다.
        # "type": "dialog",          # dialog | flag | banner | intercept
        # "displayType": "2",        # 0=None, 1=Warning, 2=Information, 3=Error
        # "showButton": "true",      # true | false
        # "isActive": "true",        # true | false
        # "format": "1",             # 0=HTML, 1=plain, 2=Markdown
        # "reset_button": "true",    # true | false (ScriptRunner 매번 로그인 시 공지 재노출)
        # "recurrence_from": "2026-04-08 00:00",
        # "recurrence_to": "2026-04-14 09:01",
        # "recurrence_duration": 3,
        # "recurrence_frequency": "days",
    }
]
