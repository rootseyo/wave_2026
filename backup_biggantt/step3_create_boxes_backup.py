import sys
import asyncio
import json
import time
import os
import re

# Embedded version for copies deployed to offline environments.
# Follow Semantic Versioning and update this value whenever this file changes.
__version__ = "1.0.0"


def get_version_label():
    return f"{os.path.basename(__file__)} v{__version__}"


def versioned_prompt(message):
    return input(f"[{get_version_label()}] {message}")


if __name__ == "__main__" and any(arg in ("-V", "--version") for arg in sys.argv[1:]):
    print(get_version_label())
    sys.exit(0)

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from datetime import datetime
from playwright.async_api import async_playwright

# --- Selector Constants ---
SELECTORS = {
    "leave_page_guard": "#sp-guard__leave-page",
    "box_switcher_main": "#sp-box-switcher-main",
    "add_new_project_btn": "#sp-home-add-new-project",
    "box_type_dropdown_btn": "#sp-inline-add-box-type-dropdown-btn",
    "dropdown_item": "sp-dropdown-item",
    "box_name_input": "#boxName",
    "date_start_input": "#date-range-start-date-input",
    "date_end_input": "#date-range-end-date-input",
    "project_multi_picker": "#sp-select-multi-picker",
    "project_option": "sp-ext-task-query-element-option",
    "save_button": "#sp-save-button",
    "description_input": "#description",
    "save_button_element": "#sp-save-button-element",
    "advanced_accordion_header": "#sp-scope-definition-advanced-configuration-accordion-header",
    "user_picker_wrapper": "#sp-user-picker-select-wrapper",
    "user_picker_search": "#sp-user-picker-search-field",
    "user_picker_option_prefix": "[id^='sp-option-element-index-']",
    "user_picker_first_option": "#sp-option-element-index-0",
    "final_save_btn": "#sp-save-button > button",
}

# --- Shared Helpers ---
async def get_target_and_locator(page, selector, timeout=20000):
    """
    최대 timeout(ms) 동안 메인 페이지와 모든 프레임에서 요소를 반복해서 검색합니다.
    요소가 나타나고 가시적인 상태가 되면 즉시 반환합니다.
    """
    start_time = time.time()
    while (time.time() - start_time) * 1000 < timeout:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0 and await loc.first.is_visible():
                return page, loc
        except Exception:
            pass

        for frame in page.frames:
            try:
                # 3자 트래커나 빈 프레임, 다른 도메인은 스킵하여 Playwright가 블로킹되는 현상 방지
                url = frame.url
                if url and "atlassian" not in url and "softwareplant" not in url:
                    continue
                loc = frame.locator(selector)
                if await loc.count() > 0 and await loc.first.is_visible():
                    return frame, loc
            except Exception:
                continue
        
        await asyncio.sleep(0.5)
            
    return None, None

async def get_jira_cloud_account_id(email):
    """Jira Cloud REST API를 통해 이메일로 accountId를 조회합니다."""
    url = os.getenv("JIRA_CLOUD_URL")
    token = os.getenv("JIRA_CLOUD_API_TOKEN")
    admin_email = os.getenv("JIRA_CLOUD_ADMIN_EMAIL")

    if not all([url, token, admin_email]):
        return None, "Missing Jira Cloud credentials in env."

    search_url = f"{url.rstrip('/')}/rest/api/3/user/search?query={email}"
    try:
        response = requests.get(
            search_url,
            auth=HTTPBasicAuth(admin_email, token),
            headers={"Accept": "application/json"}
        )
        if response.status_code == 200:
            users = response.json()
            if users:
                return users[0].get("accountId"), None
            return None, "User not found in Jira Cloud."
        return None, f"API Error: {response.status_code}"
    except Exception as e:
        return None, f"Exception: {e}"

async def handle_leave_page_guard(page):
    """페이지 이동 시 나타날 수 있는 '변경사항이 저장되지 않을 수 있습니다' 다이얼로그를 처리합니다."""
    try:
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.accept()))
    except Exception:
        pass

def log(message):
    print(message)
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

