"""Shared helpers for building fake ConnectLife gateway payloads in tests."""

from __future__ import annotations

from typing import Any


def make_appliance(
    puid: str,
    *,
    device_id: str | None = None,
    nickname: str = "Living Room AC",
    device_type_code: str = "009",
    device_feature_code: str = "117",
    offline_state: int = 1,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a raw device payload matching what the ConnectLife gateway returns.

    Mirrors the shape `connectlife.test_server`'s `get_device_status_list`
    route serves (see connectlife.appliance.ConnectLifeAppliance for the
    fields it requires) and what this integration's coordinator/platforms
    read straight off `statusList`.
    """
    default_status = {
        "t_power": "1",
        "t_work_mode": "2",  # cool
        "t_temp": "22",
        "t_temp_type": "0",  # celsius
        "t_fan_speed": "0",  # auto
        "f_temp_in": "24",
        "t_sleep": "0",
        "t_super": "0",
        "t_fan_mute": "0",
        "t_up_down": "0",
        # Write-only on real devices (never echoed back in statusList — see
        # api.py's _DIFF_IGNORE_KEYS), but every command payload includes it
        # unconditionally, and the test gateway rejects writes to properties
        # missing from the appliance's statusList — so it has to be seeded.
        "t_beep": "0",
    }
    if status:
        default_status.update(status)

    return {
        "wifiId": f"wifi-{puid}",
        "deviceId": device_id or f"dev-{puid}",
        "puid": puid,
        "deviceNickName": nickname,
        "deviceFeatureCode": device_feature_code,
        "deviceFeatureName": "AC",
        "deviceTypeCode": device_type_code,
        "deviceTypeName": "Split AC",
        "role": 1,
        "roomId": 1,
        "roomName": "Living Room",
        "offlineState": offline_state,
        "seq": 1,
        "bindTime": None,
        "useTime": None,
        "createTime": None,
        "statusList": default_status,
    }
