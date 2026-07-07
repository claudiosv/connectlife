"""Climate platform for ConnectLife AC devices."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    PRESET_SLEEP,
)
from homeassistant.const import PRECISION_TENTHS, PRECISION_WHOLE, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from . import entry_config
from .const import (
    COMMAND_REFRESH_DELAY_SECONDS,
    CONF_BEEPING,
    CONF_COMMAND_REFRESH_DELAY,
    CONF_CURRENT_HUMIDITY_ENTITY,
    CONF_CURRENT_TEMP_ENTITY,
    CONF_DEBOUNCE_DELAY,
    CONF_DEVICES_CONFIG,
    CONF_EXTERNAL_TEMP_ENABLED,
    CONF_HUMIDITY_PRECISION,
    CONF_MATTER_CLIMATE_ENTITY,
    CONF_MATTER_SYNC_TIMEOUT,
    CONF_TARGET_HUMIDITY,
    CONF_TEMPERATURE_PRECISION,
    DEBOUNCE_DELAY_SECONDS,
    DEFAULT_DEVICES_CONFIG,
    DOMAIN,
    MATTER_SYNC_TIMEOUT_SECONDS,
    TEMP_CODE_CELSIUS,
    TEMP_CODE_FAHRENHEIT,
)
from .coordinator import ConnectLifeCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

# API target temperatures used by the external-sensor thermostat logic
# When sensor > desired temp: push minimum to make AC work hard
# When sensor <= desired temp: push a comfortable idle value to back off
_THERMOSTAT_COOL_F = 61
_THERMOSTAT_IDLE_F = 75
_THERMOSTAT_COOL_C = 16
_THERMOSTAT_IDLE_C = 24

# Mapping from ConnectLife work mode names to HA HVAC modes
_HA_MODE_MAP: dict[str, HVACMode] = {
    "fan only": HVACMode.FAN_ONLY,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "dry": HVACMode.DRY,
    "auto": HVACMode.AUTO,
    "off": HVACMode.OFF,
}
_CL_MODE_MAP: dict[HVACMode, str] = {v: k for k, v in _HA_MODE_MAP.items()}

# HVAC modes the linked Matter climate entity is able to set directly.
_MATTER_SUPPORTED_MODES = {
    HVACMode.COOL,
    HVACMode.DRY,
    HVACMode.FAN_ONLY,
    HVACMode.OFF,
}

# Fallback Matter min/max (°C) used only if the linked entity's own min_temp/
# max_temp attributes aren't available.
_MATTER_MIN_TEMP_C = 16.0
_MATTER_MAX_TEMP_C = 32.0

# Matter's reported min_temp/max_temp are themselves rounded for display
# (e.g. a true 32.0°C ceiling shows as 90°F, not 89.6), so a value sent right
# at that boundary can still overshoot the device's real limit once HA
# converts it back. Nudge inward by this much (in system unit) as a margin.
_MATTER_BOUND_SAFETY_MARGIN = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ConnectLife climate entities from a config entry."""
    coordinator: ConnectLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    cfg = entry_config(entry)
    devices_config_raw = cfg.get(CONF_DEVICES_CONFIG, "{}")
    try:
        devices_config = json.loads(devices_config_raw)
    except json.JSONDecodeError:
        _LOGGER.warning("Invalid devices_config JSON, using defaults")
        devices_config = {}

    beeping = cfg.get(CONF_BEEPING, False)
    current_temp_entity = cfg.get(CONF_CURRENT_TEMP_ENTITY)
    external_temp_enabled = cfg.get(CONF_EXTERNAL_TEMP_ENABLED, True)
    current_humidity_entity = cfg.get(CONF_CURRENT_HUMIDITY_ENTITY)
    target_humidity = cfg.get(CONF_TARGET_HUMIDITY)
    matter_climate_entity = cfg.get(CONF_MATTER_CLIMATE_ENTITY)
    matter_sync_timeout = float(
        cfg.get(CONF_MATTER_SYNC_TIMEOUT, MATTER_SYNC_TIMEOUT_SECONDS)
    )
    temperature_precision = cfg.get(CONF_TEMPERATURE_PRECISION)
    humidity_precision = cfg.get(CONF_HUMIDITY_PRECISION)
    command_refresh_delay = int(
        cfg.get(CONF_COMMAND_REFRESH_DELAY, COMMAND_REFRESH_DELAY_SECONDS)
    )
    debounce_delay = float(cfg.get(CONF_DEBOUNCE_DELAY, DEBOUNCE_DELAY_SECONDS))

    entities = [
        ConnectLifeClimate(
            coordinator,
            puid,
            device,
            devices_config,
            beeping,
            current_temp_entity,
            external_temp_enabled,
            current_humidity_entity,
            target_humidity,
            command_refresh_delay,
            debounce_delay,
            matter_climate_entity,
            temperature_precision,
            humidity_precision,
            matter_sync_timeout,
        )
        for puid, device in coordinator.data.items()
    ]
    async_add_entities(entities)


