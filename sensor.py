"""Sensor platform for ConnectLife."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entry_config
from .const import (
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
    TEMP_CODE_FAHRENHEIT,
)
from .coordinator import ConnectLifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ConnectLife sensors from a config entry."""
    coordinator: ConnectLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    cfg = entry_config(entry)
    temp_sensors_enabled = cfg.get(CONF_TEMPERATURE_SENSORS, False)

    entities: list[SensorEntity] = []
    for puid, device in coordinator.data.items():
        # if "daily_energy_kwh" in device.get("statusList", {}):
        #     entities.append(ConnectLifeEnergySensor(coordinator, puid, device))

        if temp_sensors_enabled:
            entities.append(ConnectLifeCurrentTempSensor(coordinator, puid, device))
            entities.append(ConnectLifeTargetTempSensor(coordinator, puid, device))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _device_info(device: dict[str, Any], puid: str, domain: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(domain, puid)},
        name=device.get("deviceNickName", puid),
        manufacturer="ConnectLife",
        model=f"{device.get('deviceTypeCode', '')}-{device.get('deviceFeatureCode', '')}",
    )


def _temp_unit(status: dict[str, Any]) -> str:
    temp_type = str(status.get("t_temp_type", "0"))
    return (
        UnitOfTemperature.FAHRENHEIT
        if temp_type == TEMP_CODE_FAHRENHEIT
        else UnitOfTemperature.CELSIUS
    )


class _ConnectLifeBaseSensor(CoordinatorEntity[ConnectLifeCoordinator], SensorEntity):
    """Base class with shared availability logic."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ConnectLifeCoordinator, puid: str) -> None:
        super().__init__(coordinator)
        self._puid = puid

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._puid in self.coordinator.data
        )

    def _status(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._puid, {}).get("statusList", {})


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


class ConnectLifeEnergySensor(_ConnectLifeBaseSensor):
    """Daily energy consumption sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, coordinator: ConnectLifeCoordinator, puid: str, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, puid)
        self._attr_unique_id = f"{puid}_daily_energy"
        self._attr_name = "Daily Energy"
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    @property
    def native_value(self) -> float | None:
        val = self._status().get("daily_energy_kwh")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Temperature sensors
# ---------------------------------------------------------------------------


class ConnectLifeCurrentTempSensor(_ConnectLifeBaseSensor):
    """Current (measured) room temperature reported by the AC."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: ConnectLifeCoordinator, puid: str, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, puid)
        self._attr_unique_id = f"{puid}_current_temperature"
        self._attr_name = "Current Temperature"
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    @property
    def native_unit_of_measurement(self) -> str:
        return _temp_unit(self._status())

    @property
    def native_value(self) -> float | None:
        val = self._status().get("f_temp_in")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class ConnectLifeTargetTempSensor(_ConnectLifeBaseSensor):
    """Target (set-point) temperature of the AC."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: ConnectLifeCoordinator, puid: str, device: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, puid)
        self._attr_unique_id = f"{puid}_target_temperature"
        self._attr_name = "Target Temperature"
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    @property
    def native_unit_of_measurement(self) -> str:
        return _temp_unit(self._status())

    @property
    def native_value(self) -> float | None:
        val = self._status().get("t_temp")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None
