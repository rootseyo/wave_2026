"""Read-only Exalate configuration discovery and security review.

The public Exalate monitoring API exposes queue and error endpoints, but it does
not document a stable Connection/Trigger/Sync Rule export API for Jira
Data Center.  This module therefore keeps configuration discovery deliberately
defensive:

* only GET requests are made;
* an endpoint can be supplied explicitly through configuration;
* a small list of candidates is probed only to establish evidence of whether a
  deployment exposes a configuration endpoint;
* no sync rule or credential is sent back to the server.

The scanner is a static review aid.  It identifies data classes referenced by
outgoing rules; it cannot prove what a Groovy script will produce at runtime.
"""

from dataclasses import asdict, dataclass, field
import html
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from exalate.client import ExalateClientError, ExalateHttpClient


@dataclass
class EndpointCheck:
    """Evidence collected while testing one possible configuration endpoint."""

    kind: str
    endpoint: str
    status: str
    detail: str = ""
    item_count: Optional[int] = None


@dataclass
class SecurityFinding:
    """One static security or Cloud compatibility finding."""

    severity: str
    direction: str
    connection: str
    rule_type: str
    category: str
    matched: str
    evidence: str
    recommendation: str


@dataclass
class ConfigurationAudit:
    """Complete configuration audit result suitable for JSON export."""

    base_url: str
    collected_at: str
    rest_configuration_available: bool
    rest_configuration_status: str
    endpoint_checks: List[EndpointCheck] = field(default_factory=list)
    connections: List[Dict[str, Any]] = field(default_factory=list)
    triggers: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[SecurityFinding] = field(default_factory=list)
    database_required: bool = False
    database_investigation: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {
            "jira": {"baseUrl": self.base_url},
            "collectedAt": self.collected_at,
            "configurationSource": {
                "restAvailable": self.rest_configuration_available,
                "status": self.rest_configuration_status,
                "databaseRequired": self.database_required,
            },
            "endpointChecks": [asdict(item) for item in self.endpoint_checks],
            "connections": self.connections,
            "triggers": self.triggers,
            "findings": [asdict(item) for item in self.findings],
            "databaseInvestigation": self.database_investigation,
        }


class ExalateConfigurationApi:
    """Best-effort, GET-only discovery of configuration endpoints."""

    # These are intentionally candidates, not claimed public API contracts.
    DEFAULT_ENDPOINTS: Dict[str, Tuple[str, ...]] = {
        "connections": (
            "/rest/issuehub/4.0/connections",
            "/rest/issuehub/4.0/connection",
            "/rest/issuehub/4.0/configurations/connections",
        ),
        "triggers": (
            "/rest/issuehub/4.0/triggers",
            "/rest/issuehub/4.0/trigger",
            "/rest/issuehub/4.0/configurations/triggers",
        ),
    }

    def __init__(self, http_client: ExalateHttpClient, logger: Optional[logging.Logger] = None):
        self.client = http_client
        self.logger = logger or logging.getLogger("exalate_audit")

    @staticmethod
    def _count_items(data: Any) -> Optional[int]:
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("connections", "triggers", "results", "items", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return len(value)
        return None

    def discover(
        self,
        endpoint_overrides: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, Any], List[EndpointCheck]]:
        """Try explicit endpoints first, then candidates, using GET only."""
        overrides = endpoint_overrides or {}
        payloads: Dict[str, Any] = {}
        checks: List[EndpointCheck] = []

        for kind in ("connections", "triggers"):
            endpoints: Sequence[str]
            if overrides.get(kind):
                endpoints = (overrides[kind],)
            else:
                endpoints = self.DEFAULT_ENDPOINTS[kind]

            found = False
            for endpoint in endpoints:
                try:
                    data = self.client.get_json(endpoint, params={"limit": 1000})
                    payloads[kind] = data
                    checks.append(EndpointCheck(
                        kind=kind,
                        endpoint=endpoint,
                        status="available",
                        detail="GET returned JSON",
                        item_count=self._count_items(data),
                    ))
                    found = True
                    break
                except ExalateClientError as exc:
                    # Candidate misses are expected; auth/permission errors are
                    # retained as evidence and the next candidate is not useful.
                    message = str(exc)
                    status = "not_found" if "404" in message or "not found" in message.lower() else "error"
                    checks.append(EndpointCheck(kind=kind, endpoint=endpoint, status=status, detail=message[:300]))
                    if status != "not_found" or overrides.get(kind):
                        break
                except Exception as exc:  # pragma: no cover - defensive boundary
                    checks.append(EndpointCheck(kind=kind, endpoint=endpoint, status="error", detail=str(exc)[:300]))
                    break

            if not found:
                payloads.setdefault(kind, None)

        return payloads, checks


