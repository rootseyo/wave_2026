"""Logging utility with sensitive data masking and exploratory schema inspector."""

import logging
import re
from typing import Any, List, Optional, Set

# Regex patterns for sensitive credentials
SENSITIVE_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{10,}", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(password['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1***REDACTED***\2"),
    (re.compile(r"(token['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1***REDACTED***\2"),
    (re.compile(r"(cookie['\"]?\s*[:=]\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1***REDACTED***\2"),
    (re.compile(r"://([^:]+):([^@]+)@"), r"://\1:***REDACTED***@"),
]

# Set of dynamic secret strings to mask
_REGISTERED_SECRETS: Set[str] = set()


def register_secret(secret: Optional[str]) -> None:
    """Register a specific secret string (e.g. password, PAT) to be masked in logs."""
    if secret and len(secret.strip()) >= 3:
        _REGISTERED_SECRETS.add(secret.strip())


def sanitize_message(message: str) -> str:
    """Sanitize sensitive credentials from a string message."""
    if not isinstance(message, str):
        return message

    sanitized = message

    # Redact registered exact secrets
    for secret in _REGISTERED_SECRETS:
        if secret in sanitized:
            sanitized = sanitized.replace(secret, "***REDACTED***")

    # Apply regex redaction patterns
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    return sanitized


class SensitiveDataFilter(logging.Filter):
    """Logging filter to redact passwords, tokens, and authorization headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_message(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize_message(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(sanitize_message(str(arg)) for arg in record.args)
        return True


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configure root logger with sensitive data redaction."""
    level = logging.DEBUG if debug else logging.INFO

    logger = logging.getLogger("exalate_audit")
    logger.setLevel(level)

    # Avoid duplicate handlers if already configured
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)

    return logger


def log_exploratory_schema(
    logger: logging.Logger,
    method: str,
    endpoint: str,
    status_code: int,
    content_type: str,
    data: Any,
) -> None:
    """
    Log defensive exploratory schema details for Exalate API response.
    
    Helps administrators identify the exact schema of their Exalate version
    without dumping entire sensitive payloads.
    """
    lines: List[str] = [
        f"\n--- Exalate API Response Schema Inspection ---",
        f"{method} {endpoint}",
        f"HTTP {status_code}",
        f"Content-Type: {content_type}",
    ]

    if isinstance(data, dict):
        root_keys = list(data.keys())
        lines.append(f"Root keys ({len(root_keys)}):")
        for k in root_keys:
            lines.append(f"  - {k}")

        # Check for results list inside dict
        results = None
        for candidate_key in ["results", "items", "events", "requests", "errors", "issues"]:
            if candidate_key in data and isinstance(data[candidate_key], list):
                results = data[candidate_key]
                lines.append(f"Found results array in key '{candidate_key}' (count: {len(results)})")
                break

        if results and len(results) > 0 and isinstance(results[0], dict):
            lines.append(f"First result item keys ({len(results[0].keys())}):")
            for k in results[0].keys():
                lines.append(f"  - {k}")
        elif results is not None and len(results) == 0:
            lines.append("Results array is empty (0 items).")

    elif isinstance(data, list):
        lines.append(f"Root is a list with {len(data)} items.")
        if len(data) > 0 and isinstance(data[0], dict):
            lines.append(f"First list item keys ({len(data[0].keys())}):")
            for k in data[0].keys():
                lines.append(f"  - {k}")
    else:
        lines.append(f"Response data type: {type(data).__name__}")

    lines.append("----------------------------------------------\n")
    logger.debug("\n".join(lines))
