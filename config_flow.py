"""Config flow for the ConnectLife integration."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import ConnectLifeApi, ConnectLifeAuthError
from .const import (
    COMMAND_REFRESH_DELAY_SECONDS,
    CONF_BEEPING,
    CONF_COMMAND_REFRESH_DELAY,
    CONF_CURRENT_HUMIDITY_ENTITY,
    CONF_CURRENT_TEMP_ENTITY,
    CONF_DEBOUNCE_DELAY,
    CONF_DEBUG_LOGGING,
    CONF_DEVICES_CONFIG,
    CONF_DRY_IDLE_MODE,
    CONF_EXTERNAL_TEMP_ENABLED,
    CONF_HUMIDITY_HYSTERESIS,
    CONF_HUMIDITY_PRECISION,
    CONF_MATTER_CLIMATE_ENTITY,
    CONF_MATTER_SYNC_TIMEOUT,
    CONF_MATTER_TEMPERATURE_SENSOR_ENTITY,
    CONF_POLL_INTERVAL,
    CONF_TARGET_HUMIDITY,
    CONF_TEMPERATURE_PRECISION,
    CONF_TEMPERATURE_SENSORS,
    CONF_TEMPERATURE_UNIT,
    DEBOUNCE_DELAY_SECONDS,
    DEFAULT_HUMIDITY_HYSTERESIS,
    DOMAIN,
    DRY_IDLE_MODE_FAN_ONLY,
    DRY_IDLE_MODE_OFF,
    MATTER_SYNC_TIMEOUT_SECONDS,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_DEFAULT_DEVICES_CONFIG = json.dumps({
    "200": {
        "t_work_mode": ["fan only", "cool", "dry", "auto"],
        "t_fan_speed": {
            "0": "auto",
            # "1": "super low",
            "2": "low",
            "3": "medium",
            "4": "high",
        },
        "t_up_down": {"0": "off", "1": "on"},
    },
    "201": {
        "t_work_mode": ["fan only", "cool", "dry", "auto"],
        "t_fan_speed": {
            "0": "auto",
            "1": "super low",
            "2": "low",
            "3": "medium",
            "4": "high",
        },
        "t_up_down": {"0": "off", "1": "on"},
    },
    "117": {
        "t_work_mode": ["fan only", "heat", "cool", "dry", "auto"],
        "t_fan_speed": {
            "0": "auto",
            "5": "super low",
            "6": "low",
            "7": "medium",
            "8": "high",
            "9": "super high",
        },
        "t_swing_direction": ["straight", "right", "both sides", "swing", "left"],
        "t_swing_angle": {
            "0": "swing",
            "2": "bottom 1/6",
            "3": "bottom 2/6",
            "4": "bottom 3/6",
            "5": "top 4/6",
            "6": "top 5/6",
            "7": "top 6/6",
        },
    },
})

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_BEEPING, default=False): bool,
    vol.Optional(CONF_TEMPERATURE_UNIT, default=TEMP_UNIT_CELSIUS): vol.In([
        TEMP_UNIT_CELSIUS,
        TEMP_UNIT_FAHRENHEIT,
    ]),
    vol.Optional(CONF_TEMPERATURE_SENSORS, default=False): bool,
    vol.Optional(CONF_DEVICES_CONFIG, default=_DEFAULT_DEVICES_CONFIG): str,
})


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options schema pre-filled with current values."""
    return vol.Schema({
        vol.Required(CONF_PASSWORD, default=current.get(CONF_PASSWORD, "")): str,
        vol.Optional(CONF_BEEPING, default=current.get(CONF_BEEPING, False)): bool,
        vol.Optional(
            CONF_TEMPERATURE_UNIT,
            default=current.get(CONF_TEMPERATURE_UNIT, TEMP_UNIT_CELSIUS),
        ): vol.In([TEMP_UNIT_CELSIUS, TEMP_UNIT_FAHRENHEIT]),
        vol.Optional(
            CONF_TEMPERATURE_SENSORS,
            default=current.get(CONF_TEMPERATURE_SENSORS, False),
        ): bool,
        vol.Optional(
            CONF_CURRENT_TEMP_ENTITY,
            description={"suggested_value": current.get(CONF_CURRENT_TEMP_ENTITY)},
        ): EntitySelector(
            EntitySelectorConfig(
                domain="sensor", device_class="temperature", multiple=False
            )
        ),
        vol.Optional(
            CONF_EXTERNAL_TEMP_ENABLED,
            default=current.get(CONF_EXTERNAL_TEMP_ENABLED, True),
        ): bool,
        vol.Optional(
            CONF_CURRENT_HUMIDITY_ENTITY,
            description={"suggested_value": current.get(CONF_CURRENT_HUMIDITY_ENTITY)},
        ): EntitySelector(
            EntitySelectorConfig(
                domain="sensor", device_class="humidity", multiple=False
            )
        ),
        vol.Optional(
            CONF_TARGET_HUMIDITY,
            description={"suggested_value": current.get(CONF_TARGET_HUMIDITY)},
        ): NumberSelector(
            NumberSelectorConfig(min=30, max=80, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(
            CONF_DRY_IDLE_MODE,
            default=current.get(CONF_DRY_IDLE_MODE, DRY_IDLE_MODE_FAN_ONLY),
        ): vol.In([DRY_IDLE_MODE_FAN_ONLY, DRY_IDLE_MODE_OFF]),
        vol.Optional(
            CONF_HUMIDITY_HYSTERESIS,
            default=current.get(CONF_HUMIDITY_HYSTERESIS, DEFAULT_HUMIDITY_HYSTERESIS),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=20,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="%",
            )
        ),
        vol.Optional(
            CONF_MATTER_CLIMATE_ENTITY,
            description={"suggested_value": current.get(CONF_MATTER_CLIMATE_ENTITY)},
        ): EntitySelector(
            EntitySelectorConfig(domain="climate", integration="matter", multiple=False)
        ),
        vol.Optional(
            CONF_MATTER_TEMPERATURE_SENSOR_ENTITY,
            description={
                "suggested_value": current.get(CONF_MATTER_TEMPERATURE_SENSOR_ENTITY)
            },
        ): EntitySelector(
            EntitySelectorConfig(
                domain="sensor", device_class="temperature", multiple=False
            )
        ),
        vol.Optional(
            CONF_MATTER_SYNC_TIMEOUT,
            default=current.get(CONF_MATTER_SYNC_TIMEOUT, MATTER_SYNC_TIMEOUT_SECONDS),
        ): NumberSelector(
            NumberSelectorConfig(
                min=5,
                max=300,
                step=5,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_TEMPERATURE_PRECISION,
            description={"suggested_value": current.get(CONF_TEMPERATURE_PRECISION)},
        ): NumberSelector(
            NumberSelectorConfig(min=0, max=1, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(
            CONF_HUMIDITY_PRECISION,
            description={"suggested_value": current.get(CONF_HUMIDITY_PRECISION)},
        ): NumberSelector(
            NumberSelectorConfig(min=0, max=2, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Optional(
            CONF_POLL_INTERVAL,
            default=current.get(CONF_POLL_INTERVAL, UPDATE_INTERVAL_SECONDS),
        ): NumberSelector(
            NumberSelectorConfig(
                min=10,
                max=3600,
                step=10,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_COMMAND_REFRESH_DELAY,
            default=current.get(
                CONF_COMMAND_REFRESH_DELAY, COMMAND_REFRESH_DELAY_SECONDS
            ),
        ): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=60,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_DEBOUNCE_DELAY,
            default=current.get(CONF_DEBOUNCE_DELAY, DEBOUNCE_DELAY_SECONDS),
        ): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=30,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Optional(
            CONF_DEVICES_CONFIG,
            default=current.get(CONF_DEVICES_CONFIG, _DEFAULT_DEVICES_CONFIG),
        ): str,
        vol.Optional(
            CONF_DEBUG_LOGGING,
            default=current.get(CONF_DEBUG_LOGGING, False),
        ): bool,
    })


class ConnectLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the ConnectLife config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ConnectLifeOptionsFlow:
        """Return the options flow handler."""
        return ConnectLifeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            devices_config_raw = user_input.get(CONF_DEVICES_CONFIG, "{}")
            try:
                json.loads(devices_config_raw)
            except json.JSONDecodeError:
                errors[CONF_DEVICES_CONFIG] = "invalid_json"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()

                session = async_get_clientsession(self.hass)
                api = ConnectLifeApi(
                    session,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    self.hass,
                )
                try:
                    valid = await api.validate_credentials()
                    if not valid:
                        errors["base"] = "invalid_auth"
                except ConnectLifeAuthError:
                    errors["base"] = "invalid_auth"
                except aiohttp.ClientError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during credential validation")
                    errors["base"] = "unknown"

                if not errors:
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME],
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )


class ConnectLifeOptionsFlow(OptionsFlow):
    """Handle ConnectLife options (reconfigure after setup)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        # Merge entry data + previously saved options so current values are shown
        current = {**self._config_entry.data, **self._config_entry.options}

        if user_input is not None:
            devices_config_raw = user_input.get(CONF_DEVICES_CONFIG, "{}")
            try:
                json.loads(devices_config_raw)
            except json.JSONDecodeError:
                errors[CONF_DEVICES_CONFIG] = "invalid_json"
            else:
                # Re-validate credentials only if the password changed
                new_password = user_input[CONF_PASSWORD]
                if new_password != current.get(CONF_PASSWORD):
                    session = async_get_clientsession(self.hass)
                    api = ConnectLifeApi(
                        session,
                        self._config_entry.data[CONF_USERNAME],
                        new_password,
                        self.hass,
                    )
                    try:
                        valid = await api.validate_credentials()
                        if not valid:
                            errors["base"] = "invalid_auth"
                    except ConnectLifeAuthError:
                        errors["base"] = "invalid_auth"
                    except aiohttp.ClientError:
                        errors["base"] = "cannot_connect"
                    except Exception:
                        _LOGGER.exception(
                            "Unexpected error during credential validation"
                        )
                        errors["base"] = "unknown"

                if not errors:
                    # Persist options and update the config entry data with new password
                    self.hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={**self._config_entry.data, CONF_PASSWORD: new_password},
                    )
                    return self.async_create_entry(
                        title="",
                        data={
                            CONF_BEEPING: user_input[CONF_BEEPING],
                            CONF_TEMPERATURE_UNIT: user_input[CONF_TEMPERATURE_UNIT],
                            CONF_TEMPERATURE_SENSORS: user_input[
                                CONF_TEMPERATURE_SENSORS
                            ],
                            CONF_CURRENT_TEMP_ENTITY: user_input.get(
                                CONF_CURRENT_TEMP_ENTITY
                            ),
                            CONF_EXTERNAL_TEMP_ENABLED: user_input.get(
                                CONF_EXTERNAL_TEMP_ENABLED, True
                            ),
                            CONF_CURRENT_HUMIDITY_ENTITY: user_input.get(
                                CONF_CURRENT_HUMIDITY_ENTITY
                            ),
                            CONF_TARGET_HUMIDITY: user_input.get(CONF_TARGET_HUMIDITY),
                            CONF_DRY_IDLE_MODE: user_input.get(
                                CONF_DRY_IDLE_MODE, DRY_IDLE_MODE_FAN_ONLY
                            ),
                            CONF_HUMIDITY_HYSTERESIS: user_input.get(
                                CONF_HUMIDITY_HYSTERESIS, DEFAULT_HUMIDITY_HYSTERESIS
                            ),
                            CONF_MATTER_CLIMATE_ENTITY: user_input.get(
                                CONF_MATTER_CLIMATE_ENTITY
                            ),
                            CONF_MATTER_TEMPERATURE_SENSOR_ENTITY: user_input.get(
                                CONF_MATTER_TEMPERATURE_SENSOR_ENTITY
                            ),
                            CONF_MATTER_SYNC_TIMEOUT: user_input.get(
                                CONF_MATTER_SYNC_TIMEOUT, MATTER_SYNC_TIMEOUT_SECONDS
                            ),
                            CONF_TEMPERATURE_PRECISION: user_input.get(
                                CONF_TEMPERATURE_PRECISION
                            ),
                            CONF_HUMIDITY_PRECISION: user_input.get(
                                CONF_HUMIDITY_PRECISION
                            ),
                            CONF_POLL_INTERVAL: user_input.get(
                                CONF_POLL_INTERVAL, UPDATE_INTERVAL_SECONDS
                            ),
                            CONF_COMMAND_REFRESH_DELAY: user_input.get(
                                CONF_COMMAND_REFRESH_DELAY,
                                COMMAND_REFRESH_DELAY_SECONDS,
                            ),
                            CONF_DEBOUNCE_DELAY: user_input.get(
                                CONF_DEBOUNCE_DELAY, DEBOUNCE_DELAY_SECONDS
                            ),
                            CONF_DEVICES_CONFIG: user_input[CONF_DEVICES_CONFIG],
                            CONF_DEBUG_LOGGING: user_input.get(
                                CONF_DEBUG_LOGGING, False
                            ),
                        },
                    )

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )
