from homeassistant.core import HomeAssistant
import logging
import requests
import asyncio

from .const import DOMAIN, SCAN_INTERVAL, ATTRIBUTION
from comwatt_client import ComwattClient
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
    UpdateFailed
)

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    """Set up the Comwatt component."""
    hass.data.setdefault(DOMAIN, {})
    # Nothing to set up for now
    return True

async def async_setup_entry(hass, entry):
    """Set up Comwatt from a config entry."""
    # Create the Comwatt client
    client = ComwattClient()

    # Retrieve the necessary configuration data from the entry
    username = entry.data['username']
    password = entry.data['password']

    await asyncio.to_thread(lambda: client.authenticate(username, password))

    # Create the data coordinator for updating the data
    coordinator = ComwattDataUpdateCoordinator(hass, entry, client)

    # Initialize the data coordinator
    await coordinator.async_refresh()

    # Store the coordinator in the hass data for later access
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward the setup to the platform entry setup
    await hass.config_entries.async_forward_entry_setup(entry, "sensor")

    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    # Retrieve the coordinator for the entry
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)

    # Unload the platform entry
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")

    return True


class ComwattDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to hold Comwatt data retrieval."""

    def __init__(self, hass, entry, client):
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name="Comwatt data update",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.hass = hass

    async def _async_update_data(self):
        """Fetch data from the Comwatt API."""
        try:
            # Retrieve the data from the Comwatt client
            sites = await asyncio.to_thread(lambda: self.client.get_sites())
            data = []
            for site in sites:
                devices = await asyncio.to_thread(lambda: self.client.get_devices(site['id']))
                data.extend(devices)
        except Exception as error:
            raise UpdateFailed(error) from error

        return data

class ComwattEntity(CoordinatorEntity):
    """Implements a common class representing the Comwatt entity."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator, description):
        """Initialize the Comwatt entity."""
        super().__init__(coordinator)
        self.entity_description = description
        if coordinator.config_entry:
            site_id = coordinator.config_entry.data['site']['id']
            name = coordinator.config_entry.data['name']
            device_id = coordinator.config_entry.data['id']
            self._attr_unique_id = f"comwatt-{site_id}-{device_id}-"

            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, site_id)},
                name=name,
            )
