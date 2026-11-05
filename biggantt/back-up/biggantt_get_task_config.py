#!/usr/bin/env python3
"""Get a BigGantt task configuration using the SoftwarePlant API."""

import json
import os
import sys
import urllib.error
import urllib.request


URL = (
    "https://cloud.softwareplant.com/biggantt/jiracloud/23/rest/ppm/task/"
    "config/extPlatform/2/catalog/10067/getTaskConfigOnBox/AGILE-67"
)


def load_env_token():
    token = os.environ.get("BIGGANTT_TOKEN", "").strip()
    if token:
        return token

    # Check ../env file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.abspath(os.path.join(script_dir, "..", "env"))
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if k.strip() in (
                        "BIGGANTT_TOKEN",
                        "JIRA_BIGGANTT_OPS_API_TOKEN",
                        "JIRA_BIGGANTT_API_TOKEN",
                        "JIRA_BIGGANTT_SANDBOX_API_TOKEN",
                    ) and v:
                        token = v
                        break
    return token


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
    token = load_env_token()
    if not token:
        print(
            "BIGGANTT_TOKEN 환경변수 또는 ../env 파일 내 토큰이 설정되지 않았습니다.",
            file=sys.stderr,
        )
        return 2

    # Format BPBearer header
    auth_header = token if token.startswith("BPBearer ") or token.startswith("Bearer ") else f"BPBearer {token}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Authorization": auth_header,
        "BaseUrl": "",
        "BP-Platform-Type": "JiraCloud",
        "BP-Tenant-Id": "0200583",
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "Pragma": "no-cache",
        "ScenarioId": "-1",
    }

    request = urllib.request.Request(
        URL,
        headers=headers,
        method="GET",
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
