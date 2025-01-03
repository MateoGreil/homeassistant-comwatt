"""Platform for sensor integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPower,
    UnitOfEnergy
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_utc_time_change,
)

import asyncio
from datetime import timedelta
from .const import DOMAIN
from .client import comwatt_client

async def async_setup_entry(hass, entry, async_add_entities):
    new_devices = []
    sites = await asyncio.to_thread(lambda: comwatt_client.get_sites())
    for site in sites:
        devices = await asyncio.to_thread(lambda: comwatt_client.get_devices(site['id']))
        for device in devices:
            if 'id' in device:
                if 'partChilds' in device and len(device['partChilds']) > 0:
                    childs = device["partChilds"]
                    for child in childs:
                        if 'id' in child:
                            new_devices.append(ComwattPowerSensor(entry, child))
                            new_devices.append(ComwattEnergySensor(entry, child))
                else:
                    new_devices.append(ComwattPowerSensor(entry, device))
                    new_devices.append(ComwattEnergySensor(entry, device))

    # TODO: Remove existing devices?
    # TODO: Remove old existing devices?

    if new_devices:
        async_add_entities(new_devices, update_before_add=True)

class ComwattSensor(SensorEntity):
    @property
    def should_poll(self) -> bool:
        return False

    @property
    def device_info(self) -> DeviceInfo:
        """Return te devce info."""
        if 'deviceKind' in self._device and 'code' in self._device['deviceKind']:
            model = self._device['deviceKind']['code']
        else:
            model = None

        return DeviceInfo(
            identifiers={
                ("comwatt", self._device['name'])
            },
            manufacturer='Comwatt',
            name=self._device['name'],
            model=model
        )

    async def _async_update(self, *args):
        await self.hass.async_add_executor_job(self.update)

class ComwattEnergySensor(ComwattSensor):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, entry, device):
        self._device = device
        self._username = entry.data["username"]
        self._password = entry.data["password"]
        self._attr_unique_id = f"{self._device['id']}_total_energy"
        self._attr_name = f"{self._device['name']} Total Energy"

    # TODO: Update it ~ only 1 per hour
    def update(self) -> None:
        """Fetch new state data for the sensor."""

        try:
            time_series_data = comwatt_client.get_device_ts_time_ago(self._device["id"], "VIRTUAL_QUANTITY", "HOUR", "NONE")
        except Exception:
            comwatt_client.authenticate(self._username, self._password)
            time_series_data = comwatt_client.get_device_ts_time_ago(self._device["id"], "VIRTUAL_QUANTITY", "HOUR", "NONE")

        if self._attr_native_value == None:
            self._last_native_value_at = 0
            self._attr_native_value = 0

        # TODO: Update to the time of comwatt and not the current time
        if time_series_data["timestamps"] and self._last_native_value_at != time_series_data["timestamps"][-1]:
            self._last_native_value_at = time_series_data["timestamps"][-1]
            self._attr_native_value += time_series_data["values"][-1]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_utc_time_change(self.hass, self._async_update, minute=55, second=0)
        )

class ComwattPowerSensor(ComwattSensor):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, device):
        self._device = device
        self._username = entry.data["username"]
        self._password = entry.data["password"]
        self._attr_unique_id = f"{self._device['id']}_power"
        self._attr_name = f"{self._device['name']} Power"

    def update(self) -> None:
        """Fetch new state data for the sensor."""

        try:
            time_series_data = comwatt_client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)
        except Exception:
            comwatt_client.authenticate(self._username, self._password)
            time_series_data = comwatt_client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)

        # TODO: Update to the time of comwatt and not the current time
        if time_series_data["values"]:
            self._attr_native_value = time_series_data["values"][-1]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_update, timedelta(minutes=2))
        )
