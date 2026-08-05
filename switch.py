"""Switch platform for ConnectLife — independent toggles ConnectLife-only features."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entry_config
from .climate import _build_full_properties
from .const import (
    COMMAND_REFRESH_DELAY_SECONDS,
    CONF_BEEPING,
    CONF_COMMAND_REFRESH_DELAY,
    DOMAIN,
)
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
    cfg = entry_config(entry)
    beeping = cfg.get(CONF_BEEPING, False)
    command_refresh_delay = int(
        cfg.get(CONF_COMMAND_REFRESH_DELAY, COMMAND_REFRESH_DELAY_SECONDS)
    )
    async_add_entities(
        ConnectLifeToggleSwitch(
            coordinator, puid, device, td, beeping, command_refresh_delay
        )
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
        beeping: bool = False,
        command_refresh_delay: int = COMMAND_REFRESH_DELAY_SECONDS,
    ) -> None:
        super().__init__(coordinator)
        self._puid = puid
        self._key = td.key
        self._beeping = beeping
        self._command_refresh_delay = command_refresh_delay
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
        # Mirror into the shared coordinator data too (same dict instance
        # every platform reads via coordinator.data[puid]["statusList"]) so
        # e.g. the climate entity's preset chip reflects a direct Sleep/
        # Boost/Fan Mute toggle immediately instead of staying stale until
        # the next poll confirms it.
        device = self.coordinator.data.get(self._puid) if self.coordinator.data else None
        if device is not None:
            device.setdefault("statusList", {})[self._key] = 1 if value else 0
        # Send the full current property set, not just this one key —
        # ConnectLife's API can silently drop a bare single-property update.
        props = _build_full_properties(
            self._status(),
            {self._key: 1 if value else 0},
            beeping=self._beeping,
        )
        _LOGGER.debug("[%s] Sending update to ConnectLife: %s", self._puid, props)
        await self.coordinator.api.update_device(self._puid, props)
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Schedule a coordinator refresh after a short delay, resetting the poll timer.

        ConnectLife's cloud needs time to actually apply a command — polling
        immediately just re-reads the pre-change state, same as climate.py's
        _schedule_refresh.
        """

        async def _refresh() -> None:
            await asyncio.sleep(self._command_refresh_delay)
            _LOGGER.debug(
                "[%s] %s: requesting coordinator refresh after %.1fs command delay",
                self._puid,
                self._key,
                self._command_refresh_delay,
            )
            await self.coordinator.async_request_refresh()

        self.hass.async_create_task(_refresh())
