"""The Comwatt integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_list_statistic_ids
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util.unit_conversion import PowerConverter

from .const import DOMAIN
from .coordinator import ComwattCoordinator
from .stream import ComwattStreamManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

SITE_POWER_KEYS: tuple[str, ...] = (
    "production",
    "consumption",
    "injection",
    "withdrawal",
    "charge",
    "discharge",
)
"""Metric keys of the six site power sensors.

Mirrors the `_power(...)` entity descriptions in `sensor.py` (same unique_id
scheme, `site_{site_id}_{key}`); duplicated here so the statistics migration
can resolve their entity_ids without importing the sensor platform.
"""

type ComwattConfigEntry = ConfigEntry[ComwattCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ComwattConfigEntry) -> bool:
    """Set up Comwatt from a config entry."""
    coordinator = ComwattCoordinator(hass, entry)
    await coordinator.async_load_energy_state()
    # `async_config_entry_first_refresh` raises `ConfigEntryAuthFailed` or
    # `ConfigEntryNotReady` for us based on the exception the coordinator
    # raised, so no explicit re-raise is needed here.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    async def _async_save_energy_state_on_final_write(_event: Event) -> None:
        """Save energy state when HA performs its final write to disk.

        EVENT_HOMEASSISTANT_FINAL_WRITE fires after EVENT_HOMEASSISTANT_STOP
        and is HA's designated hook for integrations to persist critical state
        before shutdown. This captures accumulated energy from the WebSocket
        stream since the last poll-cycle save (up to 2 min between regular
        polls), preventing loss of energy data on clean shutdown or restart.
        """
        await coordinator.async_save_energy_state()

    entry.async_on_unload(
        hass.bus.async_listen(EVENT_HOMEASSISTANT_FINAL_WRITE, _async_save_energy_state_on_final_write)
    )

    await _async_migrate_site_power_statistics(hass, coordinator.sites)
    _async_prune_stale(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.stream_manager = ComwattStreamManager(
        hass, coordinator, entry.data["username"], entry.data["password"]
    )
    await coordinator.stream_manager.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ComwattConfigEntry) -> bool:
    """Unload a config entry.

    Stops the WebSocket stream to freeze the energy accumulator, persists the
    current state to storage (so an integration reload doesn't lose accumulated
    energy), then unloads the platforms.
    """
    coordinator = entry.runtime_data
    if coordinator.stream_manager is not None:
        await coordinator.stream_manager.async_stop()
        coordinator.stream_manager = None
    await coordinator.async_save_energy_state()
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
    for device in coordinator.sensor_devices:
        current_unique_ids.add(f"{device['id']}_power")
        current_unique_ids.add(f"{device['id']}_total_energy")
        current_device_identifiers.add((DOMAIN, device["name"]))
    for device in coordinator.switch_devices:
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


async def _async_migrate_site_power_statistics(
    hass: HomeAssistant, sites: list[dict[str, Any]]
) -> None:
    """Clear stale long-term statistics the site power sensors recorded in Wh.

    The six site power sensors used to be labelled `Wh` while carrying
    instantaneous watt values; since the unit was corrected to `W`, Home
    Assistant raises a `units_changed` repair per sensor and stops compiling
    long-term statistics until the old Wh statistics are deleted. No conversion
    is possible (watt-hours and watts are different physical dimensions, and
    the stored values were wrong anyway), so — like duke_energy and
    ista_ecotrend in HA core — we delete them. Only statistics whose recorded
    unit is not a power unit at all are deleted: a user who overrode the display
    unit (kW, MW…) has valid statistics recorded in that unit, and comparing
    against `W` alone would delete them again on every restart. Entity_ids are
    resolved through the entity registry (robust to user renames; for
    state_class sensors the statistic_id is the entity_id). It runs before
    stale-entity pruning so the registry entries of a prior install are still
    resolvable. Best effort: any failure is logged and never blocks the setup,
    and the migration is naturally idempotent — once cleared, subsequent boots
    find no mismatched metadata.
    """
    if "recorder" not in hass.config.components:
        return
    try:
        ent_reg = er.async_get(hass)
        statistic_ids: set[str] = set()
        for site in sites:
            for key in SITE_POWER_KEYS:
                entity_id = ent_reg.async_get_entity_id(
                    "sensor", DOMAIN, f"site_{site['id']}_{key}"
                )
                if entity_id is not None:
                    statistic_ids.add(entity_id)
        if not statistic_ids:
            return
        stale_statistic_ids = [
            meta["statistic_id"]
            for meta in await async_list_statistic_ids(hass, statistic_ids)
            if meta.get("unit_of_measurement") not in PowerConverter.VALID_UNITS
        ]
        if not stale_statistic_ids:
            return
        get_instance(hass).async_clear_statistics(stale_statistic_ids)
        _LOGGER.info(
            "Deleted stale Wh long-term statistics of the site power sensors (unit "
            "corrected from Wh to W): %s. Raw history states are kept; long-term "
            "statistics will restart from scratch",
            stale_statistic_ids,
        )
    except Exception:
        _LOGGER.warning(
            "Could not migrate the Wh long-term statistics of the site power sensors; "
            "delete them via Developer Tools > Statistics if a units_changed repair "
            "appears",
            exc_info=True,
        )
