#!/usr/bin/env python3
"""Exalate communication-server security evidence collector.

This is a standalone, standard-library-only script intended to be copied to
an internal execution environment.  It does not configure Jira Cloud, change
Exalate, run SQL, or make a security decision.  It collects evidence for the
security team to review:

* communication-server / Jira DC / Jira Cloud endpoints and environment;
* explicitly supplied, read-only REST endpoint responses;
* Connection, Trigger, and Outgoing/Incoming Rule references from a redacted
  JSON snapshot;
* data-transfer candidates and Cloud compatibility review items;
* a DBA follow-up plan when active Exalate configuration is not available via
  REST or snapshot.

Example:
    python3 exalate_security_evidence.py --target STG

For live read-only endpoint checks, credentials should be supplied through
environment variables rather than command-line arguments:
    EXALATE_AUDIT_USERNAME / EXALATE_AUDIT_PASSWORD / EXALATE_AUDIT_TOKEN

The default ``--env-file ../env`` mapping is:
    STG: CONF_DC_STG_* or STG_CONF_* -> communication server
         JIRA_DC_STG_* -> Jira Data Center
         JIRA_CLOUD_SANDBOX_* -> Jira Cloud test tenant
    OPS: CONF_DC_OPS_* or OPS_CONF_* -> communication server
         JIRA_DC_OPS_* -> Jira Data Center
         JIRA_CLOUD_OPS_* -> Jira Cloud tenant

The JSON snapshot format is intentionally loose.  The following keys are
recognized when present: communication_server, jira_dc, jira_cloud,
connections, triggers, sync_rules, rest_endpoints.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCRIPT_VERSION = "1.0"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
JIRA_MONITOR_ENDPOINTS = {
    "outgoing_queue": "/rest/issuehub/4.0/syncevents?limit=0",
    "incoming_queue": "/rest/issuehub/4.0/syncrequests?limit=0",
    "errors": "/rest/issuehub/4.0/errors?limit=0",
}
DB_TABLES = [
    "AO_08F1AF_TRIGGER",
    "AO_08F1AF_TRIGGER_CONFIG_DTO",
    "AO_08F1AF_INSTANCE",
    "AO_08F1AF_NODE",
    "AO_08F1AF_RELATION",
    "AO_08F1AF_SYNC_EVENT",
    "AO_08F1AF_SYNC_REQUEST",
    "AO_08F1AF_REPLICA",
    "AO_08F1AF_ERROR",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load_env_file(path: Path) -> Dict[str, str]:
    """Load a simple dotenv file without requiring python-dotenv."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def first_value(values: Dict[str, str], *names: str) -> Optional[str]:
    for name in names:
        value = values.get(name)
        if value:
            return value
    return None


