"""Tests for the Comwatt integration lifecycle (`__init__.py`)."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN
from custom_components.comwatt.coordinator import ComwattCoordinator

_STORE_KEY = "comwatt.energy_state"
_STORE_VERSION = 1

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}

SITE = {"id": "site-1", "name": "Home", "siteKind": "RESIDENTIAL"}
DEVICE = {"id": "dev-1", "name": "Panel", "deviceKind": {"code": "PANEL"}}
DEVICE_23593 = {"id": 23593, "name": "Solar Panel", "deviceKind": {"code": "PANEL"}}


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_entry_authenticates_and_loads(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A valid entry reaches LOADED and exposes a coordinator on runtime_data."""
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    mock_comwatt_client.authenticate.assert_called_with(
        ENTRY_DATA["username"], ENTRY_DATA["password"]
    )
    assert isinstance(entry.runtime_data, ComwattCoordinator)


async def test_unload_entry_cleans_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Unloading a loaded entry returns True and leaves state as NOT_LOADED."""
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_prunes_stale_entities_and_devices(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Entities/devices left over from a prior run but no longer in the API
    response are removed from the HA registries on setup."""
    entry = _make_entry(hass)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Pre-seed a stale entity and device as if a device had previously been
    # registered and then deleted on the Comwatt side.
    stale_device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "Old Panel")},
        name="Old Panel",
    )
    stale_entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "old-id_power",
        suggested_object_id="old_panel_power",
        config_entry=entry,
        device_id=stale_device.id,
    )
    stale_entity_id = stale_entity.entity_id

    # Current API only knows about `DEVICE`, not `Old Panel`.
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Stale entity is gone.
    assert ent_reg.async_get(stale_entity_id) is None
    # Stale device has been detached from this entry (and auto-removed since
    # it had no other config entries).
    remaining_device_names = {
        dev.name
        for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    }
    assert "Old Panel" not in remaining_device_names
    # Current device is still there.
    assert "Panel" in remaining_device_names


async def test_energy_state_saved_on_final_write(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Energy state is persisted to store when EVENT_HOMEASSISTANT_FINAL_WRITE fires,
    capturing stream-accumulated energy beyond the last poll-cycle save.
    """
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "23593": {
                    "live_total_wh": 1000.0,
                    "total_wh": 0.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                }
            },
        }
    )

    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [200.0],
        "timestamps": [1],
    }
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    device_id = 23593

    state = coordinator._energy_state[device_id]
    state.live_total_wh = 1000.0 + 10.5

    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()

    raw = await store.async_load()

    assert raw is not None
    assert "23593" in raw["data"]
    assert raw["data"]["23593"]["live_total_wh"] == 1010.5


async def test_energy_state_saved_on_unload(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Energy state is persisted to store when the config entry is unloaded,
    ensuring a reload doesn't lose stream-accumulated energy.
    """
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "23593": {
                    "live_total_wh": 2000.0,
                    "total_wh": 0.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                }
            },
        }
    )

    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [200.0],
        "timestamps": [1],
    }
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    device_id = 23593

    state = coordinator._energy_state[device_id]
    state.live_total_wh = 2000.0 + 7.25

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    raw = await store.async_load()

    assert raw is not None
    assert "23593" in raw["data"]
    assert raw["data"]["23593"]["live_total_wh"] == 2007.25
