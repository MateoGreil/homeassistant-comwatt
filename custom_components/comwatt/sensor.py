"""Platform for sensor integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPower,
    UnitOfEnergy,
    PERCENTAGE
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.device_registry import DeviceInfo

import asyncio
from .const import DOMAIN
from comwatt_client import ComwattClient
from datetime import timedelta

SCAN_INTERVAL = timedelta(minutes=2)

async def async_setup_entry(hass, entry, async_add_entities):
    client = ComwattClient()
    client.session.cookies.update(hass.data[DOMAIN]["cookies"])

    new_devices = []
    sites = await asyncio.to_thread(lambda: client.get_sites())
    for site in sites:
        new_devices.append(ComwattAutoProductionRateSensor(entry, site))

        devices = await asyncio.to_thread(lambda: client.get_devices(site['id']))
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
        async_add_entities(new_devices)

class ComwattSensor(SensorEntity):
    @property
    def device_info(self) -> DeviceInfo:
        """Return te device info."""
        if 'deviceKind' in self._device and 'code' in self._device['deviceKind']:
            model = self._device['deviceKind']['code']
        elif 'siteKind' in self._device:
            model = self._device['siteKind']
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

class ComwattAutoProductionRateSensor(ComwattSensor):
    """Representation of an Auto Production Rate Sensor."""
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry, device):
        self._device = device
        self._username = entry.data["username"]
        self._password = entry.data["password"]
        self._attr_unique_id = f"site_{self._device['id']}_auto_production_rate"
        self._attr_name = f"{self._device['name']} Auto Production Rate"

    def update(self) -> None:
        """Fetch new state data for the sensor."""
        client = ComwattClient()
        client.session.cookies.update(self.hass.data[DOMAIN]["cookies"])
        try:
            time_series_data = client.get_site_networks_ts_time_ago(self._device["id"], "FLOW", "NONE", None, "HOUR", 1)
            self._attr_native_value = auto_production_rate
        except Exception:
            client.authenticate(self._username, self._password)
            self.hass.data[DOMAIN]["cookies"] = client.session.cookies.get_dict()
            time_series_data = client.get_site_networks_ts_time_ago(self._device["id"], "FLOW", "NONE", None, "HOUR", 1)

        # TODO: Update to the time of comwatt and not the current time
        if time_series_data["autoproductionRates"]:
            self._attr_native_value = time_series_data["autoproductionRates"][-1] * 100


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
        client = ComwattClient()
        client.session.cookies.update(self.hass.data[DOMAIN]["cookies"])

        try:
            time_series_data = client.get_device_ts_time_ago(self._device["id"], "QUANTITY", "HOUR", "NONE")
        except Exception:
            client.authenticate(self._username, self._password)
            self.hass.data[DOMAIN]["cookies"] = client.session.cookies.get_dict()
            time_series_data = client.get_device_ts_time_ago(self._device["id"], "QUANTITY", "HOUR", "NONE")

        if self._attr_native_value == None:
            self._last_native_value_at = 0
            self._attr_native_value = 0

        # TODO: Update to the time of comwatt and not the current time
        if time_series_data["timestamps"] and self._last_native_value_at != time_series_data["timestamps"][-1]:
            self._last_native_value_at = time_series_data["timestamps"][-1]
            self._attr_native_value += time_series_data["values"][-1]

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
        client = ComwattClient()
        client.session.cookies.update(self.hass.data[DOMAIN]["cookies"])

        try:
            time_series_data = client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)
        except Exception:
            client.authenticate(self._username, self._password)
            self.hass.data[DOMAIN]["cookies"] = client.session.cookies.get_dict()
            time_series_data = client.get_device_ts_time_ago(self._device["id"], "FLOW", "NONE", "NONE", "HOUR", 1)

        # TODO: Update to the time of comwatt and not the current time
        if time_series_data["values"]:
            self._attr_native_value = time_series_data["values"][-1]