# --- UI Automation Helpers ---
async def click_element(page, selector, step_name, dry_run=False, timeout=20000):
    target, loc = await get_target_and_locator(page, selector, timeout)
    if not loc:
        log(f"{step_name} Failed: Selector '{selector}' not found.")
        return False
    if not dry_run:
        try:
            await loc.first.click(timeout=3000)
        except Exception as e:
            log(f"{step_name} Warning: Standard click failed ({e}). Retrying with force=True...")
            try:
                await loc.first.click(force=True, timeout=3000)
            except Exception as e2:
                log(f"{step_name} Warning: Force click failed ({e2}). Trying dispatch_event...")
                try:
                    await loc.first.dispatch_event("click")
                except Exception as e3:
                    log(f"{step_name} Failed: All click strategies failed. Error: {e3}")
                    return False
    log(f"{step_name}: Handled click on '{selector}'")
    return True

async def fill_element(page, selector, value, step_name, dry_run=False, timeout=20000):
    target, loc = await get_target_and_locator(page, selector, timeout)
    if not loc:
        log(f"{step_name} Failed: Selector '{selector}' not found.")
        return False
    if not dry_run:
        await loc.first.fill("")
        await loc.first.fill(value)
    log(f"{step_name}: Entered value into '{selector}'")
    return True

async def cleanup_browser_memory(page):
    """CDP를 통해 브라우저 캐시와 JS 메모리를 비웁니다."""
    try:
        client = await page.context.new_cdp_session(page)
        await client.send("Memory.forciblyPurgeJavaScriptMemory")
        await client.send("Network.clearBrowserCache")
        log("Browser memory and cache cleaned up.")
    except Exception as e:
        log(f"Warning: Failed to cleanup browser memory: {e}")

# --- Main Automation Phases ---
async def create_box_step(page, box, dry_run):
    box_name = box.get("boxName")
    box_type = str(box.get("boxType"))
    box_start_date = box.get("boxStartDate")
    box_end_date = box.get("boxEndDate")
    jira_projects = box.get("jira_projects", [])

    # Pre-check: 이전 작업에서 남은 페이지 이동 Guard 팝업 처리
    try:
        target, loc_guard = await get_target_and_locator(page, SELECTORS["leave_page_guard"], timeout=2000)
        if loc_guard:
            await loc_guard.first.click(force=True)
            log("Pre-check: Handled #sp-guard__leave-page popup.")
            await asyncio.sleep(1.0)
    except Exception:
        pass

    # Step 1: id="sp-box-switcher-main" 클릭 (옵션)
    try:
        target, loc = await get_target_and_locator(page, SELECTORS["box_switcher_main"], timeout=3000)
        if loc:
            if not dry_run:
                await loc.first.click(force=True)
            log("Step 1: Handled #sp-box-switcher-main")
    except Exception:
        pass

    # Step 2 & 3: "Create box"를 클릭하고 드롭다운 버튼이 나타날 때까지 대기 (실패 시 retry)
    dropdown_visible = False
    
    target_dd, loc_dd = await get_target_and_locator(page, SELECTORS["box_type_dropdown_btn"], timeout=2000)
    if loc_dd:
        dropdown_visible = True
        log("Step 2/3: Dropdown button is already visible. Skipping Create box click.")

    if not dropdown_visible and not dry_run:
        for attempt in range(1, 4):
            log(f"Step 2: Clicking Create box button (#sp-home-add-new-project) - Attempt {attempt}")
            target_btn, loc_btn = await get_target_and_locator(page, SELECTORS["add_new_project_btn"], timeout=5000)
            if loc_btn:
                try:
                    if attempt == 1:
                        await loc_btn.first.click(timeout=3000)
                    else:
                        await loc_btn.first.click(force=True, timeout=3000)
                except Exception as click_err:
                    log(f"  -> Click attempt {attempt} failed: {click_err}. Trying dispatch click.")
                    try:
                        await loc_btn.first.dispatch_event("click")
                    except Exception:
                        pass
                
                target_dd, loc_dd = await get_target_and_locator(page, SELECTORS["box_type_dropdown_btn"], timeout=4000)
                if loc_dd:
                    dropdown_visible = True
                    log(f"  -> Successfully opened dropdown on attempt {attempt}.")
                    break
            else:
                log("  -> Create box button not found.")
            await asyncio.sleep(1.5)

    if not dry_run:
        if not dropdown_visible:
            log("Step 3 Failed: Dropdown button not visible after attempts.")
            return False
        
        if not await click_element(page, SELECTORS["box_type_dropdown_btn"], "Step 3", dry_run):
            return False
    else:
        log("Step 2 & 3: [DRY RUN] Would handle Create box click and dropdown selection.")
    await asyncio.sleep(0.5)

    # Step 4: boxType 선택
    target, loc = await get_target_and_locator(page, SELECTORS["dropdown_item"])
    if not loc:
        log("Step 4 Failed: sp-dropdown-item not found.")
        return False
    
    idx = 0
    if "Agile" in box_type or box_type == "8":
        idx = 0
    elif "Program" in box_type or box_type == "2":
        idx = 1
    
    if not dry_run:
        await loc.nth(idx).click(force=True)
    log(f"Step 4: Selected box type index {idx}")

    # Step 5: id="boxName" 입력
    if not await fill_element(page, SELECTORS["box_name_input"], box_name, "Step 5", dry_run):
        return False

    # Step 5.1 & 5.2: 날짜 입력
    if box_start_date:
        await fill_element(page, SELECTORS["date_start_input"], box_start_date, "Step 5.1", dry_run)
    if box_end_date:
        await fill_element(page, SELECTORS["date_end_input"], box_end_date, "Step 5.2", dry_run)

    # Step 6: id=sp-select-multi-picker 에 jira_project 입력
    target_picker, loc_picker = await get_target_and_locator(page, SELECTORS["project_multi_picker"])
    if loc_picker:
        for proj in jira_projects:
            if not dry_run:
                await loc_picker.first.type(proj)
                start_wait = time.time()
                while (time.time() - start_wait) < 10:
                    if await target_picker.locator(SELECTORS["project_option"]).count() > 0:
                        break
                    await asyncio.sleep(0.5)
                await asyncio.sleep(0.5)
                await loc_picker.first.press("Enter")
            log(f"Step 6: Handled project: {proj}")

    # Step 7: id=sp-save-button click
    if not dry_run:
        await asyncio.sleep(2.0)
        if not await click_element(page, SELECTORS["save_button"], "Step 7", dry_run):
            return False
    else:
        log("Step 7: [DRY RUN] Would click save button")

    return True

