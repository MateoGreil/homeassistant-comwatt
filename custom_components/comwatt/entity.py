"""Shared entity base for the Comwatt integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ComwattCoordinator


class ComwattEntity(CoordinatorEntity[ComwattCoordinator]):
    """Common device_info for every Comwatt entity (sensor or switch)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ComwattCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._device = device

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        if "deviceKind" in self._device and "code" in self._device["deviceKind"]:
            model = self._device["deviceKind"]["code"]
        elif "siteKind" in self._device:
            model = self._device["siteKind"]
        else:
            model = None
        return DeviceInfo(
            identifiers={(DOMAIN, self._device["name"])},
            manufacturer="Comwatt",
            name=self._device["name"],
            model=model,
        )
