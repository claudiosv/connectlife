"""The ConnectLife integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConnectLifeApi
from .const import (
    CONF_POLL_INTERVAL,
    DOMAIN,
    MATTER_DRY_FAN_DEVICES,
    UPDATE_INTERVAL_SECONDS,
)
from .coordinator import ConnectLifeCoordinator

PLATFORMS = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.SENSOR,
    Platform.SWITCH,
]


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

    matter_climate.SUPPORT_DRY_MODE_DEVICES.update(devices)
    matter_climate.SUPPORT_FAN_MODE_DEVICES.update(devices)

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ConnectLife from a config entry."""
    cfg = entry_config(entry)
    session = async_get_clientsession(hass)
    api = ConnectLifeApi(
        session=session,
        username=cfg[CONF_USERNAME],
        password=cfg[CONF_PASSWORD],
        hass=hass,
    )

    poll_interval = int(cfg.get(CONF_POLL_INTERVAL, UPDATE_INTERVAL_SECONDS))
    coordinator = ConnectLifeCoordinator(hass, api, poll_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

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
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a ConnectLife config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
