"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from comwatt_client import ComwattAuthError
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter
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


async def test_new_energy_buckets_are_pushed_as_external_statistics(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Each new hourly bucket is recorded at its real timestamp via
    `async_add_external_statistics` (closes #5 and #42)."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]

    def ts_returner(device_id: str, kind: str, *rest: object) -> dict:
        if kind == "QUANTITY":
            return {"timestamps": [3600, 7200], "values": [10.0, 15.0]}
        return {"values": [42.0], "timestamps": [1]}

    mock_comwatt_client.get_device_ts_time_ago.side_effect = ts_returner
    mock_comwatt_client.get_site_time_series.return_value = {
        "autoproductionRates": [],
    }

    entry = _make_entry(hass)
    with patch(
        "custom_components.comwatt.coordinator.async_add_external_statistics"
    ) as mock_push:
        # Recorder isn't loaded in test env — pretend it is, so the push runs.
        hass.config.components.add("recorder")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert mock_push.call_count == 1
    metadata, stats = mock_push.call_args.args[1], list(mock_push.call_args.args[2])
    assert metadata["statistic_id"] == "comwatt:dev_1_total_energy"
    assert metadata["source"] == DOMAIN
    assert metadata["has_sum"] is True
    assert metadata["mean_type"] == StatisticMeanType.NONE
    assert metadata["unit_class"] == EnergyConverter.UNIT_CLASS
    assert metadata["unit_of_measurement"] == "Wh"
    # Two new buckets -> two stat entries. `state` is the per-hour Wh delta,
    # `sum` is the running cumulative total (closes #5/#42 hour alignment).
    assert len(stats) == 2
    assert stats[0]["state"] == 10.0
    assert stats[0]["sum"] == 10.0
    assert stats[1]["state"] == 15.0
    assert stats[1]["sum"] == 25.0
    # Buckets start at the top of the hour in UTC.
    assert stats[0]["start"].minute == 0 and stats[0]["start"].second == 0


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
