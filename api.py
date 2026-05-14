"""ConnectLife API client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import date
from typing import Any, Literal

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
)

from .const import (
    AC_DEVICE_TYPE_CODES,
    APP_ID,
    APP_SECRET,
    BASE_URL,
    ENERGY_REQUEST_DELAY,
    GIGYA_API_KEY,
    GIGYA_GMID,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_REDIRECT_URI,
    PUBLIC_KEY_PEM,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
    SIGN_MAGIC,
)

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = "Runner/2.0.6 (iPhone; iOS 17.2.1; Scale/3.00)"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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
        self, session: aiohttp.ClientSession, username: str, password: str
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode())

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> Any:
        """Make an HTTP request with tenacity-managed retries and backoff."""
        headers = {"User-Agent": _USER_AGENT}
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
                        params=params,
                        json=json_body,
                        data=form,
                        headers=headers,
                    ) as resp:
                        if resp.status in _RETRYABLE_STATUS:
                            raise _RetryableError(
                                f"HTTP {resp.status} from {method} {url}",
                                retry_after=_parse_retry_after(resp.headers),
                            )
                        resp.raise_for_status()
                        return await resp.json(content_type=None)
        except _RetryableError as exc:
            raise ConnectLifeRateLimitError(
                f"Request to {url} failed after {RETRY_ATTEMPTS} attempts: {exc}"
            ) from exc
        except aiohttp.ClientResponseError as exc:
            raise ConnectLifeApiError(
                f"HTTP error {exc.status}: {exc.message}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise ConnectLifeApiError(f"Network error: {exc}") from exc

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        _LOGGER.debug("Fetching new ConnectLife access token")
        self._access_token = await self._fetch_access_token()
        self._token_expires_at = time.monotonic() + 86000  # 24 h with small buffer
        return self._access_token

    async def _fetch_access_token(self) -> str:
        """Perform the full Gigya → OAuth authentication flow."""
        # Step 1: Login to Gigya
        login_data = await self._request(
            "POST",
            "https://accounts.eu1.gigya.com/accounts.login",
            form={
                "loginID": self._username,
                "password": self._password,
                "APIKey": GIGYA_API_KEY,
                "gmid": GIGYA_GMID,
            },
        )
        cookie_value = (login_data.get("sessionInfo") or {}).get("cookieValue")
        if not cookie_value:
            raise ConnectLifeAuthError(
                f"Gigya login failed. Response: {json.dumps(login_data)}"
            )
        uid = login_data["UID"]

        # Step 2: Get JWT from Gigya
        jwt_data = await self._request(
            "POST",
            "https://accounts.eu1.gigya.com/accounts.getJWT",
            form={
                "APIKey": GIGYA_API_KEY,
                "gmid": GIGYA_GMID,
                "login_token": cookie_value,
            },
        )
        id_token = jwt_data.get("id_token")
        if not id_token:
            raise ConnectLifeAuthError(
                f"Failed to get JWT. Response: {json.dumps(jwt_data)}"
            )

        # Step 3: Exchange JWT for OAuth authorization code
        auth_data = await self._request(
            "POST",
            "https://oauth.hijuconn.com/oauth/authorize",
            json_body={
                "client_id": OAUTH_CLIENT_ID,
                "idToken": id_token,
                "response_type": "code",
                "redirect_uri": OAUTH_REDIRECT_URI,
                "thirdType": "CDC",
                "thirdClientId": uid,
            },
        )
        code = auth_data.get("code")
        if not code:
            raise ConnectLifeAuthError(
                f"Failed to get OAuth code. Response: {json.dumps(auth_data)}"
            )

        # Step 4: Exchange code for access token
        token_data = await self._request(
            "POST",
            "https://oauth.hijuconn.com/oauth/token",
            form={
                "client_id": OAUTH_CLIENT_ID,
                "code": code,
                "grant_type": "authorization_code",
                "client_secret": OAUTH_CLIENT_SECRET,
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
        )
        access_token = token_data.get("access_token")
        if not access_token:
            raise ConnectLifeAuthError(
                f"Failed to get access token. Response: {json.dumps(token_data)}"
            )
        return access_token

    # ------------------------------------------------------------------
    # Request signing
    # ------------------------------------------------------------------

    def _sign(self, data: dict[str, Any]) -> str:
        sorted_items = sorted(data.items())
        parts = []
        for k, v in sorted_items:
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(",", ":"))
            parts.append(f"{k}={v}")
        to_hash = "&".join(parts) + SIGN_MAGIC
        digest = hashlib.sha256(to_hash.encode()).digest()
        encrypted = self._public_key.encrypt(digest, padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    def _common_params(self) -> dict[str, Any]:
        return {
            "appId": APP_ID,
            "appSecret": APP_SECRET,
            "languageId": "12",
            "randStr": secrets.token_hex(16),
            "timeStamp": str(int(time.time() * 1000)),
            "timezone": "1.0",
            "version": "5.0",
        }

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """Fetch all AC devices from the ConnectLife API."""
        token = await self._ensure_token()
        params = self._common_params() | {"accessToken": token}
        params["sign"] = self._sign(params)

        body = await self._request(
            "GET",
            f"{BASE_URL}/clife-svc/pu/get_device_status_list",
            params=params,
        )

        _LOGGER.debug("get_device_status_list raw response: %s", json.dumps(body))

        response = body.get("response", {})
        if "deviceList" not in response:
            _LOGGER.warning(
                "ConnectLife API: missing deviceList in response: %s", response
            )
            return []

        devices: list[dict[str, Any]] = response["deviceList"]

        for i, device in enumerate(devices):
            if i > 0:
                await asyncio.sleep(ENERGY_REQUEST_DELAY)
            try:
                energy = await self.get_device_energy(device["puid"])
                kwh = (energy.get("resultData") or {}).get("electricTotal")
                if kwh is not None:
                    device.setdefault("statusList", {})["daily_energy_kwh"] = kwh
            except ConnectLifeRateLimitError:
                _LOGGER.warning(
                    "Rate-limited fetching energy for %s; skipping this poll",
                    device.get("puid"),
                )
            except Exception as exc:
                _LOGGER.debug(
                    "Could not fetch energy for %s: %s", device.get("puid"), exc
                )

        return devices

    async def get_online_ac_devices(self) -> list[dict[str, Any]]:
        """Return only online AC devices."""
        all_devices = await self.get_devices()
        result = []
        for device in all_devices:
            puid = device.get("puid", "unknown")
            if device.get("offlineState") == 0:
                _LOGGER.info("Skipping offline device: %s", puid)
                continue
            if device.get("deviceTypeCode") not in AC_DEVICE_TYPE_CODES:
                _LOGGER.info(
                    "Skipping device %s with unsupported type: %s",
                    puid,
                    device.get("deviceTypeCode"),
                )
                continue
            result.append(device)
        return result

    async def get_device_energy(self, device_id: str) -> dict[str, Any]:
        token = await self._ensure_token()
        today = date.today().isoformat()
        payload = self._common_params() | {
            "accessToken": token,
            "puid": device_id,
            "statType": "day",
            "dateEnd": today,
            "dateStart": today,
            "curve": "1",
            "deviceType": "009",
            "featureCode": "117",
        }
        payload["sign"] = self._sign(payload)
        body = await self._request(
            "POST", f"{BASE_URL}/clife-svc/pu/air_duct_energy", json_body=payload
        )
        return body.get("response", {})

    async def update_device(
        self, device_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        token = await self._ensure_token()
        _LOGGER.info("Updating device %s: %s", device_id, properties)
        payload = self._common_params() | {
            "accessToken": token,
            "puid": device_id,
            "properties": properties,
        }
        payload["sign"] = self._sign(payload)
        body = await self._request(
            "POST", f"{BASE_URL}/device/pu/property/set", json_body=payload
        )
        result = body.get("response", {})
        _LOGGER.debug("Update device result: %s", result)
        return result

    async def validate_credentials(self) -> bool:
        try:
            self._access_token = None
            self._token_expires_at = 0.0
            await self._ensure_token()
            return True
        except ConnectLifeAuthError:
            return False
