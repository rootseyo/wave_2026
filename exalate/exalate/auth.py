"""Authentication providers for Jira Data Center & Exalate API."""

from abc import ABC, abstractmethod
from typing import Optional
import requests
from requests.auth import HTTPBasicAuth

from util.logging import register_secret


class AuthenticationError(Exception):
    """Raised when authentication configuration is invalid."""
    pass


class AuthProvider(ABC):
    """Abstract base class for Jira authentication providers."""

    @abstractmethod
    def apply(self, session: requests.Session) -> None:
        """Apply authentication to a requests.Session instance."""
        pass

    @abstractmethod
    def auth_type_name(self) -> str:
        """Return human-readable authentication type."""
        pass


class BasicAuthProvider(AuthProvider):
    """HTTP Basic Authentication using Username and Password."""

    def __init__(self, username: str, password: str):
        if not username or not password:
            raise AuthenticationError("Both username and password are required for Basic Auth.")
        self._username = username
        self._password = password
        # Register secrets to prevent logging leaks
        register_secret(self._password)

    def apply(self, session: requests.Session) -> None:
        session.auth = HTTPBasicAuth(self._username, self._password)

    def auth_type_name(self) -> str:
        return f"BasicAuth (user: {self._username})"

    def __repr__(self) -> str:
        return f"BasicAuthProvider(username='{self._username}', password='***')"


class PatAuthProvider(AuthProvider):
    """Personal Access Token (PAT) Authentication (Bearer token)."""

    def __init__(self, pat_token: str):
        if not pat_token or not pat_token.strip():
            raise AuthenticationError("Personal Access Token (PAT) cannot be empty.")
        self._token = pat_token.strip()
        register_secret(self._token)

    def apply(self, session: requests.Session) -> None:
        session.headers["Authorization"] = f"Bearer {self._token}"

    def auth_type_name(self) -> str:
        masked = f"{self._token[:4]}...{self._token[-4:]}" if len(self._token) > 8 else "***"
        return f"PersonalAccessToken (Bearer {masked})"

    def __repr__(self) -> str:
        return "PatAuthProvider(token='***')"


def create_auth_provider(
    username: Optional[str] = None,
    password: Optional[str] = None,
    pat: Optional[str] = None,
) -> AuthProvider:
    """
    Factory to create appropriate AuthProvider based on available configuration.
    
    Priority:
    1. Personal Access Token (PAT) if provided.
    2. Basic Auth (Username + Password) if provided.
    """
    if pat and pat.strip():
        return PatAuthProvider(pat_token=pat)

    if username and password:
        return BasicAuthProvider(username=username, password=password)

    raise AuthenticationError(
        "No valid authentication credentials found. "
        "Please provide either JIRA_PAT or both JIRA_USERNAME and JIRA_PASSWORD in .env or environment."
    )
