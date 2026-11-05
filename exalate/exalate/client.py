"""Strict read-only HTTP client for Exalate REST APIs with retries, backoff, and schema logging."""

import logging
import time
from typing import Any, Dict, Optional, Set, Union
from urllib.parse import urljoin

import requests
from requests.exceptions import ConnectionError, JSONDecodeError, SSLError, Timeout

from exalate.auth import AuthProvider
from util.logging import log_exploratory_schema


class ExalateClientError(Exception):
    """Base exception for Exalate client errors."""
    pass


class ExalateAuthError(ExalateClientError):
    """Raised on HTTP 401 Unauthorized."""
    pass


class ExalateForbiddenError(ExalateClientError):
    """Raised on HTTP 403 Forbidden."""
    pass


class ExalateNotFoundError(ExalateClientError):
    """Raised on HTTP 404 Not Found."""
    pass


class ExalateRateLimitError(ExalateClientError):
    """Raised on HTTP 429 Too Many Requests when retries exhausted."""
    pass


class ExalateServerError(ExalateClientError):
    """Raised on HTTP 5xx Server Error when retries exhausted."""
    pass


class ExalateJsonError(ExalateClientError):
    """Raised when response body cannot be parsed as JSON."""
    pass


class ExalateConnectionError(ExalateClientError):
    """Raised on network or SSL connection errors."""
    pass


class ExalateTimeoutError(ExalateClientError):
    """Raised when an HTTP request times out."""
    pass


