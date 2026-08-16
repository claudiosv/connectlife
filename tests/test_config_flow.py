"""Tests for custom_components/claudio_hisense/config_flow.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.claudio_hisense.const import (
    CONF_BEEPING,
    CONF_DEVICES_CONFIG,
    CONF_TEMPERATURE_SENSORS,
    CONF_TEMPERATURE_UNIT,
    DOMAIN,
)


def _entry_with_token(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ConnectLife",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": 9999999999,
            },
            "oauth_redirect_uri": "http://homeassistant.local:8123/auth/external/callback",
        },
        options={},
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_init_shows_target_picker(hass: HomeAssistant) -> None:
    entry = _entry_with_token(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_general_step_does_not_leak_oauth_token_into_options(
    hass: HomeAssistant,
) -> None:
    """Regression: async_step_general/async_step_device used to spread
    entry.data (which includes the OAuth token) into the new entry.options
    payload. Since entry_config() prefers options over data, that would
    have permanently shadowed every future token refresh (which is written
    to entry.data) with the stale, point-in-time token copied into options.
    """
    entry = _entry_with_token(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"target": "_general"}
    )
    assert result["step_id"] == "general"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BEEPING: True,
            CONF_TEMPERATURE_UNIT: "celsius",
            CONF_TEMPERATURE_SENSORS: False,
            CONF_DEVICES_CONFIG: "{}",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "token" not in result["data"]
    assert "token" not in entry.options
    # entry.data (where the real, live token lives) is untouched.
    assert entry.data["token"]["access_token"] == "secret-access-token"


async def test_options_general_step_invalid_json_shows_error(
    hass: HomeAssistant,
) -> None:
    entry = _entry_with_token(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"target": "_general"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BEEPING: False,
            CONF_TEMPERATURE_UNIT: "celsius",
            CONF_TEMPERATURE_SENSORS: False,
            CONF_DEVICES_CONFIG: "not valid json",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DEVICES_CONFIG: "invalid_json"}


async def test_options_device_target_only_offered_for_known_devices(
    hass: HomeAssistant,
) -> None:
    """No coordinator/devices registered for this entry (not set up) — the
    picker should only offer the general-settings target.
    """
    entry = _entry_with_token(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    schema = result["data_schema"].schema
    target_key = next(k for k in schema if str(k) == "target")
    assert list(schema[target_key].container) == ["_general"]
