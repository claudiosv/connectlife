"""Standalone username/password ConnectLife client, used only by cli.py.

The production integration (api.py) now authenticates via a real OAuth2
browser-redirect login (see oauth2.py/config_flow.py) using a different
ConnectLife app registration, and signs requests with HMAC-SHA256. This
module keeps the original hand-rolled Gigya-login + RSA-signed client so the
dev CLI can still authenticate with a bare username/password, independent of
Home Assistant's OAuth2 flow. It is deliberately self-contained (its own
constants, not shared with const.py) since nothing else uses it.
"""

from __future__ import annotations

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

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = "Runner/2.0.6 (iPhone; iOS 17.2.1; Scale/3.00)"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 2.0
_RETRY_BACKOFF_MAX = 60.0

_BASE_URL = "https://clife-eu-gateway.hijuconn.com"
_AC_DEVICE_TYPE_CODES = {"009", "006", "008"}

_GIGYA_API_KEY = "4_yhTWQmHFpZkQZDSV1uV-_A"
_GIGYA_GMID = (
    "gmid.ver4.AtLt3mZAMA.C8m5VqSTEQDrTRrkYYDgOaJWcyQ-XHow5nzQSXJF3EO3TnqTJ8tKUmQaaQ6z8p0s"
    ".zcTbHe6Ax6lHfvTN7JUj7VgO4x8Vl-vk1u0kZcrkKmKWw8K9r0shyut_at5Q0ri6zTewnAv2g1Dc8dauuyd-Sw.sc3"
)
_OAUTH_CLIENT_ID = "5065059336212"
_OAUTH_CLIENT_SECRET = "07swfKgvJhC3ydOUS9YV_SwVz0i4LKqlOLGNUukYHVMsJRF1b-iWeUGcNlXyYCeK"
_OAUTH_REDIRECT_URI = "https://api.connectlife.io/swagger/oauth2-redirect.html"

_APP_ID = "47110565134383"
_APP_SECRET = "yOzhz6junYno-nmULM3Wr7PU_dpSZN22ZdluvVWZ4uW5ZwwG8fIGCHTbrhcnU-iv"
_SIGN_MAGIC = "D9519A4B756946F081B7BB5B5E8D1197"

_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAyyWrNG6q475HIHu7sMVu
vHof6vlgPeixmxa4EL/UsvVvHPz33NnWoQetQqit9TBNzUjMXw0KlY9PXM4iqHUU
U+dSyNDq1jZWIiJ2C2FccppswJtIKL3NRMFvT9PFh6NlP/4FUcQKojgKFbF7Kacc
JPKYHlwaO7qgoIjLxAHlSOXGpucJcOkPzT2EqsSVnW8sn8kenvNmghXDayhgxsh6
AyxK4kehJplEnmX/iYCfNoFXknGcLqFWYccgBz3fybvx30C/0IgU1980L8QsUAv5
esZmN8ugnbRgLRxKRlkQQLxQAiZMZdKTAx665YflT3YMHJvEFE8c2XFgoxHzSMc4
BwIDAQAB
-----END PUBLIC KEY-----
"""

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


class LegacyConnectLifeAuthError(Exception):
    """Raised when authentication fails."""


class LegacyConnectLifeApiError(Exception):
    """Raised when the API returns an unrecoverable error."""


class LegacyConnectLifeRateLimitError(LegacyConnectLifeApiError):
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
        return min(exc.retry_after, _RETRY_BACKOFF_MAX)
    return min(
        _RETRY_BACKOFF_BASE * (2 ** (retry_state.attempt_number - 1)), _RETRY_BACKOFF_MAX
    )


def _jwt_monotonic_expiry(token: str) -> float:
    """Return the monotonic time at which a JWT expires, or 0 on any parse error."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        exp: int = json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp", 0)
        return time.monotonic() + (exp - time.time())
    except Exception:
        return 0.0


