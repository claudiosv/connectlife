"""ConnectLife API client.

Thin wrapper around the `connectlife` PyPI package (auth, request signing and
the HijuConn gateway calls all live there). This module adds the bits that
are specific to this integration: retry/backoff on transient gateway errors,
translating errors into this integration's exception types, device-list
filtering for online AC units, and diff-only state-change logging.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

import aiohttp
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
)

from connectlife.api import ConnectLifeApi as _UpstreamConnectLifeApi
from connectlife.api import LifeConnectAuthError as _UpstreamAuthError
from connectlife.api import LifeConnectError as _UpstreamApiError

from .const import (
    AC_DEVICE_TYPE_CODES,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
)

_LOGGER = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_DIFF_IGNORE_KEYS = {
    # ConnectLife never echoes t_beep back in its status responses, so it
    # would otherwise always show as a bogus None -> value change.
    "t_beep",
}
# Keys ConnectLife sometimes omits from a device's initial status entirely —
# only worth reporting once we actually have a previous value to compare
# against, or every first sighting logs a meaningless None -> value "change".
_DIFF_ONLY_IF_KNOWN_KEYS = {
    "t_eco",
}


def _diff_status(
    previous: dict[str, Any], new: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Return {key: (previous_value, new_value)} for entries that differ.

    Only keys present in `new` are compared — `previous` may carry many more
    fields than are actually changing. Values are compared as strings since
    ConnectLife reports (and expects) status values as strings regardless of
    their logical type.
    """
    diff: dict[str, tuple[Any, Any]] = {}
    for key, new_val in new.items():
        if key in _DIFF_IGNORE_KEYS:
            continue
        old_val = previous.get(key)
        if old_val is None and key in _DIFF_ONLY_IF_KNOWN_KEYS:
            continue
        if str(old_val) != str(new_val):
            diff[key] = (old_val, new_val)
    return diff


class ConnectLifeAuthError(Exception):
    """Raised when authentication fails."""


class ConnectLifeApiError(Exception):
    """Raised when the API returns an unrecoverable error."""


class ConnectLifeRateLimitError(ConnectLifeApiError):
    """Raised when the API is rate-limiting us after all retries are exhausted."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, aiohttp.ClientError):
        return True
    return isinstance(exc, _UpstreamApiError) and exc.status in _RETRYABLE_STATUS


def _retry_wait(retry_state: RetryCallState) -> float:
    """Exponential backoff."""
    return min(
        RETRY_BACKOFF_BASE * (2 ** (retry_state.attempt_number - 1)), RETRY_BACKOFF_MAX
    )


class ConnectLifeApi:
    """Client for the ConnectLife API."""

    def __init__(
        self,
        username: str,
        password: str,
        hass: HomeAssistant | None = None,
        test_server: str | None = None,
    ) -> None:
        # Public: the dev CLI (cli.py) reaches into this for direct login /
        # token inspection outside the retry/logging wrapper below.
        # `test_server` is forwarded as-is to the upstream client — it points
        # every endpoint (login, JWT, OAuth, gateway) at a base URL instead of
        # the real ConnectLife servers; used by the test suite.
        self.client = _UpstreamConnectLifeApi(username, password, test_server=test_server)
        self._username = username
        self._hass = hass
        # Most recently fetched statusList per device (puid), used to log
        # what actually changed on update_device()/get_devices() calls
        # instead of the full payload/state every time.
        self._last_status: dict[str, dict[str, Any]] = {}

    async def _retrying(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Call an upstream client method with retry/backoff, translating errors."""
        retryer = AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=_retry_wait,
            before_sleep=before_sleep_log(_LOGGER, logging.WARNING),
            reraise=True,
        )
        try:
            return await retryer(func, *args, **kwargs)
        except _UpstreamAuthError as exc:
            if exc.status in _RETRYABLE_STATUS:
                raise ConnectLifeRateLimitError(
                    f"Request to {exc.endpoint} failed after {RETRY_ATTEMPTS} attempts: {exc}"
                ) from exc
            raise ConnectLifeAuthError(str(exc)) from exc
        except _UpstreamApiError as exc:
            if exc.status in _RETRYABLE_STATUS:
                raise ConnectLifeRateLimitError(
                    f"Request to {exc.endpoint} failed after {RETRY_ATTEMPTS} attempts: {exc}"
                ) from exc
            raise ConnectLifeApiError(str(exc)) from exc
        except aiohttp.ClientError as exc:
            raise ConnectLifeApiError(f"Network error: {exc}") from exc

    async def _write_debug_dump(self, devices: list[dict[str, Any]]) -> None:
        """Best-effort dump of a fetched device list for troubleshooting."""
        if self._hass is None:
            return
        log_dir = Path(self._hass.config.path("connectlife_logs"))
        log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        full_path = log_dir / f"ac_state_{timestamp}.json"

        def _write(data: list[dict[str, Any]], path: Path) -> None:
            try:
                path.write_text(json.dumps(data, indent=4), encoding="utf-8")
            except Exception as exc:
                _LOGGER.error("Failed to write request log: %s", exc)

        await self._hass.async_add_executor_job(_write, devices, full_path)

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch all AC devices from the ConnectLife API."""
        devices: list[dict[str, Any]] = await self._retrying(
            self.client.get_appliances_json
        )
        _LOGGER.info("Fetched status for %d device(s) from ConnectLife", len(devices))

        for device in devices:
            puid = device.get("puid")
            if not puid:
                continue
            fresh_status = device.get("statusList", {})
            diff = _diff_status(self._last_status.get(puid, {}), fresh_status)
            if diff:
                _LOGGER.info(
                    "[%s] ConnectLife state changed vs local: %s", puid, diff
                )
            self._last_status[puid] = dict(fresh_status)

        await self._write_debug_dump(devices)
        return devices

    async def get_online_ac_devices(self) -> list[dict[str, Any]]:
        """Return only online AC devices."""
        all_devices = await self.get_devices()
        result = []
        for device in all_devices:
            puid = device.get("puid", "unknown")
            if device.get("offlineState") == 0:
                _LOGGER.debug("Skipping offline device: %s", puid)
                continue
            if device.get("deviceTypeCode") not in AC_DEVICE_TYPE_CODES:
                _LOGGER.debug(
                    "Skipping device %s with unsupported type: %s",
                    puid,
                    device.get("deviceTypeCode"),
                )
                continue
            result.append(device)
        _LOGGER.debug(
            "get_online_ac_devices: %d of %d device(s) online and supported",
            len(result),
            len(all_devices),
        )
        return result

    async def update_device(self, device_id: str, properties: dict[str, Any]) -> None:
        # The gateway expects property values as strings (e.g. {"t_temp": "75"}),
        # not JSON numbers/booleans — sending raw ints causes "Signature check
        # fail" even though the signing algorithm itself is otherwise correct.
        string_properties = {k: str(v) for k, v in properties.items()}
        diff = _diff_status(self._last_status.get(device_id, {}), string_properties)
        if diff:
            _LOGGER.info(
                "Updating device %s — changes vs last known ConnectLife state: %s",
                device_id,
                diff,
            )
        else:
            _LOGGER.info(
                "Updating device %s: no change vs last known ConnectLife state "
                "(sending: %s)",
                device_id,
                string_properties,
            )
        await self._retrying(self.client.update_appliance, device_id, string_properties)

    async def validate_credentials(self) -> bool:
        try:
            await self._retrying(self.client.login)
            _LOGGER.debug("Credential validation succeeded for %s", self._username)
            return True
        except ConnectLifeAuthError:
            _LOGGER.debug("Credential validation failed for %s", self._username)
            return False
