"""Exalate Monitoring API client for Sync Events, Sync Requests, and Errors."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from exalate.client import ExalateHttpClient


class ExalateMonitoringApi:
    """
    Client for Exalate IssueHub 4.0 Monitoring REST APIs.
    
    Endpoints:
    - Outgoing: /rest/issuehub/4.0/syncevents
    - Incoming: /rest/issuehub/4.0/syncrequests
    - Errors:   /rest/issuehub/4.0/errors
    """

    SYNCEVENTS_ENDPOINT = "/rest/issuehub/4.0/syncevents"
    SYNCREQUESTS_ENDPOINT = "/rest/issuehub/4.0/syncrequests"
    ERRORS_ENDPOINT = "/rest/issuehub/4.0/errors"

    def __init__(
        self,
        http_client: ExalateHttpClient,
        page_size: int = 50,
        max_pages: int = 1000,
        logger: Optional[logging.Logger] = None,
    ):
        self.client = http_client
        self.page_size = page_size
        self.max_pages = max_pages
        self.logger = logger or logging.getLogger("exalate_audit")

    def _extract_count_from_response(self, data: Any) -> int:
        """Defensively extract total count from count response (e.g. limit=0)."""
        if isinstance(data, dict):
            for count_key in ["total", "count", "size", "totalCount"]:
                if count_key in data:
                    try:
                        return int(data[count_key])
                    except (ValueError, TypeError):
                        pass

            # If results array is present
            for candidate_key in ["results", "items", "events", "requests", "errors"]:
                if candidate_key in data and isinstance(data[candidate_key], list):
                    return len(data[candidate_key])

        elif isinstance(data, list):
            return len(data)

        return 0

    def _extract_items_from_response(self, data: Any) -> List[Dict[str, Any]]:
        """Defensively extract list of items from a paginated API response."""
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]

        if isinstance(data, dict):
            for candidate_key in ["results", "items", "events", "requests", "errors", "issues"]:
                if candidate_key in data and isinstance(data[candidate_key], list):
                    return [item for item in data[candidate_key] if isinstance(item, dict)]

            # Check if any value is a list of dicts
            for v in data.values():
                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                    return v

        return []

    def get_count(self, endpoint: str) -> int:
        """Fetch total item count using limit=0."""
        self.logger.debug(f"Fetching count for {endpoint} with limit=0")
        data = self.client.get_json(endpoint, params={"limit": 0})
        count = self._extract_count_from_response(data)
        self.logger.debug(f"Count for {endpoint}: {count}")
        return count

    def get_outgoing_count(self) -> int:
        """Get count of Outgoing Sync Events in Queue."""
        return self.get_count(self.SYNCEVENTS_ENDPOINT)

    def get_incoming_count(self) -> int:
        """Get count of Incoming Sync Requests in Queue."""
        return self.get_count(self.SYNCREQUESTS_ENDPOINT)

    def get_errors_count(self) -> int:
        """Get count of Exalate Errors."""
        return self.get_count(self.ERRORS_ENDPOINT)

    def paginate(
        self,
        endpoint: str,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Fetch all records from an endpoint with pagination and infinite-loop guards.
        
        Returns:
            Tuple[int, List[dict]]: (total_reported, collected_items)
        """
        size = page_size or self.page_size
        max_p = max_pages or self.max_pages

        all_items: List[Dict[str, Any]] = []
        offset = 0
        page = 0
        reported_total: Optional[int] = None

        self.logger.info(f"Starting pagination for {endpoint} (page_size={size}, max_pages={max_p})")

        while page < max_p:
            page += 1
            params = {"limit": size, "offset": offset}
            data = self.client.get_json(endpoint, params=params)

            # Check for reported total
            if reported_total is None and isinstance(data, dict):
                for count_key in ["total", "count", "size", "totalCount"]:
                    if count_key in data:
                        try:
                            reported_total = int(data[count_key])
                            break
                        except (ValueError, TypeError):
                            pass

            items = self._extract_items_from_response(data)
            num_items = len(items)
            all_items.extend(items)

            total_str = f"/{reported_total}" if reported_total is not None else ""
            self.logger.info(
                f"[{endpoint}] Page {page}: fetched {num_items} items "
                f"(collected {len(all_items)}{total_str})"
            )

            # Stop conditions
            if num_items == 0:
                self.logger.debug(f"[{endpoint}] Empty page received. Stopping pagination.")
                break

            if reported_total is not None and len(all_items) >= reported_total:
                self.logger.debug(
                    f"[{endpoint}] Reached reported total ({len(all_items)} >= {reported_total}). Stopping."
                )
                break

            if num_items < size:
                self.logger.debug(
                    f"[{endpoint}] Received {num_items} items (< page_size {size}). Last page reached."
                )
                break

            offset += num_items

        if page >= max_p and (reported_total is None or len(all_items) < reported_total):
            self.logger.warning(
                f"[{endpoint}] Reached MAX_PAGES safety guard ({max_p}). "
                f"Halting pagination with {len(all_items)} items collected."
            )

        final_total = reported_total if reported_total is not None else len(all_items)
        return final_total, all_items

    def get_all_outgoing_events(
        self,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch all Outgoing Sync Events (Queue)."""
        return self.paginate(self.SYNCEVENTS_ENDPOINT, page_size=page_size, max_pages=max_pages)

    def get_all_incoming_requests(
        self,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch all Incoming Sync Requests (Queue)."""
        return self.paginate(self.SYNCREQUESTS_ENDPOINT, page_size=page_size, max_pages=max_pages)

    def get_all_errors(
        self,
        page_size: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch all Exalate Errors."""
        return self.paginate(self.ERRORS_ENDPOINT, page_size=page_size, max_pages=max_pages)