def _as_list(payload: Any, keys: Iterable[str]) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # Some installations return one object for a direct endpoint.
        if any(key in payload for key in ("id", "name", "key", "jql", "query")):
            return [payload]
    return []


def _value(record: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        current: Any = record
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


def _redact(value: str) -> str:
    """Redact obvious inline secrets from evidence snippets."""
    value = re.sub(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)\s*[:=]\s*[^,;\s)]+", r"\1=***REDACTED***", value)
    value = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***REDACTED***", value)
    return value[:240]


def _evidence(script: str, match: re.Match[str]) -> str:
    start = script.rfind("\n", 0, match.start()) + 1
    end = script.find("\n", match.end())
    if end == -1:
        end = len(script)
    return _redact(script[start:end].strip())


# Ordered from highest impact to lower impact.  The matched text is retained,
# but the source line is redacted and the raw script is never exported here.
RULE_PATTERNS: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("CRITICAL", "secret", r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|private[_ ]?key)", "Credential or secret may be transferred or used in a rule.", "Remove credentials from sync rules; rotate any exposed credential and use a protected integration mechanism."),
    ("HIGH", "broad-payload", r"(?i)\b(?:replica\s*=\s*issue|replica\s*\.\s*(?:fields|customFields|allFields)|issue\s*\.\s*fields\b)", "Broad issue-field assignment can send data not approved for Jira Cloud.", "Replace broad assignment with an explicit allow-list of approved fields."),
    ("HIGH", "attachment", r"(?i)\b(?:replica\s*\.\s*attachments|attachmentHelper|mergeAttachments)\b", "Attachments can contain unrestricted files and internal documents.", "Confirm attachment classification, size, retention, and Cloud approval; otherwise exclude attachments."),
    ("HIGH", "comment-or-worklog", r"(?i)\b(?:replica\s*\.\s*(?:comments|workLogs?|changelog|changeHistory)|commentHelper|worklog)\b", "Comments, worklogs, or history may include confidential or personal data.", "Review each field and visibility rule; use an explicit allow-list and exclude internal comments."),
    ("HIGH", "security-or-customer-data", r"(?i)\b(?:securityLevel|requestParticipants|organizations?|customer|watchers?)\b", "Security scope, customer, participant, or watcher data needs a Cloud privacy review.", "Verify Cloud account mapping and visibility semantics before enabling this mapping."),
    ("MEDIUM", "personal-data", r"(?i)\b(?:reporter|assignee|creator|author|email|displayName|username|user)\b", "User identity or contact information may be transferred.", "Confirm lawful basis, account-ID mapping, minimization, and Cloud visibility."),
    ("MEDIUM", "custom-field", r"(?i)\bcustomFields?\b|customfield[_-]?\d+", "A custom field is referenced; its contents are not known from the rule alone.", "Inventory the field value/classification and verify it exists and is permitted in Cloud."),
    ("MEDIUM", "external-call", r"(?i)\b(?:httpClient\.(?:get|post|put|patch|delete)|java\.sql|groovy\.sql|ComponentAccessor)\b", "The rule calls an external API, database, or Data Center-specific API.", "Revalidate endpoint access, data egress, authentication, and Cloud compatibility."),
    ("LOW", "unclassified-replica-field", r"(?i)\breplica\s*\.\s*[A-Za-z_][A-Za-z0-9_]*", "A replica field is populated; its data classification requires review.", "Document the field and approve it explicitly for Cloud transfer."),
)


