"""Tests for the Comwatt integration lifecycle (`__init__.py`)."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"])
    entry.add_to_hass(hass)
    return entry


async def test_setup_entry_authenticates_and_loads(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A valid entry is set up: authenticate is called, cookies are cached, state is LOADED."""
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    mock_comwatt_client.authenticate.assert_called_with(
        ENTRY_DATA["username"], ENTRY_DATA["password"]
    )
    assert hass.data[DOMAIN][entry.entry_id]["cookies"] == {"cwt_session": "fake"}


async def test_unload_entry_cleans_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Unloading a loaded entry returns True and removes per-entry data."""
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]
