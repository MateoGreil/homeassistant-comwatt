"""Tests for the Comwatt integration lifecycle (`__init__.py`)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
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

SITE_POWER_KEYS = (
    "production",
    "consumption",
    "injection",
    "withdrawal",
    "charge",
    "discharge",
)


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


def _expose_single_site(mock_comwatt_client: MagicMock) -> None:
    """Make the mocked client answer the coordinator's first refresh with
    a single site and no measurements, which is enough for setup to succeed.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }


def _register_site_power_entities(
    hass: HomeAssistant, entry: MockConfigEntry
) -> list[str]:
    """Pre-create entity-registry entries for the six site power sensors,
    simulating an installation that existed before the Wh to W unit fix.
    """
    ent_reg = er.async_get(hass)
    entity_ids = []
    for key in SITE_POWER_KEYS:
        entity = ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"site_{SITE['id']}_{key}",
            suggested_object_id=f"{SITE['name'].lower()}_{key}",
            config_entry=entry,
        )
        entity_ids.append(entity.entity_id)
    return entity_ids


def _mock_statistics(
    monkeypatch: pytest.MonkeyPatch,
    metadata: list[dict] | None = None,
    error: Exception | None = None,
) -> MagicMock:
    """Replace the recorder helpers on the comwatt module with test doubles.

    The suite runs without a real recorder instance or sqlite database, so the
    integration's imported helpers are swapped on `custom_components.comwatt`
    itself (`raising=False` lets the tests run before the implementation adds
    the imports). Returns the mocked recorder instance whose
    `async_clear_statistics` method tests assert on.
    """
    list_statistic_ids = AsyncMock(
        name="async_list_statistic_ids",
        return_value=metadata,
        side_effect=error,
    )
    recorder_instance = MagicMock(name="recorder_instance")
    monkeypatch.setattr(
        "custom_components.comwatt.async_list_statistic_ids",
        list_statistic_ids,
        raising=False,
    )
    monkeypatch.setattr(
        "custom_components.comwatt.get_instance",
        MagicMock(name="get_instance", return_value=recorder_instance),
        raising=False,
    )
    return recorder_instance


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


async def test_migrates_stale_wh_statistics(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setup clears exactly the long-term statistics still recorded in Wh for
    the site power sensors, and logs the migration.
    """
    hass.config.components.add("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)
    entity_ids = _register_site_power_entities(hass, entry)

    production, consumption, injection, *_ = entity_ids
    recorder_instance = _mock_statistics(
        monkeypatch,
        metadata=[
            {"statistic_id": production, "unit_of_measurement": "Wh"},
            {"statistic_id": consumption, "unit_of_measurement": "Wh"},
            {"statistic_id": injection, "unit_of_measurement": "W"},
        ],
    )

    with caplog.at_level(logging.INFO, logger="custom_components.comwatt"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from custom_components import comwatt

    assert comwatt.async_list_statistic_ids.await_count == 1
    requested_ids = comwatt.async_list_statistic_ids.await_args.args[1]
    assert set(requested_ids) == set(entity_ids)

    assert recorder_instance.async_clear_statistics.call_count == 1
    cleared_ids = recorder_instance.async_clear_statistics.call_args.args[0]
    assert set(cleared_ids) == {production, consumption}

    assert production in caplog.text
    assert consumption in caplog.text


async def test_migration_skips_when_all_watts(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every site power statistic is already in W, nothing is cleared
    (a second boot after a successful migration stays idle).
    """
    hass.config.components.add("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)
    entity_ids = _register_site_power_entities(hass, entry)

    recorder_instance = _mock_statistics(
        monkeypatch,
        metadata=[
            {"statistic_id": entity_id, "unit_of_measurement": "W"}
            for entity_id in entity_ids
        ],
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert recorder_instance.async_clear_statistics.call_count == 0


async def test_migration_keeps_user_overridden_power_units(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Statistics recorded in another power unit are valid and must be kept.

    A user may override the display unit of a power sensor (kW, MW…), and the
    statistics metadata then holds that unit because the first state seen sets
    it. Comparing against `W` alone would treat those statistics as stale and
    delete them on every single restart, destroying valid history; only units
    that are not power units at all (such as the old `Wh`) may be cleared.
    """
    hass.config.components.add("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)
    entity_ids = _register_site_power_entities(hass, entry)

    units = ["kW", "MW", "mW", "W", "GW", "TW"]
    recorder_instance = _mock_statistics(
        monkeypatch,
        metadata=[
            {"statistic_id": entity_id, "unit_of_measurement": unit}
            for entity_id, unit in zip(entity_ids, units, strict=True)
        ],
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert recorder_instance.async_clear_statistics.call_count == 0


async def test_migration_skips_without_recorder(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the recorder component loaded, statistics are never queried
    (nothing can have compiled long-term statistics anyway).
    """
    if "recorder" in hass.config.components:
        hass.config.components.remove("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)
    _register_site_power_entities(hass, entry)

    recorder_instance = _mock_statistics(
        monkeypatch,
        metadata=[
            {"statistic_id": "sensor.home_production", "unit_of_measurement": "Wh"}
        ],
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components import comwatt

    assert comwatt.async_list_statistic_ids.await_count == 0
    assert recorder_instance.async_clear_statistics.call_count == 0


async def test_migration_failure_does_not_break_setup(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A recorder failure during migration only logs a warning; the config
    entry still reaches LOADED.
    """
    hass.config.components.add("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)
    _register_site_power_entities(hass, entry)

    _mock_statistics(
        monkeypatch, error=RuntimeError("statistics database unavailable")
    )

    with caplog.at_level(logging.WARNING, logger="custom_components.comwatt"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert "statistics database unavailable" in caplog.text


async def test_migration_noop_fresh_install(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a fresh install, no site entity exists in the registry before the
    sensor platform is forwarded, so statistics are never queried.
    """
    hass.config.components.add("recorder")
    _expose_single_site(mock_comwatt_client)
    entry = _make_entry(hass)

    recorder_instance = _mock_statistics(
        monkeypatch,
        metadata=[
            {"statistic_id": "sensor.home_production", "unit_of_measurement": "Wh"}
        ],
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components import comwatt

    assert comwatt.async_list_statistic_ids.await_count == 0
    assert recorder_instance.async_clear_statistics.call_count == 0
