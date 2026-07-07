"""Switch platform for ConnectLife — independent toggles ConnectLife-only features."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .climate import _build_full_properties
from .const import DOMAIN
from .coordinator import ConnectLifeCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)


@dataclass
class _ToggleDef:
    key: str
    name: str
    icon: str


_TOGGLE_SWITCHES = [
    _ToggleDef("t_fan_mute", "Fan Mute", "mdi:fan-off"),
    _ToggleDef("t_sleep", "Sleep", "mdi:power-sleep"),
    _ToggleDef("t_super", "Boost", "mdi:rocket-launch"),
    _ToggleDef("t_up_down", "Vertical Swing", "mdi:arrow-up-down"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ConnectLife toggle switches for each device."""
    coordinator: ConnectLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ConnectLifeToggleSwitch(coordinator, puid, device, td)
        for puid, device in coordinator.data.items()
        for td in _TOGGLE_SWITCHES
    )


class ConnectLifeToggleSwitch(CoordinatorEntity[ConnectLifeCoordinator], SwitchEntity):
    """A single boolean ConnectLife property exposed as a switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        td: _ToggleDef,
    ) -> None:
        super().__init__(coordinator)
        self._puid = puid
        self._key = td.key
        self._optimistic_is_on: bool | None = None
        self._attr_unique_id = f"{puid}_{td.key}"
        self._attr_name = td.name
        self._attr_icon = td.icon
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._puid in self.coordinator.data
        )

    def _status(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._puid, {}).get("statusList", {})

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._optimistic_is_on is not None:
            _LOGGER.debug(
                "[%s] %s: coordinator update, clearing optimistic state",
                self._puid,
                self._key,
            )
        self._optimistic_is_on = None
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        val = self._status().get(self._key)
        if val is None:
            return None
        return str(val) == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        _LOGGER.debug("[%s] %s: async_turn_%s()", self._puid, self._key, "on" if value else "off")
        self._optimistic_is_on = value
        self.async_write_ha_state()
        # Send the full current property set, not just this one key —
        # ConnectLife's API can silently drop a bare single-property update.
        props = _build_full_properties(self._status(), {self._key: 1 if value else 0})
        _LOGGER.debug("[%s] Sending update to ConnectLife: %s", self._puid, props)
        await self.coordinator.api.update_device(self._puid, props)
        await self.coordinator.async_request_refresh()
