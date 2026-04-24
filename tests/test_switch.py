"""Tests for Comwatt switches."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}

SITE = {"id": "site-1", "name": "Home", "siteKind": "RESIDENTIAL"}


def _switchable_device(*, enabled: bool) -> dict:
    """Build a device that exposes a POWER_SWITCH capacity."""
    return {
        "id": "dev-1",
        "name": "Relay",
        "deviceKind": {"code": "RELAY"},
        "features": [
            {
                "capacities": [
                    {
                        "capacity": {
                            "id": "cap-1",
                            "nature": "POWER_SWITCH",
                            "enable": enabled,
                        }
                    }
                ]
            }
        ],
    }


def _non_switchable_device() -> dict:
    """Build a device that has features/capacities but no switchable nature."""
    return {
        "id": "dev-2",
        "name": "Meter",
        "deviceKind": {"code": "METER"},
        "features": [
            {
                "capacities": [
                    {"capacity": {"id": "cap-2", "nature": "POWER_CONSUMED"}}
                ]
            }
        ],
    }


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


async def test_no_devices_creates_no_switches(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    switch_states = [s for s in hass.states.async_all() if s.domain == "switch"]
    assert switch_states == []


async def test_device_without_switch_capacity_creates_no_switch(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [_non_switchable_device()]
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

    switch_states = [s for s in hass.states.async_all() if s.domain == "switch"]
    assert switch_states == []


async def test_switchable_device_creates_switch_with_initial_state(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    device = _switchable_device(enabled=True)
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [device]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }
    mock_comwatt_client.get_device.return_value = device

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = hass.data["entity_registry"]
    switch_entries = [
        e for e in registry.entities.values() if e.domain == "switch"
    ]
    assert len(switch_entries) == 1
    assert switch_entries[0].unique_id == "dev-1_switch"

    state = hass.states.get(switch_entries[0].entity_id)
    assert state is not None
    assert state.state == "on"


async def test_switch_turn_on_calls_client(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    device = _switchable_device(enabled=False)
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [device]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }
    mock_comwatt_client.get_device.return_value = device

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = next(
        e.entity_id
        for e in hass.data["entity_registry"].entities.values()
        if e.domain == "switch"
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id},
        blocking=True,
    )
    mock_comwatt_client.switch_capacity.assert_called_with("cap-1", True)


async def test_switch_turn_off_calls_client(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    device = _switchable_device(enabled=True)
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [device]
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }
    mock_comwatt_client.get_device.return_value = device

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = next(
        e.entity_id
        for e in hass.data["entity_registry"].entities.values()
        if e.domain == "switch"
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id},
        blocking=True,
    )
    mock_comwatt_client.switch_capacity.assert_called_with("cap-1", False)
