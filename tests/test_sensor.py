"""Tests for Comwatt sensors."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import async_update_entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}

SITE = {"id": "site-1", "name": "Home", "siteKind": "RESIDENTIAL"}

SIMPLE_DEVICE = {
    "id": "dev-1",
    "name": "Panel",
    "deviceKind": {"code": "PANEL"},
}

PARENT_WITH_CHILDREN = {
    "id": "parent-1",
    "name": "Parent",
    "deviceKind": {"code": "PARENT"},
    "partChilds": [
        {"id": "child-1", "name": "Child 1", "deviceKind": {"code": "C"}},
        {"id": "child-2", "name": "Child 2", "deviceKind": {"code": "C"}},
    ],
}


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"])
    entry.add_to_hass(hass)
    return entry


async def test_no_sites_creates_no_sensors(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = []
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor_states = [s for s in hass.states.async_all() if s.domain == "sensor"]
    assert sensor_states == []


async def test_simple_device_creates_three_sensors(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A site with one leaf device yields: auto-production-rate (site), power, energy."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [SIMPLE_DEVICE]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = hass.data["entity_registry"]
    unique_ids = {e.unique_id for e in registry.entities.values() if e.domain == "sensor"}
    assert unique_ids == {
        "site_site-1_auto_production_rate",
        "dev-1_power",
        "dev-1_total_energy",
    }


async def test_parent_with_children_creates_sensors_per_child(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """When a device exposes partChilds, each child gets power + energy sensors
    (but the parent itself does not)."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [PARENT_WITH_CHILDREN]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = hass.data["entity_registry"]
    unique_ids = {e.unique_id for e in registry.entities.values() if e.domain == "sensor"}
    assert unique_ids == {
        "site_site-1_auto_production_rate",
        "child-1_power",
        "child-1_total_energy",
        "child-2_power",
        "child-2_total_energy",
    }


async def test_power_sensor_reads_latest_value(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The power sensor reports the last element of `values`."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [SIMPLE_DEVICE]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [100, 200, 350],
        "timestamps": [1, 2, 3],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_update_entity(hass, "sensor.panel_power")

    state = hass.states.get("sensor.panel_power")
    assert state is not None
    assert state.state == "350"
    assert state.attributes["unit_of_measurement"] == UnitOfPower.WATT
    assert state.attributes["device_class"] == SensorDeviceClass.POWER
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT


async def test_energy_sensor_accumulates_new_buckets(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Each time a new timestamp appears, the delta is added to the running total.

    Documents current (client-side accumulator) behavior. Finding H4 proposes
    replacing this with a cumulative counter from the API; when that lands, this
    test should be rewritten.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [SIMPLE_DEVICE]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [42],
        "timestamps": [1],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_update_entity(hass, "sensor.panel_total_energy")

    state = hass.states.get("sensor.panel_total_energy")
    assert state is not None
    assert state.state == "42"
    assert state.attributes["unit_of_measurement"] == UnitOfEnergy.WATT_HOUR
    assert state.attributes["device_class"] == SensorDeviceClass.ENERGY
    assert state.attributes["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_auto_production_rate_reads_latest_value(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The auto-production-rate sensor reports `autoproductionRates[-1] * 100`."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [0.42],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_update_entity(hass, "sensor.home_auto_production_rate")

    state = hass.states.get("sensor.home_auto_production_rate")
    assert state is not None
    assert state.state == "42.0"
    assert state.attributes["unit_of_measurement"] == PERCENTAGE
    # Only the setup-time authenticate call; update() should not re-auth on a
    # successful fetch.
    assert mock_comwatt_client.authenticate.call_count == 1
