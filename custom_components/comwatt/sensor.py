"""Platform for sensor integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

import asyncio
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]

    new_devices = []
    sites = await asyncio.to_thread(lambda: client.get_sites())
    for site in sites:
        devices = await asyncio.to_thread(lambda: client.get_devices(site['id']))
        for device in devices:
            if 'id' in device:
                new_devices.append(ComwattSensor(client, device))
    # TODO: Remove existing devices
    if new_devices:
        async_add_entities(new_devices)


class ComwattSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, client, device):
        self._device = device
        self._client = client
        self._attr_unique_id = f"{self._device['id']}_energy"
        self._attr_name = f"{self._device['name']} Energy"

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        time_series_data = self._client.get_device_ts_time_ago(self._device["id"])

        # TODO: Fix the state and native_value
        self._attr_native_value = time_series_data["values"][-1]
        self._state = time_series_data["values"][-1]
        return self


