import sys
import asyncio
import json
import os
import time
import re
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright

def log(message):
    print(message)
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

async def get_big_gantt_frame(page):
    """
    sp-app 태그가 포함된 BigGantt 애플리케이션 프레임을 찾아 반환합니다.
    """
    for frame in page.frames:
        try:
            has_sp = await frame.evaluate("() => document.querySelector('sp-app') !== null")
            if has_sp:
                return frame
        except Exception:
            continue
    return None

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

async def expand_tree_on_frame(frame):
    """
    지정한 프레임의 보안 트리를 최대한 펼칩니다.
    """
    log("  Expanding security tree...")
    for iter_idx in range(5):
        expanded_in_this_iter = 0
        try:
            # 닫힌 토글러 찾기
            collapsed_locator = frame.locator("#sp-tree-toggler:not(.sp-toggler-rotate-down)")
            count = await collapsed_locator.count()
            for i in range(count):
                try:
                    toggler = collapsed_locator.nth(i)
                    if await toggler.is_visible():
                        await toggler.click(force=True, timeout=1000)
                        expanded_in_this_iter += 1
                        await asyncio.sleep(0.3)
                except: continue
        except: pass
        
        if expanded_in_this_iter == 0:
            break
        await asyncio.sleep(1.5)

async def process_security_items(frame, items, category, role):
    """
    사용자 또는 그룹을 검색하고 추가합니다.
    매칭 실패 시 해당 항목의 이름을 리스트로 반환합니다.
    """
    failed_items = []
    for item in items:
        # Extract display name and email
        display_name = item
        email = None
        if "(" in item and ")" in item:
            match_email = re.search(r'\(([^)]+)\)', item)
            match_name = re.search(r'^([^(]+)', item)
            if match_email:
                email = match_email.group(1).strip()
            if match_name:
                display_name = match_name.group(1).strip()
        
        # Use display name as the search query for users, otherwise the whole item (groups)
        search_query = display_name if category == "Users" else item
        
        log(f"    - Adding {category[:-1]}: {search_query}")
        
        try:
            # 1. 트리거(Add/Edit 버튼 영역) 클릭
            trigger_clicked = False
            
            if role == "Box Admin" and category == "Users":
                # 사용자가 제공한 전용 셀렉터 시도
                trigger_selector = "#sp-treegrid-row-cell-0 > sp-role-assigment-user > ngp-multi-select > ngp-multi-select-trigger > div.ngp-multi-select__trigger-options-container"
                trigger = frame.locator(trigger_selector)
                try:
                    if await trigger.count() > 0:
                        await trigger.first.click(force=True, timeout=2000)
                        log(f"      [Debug] Clicked Box Admin Users via specific selector.")
                        trigger_clicked = True
                    else:
                        log(f"      [Debug] Specific selector not found for Box Admin Users. Trying fallback.")
                except Exception as e:
                    log(f"      [Debug] Error clicking specific selector: {e}")

            if not trigger_clicked:
                # 기존 Y좌표 기반 로직 (Fallback)
                trigger_handle = await frame.evaluate_handle("""
                    ([role, category]) => {
                        const nodes = Array.from(document.querySelectorAll('sp-tree-node'));
                        let positionedNodes = nodes.map(n => {
                            const rect = n.getBoundingClientRect();
                            const firstLine = (n.innerText || "").split('\\n')[0].trim();
                            const trigger = category === 'Users' 
                                ? n.querySelector('sp-role-assigment-user ngp-multi-select-trigger, sp-role-assigment-user .ngp-multi-select')
                                : n.querySelector('sp-role-assigment-group-picker ngp-multi-select-trigger, sp-role-assigment-group-picker .ngp-multi-select');
                            return { text: firstLine, top: Math.round(rect.top), height: rect.height, trigger: trigger };
                        }).filter(n => n.height > 0);
                        
                        positionedNodes.sort((a, b) => a.top - b.top);
                        
                        let currentRole = null;
                        let currentCategory = null;
                        const roles = ['Box Admin', 'Box Editor', 'Box Viewer', 'Sub-Box Creator'];
                        
                        for (let item of positionedNodes) {
                            if (roles.some(r => item.text.startsWith(r))) {
                                currentRole = roles.find(r => item.text.startsWith(r));
                            } else if (item.text === 'Users') {
                                currentCategory = 'Users';
                            } else if (item.text === 'Groups') {
                                currentCategory = 'Groups';
                            } else if (item.trigger) {
                                if (currentRole === role && currentCategory === category) {
                                    return item.trigger;
                                }
                            }
                        }
                        return null;
                    }
                """, [role, category])
                
                if trigger_handle and str(trigger_handle) != "JSHandle@null":
                    await trigger_handle.click(force=True)
                    await trigger_handle.dispose()
                    trigger_clicked = True
                else:
                    log(f"      [Error] Could not find trigger for {role} > {category}")
                    failed_items.append(item)
                    continue
            
            await asyncio.sleep(1.5) # 드롭다운 렌더링 대기
            
            # 2. 검색창 찾아 검색어 입력
            search_input = None
            if role == "Box Admin" and category == "Users":
                # 사용자가 제공한 전용 검색창 셀렉터 시도
                search_input_selector = "body > sp-component-outlet > div > ngp-multi-select-dropdown > div.sp-px-space-large > ngp-rich-text > sp-role-assigment-user-select > div > ngp-multi-select-search > div > input"
                si = frame.locator(search_input_selector)
                if await si.count() > 0:
                    search_input = si.first
                    log(f"      [Debug] Found specific search input for Box Admin Users.")

            if not search_input:
                # 일반적인 검색창 셀렉터들
                for selector in ["input.ngp-multi-select__dropdown-search-input", "input[placeholder='Search']", "input.ngp-multi-select-search__input"]:
                    si = frame.locator(selector)
                    if await si.count() > 0 and await si.first.is_visible():
                        search_input = si.first
                        break

            if search_input:
                try:
                    await search_input.wait_for(state="visible", timeout=3000)
                    await search_input.click()
                    await search_input.fill("")
                    await search_input.press_sequentially(search_query, delay=70)
                    log(f"      [Debug] Entered search query: {search_query}")
                except Exception as e:
                    log(f"      [Error] Failed to input search query: {e}")
                    failed_items.append(item)
                    continue
            else:
                log(f"      [Error] Search input not found.")
                failed_items.append(item)
                continue
                
            # 3. 결과 대기 및 첫 번째 결과 클릭
            await asyncio.sleep(3.5)
            
            options = frame.locator("ngp-multi-select-option, .ngp-multi-select__dropdown-option, .ngp-multi-select-option")
            count = await options.count()
            
            if count > 0:
                try:
                    selected_opt = None
                    if category == "Users" and email:
                        # Get Jira Cloud account ID (user key)
                        user_key, err = await get_jira_cloud_account_id(email)
                        if user_key:
                            log(f"      [Debug] Found Jira Cloud account ID (user key) for {email}: {user_key}")
                            # Look for option whose ID contains user_key
                            for i in range(count):
                                opt = options.nth(i)
                                opt_id = await opt.get_attribute("id")
                                if opt_id and user_key in opt_id:
                                    selected_opt = opt
                                    log(f"      [Debug] Matched option by user key: {opt_id}")
                                    break
                            if not selected_opt:
                                log(f"      [Warning] No option ID matched user key {user_key} among {count} options.")
                        else:
                            log(f"      [Warning] Could not get user key for {email}: {err}")
                    
                    if not selected_opt:
                        selected_opt = options.first
                        log(f"      [Info] Selecting first option as fallback.")

                    await selected_opt.scroll_into_view_if_needed()
                    opt_text = await selected_opt.inner_text()
                    
                    checkbox = selected_opt.locator("input[type='checkbox']")
                    if await checkbox.count() > 0:
                        if not await checkbox.is_checked():
                            await selected_opt.click(force=True)
                            log(f"      [Success] Checked option: {opt_text.strip().split('\\n')[0]}")
                        else:
                            log(f"      [Info] Option already checked: {opt_text.strip().split('\\n')[0]}")
                    else:
                        await selected_opt.click(force=True)
                        log(f"      [Success] Clicked option: {opt_text.strip().split('\\n')[0]}")
                except Exception as e:
                    log(f"      [Error] Failed to click option: {e}")
                    failed_items.append(item)
            else:
                log(f"      [No Match] No results found for: {search_query}")
                failed_items.append(item)
            
            # 드롭다운 닫기
            try:
                await frame.keyboard.press("Escape")
            except: pass
            await asyncio.sleep(1.0)
            
        except Exception as e:
            log(f"      [Exception] Failed to process {search_query}: {e}")
            failed_items.append(item)
        
        await asyncio.sleep(1.0)
    
    return failed_items

