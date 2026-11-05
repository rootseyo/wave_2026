"""Data models and defensive field extractors for Exalate monitoring responses."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AuditSummary:
    """Summary of Exalate queues and error counts."""
    base_url: str
    collected_at: str
    outgoing_total: int = 0
    incoming_total: int = 0
    errors_total: int = 0
    outgoing_collected: int = 0
    incoming_collected: int = 0
    errors_collected: int = 0

    def to_summary_json(self) -> Dict[str, Any]:
        """Convert to user-specified JSON schema format."""
        return {
            "jira": {
                "baseUrl": self.base_url
            },
            "collectedAt": self.collected_at,
            "exalate": {
                "outgoing": {
                    "total": self.outgoing_total,
                    "collected": self.outgoing_collected
                },
                "incoming": {
                    "total": self.incoming_total,
                    "collected": self.incoming_collected
                },
                "errors": {
                    "total": self.errors_total,
                    "collected": self.errors_collected
                }
            }
        }

    def to_csv_rows(self) -> List[Dict[str, Any]]:
        """Convert to user-specified CSV format."""
        return [
            {"type": "OUTGOING", "count": self.outgoing_total},
            {"type": "INCOMING", "count": self.incoming_total},
            {"type": "ERROR", "count": self.errors_total},
        ]


def _get_nested(data: Dict[str, Any], *paths: str, default: Any = None) -> Any:
    """Safely traverse multiple fallback key paths in nested dict."""
    for path in paths:
        keys = path.split(".")
        curr = data
        found = True
        for k in keys:
            if isinstance(curr, dict) and k in curr:
                curr = curr[k]
            else:
                found = False
                break
        if found and curr is not None:
            return curr
    return default


def extract_outgoing_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract normalized fields from an Outgoing Sync Event defensively.
    
    Fields:
    id, issueId, issueKey, projectKey, connectionId, connectionName,
    remoteInstance, status, created, updated, error
    """
    if not isinstance(raw, dict):
        return {"raw": str(raw)}

    return {
        "id": _get_nested(raw, "id", "eventId", "syncEventId"),
        "issueId": _get_nested(raw, "issueId", "issue.id", "localIssue.id"),
        "issueKey": _get_nested(raw, "issueKey", "issue.key", "localIssue.key"),
        "projectKey": _get_nested(raw, "projectKey", "issue.projectKey", "localIssue.projectKey"),
        "connectionId": _get_nested(raw, "connectionId", "connection.id"),
        "connectionName": _get_nested(raw, "connectionName", "connection.name"),
        "remoteInstance": _get_nested(raw, "remoteInstance", "connection.remoteInstance", "connection.remoteUrl"),
        "status": _get_nested(raw, "status", "state", "eventStatus"),
        "created": _get_nested(raw, "created", "createdAt", "creationDate"),
        "updated": _get_nested(raw, "updated", "updatedAt", "modificationDate"),
        "error": _get_nested(raw, "error", "errorMessage", "error.message", "failureReason"),
    }


def extract_incoming_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract normalized fields from an Incoming Sync Request defensively.
    
    Fields:
    id, issueId, issueKey, projectKey, connectionId, connectionName,
    remoteInstance, status, created, updated, error
    """
    if not isinstance(raw, dict):
        return {"raw": str(raw)}

    return {
        "id": _get_nested(raw, "id", "requestId", "syncRequestId"),
        "issueId": _get_nested(raw, "issueId", "issue.id", "localIssue.id"),
        "issueKey": _get_nested(raw, "issueKey", "issue.key", "localIssue.key"),
        "projectKey": _get_nested(raw, "projectKey", "issue.projectKey", "localIssue.projectKey"),
        "connectionId": _get_nested(raw, "connectionId", "connection.id"),
        "connectionName": _get_nested(raw, "connectionName", "connection.name"),
        "remoteInstance": _get_nested(raw, "remoteInstance", "connection.remoteInstance", "connection.remoteUrl"),
        "status": _get_nested(raw, "status", "state", "requestStatus"),
        "created": _get_nested(raw, "created", "createdAt", "creationDate"),
        "updated": _get_nested(raw, "updated", "updatedAt", "modificationDate"),
        "error": _get_nested(raw, "error", "errorMessage", "error.message", "failureReason"),
    }


def extract_error_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract normalized fields from an Exalate Error record defensively.
    
    Fields:
    id, issueKey, connection, errorType, message, created, updated
    """
    if not isinstance(raw, dict):
        return {"raw": str(raw)}

    return {
        "id": _get_nested(raw, "id", "errorId"),
        "issueKey": _get_nested(raw, "issueKey", "issue.key", "localIssue.key", "entityKey"),
        "connection": _get_nested(raw, "connection", "connectionName", "connection.name"),
        "errorType": _get_nested(raw, "errorType", "type", "category"),
        "message": _get_nested(raw, "message", "errorMessage", "description"),
        "created": _get_nested(raw, "created", "createdAt", "creationDate"),
        "updated": _get_nested(raw, "updated", "updatedAt", "modificationDate"),
    }
