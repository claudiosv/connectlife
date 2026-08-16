"""Tests for custom_components/claudio_hisense/api.py."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from unittest.mock import AsyncMock

import pytest

from custom_components.claudio_hisense.api import (
    ConnectLifeApi,
    ConnectLifeApiError,
    ConnectLifeAuthError,
)

from .conftest import FakeOAuthSession


def _make_api(fake_oauth_session: FakeOAuthSession) -> ConnectLifeApi:
    return ConnectLifeApi(oauth_session=fake_oauth_session)


# ---------------------------------------------------------------------------
# Signing helpers (pure functions — no network/hass needed)
# ---------------------------------------------------------------------------


def test_sign_hmac_matches_manual_computation() -> None:
    secret = "s3cret"
    base_string = "client\nGET /path\ndate: X\nhi-params-encrypt: client\n"
    expected = base64.b64encode(
        hmac.new(secret.encode(), base_string.encode(), hashlib.sha256).digest()
    ).decode()
    assert ConnectLifeApi._sign_hmac(secret, base_string) == expected


def test_sign_hmac_is_deterministic_and_sensitive_to_input() -> None:
    sig1 = ConnectLifeApi._sign_hmac("secret", "message")
    sig2 = ConnectLifeApi._sign_hmac("secret", "message")
    sig3 = ConnectLifeApi._sign_hmac("secret", "different message")
    assert sig1 == sig2
    assert sig1 != sig3


def test_body_digest_empty_body_uses_fixed_constant() -> None:
    empty_digest = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
    assert ConnectLifeApi._body_digest(None) == empty_digest
    assert ConnectLifeApi._body_digest("") == empty_digest


def test_body_digest_nonempty_body_matches_sha256() -> None:
    body = '{"puid":"pu1"}'
    expected = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert ConnectLifeApi._body_digest(body) == expected


def test_gmt_date_format() -> None:
    result = ConnectLifeApi._gmt_date()
    assert re.match(
        r"^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$", result
    )


def test_path_for_signing_strips_scheme_and_host() -> None:
    url = "https://juapi-3rd.hijuconn.com/clife-svc/pu/get_device_status_list?a=1&b=2"
    assert (
        ConnectLifeApi._path_for_signing(url)
        == "/clife-svc/pu/get_device_status_list?a=1&b=2"
    )


def test_path_for_signing_no_query_string() -> None:
    url = "https://juapi-3rd.hijuconn.com/device/pu/property/set"
    assert ConnectLifeApi._path_for_signing(url) == "/device/pu/property/set"


# ---------------------------------------------------------------------------
# _api_request: token handling, error surfacing
# ---------------------------------------------------------------------------


async def test_api_request_ensures_token_before_request(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._request = AsyncMock(return_value={"resultCode": 0})  # type: ignore[method-assign]
    await api._api_request("GET", "/some/path")
    assert fake_oauth_session.ensure_token_valid_calls == 1


async def test_api_request_wraps_token_valueerror_as_autherror(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    fake_oauth_session.async_ensure_token_valid = AsyncMock(  # type: ignore[method-assign]
        side_effect=ValueError("no token available")
    )
    api = _make_api(fake_oauth_session)
    with pytest.raises(ConnectLifeAuthError):
        await api._api_request("GET", "/some/path")


async def test_api_request_raises_with_error_desc(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    """Regression: the server's error field is errorDesc, not msg.

    Before this was fixed, a rejected command (e.g. "Device offline") always
    surfaced as the useless "API error: Unknown error".
    """
    api = _make_api(fake_oauth_session)
    api._request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "resultCode": 1,
            "errorCode": 5,
            "errorDesc": "Device offline",
            "kvMap": None,
        }
    )
    with pytest.raises(ConnectLifeApiError, match="Device offline"):
        await api._api_request("POST", "/device/pu/property/set", data={})


async def test_api_request_success_passes_through_response(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._request = AsyncMock(  # type: ignore[method-assign]
        return_value={"resultCode": 0, "deviceList": []}
    )
    result = await api._api_request("GET", "/clife-svc/pu/get_device_status_list")
    assert result == {"resultCode": 0, "deviceList": []}


async def test_api_request_get_omits_body_and_signs_headers(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._request = AsyncMock(return_value={"resultCode": 0})  # type: ignore[method-assign]
    await api._api_request("GET", "/clife-svc/pu/get_device_status_list")

    call = api._request.call_args
    method, url = call.args
    assert method == "GET"
    assert url.startswith(
        "https://juapi-3rd.hijuconn.com/clife-svc/pu/get_device_status_list?"
    )
    assert call.kwargs["data"] is None
    headers = call.kwargs["headers"]
    assert headers["accessToken"] == "test-access-token"
    assert headers["Authorization"].startswith('Signature signature="')
    assert "hi-params-encrypt" in headers
    assert headers["Digest"] == "SHA-256=47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="


async def test_api_request_post_includes_json_body(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._request = AsyncMock(return_value={"resultCode": 0})  # type: ignore[method-assign]
    await api._api_request(
        "POST", "/device/pu/property/set", data={"puid": "pu1", "properties": {}}
    )

    call = api._request.call_args
    method, url = call.args
    assert method == "POST"
    assert url == "https://juapi-3rd.hijuconn.com/device/pu/property/set"
    body = call.kwargs["data"]
    assert body is not None
    assert '"puid": "pu1"' in body or '"puid":"pu1"' in body


# ---------------------------------------------------------------------------
# get_devices() / get_online_ac_devices() / update_device() response shape
# ---------------------------------------------------------------------------


async def test_get_devices_reads_top_level_device_list(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    """Regression: the server returns deviceList at the top level, not
    nested under a "response" key — get_devices() used to look in the wrong
    place and silently return [] despite a valid response.
    """
    api = _make_api(fake_oauth_session)
    device = {"puid": "pu1", "statusList": {"t_power": "1"}}
    api._api_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"resultCode": 0, "deviceList": [device]}
    )
    devices = await api.get_devices()
    assert devices == [device]


async def test_get_devices_missing_device_list_returns_empty(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._api_request = AsyncMock(return_value={"resultCode": 0})  # type: ignore[method-assign]
    assert await api.get_devices() == []


async def test_get_devices_null_device_list_returns_empty(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._api_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"resultCode": 0, "deviceList": None}
    )
    assert await api.get_devices() == []


async def test_get_online_ac_devices_filters_offline_and_unsupported(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    online_ac = {"puid": "pu1", "offlineState": 1, "deviceTypeCode": "009"}
    offline_ac = {"puid": "pu2", "offlineState": 0, "deviceTypeCode": "009"}
    unsupported = {"puid": "pu3", "offlineState": 1, "deviceTypeCode": "999"}
    api._api_request = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "resultCode": 0,
            "deviceList": [online_ac, offline_ac, unsupported],
        }
    )
    result = await api.get_online_ac_devices()
    assert result == [online_ac]


async def test_update_device_extracts_kv_map(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    """Regression: update_device used to read a nonexistent "response" key
    instead of the top-level "kvMap".
    """
    api = _make_api(fake_oauth_session)
    api._api_request = AsyncMock(  # type: ignore[method-assign]
        return_value={"resultCode": 0, "kvMap": {"t_temp": "77"}}
    )
    result = await api.update_device("pu1", {"t_temp": 77})
    assert result == {"t_temp": "77"}


async def test_update_device_falls_back_to_full_body_without_kv_map(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    body = {"resultCode": 0}
    api._api_request = AsyncMock(return_value=body)  # type: ignore[method-assign]
    result = await api.update_device("pu1", {"t_temp": 77})
    assert result == body


async def test_update_device_stringifies_property_values(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._api_request = AsyncMock(return_value={"resultCode": 0})  # type: ignore[method-assign]
    await api.update_device("pu1", {"t_temp": 77, "t_power": 1})
    sent_data = api._api_request.call_args.kwargs["data"]
    assert sent_data["properties"] == {"t_temp": "77", "t_power": "1"}


async def test_update_device_propagates_api_error(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    api._api_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=ConnectLifeApiError("API error: Device offline")
    )
    with pytest.raises(ConnectLifeApiError):
        await api.update_device("pu1", {"t_temp": 77})


# ---------------------------------------------------------------------------
# validate_credentials()
# ---------------------------------------------------------------------------


async def test_validate_credentials_true_when_token_valid(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    api = _make_api(fake_oauth_session)
    assert await api.validate_credentials() is True


async def test_validate_credentials_false_on_auth_error(
    fake_oauth_session: FakeOAuthSession,
) -> None:
    fake_oauth_session.async_ensure_token_valid = AsyncMock(  # type: ignore[method-assign]
        side_effect=ValueError("no token")
    )
    api = _make_api(fake_oauth_session)
    assert await api.validate_credentials() is False
