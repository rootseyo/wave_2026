#!/usr/bin/env python3
"""Save a BigGantt task configuration using the SoftwarePlant API."""

import json
import os
import sys
import urllib.error
import urllib.request


URL = (
    "https://cloud.softwareplant.com/biggantt/jiracloud/23/rest/ppm/task/"
    "config/extPlatform/2/catalog/10067/saveTaskConfigOnBox/AGILE-67"
)

PAYLOAD = {
    "dynamicMapping": {
        "Actual Cost": {"primaryFieldId": "none", "secondaryFieldId": "none"},
        "Assignee": {"primaryFieldId": "assignee", "secondaryFieldId": "none"},
        "Baseline End Date": {
            "primaryFieldId": "customfield_10072",
            "secondaryFieldId": "none",
        },
        "Baseline Start Date": {
            "primaryFieldId": "customfield_10071",
            "secondaryFieldId": "none",
        },
        "End Date": {
            "primaryFieldId": "customfield_10070",
            "secondaryFieldId": "none",
        },
        "Estimated Cost": {"primaryFieldId": "none", "secondaryFieldId": "none"},
        "Milestone": {"primaryFieldId": "none", "secondaryFieldId": "none"},
        "Progress": {
            "primaryFieldId": "customfield_10074",
            "secondaryFieldId": "none",
        },
        "Scheduling Mode": {
            "primaryFieldId": "customfield_10073",
            "secondaryFieldId": "none",
        },
        "Skills": {"primaryFieldId": "labels", "secondaryFieldId": "none"},
        "Start Date": {
            "primaryFieldId": "customfield_10015",
            "secondaryFieldId": "none",
        },
        "Status": {"primaryFieldId": "status", "secondaryFieldId": "none"},
        "Story Points": {"primaryFieldId": "none", "secondaryFieldId": "none"},
        "Team code": {"primaryFieldId": "labels", "secondaryFieldId": "none"},
        "Time Spent": {"primaryFieldId": "timeSpent", "secondaryFieldId": "none"},
    },
    "extPlatformId": 2,
    "externalCatalogId": "10067",
    "updateNotifications": False,
    "taskComment": False,
    "periodCalculatedForAssignee": True,
    "migrateValues": False,
    "fieldScreenValidation": False,
    "projectName": "CFMT",
    "projectKey": "CFMT",
    "projectIcon": "",
}


def print_response(status: int, body: bytes) -> None:
    text = body.decode("utf-8", errors="replace")
    print(f"HTTP status: {status}")

    if not text:
        print("Response body: (empty)")
        return

    try:
        parsed = json.loads(text)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(text)


def main() -> int:
    token = os.environ.get("BIGGANTT_TOKEN", "").strip()
    if not token:
        print(
            "BIGGANTT_TOKEN 환경변수가 없습니다.\n"
            '예: export BIGGANTT_TOKEN="BPBearer 실제_토큰"',
            file=sys.stderr,
        )
        return 2

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Authorization": token,
        "BaseUrl": "",
        "BP-Platform-Type": "JiraCloud",
        "BP-Tenant-Id": "0200583",
        "BP-User-Key": "557058%3A1a6b8271-38b5-47b4-846b-1936355b5cd0",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Pragma": "no-cache",
        "ScenarioId": "-1",
        "Referer": (
            "https://cloud.softwareplant.com/cdn/frontend/"
            "20260721125846-c616482b28fb42fcb3c6b44c056763968c8b9367-biggantt/"
        ),
    }

    # 인증에 세션 쿠키도 필요하다면 BIGGANTT_COOKIE 환경변수로 전달합니다.
    cookie = os.environ.get("BIGGANTT_COOKIE", "").strip()
    if cookie:
        headers["Cookie"] = cookie

    request = urllib.request.Request(
        URL,
        data=json.dumps(PAYLOAD, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            print_response(response.status, response.read())
            return 0
    except urllib.error.HTTPError as error:
        print_response(error.code, error.read())
        return 1
    except urllib.error.URLError as error:
        print(f"요청 실패: {error.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