def _get_device_config(devices_config: dict, feature_code: str) -> dict:
    """Return device-specific config, falling back to defaults."""
    if feature_code in devices_config:
        return devices_config[feature_code]
    _LOGGER.debug("No device config for feature code %s, using defaults", feature_code)
    return DEFAULT_DEVICES_CONFIG


# ConnectLife API work-mode values are fixed regardless of which modes a device supports.
_WORK_MODE_API_VALUES: dict[str, str] = {
    "fan only": "0",
    "heat": "1",
    "cool": "2",
    "dry": "3",
    "auto": "4",
}


def _build_mode_options(config: dict) -> dict[str, str]:
    """Build a name→api_value map for t_work_mode.

    The device config lists *available* mode names; the API integer values are
    fixed by ConnectLife and must not be derived from array position.
    """
    options: dict[str, str] = {}
    for name in config.get("t_work_mode", []):
        api_val = _WORK_MODE_API_VALUES.get(name)
        if api_val is not None:
            slug = name.replace(" ", "_").lower()
            options[slug] = api_val
    return options


def _build_fan_options(config: dict) -> dict[str, str]:
    """Build a name→api_value map for t_fan_speed."""
    options: dict[str, str] = {}
    for key, value in config.get("t_fan_speed", {}).items():
        slug = value.replace(" ", "_").lower()
        options[slug] = str(key)
    return options


def _build_swing_options(config: dict) -> dict[str, dict[str, str]]:
    """Build swing options from device config.

    Supports two swing types, both tagged with a ``"type"`` key:
    - ``"directional"``: combined ``t_swing_direction`` x ``t_swing_angle``
    - ``"up_down"``: simple ``t_up_down`` on/off
    """
    options: dict[str, dict[str, str]] = {}

    # Combined direction x angle swing
    directions = config.get("t_swing_direction", {})
    angles = config.get("t_swing_angle", {})
    if directions and angles:
        if isinstance(directions, list):
            directions = {str(i): v for i, v in enumerate(directions)}
        if isinstance(angles, list):
            angles = {str(i): v for i, v in enumerate(angles)}
        for dir_key, dir_name in directions.items():
            for ang_key, ang_name in angles.items():
                label: str = f"{dir_name} - {ang_name}"
                options[label] = {
                    "type": "directional",
                    "t_swing_direction": str(dir_key),
                    "t_swing_angle": str(ang_key),
                }

    # Simple up/down swing
    up_down = config.get("t_up_down", {})
    if up_down:
        if isinstance(up_down, list):
            up_down = {str(i): v for i, v in enumerate(up_down)}
        for key, name in up_down.items():
            options[name] = {"type": "up_down", "t_up_down": str(key)}

    return options


