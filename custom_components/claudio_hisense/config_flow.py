"""Config flow for the ConnectLife integration."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

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
    CONF_OAUTH_REDIRECT_URI,
    CONF_POLL_INTERVAL,
    CONF_SENSOR_CONTROL_MIN_INTERVAL,
    CONF_TARGET_HUMIDITY,
    CONF_TEMPERATURE_PRECISION,
    CONF_TEMPERATURE_SENSORS,
    CONF_TEMPERATURE_UNIT,
    CONF_THERMOSTAT_FORCING_ENABLED,
    DEBOUNCE_DELAY_SECONDS,
    DEFAULT_HUMIDITY_HYSTERESIS,
    DEFAULT_OAUTH_REDIRECT_URI,
    DOMAIN,
    DRY_IDLE_MODE_FAN_ONLY,
    DRY_IDLE_MODE_OFF,
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_DEFAULT,
    LOG_LEVEL_INFO,
    MATTER_SYNC_TIMEOUT_SECONDS,
    SENSOR_CONTROL_MIN_INTERVAL_SECONDS,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    UPDATE_INTERVAL_SECONDS,
)
from .oauth2 import ConnectLifeOAuth2Implementation

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

def _normalize_log_level(value: Any) -> str:
    """Migrate the old on/off debug_logging checkbox's stored bool values."""
    if value is True:
        return LOG_LEVEL_DEBUG
    if value is False or value is None:
        return LOG_LEVEL_DEFAULT
    return value


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    """Build the options schema pre-filled with current values."""
    return vol.Schema({
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
            CONF_THERMOSTAT_FORCING_ENABLED,
            default=current.get(CONF_THERMOSTAT_FORCING_ENABLED, False),
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
            CONF_SENSOR_CONTROL_MIN_INTERVAL,
            default=current.get(
                CONF_SENSOR_CONTROL_MIN_INTERVAL, SENSOR_CONTROL_MIN_INTERVAL_SECONDS
            ),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=600,
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
            default=_normalize_log_level(current.get(CONF_DEBUG_LOGGING)),
        ): vol.In([LOG_LEVEL_DEFAULT, LOG_LEVEL_INFO, LOG_LEVEL_DEBUG]),
    })


class ConnectLifeConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the ConnectLife config flow via OAuth2 browser login."""

    DOMAIN = DOMAIN
    VERSION = 1

    _redirect_uri: str

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict:
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ConnectLifeOptionsFlow:
        """Return the options flow handler."""
        return ConnectLifeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the OAuth redirect URI, then hand off to OAuth2.

        ConnectLife's OAuth server only accepts a fixed, pre-registered
        redirect URI — it will not redirect to Home Assistant's own
        dynamically-computed external URL like most OAuth2 integrations do.
        So instead of picking a registered implementation, this builds one
        directly with the redirect URI the user confirms/edits here.
        """
        await self.async_set_unique_id(DOMAIN)
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(
                        CONF_OAUTH_REDIRECT_URI, default=DEFAULT_OAUTH_REDIRECT_URI
                    ): str,
                }),
            )

        self._redirect_uri = user_input[CONF_OAUTH_REDIRECT_URI]
        self.flow_impl = ConnectLifeOAuth2Implementation(
            self.hass, redirect_uri=self._redirect_uri
        )
        return await self.async_step_auth()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create the config entry once the OAuth2 token exchange completes."""
        return self.async_create_entry(
            title="ConnectLife",
            data={
                **data,
                CONF_OAUTH_REDIRECT_URI: self._redirect_uri,
                CONF_BEEPING: False,
                CONF_TEMPERATURE_UNIT: TEMP_UNIT_CELSIUS,
                CONF_TEMPERATURE_SENSORS: False,
                CONF_DEVICES_CONFIG: _DEFAULT_DEVICES_CONFIG,
            },
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
                if not errors:
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
                            CONF_THERMOSTAT_FORCING_ENABLED: user_input.get(
                                CONF_THERMOSTAT_FORCING_ENABLED, False
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
                            CONF_SENSOR_CONTROL_MIN_INTERVAL: user_input.get(
                                CONF_SENSOR_CONTROL_MIN_INTERVAL,
                                SENSOR_CONTROL_MIN_INTERVAL_SECONDS,
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
                                CONF_DEBUG_LOGGING, LOG_LEVEL_DEFAULT
                            ),
                        },
                    )

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )
