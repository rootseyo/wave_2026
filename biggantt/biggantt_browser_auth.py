"""Interactive Chrome login followed by reusable Playwright authentication.

Google/SSO login is performed manually in ordinary Chrome, without a driver.
No passwords, API tokens, anti-bot bypass flags, or copied personal profiles.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = ROOT / ".playwright" / "manual-ops-profile"


def validate_urls(site_url, app_url):
    site, app = urlsplit(site_url), urlsplit(app_url)
    if (site.scheme != "https" or app.scheme != "https" or not site.hostname
            or site.hostname != app.hostname or site.username or app.username
            or site.password or app.password or site.port not in (None, 443)
            or app.port not in (None, 443)):
        raise ValueError("Jira site and app must use the same HTTPS host")
    return "https://" + site.hostname


def secure_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def launch_context(playwright, profile, headed=True):
    return playwright.chromium.launch_persistent_context(
        str(secure_directory(profile)), channel="chrome", headless=not headed,
        viewport={"width": 1440, "height": 1050},
        # Use the same OS credential store as the ordinary Chrome login.
        ignore_default_args=["--use-mock-keychain", "--password-store=basic"],
    )


def wait_authenticated(context, page, site_url, timeout=900, require_app=False):
    """Poll actual Jira session state; a login-page redirect is never success."""
    deadline = time.monotonic() + timeout
    next_notice = 0
    while time.monotonic() < deadline:
        if page.is_closed():
            raise RuntimeError("Login browser was closed before authentication completed")
        # Examine the current browser session only. No REST API token fallback.
        if urlsplit(page.url).hostname == urlsplit(site_url).hostname:
            response = None
            try:
                response = context.request.get(site_url + "/rest/api/3/myself", timeout=5000,
                                               max_redirects=0)
                authenticated = response.status == 200 and bool(response.json().get("accountId"))
                app_loaded = any(urlsplit(f.url).hostname == "cloud.softwareplant.com"
                                 for f in page.frames)
                if authenticated and (not require_app or app_loaded):
                    return
            except Exception:
                pass
            finally:
                if response is not None:
                    response.dispose()
        if time.monotonic() >= next_notice:
            print("WAITING_FOR_LOGIN: 브라우저에서 로그인을 완료해 주세요. Mapping 작업은 대기 중입니다.", flush=True)
            next_notice = time.monotonic() + 30
        # Give the browser event loop time to process redirects and SSO callbacks.
        page.wait_for_timeout(1000)
    raise TimeoutError("LOGIN_TIMEOUT: no authenticated Jira browser session")


def save_storage_state(context, profile):
    path = Path(profile).parent / (Path(profile).name + "-state.json")
    temporary = path.with_suffix(".tmp")
    context.storage_state(path=str(temporary), indexed_db=True)
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def prompt_login_confirmation():
    """An empty line or unrelated input must never advance the migration."""
    while True:
        try:
            answer = input("로그인 완료 후 ok 입력 (취소: cancel): ").strip().casefold()
        except EOFError:
            return False
        if answer == "ok":
            return True
        if answer == "cancel":
            return False
        print("계속 대기합니다. 로그인 완료 후 ok 또는 취소하려면 cancel을 입력하세요.", flush=True)


def manual_login(site_url, app_url, profile, chrome_path, timeout=900, verify_after=True):
    """Wait for a human login, then gracefully release ONLY our Chrome process."""
    site_url = validate_urls(site_url, app_url)
    profile = secure_directory(profile)
    if (profile / "SingletonLock").exists() or (profile / "SingletonLock").is_symlink():
        raise RuntimeError("PROFILE_IN_USE: close the dedicated Chrome profile and retry")
    chrome = Path(chrome_path)
    if not chrome.is_file():
        raise RuntimeError("Chrome executable not found; use --chrome-path")
    process = subprocess.Popen([
        str(chrome), "--user-data-dir=" + str(profile), "--no-first-run", app_url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("MANUAL_LOGIN: 일반 Chrome이 열렸습니다. Google/Atlassian 로그인을 직접 완료하세요.", flush=True)
    print("로그인 후 BigGantt 화면이 나타나면 이 터미널에서 ok를 입력하세요.", flush=True)
    print("자동화 명령이나 Field Mapping 저장은 아직 실행되지 않습니다.", flush=True)
    confirmation = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: confirmation.put(prompt_login_confirmation()), daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LOGIN_TIMEOUT: manual login was not confirmed")
            try:
                confirmed = confirmation.get(timeout=min(1, remaining))
            except queue.Empty:
                confirmed = None
            if confirmed is False:
                raise RuntimeError("LOGIN_CANCELLED: no authenticated state was saved")
            if confirmed is True:
                break
            if process.poll() is not None:
                raise RuntimeError("LOGIN_BROWSER_CLOSED: login was not confirmed with ok")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Dedicated Chrome did not close; close it and retry") from exc
    if not verify_after:
        return
    with sync_playwright() as playwright:
        context = launch_context(playwright, profile)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(app_url, wait_until="domcontentloaded", timeout=60000)
            # Do not send a blocked Google login through automation again.
            wait_authenticated(context, page, site_url, timeout=60)
            state = save_storage_state(context, profile)
            print(json.dumps({"status": "AUTHENTICATED", "site": site_url,
                              "profile": str(profile), "storage_state": str(state)}), flush=True)
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--chrome-path", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--login-timeout", type=int, default=900)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        manual_login(args.site_url, args.app_url, args.profile, args.chrome_path, args.login_timeout)
        return 0
    except (ValueError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
