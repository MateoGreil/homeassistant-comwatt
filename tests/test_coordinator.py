"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

from comwatt_client import ComwattAuthError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN
from custom_components.comwatt.coordinator import _parse_bucket_ts

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
    mock_comwatt_client.get_site_time_series.return_value = {
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


async def test_energy_fetch_is_skipped_within_interval(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The QUANTITY/HOUR energy endpoint is only called once per ~hour.

    Closes #3: the API bucket only changes hourly, so a second coordinator
    refresh within a few minutes must not re-call that endpoint.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]

    def ts_returner(device_id: str, kind: str, *rest: object) -> dict:
        if kind == "QUANTITY":
            return {"timestamps": [1, 2], "values": [10.0, 15.0]}
        return {"values": [42.0], "timestamps": [1]}

    mock_comwatt_client.get_device_ts_time_ago.side_effect = ts_returner
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def count_quantity_calls() -> int:
        return sum(
            1
            for call in mock_comwatt_client.get_device_ts_time_ago.call_args_list
            if len(call.args) >= 2 and call.args[1] == "QUANTITY"
        )

    calls_after_setup = count_quantity_calls()
    assert calls_after_setup == 1

    # Second refresh shortly after (same test — same second).
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert count_quantity_calls() == 1, "energy endpoint should be skipped"

    # Simulate an hour passing by rewinding last_fetched_at.
    for state in entry.runtime_data._energy_state.values():
        state.last_fetched_at = time.monotonic() - 60 * 60
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert count_quantity_calls() == 2, "energy endpoint should be called again after the interval"


async def test_capacity_map_built_from_connected_objects(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The coordinator folds every connected object's capacities into a
    capacityId -> (deviceId, nature, production) map, skipping capacities
    with a null deviceId.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }
    connected_object_a = {
        "capacities": [
            {
                "capacityId": "AZUREIOT-co.2.instances.3.sensor.3.data",
                "deviceId": 23600,
                "nature": "CLAMP",
                "production": False,
            },
            {
                "capacityId": "AZUREIOT-co.2.instances.0.sensor.0.withdrawal.data",
                "deviceId": 23599,
                "nature": "CLAMP",
                "production": False,
            },
            {
                "capacityId": "AZUREIOT-co.2.instances.0.sensor.0.injection.data",
                "deviceId": 23598,
                "nature": "CLAMP",
                "production": False,
            },
            {
                "capacityId": "AZUREIOT-co.2.instances.9.sensor.9.data",
                "deviceId": None,
                "nature": "CLAMP",
                "production": False,
            },
        ]
    }
    connected_object_b = {
        "capacities": [
            {
                "capacityId": "AZUREIOT-co.1.instances.3.sensor.3.battery_charge.data",
                "deviceId": 147223,
                "nature": "CLAMP",
                "production": False,
            },
            {
                "capacityId": "AZUREIOT-co.10.instances.0.switch.0.data",
                "deviceId": 129443,
                "nature": "POWER_SWITCH",
                "production": False,
            },
        ]
    }
    mock_comwatt_client.get_connected_objects.return_value = [
        connected_object_a,
        connected_object_b,
    ]

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    capacity_map = entry.runtime_data.capacity_map
    assert capacity_map["AZUREIOT-co.2.instances.3.sensor.3.data"] == ("23600", "CLAMP", False)
    assert capacity_map["AZUREIOT-co.2.instances.0.sensor.0.withdrawal.data"] == ("23599", "CLAMP", False)
    assert capacity_map["AZUREIOT-co.1.instances.3.sensor.3.battery_charge.data"] == ("147223", "CLAMP", False)
    assert capacity_map["AZUREIOT-co.10.instances.0.switch.0.data"] == ("129443", "POWER_SWITCH", False)
    assert "AZUREIOT-co.2.instances.9.sensor.9.data" not in capacity_map
    assert len(capacity_map) == 5


async def test_fetch_device_metrics_returns_live_total_when_stream_active(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Once the stream has taken over, _fetch_device_metrics returns the live
    total and skips the QUANTITY/HOUR fetch so the poll and stream don't
    double-count the same energy."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [42.0],
        "timestamps": [1],
    }

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data

    def count_quantity_calls() -> int:
        return sum(
            1
            for call in mock_comwatt_client.get_device_ts_time_ago.call_args_list
            if len(call.args) >= 2 and call.args[1] == "QUANTITY"
        )

    coord._energy_state[DEVICE["id"]].live_total_wh = 1234.0
    calls_before = count_quantity_calls()
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert result == {"power": 42.0, "energy": 1234.0}
    assert count_quantity_calls() == calls_before

    coord._energy_state[DEVICE["id"]].live_total_wh = None
    coord._energy_state[DEVICE["id"]].last_fetched_at = time.monotonic() - 60 * 60
    await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert count_quantity_calls() == calls_before + 1


# ---------------------------------------------------------------------------
# _parse_bucket_ts — boundary parser for the Comwatt time-series endpoint.
# Documents every shape we have seen the API actually return, so future drift
# is caught here instead of in production.
# ---------------------------------------------------------------------------

def test_parse_bucket_ts_iso_with_milliseconds_and_offset() -> None:
    # Real shape returned by /aggregations/time-series.
    dt = _parse_bucket_ts("2026-04-29T10:00:00.000+0000")
    assert dt == datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_parse_bucket_ts_iso_with_z_suffix() -> None:
    dt = _parse_bucket_ts("2026-04-29T10:00:00Z")
    assert dt == datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_parse_bucket_ts_iso_with_colon_offset() -> None:
    dt = _parse_bucket_ts("2026-04-29T12:00:00+02:00")
    assert dt == datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_parse_bucket_ts_naive_iso_assumed_utc() -> None:
    dt = _parse_bucket_ts("2026-04-29T10:00:00")
    assert dt == datetime(2026, 4, 29, 10, 0, tzinfo=UTC)


def test_parse_bucket_ts_epoch_seconds_int() -> None:
    dt = _parse_bucket_ts(1719504000)  # 2024-06-27 16:00:00 UTC
    assert dt == datetime(2024, 6, 27, 16, 0, tzinfo=UTC)


def test_parse_bucket_ts_epoch_seconds_float() -> None:
    dt = _parse_bucket_ts(1719504000.0)
    assert dt == datetime(2024, 6, 27, 16, 0, tzinfo=UTC)


def test_parse_bucket_ts_epoch_milliseconds() -> None:
    # Same instant as above, expressed in ms.
    dt = _parse_bucket_ts(1719504000000)
    assert dt == datetime(2024, 6, 27, 16, 0, tzinfo=UTC)


def test_parse_bucket_ts_numeric_string() -> None:
    dt = _parse_bucket_ts("1719504000")
    assert dt == datetime(2024, 6, 27, 16, 0, tzinfo=UTC)


def test_parse_bucket_ts_garbage_returns_none() -> None:
    assert _parse_bucket_ts("not-a-date") is None
    assert _parse_bucket_ts("") is None
    assert _parse_bucket_ts("   ") is None


def test_parse_bucket_ts_unsupported_types_return_none() -> None:
    assert _parse_bucket_ts(None) is None
    assert _parse_bucket_ts(True) is None  # bool is a subclass of int — exclude
    assert _parse_bucket_ts(False) is None
    assert _parse_bucket_ts([1719504000]) is None
    assert _parse_bucket_ts({"ts": 1719504000}) is None
