"""DataUpdateCoordinator for the ConnectLife integration."""

from __future__ import annotations

import base64
import json
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

_DEVICE_STATUS_MSG_TYPES = {"status_devicestatus", "status_wifistatus"}


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
        # Set by __init__.async_setup_entry once the WebSocket connects; used
        # only so async_unload_entry can find it to disconnect on unload.
        self.websocket: Any = None

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch devices from the API and return a puid-keyed dict."""
        _LOGGER.debug("Polling ConnectLife for device state")
        try:
            devices = await self.api.get_online_ac_devices()
        except ConnectLifeAuthError as exc:
            _LOGGER.debug("Poll failed with an authentication error: %s", exc)
            raise UpdateFailed(f"Authentication error: {exc}") from exc
        except ConnectLifeRateLimitError as exc:
            # Return stale data so entities stay available instead of going unknown
            if self.data is not None:
                _LOGGER.warning(
                    "ConnectLife API rate-limited; returning cached data. Error: %s",
                    exc,
                )
                return self.data
            _LOGGER.debug("Poll failed: rate limited with no cached data: %s", exc)
            raise UpdateFailed(
                f"Rate limited and no cached data available: {exc}"
            ) from exc
        except ConnectLifeApiError as exc:
            _LOGGER.debug("Poll failed with an API error: %s", exc)
            raise UpdateFailed(f"API error: {exc}") from exc
        except Exception as exc:
            _LOGGER.debug("Poll failed with an unexpected error: %s", exc)
            raise UpdateFailed(f"Unexpected error: {exc}") from exc

        for device in devices:
            _LOGGER.debug(
                "Raw device data [%s] statusList: %s",
                device.get("puid"),
                device.get("statusList"),
            )

        _LOGGER.debug(
            "Poll complete: %d device(s): %s",
            len(devices),
            [d.get("puid") for d in devices],
        )
        return {device["puid"]: device for device in devices}

    def async_handle_push_update(self, message: dict[str, Any]) -> None:
        """Merge a WebSocket push notification into the current device data.

        `message` is the decoded top-level payload from ConnectLifeWebSocket:
        {"msgTypeCode": "status_devicestatus" | "status_wifistatus",
         "content": "<json-encoded string>"}.
        """
        msg_type = message.get("msgTypeCode")
        if msg_type not in _DEVICE_STATUS_MSG_TYPES:
            return

        content_raw = message.get("content", "{}")
        if not isinstance(content_raw, str):
            return
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError as exc:
            _LOGGER.debug("Failed to parse WebSocket message content: %s", exc)
            return

        puid = content.get("puid")
        if not puid or self.data is None or puid not in self.data:
            _LOGGER.debug("Push update for unknown/untracked device puid=%s", puid)
            return

        device = dict(self.data[puid])
        status = dict(device.get("statusList", {}))

        if msg_type == "status_wifistatus":
            online = content.get("onlinestats")
            if online is not None:
                # offlineState==0 means offline, nonzero means online — see
                # get_online_ac_devices() / ConnectLifeApi.get_online_ac_devices.
                device["offlineState"] = 1 if int(online) == 1 else 0
        else:  # status_devicestatus
            encoded_status = content.get("status")
            if isinstance(encoded_status, str) and encoded_status:
                try:
                    decoded = json.loads(base64.b64decode(encoded_status).decode("utf-8"))
                    if isinstance(decoded, dict):
                        status.update(decoded)
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    _LOGGER.debug("Failed to decode push status payload: %s", exc)
            properties = content.get("properties")
            if isinstance(properties, dict):
                status.update(properties)

        device["statusList"] = status
        new_data = dict(self.data)
        new_data[puid] = device
        _LOGGER.debug(
            "[%s] ConnectLife state updated via WebSocket push: msg_type=%s "
            "content=%s resulting_status=%s",
            puid,
            msg_type,
            content,
            status,
        )
        self.async_set_updated_data(new_data)
