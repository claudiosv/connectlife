"""OAuth2 implementation for the ConnectLife integration.

Ported from Connectlife-LLC/HomeAssistantPlugin's oauth2.py. Unlike that
plugin, this does NOT override `redirect_uri` with a hardcoded
homeassistant.local:8123 URL — it inherits LocalOAuth2Implementation's
default, which derives the callback from this Home Assistant instance's own
configured internal/external URL, so it works for any deployment.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import CLIENT_ID, CLIENT_SECRET, DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN

_LOGGER = logging.getLogger(__name__)

_REDACT_KEYS = ("access_token", "refresh_token")


class OAuth2Session:
    """Thin wrapper around a token dict that ensures it stays fresh."""

    def __init__(
        self,
        hass: HomeAssistant,
        oauth2_implementation: config_entry_oauth2_flow.AbstractOAuth2Implementation,
        token: dict[str, Any] | None = None,
    ) -> None:
        self.hass = hass
        self.oauth2_implementation = oauth2_implementation
        self.token = token or {}
        self.session = aiohttp.ClientSession()

        _LOGGER.debug(
            "Initialized OAuth2Session with token info: %s",
            {k: "***" if k in _REDACT_KEYS else v for k, v in self.token.items()},
        )

    async def async_ensure_token_valid(self) -> None:
        """Ensure that the token is valid, refreshing it if needed."""
        if not self.token:
            raise ValueError("No token available")

        if self._is_token_expired():
            _LOGGER.debug("Token has expired, refreshing...")
            token_data = await self.oauth2_implementation.async_refresh_token(self.token)
            self.token.update(token_data)
            _LOGGER.debug("Token refreshed successfully")

    def _is_token_expired(self) -> bool:
        expires_at = self.token.get("expires_at")
        if not expires_at:
            expires_in = self.token.get("expires_in", 0)
            if expires_in:
                self.token["expires_at"] = time.time() + expires_in
                return False
            return True
        return time.time() >= expires_at - 300  # refresh 5 minutes before expiry

    async def async_get_access_token(self) -> str:
        await self.async_ensure_token_valid()
        return self.token["access_token"]

    async def close(self) -> None:
        await self.session.close()


class ConnectLifeOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """OAuth2 implementation for the ConnectLife/Hisense backend."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass=hass,
            domain=DOMAIN,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            authorize_url=OAUTH2_AUTHORIZE,
            token_url=OAUTH2_TOKEN,
        )

    @property
    def name(self) -> str:
        return "ConnectLife"

    async def _token_request(self, data: dict) -> dict:
        response = await super()._token_request(data)
        if "expires_in" in response and "expires_at" not in response:
            response["expires_at"] = time.time() + response["expires_in"]
        return response

    async def async_refresh_token(self, token: dict) -> dict:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise ValueError("No refresh token available")

        return await self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            }
        )
