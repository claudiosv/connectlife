"""Button platform for ConnectLife — manual device refresh."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ConnectLifeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a refresh button for each ConnectLife device."""
    coordinator: ConnectLifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ConnectLifeRefreshButton(coordinator, puid, device)
        for puid, device in coordinator.data.items()
    )


class ConnectLifeRefreshButton(CoordinatorEntity[ConnectLifeCoordinator], ButtonEntity):
    """Button that triggers an immediate coordinator refresh.

    Calling async_request_refresh() both fetches fresh state right away and
    resets the automatic poll timer, so the next scheduled poll is a full
    update_interval after this one completes.
    """

    _attr_has_entity_name = True
    _attr_name = "Refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        coordinator: ConnectLifeCoordinator,
        puid: str,
        device: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{puid}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, puid)},
            name=device.get("deviceNickName", puid),
            manufacturer="ConnectLife",
            model=f"{device.get('deviceTypeCode', '')}-{device.get('deviceFeatureCode', '')}",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
