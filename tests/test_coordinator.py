"""Tests for the Comwatt DataUpdateCoordinator."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
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
    assert result == {"power": 42.0, "energy": 1234.0 + (500.0 - 510.0)}


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


async def test_accumulation_unchanged_when_stream_not_active(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """When the stream has not taken over (`live_total_wh is None`), QUANTITY/
    HOUR buckets accumulate into `total_wh` exactly as before Slice 5 — the
    fallback for devices whose stream never started."""
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

    assert state.total_wh == 25.0
    assert state.live_total_wh is None
    assert state.live_by_hour == {}
    assert state.last_bucket_ts == datetime(1970, 1, 1, 0, 0, 2, tzinfo=UTC)
    assert result == {"power": 42.0, "energy": 25.0}


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


async def test_reconcile_skips_kwh_unit_bucket_so_no_hundred_wh_jump(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    """A grid device whose QUANTITY/HOUR returns kWh (server val ~0.9 for an
    ~900 Wh hour) must NOT snap the live total down to 0.9. The unit cannot be
    trusted, so the bucket is skipped: the live ∫W·dt total is left untouched
    and the high-water mark still advances so the bucket is not reconsidered."""
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
