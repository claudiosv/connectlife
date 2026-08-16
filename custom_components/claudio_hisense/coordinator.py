"""DataUpdateCoordinator for the ConnectLife integration."""

from __future__ import annotations

import base64
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ConnectLifeApi,
    ConnectLifeApiError,
    ConnectLifeAuthError,
    ConnectLifeRateLimitError,
)
from .const import DOMAIN, FAULT_FIELDS, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

_DEVICE_STATUS_MSG_TYPES = {"status_devicestatus", "status_wifistatus"}


class ConnectLifeCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that polls ConnectLife for device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ConnectLifeApi,
        update_interval_seconds: int | None = UPDATE_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # None disables periodic polling entirely (WebSocket-only,
            # relying on ConnectLifeWebSocket's push updates) — see
            # CONF_POLL_INTERVAL_ENABLED. Manual refreshes (first refresh,
            # post-command _schedule_refresh) work either way.
            update_interval=(
                timedelta(seconds=update_interval_seconds)
                if update_interval_seconds is not None
                else None
            ),
        )
        self.api = api
        # Set by __init__.async_setup_entry once the WebSocket connects; used
        # only so async_unload_entry can find it to disconnect on unload.
        self.websocket: Any = None
        # {puid: {fault_key, ...}} — fault fields currently active per
        # device, so _check_faults only creates/clears a repair issue and
        # notification on a state *transition*, not on every update.
        self._active_faults: dict[str, set[str]] = {}

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
        result = {device["puid"]: device for device in devices}
        self._check_faults(result)
        return result

    def _check_faults(self, data: dict[str, dict[str, Any]]) -> None:
        """Raise/clear a repair issue + notification for each fault field.

        Compares each device's current fault flags against what was active
        last time, so an issue/notification is only created or cleared on
        an actual transition — not re-created on every poll/push while a
        fault stays active.
        """
        for puid, device in data.items():
            status = device.get("statusList", {})
            device_name = device.get("deviceNickName", puid)
            active_now = self._active_faults.setdefault(puid, set())

            for key, fault_name in FAULT_FIELDS.items():
                is_active = str(status.get(key, "0")) == "1"
                issue_id = f"{puid}_{key}"

                if is_active and key not in active_now:
                    active_now.add(key)
                    _LOGGER.warning(
                        "[%s] ConnectLife fault active: %s", puid, fault_name
                    )
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        issue_id,
                        is_fixable=False,
                        severity=ir.IssueSeverity.WARNING,
                        translation_key="device_fault",
                        translation_placeholders={
                            "device_name": str(device_name),
                            "fault_name": fault_name,
                        },
                    )
                    persistent_notification.async_create(
                        self.hass,
                        f"ConnectLife reported a fault on {device_name}: "
                        f"{fault_name}.",
                        title=f"{device_name}: {fault_name}",
                        notification_id=f"{DOMAIN}_{issue_id}",
                    )
                elif not is_active and key in active_now:
                    active_now.discard(key)
                    _LOGGER.info(
                        "[%s] ConnectLife fault cleared: %s", puid, fault_name
                    )
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                    persistent_notification.async_dismiss(
                        self.hass, f"{DOMAIN}_{issue_id}"
                    )

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
        self._check_faults(new_data)
        self.async_set_updated_data(new_data)
