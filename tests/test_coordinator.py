"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from comwatt_client import ComwattAuthError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers.storage import Store

from custom_components.comwatt.const import DOMAIN
from custom_components.comwatt.coordinator import (
    ComwattCoordinator,
    _EnergyState,
    _STORE_MINOR_VERSION,
    _parse_bucket_ts,
)

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}
SITE = {"id": "site-1", "name": "Home", "siteKind": "RESIDENTIAL"}
DEVICE = {"id": "dev-1", "name": "Panel", "deviceKind": {"code": "PANEL"}}
DEVICE_23593 = {"id": 23593, "name": "Solar Panel", "deviceKind": {"code": "PANEL"}}
DEVICE_23598 = {"id": 23598, "name": "Injection", "deviceKind": {"code": "CLAMP"}}
_STORE_KEY = "comwatt.energy_state"
_STORE_VERSION = 1


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


def _device_ts_side_effect(power: float, quantity: dict[str, Any]) -> Any:
    """Route get_device_ts_time_ago by measure kind: FLOW → power, QUANTITY → buckets."""

    def _route(device_id: str, kind: str, *rest: object) -> dict[str, Any]:
        if kind == "QUANTITY":
            return quantity
        return {"values": [power], "timestamps": [1]}

    return _route


def _count_quantity_calls(client: MagicMock) -> int:
    """Count get_device_ts_time_ago calls made with measure kind QUANTITY."""
    return sum(
        1
        for call in client.get_device_ts_time_ago.call_args_list
        if len(call.args) >= 2 and call.args[1] == "QUANTITY"
    )


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
    assert capacity_map["AZUREIOT-co.2.instances.3.sensor.3.data"] == (23600, "CLAMP", False)
    assert capacity_map["AZUREIOT-co.2.instances.0.sensor.0.withdrawal.data"] == (23599, "CLAMP", False)
    assert capacity_map["AZUREIOT-co.1.instances.3.sensor.3.battery_charge.data"] == (147223, "CLAMP", False)
    assert capacity_map["AZUREIOT-co.10.instances.0.switch.0.data"] == (129443, "POWER_SWITCH", False)
    assert "AZUREIOT-co.2.instances.9.sensor.9.data" not in capacity_map
    assert len(capacity_map) == 5


