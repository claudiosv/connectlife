"""End-to-end tests: config entry -> coordinator -> platforms -> gateway.

Boots this integration inside a real (in-process) Home Assistant core and
drives it entirely against `connectlife`'s bundled test gateway — no part of
the integration's own code is mocked, only the network boundary.
"""

from __future__ import annotations

import connectlife.test_server as cl_test_server
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.connectlife.const import (
    CONF_COMMAND_REFRESH_DELAY,
    CONF_DEBOUNCE_DELAY,
    CONF_POLL_INTERVAL,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
)

from .helpers import make_appliance

PUID = "test-puid-1"


def _make_entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "hunter2"},
        options={
            # Fast, deterministic timings for tests: no debounce wait, no
            # extra post-command re-poll delay, long poll interval so the
            # background coordinator timer never fires mid-test.
            CONF_DEBOUNCE_DELAY: 0,
            CONF_COMMAND_REFRESH_DELAY: 0,
            CONF_POLL_INTERVAL: 3600,
            # Opt in so the dedicated current/target temperature sensors get
            # created too (exercises that config option, off by default).
            CONF_TEMPERATURE_SENSORS: True,
            **options,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, (
        f"no {platform} entity registered for unique_id={unique_id!r}"
    )
    return entity_id


async def test_setup_creates_entities_across_all_platforms(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """One online AC device produces climate/fan/sensor/switch/button entities."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())

    climate_id = _entity_id(hass, "climate", PUID)
    fan_id = _entity_id(hass, "fan", f"{PUID}_fan")
    current_temp_id = _entity_id(hass, "sensor", f"{PUID}_current_temperature")
    target_temp_id = _entity_id(hass, "sensor", f"{PUID}_target_temperature")
    sleep_switch_id = _entity_id(hass, "switch", f"{PUID}_t_sleep")
    refresh_button_id = _entity_id(hass, "button", f"{PUID}_refresh")

    climate_state = hass.states.get(climate_id)
    assert climate_state is not None
    assert climate_state.state == "cool"
    assert climate_state.attributes["temperature"] == 22
    assert climate_state.attributes["current_temperature"] == 24

    assert hass.states.get(fan_id) is not None
    assert hass.states.get(current_temp_id).state == "24.0"
    assert hass.states.get(target_temp_id).state == "22.0"
    assert hass.states.get(sleep_switch_id).state == "off"
    assert hass.states.get(refresh_button_id) is not None


async def test_offline_device_excluded(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """Devices reported offline by the gateway don't get entities."""
    cl_test_server.appliances[PUID] = make_appliance(PUID, offline_state=0)
    await _setup(hass, _make_entry())

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("climate", DOMAIN, PUID) is None


async def test_set_temperature_round_trips_to_gateway(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """climate.set_temperature -> debounced update_device -> gateway state changes."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())
    climate_id = _entity_id(hass, "climate", PUID)

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "temperature": 19},
        blocking=True,
    )
    # Optimistic update happens synchronously...
    assert hass.states.get(climate_id).attributes["temperature"] == 19
    await hass.async_block_till_done()

    # ...and the debounced write (debounce_delay=0) has now reached the
    # gateway's own copy of the device's state.
    assert cl_test_server.appliances[PUID]["statusList"]["t_temp"] == "19"


async def test_set_hvac_mode_off_round_trips_to_gateway(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """climate.set_hvac_mode(off) turns the real (mock) device off."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())
    climate_id = _entity_id(hass, "climate", PUID)

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": climate_id, "hvac_mode": "off"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert cl_test_server.appliances[PUID]["statusList"]["t_power"] == "0"


async def test_switch_toggle_round_trips_to_gateway(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """switch.turn_on(sleep) reaches the gateway and the entity reflects it."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())
    sleep_switch_id = _entity_id(hass, "switch", f"{PUID}_t_sleep")

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": sleep_switch_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert cl_test_server.appliances[PUID]["statusList"]["t_sleep"] == "1"
    assert hass.states.get(sleep_switch_id).state == "on"


async def test_coordinator_refresh_picks_up_gateway_side_change(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """A change made directly on the mock gateway shows up after a refresh."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())
    climate_id = _entity_id(hass, "climate", PUID)
    assert hass.states.get(climate_id).attributes["temperature"] == 22

    # Simulate the device (or another client) changing state out-of-band.
    cl_test_server.appliances[PUID]["statusList"]["t_temp"] = "18"

    coordinator = hass.data[DOMAIN][
        next(iter(hass.config_entries.async_entries(DOMAIN))).entry_id
    ]
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(climate_id).attributes["temperature"] == 18


async def test_refresh_button_triggers_gateway_poll(
    hass: HomeAssistant,
    patch_connectlife_api: str,
    enable_custom_integrations: None,
) -> None:
    """Pressing the refresh button re-polls the (mock) gateway."""
    cl_test_server.appliances[PUID] = make_appliance(PUID)
    await _setup(hass, _make_entry())
    climate_id = _entity_id(hass, "climate", PUID)
    refresh_button_id = _entity_id(hass, "button", f"{PUID}_refresh")

    cl_test_server.appliances[PUID]["statusList"]["t_temp"] = "27"

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": refresh_button_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(climate_id).attributes["temperature"] == 27
