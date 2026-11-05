import sys
import asyncio
import json
import os
import time
import re
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright

def get_all_frames_and_page(page):
    """메인 페이지와 모든 프레임을 포함한 리스트를 반환합니다."""
    targets = [page]
    targets.extend(page.frames)
    return targets

async def expand_tree_on_page(page):
    """지정한 페이지의 트리를 최대한 펼칩니다."""
    expanded_total = 0
    for iter_idx in range(5):
        expanded_in_this_iter = 0
        all_frames = get_all_frames_and_page(page)
        for target_frame in all_frames:
            try:
                # 닫힌 토글러 찾기
                collapsed_locator = target_frame.locator("#sp-tree-toggler:not(.sp-toggler-rotate-down)")
                count = await collapsed_locator.count()
                for i in range(count):
                    try:
                        toggler = collapsed_locator.nth(i)
                        if await toggler.is_visible():
                            # Native Click 시도
                            try:
                                await toggler.click(force=True, timeout=1000)
                                expanded_in_this_iter += 1
                                await asyncio.sleep(0.2)
                            except:
                                # Fallback: JS Click
                                await toggler.evaluate("el => el.click()")
                                expanded_in_this_iter += 1
                                await asyncio.sleep(0.2)
                    except: continue
            except: continue
        
        expanded_total += expanded_in_this_iter
        if expanded_in_this_iter == 0:
            break
        await asyncio.sleep(2.0)
    return expanded_total

async def extract_security_from_page(page):
    """현재 페이지(프레임 포함)에서 보안 설정 데이터를 추출합니다."""
    extract_script = """
    () => {
        const results = {};
        const roles = ['Box Admin', 'Box Editor', 'Box Viewer', 'Sub-Box Creator'];
        roles.forEach(r => results[r] = { "Users": [], "Groups": [] });
        
        let nodes = Array.from(document.querySelectorAll('sp-tree-node'));
        let positionedNodes = nodes.map(n => {
            const rect = n.getBoundingClientRect();
            return {
                node: n,
                text: n.innerText.trim(),
                top: rect.top,
                height: rect.height
            };
        }).filter(n => n.height > 0);
        
        positionedNodes.sort((a, b) => a.top - b.top);
        
        let currentRole = null;
        let currentCategory = null;
        
        for (let i = 0; i < positionedNodes.length; i++) {
            const item = positionedNodes[i];
            const nodeText = item.text;
            const firstLine = nodeText.split('\\n')[0].trim();
            
            if (roles.some(r => firstLine.startsWith(r))) {
                currentRole = roles.find(r => firstLine.startsWith(r));
                currentCategory = null;
                continue;
            }
            
            if (!currentRole) continue;

            if (firstLine === 'Users') {
                currentCategory = 'Users';
                continue;
            } else if (firstLine === 'Groups') {
                currentCategory = 'Groups';
                continue;
            }

            if (currentCategory) {
                const blacklist = ['Add users', 'Add groups', 'Add role', 'Users', 'Groups'];
                if (firstLine && !blacklist.includes(firstLine) && !firstLine.startsWith('Add ')) {
                    let entry = firstLine;
                    if (currentCategory === 'Users') {
                        const lines = nodeText.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                        if (lines.length >= 2 && lines[1].includes('@')) {
                            entry = `${lines[0]} (${lines[1]})`;
                        }
                    }
                    if (!results[currentRole][currentCategory].includes(entry)) {
                        results[currentRole][currentCategory].push(entry);
                    }
                }
            }
        }
        return results;
    }
    """
    
    security_info = {}
    for frame in get_all_frames_and_page(page):
        try:
            if frame.is_detached(): continue
            res = await frame.evaluate(extract_script)
            if res:
                for role, data in res.items():
                    if role not in security_info:
                        security_info[role] = { "Users": [], "Groups": [] }
                    security_info[role]["Users"] = list(set(security_info[role]["Users"] + data["Users"]))
                    security_info[role]["Groups"] = list(set(security_info[role]["Groups"] + data["Groups"]))
        except: continue
    return security_info

def log(message):
    print(message)
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {message}\n")

