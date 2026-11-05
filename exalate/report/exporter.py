"""Export Exalate audit data to HTML, JSON, and CSV formats."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from exalate.models import (
    AuditSummary,
    extract_error_record,
    extract_incoming_record,
    extract_outgoing_record,
)
from exalate.config_audit import ConfigurationAudit, render_configuration_audit_html
from report.html_templates import (
    render_master_report_html,
    render_summary_html,
    render_table_html,
)

OUTGOING_COLUMNS = [
    "id", "issueId", "issueKey", "projectKey", "connectionId",
    "connectionName", "remoteInstance", "status", "created", "updated", "error"
]

INCOMING_COLUMNS = [
    "id", "issueId", "issueKey", "projectKey", "connectionId",
    "connectionName", "remoteInstance", "status", "created", "updated", "error"
]

ERROR_COLUMNS = [
    "id", "issueKey", "connection", "errorType", "message", "created", "updated"
]


class ReportExporter:
    """Exports collected audit data to HTML, JSON, and CSV files."""

    def __init__(self, output_dir: str = "./output", logger: Optional[logging.Logger] = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger("exalate_audit")

    def _write_file(self, filename: str, content: str) -> Path:
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.debug(f"Wrote file: {path}")
        return path

    def _write_json(self, filename: str, data: Any) -> Path:
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.logger.debug(f"Wrote JSON: {path}")
        return path

    def export_summary(self, summary: AuditSummary) -> List[Path]:
        """Export summary HTML, JSON, and CSV files."""
        generated: List[Path] = []

        # 1. Summary HTML
        html_path = self._write_file("exalate_summary.html", render_summary_html(summary))
        generated.append(html_path)

        # 2. Summary JSON
        json_path = self._write_json("exalate_summary.json", summary.to_summary_json())
        generated.append(json_path)

        # 3. Summary CSV
        csv_path = self.output_dir / "exalate_summary.csv"
        df = pd.DataFrame(summary.to_csv_rows())
        df.to_csv(csv_path, index=False, encoding="utf-8")
        self.logger.debug(f"Wrote CSV: {csv_path}")
        generated.append(csv_path)

        return generated

    def export_outgoing(self, items: List[Dict[str, Any]]) -> List[Path]:
        """Export Outgoing sync queue items to HTML, JSON, and CSV."""
        generated: List[Path] = []

        # Build DataFrame
        if items:
            normalized = [extract_outgoing_record(item) for item in items]
            df = pd.DataFrame(normalized)
            for col in OUTGOING_COLUMNS:
                if col not in df.columns:
                    df[col] = None
            df = df[OUTGOING_COLUMNS]
        else:
            df = pd.DataFrame(columns=OUTGOING_COLUMNS)

        # 1. Outgoing HTML
        html_content = render_table_html(
            title="Exalate Outgoing Sync Queue",
            subtitle="현재 로컬 Jira에서 원격 시스템으로 전송 대기/진행 중인 이벤트 목록",
            columns=OUTGOING_COLUMNS,
            df=df,
            summary_metric_title="Outgoing Events",
            summary_count=len(df),
        )
        html_path = self._write_file("exalate_outgoing.html", html_content)
        generated.append(html_path)

        # 2. Outgoing JSON
        json_path = self._write_json("exalate_outgoing.json", items)
        generated.append(json_path)

        # 3. Outgoing CSV
        csv_path = self.output_dir / "exalate_outgoing.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8")
        self.logger.debug(f"Wrote CSV: {csv_path}")
        generated.append(csv_path)

        return generated

    def export_incoming(self, items: List[Dict[str, Any]]) -> List[Path]:
        """Export Incoming sync queue items to HTML, JSON, and CSV."""
        generated: List[Path] = []

        if items:
            normalized = [extract_incoming_record(item) for item in items]
            df = pd.DataFrame(normalized)
            for col in INCOMING_COLUMNS:
                if col not in df.columns:
                    df[col] = None
            df = df[INCOMING_COLUMNS]
        else:
            df = pd.DataFrame(columns=INCOMING_COLUMNS)

        # 1. Incoming HTML
        html_content = render_table_html(
            title="Exalate Incoming Sync Queue",
            subtitle="원격 시스템에서 수신되어 로컬 Jira에 반영 대기/진행 중인 요청 목록",
            columns=INCOMING_COLUMNS,
            df=df,
            summary_metric_title="Incoming Requests",
            summary_count=len(df),
        )
        html_path = self._write_file("exalate_incoming.html", html_content)
        generated.append(html_path)

        # 2. Incoming JSON
        json_path = self._write_json("exalate_incoming.json", items)
        generated.append(json_path)

        # 3. Incoming CSV
        csv_path = self.output_dir / "exalate_incoming.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8")
        self.logger.debug(f"Wrote CSV: {csv_path}")
        generated.append(csv_path)

        return generated

    def export_errors(self, items: List[Dict[str, Any]]) -> List[Path]:
        """Export Exalate errors to HTML, JSON, and CSV."""
        generated: List[Path] = []

        if items:
            normalized = [extract_error_record(item) for item in items]
            df = pd.DataFrame(normalized)
            for col in ERROR_COLUMNS:
                if col not in df.columns:
                    df[col] = None
            df = df[ERROR_COLUMNS]
        else:
            df = pd.DataFrame(columns=ERROR_COLUMNS)

        # 1. Errors HTML
        html_content = render_table_html(
            title="Exalate Sync Errors",
            subtitle="마이그레이션 전 해결 및 사전 조치해야 할 Exalate 동기화 오류 목록",
            columns=ERROR_COLUMNS,
            df=df,
            summary_metric_title="Exalate Errors",
            summary_count=len(df),
        )
        html_path = self._write_file("exalate_errors.html", html_content)
        generated.append(html_path)

        # 2. Errors JSON
        json_path = self._write_json("exalate_errors.json", items)
        generated.append(json_path)

        # 3. Errors CSV
        csv_path = self.output_dir / "exalate_errors.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8")
        self.logger.debug(f"Wrote CSV: {csv_path}")
        generated.append(csv_path)

        return generated

    def export_configuration_audit(self, audit: ConfigurationAudit) -> List[Path]:
        """Export configuration security findings and DB follow-up plan."""
        generated: List[Path] = []
        generated.append(self._write_file(
            "exalate_configuration_audit.html",
            render_configuration_audit_html(audit),
        ))
        generated.append(self._write_json("exalate_configuration_audit.json", audit.to_json()))

        finding_rows = [
            {
                "severity": finding.severity,
                "direction": finding.direction,
                "connection": finding.connection,
                "ruleType": finding.rule_type,
                "category": finding.category,
                "matched": finding.matched,
                "evidence": finding.evidence,
                "recommendation": finding.recommendation,
            }
            for finding in audit.findings
        ]
        csv_path = self.output_dir / "exalate_configuration_findings.csv"
        pd.DataFrame(finding_rows, columns=[
            "severity", "direction", "connection", "ruleType", "category",
            "matched", "evidence", "recommendation",
        ]).to_csv(csv_path, index=False, encoding="utf-8")
        self.logger.debug(f"Wrote CSV: {csv_path}")
        generated.append(csv_path)
        return generated

    def export_all(
        self,
        summary: AuditSummary,
        outgoing: Optional[List[Dict[str, Any]]] = None,
        incoming: Optional[List[Dict[str, Any]]] = None,
        errors: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Path]:
        """Export summary, detail items, and master unified HTML dashboard."""
        all_paths: List[Path] = []

        # Export individual reports
        all_paths.extend(self.export_summary(summary))

        out_df = None
        if outgoing is not None:
            all_paths.extend(self.export_outgoing(outgoing))
            normalized_out = [extract_outgoing_record(item) for item in outgoing]
            out_df = pd.DataFrame(normalized_out) if normalized_out else pd.DataFrame(columns=OUTGOING_COLUMNS)

        in_df = None
        if incoming is not None:
            all_paths.extend(self.export_incoming(incoming))
            normalized_in = [extract_incoming_record(item) for item in incoming]
            in_df = pd.DataFrame(normalized_in) if normalized_in else pd.DataFrame(columns=INCOMING_COLUMNS)

        err_df = None
        if errors is not None:
            all_paths.extend(self.export_errors(errors))
            normalized_err = [extract_error_record(item) for item in errors]
            err_df = pd.DataFrame(normalized_err) if normalized_err else pd.DataFrame(columns=ERROR_COLUMNS)

        # Master Unified HTML Dashboard (exalate_report.html)
        master_html = render_master_report_html(
            summary=summary,
            outgoing_df=out_df,
            incoming_df=in_df,
            errors_df=err_df,
        )
        master_path = self._write_file("exalate_report.html", master_html)
        all_paths.insert(0, master_path)

        return all_paths
