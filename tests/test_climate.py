"""Tests for custom_components/claudio_hisense/climate.py."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.claudio_hisense.api import (
    ConnectLifeApiError,
    ConnectLifeAuthError,
)
from custom_components.claudio_hisense.climate import _async_update_device_safe


async def test_update_device_safe_returns_true_on_success() -> None:
    api = AsyncMock()
    api.update_device = AsyncMock(return_value={"t_temp": "77"})

    result = await _async_update_device_safe(api, "pu1", {"t_temp": "77"})

    assert result is True
    api.update_device.assert_awaited_once_with("pu1", {"t_temp": "77"})


async def test_update_device_safe_swallows_api_error() -> None:
    """Regression: a rejected command (e.g. "Device offline") used to
    propagate ConnectLifeApiError straight out of service-call handlers and
    fire-and-forget background tasks.
    """
    api = AsyncMock()
    api.update_device = AsyncMock(
        side_effect=ConnectLifeApiError("API error: Device offline")
    )

    result = await _async_update_device_safe(api, "pu1", {"t_temp": "77"})

    assert result is False


async def test_update_device_safe_swallows_auth_error() -> None:
    api = AsyncMock()
    api.update_device = AsyncMock(side_effect=ConnectLifeAuthError("no token"))

    result = await _async_update_device_safe(api, "pu1", {"t_temp": "77"})

    assert result is False


async def test_update_device_safe_swallows_unexpected_exception() -> None:
    api = AsyncMock()
    api.update_device = AsyncMock(side_effect=RuntimeError("boom"))

    result = await _async_update_device_safe(api, "pu1", {"t_temp": "77"})

    assert result is False
