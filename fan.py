"""Fan platform for ConnectLife — cycle AC fan speed independently of climate mode."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from . import entry_config
from .climate import _build_fan_options, _build_full_properties, _get_device_config
from .const import CONF_DEVICES_CONFIG, DOMAIN
from .coordinator import ConnectLifeCoordinator
from .sensor import _device_info

_LOGGER = logging.getLogger(__name__)

# Not a discrete speed step — kept as a separate preset instead of a percentage.
_AUTO_SLUG = "auto"


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

    entities = []
    for puid, device in coordinator.data.items():
        feature_code = device.get("deviceFeatureCode", "")
        device_config = _get_device_config(devices_config, feature_code)
        fan_options = _build_fan_options(device_config)
        if fan_options:
            entities.append(ConnectLifeFan(coordinator, puid, device, fan_options))
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
    ) -> None:
        super().__init__(coordinator)
        self._puid = puid
        self._fan_options = fan_options
        self._attr_unique_id = f"{puid}_fan"
        self._attr_device_info = _device_info(device, puid, DOMAIN)

        self._named_speeds = [name for name in fan_options if name != _AUTO_SLUG]

        features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        if self._named_speeds:
            features |= FanEntityFeature.SET_SPEED
            self._attr_speed_count = len(self._named_speeds)
        if _AUTO_SLUG in fan_options:
            features |= FanEntityFeature.PRESET_MODE
            self._attr_preset_modes = [_AUTO_SLUG]
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

    def _current_speed_name(self) -> str | None:
        fan_val = str(self._status().get("t_fan_speed", ""))
        for name, api_val in self._fan_options.items():
            if api_val == fan_val:
                return name
        return None

    @property
    def is_on(self) -> bool | None:
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
        name = self._current_speed_name()
        return name if name == _AUTO_SLUG else None

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
        if preset_mode is not None:
            fan_val = self._fan_options.get(preset_mode)
            if fan_val is not None:
                overrides["t_fan_speed"] = int(fan_val)
        elif percentage is not None:
            name = percentage_to_ordered_list_item(self._named_speeds, percentage)
            fan_val = self._fan_options.get(name)
            if fan_val is not None:
                overrides["t_fan_speed"] = int(fan_val)
        await self._async_update(overrides)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug("[%s] async_turn_off()", self._puid)
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
        await self._async_update({"t_power": 1, "t_fan_speed": int(fan_val)})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        _LOGGER.debug("[%s] async_set_preset_mode(%r)", self._puid, preset_mode)
        fan_val = self._fan_options.get(preset_mode)
        if fan_val is None:
            _LOGGER.warning("Unknown fan preset mode: %s", preset_mode)
            return
        await self._async_update({"t_power": 1, "t_fan_speed": int(fan_val)})

    async def _async_update(self, overrides: dict[str, Any]) -> None:
        # Send the full current property set, not just the changed keys —
        # ConnectLife's API can silently drop a bare partial update.
        props = _build_full_properties(self._status(), overrides)
        _LOGGER.debug("[%s] Sending update to ConnectLife: %s", self._puid, props)
        await self.coordinator.api.update_device(self._puid, props)
        await self.coordinator.async_request_refresh()
