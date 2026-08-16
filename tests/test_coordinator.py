"""Tests for custom_components/claudio_hisense/coordinator.py."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import homeassistant.components.persistent_notification as pn
import homeassistant.helpers.issue_registry as ir
from homeassistant.core import HomeAssistant

from custom_components.claudio_hisense.const import DOMAIN
from custom_components.claudio_hisense.coordinator import ConnectLifeCoordinator


def _make_coordinator(hass: HomeAssistant) -> ConnectLifeCoordinator:
    api = AsyncMock()
    return ConnectLifeCoordinator(hass, api, update_interval_seconds=60)


def _device(puid: str, **status: str) -> dict:
    return {
        "puid": puid,
        "deviceNickName": "Living Room AC",
        "offlineState": 1,
        "deviceTypeCode": "009",
        "statusList": {"t_power": "1", **status},
    }


# ---------------------------------------------------------------------------
# update_interval_seconds=None disables periodic polling
# ---------------------------------------------------------------------------


async def test_none_update_interval_disables_polling(hass: HomeAssistant) -> None:
    api = AsyncMock()
    coordinator = ConnectLifeCoordinator(hass, api, update_interval_seconds=None)
    assert coordinator.update_interval is None


async def test_int_update_interval_sets_timedelta(hass: HomeAssistant) -> None:
    api = AsyncMock()
    coordinator = ConnectLifeCoordinator(hass, api, update_interval_seconds=30)
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 30


# ---------------------------------------------------------------------------
# async_handle_push_update: merging device-status / wifi-status pushes
# ---------------------------------------------------------------------------


def _encode_status(status: dict) -> str:
    return base64.b64encode(json.dumps(status).encode()).decode()


async def test_push_update_merges_decoded_status_field(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1", t_temp="61")})

    message = {
        "msgTypeCode": "status_devicestatus",
        "content": json.dumps(
            {"puid": "pu1", "status": _encode_status({"t_temp": "77"})}
        ),
    }
    coordinator.async_handle_push_update(message)

    assert coordinator.data["pu1"]["statusList"]["t_temp"] == "77"
    # Existing keys not touched by this push are preserved.
    assert coordinator.data["pu1"]["statusList"]["t_power"] == "1"


async def test_push_update_merges_properties_field(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})

    message = {
        "msgTypeCode": "status_devicestatus",
        "content": json.dumps({"puid": "pu1", "properties": {"t_work_mode": "2"}}),
    }
    coordinator.async_handle_push_update(message)

    assert coordinator.data["pu1"]["statusList"]["t_work_mode"] == "2"


async def test_push_update_wifistatus_online_maps_to_offline_state_1(
    hass: HomeAssistant,
) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})
    coordinator.data["pu1"]["offlineState"] = 0

    message = {
        "msgTypeCode": "status_wifistatus",
        "content": json.dumps({"puid": "pu1", "onlinestats": 1}),
    }
    coordinator.async_handle_push_update(message)

    assert coordinator.data["pu1"]["offlineState"] == 1


async def test_push_update_wifistatus_offline_maps_to_offline_state_0(
    hass: HomeAssistant,
) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})

    message = {
        "msgTypeCode": "status_wifistatus",
        "content": json.dumps({"puid": "pu1", "onlinestats": 0}),
    }
    coordinator.async_handle_push_update(message)

    assert coordinator.data["pu1"]["offlineState"] == 0


async def test_push_update_unknown_device_is_ignored(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})

    message = {
        "msgTypeCode": "status_devicestatus",
        "content": json.dumps({"puid": "unknown-puid", "properties": {"t_power": "0"}}),
    }
    coordinator.async_handle_push_update(message)

    # Untouched — no KeyError, no new device added.
    assert set(coordinator.data) == {"pu1"}


async def test_push_update_ignores_unrecognized_msg_type(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})

    message = {"msgTypeCode": "something_else", "content": "{}"}
    coordinator.async_handle_push_update(message)

    assert coordinator.data["pu1"]["statusList"]["t_power"] == "1"


async def test_push_update_ignores_malformed_content(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator.async_set_updated_data({"pu1": _device("pu1")})

    message = {"msgTypeCode": "status_devicestatus", "content": "not json"}
    # Must not raise.
    coordinator.async_handle_push_update(message)
    assert coordinator.data["pu1"]["statusList"]["t_power"] == "1"


# ---------------------------------------------------------------------------
# _check_faults: repair issue + persistent notification lifecycle
# ---------------------------------------------------------------------------


async def test_fault_transition_creates_issue_and_notification(
    hass: HomeAssistant,
) -> None:
    coordinator = _make_coordinator(hass)
    data = {"pu1": _device("pu1", f_e_incoiltemp="1")}

    coordinator._check_faults(data)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp")
    assert issue is not None
    assert issue.translation_key == "device_fault"

    notifications = pn._async_get_or_create_notifications(hass)
    assert "claudio_hisense_pu1_f_e_incoiltemp" in notifications


async def test_fault_clears_issue_and_notification_on_recovery(
    hass: HomeAssistant,
) -> None:
    coordinator = _make_coordinator(hass)
    coordinator._check_faults({"pu1": _device("pu1", f_e_incoiltemp="1")})
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp") is not None
    )

    coordinator._check_faults({"pu1": _device("pu1", f_e_incoiltemp="0")})

    assert ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp") is None
    notifications = pn._async_get_or_create_notifications(hass)
    assert "claudio_hisense_pu1_f_e_incoiltemp" not in notifications


async def test_fault_already_active_is_not_recreated(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator._check_faults({"pu1": _device("pu1", f_e_incoiltemp="1")})
    first_issue = ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp")

    # Same fault reported again on a later poll/push — should be a no-op,
    # not a second create (which would be harmless but wasteful/noisy).
    coordinator._check_faults({"pu1": _device("pu1", f_e_incoiltemp="1")})
    second_issue = ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp")

    assert first_issue is second_issue


async def test_no_fault_creates_nothing(hass: HomeAssistant) -> None:
    coordinator = _make_coordinator(hass)
    coordinator._check_faults({"pu1": _device("pu1")})

    assert ir.async_get(hass).async_get_issue(DOMAIN, "pu1_f_e_incoiltemp") is None
