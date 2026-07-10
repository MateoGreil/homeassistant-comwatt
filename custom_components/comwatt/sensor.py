"""Platform for sensor integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComwattConfigEntry
from .coordinator import ComwattCoordinator
from .entity import ComwattEntity


@dataclass(frozen=True, kw_only=True)
class ComwattSiteMetricDescription(SensorEntityDescription):
    """Describes a site-level sensor driven from `coordinator.data["sites"]`.

    `key` is the internal metric name used both as the dict key in the
    coordinator snapshot and as the second segment of the entity's
    `unique_id` (`site_{site_id}_{key}`).
    """

    friendly_suffix: str


def _rate(key: str, friendly_suffix: str) -> ComwattSiteMetricDescription:
    return ComwattSiteMetricDescription(
        key=key,
        friendly_suffix=friendly_suffix,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    )


def _delta(key: str, friendly_suffix: str) -> ComwattSiteMetricDescription:
    """Per-hour energy delta, not a cumulative counter — no `device_class=ENERGY`.

    HA rejects `ENERGY` paired with `MEASUREMENT`; use the `{device}_total_energy`
    entity for Energy-dashboard-quality cumulative figures.
    """
    return ComwattSiteMetricDescription(
        key=key,
        friendly_suffix=friendly_suffix,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    )


SITE_METRICS: tuple[ComwattSiteMetricDescription, ...] = (
    # Existing sensor — `key` preserved so the unique_id is unchanged.
    _rate("auto_production_rate", "Auto Production Rate"),
    _rate("auto_consumption_rate", "Auto Consumption Rate"),
    _rate("injection_rate", "Injection Rate"),
    _rate("withdrawal_rate", "Withdrawal Rate"),
    _delta("production", "Production"),
    _delta("consumption", "Consumption"),
    _delta("injection", "Injection"),
    _delta("withdrawal", "Withdrawal"),
    _delta("charge", "Charge"),
    _delta("discharge", "Discharge"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComwattConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Instantiate sensor entities from the coordinator's discovered topology."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for site in coordinator.sites:
        for description in SITE_METRICS:
            entities.append(ComwattSiteMetricSensor(coordinator, site, description))
    for device in coordinator.sensor_devices:
        entities.append(ComwattPowerSensor(coordinator, device))
        entities.append(ComwattEnergySensor(coordinator, device))
    async_add_entities(entities)


class ComwattSensor(ComwattEntity, SensorEntity):
    """Base class with shared device_info and coordinator wiring."""


class ComwattSiteMetricSensor(ComwattSensor):
    """Generic site-level sensor driven by a `ComwattSiteMetricDescription`."""

    entity_description: ComwattSiteMetricDescription

    def __init__(
        self,
        coordinator: ComwattCoordinator,
        site: dict[str, Any],
        description: ComwattSiteMetricDescription,
    ) -> None:
        super().__init__(coordinator, site)
        self.entity_description = description
        self._attr_unique_id = f"site_{site['id']}_{description.key}"
        self._attr_name = description.friendly_suffix

    @property
    def native_value(self) -> float | None:
        site_data = self.coordinator.data["sites"].get(self._device["id"])
        if not site_data:
            return None
        value = site_data.get(self.entity_description.key)
        if value is None:
            return None
        # Rates come from the API as 0-1 ratios; the % rendering is a sensor
        # concern, so the scalar lives next to its PERCENTAGE unit — the only
        # place that knows whether this is a rate.
        if self.entity_description.native_unit_of_measurement == PERCENTAGE:
            value *= 100
        return value

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
        self._attr_name = "Power"

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
        self._attr_name = "Total Energy"

    @property
    def native_value(self) -> float | None:
        device_data = self.coordinator.data["devices"].get(self._device["id"])
        return device_data.get("energy") if device_data else None

    @property
    def available(self) -> bool:
        return super().available and self._device["id"] in self.coordinator.data["devices"]
