"""Read-only HTML ID mapping and Jira Cloud preflight for one project."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

from field_mapping_data import JiraValidator, NeedsReview, resolve_fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=["ops", "sandbox"], default="ops")
    parser.add_argument("--project", required=True)
    parser.add_argument("--id-mappings", type=Path, required=True)
    parser.add_argument("--dc-start-field", required=True)
    parser.add_argument("--dc-end-field", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = dotenv_values(Path(__file__).resolve().parent.parent / "env")
    prefix = "JIRA_CLOUD_" + args.env.upper()
    result = {"project_key": args.project, "status": "NEEDS_REVIEW",
              "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        expected = resolve_fields(args.id_mappings, args.dc_start_field, args.dc_end_field)
        validator = JiraValidator(config.get(prefix + "_URL"), config.get(prefix + "_ADMIN_EMAIL"),
                                  config.get(prefix + "_API_TOKEN"))
        try:
            validated = validator.validate(args.project, expected["start_field_id"], expected["end_field_id"])
        finally:
            validator.session.close()
        result.update(validated)
        result.update(expected)
        result.update({"status": "VALIDATED", "screen_validation": "NOT_CHECKED",
                       "id_mappings_path": str(args.id_mappings.resolve())})
    except (NeedsReview, OSError) as exc:
        result["message"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": result["status"], "project_key": args.project,
                      "output": str(args.output), "message": result.get("message", "")}, ensure_ascii=False))
    return 0 if result["status"] == "VALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
