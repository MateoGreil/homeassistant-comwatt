"""The Comwatt integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import ComwattCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

type ComwattConfigEntry = ConfigEntry[ComwattCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ComwattConfigEntry) -> bool:
    """Set up Comwatt from a config entry."""
    coordinator = ComwattCoordinator(hass, entry)
    # `async_config_entry_first_refresh` raises `ConfigEntryAuthFailed` or
    # `ConfigEntryNotReady` for us based on the exception the coordinator
    # raised, so no explicit re-raise is needed here.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ComwattConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