def _build_full_properties(
    status: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """Build a full writable-property payload from current status + overrides.

    ConnectLife's API can silently ignore (or the AC firmware reverts) a bare
    single-property update like {"t_up_down": 1} — resending the device's
    other current values alongside the change is what makes it stick. Used
    by platforms (switch, fan) that don't track the full per-device config
    ConnectLifeClimate._build_properties() uses, just the raw status dict.
    """
    props: dict[str, Any] = {}
    for key, val in status.items():
        if not key.startswith("t_"):
            continue
        try:
            props[key] = int(val)
        except (TypeError, ValueError):
            props[key] = val
    props.update(overrides)
    return props


class ConnectLifeClimate(CoordinatorEntity[ConnectLifeCoordinator], ClimateEntity):
    """Representation of a ConnectLife AC as a HA climate entity."""

    _attr_has_entity_name = True
    _attr_name = None  # Use the device name as the entity name

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
        devices_config: dict,
        beeping: bool,
        current_temp_entity: str | None = None,
        external_temp_enabled: bool = True,
        current_humidity_entity: str | None = None,
        target_humidity: float | None = None,
        command_refresh_delay: int = COMMAND_REFRESH_DELAY_SECONDS,
        debounce_delay: float = DEBOUNCE_DELAY_SECONDS,
        matter_climate_entity: str | None = None,
        temperature_precision: int | None = None,
        humidity_precision: int | None = None,
        matter_sync_timeout: float = MATTER_SYNC_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(coordinator)
        self._puid = puid
        self._beeping = beeping
        self._current_temp_entity = current_temp_entity
        self._external_temp_enabled = external_temp_enabled
        self._command_refresh_delay = command_refresh_delay
        self._debounce_delay = debounce_delay
        self._current_humidity_entity = current_humidity_entity
        self._matter_climate_entity = matter_climate_entity
        self._matter_sync_timeout = matter_sync_timeout
        self._temperature_precision = temperature_precision
        self._humidity_precision = humidity_precision
        self._target_humidity = (
            float(target_humidity) if target_humidity is not None else None
        )
        # Desired room temperature when external sensor thermostat is active.
        # Initialised from the device's current t_temp; updated by async_set_temperature.
        raw_temp = device.get("statusList", {}).get("t_temp")
        self._target_room_temp: float | None = (
            float(raw_temp) if raw_temp is not None else None
        )
        self._optimistic_status: dict[str, Any] = {}
        # Monotonic timestamp each optimistic key was last set — used to keep
        # trusting our own guess until ConnectLife's data confirms it, up to
        # _matter_sync_timeout, rather than clearing on every coordinator poll.
        self._optimistic_set_at: dict[str, float] = {}
        self._pending_overrides: dict[str, Any] = {}
        self._debounce_task: asyncio.Task | None = None

        feature_code = device.get("deviceFeatureCode", "")
        self._device_config = _get_device_config(devices_config, feature_code)

        self._mode_options = _build_mode_options(self._device_config)
        self._fan_options = _build_fan_options(self._device_config)
        self._swing_options = _build_swing_options(self._device_config)

        self._attr_unique_id = puid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, puid)},
            name=device.get("deviceNickName", puid),
            manufacturer="ConnectLife",
            model=(
                f"{device.get('deviceTypeCode', '')}-{device.get('deviceFeatureCode', '')}"
            ),
        )

        # Modes
        ha_modes = [
            _HA_MODE_MAP[m.replace("_", " ")]
            for m in self._mode_options
            if m.replace("_", " ") in _HA_MODE_MAP
        ]
        ha_modes.append(HVACMode.OFF)
        self._attr_hvac_modes = list(
            dict.fromkeys(ha_modes)
        )  # deduplicate, preserve order

        self._attr_fan_modes = (
            list(self._fan_options.keys()) if self._fan_options else None
        )
        self._attr_swing_modes = (
            list(self._swing_options.keys()) if self._swing_options else None
        )

    # ------------------------------------------------------------------
    # Properties derived from coordinator data
    # ------------------------------------------------------------------

    @property
    def supported_features(self) -> ClimateEntityFeature:
        mode = self.hvac_mode
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        # Temperature: not settable in auto, dry, or fan-only
        if mode not in (HVACMode.AUTO, HVACMode.DRY, HVACMode.FAN_ONLY):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        # Fan speed: not available in dry mode
        if self._fan_options and mode != HVACMode.DRY:
            features |= ClimateEntityFeature.FAN_MODE
        # Swing: available in all non-off modes
        if self._swing_options and mode != HVACMode.OFF:
            features |= ClimateEntityFeature.SWING_MODE
        # Presets: not available in fan-only or auto (auto has no supported presets)
        if mode not in (HVACMode.FAN_ONLY, HVACMode.AUTO):
            features |= ClimateEntityFeature.PRESET_MODE
        if self._current_humidity_entity:
            features |= ClimateEntityFeature.TARGET_HUMIDITY
        return features

    @property
    def preset_modes(self) -> list[str] | None:
        mode = self.hvac_mode
        if mode in (HVACMode.FAN_ONLY, HVACMode.AUTO):
            return None
        if mode == HVACMode.DRY:
            return [PRESET_NONE, PRESET_SLEEP]
        return [PRESET_NONE, PRESET_SLEEP, PRESET_BOOST]

    @property
    def available(self) -> bool:
        """Mark unavailable when the device is absent from the latest poll."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._puid in self.coordinator.data
        )

    def _device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._puid, {})

    def _status(self) -> dict[str, Any]:
        base = self._device().get("statusList", {})
        if self._optimistic_status:
            return {**base, **self._optimistic_status}
        return base

    def _set_optimistic(self, overrides: dict[str, Any]) -> None:
        """Apply an optimistic override and record when each key was set."""
        now = time.monotonic()
        self._optimistic_status.update(overrides)
        self._optimistic_set_at.update(dict.fromkeys(overrides, now))

    @callback
    def _handle_coordinator_update(self) -> None:
        # Keep trusting an optimistic key until ConnectLife's own data confirms
        # it, or _matter_sync_timeout elapses — rather than clearing on every
        # poll, which would revert a Matter-redirected change back to stale
        # ConnectLife data before its cloud has caught up with the physical
        # device (that sync can lag well past one poll cycle).
        if self._optimistic_status:
            fresh = self._device().get("statusList", {})
            now = time.monotonic()
            for key, val in list(self._optimistic_status.items()):
                confirmed = str(fresh.get(key)) == str(val)
                expired = (
                    now - self._optimistic_set_at.get(key, 0)
                ) > self._matter_sync_timeout
                if confirmed or expired:
                    self._optimistic_status.pop(key, None)
                    self._optimistic_set_at.pop(key, None)
        super()._handle_coordinator_update()

    @property
    def temperature_unit(self) -> str:
        # t_temp_type comes from the API as a string ("0"/"1"), not an int —
        # compare numerically or this always falls through to Celsius.
        try:
            temp_type = int(self._status().get("t_temp_type", TEMP_CODE_CELSIUS))
        except (TypeError, ValueError):
            temp_type = TEMP_CODE_CELSIUS
        unit = (
            UnitOfTemperature.FAHRENHEIT
            if temp_type == TEMP_CODE_FAHRENHEIT
            else UnitOfTemperature.CELSIUS
        )
        _LOGGER.debug(
            "[%s] t_temp_type=%r → reporting unit as %s  (f_temp_in=%r, t_temp=%r)",
            self._puid,
            temp_type,
            unit,
            self._status().get("f_temp_in"),
            self._status().get("t_temp"),
        )
        return unit

    @property
    def precision(self) -> float:
        if self._temperature_precision is not None:
            return (
                PRECISION_WHOLE
                if int(self._temperature_precision) <= 0
                else PRECISION_TENTHS
            )
        # Same default Home Assistant's ClimateEntity uses when unset.
        if self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
            return PRECISION_TENTHS
        return PRECISION_WHOLE

    def _round_humidity(self, val: float | None) -> float | None:
        if val is None or self._humidity_precision is None:
            return val
        return round(val, int(self._humidity_precision))

    @property
    def target_temperature(self) -> float | None:
        if (
            self._current_temp_entity
            and self._external_temp_enabled
            and self._target_room_temp is not None
        ):
            return self._target_room_temp
        if "t_temp" in self._optimistic_status:
            val = self._optimistic_status["t_temp"]
            return float(val) if val is not None else None
        if self._matter_climate_entity and self.hvac_mode == HVACMode.COOL:
            val = self._matter_temperature("temperature")
            if val is not None:
                return val
        val = self._status().get("t_temp")
        return float(val) if val is not None else None

    @property
    def current_temperature(self) -> float | None:
        if self._current_temp_entity and self._external_temp_enabled:
            val = self._sensor_temperature(self._current_temp_entity)
            if val is not None:
                return val
        if self._matter_climate_entity:
            val = self._matter_temperature("current_temperature")
            if val is not None:
                return val
        val = self._status().get("f_temp_in")
        return float(val) if val is not None else None

    def _matter_temperature(self, attr: str) -> float | None:
        """Read a temperature attribute off the linked Matter entity.

        Climate entities' current/target temperature attributes are reported
        in Home Assistant's system-wide unit, not the entity's own native
        unit — convert to our own temperature_unit before using the value.
        """
        if not self._matter_climate_entity:
            return None
        state = self.hass.states.get(self._matter_climate_entity)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        val = state.attributes.get(attr)
        if val is None:
            return None
        try:
            ha_unit = self.hass.config.units.temperature_unit
            our_unit = self.temperature_unit
            converted = TemperatureConverter.convert(float(val), ha_unit, our_unit)
            _LOGGER.debug(
                "[%s] _matter_temperature(%s): raw=%r from %s (system unit=%s) "
                "-> converted=%r in %s (our unit)",
                self._puid,
                attr,
                val,
                self._matter_climate_entity,
                ha_unit,
                converted,
                our_unit,
            )
            return converted
        except (TypeError, ValueError):
            return None

    def _clamp_for_matter(self, temp: float) -> float:
        """Convert to system unit and clamp within the Matter entity's own bounds.

        climate.set_temperature converts the given value from HA's
        system-wide unit to the target entity's own unit before validating
        it against the entity's min/max. We read those bounds directly off
        the linked Matter entity (falling back to a hardcoded guess if
        unavailable) rather than assuming our own rounded Fahrenheit limits
        round-trip exactly — they don't (90°F -> 32.222°C overshoots a true
        32.0°C ceiling).
        """
        system_unit = self.hass.config.units.temperature_unit
        system_temp = TemperatureConverter.convert(
            temp, self.temperature_unit, system_unit
        )
        state = (
            self.hass.states.get(self._matter_climate_entity)
            if self._matter_climate_entity
            else None
        )
        max_bound = self._matter_bound(state, "max_temp", system_unit, is_max=True)
        min_bound = self._matter_bound(state, "min_temp", system_unit, is_max=False)
        return min(max(system_temp, min_bound), max_bound)

    def _matter_bound(
        self, state: Any, attr: str, system_unit: str, *, is_max: bool
    ) -> float:
        """Read min_temp/max_temp off a Matter state, nudged inward as a safety margin."""
        raw = state.attributes.get(attr) if state else None
        if raw is not None:
            try:
                bound = float(raw)
                return bound - _MATTER_BOUND_SAFETY_MARGIN if is_max else bound + _MATTER_BOUND_SAFETY_MARGIN
            except (TypeError, ValueError):
                pass
        fallback_c = _MATTER_MAX_TEMP_C if is_max else _MATTER_MIN_TEMP_C
        return TemperatureConverter.convert(
            fallback_c, UnitOfTemperature.CELSIUS, system_unit
        )

    def _sensor_temperature(self, entity_id: str) -> float | None:
        """Read a plain sensor entity's temperature, converted to our own unit.

        Unlike climate entities, sensor entities expose their own
        unit_of_measurement as a state attribute — use it directly rather
        than assuming it matches our own temperature_unit.
        """
        state = self.hass.states.get(entity_id)
        if not state or state.state in ("unknown", "unavailable"):
            return None
        try:
            raw = float(state.state)
        except ValueError:
            return None
        sensor_unit = state.attributes.get("unit_of_measurement", self.temperature_unit)
        try:
            return TemperatureConverter.convert(raw, sensor_unit, self.temperature_unit)
        except (TypeError, ValueError):
            return None

    @property
    def current_humidity(self) -> float | None:
        if self._current_humidity_entity:
            state = self.hass.states.get(self._current_humidity_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    return self._round_humidity(float(state.state))
                except ValueError:
                    pass
        return None

    @property
    def min_temp(self) -> float:
        return 61.0 if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else 16.0

    @property
    def max_temp(self) -> float:
        return 90.0 if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else 32.0

    @property
    def target_temperature_step(self) -> float:
        return 1.0

    @property
    def target_humidity(self) -> float | None:
        return self._round_humidity(self._target_humidity)

    @property
    def min_humidity(self) -> float:
        return 30.0

    @property
    def max_humidity(self) -> float:
        return 80.0

    @property
    def hvac_mode(self) -> HVACMode:
        status = self._status()
        if status.get("t_power") == "0" or status.get("t_power") == 0:
            return HVACMode.OFF

        work_mode_val = str(status.get("t_work_mode", ""))
        # Find name for the API value
        for name, api_val in self._mode_options.items():
            if api_val == work_mode_val:
                cl_name = name.replace("_", " ")
                return _HA_MODE_MAP.get(cl_name, HVACMode.AUTO)
        return HVACMode.AUTO

    @property
    def fan_mode(self) -> str | None:
        if not self._fan_options:
            return None
        fan_val = str(self._status().get("t_fan_speed", ""))
        for name, api_val in self._fan_options.items():
            if api_val == fan_val:
                return name
        return None

    @property
    def swing_mode(self) -> str | None:
        if not self._swing_options:
            return None
        status = self._status()
        for label, vals in self._swing_options.items():
            if vals["type"] == "directional":
                if vals["t_swing_direction"] == str(
                    status.get("t_swing_direction", "")
                ) and vals["t_swing_angle"] == str(status.get("t_swing_angle", "")):
                    return label
            elif vals["type"] == "up_down":
                if vals["t_up_down"] == str(status.get("t_up_down", "")):
                    return label
        return None

    @property
    def preset_mode(self) -> str:
        status = self._status()
        if str(status.get("t_fan_mute", 0)) == "1":
            return PRESET_ECO
        if str(status.get("t_sleep", 0)) == "1":
            return PRESET_SLEEP
        if str(status.get("t_super", 0)) == "1":
            return PRESET_BOOST
        return PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self._status())

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        """Schedule a coordinator refresh after a short delay, resetting the poll timer."""

        async def _refresh() -> None:
            await asyncio.sleep(self._command_refresh_delay)
            _LOGGER.debug(
                "[%s] Requesting coordinator refresh after %.1fs command delay",
                self._puid,
                self._command_refresh_delay,
            )
            await self.coordinator.async_request_refresh()

        self.hass.async_create_task(_refresh())

    def _enqueue(self, overrides: dict[str, Any]) -> None:
        """Accumulate overrides and (re)start the debounce timer.

        Multiple rapid calls merge into a single API request sent after
        _DEBOUNCE_DELAY seconds of inactivity.
        """
        self._pending_overrides.update(overrides)
        _LOGGER.debug("[%s] Enqueued overrides: %s", self._puid, overrides)
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = self.hass.async_create_task(self._flush_pending())

    async def _flush_pending(self) -> None:
        """Send all accumulated overrides in one API call after the debounce window."""
        await asyncio.sleep(self._debounce_delay)
        overrides = dict(self._pending_overrides)
        self._pending_overrides.clear()
        props = self._build_properties(overrides)
        _LOGGER.debug(
            "[%s] Sending debounced update to ConnectLife: %s", self._puid, props
        )
        await self.coordinator.api.update_device(self._puid, props)
        self._schedule_refresh()

    async def async_will_remove_from_hass(self) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

    def _build_properties(
        self, overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build the full property payload from current state + overrides."""
        status = self._status()
        is_off = (overrides or {}).get("t_power", status.get("t_power", 0)) in {"0", 0}

        props: dict[str, Any] = {
            "t_power": 0 if is_off else 1,
            "t_temp_type": status.get("t_temp_type", TEMP_CODE_CELSIUS),
            "t_temp": int(status.get("t_temp", 24)),
            "t_eco": int(status.get("t_eco", 0)),
            "t_beep": 1 if self._beeping else 0,
        }

        # Work mode (only if device is on)
        if not is_off:
            props["t_work_mode"] = int(status.get("t_work_mode", 0))

        # Fan speed
        if self._fan_options and "t_fan_speed" in status:
            props["t_fan_speed"] = int(status["t_fan_speed"])

        # Preset-controlled fields — include current values if present
        # for key in ("t_fan_mute", "t_sleep", "t_super"):
        for key in ("t_sleep", "t_super"):
            if key in status:
                props[key] = int(status[key])

        # Swing — include whichever field(s) this device uses
        if self._swing_options:
            sample = next(iter(self._swing_options.values()))
            if sample["type"] == "directional":
                if "t_swing_direction" in status:
                    props["t_swing_direction"] = int(status["t_swing_direction"])
                if "t_swing_angle" in status:
                    props["t_swing_angle"] = int(status["t_swing_angle"])
            elif sample["type"] == "up_down" and "t_up_down" in status:
                props["t_up_down"] = int(status["t_up_down"])

        if overrides:
            props.update(overrides)

        return props

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        _LOGGER.debug("[%s] async_set_hvac_mode(%s)", self._puid, hvac_mode)
        if hvac_mode == HVACMode.OFF:
            overrides: dict[str, Any] = {"t_power": 0}
        else:
            cl_name = _CL_MODE_MAP.get(hvac_mode, "auto")
            slug = cl_name.replace(" ", "_").lower()
            mode_val = self._mode_options.get(slug, "4")
            overrides = {"t_power": 1, "t_work_mode": int(mode_val)}
            # Clear presets incompatible with the target mode so the device
            # doesn't hold stale state after the switch.
            if hvac_mode == HVACMode.AUTO:
                overrides.update({"t_sleep": 0, "t_super": 0, "t_fan_mute": 0})
            elif hvac_mode == HVACMode.DRY:
                overrides["t_super"] = 0

        self._set_optimistic(overrides)
        self.async_write_ha_state()

        if self._matter_climate_entity and hvac_mode in _MATTER_SUPPORTED_MODES:
            _LOGGER.debug(
                "[%s] Sending set_hvac_mode=%s to Matter entity %s",
                self._puid,
                hvac_mode,
                self._matter_climate_entity,
            )
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": self._matter_climate_entity, "hvac_mode": hvac_mode},
                    blocking=True,
                )
            except Exception:
                _LOGGER.warning(
                    "Failed to set HVAC mode on Matter entity %s, falling back to "
                    "ConnectLife API",
                    self._matter_climate_entity,
                    exc_info=True,
                )
                self._enqueue(overrides)
            else:
                _LOGGER.debug(
                    "[%s] Matter set_hvac_mode succeeded", self._puid
                )
                self._schedule_refresh()
        else:
            self._enqueue(overrides)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get("temperature")
        _LOGGER.debug("[%s] async_set_temperature(temperature=%r)", self._puid, temp)
        if temp is None:
            return
        if self.hvac_mode in (HVACMode.AUTO, HVACMode.DRY, HVACMode.FAN_ONLY):
            _LOGGER.warning(
                "Target temperature cannot be set in %s mode", self.hvac_mode
            )
            return
        if self._current_temp_entity and self._external_temp_enabled:
            # External sensor mode: store desired room temp and let the thermostat
            # logic decide the actual API target.
            _LOGGER.debug(
                "[%s] External sensor mode: storing desired room temp %r",
                self._puid,
                temp,
            )
            self._target_room_temp = float(temp)
            self.async_write_ha_state()
            await self._async_control()
        elif self._matter_climate_entity and self.hvac_mode == HVACMode.COOL:
            overrides = {"t_temp": int(temp)}
            self._set_optimistic(overrides)
            self.async_write_ha_state()
            matter_temp = self._clamp_for_matter(float(temp))
            _LOGGER.debug(
                "[%s] Sending set_temperature=%r (clamped from %r) to Matter entity %s",
                self._puid,
                matter_temp,
                temp,
                self._matter_climate_entity,
            )
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": self._matter_climate_entity,
                        "temperature": matter_temp,
                    },
                    blocking=True,
                )
            except Exception:
                _LOGGER.warning(
                    "Failed to set temperature on Matter entity %s (sent %r, "
                    "originally %r), falling back to ConnectLife API",
                    self._matter_climate_entity,
                    matter_temp,
                    temp,
                    exc_info=True,
                )
                self._enqueue(overrides)
            else:
                _LOGGER.debug("[%s] Matter set_temperature succeeded", self._puid)
                self._schedule_refresh()
        else:
            overrides = {"t_temp": int(temp)}
            self._set_optimistic(overrides)
            self.async_write_ha_state()
            self._enqueue(overrides)

    async def async_set_humidity(self, humidity: float) -> None:
        """Set target humidity for dry-mode control."""
        _LOGGER.debug("[%s] async_set_humidity(%r)", self._puid, humidity)
        self._target_humidity = humidity
        self.async_write_ha_state()
        await self._async_control()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""
        _LOGGER.debug("[%s] async_set_fan_mode(%r)", self._puid, fan_mode)
        fan_val = self._fan_options.get(fan_mode)
        if fan_val is None:
            _LOGGER.warning("Unknown fan mode: %s", fan_mode)
            return
        overrides: dict[str, Any] = {"t_fan_speed": int(fan_val)}
        if self.hvac_mode == HVACMode.DRY:
            # Dry mode doesn't support fan speed; switch to the best available
            # mode that does, in a single API call.
            fallback = next(
                (
                    slug
                    for slug in ("auto", "cool", "fan_only", "heat")
                    if slug in self._mode_options
                ),
                None,
            )
            if fallback is None:
                _LOGGER.warning("No mode available that supports fan speed control")
                return
            overrides["t_power"] = 1
            overrides["t_work_mode"] = int(self._mode_options[fallback])
        # Selecting a non-auto fan speed while sleep is active violates the
        # sleep constraint (sleep requires auto fan), so exit sleep mode.
        auto_val = self._fan_options.get("auto")
        if auto_val is not None and fan_val != auto_val:
            if int(self._status().get("t_sleep", 0)) == 1:
                overrides["t_sleep"] = 0
        self._set_optimistic(overrides)
        self.async_write_ha_state()
        self._enqueue(overrides)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing mode."""
        _LOGGER.debug("[%s] async_set_swing_mode(%r)", self._puid, swing_mode)
        swing_vals = self._swing_options.get(swing_mode)
        if swing_vals is None:
            _LOGGER.warning("Unknown swing mode: %s", swing_mode)
            return
        if swing_vals["type"] == "directional":
            overrides = {
                "t_swing_direction": int(swing_vals["t_swing_direction"]),
                "t_swing_angle": int(swing_vals["t_swing_angle"]),
            }
        else:  # up_down
            overrides = {"t_up_down": int(swing_vals["t_up_down"])}
        self._set_optimistic(overrides)
        self.async_write_ha_state()
        self._enqueue(overrides)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        _LOGGER.debug("[%s] async_set_preset_mode(%r)", self._puid, preset_mode)
        mode = self.hvac_mode
        if mode in (HVACMode.AUTO, HVACMode.FAN_ONLY):
            _LOGGER.warning("Preset modes are not supported in %s mode", mode)
            return
        if mode == HVACMode.DRY and preset_mode == PRESET_BOOST:
            _LOGGER.warning("Boost preset is not supported in dry mode")
            return

        # Always clear all three flags first — t_sleep and t_super are mutually
        # exclusive, so zeroing both before setting one enforces that constraint.
        overrides: dict[str, Any] = {
            "t_sleep": 0,
            "t_super": 0,
        }
        if preset_mode == PRESET_ECO:
            overrides["t_fan_mute"] = 1
        elif preset_mode == PRESET_SLEEP:
            overrides["t_sleep"] = 1
            # Sleep requires auto fan speed.
            auto_val = self._fan_options.get("auto")
            if auto_val is not None:
                overrides["t_fan_speed"] = int(auto_val)
        elif preset_mode == PRESET_BOOST:
            overrides["t_super"] = 1
        self._set_optimistic(overrides)
        self.async_write_ha_state()
        self._enqueue(overrides)

    async def async_turn_on(self) -> None:
        """Turn the AC on."""
        _LOGGER.debug("[%s] async_turn_on()", self._puid)
        overrides: dict[str, Any] = {"t_power": 1}
        self._set_optimistic(overrides)
        self.async_write_ha_state()
        self._enqueue(overrides)

    async def async_turn_off(self) -> None:
        """Turn the AC off."""
        _LOGGER.debug("[%s] async_turn_off()", self._puid)
        await self.async_set_hvac_mode(HVACMode.OFF)

    # ------------------------------------------------------------------
    # External sensor tracking
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to external sensor state changes once added to HA."""
        await super().async_added_to_hass()
        entities_to_track = [
            e
            for e in (self._current_temp_entity, self._current_humidity_entity)
            if e is not None
        ]
        if entities_to_track:
            _LOGGER.debug(
                "[%s] Tracking external sensor entities: %s",
                self._puid,
                entities_to_track,
            )
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    entities_to_track,
                    self._async_sensor_event,
                )
            )
        if self._matter_climate_entity:
            _LOGGER.debug(
                "[%s] Tracking linked Matter entity: %s",
                self._puid,
                self._matter_climate_entity,
            )
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [self._matter_climate_entity],
                    self._async_matter_event,
                )
            )

    @callback
    def _async_sensor_event(self, event: Any) -> None:
        """Handle a state change on a tracked sensor entity."""
        new_state = event.data.get("new_state")
        _LOGGER.debug(
            "[%s] External sensor %s changed to %s",
            self._puid,
            event.data.get("entity_id"),
            new_state.state if new_state else None,
        )
        self.hass.async_create_task(self._async_control())

    @callback
    def _async_matter_event(self, event: Any) -> None:
        """Push state immediately when the linked Matter entity updates."""
        new_state = event.data.get("new_state")
        _LOGGER.debug(
            "[%s] Matter entity %s changed: state=%s attributes=%s",
            self._puid,
            event.data.get("entity_id"),
            new_state.state if new_state else None,
            dict(new_state.attributes) if new_state else None,
        )
        # Drop any locally-optimistic target temp so a change made directly
        # on the Matter side (not through this entity) isn't shadowed by a
        # stale guess until ConnectLife's next poll clears it.
        self._optimistic_status.pop("t_temp", None)
        self._optimistic_set_at.pop("t_temp", None)
        self.async_write_ha_state()

    async def _async_control(self) -> None:
        """Apply external-sensor thermostat and dry-mode humidity control."""
        # --- Temperature thermostat ---
        if self._current_temp_entity and self._target_room_temp is not None:
            current_temp = self._sensor_temperature(self._current_temp_entity)
            if current_temp is not None:
                is_f = self.temperature_unit == UnitOfTemperature.FAHRENHEIT
                if current_temp > self._target_room_temp:
                    api_temp = _THERMOSTAT_COOL_F if is_f else _THERMOSTAT_COOL_C
                else:
                    api_temp = _THERMOSTAT_IDLE_F if is_f else _THERMOSTAT_IDLE_C
                _LOGGER.debug(
                    "[%s] Thermostat: sensor=%.1f target=%.1f -> forcing t_temp=%s",
                    self._puid,
                    current_temp,
                    self._target_room_temp,
                    api_temp,
                )
                await self.coordinator.api.update_device(
                    self._puid, {"t_temp": api_temp}
                )

        # --- Dry-mode humidity control ---
        if (
            self._current_humidity_entity
            and self._target_humidity is not None
            and self.hvac_mode == HVACMode.DRY
        ):
            state = self.hass.states.get(self._current_humidity_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    current_humidity = float(state.state)
                    status = self._status()
                    is_on = str(status.get("t_power", "0")) not in ("0",)
                    if current_humidity > self._target_humidity and not is_on:
                        _LOGGER.debug(
                            "[%s] humidity %.1f > target %.1f — turning on",
                            self._puid,
                            current_humidity,
                            self._target_humidity,
                        )
                        await self.coordinator.api.update_device(
                            self._puid, self._build_properties({"t_power": 1})
                        )
                        await self.coordinator.async_request_refresh()
                    elif current_humidity <= self._target_humidity and is_on:
                        _LOGGER.debug(
                            "[%s] humidity %.1f <= target %.1f — turning off",
                            self._puid,
                            current_humidity,
                            self._target_humidity,
                        )
                        await self.coordinator.api.update_device(
                            self._puid, self._build_properties({"t_power": 0})
                        )
                        await self.coordinator.async_request_refresh()
                except ValueError:
                    pass