async def run_automation():
    # Load environment variables
    possible_paths = ['env', '../env']
    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(dotenv_path=path)
            break

    log("\n" + "="*80)
    log(" [Step 4: Box Security - Dual-Tab Expand & Extract]")
    log("="*80)
    log("To use this automation, you must launch Chrome with remote debugging enabled.")
    log("IMPORTANT: Close ALL existing Chrome windows before running the command below.")
    log("\nWindows (Command Prompt):")
    log('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\temp\\chrome_dev"')
    log("\nWindows (PowerShell):")
    log('  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\temp\\chrome_dev"')
    log("\nmacOS (Terminal):")
    log('  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_dev"')
    log("\nTroubleshooting:")
    log("  - If you see 'ECONNREFUSED ::1:9222', it means the script is trying IPv6.")
    log("  - This script now uses '127.0.0.1' to force IPv4.")
    log("="*80 + "\n")

    def select_environment():
        log("\nSelect SOURCE Environment (Tab 1):")
        log("1. STG (Staging)")
        log("2. OPS (Operations)")
        choice = input("Enter choice (1/2): ").strip()
        if choice == '2': return "OPS", os.getenv("JIRA_DC_OPS_URL", "")
        return "STG", os.getenv("JIRA_DC_STG_URL", "https://dev-jira.osci.kr")

    def select_dry_run():
        choice = input("Dry-run mode? (y/n): ").lower().strip()
        return choice == 'y'

    ENV_NAME, BASE_URL = select_environment()
    DRY_RUN = select_dry_run()

    # migration_box.json 로드
    try:
        with open("migration_box.json", "r", encoding="utf-8") as f:
            boxes = json.load(f)
    except Exception as e:
        log(f"Error loading migration_box.json: {e}")
        return

    pw = None
    browser = None
    all_extracted_data = {}

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
        if not context.pages:
            raise Exception("No open pages found.")
        
        page1 = context.pages[0]
        page2 = context.pages[1] if len(context.pages) >= 2 else None
        
        if page2:
            log("  [Target Tab Detected] Second tab will also be updated.")

        for index, box in enumerate(boxes):
            box_id = box.get("boxId")
            new_box_id = box.get("newBoxId")
            box_name = box.get("boxName", "Unknown")
            if not box_id: continue

            log(f"\n[{index+1}/{len(boxes)}] Processing Box: {box_name}")
            log(f"  Source ID: {box_id}, Target ID: {new_box_id}")

            # --- TAB 1 (Source) ---
            target_url1 = f"{BASE_URL}/plugins/servlet/softwareplant-biggantt/#/box/{box_id}/settings/security/security"
            log(f"  Tab 1 -> {target_url1}")

            if not DRY_RUN:
                await page1.bring_to_front()
                await page1.goto(target_url1)
                await asyncio.sleep(7.0) 
                
                log("  Expanding tree in Tab 1...")
                await expand_tree_on_page(page1)
                
                log("  Extracting security data from Tab 1...")
                box_security = await extract_security_from_page(page1)
                all_extracted_data[box_id] = { "boxName": box_name, "roles": box_security }

                # --- TAB 2 (Target) ---
                if page2 and new_box_id:
                    current_url2 = page2.url
                    # #box/XXXX/ 부분을 찾아서 newBoxId로 교체
                    new_url2 = re.sub(r'(#box/)[^/]+', f'\\1{new_box_id}', current_url2)
                    
                    if new_url2 != current_url2:
                        log(f"  Tab 2 -> {new_url2}")
                        await page2.bring_to_front()
                        await page2.goto(new_url2)
                        await asyncio.sleep(7.0)
                        
                        log("  Expanding tree in Tab 2...")
                        await expand_tree_on_page(page2)
                        await page1.bring_to_front() # 다시 첫 번째 탭으로 복귀
                    else:
                        log("  Tab 2 URL format not recognized for replacement.")
                elif page2 and not new_box_id:
                    log("  Tab 2 detected but 'newBoxId' is missing in migration_box.json. Skipping Tab 2.")
            else:
                log(f"  [DRY RUN] Would process Tab 1 (and Tab 2 if present) for {box_name}")

        if not DRY_RUN:
            output_file = "step4_all_boxes_security.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(all_extracted_data, f, indent=2, ensure_ascii=False)
            log(f"\n[Success] All data saved to {output_file}")

        await browser.close()
        await pw.stop()

    except Exception as err:
        log(f"\nAutomation Error: {err}")
        if browser: await browser.close()
        if pw: await pw.stop()

if __name__ == "__main__":
    # Initialize log file for a new run
    log_file = os.path.basename(__file__).replace(".py", ".log")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"--- New Migration Session Started: {datetime.now()} ---\n")
    asyncio.run(run_automation())
