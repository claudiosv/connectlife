"""Sensor platform for ConnectLife."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entry_config
from .const import (
    CONF_MATTER_CLIMATE_ENTITY,
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
    matter_climate_entity = cfg.get(CONF_MATTER_CLIMATE_ENTITY)

    known_keys = {fd.key for fd in _STATUS_FIELDS}

    entities: list[SensorEntity] = []
    for puid, device in coordinator.data.items():
        # if "daily_energy_kwh" in device.get("statusList", {}):
        #     entities.append(ConnectLifeEnergySensor(coordinator, puid, device))

        if temp_sensors_enabled:
            entities.append(ConnectLifeCurrentTempSensor(coordinator, puid, device))
            entities.append(ConnectLifeTargetTempSensor(coordinator, puid, device))

        for fd in _STATUS_FIELDS:
            entities.append(ConnectLifeStatusSensor(coordinator, puid, device, fd))

        for key in device.get("statusList", {}):
            if key not in known_keys:
                fd = _FieldDef(key=key, name=key)
                entities.append(ConnectLifeStatusSensor(coordinator, puid, device, fd))

        if matter_climate_entity:
            entities.append(
                ConnectLifeMatterTemperatureSensor(
                    coordinator, puid, device, matter_climate_entity
                )
            )
            entities.append(
                ConnectLifeMatterSetpointSensor(
                    coordinator, puid, device, matter_climate_entity
                )
            )
            entities.append(
                ConnectLifeMatterHvacModeSensor(
                    coordinator, puid, device, matter_climate_entity
                )
            )

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
    # t_temp_type comes from the API as a string ("0"/"1"), not an int — compare
    # numerically or this always falls through to Celsius.
    try:
        temp_type = int(status.get("t_temp_type", 0))
    except (TypeError, ValueError):
        temp_type = 0
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
        self._attr_name = "Current Temperature (ConnectLife)"
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
        self._attr_name = "Target Temperature (ConnectLife)"
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


# ---------------------------------------------------------------------------
# Generic status sensors — one per statusList field
# ---------------------------------------------------------------------------


@dataclass
class _FieldDef:
    key: str
    name: str
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    is_temperature: bool = False  # unit follows t_temp_type
    entity_category: EntityCategory = field(default=EntityCategory.DIAGNOSTIC)


_STATUS_FIELDS: list[_FieldDef] = [
    # Error / fault flags
    _FieldDef("f_e_upmachine", "Upstream Machine Fault"),
    _FieldDef("f_e_dwmachine", "Downstream Machine Fault"),
    _FieldDef("f_e_intemp", "Indoor Temp Sensor Fault"),
    _FieldDef("f_e_incoiltemp", "Indoor Coil Temp Sensor Fault"),
    _FieldDef("f_e_outcoiltemp", "Outdoor Coil Temp Sensor Fault"),
    _FieldDef("f_e_waterfull", "Water Tank Full"),
    _FieldDef("f_e_push", "Push Fault"),
    # AC operating state
    _FieldDef("t_power", "Power"),
    _FieldDef("t_work_mode", "Work Mode"),
    _FieldDef("t_fan_speed", "Fan Speed"),
    _FieldDef("t_fan_mute", "Fan Mute"),
    _FieldDef("t_sleep", "Sleep"),
    _FieldDef("t_super", "Boost"),
    _FieldDef("t_up_down", "Vertical Swing"),
    _FieldDef("t_temp_type", "Temp Unit"),
    # Temperature (raw API values; distinct from the primary temp sensors above)
    _FieldDef(
        "f_temp_in",
        "Indoor Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        is_temperature=True,
    ),
    _FieldDef(
        "t_temp",
        "Setpoint Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        is_temperature=True,
    ),
    # Device / Matter info
    _FieldDef("f_matterOriginalVendorId", "Matter Vendor ID"),
    _FieldDef("f_matterOriginalProductId", "Matter Product ID"),
    _FieldDef("f_matterUniqueId", "Matter Unique ID"),
]


class ConnectLifeStatusSensor(_ConnectLifeBaseSensor):
    """Diagnostic sensor exposing a single raw statusList field."""

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        fd: _FieldDef,
    ) -> None:
        super().__init__(coordinator, puid)
        self._field_key = fd.key
        self._is_temperature = fd.is_temperature
        self._attr_unique_id = f"{puid}_{fd.key}"
        self._attr_name = fd.name
        self._attr_device_class = fd.device_class
        self._attr_state_class = fd.state_class
        self._attr_entity_category = fd.entity_category
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._is_temperature:
            return _temp_unit(self._status())
        return None

    @property
    def native_value(self) -> str | float | None:
        val = self._status().get(self._field_key)
        if val is None:
            return None
        if self._is_temperature:
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
        return val


# ---------------------------------------------------------------------------
# Matter mirror sensors — debugging view of the linked Matter climate entity
# ---------------------------------------------------------------------------


class _ConnectLifeMatterMirrorSensor(_ConnectLifeBaseSensor):
    """Base class for sensors that mirror the linked Matter climate entity."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        matter_entity_id: str,
    ) -> None:
        super().__init__(coordinator, puid)
        self._matter_entity_id = matter_entity_id
        self._attr_device_info = _device_info(device, puid, DOMAIN)

    async def async_added_to_hass(self) -> None:
        """Push state immediately on Matter changes instead of waiting on polling."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._matter_entity_id], self._async_matter_event
            )
        )

    @callback
    def _async_matter_event(self, _event: Any) -> None:
        self.async_write_ha_state()

    def _matter_state(self) -> State | None:
        return self.hass.states.get(self._matter_entity_id)


class ConnectLifeMatterTemperatureSensor(_ConnectLifeMatterMirrorSensor):
    """Current temperature as reported by the linked Matter climate entity."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        matter_entity_id: str,
    ) -> None:
        super().__init__(coordinator, puid, device, matter_entity_id)
        self._attr_unique_id = f"{puid}_matter_current_temperature"
        self._attr_name = "Matter Current Temperature"

    @property
    def native_unit_of_measurement(self) -> str:
        # Climate entities report current/target temperature in HA's
        # system-wide unit, not necessarily this ConnectLife device's own
        # t_temp_type — label it with the unit the raw value is actually in.
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:
        state = self._matter_state()
        if not state:
            return None
        val = state.attributes.get("current_temperature")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class ConnectLifeMatterSetpointSensor(_ConnectLifeMatterMirrorSensor):
    """Target temperature as reported by the linked Matter climate entity."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        matter_entity_id: str,
    ) -> None:
        super().__init__(coordinator, puid, device, matter_entity_id)
        self._attr_unique_id = f"{puid}_matter_setpoint"
        self._attr_name = "Matter Setpoint"

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:
        state = self._matter_state()
        if not state:
            return None
        val = state.attributes.get("temperature")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None


class ConnectLifeMatterHvacModeSensor(_ConnectLifeMatterMirrorSensor):
    """HVAC mode as reported by the linked Matter climate entity."""

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        matter_entity_id: str,
    ) -> None:
        super().__init__(coordinator, puid, device, matter_entity_id)
        self._attr_unique_id = f"{puid}_matter_hvac_mode"
        self._attr_name = "Matter HVAC Mode"

    @property
    def native_value(self) -> str | None:
        state = self._matter_state()
        return state.state if state else None
