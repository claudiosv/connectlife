"""Global fixtures for claudio_hisense integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components/ during tests (pytest-homeassistant-custom-component)."""
    return


class FakeOAuthSession:
    """Minimal stand-in for oauth2.OAuth2Session, no real network/session."""

    def __init__(self, access_token: str = "test-access-token") -> None:
        self.session = AsyncMock()
        self.token: dict[str, Any] = {"access_token": access_token}
        self.ensure_token_valid_calls = 0
        self._access_token = access_token

    async def async_ensure_token_valid(self) -> None:
        self.ensure_token_valid_calls += 1

    async def async_get_access_token(self) -> str:
        return self._access_token

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_oauth_session() -> FakeOAuthSession:
    return FakeOAuthSession()
