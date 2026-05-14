"""DataUpdateCoordinator for the ConnectLife integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ConnectLifeApi,
    ConnectLifeApiError,
    ConnectLifeAuthError,
    ConnectLifeRateLimitError,
)
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class ConnectLifeCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that polls ConnectLife for device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ConnectLifeApi,
        update_interval_seconds: int = UPDATE_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch devices from the API and return a puid-keyed dict."""
        try:
            devices = await self.api.get_online_ac_devices()
        except ConnectLifeAuthError as exc:
            raise UpdateFailed(f"Authentication error: {exc}") from exc
        except ConnectLifeRateLimitError as exc:
            # Return stale data so entities stay available instead of going unknown
            if self.data is not None:
                _LOGGER.warning(
                    "ConnectLife API rate-limited; returning cached data. Error: %s",
                    exc,
                )
                return self.data
            raise UpdateFailed(
                f"Rate limited and no cached data available: {exc}"
            ) from exc
        except ConnectLifeApiError as exc:
            raise UpdateFailed(f"API error: {exc}") from exc
        except Exception as exc:
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

        for device in devices:
            _LOGGER.debug(
                "Raw device data [%s] statusList: %s",
                device.get("puid"),
                device.get("statusList"),
            )

        return {device["puid"]: device for device in devices}
