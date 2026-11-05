"""
Configuration loader supporting ../env, Jira Data Center environments (OPS, STG),
and backward-compatible generic variables.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Optional, Tuple
import urllib3
from dotenv import load_dotenv


@dataclass
class ExalateConfig:
    """Consolidated configuration for Exalate audit tool."""
    target_env: str
    base_url: str
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    verify_ssl: bool = False
    output_dir: str = "./output"
    timeout: float = 30.0
    max_retries: int = 5
    page_size: int = 50
    max_pages: int = 1000
    env_file_loaded: Optional[str] = None
    exalate_connections_endpoint: Optional[str] = None
    exalate_triggers_endpoint: Optional[str] = None


def load_environment(env_file_path: Optional[str] = None) -> Optional[str]:
    """
    Search and load environment variables from env file.
    
    Priority:
    1. Explicit env_file_path if passed
    2. ../env (Standard hmc_wave root env)
    3. env
    4. .env
    5. ../.env
    """
    if env_file_path:
        p = Path(env_file_path)
        if p.exists():
            load_dotenv(dotenv_path=p, override=True)
            return str(p)
        else:
            # Still attempt to load, might be absolute or relative
            load_dotenv(dotenv_path=env_file_path, override=True)
            return env_file_path

    possible_paths = ["../env", "env", ".env", "../.env"]
    for path in possible_paths:
        p = Path(path)
        if p.exists():
            load_dotenv(dotenv_path=p, override=True)
            return str(p)

    # Fallback to system environment variables
    load_dotenv()
    return None


def normalize_target_env(target: Optional[str]) -> str:
    """Normalize target environment name (OPS, STG, JIRA_DC_OPS, etc.)."""
    if not target:
        return ""
    t = target.strip().upper()
    if t in ("OPS", "JIRA_DC_OPS", "DC_OPS"):
        return "JIRA_DC_OPS"
    if t in ("STG", "JIRA_DC_STG", "DC_STG"):
        return "JIRA_DC_STG"
    return t


def resolve_config(
    target: Optional[str] = None,
    base_url_override: Optional[str] = None,
    output_dir_override: Optional[str] = None,
    no_verify_ssl: bool = False,
    env_file_path: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> ExalateConfig:
    """
    Resolve Jira DC connection parameters based on ../env variable conventions.
    
    Supports:
    - JIRA_DC_OPS (JIRA_DC_OPS_URL, JIRA_DC_OPS_API_TOKEN, JIRA_DC_OPS_USERNAME, JIRA_DC_OPS_PASSWORD)
    - JIRA_DC_STG (JIRA_DC_STG_URL, JIRA_DC_STG_API_TOKEN, JIRA_DC_STG_USERNAME, JIRA_DC_STG_PASSWORD)
    - Generic fallbacks (JIRA_BASE_URL, JIRA_PAT, JIRA_USERNAME, JIRA_PASSWORD)
    - JIRA_SSL_VERIFY / VERIFY_SSL
    """
    log = logger or logging.getLogger("exalate_audit")
    loaded_file = load_environment(env_file_path)
    if loaded_file:
        log.debug(f"Loaded environment variables from: {loaded_file}")

    target_env = normalize_target_env(target or os.getenv("JIRA_TARGET_ENV"))

    # Auto-detect target if not specified
    if not target_env:
        if os.getenv("JIRA_DC_OPS_URL") or os.getenv("OPS_JIRA_URL"):
            target_env = "JIRA_DC_OPS"
        elif os.getenv("JIRA_DC_STG_URL") or os.getenv("STG_JIRA_URL"):
            target_env = "JIRA_DC_STG"
        else:
            target_env = "GENERIC"

    base_url: Optional[str] = None
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    if target_env == "JIRA_DC_OPS":
        base_url = (
            os.getenv("JIRA_DC_OPS_URL")
            or os.getenv("OPS_JIRA_URL")
            or os.getenv("JIRA_BASE_URL")
        )
        token = (
            os.getenv("JIRA_DC_OPS_API_TOKEN")
            or os.getenv("JIRA_DC_OPS_TOKEN")
            or os.getenv("JIRA_PAT")
        )
        username = (
            os.getenv("JIRA_DC_OPS_USERNAME")
            or os.getenv("OPS_JIRA_USERNAME")
            or os.getenv("JIRA_USERNAME")
        )
        password = (
            os.getenv("JIRA_DC_OPS_PASSWORD")
            or os.getenv("OPS_JIRA_PASSWORD")
            or os.getenv("JIRA_PASSWORD")
        )

    elif target_env == "JIRA_DC_STG":
        base_url = (
            os.getenv("JIRA_DC_STG_URL")
            or os.getenv("STG_JIRA_URL")
            or os.getenv("JIRA_BASE_URL")
        )
        token = (
            os.getenv("JIRA_DC_STG_API_TOKEN")
            or os.getenv("JIRA_DC_STG_TOKEN")
            or os.getenv("JIRA_PAT")
        )
        username = (
            os.getenv("JIRA_DC_STG_USERNAME")
            or os.getenv("STG_JIRA_USERNAME")
            or os.getenv("JIRA_USERNAME")
        )
        password = (
            os.getenv("JIRA_DC_STG_PASSWORD")
            or os.getenv("STG_JIRA_PASSWORD")
            or os.getenv("JIRA_PASSWORD")
        )

    else:  # Generic fallback
        base_url = os.getenv("JIRA_BASE_URL")
        token = os.getenv("JIRA_PAT") or os.getenv("JIRA_API_TOKEN")
        username = os.getenv("JIRA_USERNAME")
        password = os.getenv("JIRA_PASSWORD")

    # Command line override for base URL
    if base_url_override:
        base_url = base_url_override

    if not base_url:
        raise ValueError(
            f"No Jira Base URL found for target '{target_env}'. "
            f"Please check that {target_env}_URL (e.g. JIRA_DC_OPS_URL or JIRA_DC_STG_URL) or JIRA_BASE_URL "
            f"is set in ../env."
        )

    # SSL Verification resolution
    ssl_env = os.getenv("JIRA_SSL_VERIFY") or os.getenv("VERIFY_SSL")
    if no_verify_ssl:
        verify_ssl = False
    elif ssl_env is not None:
        verify_ssl = str(ssl_env).strip().lower() in ("true", "1", "yes")
    else:
        verify_ssl = False  # Consistent with ../jira_issue_counter.py to avoid SSL errors on internal DC

    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.debug("SSL verification disabled (InsecureRequestWarning suppressed).")

    output_dir = output_dir_override or os.getenv("OUTPUT_DIR", "./output")
    timeout = float(os.getenv("REQUEST_TIMEOUT", "30.0"))
    max_retries = int(os.getenv("MAX_RETRIES", "5"))
    page_size = int(os.getenv("PAGE_SIZE", "50"))
    max_pages = int(os.getenv("MAX_PAGES", "1000"))
    exalate_connections_endpoint = os.getenv("EXALATE_CONNECTIONS_ENDPOINT")
    exalate_triggers_endpoint = os.getenv("EXALATE_TRIGGERS_ENDPOINT")

    return ExalateConfig(
        target_env=target_env,
        base_url=base_url.rstrip("/"),
        token=token,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
        output_dir=output_dir,
        timeout=timeout,
        max_retries=max_retries,
        page_size=page_size,
        max_pages=max_pages,
        exalate_connections_endpoint=exalate_connections_endpoint,
        exalate_triggers_endpoint=exalate_triggers_endpoint,
        env_file_loaded=loaded_file,
    )
