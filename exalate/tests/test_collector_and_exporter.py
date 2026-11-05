"""Tests for ExalateRestCollector, ExalateDatabaseCollector, and ReportExporter."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from exalate.collector import ExalateDatabaseCollector, ExalateRestCollector
from exalate.models import AuditSummary
from report.exporter import (
    ERROR_COLUMNS,
    INCOMING_COLUMNS,
    OUTGOING_COLUMNS,
    ReportExporter,
)


def test_rest_collector_summary_only(mock_monitoring_api):
    collector = ExalateRestCollector(
        monitoring_api=mock_monitoring_api,
        base_url="https://jira.example.com",
    )

    with patch.object(mock_monitoring_api, "get_outgoing_count", return_value=10), \
         patch.object(mock_monitoring_api, "get_incoming_count", return_value=3), \
         patch.object(mock_monitoring_api, "get_errors_count", return_value=2):

        results = collector.collect(summary_only=True)
        summary = results["summary"]

        assert isinstance(summary, AuditSummary)
        assert summary.outgoing_total == 10
        assert summary.incoming_total == 3
        assert summary.errors_total == 2
        assert results["outgoing"] is None
        assert results["incoming"] is None
        assert results["errors"] is None


def test_rest_collector_all(mock_monitoring_api):
    collector = ExalateRestCollector(
        monitoring_api=mock_monitoring_api,
        base_url="https://jira.example.com",
    )

    out_items = [{"id": 1, "issueKey": "PROJ-1"}]
    in_items = [{"id": 2, "issueKey": "PROJ-2"}]
    err_items = [{"id": 3, "message": "error occurred"}]

    with patch.object(mock_monitoring_api, "get_all_outgoing_events", return_value=(1, out_items)), \
         patch.object(mock_monitoring_api, "get_all_incoming_requests", return_value=(1, in_items)), \
         patch.object(mock_monitoring_api, "get_all_errors", return_value=(1, err_items)):

        results = collector.collect(
            collect_outgoing=True,
            collect_incoming=True,
            collect_errors=True,
            summary_only=False,
        )
        summary = results["summary"]

        assert summary.outgoing_total == 1
        assert summary.outgoing_collected == 1
        assert results["outgoing"] == out_items
        assert results["incoming"] == in_items
        assert results["errors"] == err_items


def test_database_collector_phase_2():
    """Verify ExalateDatabaseCollector raises NotImplementedError with informative guidance."""
    db_collector = ExalateDatabaseCollector()
    with pytest.raises(NotImplementedError) as exc_info:
        db_collector.collect()
    assert "AO_08F1AF_RELATION" in str(exc_info.value)
    assert "Phase 2" in str(exc_info.value)


def test_report_exporter(tmp_path):
    """Verify JSON and CSV export logic and schema fidelity."""
    exporter = ReportExporter(output_dir=str(tmp_path))

    summary = AuditSummary(
        base_url="https://jira.example.com",
        collected_at="2026-09-03T14:00:00+09:00",
        outgoing_total=10,
        incoming_total=3,
        errors_total=2,
        outgoing_collected=1,
        incoming_collected=1,
        errors_collected=1,
    )

    outgoing_raw = [
        {
            "id": 101,
            "issue": {"id": 5001, "key": "MIG-101", "projectKey": "MIG"},
            "connection": {"id": 1, "name": "DC-to-Cloud", "remoteInstance": "https://target.atlassian.net"},
            "status": "QUEUED",
            "created": "2026-09-03T10:00:00Z",
            "updated": "2026-09-03T10:05:00Z",
        }
    ]

    incoming_raw = [
        {
            "id": 201,
            "issueId": 5002,
            "issueKey": "MIG-201",
            "projectKey": "MIG",
            "connectionId": 1,
            "connectionName": "DC-to-Cloud",
            "remoteInstance": "https://target.atlassian.net",
            "status": "PROCESSING",
            "created": "2026-09-03T11:00:00Z",
            "updated": "2026-09-03T11:05:00Z",
        }
    ]

    errors_raw = [
        {
            "id": 301,
            "issueKey": "MIG-301",
            "connection": "DC-to-Cloud",
            "errorType": "MAPPING_ERROR",
            "message": "Field mapping failed for customfield_10001",
            "created": "2026-09-03T12:00:00Z",
            "updated": "2026-09-03T12:05:00Z",
        }
    ]

    generated_files = exporter.export_all(
        summary=summary,
        outgoing=outgoing_raw,
        incoming=incoming_raw,
        errors=errors_raw,
    )

    # 1 master HTML + 4 sets of (HTML, JSON, CSV) = 13 files
    assert len(generated_files) == 13

    # Check HTML reports exist and contain valid markup
    master_html = tmp_path / "exalate_report.html"
    summary_html = tmp_path / "exalate_summary.html"
    outgoing_html = tmp_path / "exalate_outgoing.html"
    incoming_html = tmp_path / "exalate_incoming.html"
    errors_html = tmp_path / "exalate_errors.html"

    assert master_html.exists()
    assert summary_html.exists()
    assert outgoing_html.exists()
    assert incoming_html.exists()
    assert errors_html.exists()

    assert "<!DOCTYPE html>" in master_html.read_text(encoding="utf-8")
    assert "MIG-101" in outgoing_html.read_text(encoding="utf-8")
    assert "MIG-201" in incoming_html.read_text(encoding="utf-8")
    assert "MAPPING_ERROR" in errors_html.read_text(encoding="utf-8")

    # 1. Check summary JSON
    summary_json_path = tmp_path / "exalate_summary.json"
    assert summary_json_path.exists()
    with open(summary_json_path, "r", encoding="utf-8") as f:
        s_data = json.load(f)
    assert s_data["jira"]["baseUrl"] == "https://jira.example.com"
    assert s_data["exalate"]["outgoing"]["total"] == 10
    assert s_data["exalate"]["incoming"]["total"] == 3
    assert s_data["exalate"]["errors"]["total"] == 2

    # 2. Check summary CSV
    summary_csv_path = tmp_path / "exalate_summary.csv"
    assert summary_csv_path.exists()
    df_summary = pd.read_csv(summary_csv_path)
    assert list(df_summary["type"]) == ["OUTGOING", "INCOMING", "ERROR"]
    assert list(df_summary["count"]) == [10, 3, 2]

    # 3. Check outgoing CSV
    outgoing_csv_path = tmp_path / "exalate_outgoing.csv"
    assert outgoing_csv_path.exists()
    df_out = pd.read_csv(outgoing_csv_path)
    assert list(df_out.columns) == OUTGOING_COLUMNS
    assert df_out.iloc[0]["issueKey"] == "MIG-101"
    assert df_out.iloc[0]["connectionName"] == "DC-to-Cloud"

    # 4. Check incoming CSV
    incoming_csv_path = tmp_path / "exalate_incoming.csv"
    assert incoming_csv_path.exists()
    df_in = pd.read_csv(incoming_csv_path)
    assert list(df_in.columns) == INCOMING_COLUMNS
    assert df_in.iloc[0]["issueKey"] == "MIG-201"

    # 5. Check errors CSV
    errors_csv_path = tmp_path / "exalate_errors.csv"
    assert errors_csv_path.exists()
    df_err = pd.read_csv(errors_csv_path)
    assert list(df_err.columns) == ERROR_COLUMNS
    assert df_err.iloc[0]["errorType"] == "MAPPING_ERROR"


def test_report_exporter_empty_data(tmp_path):
    """Verify CSV files with empty datasets still contain correct headers."""
    exporter = ReportExporter(output_dir=str(tmp_path))

    exporter.export_outgoing([])
    df_out = pd.read_csv(tmp_path / "exalate_outgoing.csv")
    assert list(df_out.columns) == OUTGOING_COLUMNS
    assert len(df_out) == 0

    exporter.export_incoming([])
    df_in = pd.read_csv(tmp_path / "exalate_incoming.csv")
    assert list(df_in.columns) == INCOMING_COLUMNS
    assert len(df_in) == 0

    exporter.export_errors([])
    df_err = pd.read_csv(tmp_path / "exalate_errors.csv")
    assert list(df_err.columns) == ERROR_COLUMNS
    assert len(df_err) == 0
