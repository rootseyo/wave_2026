"""Sequential project batches using the existing guarded Playwright runner."""

import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parent
STATUSES = {"PENDING", "SUCCESS", "SKIP", "FAILED", "NEEDS_REVIEW"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_projects(batch_path, selected=None):
    """Resolve paths relative to the batch file; conflicting duplicates never execute."""
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    if batch.get("schema_version") != 1 or not isinstance(batch.get("projects"), list):
        raise ValueError("Expected schema_version=1 and projects array")
    projects = {}
    for entry in batch["projects"]:
        key = entry["project_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            raise ValueError("Invalid project key")
        if selected and key not in selected:
            continue
        item = projects.setdefault(key, {"project_key": key, "candidates": []})
        try:
            box = entry["box_id"]
            if not isinstance(box, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", box):
                raise ValueError("Invalid Box ID")
            path = (batch_path.parent / entry["manifest"]).resolve()
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("project_key") != key or manifest.get("status") != "VALIDATED":
                raise ValueError("Manifest project mismatch or not validated")
            identity = {name: manifest[name] for name in
                        ("site_url", "project_id", "start_field_id", "end_field_id")}
            item["candidates"].append({"box_id": box, "manifest": manifest, "identity": identity})
        except (OSError, ValueError, KeyError, TypeError):
            item["error"] = "Missing/invalid manifest or Box; review this project"
    if selected and selected - set(projects):
        raise ValueError("Requested project absent from batch: " + ", ".join(sorted(selected - set(projects))))
    for item in projects.values():
        candidates = item["candidates"]
        if len({digest(c["identity"]) for c in candidates}) > 1:
            item["error"] = "Conflicting site/project/field mappings for duplicate project"
        item["fingerprint"] = digest(item)
        if candidates:
            # A Box is navigation context only. Every candidate must agree before dedup.
            item["chosen"] = candidates[0]
    return projects


def save_results(directory, state):
    write_json(directory / "checkpoint.json", state)
    target = directory / "field_mapping_result.csv"
    temporary = target.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["project_key", "start_field_id", "end_field_id", "status", "action", "message", "report"]
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(state["projects"].values())
    temporary.chmod(0o600)
    temporary.replace(target)


def run_project(item, args, directory):
    chosen = item["chosen"]
    attempt = directory / "attempts" / item["project_key"] / uuid.uuid4().hex
    snapshot = attempt / "manifest.json"
    write_json(snapshot, chosen["manifest"])
    output = attempt / "output"
    command = [sys.executable, str(ROOT / "biggantt_field_mapping.py"), args.action,
               "--manifest", str(snapshot), "--box", chosen["box_id"],
               "--app-url", args.app_url, "--env", args.env,
               "--profile", str(args.profile), "--output-dir", str(output),
               "--login-timeout", str(args.login_timeout)]
    if args.interactive_login:
        command.append("--interactive-login")
    # Inherit stdin so manual login can wait for a human's ok. Never send it automatically.
    completed = subprocess.run(command, check=False)
    reports = list(output.glob("field-mapping-*/result.json"))
    if len(reports) != 1:
        raise ValueError("Child runner did not produce exactly one result")
    report = reports[0]
    result = json.loads(report.read_text(encoding="utf-8"))
    if result.get("status") not in STATUSES:
        raise ValueError("Invalid child status")
    if result["status"] in {"SUCCESS", "SKIP"} or (
            args.action == "dry-run" and result["status"] == "PENDING"):
        expected = {"start": chosen["identity"]["start_field_id"],
                    "end": chosen["identity"]["end_field_id"]}
        if (completed.returncode != 0 or result.get("project_key") != item["project_key"]
                or result.get("mode") != args.action or result.get("box_id") != chosen["box_id"]
                or result.get("site_url") != chosen["identity"]["site_url"]
                or result.get("expected") != expected
                or result.get("manifest_sha256") != hashlib.sha256(snapshot.read_bytes()).hexdigest()):
            raise ValueError("Child success result identity mismatch")
    return {"status": result["status"], "action": result.get("action", ""),
            "message": result.get("message", ""), "report": str(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["dry-run", "migrate", "verify"])
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--env", choices=["ops", "sandbox"], default="ops")
    parser.add_argument("--profile", type=Path, default=ROOT / ".playwright/manual-ops-profile")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project", action="append", default=[])
    parser.add_argument("--projects", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--interactive-login", action="store_true")
    parser.add_argument("--login-timeout", type=int, default=1800)
    args = parser.parse_args()
    os.umask(0o077)
    args.profile = args.profile.resolve()
    directory = args.output_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    selected = set(args.project) | {k.strip() for k in args.projects.split(",") if k.strip()}
    try:
        projects = load_projects(args.batch.resolve(), selected)
        if not projects:
            raise ValueError("Batch contains no selected projects")
        identity = {"action": args.action, "app_url": args.app_url, "env": args.env,
                    "profile": str(args.profile), "projects": sorted(projects)}
        # Kernel lock releases on process exit. Do not delete the lock file while locked.
        with (directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checkpoint = directory / "checkpoint.json"
            if args.resume:
                state = json.loads(checkpoint.read_text(encoding="utf-8"))
                if state.get("identity") != identity:
                    raise ValueError("Resume requires same mode/site/profile/project selection")
            else:
                if checkpoint.exists():
                    raise ValueError("Checkpoint exists; use --resume or a new output directory")
                state = {"identity": identity, "projects": {}}
            for key, item in projects.items():
                previous = state["projects"].get(key, {})
                if (args.resume and previous.get("status") == "NEEDS_REVIEW"
                        and previous.get("fingerprint") == item["fingerprint"]):
                    print(key + " NEEDS_REVIEW: unchanged input; manual review required", flush=True)
                    continue
                chosen = item.get("chosen", {}).get("identity", {})
                row = {"project_key": key, "fingerprint": item["fingerprint"], "status": "PENDING",
                       "start_field_id": chosen.get("start_field_id", ""),
                       "end_field_id": chosen.get("end_field_id", "")}
                state["projects"][key] = row
                save_results(directory, state)
                if "error" in item:
                    row.update(status="NEEDS_REVIEW", message=item["error"])
                else:
                    try:
                        # Re-read successful projects too: checkpoints do not establish live state.
                        row.update(run_project(item, args, directory))
                    except (OSError, ValueError, KeyError, TypeError):
                        row.update(status="FAILED", message="Runner/report failure; inspect attempt output")
                save_results(directory, state)
                print(key + " " + row["status"], flush=True)
            good = {"SUCCESS", "SKIP"} | ({"PENDING"} if args.action == "dry-run" else set())
            return 0 if all(r["status"] in good for r in state["projects"].values()) else 2
    except KeyboardInterrupt:
        print("Interrupted; resume will reconcile pending projects", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError):
        print("Batch input/checkpoint invalid or locked; inspect configuration", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
