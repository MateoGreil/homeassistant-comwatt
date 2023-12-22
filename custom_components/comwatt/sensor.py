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

import asyncio
from .const import DOMAIN
from comwatt_client import ComwattClient

async def async_setup_entry(hass, entry, async_add_entities):
    client = hass.data[DOMAIN][entry.entry_id]

    new_devices = []
    sites = await asyncio.to_thread(lambda: client.get_sites())
    for site in sites:
        devices = await asyncio.to_thread(lambda: client.get_devices(site['id']))
        for device in devices:
            if 'id' in device:
                if 'partChilds' in device and len(device['partChilds']) > 0:
                    childs = device["partChilds"]
                    for child in childs:
                        new_devices.append(ComwattPowerSensor(client, entry.data["username"], entry.data["password"], entry.data["api"], child))
                        new_devices.append(ComwattEnergySensor(client, entry.data["username"], entry.data["password"], entry.data["api"], child))
                else:
                    new_devices.append(ComwattPowerSensor(client, entry.data["username"], entry.data["password"], entry.data["api"], device))
                    new_devices.append(ComwattEnergySensor(client, entry.data["username"], entry.data["password"], entry.data["api"], device))
    # TODO: Remove existing devices?
    # TODO: Remove old existing devices?
    if new_devices:
        async_add_entities(new_devices)

class ComwattEnergySensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, client, username, password, api, device):
        self._device = device
        self._client = client
        self._username = username
        self._password = password
        self._api = api
        self._attr_unique_id = f"{self._device['id']}_total_energy"
        self._attr_name = f"{self._device['name']} Total Energy"

    # TODO: Update it ~ only 1 per hour
    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        # TODO: Better handling the API disconnection (do not create a client for each sensor)
        try:
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "VIRTUAL_QUANTITY", "HOUR", "NONE")
        except Exception:
            self._client = ComwattClient(self._api)
            self._client.authenticate(self._username, self._password)
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "VIRTUAL_QUANTITY", "HOUR", "NONE")

        if self._attr_native_value == None:
            self._last_native_value_at = 0
            self._attr_native_value = 0

        # TODO: Update to the time of comwatt and not the current time
        if self._last_native_value_at != time_series_data["timestamps"][0]:
            self._last_native_value_at = time_series_data["timestamps"][0]
            self._attr_native_value += time_series_data["values"][0]

class ComwattPowerSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client, username, password, api, device):
        self._device = device
        self._client = client
        self._username = username
        self._password = password
        self._api = api
        self._attr_unique_id = f"{self._device['id']}_power"
        self._attr_name = f"{self._device['name']} Power"

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        # TODO: Better handling the API disconnection (do not create a client for each sensor)
        try:
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)
        except Exception:
            self._client = ComwattClient(self._api)
            self._client.authenticate(self._username, self._password)
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)

        # TODO: Update to the time of comwatt and not the current time
        self._attr_native_value = time_series_data["values"][0]
