"""Project field mapping through the observed BigGantt UI, never direct internal writes."""

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright, expect

from biggantt_browser_auth import (DEFAULT_PROFILE, launch_context, manual_login,
                                   save_storage_state, validate_urls, wait_authenticated)
from field_mapping_data import JiraValidator, NeedsReview, field_id


ROOT = Path(__file__).resolve().parent
TRIGGERS = {"start": "#ngp-select-trigger--startDate_item", "end": "#ngp-select-trigger--endDate_item"}
MAPPING_NAMES = {"start": "Start Date", "end": "End Date"}
CONFIG_FLAGS = ("taskComment", "updateNotifications", "periodCalculatedForAssignee", "fieldScreenValidation")
# Observed non-editable entries in this UI: GET expands them from defaults;
# the native Save payload omits them. Only matching defaults may be omitted.
IMPLICIT_MAPPING_NAMES = frozenset(("Color", "Key", "Modification Time", "Original Estimate",
    "Priority", "Remaining Estimate", "Risk Consequence", "Risk Probability", "Sprint",
    "Summary", "Time Tracking", "Type"))


def expected_config(before, expected):
    after = copy.deepcopy(before)
    if before.get("migrateValues") is not False:
        raise NeedsReview("Overwrite Jira data must be explicitly disabled")
    for role, name in MAPPING_NAMES.items():
        if name not in after.get("dynamicMapping", {}):
            raise NeedsReview("Missing field mapping in current configuration: " + name)
        after["dynamicMapping"][name]["primaryFieldId"] = expected["fields"][role]["id"]
    return after


def validate_save_payload(payload, expected, project_id, project_key, default_mapping=None):
    if not isinstance(payload, dict):
        raise NeedsReview("Save payload is not a JSON object")
    if set(payload) != set(expected):
        raise NeedsReview("Save payload fields differ from the observed configuration")
    if str(payload.get("externalCatalogId")) != project_id or payload.get("projectKey") != project_key:
        raise NeedsReview("Save request targets another project")
    if payload.get("extPlatformId") != expected.get("extPlatformId"):
        raise NeedsReview("Save request targets another Jira platform")
    if payload.get("migrateValues") is not False:
        raise NeedsReview("Save would overwrite Jira issue values")
    sent = payload.get("dynamicMapping")
    wanted = expected.get("dynamicMapping")
    if not isinstance(sent, dict) or not isinstance(wanted, dict):
        raise NeedsReview("Save mapping must be an object")
    omitted = set(wanted) - set(sent)
    defaults = default_mapping or {}
    if not omitted.issubset(IMPLICIT_MAPPING_NAMES) or any(
            key not in defaults or wanted[key] != defaults[key] for key in omitted):
        raise NeedsReview("Save omits an editable or non-default mapping")
    effective = {**{key: defaults[key] for key in omitted}, **sent}
    if effective != wanted:
        raise NeedsReview("Save would change mappings beyond the intended Start/End fields")
    for key in CONFIG_FLAGS:
        if key not in payload or key not in expected or payload[key] != expected[key]:
            raise NeedsReview("Save would change an unrelated task setting: " + key)


def semantic_config(config):
    return {key: config.get(key) for key in
            ("dynamicMapping", "migrateValues", "extPlatformId", "externalCatalogId", "projectKey") + CONFIG_FLAGS}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