def resolve_env_settings(values: Dict[str, str], target: str) -> Dict[str, Any]:
    """Map the existing ../env naming convention to the audit scope."""
    is_ops = target.upper() in {"OPS", "JIRA_DC_OPS"}
    if is_ops:
        conf_names = ("CONF_DC_OPS_URL", "OPS_CONF_URL")
        conf_user_names = ("CONF_DC_OPS_USERNAME", "OPS_CONF_USERNAME")
        conf_password_names = ("CONF_DC_OPS_PASSWORD", "OPS_CONF_PASSWORD")
        conf_cookie_names = ("OPS_CONF_COOKIE",)
        dc_url_names = ("JIRA_DC_OPS_URL", "OPS_JIRA_URL")
        dc_token_names = ("JIRA_DC_OPS_API_TOKEN", "JIRA_DC_OPS_TOKEN")
        dc_user_names = ("JIRA_DC_OPS_USERNAME", "OPS_JIRA_USERNAME")
        dc_password_names = ("JIRA_DC_OPS_PASSWORD", "OPS_JIRA_PASSWORD")
        cloud_url_names = ("JIRA_CLOUD_OPS_URL",)
        cloud_email_names = ("JIRA_CLOUD_OPS_ADMIN_EMAIL",)
        cloud_token_names = ("JIRA_CLOUD_OPS_API_TOKEN",)
    else:
        conf_names = ("CONF_DC_STG_URL", "STG_CONF_URL")
        conf_user_names = ("CONF_DC_STG_USERNAME", "STG_CONF_USERNAME")
        conf_password_names = ("CONF_DC_STG_PASSWORD", "STG_CONF_PASSWORD")
        conf_cookie_names = ("STG_CONF_COOKIE",)
        dc_url_names = ("JIRA_DC_STG_URL", "STG_JIRA_URL")
        dc_token_names = ("JIRA_DC_STG_API_TOKEN", "JIRA_DC_STG_TOKEN")
        dc_user_names = ("JIRA_DC_STG_USERNAME", "STG_JIRA_USERNAME")
        dc_password_names = ("JIRA_DC_STG_PASSWORD", "STG_JIRA_PASSWORD")
        cloud_url_names = ("JIRA_CLOUD_SANDBOX_URL", "JIRA_CLOUD_STG_URL")
        cloud_email_names = ("JIRA_CLOUD_SANDBOX_ADMIN_EMAIL", "JIRA_CLOUD_STG_ADMIN_EMAIL")
        cloud_token_names = ("JIRA_CLOUD_SANDBOX_API_TOKEN", "JIRA_CLOUD_STG_API_TOKEN")

    return {
        "communication_server": {
            "url": first_value(values, *conf_names),
            "username": first_value(values, *conf_user_names),
            "password": first_value(values, *conf_password_names),
            "cookie": first_value(values, *conf_cookie_names),
            "urlSource": next((name for name in conf_names if values.get(name)), None),
        },
        "jira_dc": {
            "url": first_value(values, *dc_url_names),
            "token": first_value(values, *dc_token_names),
            "username": first_value(values, *dc_user_names),
            "password": first_value(values, *dc_password_names),
        },
        "jira_cloud": {
            "url": first_value(values, *cloud_url_names),
            "adminEmail": first_value(values, *cloud_email_names),
            "token": first_value(values, *cloud_token_names),
        },
        "verifyTls": str(first_value(values, "JIRA_SSL_VERIFY", "VERIFY_SSL") or "true").lower() in {"true", "1", "yes", "on"},
        "outputDir": first_value(values, "OUTPUT_DIR") or ".",
        "timeout": float(first_value(values, "REQUEST_TIMEOUT") or "20"),
        "restEndpoints": {
            key: values[key]
            for key in ("EXALATE_CONNECTIONS_ENDPOINT", "EXALATE_TRIGGERS_ENDPOINT")
            if values.get(key)
        },
    }


