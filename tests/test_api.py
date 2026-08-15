"""End-to-end tests for custom_components.connectlife.api against the test gateway.

Unlike test_integration_e2e.py, these drive ConnectLifeApi directly (no
Home Assistant core involved) to pin down its retry/backoff and error-
translation behavior, which coordinator.py and config_flow.py depend on.
"""

from __future__ import annotations

import asyncio

import connectlife.test_server as cl_test_server
import pytest

from custom_components.connectlife.api import (
    ConnectLifeApi,
    ConnectLifeAuthError,
    ConnectLifeRateLimitError,
)

from .helpers import make_appliance

PUID = "test-puid-1"


@pytest.fixture
def api(connectlife_gateway: str) -> ConnectLifeApi:
    return ConnectLifeApi(
        "user@example.com", "hunter2", test_server=connectlife_gateway
    )


async def test_validate_credentials_success(api: ConnectLifeApi) -> None:
    assert await api.validate_credentials() is True


async def test_validate_credentials_failure(api: ConnectLifeApi) -> None:
    cl_test_server.auth_error_rate = 100
    assert await api.validate_credentials() is False


async def test_get_devices_and_update_device_round_trip(api: ConnectLifeApi) -> None:
    cl_test_server.appliances[PUID] = make_appliance(PUID)

    devices = await api.get_devices()
    assert len(devices) == 1
    assert devices[0]["puid"] == PUID
    assert devices[0]["statusList"]["t_temp"] == "22"

    await api.update_device(PUID, {"t_temp": 25})
    assert cl_test_server.appliances[PUID]["statusList"]["t_temp"] == "25"

    devices = await api.get_devices()
    assert devices[0]["statusList"]["t_temp"] == "25"


async def test_get_online_ac_devices_filters_offline_and_non_ac(
    api: ConnectLifeApi,
) -> None:
    cl_test_server.appliances["online-ac"] = make_appliance(
        "online-ac", offline_state=1, device_type_code="009"
    )
    cl_test_server.appliances["offline-ac"] = make_appliance(
        "offline-ac", offline_state=0, device_type_code="009"
    )
    cl_test_server.appliances["online-other"] = make_appliance(
        "online-other", offline_state=1, device_type_code="003"
    )

    online = await api.get_online_ac_devices()
    assert [d["puid"] for d in online] == ["online-ac"]


async def test_transient_gateway_error_is_retried(api: ConnectLifeApi) -> None:
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    cl_test_server.failure_rate = 100

    async def _recover_shortly() -> None:
        await asyncio.sleep(0.2)
        cl_test_server.failure_rate = 0

    recovery_task = asyncio.create_task(_recover_shortly())
    try:
        devices = await api.get_devices()
    finally:
        await recovery_task
    assert len(devices) == 1


async def test_permanent_gateway_error_raises_rate_limit_after_retries(
    api: ConnectLifeApi,
) -> None:
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    cl_test_server.failure_rate = 100

    with pytest.raises(ConnectLifeRateLimitError):
        await api.get_devices()


async def test_invalid_credentials_raise_auth_error_from_update_device(
    api: ConnectLifeApi,
) -> None:
    cl_test_server.auth_error_rate = 100

    with pytest.raises(ConnectLifeAuthError):
        await api.update_device(PUID, {"t_temp": 20})