async def update_box_description(page, box_id, failed_items):
    """
    지정된 박스의 General > Basics 페이지로 이동하여 실패한 항목들을 Description에 추가합니다.
    """
    if not failed_items:
        return

    # URL 생성 (제공해주신 템플릿 기반)
    current_url = page.url
    base_url = current_url.split('#')[0]
    basics_url = f"{base_url}#box/{box_id}/settings/general/basics"
    
    log(f"  Navigating to General Settings: {basics_url}")
    await page.goto(basics_url)
    await asyncio.sleep(8.0)
    
    frame = await get_big_gantt_frame(page)
    if not frame:
        log("    [Error] Could not find BigGantt frame for description update.")
        return

    try:
        # Description 필드 찾기
        desc_selector = "#description"
        await frame.wait_for_selector(desc_selector, timeout=5000)
        
        # 기존 내용 읽기
        current_desc = await frame.evaluate(f"() => document.querySelector('{desc_selector}').value")
        
        # 추가할 내용 생성
        failed_text = "\n\n[Failed Security Assignments - " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]\n"
        for item in failed_items:
            failed_text += f"- {item}\n"
        
        new_desc = (current_desc or "") + failed_text
        
        # 내용 입력
        await frame.fill(desc_selector, new_desc)
        await asyncio.sleep(1.0)
        
        # 저장 버튼 클릭 (BigGantt의 경우 보통 하단에 Save 버튼이 있음)
        # 텍스트가 'Save'인 버튼 찾기 시도
        save_btn = frame.locator("button:has-text('Save')")
        if await save_btn.count() > 0:
            await save_btn.first.click()
            log("    [Success] Updated Box Description with failed assignments.")
            await asyncio.sleep(2.0)
        else:
            log("    [Warning] Save button not found. Description might not be persisted.")
            
    except Exception as e:
        log(f"    [Error] Failed to update description: {e}")

