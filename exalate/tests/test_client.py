"""Tests for ExalateHttpClient error handling, retries, and read-only enforcement."""

import logging
from unittest.mock import MagicMock, patch
import pytest
import requests

from exalate.auth import BasicAuthProvider, PatAuthProvider, create_auth_provider
from exalate.client import (
    ExalateAuthError,
    ExalateForbiddenError,
    ExalateHttpClient,
    ExalateJsonError,
    ExalateNotFoundError,
    ExalateRateLimitError,
    ExalateServerError,
)
from util.logging import SensitiveDataFilter, register_secret, sanitize_message


def _create_mock_response(status_code: int, json_data=None, text="", headers=None):
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = text.encode("utf-8") if text else b""
    resp.headers.update(headers or {"Content-Type": "application/json"})
    if json_data is not None:
        import json
        resp._content = json.dumps(json_data).encode("utf-8")
    return resp


def test_401(mock_http_client):
    """Test HTTP 401 raises ExalateAuthError."""
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(401, text="Unauthorized"),
    ):
        with pytest.raises(ExalateAuthError) as exc_info:
            mock_http_client.get("/rest/issuehub/4.0/syncevents")
        assert "401 Unauthorized" in str(exc_info.value)


def test_403(mock_http_client):
    """Test HTTP 403 raises ExalateForbiddenError."""
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(403, text="Forbidden"),
    ):
        with pytest.raises(ExalateForbiddenError) as exc_info:
            mock_http_client.get("/rest/issuehub/4.0/syncevents")
        assert "403 Forbidden" in str(exc_info.value)


def test_404(mock_http_client):
    """Test HTTP 404 raises ExalateNotFoundError."""
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(404, text="Not Found"),
    ):
        with pytest.raises(ExalateNotFoundError) as exc_info:
            mock_http_client.get("/rest/issuehub/4.0/syncevents")
        assert "404 Not Found" in str(exc_info.value)


def test_429_retry(mock_http_client):
    """Test HTTP 429 retries with backoff and succeeds on subsequent attempt."""
    responses = [
        _create_mock_response(429, text="Too Many Requests"),
        _create_mock_response(429, text="Too Many Requests"),
        _create_mock_response(200, json_data={"total": 5}),
    ]

    with patch.object(mock_http_client._session, "get", side_effect=responses) as mock_get:
        data = mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert data == {"total": 5}
        assert mock_get.call_count == 3


def test_500_retry(mock_http_client):
    """Test HTTP 500 retries with backoff and succeeds on subsequent attempt."""
    responses = [
        _create_mock_response(500, text="Internal Server Error"),
        _create_mock_response(200, json_data={"total": 7}),
    ]

    with patch.object(mock_http_client._session, "get", side_effect=responses) as mock_get:
        data = mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert data == {"total": 7}
        assert mock_get.call_count == 2


def test_500_retry_exhausted(mock_http_client):
    """Test HTTP 500 raises ExalateServerError after exhausting retries."""
    responses = [
        _create_mock_response(500, text="Internal Server Error")
        for _ in range(mock_http_client.max_retries + 1)
    ]

    with patch.object(mock_http_client._session, "get", side_effect=responses):
        with pytest.raises(ExalateServerError) as exc_info:
            mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert "500 Server Error after" in str(exc_info.value)


def test_invalid_json(mock_http_client):
    """Test malformed JSON response raises ExalateJsonError."""
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(200, text="{broken: json content"),
    ):
        with pytest.raises(ExalateJsonError) as exc_info:
            mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert "Failed to parse JSON response" in str(exc_info.value)


def test_html_response_detection(mock_http_client):
    """Test HTML page response raises informative ExalateJsonError/ExalateAuthError."""
    # Test HTML error
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(200, text="<html><head><title>Custom Error</title></head><body>Error</body></html>"),
    ):
        with pytest.raises(ExalateJsonError) as exc_info:
            mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert "Received HTML page instead of JSON" in str(exc_info.value)
        assert "Custom Error" in str(exc_info.value)

    # Test Login redirect detection
    with patch.object(
        mock_http_client._session,
        "get",
        return_value=_create_mock_response(200, text="<html><head><title>Log in to Jira</title></head></html>"),
    ):
        with pytest.raises(ExalateAuthError) as exc_info:
            mock_http_client.get_json("/rest/issuehub/4.0/syncevents")
        assert "returned HTML Login page" in str(exc_info.value)


def test_strict_read_only_enforcement(mock_http_client):
    """Ensure HTTP mutation methods are disabled."""
    with pytest.raises(NotImplementedError):
        mock_http_client.post("/rest/issuehub/4.0/syncevents")

    with pytest.raises(NotImplementedError):
        mock_http_client.put("/rest/issuehub/4.0/syncevents")

    with pytest.raises(NotImplementedError):
        mock_http_client.patch("/rest/issuehub/4.0/syncevents")

    with pytest.raises(NotImplementedError):
        mock_http_client.delete("/rest/issuehub/4.0/syncevents")


def test_auth_providers():
    """Verify BasicAuth and PAT providers configure Session correctly."""
    # Test BasicAuth
    basic_provider = BasicAuthProvider(username="alice", password="my_secret_password")
    session1 = requests.Session()
    basic_provider.apply(session1)
    assert session1.auth is not None

    # Test PAT
    pat_provider = PatAuthProvider(pat_token="my_super_secret_pat_token_12345")
    session2 = requests.Session()
    pat_provider.apply(session2)
    assert session2.headers["Authorization"] == "Bearer my_super_secret_pat_token_12345"

    # Test Factory priority: PAT takes priority if both are given
    provider = create_auth_provider(username="alice", password="pwd", pat="my_pat")
    assert isinstance(provider, PatAuthProvider)

    provider_basic = create_auth_provider(username="alice", password="pwd")
    assert isinstance(provider_basic, BasicAuthProvider)


def test_sensitive_data_masking():
    """Verify secrets and auth headers are redacted from log messages."""
    secret_pat = "my_ultra_secret_pat_9999"
    register_secret(secret_pat)

    msg1 = f"Connecting using Bearer {secret_pat}"
    sanitized1 = sanitize_message(msg1)
    assert secret_pat not in sanitized1
    assert "***REDACTED***" in sanitized1

    msg2 = "Basic dXNlcm5hbWU6cGFzc3dvcmQ="
    sanitized2 = sanitize_message(msg2)
    assert "Basic ***REDACTED***" in sanitized2

    msg3 = "https://user:mypassword123@jira.example.com"
    sanitized3 = sanitize_message(msg3)
    assert "mypassword123" not in sanitized3
    assert "://user:***REDACTED***@" in sanitized3
