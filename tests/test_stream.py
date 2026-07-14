"""Tests for the Comwatt WebSocket stream manager and switch routing."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from comwatt_client import Measurement
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN
from custom_components.comwatt.stream import ComwattStreamManager, _apply_switch_updates

ENTRY_DATA = {"username": "user@example.com", "password": "secret"}
SWITCH_CAPACITY_ID = "AZUREIOT-co.10.instances.0.switch.0.data"
SWITCH_DEVICE_ID = "129443"


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    entry.add_to_hass(hass)
    return entry


def _measurement(
    *,
    capacity_id: str,
    measure_kind: str,
    value_bool: bool | None,
    value: str = "true",
) -> Measurement:
    return Measurement(
        gateway_uid="g",
        capacity_id=capacity_id,
        measure_kind=measure_kind,
        value=value,
        value_float=None,
        value_bool=value_bool,
    )


def _switch_capacity_map() -> dict[str, tuple[str, str, bool]]:
    return {SWITCH_CAPACITY_ID: (SWITCH_DEVICE_ID, "POWER_SWITCH", False)}


def _switches_data(*, is_on: bool) -> dict[str, dict[str, Any]]:
    return {SWITCH_DEVICE_ID: {"is_on": is_on, "capacity_id": "133095"}}


def test_apply_switch_updates_updates_switch_and_returns_true() -> None:
    capacity_map = _switch_capacity_map()
    switches_data = _switches_data(is_on=False)
    batch = [
        _measurement(
            capacity_id=SWITCH_CAPACITY_ID, measure_kind="STATE", value_bool=True
        )
    ]
    assert _apply_switch_updates(batch, capacity_map, switches_data) is True
    assert switches_data[SWITCH_DEVICE_ID]["is_on"] is True


def test_apply_switch_updates_ignores_flow_measurements() -> None:
    capacity_map = _switch_capacity_map()
    switches_data = _switches_data(is_on=False)
    batch = [
        _measurement(
            capacity_id=SWITCH_CAPACITY_ID,
            measure_kind="FLOW",
            value_bool=None,
            value="42.0",
        )
    ]
    assert _apply_switch_updates(batch, capacity_map, switches_data) is False
    assert switches_data[SWITCH_DEVICE_ID]["is_on"] is False


def test_apply_switch_updates_ignores_unmapped_and_non_switch_nature() -> None:
    capacity_map: dict[str, tuple[str, str, bool]] = {
        SWITCH_CAPACITY_ID: (SWITCH_DEVICE_ID, "POWER_SWITCH", False),
        "AZUREIOT-co.2.instances.3.sensor.3.data": ("23600", "CLAMP", False),
    }
    switches_data: dict[str, dict[str, Any]] = {
        SWITCH_DEVICE_ID: {"is_on": False, "capacity_id": "133095"},
        "23600": {"is_on": False, "capacity_id": "x"},
    }

    unmapped = [
        _measurement(
            capacity_id="AZUREIOT-co.99.instances.0.switch.0.data",
            measure_kind="STATE",
            value_bool=True,
        )
    ]
    assert _apply_switch_updates(unmapped, capacity_map, switches_data) is False
    assert switches_data[SWITCH_DEVICE_ID]["is_on"] is False

    clamp = [
        _measurement(
            capacity_id="AZUREIOT-co.2.instances.3.sensor.3.data",
            measure_kind="STATE",
            value_bool=True,
        )
    ]
    assert _apply_switch_updates(clamp, capacity_map, switches_data) is False
    assert switches_data["23600"]["is_on"] is False


def test_apply_switch_updates_sets_is_on_false_for_turn_off() -> None:
    capacity_map = _switch_capacity_map()
    switches_data = _switches_data(is_on=True)
    batch = [
        _measurement(
            capacity_id=SWITCH_CAPACITY_ID,
            measure_kind="STATE",
            value_bool=False,
            value="false",
        )
    ]
    assert _apply_switch_updates(batch, capacity_map, switches_data) is True
    assert switches_data[SWITCH_DEVICE_ID]["is_on"] is False


def test_process_batch_notifies_coordinator_only_on_change(
    mock_comwatt_client: MagicMock,
) -> None:
    coordinator = MagicMock()
    coordinator.capacity_map = _switch_capacity_map()
    coordinator.data = {"switches": _switches_data(is_on=False)}
    coordinator.async_set_updated_data = MagicMock()

    manager = ComwattStreamManager(MagicMock(), coordinator, "user", "pass")

    manager._process_batch(
        [
            _measurement(
                capacity_id=SWITCH_CAPACITY_ID, measure_kind="STATE", value_bool=True
            )
        ]
    )
    assert coordinator.async_set_updated_data.call_count == 1
    assert coordinator.data["switches"][SWITCH_DEVICE_ID]["is_on"] is True

    manager._process_batch(
        [
            _measurement(
                capacity_id=SWITCH_CAPACITY_ID,
                measure_kind="FLOW",
                value_bool=None,
                value="42.0",
            )
        ]
    )
    assert coordinator.async_set_updated_data.call_count == 1


async def test_consumer_updates_switch_state_and_teardown_cleans_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    stream_site = {
        "id": "site-1",
        "name": "Home",
        "siteKind": "RESIDENTIAL",
        "siteUid": "site-uid-1",
    }
    switch_device = {
        "id": SWITCH_DEVICE_ID,
        "name": "Relay",
        "deviceKind": {"code": "RELAY"},
        "features": [
            {
                "capacities": [
                    {"capacity": {"id": "133095", "nature": "POWER_SWITCH", "enable": False}}
                ]
            }
        ],
    }
    mock_comwatt_client.get_sites.return_value = [stream_site]
    mock_comwatt_client.get_devices.return_value = [switch_device]
    mock_comwatt_client.get_device.return_value = switch_device
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_connected_objects.return_value = [
        {
            "capacities": [
                {
                    "capacityId": SWITCH_CAPACITY_ID,
                    "deviceId": int(SWITCH_DEVICE_ID),
                    "nature": "POWER_SWITCH",
                    "production": False,
                }
            ]
        }
    ]

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    manager = coordinator.stream_manager
    assert manager is not None
    assert coordinator.data["switches"][SWITCH_DEVICE_ID]["is_on"] is False

    manager._queue.put_nowait(
        _measurement(
            capacity_id=SWITCH_CAPACITY_ID, measure_kind="STATE", value_bool=True
        )
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    assert coordinator.data["switches"][SWITCH_DEVICE_ID]["is_on"] is True

    await manager.async_stop()
    assert manager._consumer_task.done()
    assert all(task.done() for task in manager._stream_tasks)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
