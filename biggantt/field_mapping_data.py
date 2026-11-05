"""Resolve migration IDs without executing report JavaScript; validate with Jira REST."""

import hashlib
import json
import re
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class NeedsReview(ValueError):
    """Input cannot safely identify an applicable project/field mapping."""


def field_id(value):
    match = re.fullmatch(r"(?:customfield_)?([0-9]+)", str(value).strip())
    if not match:
        raise NeedsReview("Expected a numeric custom field ID or customfield_<ID>")
    return "customfield_" + match.group(1)


def load_id_mappings(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    matches = list(re.finditer(r"\bconst\s+rawData\s*=\s*", text))
    if len(matches) != 1:
        raise NeedsReview("HTML must contain exactly one const rawData JSON array")
    try:
        rows, end = json.JSONDecoder().raw_decode(text[matches[0].end():])
    except ValueError as exc:
        raise NeedsReview("rawData is not valid JSON") from exc
    if not isinstance(rows, list):
        raise NeedsReview("rawData must be an array")
    remainder = text[matches[0].end() + end:].lstrip()
    if not remainder.startswith(";"):
        raise NeedsReview("rawData must be a literal JSON array, not an expression")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise NeedsReview("Invalid ID mapping row")
        if row.get("entityType") != "jira/classic:customField":
            continue
        source, target = field_id(row.get("serverId")), field_id(row.get("cloudId"))
        if source in result and result[source] != target:
            raise NeedsReview("Conflicting target IDs for " + source)
        result[source] = target
    if not result:
        raise NeedsReview("No custom field mappings found")
    return result, hashlib.sha256(raw).hexdigest()


def resolve_fields(path, dc_start, dc_end):
    mappings, digest = load_id_mappings(path)
    try:
        start, end = mappings[field_id(dc_start)], mappings[field_id(dc_end)]
    except KeyError as exc:
        raise NeedsReview("DC field is absent from ID mappings: " + str(exc.args[0])) from exc
    if start == end:
        raise NeedsReview("Start and End resolve to the same Cloud field")
    return {"start_field_id": start, "end_field_id": end, "id_mappings_sha256": digest}


class JiraValidator:
    def __init__(self, base_url, email, token):
        if not base_url or not base_url.startswith("https://") or not email or not token:
            raise NeedsReview("HTTPS Jira URL and Jira credentials are required")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers["Accept"] = "application/json"
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=1, status_forcelist=[429, 502, 503, 504],
            allowed_methods=["GET"], respect_retry_after_header=True,
        )))

    def get(self, path, params=None):
        try:
            response = self.session.get(self.base_url + path, params=params,
                                        timeout=(10, 30), allow_redirects=False)
        except requests.RequestException as exc:
            raise NeedsReview("Jira connection failed: " + type(exc).__name__) from exc
        if response.status_code != 200:
            raise NeedsReview("Jira validation HTTP %s at %s" % (response.status_code, path))
        try:
            return response.json()
        except ValueError as exc:
            raise NeedsReview("Jira returned non-JSON data") from exc

    def pages(self, path):
        start, rows = 0, []
        for _ in range(1000):
            data = self.get(path, {"startAt": start, "maxResults": 100})
            values = data.get("values")
            if not isinstance(values, list):
                raise NeedsReview("Invalid paginated Jira response")
            rows.extend(values)
            if data.get("isLast") is True:
                return rows
            total = data.get("total")
            if isinstance(total, int) and start + len(values) >= total:
                return rows
            if not values:
                raise NeedsReview("Incomplete Jira pagination")
            start += len(values)
        raise NeedsReview("Jira pagination limit exceeded")

    def validate(self, project_key, start_id, end_id):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", project_key):
            raise NeedsReview("Invalid project key")
        start_id, end_id = field_id(start_id), field_id(end_id)
        if start_id == end_id:
            raise NeedsReview("Start and End must be different fields")
        project = self.get("/rest/api/3/project/" + project_key)
        if project.get("key", "").upper() != project_key.upper() or not project.get("id"):
            raise NeedsReview("Project key/ID mismatch")
        project_id = str(project["id"])
        issue_types = {str(t["id"]) for t in project.get("issueTypes", [])}
        if not issue_types:
            raise NeedsReview("Project issue types could not be determined")
        fields = self.get("/rest/api/3/field")
        if not isinstance(fields, list):
            raise NeedsReview("Invalid Jira field inventory")
        by_id = {f["id"]: f for f in fields}
        resolved = {}
        for role, wanted in [("start", start_id), ("end", end_id)]:
            field = by_id.get(wanted)
            if not field:
                raise NeedsReview("Cloud field not found: " + wanted)
            schema = field.get("schema", {})
            if schema.get("type") != "date" or schema.get("custom") != "com.atlassian.jira.plugin.system.customfieldtypes:datepicker":
                raise NeedsReview("Only standard Jira Date Picker fields are supported: " + wanted)
            prefix = "/rest/api/3/field/" + wanted + "/context"
            contexts = self.pages(prefix)
            projects = self.pages(prefix + "/projectmapping")
            types = self.pages(prefix + "/issuetypemapping")
            applicable = []
            covered = set()
            for context in contexts:
                cid = str(context["id"])
                if not (context.get("isGlobalContext") is True or any(
                    str(p.get("contextId")) == cid and str(p.get("projectId")) == project_id
                    for p in projects
                )):
                    continue
                applicable.append(cid)
                if context.get("isAnyIssueType") is True:
                    covered.update(issue_types)
                else:
                    covered.update(str(t["issueTypeId"]) for t in types
                                   if str(t.get("contextId")) == cid and t.get("issueTypeId"))
            if not applicable or not issue_types.issubset(covered):
                raise NeedsReview("Field context does not cover all project issue types: " + wanted)
            resolved[role] = {
                "id": wanted, "name": field["name"], "schema": schema,
                "context_ids": applicable,
                "same_name_ids": sorted(f["id"] for f in fields if f.get("name") == field["name"]),
            }
        return {"site_url": self.base_url, "project_key": project["key"],
                "project_id": project_id, "issue_type_ids": sorted(issue_types), "fields": resolved}
