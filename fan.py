"""Fan platform for ConnectLife — cycle AC fan speed independently of climate mode."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import entry_config
from .climate import _build_fan_options, _build_full_properties, _get_device_config
from .const import CONF_BEEPING, CONF_DEVICES_CONFIG, DOMAIN
from .coordinator import ConnectLifeCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)

# Not a discrete speed step — kept as a separate preset instead of a percentage.
_AUTO_SLUG = "auto"
# Explicit "no preset" option — selecting it (or a manual speed) leaves auto.
_NONE_SLUG = "none"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a ConnectLife fan-speed entity for each device."""
    coordinator: ConnectLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    cfg = entry_config(entry)
    devices_config_raw = cfg.get(CONF_DEVICES_CONFIG, "{}")
    try:
        devices_config = json.loads(devices_config_raw)
    except json.JSONDecodeError:
        _LOGGER.warning("Invalid devices_config JSON, using defaults")
        devices_config = {}
    beeping = cfg.get(CONF_BEEPING, False)

    entities = []
    for puid, device in coordinator.data.items():
        feature_code = device.get("deviceFeatureCode", "")
        device_config = _get_device_config(devices_config, feature_code)
        fan_options = _build_fan_options(device_config)
        if fan_options:
            entities.append(
                ConnectLifeFan(coordinator, puid, device, fan_options, beeping)
            )
    async_add_entities(entities)


class ConnectLifeFan(CoordinatorEntity[ConnectLifeCoordinator], FanEntity):
    """Represents a ConnectLife AC's fan speed as a standalone fan entity.

    Named speeds (e.g. low/medium/high) map to ordered percentage steps —
    the same UI Home Assistant uses for any fan with a small speed_count.
    "auto" isn't a fixed speed, so it's exposed as a preset mode instead.
    """

    _attr_has_entity_name = True
    _attr_name = "Fan"

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        fan_options: dict[str, str],
        beeping: bool = False,
    ) -> None:
        super().__init__(coordinator)
        self._puid = puid
        self._fan_options = fan_options
        self._beeping = beeping
        self._attr_unique_id = f"{puid}_fan"
        self._attr_device_info = _device_info(device, puid, DOMAIN)
        # ConnectLife's cloud can take a long time (observed up to 60-90s) to
        # reflect a command in its own polled state — track our own desired
        # state locally so the entity shows it instantly instead of stale
        # data until the coordinator eventually catches up.
        self._optimistic_power: bool | None = None
        self._optimistic_speed_name: str | None = None

        self._named_speeds = [name for name in fan_options if name != _AUTO_SLUG]

        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._named_speeds:
            features |= FanEntityFeature.SET_SPEED
            self._attr_speed_count = len(self._named_speeds)
        if _AUTO_SLUG in fan_options:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = [_NONE_SLUG, _AUTO_SLUG]
        self._attr_supported_features = features

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
        self._optimistic_power = None
        self._optimistic_speed_name = None
        super()._handle_coordinator_update()

    def _current_speed_name(self) -> str | None:
        if self._optimistic_speed_name is not None:
            return self._optimistic_speed_name
        fan_val = str(self._status().get("t_fan_speed", ""))
        for name, api_val in self._fan_options.items():
            if api_val == fan_val:
                return name
        return None

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_power is not None:
            return self._optimistic_power
        val = self._status().get("t_power")
        if val is None:
            return None
        return str(val) == "1"

    @property
    def percentage(self) -> int | None:
        name = self._current_speed_name()
        if name is None or name not in self._named_speeds:
            return None
        return ordered_list_item_to_percentage(self._named_speeds, name)

    @property
    def preset_mode(self) -> str | None:
        if _AUTO_SLUG not in self._fan_options:
            return None
        name = self._current_speed_name()
        return _AUTO_SLUG if name == _AUTO_SLUG else _NONE_SLUG

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        _LOGGER.debug(
            "[%s] async_turn_on(percentage=%r, preset_mode=%r)",
            self._puid,
            percentage,
            preset_mode,
        )
        overrides: dict[str, Any] = {"t_power": 1}
        self._optimistic_power = True
        if preset_mode is not None:
            if preset_mode == _NONE_SLUG:
                fan_name = self._named_speeds[0] if self._named_speeds else None
            else:
                fan_name = preset_mode
            fan_val = self._fan_options.get(fan_name) if fan_name else None
            if fan_val is not None:
                overrides["t_fan_speed"] = int(fan_val)
                self._optimistic_speed_name = fan_name
        elif percentage is not None:
            name = percentage_to_ordered_list_item(self._named_speeds, percentage)
            fan_val = self._fan_options.get(name)
            if fan_val is not None:
                overrides["t_fan_speed"] = int(fan_val)
                self._optimistic_speed_name = name
        self.async_write_ha_state()
        await self._async_update(overrides)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug("[%s] async_turn_off()", self._puid)
        self._optimistic_power = False
        self.async_write_ha_state()
        await self._async_update({"t_power": 0})

    async def async_set_percentage(self, percentage: int) -> None:
        _LOGGER.debug("[%s] async_set_percentage(%r)", self._puid, percentage)
        if percentage == 0:
            await self.async_turn_off()
            return
        name = percentage_to_ordered_list_item(self._named_speeds, percentage)
        fan_val = self._fan_options.get(name)
        if fan_val is None:
            _LOGGER.warning("Unknown fan speed for percentage %s", percentage)
            return
        self._optimistic_power = True
        self._optimistic_speed_name = name
        self.async_write_ha_state()
        await self._async_update({"t_power": 1, "t_fan_speed": int(fan_val)})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        _LOGGER.debug("[%s] async_set_preset_mode(%r)", self._puid, preset_mode)
        if preset_mode == _NONE_SLUG:
            # "None" isn't a real speed — leave auto for the lowest manual
            # speed instead of a no-op.
            if not self._named_speeds:
                _LOGGER.warning("No named speeds available to fall back to from 'none'")
                return
            fan_name = self._named_speeds[0]
        else:
            fan_name = preset_mode
        fan_val = self._fan_options.get(fan_name)
        if fan_val is None:
            _LOGGER.warning("Unknown fan preset mode: %s", preset_mode)
            return
        self._optimistic_power = True
        self._optimistic_speed_name = fan_name
        self.async_write_ha_state()
        await self._async_update({"t_power": 1, "t_fan_speed": int(fan_val)})

    async def _async_update(self, overrides: dict[str, Any]) -> None:
        # Send the full current property set, not just the changed keys —
        # ConnectLife's API can silently drop a bare partial update.
        props = _build_full_properties(self._status(), overrides, beeping=self._beeping)
        _LOGGER.debug("[%s] Sending update to ConnectLife: %s", self._puid, props)
        await self.coordinator.api.update_device(self._puid, props)
        await self.coordinator.async_request_refresh()
