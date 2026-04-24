"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_retry_on_bad_credentials(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A 401 from authenticate() leaves the entry in SETUP_ERROR, not LOADED."""
    mock_comwatt_client.authenticate.side_effect = Exception(
        "Authentication failed: 401"
    )
    entry = _make_entry(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is not ConfigEntryState.LOADED


async def test_setup_retry_on_transient_network_error(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A transient fetch error puts the entry in SETUP_RETRY (not SETUP_ERROR)."""
    mock_comwatt_client.get_sites.side_effect = Exception(
        "Error retrieving sites: 502"
    )
    entry = _make_entry(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_successful_refresh_retries_once_after_transient_failure(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A one-off fetch failure is recovered by re-auth + retry."""
    call_count = {"n": 0}

    def flaky_get_sites() -> list[dict]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("Error retrieving sites: 500")
        return []

    mock_comwatt_client.get_sites.side_effect = flaky_get_sites
    entry = _make_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # Initial attempt + one retry after re-auth.
    assert call_count["n"] == 2
    # Re-auth was invoked between the two attempts.
    assert mock_comwatt_client.authenticate.call_count == 2
