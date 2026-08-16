"""ConnectLife API client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .oauth2 import OAuth2Session

import aiohttp
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
)

from .const import (
    AC_DEVICE_TYPE_CODES,
    BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
)

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = "Runner/2.0.6 (iPhone; iOS 17.2.1; Scale/3.00)"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_SENSITIVE_KEYS = {
    "password",
    "accessToken",
    "access_token",
    "sign",
    "appSecret",
    "client_secret",
    "idToken",
    "id_token",
    "login_token",
    "refresh_token",
    "code",
    "cookieValue",
}


def _redact(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mask sensitive top-level values before writing a payload to the log."""
    if data is None:
        return None
    return {k: ("***" if k in _SENSITIVE_KEYS else v) for k, v in data.items()}


def _redact_body(body: str | None) -> dict[str, Any] | None:
    """Best-effort redaction of a JSON request body string for logging."""
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return _redact(parsed) if isinstance(parsed, dict) else parsed


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


class _RetryableError(Exception):
    """Internal: signals a retryable HTTP error; carries an optional Retry-After delay."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_wait(retry_state: RetryCallState) -> float:
    """Exponential backoff, honouring Retry-After when the server provides it."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableError) and exc.retry_after is not None:
        return min(exc.retry_after, RETRY_BACKOFF_MAX)
    return min(
        RETRY_BACKOFF_BASE * (2 ** (retry_state.attempt_number - 1)), RETRY_BACKOFF_MAX
    )


