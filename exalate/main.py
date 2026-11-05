#!/usr/bin/env python3
"""
Exalate Configuration Security Audit Tool for Jira Data Center.

Reviews Exalate Connections, Triggers, and Sync Rules before Jira Cloud
migration. Queue and error monitoring remains available as an explicit scope.
Supports ../env configuration (JIRA_DC_OPS, JIRA_DC_STG).
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from exalate.auth import (
    AuthenticationError,
    BasicAuthProvider,
    PatAuthProvider,
    create_auth_provider,
)
from exalate.client import ExalateClientError, ExalateHttpClient
from exalate.collector import ExalateRestCollector
from exalate.config_audit import (
    ConfigurationAudit,
    EndpointCheck,
    ExalateConfigurationApi,
    audit_configuration,
)
from exalate.config import ExalateConfig, resolve_config
from exalate.monitoring import ExalateMonitoringApi
from report.exporter import ReportExporter
from util.logging import setup_logging


def print_console_banner(
    config: ExalateConfig,
    auth_provider,
    summary,
    outgoing_items,
    incoming_items,
    error_items,
    report_files: List[Path],
) -> None:
    """Render human-readable formatted summary report to console."""
    out_col = len(outgoing_items) if outgoing_items is not None else "N/A"
    in_col = len(incoming_items) if incoming_items is not None else "N/A"
    err_col = len(error_items) if error_items is not None else "N/A"

    print("\n" + "=" * 52)
    print("        Exalate Migration Audit (Jira DC)        ")
    print("=" * 52)
    print(f"Target Environment : {config.target_env}")
    print(f"Jira Base URL      : {config.base_url}")
    print(f"Config File        : {config.env_file_loaded or 'System Environment'}")
    print(f"Authentication     : {auth_provider.auth_type_name()}")
    print(f"SSL Verification   : {'Enabled' if config.verify_ssl else 'Disabled'}")
    print("-" * 52)
    print(f"Outgoing Queue : {summary.outgoing_total}")
    print(f"Incoming Queue : {summary.incoming_total}")
    print(f"Errors         : {summary.errors_total}")
    print("-" * 52)

    print("\nOUTGOING")
    print(f"Collected: {out_col}")

    print("\nINCOMING")
    print(f"Collected: {in_col}")

    print("\nERRORS")
    print(f"Collected: {err_col}")

    print("\n" + "-" * 52)
    print("Reports Generated:")
    for path in report_files:
        print(f"  {path}")
    print("=" * 52)
    print("\n[IMPORTANT MIGRATION NOTE]")
    print("  * Outgoing Queue != Total Outgoing Synced Issues")
    print("  * Incoming Queue != Total Incoming Synced Issues")
    print("  * These metrics reflect in-flight / pending queue transactions.")
    print("  * Synced Issue Relations / Connections require separate assessment (Phase 2).")
    print("=" * 52 + "\n")


def print_configuration_banner(audit: ConfigurationAudit, report_files: List[Path]) -> None:
    """Render the configuration-focused security audit result."""
    severity_counts = {}
    for finding in audit.findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1

    print("\n" + "=" * 64)
    print("       Exalate Configuration Security Audit (Jira DC)       ")
    print("=" * 64)
    print(f"Jira Base URL          : {audit.base_url}")
    print(f"Configuration REST     : {audit.rest_configuration_status}")
    print(f"Connections inspected  : {len(audit.connections)}")
    print(f"Triggers inspected     : {len(audit.triggers)}")
    print(f"Security findings      : {len(audit.findings)}")
    print(f"Severity summary       : {severity_counts or 'none'}")
    print(f"DB follow-up required  : {'YES' if audit.database_required else 'NO'}")
    print("-" * 64)
    print("Reports Generated:")
    for path in report_files:
        print(f"  {path}")
    print("=" * 64)
    print("[IMPORTANT]")
    print("  * This is static rule analysis; it does not inspect live issue values.")
    print("  * A DB-assisted extraction is required when Connection/Trigger rules are not returned by REST.")
    print("=" * 64 + "\n")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Jira Data Center Exalate Migration Pre-Assessment Audit Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Environment target selection
    parser.add_argument(
        "--target",
        "--env",
        dest="target",
        choices=["JIRA_DC_OPS", "JIRA_DC_STG", "OPS", "STG", "ops", "stg"],
        help="Target Jira environment from ../env: JIRA_DC_OPS (Operations) or JIRA_DC_STG (Staging).",
    )

    parser.add_argument(
        "--auth-type",
        choices=["pat", "basic", "auto"],
        default="auto",
        help="Authentication method: pat (Bearer Token), basic (Username/Password), or auto. Default: auto",
    )

    # Collection scope options
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Collect all metrics (summary, outgoing, incoming, errors). [Default]",
    )
    group.add_argument(
        "--summary",
        action="store_true",
        help="Collect queue & error count summary only (fast query with limit=0).",
    )
    group.add_argument(
        "--outgoing",
        action="store_true",
        help="Collect Outgoing Sync Event queue only.",
    )
    group.add_argument(
        "--incoming",
        action="store_true",
        help="Collect Incoming Sync Request queue only.",
    )
    group.add_argument(
        "--errors",
        action="store_true",
        help="Collect Exalate Errors only.",
    )
    group.add_argument(
        "--config-audit",
        action="store_true",
        help="Audit Exalate Connections, Triggers, and Sync Rules for Cloud security risks.",
    )

    # Overrides
    parser.add_argument(
        "--base-url",
        type=str,
        help="Jira Base URL override",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save report files (default: ./output)",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to environment file (default: auto-detects ../env, env, .env)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging (includes exploratory API response schema output)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (overrides JIRA_SSL_VERIFY)",
    )
    parser.add_argument(
        "--config-json",
        type=str,
        help="Read a DBA/vendor-provided JSON snapshot with 'connections' and 'triggers' instead of probing REST.",
    )

    return parser.parse_args()


def main() -> int:
    """Main execution flow."""
    args = parse_args()

    # Configuration audit is now the default workflow. Queue collection remains
    # available through the legacy explicit flags.
    config_audit_only = args.config_audit or not any(
        (args.all, args.summary, args.outgoing, args.incoming, args.errors)
    )

    # Determine collection scope
    if config_audit_only:
        summary_only = False
        collect_outgoing = False
        collect_incoming = False
        collect_errors = False
    elif args.summary:
        summary_only = True
        collect_outgoing = False
        collect_incoming = False
        collect_errors = False
    elif args.outgoing:
        summary_only = False
        collect_outgoing = True
        collect_incoming = False
        collect_errors = False
    elif args.incoming:
        summary_only = False
        collect_outgoing = False
        collect_incoming = True
        collect_errors = False
    elif args.errors:
        summary_only = False
        collect_outgoing = False
        collect_incoming = False
        collect_errors = True
    else:  # --all or default
        summary_only = False
        collect_outgoing = True
        collect_incoming = True
        collect_errors = True

    # Logger setup
    logger = setup_logging(debug=args.debug)

    # Resolve configuration from ../env or CLI overrides
    try:
        config = resolve_config(
            target=args.target,
            base_url_override=args.base_url,
            output_dir_override=args.output_dir,
            no_verify_ssl=args.no_verify_ssl,
            env_file_path=args.env_file,
            logger=logger,
        )
    except ValueError as e:
        logger.error(str(e))
        return 1

    try:
        if args.auth_type == "basic":
            if not config.username or not config.password:
                raise AuthenticationError(
                    f"Basic Auth selected but username or password not found for {config.target_env}."
                )
            auth_provider = BasicAuthProvider(username=config.username, password=config.password)
        elif args.auth_type == "pat":
            if not config.token:
                raise AuthenticationError(
                    f"PAT Auth selected but API token not found for {config.target_env}."
                )
            auth_provider = PatAuthProvider(pat_token=config.token)
        else:
            auth_provider = create_auth_provider(
                username=config.username,
                password=config.password,
                pat=config.token,
            )
        logger.info(f"Initialized authentication: {auth_provider.auth_type_name()}")
    except AuthenticationError as e:
        logger.error(f"Authentication configuration error: {e}")
        return 1

    # Initialize HTTP client and monitoring API
    http_client = ExalateHttpClient(
        base_url=config.base_url,
        auth_provider=auth_provider,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        max_retries=config.max_retries,
        logger=logger,
    )

    monitoring_api = ExalateMonitoringApi(
        http_client=http_client,
        page_size=config.page_size,
        max_pages=config.max_pages,
        logger=logger,
    )

    collector = ExalateRestCollector(
        monitoring_api=monitoring_api,
        base_url=config.base_url,
        logger=logger,
    )

    exporter = ReportExporter(output_dir=config.output_dir, logger=logger)

    logger.info(f"Target: {config.target_env} ({config.base_url})")
    logger.info(f"Output Directory: {config.output_dir}")

    if config_audit_only:
        try:
            endpoint_checks = []
            if args.config_json:
                snapshot_path = Path(args.config_json)
                with snapshot_path.open("r", encoding="utf-8") as snapshot_file:
                    payloads = json.load(snapshot_file)
                if not isinstance(payloads, dict):
                    raise ValueError("--config-json must contain a JSON object with connections/triggers keys.")
                endpoint_checks = [
                    # A snapshot is evidence of an input artifact, not evidence
                    # that a live REST endpoint exists.
                    EndpointCheck(
                        kind="snapshot", endpoint=str(snapshot_path), status="snapshot", detail="JSON snapshot loaded"
                    )
                ]
            else:
                configuration_api = ExalateConfigurationApi(http_client=http_client, logger=logger)
                payloads, endpoint_checks = configuration_api.discover({
                    "connections": config.exalate_connections_endpoint,
                    "triggers": config.exalate_triggers_endpoint,
                })
            audit = audit_configuration(
                base_url=config.base_url,
                collected_at=datetime.now(timezone.utc).astimezone().isoformat(),
                payloads=payloads,
                endpoint_checks=endpoint_checks,
                logger=logger,
            )
            report_files = exporter.export_configuration_audit(audit)
            print_configuration_banner(audit, report_files)
            return 0
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Configuration audit input error: {e}")
            return 1
        except ExalateClientError as e:
            logger.error(f"Exalate configuration API communication error: {e}")
            return 2
        except Exception as e:
            logger.exception(f"Unexpected configuration audit error: {e}")
            return 3

    try:
        results = collector.collect(
            collect_outgoing=collect_outgoing,
            collect_incoming=collect_incoming,
            collect_errors=collect_errors,
            summary_only=summary_only,
        )
    except ExalateClientError as e:
        logger.error(f"Exalate API communication error: {e}")
        return 2
    except Exception as e:
        logger.exception(f"Unexpected error during audit collection: {e}")
        return 3

    # Export reports
    summary = results["summary"]
    outgoing = results["outgoing"]
    incoming = results["incoming"]
    errors = results["errors"]

    report_files = exporter.export_all(
        summary=summary,
        outgoing=outgoing,
        incoming=incoming,
        errors=errors,
    )

    # Console display
    print_console_banner(
        config=config,
        auth_provider=auth_provider,
        summary=summary,
        outgoing_items=outgoing,
        incoming_items=incoming,
        error_items=errors,
        report_files=report_files,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
