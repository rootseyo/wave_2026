"""Tests for ExalateMonitoringApi methods: counts, pagination, and schema resilience."""

from unittest.mock import MagicMock, patch
import pytest

from exalate.monitoring import ExalateMonitoringApi


def test_outgoing_count(mock_monitoring_api):
    """Test fetching count of Outgoing Sync Events with limit=0."""
    mock_data = {
        "limit": 0,
        "offset": 0,
        "total": 10,
        "results": [],
    }

    with patch.object(mock_monitoring_api.client, "get_json", return_value=mock_data) as mock_get:
        count = mock_monitoring_api.get_outgoing_count()
        assert count == 10
        mock_get.assert_called_once_with("/rest/issuehub/4.0/syncevents", params={"limit": 0})


def test_incoming_count(mock_monitoring_api):
    """Test fetching count of Incoming Sync Requests with limit=0."""
    mock_data = {
        "limit": 0,
        "offset": 0,
        "total": 3,
        "results": [],
    }

    with patch.object(mock_monitoring_api.client, "get_json", return_value=mock_data) as mock_get:
        count = mock_monitoring_api.get_incoming_count()
        assert count == 3
        mock_get.assert_called_once_with("/rest/issuehub/4.0/syncrequests", params={"limit": 0})


def test_error_count(mock_monitoring_api):
    """Test fetching count of Exalate Errors with limit=0."""
    mock_data = {
        "limit": 0,
        "offset": 0,
        "total": 2,
        "results": [],
    }

    with patch.object(mock_monitoring_api.client, "get_json", return_value=mock_data) as mock_get:
        count = mock_monitoring_api.get_errors_count()
        assert count == 2
        mock_get.assert_called_once_with("/rest/issuehub/4.0/errors", params={"limit": 0})


def test_empty_response(mock_monitoring_api):
    """Test response with 0 results or empty list."""
    empty_dict = {"total": 0, "results": []}
    with patch.object(mock_monitoring_api.client, "get_json", return_value=empty_dict):
        count = mock_monitoring_api.get_outgoing_count()
        assert count == 0

        total, items = mock_monitoring_api.get_all_outgoing_events()
        assert total == 0
        assert items == []

    # Also test empty list
    with patch.object(mock_monitoring_api.client, "get_json", return_value=[]):
        count = mock_monitoring_api.get_outgoing_count()
        assert count == 0

        total, items = mock_monitoring_api.get_all_outgoing_events()
        assert total == 0
        assert items == []


def test_pagination(mock_monitoring_api):
    """Test multi-page fetching when total > page_size."""
    # Page size in fixture is 2
    page1 = {
        "total": 3,
        "limit": 2,
        "offset": 0,
        "results": [
            {"id": 1, "issueKey": "PROJ-1"},
            {"id": 2, "issueKey": "PROJ-2"},
        ],
    }
    page2 = {
        "total": 3,
        "limit": 2,
        "offset": 2,
        "results": [
            {"id": 3, "issueKey": "PROJ-3"},
        ],
    }

    with patch.object(mock_monitoring_api.client, "get_json", side_effect=[page1, page2]) as mock_get:
        total, items = mock_monitoring_api.paginate("/rest/issuehub/4.0/syncevents", page_size=2)
        assert total == 3
        assert len(items) == 3
        assert items[0]["issueKey"] == "PROJ-1"
        assert items[1]["issueKey"] == "PROJ-2"
        assert items[2]["issueKey"] == "PROJ-3"
        assert mock_get.call_count == 2


def test_pagination_max_pages_guard(mock_monitoring_api):
    """Test MAX_PAGES safety guard prevents infinite loops."""
    # Mock continuous pages
    continuous_page = {
        "limit": 2,
        "offset": 0,
        "results": [{"id": 1}, {"id": 2}],
    }

    with patch.object(mock_monitoring_api.client, "get_json", return_value=continuous_page) as mock_get:
        total, items = mock_monitoring_api.paginate(
            "/rest/issuehub/4.0/syncevents",
            page_size=2,
            max_pages=3,
        )
        assert mock_get.call_count == 3
        assert len(items) == 6


def test_schema_resilience_items_key(mock_monitoring_api):
    """Test pagination when API uses 'items' instead of 'results'."""
    page_data = {
        "total": 1,
        "limit": 50,
        "items": [{"id": 99, "issueKey": "CUSTOM-99"}],
    }
    with patch.object(mock_monitoring_api.client, "get_json", return_value=page_data):
        total, items = mock_monitoring_api.paginate("/rest/issuehub/4.0/syncrequests")
        assert total == 1
        assert len(items) == 1
        assert items[0]["issueKey"] == "CUSTOM-99"