class MappingUI:
    def __init__(self, page, app_url, box_id, expected):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", box_id):
            raise NeedsReview("Invalid Box ID")
        self.page, self.app_url, self.box_id, self.expected = page, app_url, box_id, expected
        self.frame = self.dialog = None
        self.allowed_payload = None
        self.allowed_writes = 0
        self.write_attempts = []
        self.guard_error = None
        self.default_mapping = {}
        self.route_pattern = re.compile(r"/rest/ppm/task/config/")
        page.route(self.route_pattern, self.guard)

    def guard(self, route):
        request = route.request
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            route.continue_()
            return
        path = urlsplit(request.url).path
        record = {"method": request.method, "path": path, "allowed": False}
        self.write_attempts.append(record)
        try:
            allowed_suffixes = (
                "/catalog/" + self.expected["project_id"],
                "/catalog/" + self.expected["project_id"] + "/saveTaskConfigOnBox/" + self.box_id,
            )
            if (self.allowed_payload is None or self.allowed_writes != 0
                    or request.method not in ("POST", "PUT")
                    or not path.endswith(allowed_suffixes)):
                raise NeedsReview("Unexpected configuration write blocked")
            payload = request.post_data_json
            if isinstance(payload, dict):
                actual_mapping = payload.get("dynamicMapping") or {}
                wanted_mapping = self.allowed_payload.get("dynamicMapping") or {}
                record["mapping_diff"] = {
                    "omitted": sorted(set(wanted_mapping) - set(actual_mapping)),
                    "added": sorted(set(actual_mapping) - set(wanted_mapping)),
                    "changed": {k: {"expected": wanted_mapping[k], "sent": actual_mapping[k]}
                                for k in set(wanted_mapping) & set(actual_mapping)
                                if wanted_mapping[k] != actual_mapping[k]},
                }
            validate_save_payload(payload, self.allowed_payload,
                                  self.expected["project_id"], self.expected["project_key"], self.default_mapping)
            self.allowed_writes += 1
            record["allowed"] = True
            route.continue_()
        except Exception as exc:
            self.guard_error = str(exc) if isinstance(exc, NeedsReview) else type(exc).__name__
            print(json.dumps({"event": "CONFIG_WRITE_BLOCKED", "reason": self.guard_error,
                              "mapping_diff": record.get("mapping_diff")}, ensure_ascii=False), flush=True)
            route.abort("blockedbyclient")

    def find_frame(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            for frame in self.page.frames:
                if (urlsplit(frame.url).hostname == "cloud.softwareplant.com"
                        and frame.locator("#sp-settings-btn").count() == 1):
                    self.frame = frame
                    return
            self.page.wait_for_timeout(250)
        raise NeedsReview("BigGantt frame/settings control not found; inspect current UI")

    def open(self):
        self.page.goto(self.app_url.split("#", 1)[0] + "#box/" + self.box_id + "/g",
                       wait_until="domcontentloaded", timeout=60000)
        self.find_frame()
        expect(self.frame.locator("#sp-settings-btn")).to_be_visible(timeout=45000)
        self.frame.locator("#sp-settings-btn").click()
        menu = self.frame.get_by_text("Field mapping", exact=True)
        expect(menu).to_be_visible(timeout=45000)
        project_id = self.expected["project_id"]
        # Observe the response caused by opening the UI; never call the internal API ourselves.
        with self.page.expect_response(lambda r: r.request.method == "GET"
                and re.search(r"/rest/ppm/task/config/extPlatform/\d+/catalog/" + re.escape(project_id) + r"$", urlsplit(r.url).path),
                timeout=45000) as response_info:
            menu.click()
        response = response_info.value
        if response.status != 200:
            raise NeedsReview("UI configuration read failed: HTTP " + str(response.status))
        data = response.json()
        project = data.get("projectValueDTO") or {}
        if str(project.get("id")) != project_id or project.get("key") != self.expected["project_key"]:
            raise NeedsReview("Box UI resolved a different Jira project")
        config = data.get("catalogConfiguration")
        if not config:
            raise NeedsReview("Project inherits defaults; explicit project configuration needs separate review")
        if str(config.get("externalCatalogId")) != project_id:
            raise NeedsReview("Configuration project ID mismatch")
        self.default_mapping = copy.deepcopy((data.get("defaultConfiguration") or {}).get("dynamicMapping") or {})
        self.dialog = self.frame.get_by_role("dialog").filter(has_text="Field sync configuration")
        expect(self.dialog).to_have_count(1)
        expect(self.dialog).to_be_visible()
        expect(self.dialog.get_by_role("link", name=self.expected["project_key"], exact=True)).to_be_visible()
        current = self.read_fields()
        for role, name in MAPPING_NAMES.items():
            if config.get("dynamicMapping", {}).get(name, {}).get("primaryFieldId") != current[role]:
                raise NeedsReview("Displayed field ID disagrees with configuration response")
        return config, current

    def read_fields(self):
        values = {}
        for role, selector in TRIGGERS.items():
            trigger = self.dialog.locator(selector)
            expect(trigger).to_be_visible()
            ids = re.findall(r"\((customfield_[0-9]+)\)", trigger.inner_text())
            if len(ids) != 1:
                raise NeedsReview("Current UI field cannot be uniquely identified: " + role)
            values[role] = field_id(ids[0])
        return values

    def check_overwrite_off(self):
        control = self.dialog.locator("#migrateValues input[type=hidden]")
        expect(control).to_have_count(1)
        if control.get_attribute("value") != "false":
            raise NeedsReview("Overwrite Jira data toggle is not off")

    def select(self, role, wanted):
        self.dialog.locator(TRIGGERS[role]).click()
        search = self.frame.get_by_role("textbox", name="Start typing…", exact=True)
        expect(search).to_be_visible()
        search.fill(wanted)
        option = self.frame.get_by_role("option", name=re.compile(r"\(" + re.escape(wanted) + r"\)$"))
        expect(option).to_have_count(1, timeout=15000)
        expect(option).to_be_visible()
        option.click()
        expect(self.dialog.locator(TRIGGERS[role])).to_contain_text("(" + wanted + ")")

    def save(self, desired_config):
        self.check_overwrite_off()
        self.allowed_writes = 0
        try:
            self.dialog.get_by_role("button", name="Save", exact=True).click()
            confirmation = self.frame.get_by_role("button", name="Update custom configuration", exact=True)
            expect(confirmation).to_have_count(1)
            expect(confirmation).to_be_visible(timeout=10000)
            self.check_overwrite_off()
            self.allowed_payload = desired_config
            with self.page.expect_response(lambda r: r.request.method in ("POST", "PUT")
                    and "/rest/ppm/task/config/" in urlsplit(r.url).path,
                    timeout=45000) as response_info:
                confirmation.click()
            response = response_info.value
            if self.guard_error:
                raise NeedsReview(self.guard_error)
            if response.status not in (200, 204):
                raise NeedsReview("Mapping save HTTP " + str(response.status))
            expect(self.dialog).not_to_be_visible(timeout=45000)
            return {"method": response.request.method, "path": urlsplit(response.url).path,
                    "http_status": response.status}
        finally:
            self.allowed_payload = None

    def close(self):
        if self.dialog is not None and self.dialog.is_visible():
            self.dialog.locator("#sp-task-config-modal__close-icon").click()
            expect(self.dialog).not_to_be_visible()

    def failure_artifacts(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if self.frame is None or self.page.is_closed():
            return
        # Only retain sanitized UI structure. App links can contain bearer tokens.
        if self.dialog is not None and self.dialog.is_visible():
            self.dialog.screenshot(path=str(directory / "mapping.png"))
            html = self.dialog.evaluate("""el => {
                const copy = el.cloneNode(true);
                copy.querySelectorAll('script, iframe, style').forEach(n => n.remove());
                for (const n of [copy, ...copy.querySelectorAll('*')]) {
                    for (const a of [...n.attributes]) {
                        if (!['class','id','role','type','aria-label','aria-checked'].includes(a.name)) n.removeAttribute(a.name);
                    }
                }
                return copy.outerHTML;
            }""")
            (directory / "mapping.html").write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["dry-run", "migrate", "verify"])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--box", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--env", choices=["ops", "sandbox"], default="ops")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test-results")
    parser.add_argument("--exercise-save", action="store_true", help="Test only: reselect/save identical values; normal migration skips them")
    parser.add_argument("--interactive-login", action="store_true", help="If session expired, open manual Chrome and wait for ok")
    parser.add_argument("--login-timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.exercise_save and args.action != "migrate":
        parser.error("--exercise-save requires migrate")
    os.umask(0o077)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output_dir / ("field-mapping-" + run_id)
    result = {"run_id": run_id, "mode": args.action, "status": "PENDING", "box_id": args.box}
    atomic_json(output / "result.json", result)
    context = ui = None
    try:
        source = json.loads(args.manifest.read_text(encoding="utf-8"))
        config = dotenv_values(ROOT.parent / "env")
        prefix = "JIRA_CLOUD_" + args.env.upper()
        site = validate_urls(config.get(prefix + "_URL", ""), args.app_url)
        if source.get("site_url") != site or source.get("status") != "VALIDATED":
            raise NeedsReview("Manifest is unvalidated or targets another site")
        validator = JiraValidator(site, config.get(prefix + "_ADMIN_EMAIL"), config.get(prefix + "_API_TOKEN"))
        try:
            expected = validator.validate(source["project_key"], source["start_field_id"], source["end_field_id"])
        finally:
            validator.session.close()
        result.update({"project_key": expected["project_key"], "project_id": expected["project_id"],
                       "site_url": site, "expected": {r: expected["fields"][r]["id"] for r in TRIGGERS},
                       "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest()})
        with sync_playwright() as playwright:
            try:
                context = launch_context(playwright, args.profile)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.app_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    wait_authenticated(context, page, site, timeout=15)
                except TimeoutError:
                    if not args.interactive_login:
                        raise NeedsReview("AUTH_REQUIRED: run biggantt_browser_auth.py or use --interactive-login")
                    context.close()
                    manual_login(site, args.app_url, args.profile,
                                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", args.login_timeout,
                                 verify_after=False)
                    context = launch_context(playwright, args.profile)
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(args.app_url, wait_until="domcontentloaded", timeout=60000)
                    wait_authenticated(context, page, site, timeout=15)
                save_storage_state(context, args.profile)
                ui = MappingUI(page, args.app_url, args.box, expected)
                before, actual = ui.open()
                result["before"] = actual
                atomic_json(output / "before-config.json", semantic_config(before))
                desired = expected_config(before, expected)
                ui.check_overwrite_off()
                same = actual == result["expected"]
                if args.action == "verify":
                    result.update(status="SUCCESS" if same else "FAILED", action="VERIFY", actual=actual)
                elif args.action == "dry-run":
                    result.update(status="SKIP" if same else "PENDING", action="NO_CHANGE" if same else "WOULD_UPDATE", actual=actual)
                elif same and not args.exercise_save:
                    result.update(status="SKIP", action="ALREADY_CONFIGURED", actual=actual)
                else:
                    if args.exercise_save and not same:
                        raise NeedsReview("--exercise-save requires already-matching values")
                    for role in TRIGGERS:
                        ui.select(role, result["expected"][role])
                    if ui.read_fields() != result["expected"]:
                        raise NeedsReview("Selected fields do not match expected IDs")
                    result.update(status="PENDING", action="SAVE_INTENT")
                    atomic_json(output / "result.json", result)
                    # A lost response is reconciled with a fresh UI read, never a blind second Save.
                    try:
                        result["save_response"] = ui.save(desired)
                    except Exception as exc:
                        result["save_response_error"] = type(exc).__name__
                        if ui.guard_error:
                            raise NeedsReview(ui.guard_error) from exc
                    after, actual = ui.open()
                    result["actual"] = actual
                    atomic_json(output / "after-config.json", semantic_config(after))
                    if actual != result["expected"] or semantic_config(after) != semantic_config(desired):
                        raise NeedsReview("Save readback mismatch or unrelated settings changed")
                    if ui.allowed_writes != 1:
                        raise NeedsReview("No confirmed UI save request was sent")
                    if args.exercise_save and "save_response" not in result:
                        raise NeedsReview("Unchanged readback cannot prove a no-op save with missing response")
                    result.update(status="SUCCESS", action="SAVED_UNCHANGED" if same else "UPDATED")
                result["config_write_attempts"] = ui.write_attempts
                ui.close()
            except Exception:
                if ui is not None:
                    result["config_write_attempts"] = ui.write_attempts
                    try:
                        ui.failure_artifacts(output / "failure")
                    except Exception:
                        result["artifact_error"] = "Could not capture failure UI"
                raise
            finally:
                if context is not None:
                    context.close()
    except Exception as exc:
        result["status"] = "NEEDS_REVIEW" if isinstance(exc, NeedsReview) else "FAILED"
        result["error_type"] = type(exc).__name__
        result["message"] = str(exc) if isinstance(exc, NeedsReview) else "Execution failed; inspect stage and sanitized artifacts"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(output / "result.json", result)
    columns = ["project_key", "project_id", "status", "action", "message"]
    with (output / "field_mapping_result.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(result)
    print(json.dumps({"status": result["status"], "action": result.get("action"),
                      "report": str(output / "result.json"), "message": result.get("message", "")}, ensure_ascii=False))
    return 0 if result["status"] in ("SUCCESS", "SKIP") or args.action == "dry-run" and result["status"] == "PENDING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
