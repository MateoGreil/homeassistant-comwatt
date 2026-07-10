"""Platform for switch integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComwattConfigEntry
from .coordinator import ComwattCoordinator
from .entity import ComwattEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComwattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Instantiate switch entities from the coordinator's discovered topology."""
    coordinator = entry.runtime_data
    async_add_entities(
        ComwattSwitch(coordinator, device)
        for device in coordinator.switch_devices
    )


class ComwattSwitch(ComwattEntity, SwitchEntity):
    """A Comwatt device exposing a POWER_SWITCH or RELAY capacity."""

    def __init__(self, coordinator: ComwattCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device['id']}_switch"
        self._attr_name = "Switch"

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.data["switches"].get(self._device["id"])
        return state.get("is_on") if state else None

    @property
    def available(self) -> bool:
        state = self.coordinator.data["switches"].get(self._device["id"])
        return (
            super().available
            and state is not None
            and state.get("capacity_id") is not None
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        state = self.coordinator.data["switches"].get(self._device["id"])
        if not state or not state.get("capacity_id"):
            return
        await self.coordinator.async_set_switch(state["capacity_id"], on)
