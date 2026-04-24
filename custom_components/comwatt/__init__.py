"""The Comwatt integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from comwatt_client import ComwattClient

import asyncio

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Comwatt from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    client = ComwattClient()
    await asyncio.to_thread(lambda: client.authenticate(entry.data["username"], entry.data["password"]))

    hass.data[DOMAIN][entry.entry_id] = {"cookies": client.session.cookies.get_dict()}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