async def update_box_description(page, box):
    box_name = box.get("boxName")
    owner_email = box.get("owner_email")
    owner_name = box.get("owner_name")

    # Step 8 & 9: URL 변경을 통한 직접 이동 대기
    start_wait_url = time.time()
    url_updated = False
    current_url = ""
    while (time.time() - start_wait_url) < 15:
        current_url = page.url
        if "showCreateBoxMessage=true" in current_url:
            url_updated = True
            break
        await asyncio.sleep(1.0)
    
    if not url_updated:
        log(f"Step 8/9 Failed: URL did not update for box '{box_name}'")
        return False

    match = re.search(r'#box/([^/?]+)', current_url)
    if match:
        new_id = match.group(1)
        box["newBoxId"] = new_id
        log(f"  -> Detected New Box ID: {new_id}")

    new_url = current_url.replace("g?showCreateBoxMessage=true", "settings/general/basics")
    await page.goto(new_url, wait_until="domcontentloaded")
    await asyncio.sleep(2.0)

    # Step 10: id=description 입력
    dc_box_id = box.get("boxId")
    description_parts = []
    if owner_email or owner_name:
        description_parts.append(f"Migrated: Original Owner was {owner_name} ({owner_email})")
    if dc_box_id:
        description_parts.append(f"Original DC Box ID: {dc_box_id}")
        
    if description_parts:
        description_text = "\n".join(description_parts)
        await fill_element(page, SELECTORS["description_input"], description_text, "Step 10")

    # Step 11: id="sp-save-button-element" 클릭
    await asyncio.sleep(0.5)
    await click_element(page, SELECTORS["save_button_element"], "Step 11")
    await asyncio.sleep(2.0)

    await handle_leave_page_guard(page)
    return True

