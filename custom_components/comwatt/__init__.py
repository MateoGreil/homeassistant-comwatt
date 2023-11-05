"""The Comwatt integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

import asyncio
from comwatt_client import ComwattClient


# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Comwatt from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    # TODO 1. Create API instance
    client = ComwattClient()
    await asyncio.to_thread(lambda: client.authenticate(entry.data["username"], entry.data["password"]))

    # TODO 2. Validate the API connection (and authentication)

    # TODO 3. Store an API object for your platforms to access
    hass.data[DOMAIN][entry.entry_id] = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