def _parse_retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class LegacyConnectLifeApi:
    """Username/password ConnectLife client (Gigya login + RSA-signed requests)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._login_token: str | None = None
        self._login_token_expires_at: float = 0.0
        self._uid: str | None = None
        self._id_token: str | None = None
        self._id_token_expires_at: float = 0.0
        self._refresh_token: str | None = None
        # Most recently fetched statusList per device (puid), used to log
        # what actually changed on update_device()/get_devices() calls
        # instead of the full payload/state every time.
        self._last_status: dict[str, dict[str, Any]] = {}
        self._public_key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM.encode())

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
        _LOGGER.debug(
            "-> %s %s params=%s json=%s form=%s",
            method,
            url,
            _redact(params),
            _redact(json_body),
            _redact(form),
        )

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((_RetryableError, aiohttp.ClientError)),
                stop=stop_after_attempt(_RETRY_ATTEMPTS),
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
                        return response_json
        except _RetryableError as exc:
            _LOGGER.debug("<- %s %s exhausted retries: %s", method, url, exc)
            raise LegacyConnectLifeRateLimitError(
                f"Request to {url} failed after {_RETRY_ATTEMPTS} attempts: {exc}"
            ) from exc
        except aiohttp.ClientResponseError as exc:
            _LOGGER.debug("<- %s %s HTTP error %s: %s", method, url, exc.status, exc.message)
            raise LegacyConnectLifeApiError(
                f"HTTP error {exc.status}: {exc.message}"
            ) from exc
        except aiohttp.ClientError as exc:
            _LOGGER.debug("<- %s %s network error: %s", method, url, exc)
            raise LegacyConnectLifeApiError(f"Network error: {exc}") from exc

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

    async def _gigya_login(self) -> None:
        """POST to accounts.login and cache the resulting login_token and UID."""
        login_data = await self._request(
            "POST",
            "https://accounts.eu1.gigya.com/accounts.login",
            form={
                "loginID": self._username,
                "password": self._password,
                "APIKey": _GIGYA_API_KEY,
                "gmid": _GIGYA_GMID,
            },
        )
        session_info = login_data.get("sessionInfo") or {}
        cookie_value = session_info.get("cookieValue")
        if not cookie_value:
            raise LegacyConnectLifeAuthError(
                f"Gigya login failed. Response: {json.dumps(login_data, indent=4)}"
            )
        # Use the server-reported max-age if present; fall back to 13 days.
        max_age = session_info.get("cookieMaxAge") or (13 * 24 * 3600)
        self._login_token = cookie_value
        self._login_token_expires_at = time.monotonic() + int(max_age)
        self._uid = login_data["UID"]
        _LOGGER.debug("Gigya login_token cached for %s s", max_age)

    async def _fetch_access_token(self) -> str:
        """Obtain a fresh access_token, reusing cached tokens at each step where possible."""
        # Fastest path: use refresh_token to skip steps 1-3 entirely.
        if self._refresh_token:
            try:
                return await self._refresh_access_token()
            except LegacyConnectLifeAuthError:
                _LOGGER.debug("refresh_token rejected; falling back to full flow")
                self._refresh_token = None

        # Ensure we have a valid Gigya login_token (step 1).
        if not self._login_token or time.monotonic() >= self._login_token_expires_at:
            await self._gigya_login()

        try:
            return await self._exchange_login_token_for_access_token()
        except LegacyConnectLifeAuthError:
            # login_token was revoked early; force a fresh login and retry once.
            _LOGGER.debug("login_token rejected; performing fresh Gigya login")
            self._login_token = None
            self._id_token = None
            await self._gigya_login()
            return await self._exchange_login_token_for_access_token()

    async def _refresh_access_token(self) -> str:
        """Use a stored refresh_token to get a new access_token (skips steps 1-3)."""
        token_data = await self._request(
            "POST",
            "https://oauth.hijuconn.com/oauth/token",
            form={
                "client_id": _OAUTH_CLIENT_ID,
                "client_secret": _OAUTH_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
        )
        access_token = token_data.get("access_token")
        if not access_token:
            raise LegacyConnectLifeAuthError(
                f"Failed to refresh access token. Response: {json.dumps(token_data, indent=4)}"
            )
        if new_refresh := token_data.get("refresh_token"):
            self._refresh_token = new_refresh
        return access_token

    async def _exchange_login_token_for_access_token(self) -> str:
        """Steps 2-4: login_token → JWT → OAuth code → access_token."""
        # Step 2: Get JWT — skip if we have a cached id_token that's still valid.
        if not self._id_token or time.monotonic() >= self._id_token_expires_at:
            jwt_data = await self._request(
                "POST",
                "https://accounts.eu1.gigya.com/accounts.getJWT",
                form={
                    "APIKey": _GIGYA_API_KEY,
                    "gmid": _GIGYA_GMID,
                    "login_token": self._login_token,
                },
            )
            id_token = jwt_data.get("id_token")
            if not id_token:
                raise LegacyConnectLifeAuthError(
                    f"Failed to get JWT. Response: {json.dumps(jwt_data, indent=4)}"
                )
            self._id_token = id_token
            self._id_token_expires_at = _jwt_monotonic_expiry(id_token)
            _LOGGER.debug(
                "id_token cached, expires in %.0fs",
                self._id_token_expires_at - time.monotonic(),
            )
        else:
            _LOGGER.debug("Reusing cached id_token")

        # Step 3: Exchange JWT for OAuth authorization code
        auth_data = await self._request(
            "POST",
            "https://oauth.hijuconn.com/oauth/authorize",
            json_body={
                "client_id": _OAUTH_CLIENT_ID,
                "idToken": self._id_token,
                "response_type": "code",
                "redirect_uri": _OAUTH_REDIRECT_URI,
                "thirdType": "CDC",
                "thirdClientId": self._uid,
            },
        )
        code = auth_data.get("code")
        if not code:
            # id_token may have been rejected; clear it so next attempt fetches a fresh one.
            self._id_token = None
            raise LegacyConnectLifeAuthError(
                f"Failed to get OAuth code. Response: {json.dumps(auth_data, indent=4)}"
            )

        # Step 4: Exchange code for access token
        token_data = await self._request(
            "POST",
            "https://oauth.hijuconn.com/oauth/token",
            form={
                "client_id": _OAUTH_CLIENT_ID,
                "code": code,
                "grant_type": "authorization_code",
                "client_secret": _OAUTH_CLIENT_SECRET,
                "redirect_uri": _OAUTH_REDIRECT_URI,
            },
        )
        access_token = token_data.get("access_token")
        if not access_token:
            raise LegacyConnectLifeAuthError(
                f"Failed to get access token. Response: {json.dumps(token_data, indent=4)}"
            )
        if refresh_token := token_data.get("refresh_token"):
            self._refresh_token = refresh_token
            _LOGGER.debug("refresh_token stored")
        return access_token

    # ------------------------------------------------------------------
    # Request signing
    # ------------------------------------------------------------------

    def _sign(self, data: dict[str, Any]) -> str:
        sorted_items = sorted((k, v) for k, v in data.items() if k != "sign")
        parts = []
        for k, v in sorted_items:
            if isinstance(v, (dict, list)):
                v = json.dumps(v, separators=(",", ":"))
            parts.append(f"{k}={v}")
        to_hash = "&".join(parts) + _SIGN_MAGIC
        digest = hashlib.sha256(to_hash.encode()).digest()
        encrypted = self._public_key.encrypt(digest, padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    def _common_params(self) -> dict[str, Any]:
        return {
            "appId": _APP_ID,
            "appSecret": _APP_SECRET,
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
            f"{_BASE_URL}/clife-svc/pu/get_device_status_list",
            params=params,
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
            if puid:
                self._last_status[puid] = dict(device.get("statusList", {}))

        return devices

    async def get_online_ac_devices(self) -> list[dict[str, Any]]:
        """Return only online AC devices."""
        all_devices = await self.get_devices()
        result = []
        for device in all_devices:
            if device.get("offlineState") == 0:
                continue
            if device.get("deviceTypeCode") not in _AC_DEVICE_TYPE_CODES:
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
            "POST", f"{_BASE_URL}/clife-svc/pu/air_duct_energy", json_body=payload
        )
        return body.get("response", {})

    async def update_device(
        self, device_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        token = await self._ensure_token()
        # The gateway expects property values as strings (e.g. {"t_temp": "75"}),
        # not JSON numbers/booleans — sending raw ints causes "Signature check
        # fail" even though the signing algorithm itself is otherwise correct.
        properties = {k: str(v) for k, v in properties.items()}
        payload = self._common_params() | {
            "accessToken": token,
            "puid": device_id,
            "properties": properties,
        }
        payload["sign"] = self._sign(payload)
        body = await self._request(
            "POST", f"{_BASE_URL}/device/pu/property/set", json_body=payload
        )
        result = body.get("response", {})
        _LOGGER.debug("Update device result: %s", result)
        return result

    async def validate_credentials(self) -> bool:
        try:
            self._access_token = None
            self._token_expires_at = 0.0
            await self._ensure_token()
            _LOGGER.debug("Credential validation succeeded for %s", self._username)
            return True
        except LegacyConnectLifeAuthError:
            _LOGGER.debug("Credential validation failed for %s", self._username)
            return False