def _parse_retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class ConnectLifeApi:
    """Client for the ConnectLife API."""

    def __init__(
        self,
        oauth_session: OAuth2Session,
        hass: HomeAssistant | None = None,
    ) -> None:
        self.oauth_session = oauth_session
        self._session = oauth_session.session
        self._hass = hass
        self._source_id: str | None = None
        # Most recently fetched statusList per device (puid), used to log
        # what actually changed on update_device()/get_devices() calls
        # instead of the full payload/state every time.
        self._last_status: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        headers: dict[str, str],
        data: str | None = None,
    ) -> Any:
        """Make an HTTP request with tenacity-managed retries and backoff."""
        _LOGGER.debug("-> %s %s data=%s", method, url, _redact_body(data))

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((_RetryableError, aiohttp.ClientError)),
                stop=stop_after_attempt(RETRY_ATTEMPTS),
                wait=_retry_wait,
                before_sleep=before_sleep_log(_LOGGER, logging.WARNING),
                reraise=True,
            ):
                with attempt:
                    async with self._session.request(
                        method,
                        url,
                        data=data,
                        headers=headers,
                    ) as resp:
                        if resp.status in _RETRYABLE_STATUS:
                            raise _RetryableError(
                                f"HTTP {resp.status} from {method} {url}",
                                retry_after=_parse_retry_after(resp.headers),
                            )
                        resp.raise_for_status()
                        response_json = await resp.json(content_type=None)
                        _LOGGER.debug(
                            "<- %s %s status=%s response=%s",
                            method,
                            url,
                            resp.status,
                            _redact(response_json)
                            if isinstance(response_json, dict)
                            else response_json,
                        )

                        if self._hass is not None:
                            log_dir = Path(self._hass.config.path("connectlife_logs"))
                            log_dir.mkdir(exist_ok=True)
                            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                            full_path = log_dir / f"ac_state_{timestamp}.json"
                            json_log = {
                                "method": method,
                                "url": url,
                                "data": data,
                                "resp_status": resp.status,
                                "response": response_json,
                            }

                            def _write(data: dict, path: Path) -> None:
                                try:
                                    path.write_text(
                                        json.dumps(data, indent=4), encoding="utf-8"
                                    )
                                except Exception as exc:
                                    _LOGGER.error(
                                        "Failed to write request log: %s", exc
                                    )

                            await self._hass.async_add_executor_job(
                                _write, json_log, full_path
                            )

                        return response_json
        except _RetryableError as exc:
            _LOGGER.debug("<- %s %s exhausted retries: %s", method, url, exc)
            raise ConnectLifeRateLimitError(
                f"Request to {url} failed after {RETRY_ATTEMPTS} attempts: {exc}"
            ) from exc
        except aiohttp.ClientResponseError as exc:
            _LOGGER.debug("<- %s %s HTTP error %s: %s", method, url, exc.status, exc.message)
            raise ConnectLifeApiError(
                f"HTTP error {exc.status}: {exc.message}"
            ) from exc
        except aiohttp.ClientError as exc:
            _LOGGER.debug("<- %s %s network error: %s", method, url, exc)
            raise ConnectLifeApiError(f"Network error: {exc}") from exc

    # ------------------------------------------------------------------
    # Request signing (HMAC-SHA256, matching Connectlife-LLC/HomeAssistantPlugin)
    # ------------------------------------------------------------------

    @staticmethod
    def _sign_hmac(secret: str, base_string: str) -> str:
        digest = hmac.new(secret.encode(), base_string.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    @staticmethod
    def _body_digest(body: str | None) -> str:
        if body:
            return base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        # SHA-256 of an empty string, matching the plugin's fixed empty-body digest.
        return "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="

    @staticmethod
    def _gmt_date() -> str:
        return datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")

    @staticmethod
    def _path_for_signing(url: str) -> str:
        """Strip the scheme+host, keeping path and query string."""
        return re.sub(r"^https://[^/]*", "", url)

    def _get_source_id(self) -> str:
        if not self._source_id:
            self._source_id = "td001002000" + hashlib.md5(
                f"{time.monotonic()}-{id(self)}".encode()
            ).hexdigest()
        return self._source_id

    async def _system_parameters(self, hass: HomeAssistant | None) -> dict[str, Any]:
        timestamp = int(time.time() * 1000)
        random_str = hashlib.md5(f"{timestamp}-{id(self)}".encode()).hexdigest()
        tz = str(hass.config.time_zone) if hass and hass.config.time_zone else "UTC"
        params: dict[str, Any] = {
            "timeStamp": str(timestamp),
            "version": "8.1",
            "languageId": "1",
            "timezone": tz,
            "randStr": random_str,
            "appId": CLIENT_ID,
            "sourceId": self._get_source_id(),
            "platformId": 5,
        }
        access_token = await self.oauth_session.async_get_access_token()
        if access_token:
            params["accessToken"] = access_token
        return params

    async def _api_request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a signed request to a ConnectLife API endpoint."""
        try:
            await self.oauth_session.async_ensure_token_valid()
        except ValueError as exc:
            raise ConnectLifeAuthError(f"OAuth token unavailable: {exc}") from exc

        request_data: dict[str, Any] = dict(data or {})
        request_data.update(await self._system_parameters(self._hass))

        url = f"{BASE_URL}{path}"
        headers: dict[str, str] = {}
        body: str | None

        if method == "GET":
            headers["accessToken"] = request_data.pop("accessToken", "")
            query = "&".join(
                f"{k}={json.dumps(v, separators=(',', ':')) if isinstance(v, (dict, list)) else v}"
                for k, v in request_data.items()
            )
            url = f"{url}?{query}" if query else url
            body = None
        else:
            body = json.dumps(request_data)

        client_id = CLIENT_ID
        header_key = "hi-params-encrypt"
        gmt_date = self._gmt_date()
        base_string = (
            f"{client_id}\n{method} {self._path_for_signing(url)}\n"
            f"date: {gmt_date}\n{header_key}: {client_id}\n"
        )
        signature = self._sign_hmac(CLIENT_SECRET, base_string)

        headers.update({
            header_key: client_id,
            "Date": gmt_date,
            "Authorization": (
                f'Signature signature="{signature}", keyId="{client_id}",'
                f'algorithm="hmac-sha256", headers="@request-target date {header_key}"'
            ),
            "Content-Type": "application/json",
            "Digest": f"SHA-256={self._body_digest(body)}",
            "User-Agent": _USER_AGENT,
        })

        response = await self._request(method, url, headers=headers, data=body)
        if not isinstance(response, dict):
            raise ConnectLifeApiError(f"Unexpected response format: {response}")
        if response.get("resultCode") not in (0, None):
            raise ConnectLifeApiError(
                f"API error: {response.get('msg', 'Unknown error')}"
            )
        return response

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch all AC devices from the ConnectLife API."""
        body = await self._api_request("GET", "/clife-svc/pu/get_device_status_list")

        _LOGGER.debug(
            "get_device_status_list raw response: %s", json.dumps(body, indent=4)
        )

        response = body.get("response", {})
        if "deviceList" not in response:
            _LOGGER.warning(
                "ConnectLife API: missing deviceList in response: %s", response
            )
            return []

        devices: list[dict[str, Any]] = response["deviceList"]
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

        # for i, device in enumerate(devices):
        #     if i > 0:
        #         await asyncio.sleep(ENERGY_REQUEST_DELAY)
        #     try:
        #         energy = await self.get_device_energy(device["puid"])
        #         kwh = (energy.get("resultData") or {}).get("electricTotal")
        #         if kwh is not None:
        #             device.setdefault("statusList", {})["daily_energy_kwh"] = kwh
        #     except ConnectLifeRateLimitError:
        #         _LOGGER.warning(
        #             "Rate-limited fetching energy for %s; skipping this poll",
        #             device.get("puid"),
        #         )
        #     except Exception as exc:
        #         _LOGGER.debug(
        #             "Could not fetch energy for %s: %s", device.get("puid"), exc
        #         )

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

    async def get_device_energy(self, device_id: str) -> dict[str, Any]:
        today = date.today().isoformat()
        body = await self._api_request(
            "POST",
            "/clife-svc/pu/air_duct_energy",
            data={
                "puid": device_id,
                "statType": "day",
                "dateEnd": today,
                "dateStart": today,
                "curve": "1",
                "deviceType": "009",
                "featureCode": "117",
            },
        )
        return body.get("response", {})

    async def update_device(
        self, device_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        # The gateway expects property values as strings (e.g. {"t_temp": "75"}),
        # not JSON numbers/booleans — sending raw ints causes "Signature check
        # fail" even though the signing algorithm itself is otherwise correct.
        properties = {k: str(v) for k, v in properties.items()}
        diff = _diff_status(self._last_status.get(device_id, {}), properties)
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
                properties,
            )
        body = await self._api_request(
            "POST",
            "/device/pu/property/set",
            data={"puid": device_id, "properties": properties},
        )
        result = body.get("response", {})
        _LOGGER.debug("Update device result: %s", result)
        return result

    async def validate_credentials(self) -> bool:
        try:
            await self.oauth_session.async_ensure_token_valid()
            _LOGGER.debug("Credential validation succeeded")
            return True
        except (ValueError, ConnectLifeAuthError, ConnectLifeApiError) as exc:
            _LOGGER.debug("Credential validation failed: %s", exc)
            return False
