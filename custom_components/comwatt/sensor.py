"""Platform for sensor integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
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
                        new_devices.append(ComwattSensor(client, entry.data["username"], entry.data["password"], child))
                else:
                    new_devices.append(ComwattSensor(client, entry.data["username"], entry.data["password"], device))
    # TODO: Remove existing devices
    if new_devices:
        async_add_entities(new_devices)


class ComwattSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, client, username, password, device):
        self._device = device
        self._client = client
        self._username = username
        self._password = password
        self._attr_unique_id = f"{self._device['id']}_power"
        self._attr_name = f"{self._device['name']} Power"

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        #TODO: Improve this handle of the deconnection of the API
        try:
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)
        except Exception:
            self._client = ComwattClient()
            self._client.authenticate(self._username, self._password)
            time_series_data = self._client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)

        # TODO: Fix the state and native_value
        # TODO: Update to the time of comwatt and not the current time
        self._attr_native_value = time_series_data["values"][0]
        self._state = time_series_data["values"][0]
