"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

from unittest.mock import MagicMock

from comwatt_client import ComwattAuthError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}
SITE = {"id": "site-1", "name": "Home", "siteKind": "RESIDENTIAL"}
DEVICE = {"id": "dev-1", "name": "Panel", "deviceKind": {"code": "PANEL"}}


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_starts_reauth_on_bad_credentials(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Rejected credentials put the entry in SETUP_ERROR and start a reauth flow."""
    mock_comwatt_client.authenticate.side_effect = ComwattAuthError(
        status_code=401, url="https://energy.comwatt.com/api/v1/authent"
    )
    entry = _make_entry(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


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


async def test_auth_error_mid_fetch_is_not_swallowed(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A ComwattAuthError from a device fetch propagates (no silent None data):
    the entry fails setup and a reauth flow starts, without any
    coordinator-level re-authentication attempt."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_networks_ts_time_ago.return_value = {
        "autoproductionRates": [],
    }
    mock_comwatt_client.get_device_ts_time_ago.side_effect = ComwattAuthError(
        status_code=401, url="https://energy.comwatt.com/api/devices/dev-1"
    )
    entry = _make_entry(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"].get("source") == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )
    assert mock_comwatt_client.authenticate.call_count == 1
