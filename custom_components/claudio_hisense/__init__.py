"""The ConnectLife integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow, entity_platform

from .api import ConnectLifeApi
from .const import (
    CONF_DEBUG_LOGGING,
    CONF_OAUTH_REDIRECT_URI,
    CONF_POLL_INTERVAL,
    CONF_POLL_INTERVAL_ENABLED,
    DEFAULT_OAUTH_REDIRECT_URI,
    DOMAIN,
    LOG_LEVEL_DEBUG,
    LOG_LEVEL_DEFAULT,
    LOG_LEVEL_INFO,
    MATTER_DRY_FAN_DEVICES,
    UPDATE_INTERVAL_SECONDS,
)
from .coordinator import ConnectLifeCoordinator
from .oauth2 import ConnectLifeOAuth2Implementation, OAuth2Session
from .websocket import ConnectLifeWebSocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _apply_debug_logging(cfg: dict) -> None:
    """Enable or reset verbose debug logging for every module in this package.

    Submodule loggers (climate.py, api.py, etc.) all use
    logging.getLogger(__name__), which makes them children of this package's
    own logger — setting the level here is enough for it to apply to all of
    them, since a child with no explicit level inherits its parent's.
    """
    package_logger = logging.getLogger(__package__)
    level = cfg.get(CONF_DEBUG_LOGGING, LOG_LEVEL_DEFAULT)
    # Migrate the old on/off checkbox's stored bool values.
    if level is True:
        level = LOG_LEVEL_DEBUG
    elif level is False:
        level = LOG_LEVEL_DEFAULT

    if level == LOG_LEVEL_DEBUG:
        package_logger.setLevel(logging.DEBUG)
        _LOGGER.debug("Debug logging enabled for %s", __package__)
    elif level == LOG_LEVEL_INFO:
        package_logger.setLevel(logging.INFO)
    else:
        package_logger.setLevel(logging.NOTSET)


def entry_config(entry: ConfigEntry) -> dict:
    """Return merged config: entry.data overridden by entry.options."""
    return {**entry.data, **entry.options}


def _matter_devices(coordinator: ConnectLifeCoordinator) -> set[tuple[int, int]]:
    """Collect (vendor_id, product_id) for devices provisioned as Matter devices."""
    devices: set[tuple[int, int]] = set()
    for device in coordinator.data.values():
        status = device.get("statusList", {})
        if not status.get("f_addmatterdevice"):
            continue
        vendor_id = status.get("f_matterOriginalVendorId")
        product_id = status.get("f_matterOriginalProductId")
        if vendor_id and product_id:
            devices.add((int(vendor_id), int(product_id)))
    _LOGGER.debug("Discovered %d Matter-provisioned ConnectLife device(s): %s", len(devices), devices)
    return devices


async def _async_patch_matter_dry_fan_support(
    hass: HomeAssistant, devices: set[tuple[int, int]]
) -> None:
    """Add (vendor_id, product_id) pairs to Matter's DRY/FAN_ONLY allowlists.

    Home Assistant's built-in Matter integration hardcodes which devices support
    DRY/FAN_ONLY HVAC modes; ConnectLife AC units are missing from that allowlist,
    so climate entities created via Matter never expose those modes.
    """
    from homeassistant.components.matter import climate as matter_climate

    _LOGGER.debug("Patching Matter DRY/FAN_ONLY allowlists with devices=%s", devices)
    matter_climate.SUPPORT_DRY_MODE_DEVICES.update(devices)
    matter_climate.SUPPORT_FAN_MODE_DEVICES.update(devices)

    patched = 0
    for platform in entity_platform.async_get_platforms(hass, "matter"):
        if platform.domain != "climate":
            continue
        for entity in platform.entities.values():
            # Private Matter climate internals — no public API to force a
            # feature-map recalculation after the allowlist changes underneath it.
            entity._feature_map = None  # type: ignore[attr-defined]
            entity._calculate_features()  # type: ignore[attr-defined]
            entity._update_hvac_mode_and_action()  # type: ignore[attr-defined]
            entity.async_write_ha_state()
            patched += 1
    _LOGGER.debug("Recalculated features for %d Matter climate entities", patched)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ConnectLife from a config entry."""
    cfg = entry_config(entry)
    _apply_debug_logging(cfg)
    _LOGGER.debug("Setting up ConnectLife entry %s (title=%r)", entry.entry_id, entry.title)

    # Built directly rather than via config_entry_oauth2_flow's implementation
    # registry: ConnectLife's OAuth server only accepts a fixed,
    # pre-registered redirect URI (configurable at setup time — see
    # config_flow.py), which the registry's one-implementation-per-domain
    # model can't represent per config entry.
    redirect_uri = entry.data.get(CONF_OAUTH_REDIRECT_URI, DEFAULT_OAUTH_REDIRECT_URI)
    implementation = ConnectLifeOAuth2Implementation(hass, redirect_uri=redirect_uri)
    ha_oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    await ha_oauth_session.async_ensure_token_valid()
    oauth_session = OAuth2Session(hass, implementation, token=dict(entry.data.get("token", {})))

    api = ConnectLifeApi(oauth_session=oauth_session, hass=hass)

    poll_interval = (
        int(cfg.get(CONF_POLL_INTERVAL, UPDATE_INTERVAL_SECONDS))
        if cfg.get(CONF_POLL_INTERVAL_ENABLED, True)
        else None
    )
    coordinator = ConnectLifeCoordinator(hass, api, poll_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    websocket = ConnectLifeWebSocket(hass, api, coordinator.async_handle_push_update)
    await websocket.async_connect()
    coordinator.websocket = websocket

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever options are changed via the UI
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    matter_devices = MATTER_DRY_FAN_DEVICES | _matter_devices(coordinator)
    if hass.is_running:
        await _async_patch_matter_dry_fan_support(hass, matter_devices)
    else:
        patch_fired = False

        async def _patch(_event: Event) -> None:
            nonlocal patch_fired
            patch_fired = True
            _LOGGER.debug("homeassistant_started fired, applying Matter allowlist patch")
            await _async_patch_matter_dry_fan_support(hass, matter_devices)

        remove_listener = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, _patch
        )

        def _cleanup_listener() -> None:
            # async_listen_once already removes itself once the event fires;
            # calling remove_listener() again logs "unknown job listener".
            if not patch_fired:
                remove_listener()

        entry.async_on_unload(_cleanup_listener)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    _LOGGER.debug("Options updated for entry %s, reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a ConnectLife config entry."""
    _LOGGER.debug("Unloading ConnectLife entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ConnectLifeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        if coordinator.websocket is not None:
            await coordinator.websocket.async_disconnect()
        await coordinator.api.oauth_session.close()
    return unload_ok