def parse_verify_ssl(val: Any) -> Union[bool, str]:
    """Parse SSL verification parameter (bool, string boolean, or CA bundle path)."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip()
        if v.lower() in ("true", "1", "yes", "on"):
            return True
        if v.lower() in ("false", "0", "no", "off"):
            return False
        return v  # Custom CA bundle file path
    return True


class ExalateHttpClient:
    """
    Read-only HTTP client for Exalate REST APIs.
    
    Enforces strict GET-only operations. Any mutation methods (POST, PUT, DELETE)
    are strictly forbidden.
    """

    DEFAULT_RETRY_STATUSES = (429, 500, 502, 503, 504)

    def __init__(
        self,
        base_url: str,
        auth_provider: AuthProvider,
        verify_ssl: Union[bool, str] = True,
        timeout: float = 30.0,
        max_retries: int = 5,
        backoff_factor: float = 1.0,
        logger: Optional[logging.Logger] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_provider = auth_provider
        self.verify_ssl = parse_verify_ssl(verify_ssl)
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.logger = logger or logging.getLogger("exalate_audit")

        self._inspected_endpoints: Set[str] = set()

        # Initialize session
        self._session = requests.Session()
        self.auth_provider.apply(self._session)
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Exalate-Migration-Audit/1.0",
        })

    def _build_url(self, endpoint: str) -> str:
        """Construct full URL from base URL and endpoint."""
        endpoint = "/" + endpoint.lstrip("/")
        return urljoin(self.base_url, endpoint)

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """
        Execute an HTTP GET request with retry and backoff logic.
        
        Only HTTP GET is supported to guarantee production read-only safety.
        """
        url = self._build_url(endpoint)
        attempt = 0

        while True:
            attempt += 1
            try:
                self.logger.debug(f"HTTP GET {url} params={params} (attempt {attempt}/{self.max_retries})")
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    verify=self.verify_ssl,
                )

                # Check if retryable HTTP status
                if response.status_code in self.DEFAULT_RETRY_STATUSES:
                    if attempt <= self.max_retries:
                        delay = self.backoff_factor * (2 ** (attempt - 1))
                        self.logger.warning(
                            f"HTTP {response.status_code} received from {endpoint}. "
                            f"Retry {attempt}/{self.max_retries} after {delay:.1f} sec"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        if response.status_code == 429:
                            raise ExalateRateLimitError(
                                f"HTTP 429 Rate Limit exceeded after {self.max_retries} retries for {endpoint}."
                            )
                        else:
                            raise ExalateServerError(
                                f"HTTP {response.status_code} Server Error after {self.max_retries} retries for {endpoint}: "
                                f"{response.text[:200]}"
                            )

                # Non-retryable errors
                if response.status_code == 401:
                    raise ExalateAuthError(
                        f"HTTP 401 Unauthorized for {endpoint}. "
                        "Authentication failed. Please verify JIRA_USERNAME / JIRA_PASSWORD or JIRA_PAT."
                    )
                if response.status_code == 403:
                    raise ExalateForbiddenError(
                        f"HTTP 403 Forbidden for {endpoint}. "
                        "Access denied. Please ensure the user has Jira Administrator and Exalate Admin permissions."
                    )
                if response.status_code == 404:
                    raise ExalateNotFoundError(
                        f"HTTP 404 Not Found for {endpoint}. "
                        "Exalate endpoint was not found. Please verify Exalate app is installed and active on this instance."
                    )

                response.raise_for_status()
                return response

            except SSLError as e:
                self.logger.error(f"SSL verification failed for {url}: {e}")
                raise ExalateConnectionError(
                    f"SSL certificate verification failed for {url}. "
                    "Set VERIFY_SSL=false in .env or provide a valid CA bundle if using self-signed certificates."
                ) from e

            except ConnectionError as e:
                if attempt <= self.max_retries:
                    delay = self.backoff_factor * (2 ** (attempt - 1))
                    self.logger.warning(
                        f"Connection failed for {endpoint}. Retry {attempt}/{self.max_retries} after {delay:.1f} sec: {e}"
                    )
                    time.sleep(delay)
                    continue
                raise ExalateConnectionError(
                    f"Connection failed after {self.max_retries} retries connecting to {url}: {e}"
                ) from e

            except Timeout as e:
                if attempt <= self.max_retries:
                    delay = self.backoff_factor * (2 ** (attempt - 1))
                    self.logger.warning(
                        f"Request timeout for {endpoint}. Retry {attempt}/{self.max_retries} after {delay:.1f} sec: {e}"
                    )
                    time.sleep(delay)
                    continue
                raise ExalateTimeoutError(
                    f"Request timed out after {self.max_retries} retries for {url}."
                ) from e

    def get_json(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Execute GET and parse JSON, logging exploratory schema on first call."""
        response = self.get(endpoint, params=params)

        content_type = response.headers.get("Content-Type", "")
        raw_text = response.text.strip()

        # Check if server returned HTML instead of JSON
        if "html" in content_type.lower() or raw_text.startswith("<!DOCTYPE") or raw_text.startswith("<html"):
            import re
            title_match = re.search(r"<title>(.*?)</title>", raw_text, re.IGNORECASE | re.DOTALL)
            page_title = title_match.group(1).strip() if title_match else "Unknown Page"

            if "log in" in page_title.lower() or "login" in page_title.lower():
                raise ExalateAuthError(
                    f"HTTP {response.status_code} returned HTML Login page ('{page_title}') for {endpoint}. "
                    "The authentication credentials/token were not recognized by Jira. "
                    "Please verify token permissions or try Basic Authentication (--auth-type basic)."
                )

            if response.status_code == 404 or "dead link" in page_title.lower() or "not found" in page_title.lower():
                raise ExalateNotFoundError(
                    f"Exalate endpoint '{endpoint}' not found (HTTP {response.status_code}, Title: '{page_title}'). "
                    f"Exalate for Jira Data Center does not appear to be installed or active on {self.base_url}."
                )

            snippet = raw_text[:200].replace("\n", " ")
            raise ExalateJsonError(
                f"Received HTML page instead of JSON from {endpoint} (HTTP {response.status_code}, "
                f"Title: '{page_title}'). Snippet: {snippet}"
            )

        try:
            data = response.json()
        except (ValueError, JSONDecodeError) as e:
            snippet = response.text[:200].replace("\n", " ")
            raise ExalateJsonError(
                f"Failed to parse JSON response from {endpoint} (HTTP {response.status_code}, "
                f"Content-Type: {content_type}). Response snippet: {snippet}"
            ) from e

        # Log exploratory schema details once per endpoint
        if endpoint not in self._inspected_endpoints:
            self._inspected_endpoints.add(endpoint)
            log_exploratory_schema(
                logger=self.logger,
                method="GET",
                endpoint=endpoint,
                status_code=response.status_code,
                content_type=content_type,
                data=data,
            )

        return data

    def post(self, *args, **kwargs):
        """Disallowed to ensure read-only operation."""
        raise NotImplementedError("HTTP POST is strictly disabled for Exalate read-only audit tool.")

    def put(self, *args, **kwargs):
        """Disallowed to ensure read-only operation."""
        raise NotImplementedError("HTTP PUT is strictly disabled for Exalate read-only audit tool.")

    def patch(self, *args, **kwargs):
        """Disallowed to ensure read-only operation."""
        raise NotImplementedError("HTTP PATCH is strictly disabled for Exalate read-only audit tool.")

    def delete(self, *args, **kwargs):
        """Disallowed to ensure read-only operation."""
        raise NotImplementedError("HTTP DELETE is strictly disabled for Exalate read-only audit tool.")
