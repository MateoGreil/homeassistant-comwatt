"""The Comwatt integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
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
    _async_prune_stale(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ComwattConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_prune_stale(
    hass: HomeAssistant,
    entry: ComwattConfigEntry,
    coordinator: ComwattCoordinator,
) -> None:
    """Remove entities and devices no longer present in the Comwatt account.

    Runs once per setup (including HA startup and integration reload). A
    device transiently missing from a single poll is never pruned because we
    only consult the snapshot taken by `async_config_entry_first_refresh`,
    which has already succeeded by the time we get here.
    """
    current_unique_ids: set[str] = set()
    current_device_identifiers: set[tuple[str, str]] = set()

    for site in coordinator.sites:
        current_unique_ids.add(f"site_{site['id']}_auto_production_rate")
        current_device_identifiers.add((DOMAIN, site["name"]))
    for _site, device in coordinator.sensor_devices:
        current_unique_ids.add(f"{device['id']}_power")
        current_unique_ids.add(f"{device['id']}_total_energy")
        current_device_identifiers.add((DOMAIN, device["name"]))
    for _site, device in coordinator.switch_devices:
        current_unique_ids.add(f"{device['id']}_switch")
        current_device_identifiers.add((DOMAIN, device["name"]))

    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if entity.unique_id not in current_unique_ids:
            ent_reg.async_remove(entity.entity_id)

    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not any(idf in current_device_identifiers for idf in device.identifiers):
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