async def run_automation():
    possible_paths = ['env', '../env']
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            break

    log("\n" + "="*80)
    log(" [Step 5: Apply Box Security Settings]")
    log("="*80)
    
    # 데이터 로드
    try:
        with open("migration_box.json", "r", encoding="utf-8") as f:
            migration_boxes = json.load(f)
        with open("step4_all_boxes_security.json", "r", encoding="utf-8") as f:
            security_data = json.load(f)
    except Exception as e:
        log(f"Error loading JSON files: {e}")
        return

    pw = None
    browser = None
    try:
        log(f"Connecting to Chrome via CDP...")
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp('http://127.0.0.1:9222')
        
        loop = asyncio.get_event_loop()
        user_input = await loop.run_in_executor(None, lambda: input("run? "))
        if user_input.lower().strip() not in ('yes', 'y'):
            log("Aborted by user.")
            await browser.close()
            await pw.stop()
            return
        
        context = browser.contexts[0]
        # 실제 Jira/BigGantt 탭 찾기
        page = None
        for p in context.pages:
            try:
                url = p.url
                # DevTools나 시스템 페이지 제외, Atlassian 도메인 포함 확인
                if "atlassian.net" in url and "devtools://" not in url:
                    page = p
                    break
            except Exception:
                continue
        
        if not page:
            # 적절한 탭을 못 찾으면 첫 번째 비시스템 탭 사용
            for p in context.pages:
                if "devtools://" not in p.url:
                    page = p
                    break
        
        if not page:
            raise Exception("No active Jira/BigGantt tab found.")
        
        log(f"  Connected to tab: {page.url}")
        await page.bring_to_front()

        for index, box in enumerate(migration_boxes):
            box_id = box.get("boxId")
            new_box_id = box.get("newBoxId")
            box_name = box.get("boxName")
            
            if not box_id or not new_box_id:
                log(f"[{index+1}/{len(migration_boxes)}] Skipping '{box_name}': Missing boxId or newBoxId.")
                continue

            box_security = security_data.get(box_id)
            if not box_security:
                log(f"[{index+1}/{len(migration_boxes)}] Skipping '{box_name}': No security data found for {box_id}.")
                continue

            log(f"\n[{index+1}/{len(migration_boxes)}] Processing Box: {box_name} ({new_box_id})")
            
            # 실패 목록 수집용
            all_failed_items = []

            # 보안 설정 URL로 이동
            current_url = page.url
            base_url = current_url.split('#')[0]
            new_url = f"{base_url}#box/{new_box_id}/settings/security/security"

            log(f"  Navigating to: {new_url}")
            await page.goto(new_url)
            await asyncio.sleep(8.0) # 페이지 및 프레임 로드 대기
            
            # BigGantt 프레임 찾기
            frame = await get_big_gantt_frame(page)
            if not frame:
                log("  [Error] Could not find BigGantt application frame.")
                continue
            
            # 트리 확장
            await expand_tree_on_frame(frame)

            # 역할별 설정 적용
            roles = ['Box Admin', 'Box Editor', 'Box Viewer', 'Sub-Box Creator']
            for role in roles:
                role_data = box_security.get("roles", {}).get(role, {})
                users = role_data.get("Users", [])
                groups = role_data.get("Groups", [])

                if users:
                    failed = await process_security_items(frame, users, "Users", role)
                    all_failed_items.extend([f"[{role} Users] {f}" for f in failed])

                if groups:
                    failed = await process_security_items(frame, groups, "Groups", role)
                    all_failed_items.extend([f"[{role} Groups] {f}" for f in failed])
            
            log(f"  [Finish] Security processing completed for {box_name}")

            # 실패 목록이 있으면 Description 업데이트
            if all_failed_items:
                log(f"  Found {len(all_failed_items)} failed items. Updating Box Description...")
                await update_box_description(page, new_box_id, all_failed_items)
            else:
                log(f"  No failures for {box_name}.")

        log("\nAll tasks completed.")
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
        f.write(f"--- New Migration Session Started: {datetime.now()} ---\n")
    asyncio.run(run_automation())