def redact_text(value: str) -> str:
    """Redact credentials and tokens from evidence text."""
    value = re.sub(
        r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^,;\s)]+",
        r"\1=***REDACTED***",
        value,
    )
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***REDACTED***", value)
    value = re.sub(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@", r"\1***REDACTED***@", value)
    return value[:500]


def redact_json(value: Any, parent_key: str = "") -> Any:
    """Redact secrets from an arbitrary JSON-like object."""
    sensitive = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization|private[_ -]?key|credential)")
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if sensitive.search(str(key)) else redact_json(item, str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item, parent_key) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def url_inventory(values: Iterable[Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for value in values:
        url = safe_url(value)
        if not url or url in seen:
            continue
        seen.add(url)
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        is_private_name = (
            host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith(".internal")
            or host.endswith(".local")
            or host.endswith(".corp")
            or host.endswith(".lan")
            or re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)", host) is not None
        )
        result.append({
            "url": url,
            "scheme": parsed.scheme,
            "host": host,
            "port": parsed.port or (443 if parsed.scheme == "https" else 80),
            "privateOrInternalName": is_private_name,
            "reviewItems": [
                "Confirm ownership and approved data-flow direction.",
                "Confirm TLS certificate validation and permitted cipher/protocol policy.",
                "Confirm firewall, proxy, DNS, and egress/ingress allow-list policy.",
            ],
        })
    return result


def walk_strings(value: Any, path: str = "") -> Iterable[Tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from walk_strings(item, child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def flatten_urls(value: Any) -> List[str]:
    urls: List[str] = []
    for _, text in walk_strings(value):
        urls.extend(re.findall(r"https?://[^\s\"'<>),]+", text))
    return urls


def line_evidence(text: str, match: re.Match[str]) -> str:
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    if end == -1:
        end = len(text)
    return redact_text(text[start:end].strip())


RULE_CHECKS: Sequence[Tuple[str, str, str, str]] = (
    ("attachment", r"(?i)\b(?:replica\s*\.\s*attachments|attachmentHelper|mergeAttachments)\b", "Attachment data may cross the boundary.", "Confirm file classification, size, retention, malware scanning, and Cloud approval."),
    ("comments-worklogs-history", r"(?i)\b(?:replica\s*\.\s*(?:comments|workLogs?|changeHistory|changelog)|commentHelper|worklog)\b", "Comments, worklogs, or history may contain confidential or personal data.", "Review public/internal visibility and approve each data class with the security team."),
    ("user-data", r"(?i)\b(?:replica\s*\.\s*(?:reporter|assignee|creator|author)|issue\s*\.\s*(?:reporter|assignee|creator)|email|displayName|username)\b", "User identity or contact data may be transferred.", "Confirm minimization, account-ID mapping, privacy basis, and Cloud visibility."),
    ("security-customer-data", r"(?i)\b(?:securityLevel|requestParticipants|organizations?|customer|watchers?)\b", "Security scope, customer, participant, or watcher data is referenced.", "Verify access-control and privacy semantics after moving to Cloud."),
    ("custom-field", r"(?i)\bcustomFields?\b|customfield[_-]?\d+", "Custom Field contents are not classified by the rule text alone.", "Map each field ID/name to its business classification and Cloud approval."),
    ("broad-field-mapping", r"(?i)\b(?:replica\s*=\s*issue|replica\s*\.\s*(?:fields|allFields)|issue\s*\.\s*fields\b)", "A broad assignment may include fields outside the approved allow-list.", "Replace with an explicit allow-list before Cloud activation."),
    ("credential-or-secret", r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|authorization|private[_ ]?key)\b", "A credential or secret reference appears in configuration text.", "Remove from rules, use secret management, and rotate if an actual value was exposed."),
    ("external-or-dc-api", r"(?i)\b(?:httpClient\.(?:get|post|put|patch|delete)|java\.sql|groovy\.sql|ComponentAccessor|com\.atlassian\.)\b", "The rule references an external call, database, or Data Center-specific API.", "Review egress, authentication, logging, and Cloud implementation compatibility."),
)


def extract_rule_text(record: Dict[str, Any], direction: str) -> str:
    if direction == "OUTGOING":
        keys = ("outgoingSync", "outgoingRule", "outgoing", "syncRules.outgoing", "rules.outgoing", "outgoingMappings", "fieldMappings")
    else:
        keys = ("incomingSync", "incomingRule", "incoming", "syncRules.incoming", "rules.incoming", "incomingMappings", "fieldMappings")

    def get_path(item: Any, path: str) -> Any:
        current = item
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    for key in keys:
        value = get_path(record, key)
        if value is not None:
            return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    return ""


def connection_records(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = snapshot.get("connections", snapshot.get("connection", []))
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        value = value["value"]
    if isinstance(value, dict):
        value = [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def trigger_records(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = snapshot.get("triggers", snapshot.get("trigger", []))
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        value = value["value"]
    if isinstance(value, dict):
        value = [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def finding(direction: str, connection: str, category: str, match: str, evidence: str, recommendation: str) -> Dict[str, Any]:
    return {
        "reviewStatus": "SECURITY_TEAM_REVIEW_REQUIRED",
        "direction": direction,
        "connection": connection,
        "category": category,
        "matched": redact_text(match),
        "evidence": redact_text(evidence),
        "recommendation": recommendation,
    }


def analyze_configuration(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    data_candidates: List[Dict[str, str]] = []
    connections = connection_records(snapshot)
    triggers = trigger_records(snapshot)

    for record in connections:
        name = str(record.get("name") or record.get("connectionName") or record.get("id") or "UNKNOWN")
        for direction in ("OUTGOING", "INCOMING"):
            text = extract_rule_text(record, direction)
            if not text:
                findings.append(finding(
                    direction, name, "rule-not-available", "active rule text unavailable",
                    "The supplied Connection object did not contain this rule.",
                    "Obtain the active rule from a supported Exalate export or DBA snapshot before approval.",
                ))
                continue

            for match in re.finditer(r"(?i)\breplica\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", text):
                field_name = match.group(1)
                candidate = {"connection": name, "direction": direction, "replicaField": field_name}
                if candidate not in data_candidates:
                    data_candidates.append(candidate)
            for category, pattern, description, recommendation in RULE_CHECKS:
                match = re.search(pattern, text)
                if match:
                    findings.append(finding(direction, name, category, match.group(0), line_evidence(text, match), recommendation))

    for record in triggers:
        name = str(record.get("name") or record.get("triggerName") or record.get("id") or "UNKNOWN")
        query = str(record.get("jql") or record.get("query") or record.get("condition") or record.get("filter") or "")
        if not query:
            findings.append(finding("TRIGGER", name, "trigger-query-not-available", "trigger query unavailable", "No JQL/filter was present in the supplied Trigger object.", "Obtain the active Trigger filter before approval."))
        elif re.search(r"(?i)(email|username|displayName|assignee|reporter|watcher)", query):
            findings.append(finding("TRIGGER", name, "user-related-filter", "user-related JQL", redact_text(query), "Review privacy impact and account-ID behavior with the security team."))

    has_configuration = bool(connections or triggers or snapshot.get("sync_rules"))
    missing_configuration = not has_configuration
    return {
        "connectionsInspected": len(connections),
        "triggersInspected": len(triggers),
        "dataTransferCandidates": data_candidates,
        "reviewItems": findings,
        "configurationAvailable": has_configuration,
        "configurationComplete": has_configuration and not any(item["category"] == "rule-not-available" for item in findings),
        "securityDecision": "NOT_PERFORMED_SECURITY_TEAM_REVIEW_REQUIRED",
        "dbFollowUpRequired": missing_configuration or any(item["category"] == "rule-not-available" for item in findings),
    }


def auth_header(credentials: Dict[str, Optional[str]]) -> Optional[str]:
    token = credentials.get("token") or os.getenv("EXALATE_AUDIT_TOKEN")
    if token:
        return f"Bearer {token}"
    username = credentials.get("username") or os.getenv("EXALATE_AUDIT_USERNAME")
    password = credentials.get("password") or os.getenv("EXALATE_AUDIT_PASSWORD")
    if username and password:
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {encoded}"
    return None


def request_json(
    url: str,
    timeout: float,
    verify_tls: bool,
    ca_file: Optional[str],
    credentials: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": f"Exalate-Security-Evidence/{SCRIPT_VERSION}"}
    credentials = credentials or {}
    authorization = auth_header(credentials)
    if authorization:
        headers["Authorization"] = authorization
    if credentials.get("cookie"):
        headers["Cookie"] = str(credentials["cookie"])
    request = urllib.request.Request(url, headers=headers, method="GET")
    context: Optional[ssl.SSLContext] = None
    if urllib.parse.urlparse(url).scheme == "https":
        if verify_tls:
            context = ssl.create_default_context(cafile=ca_file)
        else:
            context = ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
        parsed = json.loads(body.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {"value": parsed}


def probe_endpoints(
    base_url: Optional[str],
    explicit: Dict[str, str],
    timeout: float,
    verify_tls: bool,
    ca_file: Optional[str],
    credentials: Optional[Dict[str, Optional[str]]] = None,
    include_monitoring: bool = False,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    responses: Dict[str, Any] = {}
    if not base_url and not explicit:
        return {"checks": checks, "responses": responses, "configurationRestStatus": "NOT_TESTED"}

    endpoints = dict(explicit)
    if base_url and include_monitoring:
        for name, path in JIRA_MONITOR_ENDPOINTS.items():
            endpoints.setdefault(name, urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))

    config_kinds = {key for key in endpoints if key in {"connections", "triggers", "configurations"}}
    for kind, raw_url in endpoints.items():
        url = raw_url if urllib.parse.urlparse(raw_url).scheme else urllib.parse.urljoin((base_url or "").rstrip("/") + "/", raw_url.lstrip("/"))
        check: Dict[str, Any] = {"kind": kind, "url": safe_url(url) or "INVALID_URL", "method": "GET"}
        try:
            data = request_json(url, timeout, verify_tls, ca_file, credentials)
            responses[kind] = redact_json(data)
            check.update({"status": "AVAILABLE", "json": True})
            if kind in {"connections", "triggers", "configurations"}:
                config_kinds.add(kind)
        except urllib.error.HTTPError as exc:
            check.update({"status": "HTTP_ERROR", "httpStatus": exc.code, "detail": redact_text(str(exc.reason))})
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            check.update({"status": "ERROR", "detail": redact_text(str(exc))})
        checks.append(check)

    config_available = bool(config_kinds.intersection({"connections", "triggers", "configurations"}))
    return {
        "checks": checks,
        "responses": responses,
        "configurationRestStatus": "AVAILABLE" if config_available else "NO_DOCUMENTED_CONFIGURATION_ENDPOINT",
    }


def database_follow_up() -> Dict[str, Any]:
    return {
        "required": True,
        "purpose": "DBA-assisted read-only extraction of active Exalate configuration",
        "candidateTables": DB_TABLES,
        "instructions": [
            "Confirm the AO prefix and columns for the installed Exalate version.",
            "Export only approved columns from Trigger/configuration/Instance/Relation data.",
            "Redact credentials, tokens, connection secrets, and blob contents before sharing.",
            "Do not run DROP, DELETE, UPDATE, or INSERT statements.",
            "Database access is not performed by this script.",
        ],
    }


def build_report(args: argparse.Namespace, snapshot: Dict[str, Any], probes: Dict[str, Any]) -> Dict[str, Any]:
    analysis = analyze_configuration(snapshot)
    if not analysis["dbFollowUpRequired"] and probes["configurationRestStatus"] != "AVAILABLE":
        # A snapshot may be complete even if no live REST endpoint is tested.
        db_plan: Dict[str, Any] = {}
    else:
        db_plan = database_follow_up() if analysis["dbFollowUpRequired"] else {}

    endpoints = []
    endpoints.extend(flatten_urls(snapshot))
    endpoints.extend([
        args.communication_server_url or "",
        args.jira_dc_url or "",
        args.jira_cloud_url or "",
    ])
    for check in probes["checks"]:
        endpoints.append(check.get("url", ""))

    return {
        "reportType": "EXALATE_COMMUNICATION_SERVER_SECURITY_EVIDENCE",
        "reportVersion": SCRIPT_VERSION,
        "collectedAt": now_iso(),
        "executionContext": {
            "assessmentEnvironment": args.environment,
            "executionEnvironment": args.execution_environment,
            "target": getattr(args, "target", None),
            "envFile": str(getattr(args, "env_file", "")),
            "collectorHost": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "scope": {
            "communicationServer": redact_json(snapshot.get("communication_server", {})),
            "jiraDataCenter": redact_json(snapshot.get("jira_dc", {})),
            "jiraCloud": redact_json(snapshot.get("jira_cloud", {})),
            "securityOwner": "Security Team",
            "securityDecision": "NOT_PERFORMED",
        },
        "networkEndpoints": url_inventory(endpoints),
        "restEvidence": {
            "onlyGetRequests": True,
            "configurationStatus": probes["configurationRestStatus"],
            "checks": probes["checks"],
            "responses": probes["responses"],
        },
        "configurationAnalysis": analysis,
        "databaseFollowUp": db_plan,
        "securityTeamChecklist": [
            "Approve the communication-server trust boundary and data-flow diagram.",
            "Approve Jira Cloud tenant, Exalate tenant, region/data residency, and retention policy.",
            "Validate TLS certificate verification, firewall/proxy rules, egress/ingress allow-lists, and DNS.",
            "Review proxy/service-account least privilege and project-level access.",
            "Review Outgoing Rule allow-list, Incoming Rule mappings, Trigger scope, attachments, comments, users, and custom fields.",
            "Review secrets management, log redaction, monitoring, vulnerability patching, backup, and incident response.",
            "Run a controlled test with synthetic/non-sensitive Jira issues before production approval.",
        ],
    }


def parse_endpoint_values(values: Sequence[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--rest-endpoint must be KIND=URL: {value}")
        kind, url = value.split("=", 1)
        if not kind.strip() or not url.strip():
            raise ValueError(f"--rest-endpoint must be KIND=URL: {value}")
        result[kind.strip()] = url.strip()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Exalate communication-server security evidence (read-only).")
    parser.add_argument("--env-file", type=Path, default=Path("../env"), help="Environment file (default: ../env).")
    parser.add_argument("--target", choices=("STG", "OPS"), default=None, help="Environment in ../env (default: STG).")
    parser.add_argument("--input", type=Path, help="Redacted JSON snapshot of communication server / Exalate configuration.")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON report path (default: OUTPUT_DIR/exalate-security-evidence.json).")
    parser.add_argument("--environment", default=None, help="Assessment environment label (default: target name).")
    parser.add_argument("--execution-environment", default="internal", help="Where this script runs, e.g. internal.")
    parser.add_argument("--communication-server-url", help="Communication server base URL to record in evidence.")
    parser.add_argument("--jira-dc-url", help="Jira Data Center base URL for read-only monitoring endpoint checks.")
    parser.add_argument("--jira-cloud-url", help="Jira Cloud base URL to record in the data-flow inventory.")
    parser.add_argument("--rest-endpoint", action="append", default=[], metavar="KIND=URL", help="Explicit read-only GET endpoint; repeatable, e.g. connections=/rest/.../connections")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--ca-file", help="Optional CA bundle for TLS verification.")
    parser.add_argument("--no-verify-tls", action="store_true", help="Disable TLS verification only for a test endpoint; record this in the report.")
    parser.add_argument("--probe-jira-monitoring", action="store_true", help="Also GET the documented Jira DC queue/error monitoring endpoints.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        file_values = load_env_file(args.env_file)
        # Explicit process variables take precedence over ../env, while the
        # file remains the normal source for the internal execution server.
        env_values = dict(file_values)
        env_values.update(os.environ)
        target = (args.target or first_value(env_values, "JIRA_TARGET_ENV") or "STG").upper()
        settings = resolve_env_settings(env_values, target)
        communication = settings["communication_server"]
        jira_dc = settings["jira_dc"]
        jira_cloud = settings["jira_cloud"]
        communication_url = args.communication_server_url or communication.get("url")
        jira_dc_url = args.jira_dc_url or jira_dc.get("url")
        jira_cloud_url = args.jira_cloud_url or jira_cloud.get("url")
        args.environment = args.environment or ("test" if target == "STG" else "ops")
        args.communication_server_url = communication_url
        args.jira_dc_url = jira_dc_url
        args.jira_cloud_url = jira_cloud_url
        if args.output is None:
            args.output = Path(settings["outputDir"]) / "exalate-security-evidence.json"
        if args.timeout == 20.0:
            args.timeout = settings["timeout"]

        snapshot: Dict[str, Any] = {
            "communication_server": {
                "url": communication_url,
                "environment": args.environment,
                "urlSource": communication.get("urlSource"),
                "credentialsConfigured": bool(communication.get("username") and communication.get("password")) or bool(communication.get("cookie")) or bool(os.getenv("EXALATE_AUDIT_TOKEN")),
            },
            "jira_dc": {"url": jira_dc_url, "environment": args.environment},
            "jira_cloud": {"url": jira_cloud_url, "environment": args.environment, "adminEmailConfigured": bool(jira_cloud.get("adminEmail"))},
        }
        if args.input:
            with args.input.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("--input must contain a JSON object")
            snapshot.update(loaded)

        explicit = parse_endpoint_values(args.rest_endpoint)
        for kind, url in settings["restEndpoints"].items():
            explicit.setdefault(kind, url)
        if isinstance(snapshot.get("rest_endpoints"), dict):
            for kind, url in snapshot["rest_endpoints"].items():
                if isinstance(kind, str) and isinstance(url, str):
                    explicit.setdefault(kind, url)

        probes = probe_endpoints(
            base_url=communication_url,
            explicit=explicit,
            timeout=args.timeout,
            verify_tls=settings["verifyTls"] and not args.no_verify_tls,
            ca_file=args.ca_file,
            credentials=communication,
            include_monitoring=False,
        )
        if args.probe_jira_monitoring and jira_dc_url:
            monitoring = probe_endpoints(
                base_url=jira_dc_url,
                explicit={},
                timeout=args.timeout,
                verify_tls=settings["verifyTls"] and not args.no_verify_tls,
                ca_file=args.ca_file,
                credentials=jira_dc,
                include_monitoring=True,
            )
            probes["checks"].extend(monitoring["checks"])
            probes["responses"].update(monitoring["responses"])
        # Treat a live REST response as an in-memory snapshot for the same
        # analysis path.  Only redacted responses are retained in the report.
        for kind in ("connections", "triggers"):
            if kind not in snapshot and kind in probes["responses"]:
                snapshot[kind] = probes["responses"][kind]
        report = build_report(args, snapshot, probes)
        report["restEvidence"]["tlsVerification"] = "ENABLED" if settings["verifyTls"] and not args.no_verify_tls else "DISABLED_FOR_TEST"
        report["input"] = {"path": str(args.input) if args.input else None, "rawConfigExported": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        analysis = report["configurationAnalysis"]
        print(f"Report: {args.output}")
        print(f"Environment: {args.environment} / execution: {args.execution_environment}")
        print(f"Connections: {analysis['connectionsInspected']}, triggers: {analysis['triggersInspected']}")
        print(f"REST configuration: {report['restEvidence']['configurationStatus']}")
        print(f"Security review items: {len(analysis['reviewItems'])}")
        print(f"DB follow-up required: {'YES' if analysis['dbFollowUpRequired'] else 'NO'}")
        print("Security decision: SECURITY TEAM REVIEW REQUIRED")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {redact_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