async def configure_box_owner(page, box):
    target_owner = os.getenv("FALLBACK_OWNER_NAME", "홍권혁 Dennis")
    
    current_url = page.url
    if "settings/general/basics" not in current_url:
        return

    scope_url = current_url.replace("settings/general/basics", "settings/tasks/scope-definition")
    await page.goto(scope_url, wait_until="domcontentloaded")

    # Step 13: Advanced Configuration Accordion 클릭 (최대 30초 대기)
    await click_element(page, SELECTORS["advanced_accordion_header"], "Step 13", timeout=30000)
    await asyncio.sleep(1.0)

    # Step 14: Box Owner 설정 (기본값인 FALLBACK_OWNER_NAME으로 바로 설정)
    owner_step_success = True
    failure_msg = ""
    
    target, loc = await get_target_and_locator(page, SELECTORS["user_picker_wrapper"])
    if loc:
        log("Step 14: Clicking User Picker wrapper...")
        await loc.first.click(force=True)
        await asyncio.sleep(1.0)
        
        target, loc_search = await get_target_and_locator(page, SELECTORS["user_picker_search"])
        if loc_search:
            log(f"Step 14: Searching for owner: '{target_owner}'")
            await loc_search.first.fill("")
            await loc_search.first.fill(target_owner)
            await asyncio.sleep(2.5)
            
            # 드롭다운에서 매칭되는 옵션 수 확인
            options_loc = target.locator(SELECTORS["user_picker_option_prefix"])
            options_count = await options_loc.count()
            
            if options_count > 0:
                log(f"Step 14: Found {options_count} matches for '{target_owner}'. Clicking first option.")
                try:
                    await target.locator(SELECTORS["user_picker_first_option"]).first.click(force=True, timeout=3000)
                except Exception as e:
                    log(f"Step 14 Warning: Clicking first option failed ({e}). Trying to press Enter...")
                    await loc_search.first.press("Enter")
            else:
                log(f"Step 14 Warning: No option element matches '{target_owner}' in dropdown. Trying to press Enter...")
                await loc_search.first.press("Enter")
            
            await asyncio.sleep(3.0)
            
            # 선택 후 에러 메시지 검사
            err_target, err_loc = await get_target_and_locator(page, "#scope-definition-error-message", timeout=1000)
            if err_loc:
                error_text = await err_loc.first.inner_text()
                owner_step_success = False
                failure_msg = f"Error detected after selecting {target_owner}: {error_text.strip()}"
                log(f"Step 14 Error: {failure_msg}")
        else:
            owner_step_success = False
            failure_msg = "User picker search field not found."
            log(f"Step 14 Failed: {failure_msg}")
    else:
        owner_step_success = False
        failure_msg = "User picker wrapper not found."
        log(f"Step 14 Failed: {failure_msg}")

    # Step 17: 최종 저장 버튼 확인 및 클릭
    await asyncio.sleep(3.0)
    
    loc_save = None
    target_container, loc_container = await get_target_and_locator(page, "#sp-save-button")
    if loc_container:
        buttons = loc_container.locator("button")
        btn_count = await buttons.count()
        for i in range(btn_count):
            btn = buttons.nth(i)
            text = await btn.inner_text()
            if text.strip().lower() == "save":
                loc_save = btn
                break
                
    if not loc_save:
        # container 내부에서 찾지 못했다면 전체 페이지 모든 버튼에서 찾기
        buttons = page.locator("button")
        btn_count = await buttons.count()
        for i in range(btn_count):
            btn = buttons.nth(i)
            text = await btn.inner_text()
            if text.strip().lower() == "save":
                loc_save = btn
                break

    if loc_save:
        is_disabled = await loc_save.is_disabled()
        if is_disabled:
            log("Step 17: Save button is [DISABLED]. Waiting for it to become enabled...")
            for attempt in range(1, 21):
                await asyncio.sleep(0.5)
                is_disabled = await loc_save.is_disabled()
                if not is_disabled:
                    log(f"  -> Save button became [ENABLED] on attempt {attempt}.")
                    break
        
        is_disabled = await loc_save.is_disabled()
        if not is_disabled:
            await asyncio.sleep(0.5)
            await loc_save.click(force=True)
            log("Step 17: Save button clicked. Waiting for save to complete (button becomes DISABLED)...")
            
            # 최대 15초 동안 0.5초 간격으로 버튼이 다시 DISABLED 상태가 될 때까지 대기
            save_completed = False
            for attempt in range(1, 31):
                await asyncio.sleep(0.5)
                is_disabled = await loc_save.is_disabled()
                if is_disabled:
                    log(f"  -> Save completed successfully (button became DISABLED) on attempt {attempt}.")
                    save_completed = True
                    break
            
            if not save_completed:
                log("  -> Warning: Save button did not become DISABLED within 15 seconds after click.")
            
            await asyncio.sleep(1.0)
        else:
            log("Step 17: Save button remained [DISABLED] after waiting 10 seconds. Skipping click.")
    else:
        log("Step 17 Failed: Save button with text 'Save' not found.")

    # 실패 시 Description 기록
    if not owner_step_success:
        log(f"  [Failure Handling] {failure_msg}")
        current_url_step14 = page.url
        if "settings/tasks/scope-definition" in current_url_step14:
            basics_url = current_url_step14.replace("settings/tasks/scope-definition", "settings/general/basics")
            log(f"  -> Navigating to Basics to log failure: {basics_url}")
            await page.goto(basics_url, wait_until="domcontentloaded")
            await asyncio.sleep(5.0)
            
            target_b, loc_desc = await get_target_and_locator(page, SELECTORS["description_input"])
            if loc_desc:
                current_val = await loc_desc.first.evaluate("el => el.value")
                append_text = f"\n\n[Migration Warning] {failure_msg} (Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
                await loc_desc.first.fill((current_val or "") + append_text)
                log("  -> Appended failure message to description")
                
                # 저장
                await click_element(page, SELECTORS["save_button_element"], "  -> Save Description")
                await asyncio.sleep(2.0)

    # [Optional] 페이지 이동 Guard 팝업 처리
    try:
        target, loc_guard = await get_target_and_locator(page, SELECTORS["leave_page_guard"], timeout=2000)
        if loc_guard:
            await loc_guard.first.click(force=True)
            log("Final Guard: Handled #sp-guard__leave-page popup.")
    except Exception:
        pass