async def test_fetch_device_metrics_returns_live_total_when_stream_active(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """While the stream owns the live total, _fetch_device_metrics returns that
    total and reopens the QUANTITY/HOUR path as a reconciliation when the
    throttle allows (Slice 5), instead of skipping it outright.

    Within the throttle interval the QUANTITY call is still skipped and the
    live total is returned untouched; once the interval elapses, a new server
    bucket reconciles the live total and the reconciled value is returned.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]

    state.live_total_wh = 1234.0
    calls_before = _count_quantity_calls(mock_comwatt_client)
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert result == {"power": 42.0, "energy": 1234.0}
    assert _count_quantity_calls(mock_comwatt_client) == calls_before

    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert _count_quantity_calls(mock_comwatt_client) == calls_before + 1
    assert state.live_total_wh == 1234.0 + (500.0 - 510.0)
    assert result == {"power": 42.0, "energy": 1234.0}


async def test_reconcile_server_bucket_corrects_live_total(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A server QUANTITY/HOUR bucket for a completed hour reconciles the live
    total: the server's authoritative Wh corrects the live accumulator's drift.

    Bucket-labeling assumption (documented here and in _fetch_device_metrics):
    the server's `bucket_dt` is the START of the hour it represents, matching
    the live accumulator's hour key (the power-sample timestamp truncated to
    the hour). So a server bucket labeled 11:00 and the live accumulator's
    11:00 entry describe the same physical hour. If real data proves the server
    labels by the END of the hour, the fix is to key the server bucket by
    `bucket_dt - 1h` — a one-line change; the architecture is correct either
    way.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 90.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 500.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 90.0}


async def test_reconcile_skips_small_backward_correction(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A server bucket whose backward correction is within the drift tolerance
    (0 < -correction <= _RECONCILE_BACKWARD_DRIFT_TOLERANCE_WH) is SKIPPED to
    keep the `total_increasing` live total monotone and stop HA recorder's
    "state is not strictly increasing" warnings. The live accumulator stays the
    source of truth for that hour and the high-water mark still advances, so the
    bucket is not reconsidered on the next fetch.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [508.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 510.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 100.0}

    state.last_fetched_at = time.monotonic() - 60 * 60
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert state.live_total_wh == 100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 510.0
    assert result == {"power": 42.0, "energy": 100.0}


async def test_reconcile_applies_large_backward_correction(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A server bucket whose backward correction EXCEEDS the drift tolerance
    (-correction > _RECONCILE_BACKWARD_DRIFT_TOLERANCE_WH) is still applied as a
    snap: the live accumulator is corrected toward the authoritative server value,
    preventing permanent over-count now that the live total survives restarts.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [400.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 1000.0 - 110.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 400.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 1000.0 - 110.0}


async def test_reconcile_boundary_at_tolerance_is_skipped(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A backward correction of exactly the tolerance (-correction ==
    _RECONCILE_BACKWARD_DRIFT_TOLERANCE_WH) is SKIPPED: the tolerance is
    inclusive (<=), so exactly 5 Wh of drift is treated as monotone-able noise.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [505.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 510.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 100.0}


async def test_reconcile_forward_correction_unchanged(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A forward reconciliation (correction >= 0) is applied exactly as before —
    regression guard for the forward path under the new backward-skip branch.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [530.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 500.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 130.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 530.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 130.0}


async def test_reconcile_skips_bucket_at_or_below_high_water_mark(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A server bucket whose `bucket_dt` is not newer than `last_bucket_ts` is
    skipped, so an already-reconciled hour is never corrected twice and an
    early same-hour bucket doesn't fight the live accumulator."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    calls_before = _count_quantity_calls(mock_comwatt_client)
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert _count_quantity_calls(mock_comwatt_client) == calls_before + 1
    assert state.live_total_wh == 100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 510.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 100.0}


async def test_legacy_seed_skips_when_no_live_reference(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """When live_total_wh is None and live_by_hour has no reference for a bucket's
    hour, the unit-safe legacy path skips accumulation (live_wh=0 → _server_bucket_to_wh
    returns None). total_wh stays at 0; last_bucket_ts still advances."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": [1, 2], "values": [10.0, 15.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = None
    state.last_bucket_ts = None
    state.total_wh = 0.0
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.total_wh == 0.0
    assert state.live_total_wh is None
    assert state.live_by_hour == {}
    assert state.last_bucket_ts == datetime(1970, 1, 1, 0, 0, 2, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 0.0}


async def test_reconcile_across_multiple_new_buckets(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Multiple new server buckets in one fetch are each reconciled in
    timestamp order against the matching live-by-hour entry."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={
            "timestamps": [
                "2026-07-14T10:00:00.000+0000",
                "2026-07-14T11:00:00.000+0000",
            ],
            "values": [500.0, 190.0],
        },
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {
        datetime(2026, 7, 14, 10, 0, tzinfo=UTC): 510.0,
        datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 200.0,
    }
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 80.0
    assert datetime(2026, 7, 14, 10, 0, tzinfo=UTC) not in state.live_by_hour
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 190.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 80.0}


async def test_reconcile_does_not_double_correct_on_refetch(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """After a bucket is reconciled, `live_by_hour` snaps to the server value
    and `last_bucket_ts` advances, so re-fetching the same bucket (throttle
    rewound) applies no further correction — the high-water mark prevents
    double-correction."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)
    assert state.live_total_wh == 90.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 500.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)

    state.last_fetched_at = time.monotonic() - 60 * 60
    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 90.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 500.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 90.0}


async def test_refresh_reconciles_live_energy_via_periodic_poll(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A periodic refresh reopens the QUANTITY path for an active stream and
    surfaces the reconciled live total on the device's energy sensor, end-to-end
    through _async_update_data → _fetch_all → _fetch_device_metrics."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    await coord.async_refresh()
    await hass.async_block_till_done()

    assert coord.data["devices"][DEVICE["id"]]["energy"] == 90.0
    assert state.live_total_wh == 90.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 500.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)


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


async def test_reconcile_preserves_concurrent_stream_delta(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    def concurrent_quantity_side_effect(device_id: str, kind: str, *rest: object) -> dict[str, Any]:
        if kind == "QUANTITY":
            state.live_total_wh += 30.0
            return {"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]}
        return {"values": [42.0], "timestamps": [1]}

    mock_comwatt_client.get_device_ts_time_ago.side_effect = concurrent_quantity_side_effect

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 120.0
    assert result == {"power": 42.0, "energy": 120.0}


async def test_reconcile_prunes_stale_live_by_hour(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    state.live_by_hour = {
        datetime(2026, 7, 14, 8, 0, tzinfo=UTC): 100.0,
        datetime(2026, 7, 14, 9, 0, tzinfo=UTC): 200.0,
        datetime(2026, 7, 14, 10, 0, tzinfo=UTC): 300.0,
        datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0,
        datetime(2026, 7, 14, 12, 0, tzinfo=UTC): 50.0,
    }
    state.last_fetched_at = time.monotonic() - 60 * 60

    await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert set(state.live_by_hour.keys()) == {
        datetime(2026, 7, 14, 11, 0, tzinfo=UTC),
        datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
    }


async def test_reconcile_converts_kwh_bucket_without_hundred_wh_jump(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A grid device whose QUANTITY/HOUR returns kWh (server val ~0.9 for an
    ~900 Wh hour) is converted to 900 Wh before reconciling, so the live total
    stays ~900 instead of snapping to ~0.9 — no hundred-Wh unit-conversion
    jump. (Slice 1 skipped this bucket; slice 2 adds the kWh→Wh conversion.)"""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [0.9]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 900.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 900.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 900.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 900.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 900.0}


async def test_reconcile_converts_kwh_bucket_to_wh_before_snapping(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A grid device whose QUANTITY/HOUR returns kWh (server val ~0.9 for an
    ~900 Wh hour) is converted to 900 Wh before reconciling, so the live total
    snaps to ~900 instead of ~0.9 — and the correction is a bounded drift fix,
    not a hundred-Wh unit-conversion jump."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [0.9]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 900.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 1000.0 + (900.0 - 900.0)
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 900.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 1000.0}


async def test_reconcile_kwh_conversion_corrects_live_drift(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """When the live ∫W·dt drifted below the authoritative server kWh value, the
    kWh→Wh conversion reconciles the live total toward the server value: server
    1.1 kWh (1100 Wh) vs live 1000 Wh → +100 Wh correction."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [1.1]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 1000.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 1100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 1100.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 1100.0}


async def test_reconcile_converts_withdrawal_kwh_bucket(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The grid-withdrawal (soutirage) device returns kWh too: server 13.23 for
    an ~13200 Wh hour is converted to 13230 Wh and reconciled. This covers the
    handoff's open soutirage case — its unit is kWh, same as its injection
    sibling under the same GRID_METER."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [13.23]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 10000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 13000.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 10000.0 + (13230.0 - 13000.0)
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 13230.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 10230.0}


async def test_reconcile_skips_bucket_when_live_has_no_reference(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A bucket for an hour with no live ∫W·dt (live ≈ 0) has nothing to compare
    against, so it is skipped — but the high-water mark advances so it is not
    reconsidered. This is what stops bogus non-zero night values (solar,
    injection) from snapping a live total that correctly read ~0 for that hour."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 100.0
    assert datetime(2026, 7, 14, 11, 0, tzinfo=UTC) not in state.live_by_hour
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 100.0}


async def test_reconcile_skips_anomalous_ratio_bucket(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A server value whose ratio to the live ∫W·dt is incoherent (neither a Wh
    ~1.0 nor explainable as drift) is skipped. An electric-vehicle device can
    return a one-off ~62.83 bucket while the live accumulator measured ~2 Wh for
    that hour; snapping to 62.83 would be a spurious +60 Wh jump, so the bucket
    is skipped and the high-water mark advances."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [62.83]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 100.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 20.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 100.0
    assert state.live_by_hour[datetime(2026, 7, 14, 11, 0, tzinfo=UTC)] == 20.0
    assert state.last_bucket_ts == datetime(2026, 7, 14, 11, 0, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 100.0}


async def test_capacity_map_skips_capacity_without_nature(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_connected_objects.return_value = [
        {
            "capacities": [
                {
                    "capacityId": "AZUREIOT-co.1.sensor.1.data",
                    "deviceId": 99001,
                    "nature": "CLAMP",
                },
                {
                    "capacityId": "AZUREIOT-co.1.sensor.2.data",
                    "deviceId": 99002,
                },
            ]
        }
    ]

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    capacity_map = entry.runtime_data.capacity_map
    assert "AZUREIOT-co.1.sensor.1.data" in capacity_map
    assert "AZUREIOT-co.1.sensor.2.data" not in capacity_map


async def test_restore_live_total_wh_after_restart(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Persisted live_total_wh is restored before the first poll so the energy
    counter continues from the saved value rather than reseeding from the 24h
    bucket sum.  Without the store the no-live-reference seed path yields 0.0;
    with it the counter begins at 42506.0.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=200.0,
        quantity={
            "timestamps": ["2026-07-25T16:00:00.000+0000"],
            "values": [500.0],
        },
    )

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "23593": {
                    "live_total_wh": 42506.0,
                    "total_wh": 0.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                }
            },
        }
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    energy = coord.data["devices"][23593]["energy"]
    assert energy == 42506.0


async def test_poll_cycle_persists_state(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """After a successful poll cycle the coordinator writes the current energy
    state to the HA store, including live_total_wh and live_by_hour.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=200.0,
        quantity={"timestamps": ["2026-07-25T16:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    coord._energy_state[23593].live_total_wh = 77777.0

    await coord.async_save_energy_state()

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load()

    assert raw is not None
    assert raw["version"] == 1
    assert "23593" in raw["data"]
    assert raw["data"]["23593"]["live_total_wh"] == 77777.0


async def test_negative_reconciliation_keeps_published_energy_monotonic(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [400.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.published_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 510.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 890.0
    assert state.published_total_wh == 1000.0
    assert result == {"power": 42.0, "energy": 1000.0}


async def test_positive_reconciliation_advances_published_energy(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [530.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 1000.0
    state.published_total_wh = 1000.0
    state.last_bucket_ts = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)
    state.live_by_hour = {datetime(2026, 7, 14, 11, 0, tzinfo=UTC): 500.0}
    state.last_fetched_at = time.monotonic() - 60 * 60

    result = await hass.async_add_executor_job(coord._fetch_device_metrics, DEVICE)

    assert state.live_total_wh == 1030.0
    assert state.published_total_wh == 1030.0
    assert result == {"power": 42.0, "energy": 1030.0}


async def test_stream_keeps_high_water_mark_while_internal_energy_catches_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": [], "values": []},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = 90.0
    state.published_total_wh = 100.0
    state.last_power_w = 42.0
    state.last_power_t = time.monotonic() + 60.0
    coord.data["devices"][DEVICE["id"]]["energy"] = 100.0

    coord.integrate_live_energy({DEVICE["id"]: 42.0})

    assert state.live_total_wh == 90.0
    assert state.published_total_wh == 100.0
    assert coord.data["devices"][DEVICE["id"]]["energy"] == 100.0


async def test_restore_missing_published_energy_uses_live_total(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "23593": {
                    "live_total_wh": 42506.0,
                    "total_wh": 0.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                }
            },
        }
    )

    entry = _make_entry(hass)
    coord = ComwattCoordinator(hass, entry)
    await coord.async_load_energy_state()

    state = coord._energy_state[23593]
    assert state.live_total_wh == 42506.0
    assert state.published_total_wh == 42506.0


async def test_restore_published_high_water_survives_reload_until_live_catches_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    first_coord = ComwattCoordinator(hass, _make_entry(hass))
    first_coord._energy_state[23593] = _EnergyState(
        live_total_wh=90.0,
        published_total_wh=100.0,
    )
    await first_coord.async_save_energy_state()

    entry = _make_entry(hass)
    restored_coord = ComwattCoordinator(hass, entry)
    await restored_coord.async_load_energy_state()

    state = restored_coord._energy_state[23593]
    assert state.live_total_wh == 90.0
    assert state.published_total_wh == 100.0

    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [42.0],
        "timestamps": [1],
    }
    state.last_fetched_at = time.monotonic()
    restored_coord.data = {"devices": {23593: {"energy": 90.0}}}

    first_result = await hass.async_add_executor_job(
        restored_coord._fetch_device_metrics, DEVICE_23593
    )

    assert first_result["energy"] == 100.0
    assert state.live_total_wh == 90.0
    assert state.published_total_wh == 100.0

    state.last_power_w = 0.0
    state.last_power_t = time.monotonic() - 3600.0
    restored_coord.integrate_live_energy({23593: 20.0})

    assert state.live_total_wh == pytest.approx(100.0)
    assert state.published_total_wh == pytest.approx(100.0)
    assert restored_coord.data["devices"][23593]["energy"] == pytest.approx(100.0)


async def test_restore_missing_live_total_uses_nonzero_total_as_published_energy(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "23593": {
                    "total_wh": 321.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                },
                "23594": {
                    "live_total_wh": None,
                    "total_wh": 654.0,
                    "live_by_hour": {},
                    "last_bucket_ts": None,
                },
            },
        }
    )

    coord = ComwattCoordinator(hass, _make_entry(hass))
    await coord.async_load_energy_state()

    assert coord._energy_state[23593].live_total_wh is None
    assert coord._energy_state[23593].published_total_wh == 321.0
    assert coord._energy_state[23594].live_total_wh is None
    assert coord._energy_state[23594].published_total_wh == 654.0


async def test_save_persists_published_energy(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    entry = _make_entry(hass)
    coord = ComwattCoordinator(hass, entry)
    coord._energy_state[23593] = _EnergyState(
        live_total_wh=890.0,
        published_total_wh=1000.0,
    )

    await coord.async_save_energy_state()

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load()
    assert raw["data"]["23593"]["published_total_wh"] == 1000.0


async def test_refresh_serializes_stream_energy_and_patches_current_snapshot(
    hass: HomeAssistant,
    mock_comwatt_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coord = ComwattCoordinator(hass, _make_entry(hass))
    coord.data = {
        "sites": {},
        "devices": {23593: {"power": None, "energy": 80.0}},
        "switches": {},
    }
    coord._energy_state[23593] = _EnergyState(
        live_total_wh=90.0,
        published_total_wh=100.0,
        last_power_w=0.0,
        last_power_t=time.monotonic() - 3600.0,
    )
    refresh_data = {
        "sites": {},
        "devices": {23593: {"power": None, "energy": 90.0}},
        "switches": {},
    }
    monkeypatch.setattr(coord, "_fetch_all", lambda: refresh_data)

    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def blocked_save(_data: dict[str, Any]) -> None:
        save_started.set()
        await release_save.wait()

    monkeypatch.setattr(coord._energy_store, "async_save", blocked_save)
    committed_energies: list[float] = []
    monkeypatch.setattr(
        coord,
        "async_update_listeners",
        lambda: committed_energies.append(coord.data["devices"][23593]["energy"]),
    )

    refresh_task = asyncio.create_task(coord._async_refresh(log_failures=False))
    await save_started.wait()

    stream_task = asyncio.create_task(
        coord.async_integrate_live_energy({23593: 200.0})
    )
    await asyncio.sleep(0)
    assert stream_task.done() is False

    release_save.set()
    await refresh_task
    await stream_task

    assert committed_energies == [100.0]
    assert coord._energy_state[23593].published_total_wh == pytest.approx(190.0)
    assert coord.data["devices"][23593]["energy"] == pytest.approx(190.0)


async def test_first_install_no_store_is_unit_safe(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """At first install (no store), a kWh-scale QUANTITY/HOUR value of 0.9 is NOT
    raw-added to total_wh.  Without a live ∫W·dt reference _server_bucket_to_wh
    returns None and the bucket is skipped — total_wh stays at 0.0.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23598]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=900.0,
        quantity={
            "timestamps": ["2026-07-25T16:00:00.000+0000"],
            "values": [0.9],
        },
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[23598]
    assert state.live_total_wh is None
    assert state.total_wh == 0.0


async def test_store_schema_versioned(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Saved JSON has version=1 and a data dict keyed by string device id."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=200.0,
        quantity={"timestamps": ["2026-07-25T16:00:00.000+0000"], "values": [500.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load()

    assert raw is not None
    assert raw["version"] == 1
    assert "data" in raw
    assert "23593" in raw["data"]
    device_data = raw["data"]["23593"]
    assert "live_total_wh" in device_data
    assert "live_by_hour" in device_data
    assert "last_bucket_ts" in device_data
    assert "total_wh" in device_data
    assert "published_total_wh" in device_data


async def test_round_trip_live_by_hour_iso(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """live_by_hour datetime keys survive a save → load round-trip via ISO-UTC strings."""
    entry = _make_entry(hass)
    coord = ComwattCoordinator(hass, entry)

    hour = datetime(2026, 7, 25, 16, 0, tzinfo=UTC)
    last_ts = datetime(2026, 7, 25, 16, 0, 0, tzinfo=UTC)
    coord._energy_state[23593] = _EnergyState(
        live_total_wh=12.34,
        total_wh=5.0,
        live_by_hour={hour: 12.34},
        last_bucket_ts=last_ts,
    )

    await coord.async_save_energy_state()

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load()
    assert raw["data"]["23593"]["live_by_hour"]["2026-07-25T16:00:00+00:00"] == 12.34

    coord2 = ComwattCoordinator(hass, entry)
    await coord2.async_load_energy_state()

    state2 = coord2._energy_state[23593]
    assert state2.live_total_wh == 12.34
    assert state2.published_total_wh == 12.34
    assert state2.total_wh == 5.0
    assert state2.live_by_hour == {hour: 12.34}
    assert state2.last_bucket_ts == last_ts
    assert state2.last_power_w is None
    assert state2.last_power_t is None


async def test_published_energy_is_fresh_not_stale_snapshot(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Sequential fetch of multiple devices with concurrent stream advances:
    published energy must be fresh (read at publication time), not the stale
    snapshot captured during _fetch_device_metrics for earlier devices.

    Simulates the production bug: device 1 is fetched and its energy is captured
    as 1000.0. While device 2 is being fetched, the stream thread advances
    device 1's live_total_wh to 1010.0 (via integrate_live_energy). At
    publication time, device 1's published energy should be 1010.0 (fresh),
    not 1000.0 (stale snapshot).
    """
    DEVICE_1 = {"id": "dev-1", "name": "Device 1", "deviceKind": {"code": "PANEL"}}
    DEVICE_2 = {"id": "dev-2", "name": "Device 2", "deviceKind": {"code": "PANEL"}}

    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE_1, DEVICE_2]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    coord._energy_state["dev-1"].live_total_wh = 1000.0
    coord._energy_state["dev-1"].last_fetched_at = time.monotonic() - 60 * 60
    coord._energy_state["dev-2"].live_total_wh = 2000.0
    coord._energy_state["dev-2"].last_fetched_at = time.monotonic() - 60 * 60

    def concurrent_stream_advance(device_id: str, kind: str, *rest: object) -> dict[str, Any]:
        if kind == "QUANTITY" and device_id == "dev-2":
            coord._energy_state["dev-1"].live_total_wh = 1010.0
        return ({"timestamps": [1], "values": [100.0]}
                if kind == "QUANTITY"
                else {"values": [42.0], "timestamps": [1]})

    mock_comwatt_client.get_device_ts_time_ago.side_effect = concurrent_stream_advance

    await coord.async_refresh()
    await hass.async_block_till_done()

    assert coord.data["devices"]["dev-1"]["energy"] == 1010.0


async def test_publish_pass_leaves_stream_less_device_untouched(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A device without stream ownership (live_total_wh is None) retains the
    energy value returned by _fetch_device_metrics. The final publish pass
    only rewrites energy when live_total_wh is not None.
    """
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = [DEVICE]
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": [1], "values": [100.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._energy_state[DEVICE["id"]]
    state.live_total_wh = None
    state.total_wh = 456.0
    state.last_fetched_at = time.monotonic() - 60 * 60

    await coord.async_refresh()
    await hass.async_block_till_done()

    assert coord.data["devices"][DEVICE["id"]]["energy"] == 456.0


def _site_ts_side_effect(quantity: dict[str, Any]) -> Any:
    """Route get_site_time_series by measure kind: FLOW → empty, QUANTITY → buckets."""

    def _route(site_id: str, measure_kind: str, *rest: object) -> dict[str, Any]:
        if measure_kind == "QUANTITY":
            return quantity
        return {"autoproductionRates": []}

    return _route


def _count_site_quantity_calls(client: MagicMock) -> int:
    """Count get_site_time_series calls made with measure kind QUANTITY."""
    return sum(
        1
        for call in client.get_site_time_series.call_args_list
        if len(call.args) >= 2 and call.args[1] == "QUANTITY"
    )


async def test_site_energy_fetch_is_gated(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The site QUANTITY/HOUR call runs at most once per ENERGY_MIN_FETCH_INTERVAL_S,
    mirroring the device gate: a 2-min refresh does not re-call the endpoint."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {"timestamps": [1], "productions": [10.0]}
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _count_site_quantity_calls(mock_comwatt_client) == 1

    coord = entry.runtime_data
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert _count_site_quantity_calls(mock_comwatt_client) == 1, "site energy endpoint should be skipped"

    coord._last_site_energy_fetch = time.monotonic() - 60 * 60
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert (
        _count_site_quantity_calls(mock_comwatt_client) == 2
    ), "site energy endpoint should be called again after the interval"


async def test_site_energy_seed_all_sites_same_poll(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Every site is folded in the same gated poll — the coordinator-level gate
    must not starve sites listed after the first one (each site gets its seed
    and its own totals on the first refresh)."""
    site_int = {"id": 3349, "name": "Greil", "siteKind": "RESIDENTIAL"}
    mock_comwatt_client.get_sites.return_value = [SITE, site_int]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]}
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _count_site_quantity_calls(mock_comwatt_client) == 2
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert coord.data["sites"][3349]["production_total_energy"] == 100.0


async def test_site_energy_state_uses_reserved_store_key(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Site totals persist under the reserved `__sites__` key of the existing
    energy store — never mixed with per-device entries — and restore into a
    fresh coordinator, with int site ids normalized back to int keys."""
    site_int = {"id": 3349, "name": "Greil", "siteKind": "RESIDENTIAL"}
    mock_comwatt_client.get_sites.return_value = [SITE, site_int]
    mock_comwatt_client.get_devices.return_value = [DEVICE_23593]
    mock_comwatt_client.get_device_ts_time_ago.side_effect = _device_ts_side_effect(
        power=42.0,
        quantity={"timestamps": ["2026-07-14T11:00:00.000+0000"], "values": [500.0]},
    )
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]}
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load()
    assert raw is not None
    assert raw["version"] == 1
    assert "23593" in raw["data"], "device entries keep their per-id keys"
    sites_entry = raw["data"]["__sites__"]
    assert sites_entry["site-1"]["totals"] == {"production": 100.0}
    assert sites_entry["site-1"]["last_bucket_ts"] == "2026-08-15T10:00:00+00:00"
    assert sites_entry["3349"]["totals"] == {"production": 100.0}

    coord2 = ComwattCoordinator(hass, entry)
    await coord2.async_load_energy_state()
    assert set(coord2._site_energy_state) == {"site-1", 3349}
    state2 = coord2._site_energy_state["site-1"]
    assert state2.totals == {"production": 100.0}
    assert state2.last_bucket_ts == datetime(2026, 8, 15, 10, 0, tzinfo=UTC)

async def test_site_energy_skips_negative_bucket_values(
    hass: HomeAssistant, mock_comwatt_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A negative Wh bucket is corrupt upstream data: folding it would push a
    TOTAL_INCREASING sensor backwards and corrupt HA statistics, so it is
    skipped (the total stays put) and a warning is logged naming the metric,
    value, site and bucket timestamp."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {
            "timestamps": [
                "2026-08-15T10:00:00.000+0000",
                "2026-08-15T11:00:00.000+0000",
            ],
            "productions": [100.0, -50.0],
            "consumptions": [10.0, 30.0],
        }
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert coord.data["sites"]["site-1"]["consumption_total_energy"] == 40.0

    coord._last_site_energy_fetch = time.monotonic() - 60 * 60
    await coord.async_refresh()
    await hass.async_block_till_done()
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "negative" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "production" in warnings[0].getMessage()
    assert "-50" in warnings[0].getMessage()
    assert "site-1" in warnings[0].getMessage()
    assert "2026-08-15T11:00:00+00:00" in warnings[0].getMessage()


async def test_site_energy_short_series_does_not_corrupt_other_metrics(
    hass: HomeAssistant, mock_comwatt_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A metric whose series is missing or shorter than `timestamps` only loses
    its own bucket: every other metric still folds the full window, and the
    skip is observable as a debug log naming the metric, index, series length,
    site and bucket timestamp."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {
            "timestamps": [
                "2026-08-15T10:00:00.000+0000",
                "2026-08-15T11:00:00.000+0000",
            ],
            "productions": [100.0, 200.0],
            "consumptions": [10.0],
        }
    )

    with caplog.at_level(logging.DEBUG, logger="custom_components.comwatt.coordinator"):
        entry = _make_entry(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 300.0
    assert coord.data["sites"]["site-1"]["consumption_total_energy"] == 10.0

    skips = [
        r for r in caplog.records if r.levelno == logging.DEBUG and "site bucket" in r.getMessage()
    ]
    assert len(skips) == 1
    message = skips[0].getMessage()
    assert "consumption" in message
    assert "site-1" in message
    assert "2026-08-15T11:00:00+00:00" in message


def _sequential_site_ts_side_effect(*quantities: dict[str, Any]) -> Any:
    """Route get_site_time_series; each QUANTITY call returns the next response."""

    remaining = list(quantities)

    def _route(site_id: str, measure_kind: str, *rest: object) -> dict[str, Any]:
        if measure_kind == "QUANTITY":
            return remaining.pop(0)
        return {"autoproductionRates": []}

    return _route


async def _rewound_site_refresh(
    coord: ComwattCoordinator, hass: HomeAssistant
) -> None:
    """Reopen the ~55-min site energy gate, then run one coordinator refresh."""
    coord._last_site_energy_fetch = time.monotonic() - 60 * 60
    await coord.async_refresh()
    await hass.async_block_till_done()


async def test_site_bucket_fold_records_ledger(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Folding a new bucket records what was actually folded, per (bucket,
    metric), in the site's folded_buckets ledger (bucket UTC ISO key → metric
    → folded Wh). A metric with no series for that bucket gets no entry, so
    the ledger never invents a baseline to reconcile from."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {
            "timestamps": ["2026-08-15T10:00:00.000+0000"],
            "productions": [100.0],
        }
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coord = entry.runtime_data
    state = coord._site_energy_state["site-1"]
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert state.folded_buckets == {
        "2026-08-15T10:00:00+00:00": {"production": 100.0}
    }


async def test_site_bucket_upward_revision_caught_up_idempotently(
    hass: HomeAssistant, mock_comwatt_client: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A completed bucket the server revised upward after publication (issue
    #51) is caught up by delta exactly once: the positive delta is added to the
    total and the ledger entry is raised to the new server value, so the next
    fetch of the same response computes delta 0 and adds nothing. Each catch-up
    logs at debug, plus one per-site summary with the total Wh caught up."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [110.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [110.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0

    with caplog.at_level(logging.DEBUG, logger="custom_components.comwatt.coordinator"):
        await _rewound_site_refresh(coord, hass)

    assert coord.data["sites"]["site-1"]["production_total_energy"] == 110.0
    state = coord._site_energy_state["site-1"]
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["production"] == 110.0
    catches = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "Catching up revised site bucket" in r.getMessage()
    ]
    assert len(catches) == 1
    assert "+10.0" in catches[0].getMessage()
    summaries = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG and "Caught up" in r.getMessage()
    ]
    assert len(summaries) == 1
    assert "10.0" in summaries[0].getMessage()

    await _rewound_site_refresh(coord, hass)
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 110.0
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["production"] == 110.0


async def test_site_bucket_downward_revision_ignored_and_floor_enforced(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A downward revision is ignored permanently: neither the total nor the
    ledger entry moves, so the folded value stays a floor. A later upward
    re-revision only folds the part above the floor: 100 → 80 → 110 ends at
    110 Wh (+10), not 130."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [80.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [110.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0

    await _rewound_site_refresh(coord, hass)
    state = coord._site_energy_state["site-1"]
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["production"] == 100.0

    await _rewound_site_refresh(coord, hass)
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 110.0
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["production"] == 110.0


async def test_site_revision_reconciles_per_metric(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Reconciliation is per (bucket, metric) independently: when the revised
    response only carries the productions series (consumptions missing for that
    bucket), production catches up its delta while consumption keeps its folded
    total — a metric absent from one series never reconciles."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {
            "timestamps": ["2026-08-15T10:00:00.000+0000"],
            "productions": [100.0],
            "consumptions": [40.0],
        },
        {
            "timestamps": ["2026-08-15T10:00:00.000+0000"],
            "productions": [130.0],
        },
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert coord.data["sites"]["site-1"]["consumption_total_energy"] == 40.0

    await _rewound_site_refresh(coord, hass)

    assert coord.data["sites"]["site-1"]["production_total_energy"] == 130.0
    assert coord.data["sites"]["site-1"]["consumption_total_energy"] == 40.0
    state = coord._site_energy_state["site-1"]
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["production"] == 130.0
    assert state.folded_buckets["2026-08-15T10:00:00+00:00"]["consumption"] == 40.0


async def test_site_ledger_prunes_buckets_outside_response(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Ledger entries whose bucket no longer appears in the response (the
    rolling 8-day window moved on) are pruned, keeping the ledger bounded;
    surviving entries still reconcile upward."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {
            "timestamps": [
                "2026-08-15T10:00:00.000+0000",
                "2026-08-15T11:00:00.000+0000",
            ],
            "productions": [100.0, 50.0],
        },
        {
            "timestamps": ["2026-08-15T11:00:00.000+0000"],
            "productions": [60.0],
        },
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 150.0
    state = coord._site_energy_state["site-1"]
    assert set(state.folded_buckets) == {
        "2026-08-15T10:00:00+00:00",
        "2026-08-15T11:00:00+00:00",
    }

    await _rewound_site_refresh(coord, hass)

    assert coord.data["sites"]["site-1"]["production_total_energy"] == 160.0
    assert state.folded_buckets == {
        "2026-08-15T11:00:00+00:00": {"production": 60.0}
    }


async def test_empty_or_unparseable_response_keeps_ledger(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A well-formed 200 response that yields no parseable bucket (timestamps
    all unparseable, or an empty list) prunes nothing: without a single parsed
    bucket proving the server window actually moved, the folded_buckets ledger
    stays untouched so those buckets can still reconcile when the server
    responds normally again."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]},
        {"timestamps": ["not-a-timestamp", "also not one"], "productions": [999.0, 999.0]},
        {"timestamps": [], "productions": []},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    state = coord._site_energy_state["site-1"]
    assert state.folded_buckets == {
        "2026-08-15T10:00:00+00:00": {"production": 100.0}
    }

    await _rewound_site_refresh(coord, hass)
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert state.folded_buckets == {
        "2026-08-15T10:00:00+00:00": {"production": 100.0}
    }

    await _rewound_site_refresh(coord, hass)
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert state.folded_buckets == {
        "2026-08-15T10:00:00+00:00": {"production": 100.0}
    }


async def test_site_totals_without_ledger_entries_untouched(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """Totals that predate the ledger (no folded_buckets entries, e.g. state
    restored from a store version that did not persist the ledger) are never
    reconciled: catch-up only works from a recorded fold and never invents a
    baseline, so an upward revision leaves the total untouched."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [150.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0

    coord._site_energy_state["site-1"].folded_buckets.clear()
    await _rewound_site_refresh(coord, hass)

    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0
    assert coord._site_energy_state["site-1"].folded_buckets == {}


async def test_site_bucket_ledger_survives_reload_and_catches_up_once(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """The folded_buckets ledger round-trips through the store: a fresh
    coordinator restores the same buckets/metrics/values, so an upward server
    revision of an already-folded bucket is caught up exactly once after a
    reload — without the persisted ledger the total would stay frozen at the
    folded value, and a double fold would land at 120, not 110."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _sequential_site_ts_side_effect(
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [100.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [110.0]},
        {"timestamps": ["2026-08-15T10:00:00.000+0000"], "productions": [110.0]},
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = entry.runtime_data
    assert coord.data["sites"]["site-1"]["production_total_energy"] == 100.0

    coord2 = ComwattCoordinator(hass, entry)
    await coord2.async_load_energy_state()
    state2 = coord2._site_energy_state["site-1"]
    assert state2.totals == {"production": 100.0}
    assert state2.last_bucket_ts == datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    assert state2.folded_buckets == {
        "2026-08-15T10:00:00+00:00": {"production": 100.0}
    }

    await _rewound_site_refresh(coord2, hass)

    assert coord2.data["sites"]["site-1"]["production_total_energy"] == 110.0
    assert (
        coord2._site_energy_state["site-1"].folded_buckets[
            "2026-08-15T10:00:00+00:00"
        ]["production"]
        == 110.0
    )

    await _rewound_site_refresh(coord2, hass)

    assert coord2.data["sites"]["site-1"]["production_total_energy"] == 110.0
    assert (
        coord2._site_energy_state["site-1"].folded_buckets[
            "2026-08-15T10:00:00+00:00"
        ]["production"]
        == 110.0
    )


async def test_old_site_state_without_ledger_loads_empty_ledger(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A site entry persisted before store minor 2 (no `folded_buckets` key)
    loads without error: totals and last_bucket_ts are restored unchanged and
    the ledger comes back empty, so pre-existing drift stays frozen."""
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(
        {
            "version": 1,
            "data": {
                "__sites__": {
                    "site-1": {
                        "totals": {"production": 123.0, "consumption": 45.0},
                        "last_bucket_ts": "2026-08-15T10:00:00+00:00",
                    }
                }
            },
        }
    )

    entry = _make_entry(hass)
    coord = ComwattCoordinator(hass, entry)
    await coord.async_load_energy_state()

    state = coord._site_energy_state["site-1"]
    assert state.totals == {"production": 123.0, "consumption": 45.0}
    assert state.last_bucket_ts == datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    assert state.folded_buckets == {}


async def test_site_bucket_ledger_persisted_json_shape(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    mock_comwatt_client: MagicMock,
) -> None:
    """The persisted site entry carries `folded_buckets` with bucket UTC ISO
    string keys mapping metric names to floats, and the persisted store
    envelope is version 1 / minor 2."""
    mock_comwatt_client.get_sites.return_value = [SITE]
    mock_comwatt_client.get_devices.return_value = []
    mock_comwatt_client.get_site_time_series.side_effect = _site_ts_side_effect(
        {
            "timestamps": ["2026-08-15T10:00:00.000+0000"],
            "productions": [100.0],
            "consumptions": [40.0],
        }
    )

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    store = Store(hass, _STORE_VERSION, _STORE_KEY, minor_version=_STORE_MINOR_VERSION)
    raw = await store.async_load()
    assert raw is not None
    sites_entry = raw["data"]["__sites__"]
    ledger = sites_entry["site-1"]["folded_buckets"]
    assert ledger == {"2026-08-15T10:00:00+00:00": {"production": 100.0, "consumption": 40.0}}
    assert isinstance(ledger["2026-08-15T10:00:00+00:00"]["production"], float)

    envelope = hass_storage[_STORE_KEY]
    assert envelope["version"] == 1
    assert envelope["minor_version"] == _STORE_MINOR_VERSION
