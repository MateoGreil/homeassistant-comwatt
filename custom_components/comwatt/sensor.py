"""Platform for sensor integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ComwattConfigEntry
from .const import DOMAIN
from .coordinator import ComwattCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComwattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Instantiate sensor entities from the coordinator's discovered topology."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for site in coordinator.sites:
        entities.append(ComwattAutoProductionRateSensor(coordinator, site))
    for _site, device in coordinator.sensor_devices:
        entities.append(ComwattPowerSensor(coordinator, device))
        entities.append(ComwattEnergySensor(coordinator, device))
    async_add_entities(entities)


class ComwattSensor(CoordinatorEntity[ComwattCoordinator], SensorEntity):
    """Base class with shared device_info and coordinator wiring."""

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


class ComwattAutoProductionRateSensor(ComwattSensor):
    """Site-level auto-production rate as a percentage."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ComwattCoordinator, site: dict[str, Any]) -> None:
        super().__init__(coordinator, site)
        self._attr_unique_id = f"site_{site['id']}_auto_production_rate"
        self._attr_name = f"{site['name']} Auto Production Rate"

    @property
    def native_value(self) -> float | None:
        site_data = self.coordinator.data["sites"].get(self._device["id"])
        return site_data.get("auto_production_rate") if site_data else None

    @property
    def available(self) -> bool:
        return super().available and self._device["id"] in self.coordinator.data["sites"]


class ComwattPowerSensor(ComwattSensor):
    """Instantaneous power consumption/production in watts."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ComwattCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device['id']}_power"
        self._attr_name = f"{device['name']} Power"

    @property
    def native_value(self) -> float | None:
        device_data = self.coordinator.data["devices"].get(self._device["id"])
        return device_data.get("power") if device_data else None

    @property
    def available(self) -> bool:
        return super().available and self._device["id"] in self.coordinator.data["devices"]


class ComwattEnergySensor(ComwattSensor):
    """Accumulated energy total in watt-hours.

    The running total lives in the coordinator so the bookkeeping survives
    every poll; it still resets on HA restart — that's finding H4, tracked
    separately.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: ComwattCoordinator, device: dict[str, Any]) -> None:
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{device['id']}_total_energy"
        self._attr_name = f"{device['name']} Total Energy"

    @property
    def native_value(self) -> float | None:
        device_data = self.coordinator.data["devices"].get(self._device["id"])
        return device_data.get("energy") if device_data else None

    @property
    def available(self) -> bool:
        return super().available and self._device["id"] in self.coordinator.data["devices"]
