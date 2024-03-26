from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo

import asyncio
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
                            if 'features' in child and any('capacities' in feature and any(capacity['capacity'].get('nature') == 'POWER_SWITCH' for capacity in feature['capacities']) for feature in child['features']):
                                new_devices.append(ComwattSwitch(entry, child))
                else:
                    if 'features' in device and any('capacities' in feature and any(capacity['capacity'].get('nature') == 'POWER_SWITCH' for capacity in feature['capacities']) for feature in device['features']):
                        new_devices.append(ComwattSwitch(entry, device))

    # TODO: Remove existing devices?
    # TODO: Remove old existing devices?
    if new_devices:
        async_add_entities(new_devices)


class ComwattSwitch(SwitchEntity):
    """Representation of a Switch."""
    _attr_has_entity_name = True

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

    def __init__(self, entry, device):
        self._device = device
        self._username = entry.data["username"]
        self._password = entry.data["password"]
        self._ref = self._device['id']
        self._attr_unique_id = f"{self._device['id']}_switch"
        self._attr_name = f"{self._device['name']} Switch"
        for feature in device['features']:
            for capacity in feature['capacities']:
                if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                    self._is_on = capacity['capacity']['enable']

    @property
    def is_on(self):
        return self._is_on

    def turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        try:
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        capacity_id = capacity['capacity']['id']
            comwatt_client.switch_capacity(capacity_id, True)

        except Exception:
            comwatt_client.authenticate(self._username, self._password)
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        capacity_id = capacity['capacity']['id']
            comwatt_client.switch_capacity(capacity_id, True)

        self.schedule_update_ha_state()

    def turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        try:
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        capacity_id = capacity['capacity']['id']
            comwatt_client.switch_capacity(capacity_id, False)

        except Exception:
            comwatt_client.authenticate(self._username, self._password)
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        capacity_id = capacity['capacity']['id']
            comwatt_client.switch_capacity(capacity_id, False)

        self.schedule_update_ha_state()

    def update(self) -> None:
        """Fetch new state data for the sensor."""
        try:
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        self._is_on = capacity['capacity']['enable']

        except Exception:
            comwatt_client.authenticate(self._username, self._password)
            device = comwatt_client.get_device(self._ref)
            for feature in device['features']:
                for capacity in feature['capacities']:
                    if capacity.get('capacity', {}).get('nature') == "POWER_SWITCH":
                        self._is_on = capacity['capacity']['enable']
