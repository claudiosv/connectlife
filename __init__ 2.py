"""Add missing (vendor_id, product_id) to Matter DRY/FAN_ONLY allowlists."""
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_platform

# decimal (vendor_id, product_id) — pull from device diagnostics JSON
DEVICES: set[tuple[int, int]] = {
    (0x138C, 0x3601),  # replace with yours
}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async def _patch(_event: Event) -> None:
        from homeassistant.components.matter import climate as matter_climate

        matter_climate.SUPPORT_DRY_MODE_DEVICES.update(DEVICES)
        matter_climate.SUPPORT_FAN_MODE_DEVICES.update(DEVICES)

        for platform in entity_platform.async_get_platforms(hass, "matter"):
            if platform.domain != "climate":
                continue
            for entity in platform.entities.values():
                entity._feature_map = None  # invalidate cache so recalc runs
                entity._calculate_features()
                entity._update_hvac_mode_and_action()
                entity.async_write_ha_state()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _patch)
    return True