# --- Runner ---
async def run_automation():
    possible_paths = ['env', '../env']
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            break

    log("\n" + "="*80)
    log(f" [Step 3: Create Boxes | {get_version_label()}]")
    log("="*80)

    def select_dry_run():
        if len(sys.argv) > 1:
            val = sys.argv[1].lower().strip()
            return val in ('y', 'yes', 'true')
        choice = versioned_prompt("Dry-run mode? (y/n): ").lower().strip()
        return choice == 'y'

    ENV = "CLOUD"
    DRY_RUN = select_dry_run()
    
    if DRY_RUN:
        log("\n*** DRY RUN MODE ENABLED: No clicks or typing will be performed. ***\n")

    try:
        with open("migration_box.json", "r", encoding="utf-8") as f:
            boxes = json.load(f)
    except Exception as e:
        log(f"Error loading migration_box.json: {e}")
        return

    pw = None
    browser = None
    try:
        log(f"Connecting to Chrome via CDP...")
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp('http://127.0.0.1:9222')
        
        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(
            None,
            lambda: versioned_prompt("Start box creation? (y/n): ")
        )
        if user_input.lower().strip() not in ('yes', 'y'):
            log("Aborted by user.")
            await browser.close()
            await pw.stop()
            return
        
        context = browser.contexts[0]
        if not context.pages:
            raise Exception("No open pages found.")
        page = context.pages[0]
        await page.bring_to_front()

        # 처음 시작할 때의 Home URL 기억 (설정 페이지 등에서 시작해도 홈 주소를 찾도록 가공)
        current_url = page.url
        if "#" in current_url:
            home_url = current_url.split("#")[0] + "#home"
        else:
            home_url = current_url + "#home"
        log(f"Detected Home URL: {home_url}")

        for index, box in enumerate(boxes):
            box_name = box.get("boxName")
            box_type = str(box.get("boxType"))
            log(f"\n[{index+1}/{len(boxes)}] Processing Box: {box_name} ({box_type}) {'[DRY RUN]' if DRY_RUN else ''}")

            # 매 루프 시작 시 Home URL로 복귀하여 홈 화면에서 시작하도록 보장
            if page.url == home_url:
                log("Already on Home URL. Reloading page to ensure clean state...")
                await page.reload(wait_until="domcontentloaded")
            else:
                log(f"Navigating back to Home URL: {home_url}")
                await page.goto(home_url, wait_until="domcontentloaded")
                await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(5.0)

            # 1. 박스 신규 생성
            if not await create_box_step(page, box, DRY_RUN):
                continue

            if DRY_RUN:
                continue

            # 2. Basics 설정 (설명 입력)
            if not await update_box_description(page, box):
                continue

            # 3. Scope 및 Owner 설정
            await configure_box_owner(page, box)

            # 매 박스 완료 후 브라우저 메모리 비우기
            await cleanup_browser_memory(page)

            await asyncio.sleep(1)

        log("\nAll boxes processed.")
        
        if not DRY_RUN:
            try:
                with open("migration_box.json", "w", encoding="utf-8") as f:
                    json.dump(boxes, f, indent=2, ensure_ascii=False)
                log("Successfully updated migration_box.json with newBoxId.")
            except Exception as e:
                log(f"Error updating migration_box.json: {e}")

        await browser.close()
        await pw.stop()

    except Exception as err:
        log(f"\nAutomation Error: {err}")
        if browser: await browser.close()
        if pw: await pw.stop()
        sys.exit(1)

if __name__ == "__main__":
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(
            f"--- New Migration Session Started: {datetime.now()} "
            f"({get_version_label()}) ---\n"
        )
    asyncio.run(run_automation())
