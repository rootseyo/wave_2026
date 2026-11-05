"""Collector architecture for Exalate audit data.

Configuration security review is implemented in :mod:`exalate.config_audit`.
The database collector remains intentionally DBA-assisted because Jira's DB is
an implementation detail and this tool must not run arbitrary SQL against a
production database.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from exalate.models import AuditSummary
from exalate.monitoring import ExalateMonitoringApi


class AuditCollector(ABC):
    """Abstract base collector for audit sources (REST, Database, etc.)."""

    @abstractmethod
    def collect(self, **kwargs) -> Dict[str, Any]:
        """Execute audit collection and return structured results."""
        raise NotImplementedError


class ExalateRestCollector(AuditCollector):
    """Collector implementing REST API monitoring data gathering."""

    def __init__(
        self,
        monitoring_api: ExalateMonitoringApi,
        base_url: str,
        logger: Optional[logging.Logger] = None,
    ):
        self.api = monitoring_api
        self.base_url = base_url
        self.logger = logger or logging.getLogger("exalate_audit")

    def collect_summary(self) -> AuditSummary:
        """Collect count-only summary using limit=0 endpoints."""
        self.logger.info("Collecting Exalate queue & error summary counts...")
        now = datetime.now(timezone.utc).astimezone().isoformat()

        outgoing_total = self.api.get_outgoing_count()
        incoming_total = self.api.get_incoming_count()
        errors_total = self.api.get_errors_count()

        return AuditSummary(
            base_url=self.base_url,
            collected_at=now,
            outgoing_total=outgoing_total,
            incoming_total=incoming_total,
            errors_total=errors_total,
        )

    def collect(
        self,
        collect_outgoing: bool = True,
        collect_incoming: bool = True,
        collect_errors: bool = True,
        summary_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute collection based on requested flags.
        
        Returns:
            Dict containing 'summary', 'outgoing', 'incoming', 'errors', 'metadata'.
        """
        now = datetime.now(timezone.utc).astimezone().isoformat()

        if summary_only:
            summary = self.collect_summary()
            return {
                "summary": summary,
                "outgoing": None,
                "incoming": None,
                "errors": None,
                "collected_at": now,
            }

        outgoing_items: Optional[List[Dict[str, Any]]] = None
        incoming_items: Optional[List[Dict[str, Any]]] = None
        error_items: Optional[List[Dict[str, Any]]] = None

        outgoing_total = 0
        incoming_total = 0
        errors_total = 0

        # Outgoing queue
        if collect_outgoing:
            self.logger.info("--- Collecting Outgoing Sync Queue ---")
            outgoing_total, outgoing_items = self.api.get_all_outgoing_events()
        else:
            outgoing_total = self.api.get_outgoing_count()

        # Incoming queue
        if collect_incoming:
            self.logger.info("--- Collecting Incoming Sync Queue ---")
            incoming_total, incoming_items = self.api.get_all_incoming_requests()
        else:
            incoming_total = self.api.get_incoming_count()

        # Errors
        if collect_errors:
            self.logger.info("--- Collecting Exalate Errors ---")
            errors_total, error_items = self.api.get_all_errors()
        else:
            errors_total = self.api.get_errors_count()

        summary = AuditSummary(
            base_url=self.base_url,
            collected_at=now,
            outgoing_total=outgoing_total,
            incoming_total=incoming_total,
            errors_total=errors_total,
            outgoing_collected=len(outgoing_items) if outgoing_items is not None else 0,
            incoming_collected=len(incoming_items) if incoming_items is not None else 0,
            errors_collected=len(error_items) if error_items is not None else 0,
        )

        return {
            "summary": summary,
            "outgoing": outgoing_items,
            "incoming": incoming_items,
            "errors": error_items,
            "collected_at": now,
        }


class ExalateDatabaseCollector(AuditCollector):
    """
    Phase 2 Architecture Placeholder: Read-only Jira ActiveObjects DB Collector.
    
    Planned Tables for Migration Assessment:
    - AO_08F1AF_RELATION: Issue synchronization relations (Local Issue <-> Remote Issue mapping)
    - AO_08F1AF_SYNC_EVENT: Outgoing transaction queue records
    - AO_08F1AF_SYNC_REQUEST: Incoming transaction queue records
    - AO_08F1AF_ERROR: Sync error records and stack traces
    - AO_08F1AF_TRIGGER: Sync trigger rules (JQL triggers)
    - AO_08F1AF_TRIGGER_CONFIG_DTO: Trigger configuration details
    - AO_08F1AF_INSTANCE: Remote Exalate instances
    - AO_08F1AF_NODE_INFO: Jira cluster node information
    - AO_08F1AF_TRACE: Detailed sync execution traces
    - AO_08F1AF_TWIN_TRACE: Twin mapping traces
    """

    def __init__(self, db_connection_url: Optional[str] = None):
        self.db_connection_url = db_connection_url

    @staticmethod
    def investigation_plan() -> Dict[str, Any]:
        """Return the safe read-only plan used when REST config is unavailable."""
        from exalate.config_audit import build_database_investigation_plan

        return build_database_investigation_plan()

    def collect(self, **kwargs) -> Dict[str, Any]:
        """Raise NotImplementedError to ensure explicit Phase 2 configuration."""
        raise NotImplementedError(
            "Exalate configuration REST is not a documented stable interface for this deployment (Phase 2 DB follow-up). "
            "Use ExalateDatabaseCollector.investigation_plan() and have a DBA provide a redacted, "
            "read-only snapshot of AO_08F1AF_* tables (including AO_08F1AF_RELATION); "
            "this tool does not execute SQL against Jira DB."
        )