def _scan_script(
    script: str,
    direction: str,
    connection: str,
    rule_type: str,
) -> List[SecurityFinding]:
    findings: List[SecurityFinding] = []
    if not script.strip():
        findings.append(SecurityFinding(
            severity="HIGH",
            direction=direction,
            connection=connection,
            rule_type=rule_type,
            category="missing-rule",
            matched="(empty)",
            evidence="No rule text was returned",
            recommendation="Obtain the active rule version before approving the migration.",
        ))
        return findings

    seen: set[Tuple[str, str]] = set()
    for severity, category, pattern, recommendation, action in RULE_PATTERNS:
        for match in re.finditer(pattern, script):
            key = (category, match.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            findings.append(SecurityFinding(
                severity=severity,
                direction=direction,
                connection=connection,
                rule_type=rule_type,
                category=category,
                matched=match.group(0),
                evidence=_evidence(script, match),
                recommendation=action,
            ))
    return findings


def _extract_rule(record: Dict[str, Any], direction: str) -> str:
    if direction == "OUTGOING":
        keys = (
            "outgoingSync", "outgoingRule", "outgoing", "syncRules.outgoing", "rules.outgoing",
            "outgoingMappings", "outgoingFields", "mapping", "fieldMappings",
        )
    else:
        keys = (
            "incomingSync", "incomingRule", "incoming", "syncRules.incoming", "rules.incoming",
            "incomingMappings", "incomingFields", "mapping", "fieldMappings",
        )
    return _text(_value(record, *keys, default=""))


def build_database_investigation_plan() -> Dict[str, Any]:
    """Return safe, read-only SQL guidance for a DBA-assisted investigation."""
    tables = [
        "AO_08F1AF_TRIGGER",
        "AO_08F1AF_TRIGGER_CONFIG_DTO",
        "AO_08F1AF_INSTANCE",
        "AO_08F1AF_NODE",
        "AO_08F1AF_RELATION",
        "AO_08F1AF_SYNC_EVENT",
        "AO_08F1AF_SYNC_REQUEST",
        "AO_08F1AF_ERROR",
        "AO_08F1AF_REPLICA",
        "AO_08F1AF_BLOB_EVENT",
        "AO_08F1AF_BLOB_REQUEST",
    ]
    return {
        "purpose": "DBA-assisted, read-only extraction of active Exalate configuration and transfer evidence",
        "tables": tables,
        "queries": [
            "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'AO_08F1AF_%';",
            "SELECT * FROM AO_08F1AF_TRIGGER;",
            "SELECT * FROM AO_08F1AF_TRIGGER_CONFIG_DTO;",
            "SELECT * FROM AO_08F1AF_INSTANCE;",
            "SELECT * FROM AO_08F1AF_RELATION;",
            "SELECT * FROM AO_08F1AF_REPLICA;",
        ],
        "notes": [
            "Confirm the actual AO prefix and column names from the installed Exalate version before running SELECTs.",
            "Export only the required columns and redact credentials/blob contents before handing the snapshot to this tool.",
            "Do not run DROP, DELETE, UPDATE, or INSERT statements. A Jira DB backup is required before any administrative DB operation.",
            "SYNC_EVENT/SYNC_REQUEST/ERROR are operational evidence, not a complete list of synchronized issues.",
        ],
    }


def audit_configuration(
    base_url: str,
    collected_at: str,
    payloads: Dict[str, Any],
    endpoint_checks: List[EndpointCheck],
    logger: Optional[logging.Logger] = None,
) -> ConfigurationAudit:
    """Normalize discovered config and statically inspect active rules."""
    log = logger or logging.getLogger("exalate_audit")
    connections_raw = _as_list(payloads.get("connections"), ("connections", "items", "results", "data"))
    triggers_raw = _as_list(payloads.get("triggers"), ("triggers", "items", "results", "data"))
    connections: List[Dict[str, Any]] = []
    findings: List[SecurityFinding] = []

    for record in connections_raw:
        name = _text(_value(record, "name", "connectionName", "id", default="UNKNOWN"))
        normalized = {
            "id": _value(record, "id", "connectionId"),
            "name": name,
            "mode": _value(record, "mode", "configurationMode", "type"),
            "status": _value(record, "status", "state", "active"),
            "remoteInstance": _value(record, "remoteInstance", "remoteUrl", "destinationUrl", "instance.url"),
            "outgoingRulePresent": bool(_extract_rule(record, "OUTGOING").strip()),
            "incomingRulePresent": bool(_extract_rule(record, "INCOMING").strip()),
        }
        connections.append(normalized)
        for direction in ("OUTGOING", "INCOMING"):
            findings.extend(_scan_script(_extract_rule(record, direction), direction, name, f"{direction.title()} Sync Rule"))

        if not normalized["outgoingRulePresent"] and not normalized["incomingRulePresent"]:
            findings.append(SecurityFinding(
                severity="HIGH", direction="BOTH", connection=name,
                rule_type="Connection", category="rule-not-returned",
                matched="outgoing/incoming rule text unavailable",
                evidence="The returned Connection object did not include either active rule.",
                recommendation="Obtain the active Sync Rules from a supported vendor export or a redacted DBA snapshot before Cloud approval.",
            ))

    triggers: List[Dict[str, Any]] = []
    for record in triggers_raw:
        query = _text(_value(record, "jql", "query", "condition", "filter", default=""))
        name = _text(_value(record, "name", "triggerName", "id", default="UNKNOWN"))
        normalized = {
            "id": _value(record, "id", "triggerId"),
            "name": name,
            "active": _value(record, "active", "enabled", "status"),
            "entityType": _value(record, "entityType", "type"),
            "query": _redact(query),
            "connection": _value(record, "connectionName", "connection.name", "connectionId"),
        }
        triggers.append(normalized)
        if query:
            if re.search(r"(?i)(email|username|displayName|assignee|reporter|watcher)", query):
                findings.append(SecurityFinding(
                    severity="MEDIUM", direction="TRIGGER", connection=name,
                    rule_type="Trigger JQL", category="personal-data-filter",
                    matched="user-related JQL", evidence=_redact(query),
                    recommendation="Review GDPR/account-ID behavior and ensure the trigger does not select restricted issues.",
                ))
            if re.search(r"(?i)project\s*=\s*\*|labels?\s+is\s+not\s+empty|ORDER\s+BY", query):
                findings.append(SecurityFinding(
                    severity="LOW", direction="TRIGGER", connection=name,
                    rule_type="Trigger JQL", category="broad-trigger",
                    matched="broad JQL", evidence=_redact(query),
                    recommendation="Validate the resulting issue population against the Cloud migration allow-list.",
                ))

    available_kinds = {check.kind for check in endpoint_checks if check.status == "available"}
    available = {"connections", "triggers"}.issubset(available_kinds)
    snapshot_available = any(check.status == "snapshot" for check in endpoint_checks)
    rules_missing = bool(connections_raw) and any(
        not connection["outgoingRulePresent"] and not connection["incomingRulePresent"]
        for connection in connections
    )
    if not available and not snapshot_available:
        status = "not_documented_or_unavailable"
        database_required = True
        log.warning("No Exalate configuration REST endpoint returned JSON; DB-assisted extraction is required for complete review.")
    elif snapshot_available and not available:
        status = "snapshot_incomplete" if rules_missing else "snapshot"
        database_required = rules_missing
    else:
        status = "partial" if rules_missing else "available"
        database_required = rules_missing

    return ConfigurationAudit(
        base_url=base_url,
        collected_at=collected_at,
        rest_configuration_available=available,
        rest_configuration_status=status,
        endpoint_checks=endpoint_checks,
        connections=connections,
        triggers=triggers,
        findings=findings,
        database_required=database_required,
        database_investigation=build_database_investigation_plan() if database_required else {},
    )


def render_configuration_audit_html(audit: ConfigurationAudit) -> str:
    """Render a self-contained, intentionally redacted audit report."""
    data = audit.to_json()
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(item.get(key, '')))}</td>" for key in (
            "severity", "direction", "connection", "rule_type", "category", "matched", "evidence", "recommendation"
        )) + "</tr>"
        for item in data["findings"]
    )
    if not rows:
        rows = '<tr><td colspan="8">No static findings. This is not proof that the configuration is safe.</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Exalate Configuration Security Audit</title>
<style>body{{font-family:Arial,sans-serif;margin:2rem;color:#172033}}table{{border-collapse:collapse;width:100%;font-size:.9rem}}th,td{{border:1px solid #ccd3df;padding:.45rem;text-align:left;vertical-align:top}}th{{background:#e9eef5}}.critical{{color:#a40000;font-weight:bold}}pre{{white-space:pre-wrap}}</style></head>
<body><h1>Exalate Configuration Security Audit</h1>
<p>Jira: {html.escape(audit.base_url)}<br>Collected: {html.escape(audit.collected_at)}</p>
<p><strong>REST configuration:</strong> {html.escape(audit.rest_configuration_status)} &nbsp; <strong>DB follow-up required:</strong> {str(audit.database_required).lower()}</p>
<h2>Endpoint checks</h2><pre>{html.escape(str(data["endpointChecks"]))}</pre>
<h2>Static findings</h2><table><thead><tr><th>Severity</th><th>Direction</th><th>Connection</th><th>Rule</th><th>Category</th><th>Matched</th><th>Evidence</th><th>Recommendation</th></tr></thead><tbody>{rows}</tbody></table>
<h2>DB investigation plan</h2><pre>{html.escape(str(data["databaseInvestigation"]))}</pre>
</body></html>"""
