"""Tests for configuration discovery and static security review."""

from unittest.mock import patch

from exalate.config_audit import (
    EndpointCheck,
    ExalateConfigurationApi,
    audit_configuration,
)


def test_configuration_audit_identifies_outgoing_risk_and_trigger():
    audit = audit_configuration(
        base_url="https://jira.example.com",
        collected_at="2026-09-04T00:00:00Z",
        payloads={
            "connections": [{
                "id": 10,
                "name": "DC-to-Cloud",
                "outgoingSync": (
                    'replica.comments = issue.comments\n'
                    'replica.attachments = issue.attachments\n'
                    'replica.customFields = issue.fields'
                ),
                "incomingSync": "issue.summary = replica.summary",
            }],
            "triggers": [{"name": "production", "jql": "project = SEC AND reporter is not EMPTY"}],
        },
        endpoint_checks=[
            EndpointCheck("connections", "/connections", "available"),
            EndpointCheck("triggers", "/triggers", "available"),
        ],
    )

    categories = {finding.category for finding in audit.findings}
    assert audit.rest_configuration_available is True
    assert audit.database_required is False
    assert {"comment-or-worklog", "attachment", "broad-payload", "personal-data-filter"} <= categories


def test_configuration_audit_requires_db_when_rest_is_unavailable():
    audit = audit_configuration(
        base_url="https://jira.example.com",
        collected_at="2026-09-04T00:00:00Z",
        payloads={"connections": None, "triggers": None},
        endpoint_checks=[EndpointCheck("connections", "/connections", "not_found")],
    )

    assert audit.rest_configuration_status == "not_documented_or_unavailable"
    assert audit.database_required is True
    assert "AO_08F1AF_TRIGGER" in audit.database_investigation["tables"]


def test_configuration_endpoint_discovery_uses_get_json():
    client = type("Client", (), {})()
    client.get_json = lambda endpoint, params=None: {"items": [{"id": 1}]} if endpoint == "/connections" else (_ for _ in ()).throw(Exception("unexpected"))
    api = ExalateConfigurationApi(client)

    with patch.object(client, "get_json", return_value={"items": [{"id": 1}]}) as get_json:
        payloads, checks = api.discover({"connections": "/connections", "triggers": "/triggers"})

    assert payloads["connections"]["items"][0]["id"] == 1
    assert checks[0].status == "available"
    assert get_json.call_count == 2
    get_json.assert_any_call("/connections", params={"limit": 1000})
