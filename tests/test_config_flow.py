"""End-to-end tests for the ConnectLife config flow against the test gateway."""

from __future__ import annotations

import connectlife.test_server as cl_test_server
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.connectlife.const import DOMAIN


async def test_user_flow_success(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """A valid login completes the flow and creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "hunter2"},
    )
    await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"][CONF_USERNAME] == "user@example.com"
    assert result["data"][CONF_PASSWORD] == "hunter2"


async def test_user_flow_invalid_auth(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """A rejected login re-shows the form with an invalid_auth error."""
    cl_test_server.auth_error_rate = 100
    cl_test_server.auth_error_type = "invalid_login"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "wrong"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_duplicate_username_aborts(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """A second entry for the same (lowercased) username is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "hunter2"},
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "USER@example.com", CONF_PASSWORD: "hunter2"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"
