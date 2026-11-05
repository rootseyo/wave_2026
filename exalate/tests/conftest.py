"""Pytest fixtures and mock objects for Exalate API tests."""

import pytest
from unittest.mock import MagicMock
import requests

from exalate.auth import BasicAuthProvider, PatAuthProvider
from exalate.client import ExalateHttpClient
from exalate.monitoring import ExalateMonitoringApi


@pytest.fixture
def mock_basic_auth():
    return BasicAuthProvider(username="test_admin", password="secret_password_123")


@pytest.fixture
def mock_pat_auth():
    return PatAuthProvider(pat_token="test_pat_token_xyz_789")


@pytest.fixture
def mock_http_client(mock_basic_auth):
    client = ExalateHttpClient(
        base_url="https://jira.example.com",
        auth_provider=mock_basic_auth,
        verify_ssl=False,
        timeout=5.0,
        max_retries=3,
        backoff_factor=0.01,  # Fast retries for testing
    )
    return client


@pytest.fixture
def mock_monitoring_api(mock_http_client):
    return ExalateMonitoringApi(
        http_client=mock_http_client,
        page_size=2,
        max_pages=10,
    )
