"""Tests for the Comwatt WebSocket stream manager and switch routing."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

from comwatt_client import Measurement
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comwatt.const import DOMAIN
from custom_components.comwatt.coordinator import ComwattCoordinator, _EnergyState
from custom_components.comwatt.stream import (
    ComwattStreamManager,
    _apply_power_updates,
    _apply_switch_updates,
    _compute_device_powers,
)

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
    value_float: float | None = None,
) -> Measurement:
    return Measurement(
        gateway_uid="g",
        capacity_id=capacity_id,
        measure_kind=measure_kind,
        value=value,
        value_float=value_float,
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
    coordinator.data = {
        "switches": _switches_data(is_on=False),
        "devices": {},
    }
    coordinator.async_update_listeners = MagicMock()

    manager = ComwattStreamManager(MagicMock(), coordinator, "user", "pass")

    manager._process_batch(
        [
            _measurement(
                capacity_id=SWITCH_CAPACITY_ID, measure_kind="STATE", value_bool=True
            )
        ]
    )
    assert coordinator.async_update_listeners.call_count == 1
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
    assert coordinator.async_update_listeners.call_count == 1


async def test_consume_swallows_batch_errors_and_keeps_running(
    mock_comwatt_client: MagicMock,
) -> None:
    coordinator = MagicMock()
    coordinator.capacity_map = {}
    coordinator.data = {"switches": {}}

    manager = ComwattStreamManager(MagicMock(), coordinator, "user", "pass")
    manager._queue = asyncio.Queue()

    calls: list[int] = []

    def fake_process_batch(batch: list[Any]) -> None:
        calls.append(len(batch))
        if len(calls) == 1:
            raise RuntimeError("boom")

    manager._process_batch = fake_process_batch

    manager._consumer_task = asyncio.create_task(manager._consume())

    manager._queue.put_nowait("batch-1")
    await asyncio.sleep(0.05)
    manager._queue.put_nowait("batch-2")
    await asyncio.sleep(0.05)

    assert manager._consumer_task.done() is False
    assert len(calls) == 2

    manager._consumer_task.cancel()
    await asyncio.gather(manager._consumer_task, return_exceptions=True)


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


def test_apply_power_updates_sums_multi_instance_device_power() -> None:
    capacity_map = {
        "co.1.inst.0.sensor.0.data": ("23593", "CLAMP", True),
        "co.1.inst.1.sensor.1.data": ("23593", "CLAMP", True),
        "co.1.inst.2.sensor.2.data": ("23593", "CLAMP", True),
    }
    devices_data = {"23593": {"power": None, "energy": None}}
    batch = [
        _measurement(
            capacity_id="co.1.inst.0.sensor.0.data",
            measure_kind="FLOW",
            value_bool=None,
            value="629.0",
            value_float=629.0,
        ),
        _measurement(
            capacity_id="co.1.inst.1.sensor.1.data",
            measure_kind="FLOW",
            value_bool=None,
            value="622.0",
            value_float=622.0,
        ),
        _measurement(
            capacity_id="co.1.inst.2.sensor.2.data",
            measure_kind="FLOW",
            value_bool=None,
            value="604.0",
            value_float=604.0,
        ),
    ]
    device_powers = _compute_device_powers(batch, capacity_map)
    assert device_powers == {"23593": 1855.0}
    assert _apply_power_updates(device_powers, devices_data) is True
    assert devices_data["23593"]["power"] == 1855.0


def test_apply_power_updates_writes_single_instance_device_power() -> None:
    capacity_map = {"co.2.inst.3.sensor.3.data": ("23600", "CLAMP", False)}
    devices_data = {"23600": {"power": None, "energy": None}}
    batch = [
        _measurement(
            capacity_id="co.2.inst.3.sensor.3.data",
            measure_kind="FLOW",
            value_bool=None,
            value="161.0",
            value_float=161.0,
        )
    ]
    device_powers = _compute_device_powers(batch, capacity_map)
    assert device_powers == {"23600": 161.0}
    assert _apply_power_updates(device_powers, devices_data) is True
    assert devices_data["23600"]["power"] == 161.0


def test_apply_power_updates_ignores_state_and_quantity_measurements() -> None:
    capacity_map = {
        SWITCH_CAPACITY_ID: (SWITCH_DEVICE_ID, "POWER_SWITCH", False),
        "co.2.inst.3.sensor.3.data": ("23600", "CLAMP", False),
    }
    devices_data = {
        SWITCH_DEVICE_ID: {"is_on": False, "capacity_id": "133095"},
        "23600": {"power": None, "energy": None},
    }
    batch = [
        _measurement(
            capacity_id=SWITCH_CAPACITY_ID,
            measure_kind="STATE",
            value_bool=True,
        ),
        _measurement(
            capacity_id="co.2.inst.3.sensor.3.data",
            measure_kind="QUANTITY",
            value_bool=None,
            value="999.0",
            value_float=999.0,
        ),
    ]
    device_powers = _compute_device_powers(batch, capacity_map)
    assert device_powers == {}
    assert _apply_power_updates(device_powers, devices_data) is False
    assert devices_data["23600"]["power"] is None


def test_apply_power_updates_ignores_unmapped_and_null_value_float() -> None:
    capacity_map = {"co.2.inst.3.sensor.3.data": ("23600", "CLAMP", False)}
    devices_data = {"23600": {"power": None, "energy": None}}

    unmapped = [
        _measurement(
            capacity_id="co.99.inst.0.sensor.0.data",
            measure_kind="FLOW",
            value_bool=None,
            value="42.0",
            value_float=42.0,
        )
    ]
    device_powers = _compute_device_powers(unmapped, capacity_map)
    assert device_powers == {}
    assert _apply_power_updates(device_powers, devices_data) is False
    assert devices_data["23600"]["power"] is None

    null_value = [
        _measurement(
            capacity_id="co.2.inst.3.sensor.3.data",
            measure_kind="FLOW",
            value_bool=None,
            value="",
            value_float=None,
        )
    ]
    device_powers = _compute_device_powers(null_value, capacity_map)
    assert device_powers == {}
    assert _apply_power_updates(device_powers, devices_data) is False
    assert devices_data["23600"]["power"] is None


def test_process_batch_notifies_on_power_or_switch_change(
    mock_comwatt_client: MagicMock,
) -> None:
    coordinator = MagicMock()
    coordinator.capacity_map = {
        SWITCH_CAPACITY_ID: (SWITCH_DEVICE_ID, "POWER_SWITCH", False),
        "co.2.inst.3.sensor.3.data": ("23600", "CLAMP", False),
    }
    coordinator.data = {
        "switches": {SWITCH_DEVICE_ID: {"is_on": False, "capacity_id": "x"}},
        "devices": {"23600": {"power": None, "energy": None}},
    }
    coordinator.async_update_listeners = MagicMock()

    manager = ComwattStreamManager(MagicMock(), coordinator, "user", "pass")

    manager._process_batch(
        [
            _measurement(
                capacity_id="co.2.inst.3.sensor.3.data",
                measure_kind="FLOW",
                value_bool=None,
                value="161.0",
                value_float=161.0,
            )
        ]
    )
    assert coordinator.async_update_listeners.call_count == 1
    assert coordinator.data["devices"]["23600"]["power"] == 161.0

    manager._process_batch(
        [
            _measurement(
                capacity_id=SWITCH_CAPACITY_ID,
                measure_kind="STATE",
                value_bool=True,
            )
        ]
    )
    assert coordinator.async_update_listeners.call_count == 2
    assert coordinator.data["switches"][SWITCH_DEVICE_ID]["is_on"] is True

    manager._process_batch(
        [
            _measurement(
                capacity_id="co.99.inst.0.sensor.0.data",
                measure_kind="FLOW",
                value_bool=None,
                value="42.0",
                value_float=42.0,
            )
        ]
    )
    assert coordinator.async_update_listeners.call_count == 2


async def test_consumer_updates_device_power_and_teardown_cleans_up(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    stream_site = {
        "id": "site-1",
        "name": "Home",
        "siteKind": "RESIDENTIAL",
        "siteUid": "site-uid-1",
    }
    solar_device = {
        "id": "23593",
        "name": "Solar Inverter",
        "deviceKind": {"code": "SOLAR"},
    }
    mock_comwatt_client.get_sites.return_value = [stream_site]
    mock_comwatt_client.get_devices.return_value = [solar_device]
    mock_comwatt_client.get_device.return_value = solar_device
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_connected_objects.return_value = [
        {
            "capacities": [
                {
                    "capacityId": "co.1.inst.0.sensor.0.data",
                    "deviceId": 23593,
                    "nature": "CLAMP",
                    "production": True,
                },
                {
                    "capacityId": "co.1.inst.1.sensor.1.data",
                    "deviceId": 23593,
                    "nature": "CLAMP",
                    "production": True,
                },
                {
                    "capacityId": "co.1.inst.2.sensor.2.data",
                    "deviceId": 23593,
                    "nature": "CLAMP",
                    "production": True,
                },
            ]
        }
    ]

    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    manager = coordinator.stream_manager
    assert manager is not None
    assert coordinator.data["devices"]["23593"]["power"] is None

    manager._queue.put_nowait(
        _measurement(
            capacity_id="co.1.inst.0.sensor.0.data",
            measure_kind="FLOW",
            value_bool=None,
            value="629.0",
            value_float=629.0,
        )
    )
    manager._queue.put_nowait(
        _measurement(
            capacity_id="co.1.inst.1.sensor.1.data",
            measure_kind="FLOW",
            value_bool=None,
            value="622.0",
            value_float=622.0,
        )
    )
    manager._queue.put_nowait(
        _measurement(
            capacity_id="co.1.inst.2.sensor.2.data",
            measure_kind="FLOW",
            value_bool=None,
            value="604.0",
            value_float=604.0,
        )
    )
    await hass.async_block_till_done()
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()

    assert coordinator.data["devices"]["23593"]["power"] == 1855.0

    await manager.async_stop()
    assert manager._consumer_task.done()
    assert all(task.done() for task in manager._stream_tasks)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_compute_device_powers_sums_multi_instance_flow() -> None:
    capacity_map = {
        "co.1.inst.0.sensor.0.data": ("23593", "CLAMP", True),
        "co.1.inst.1.sensor.1.data": ("23593", "CLAMP", True),
        "co.1.inst.2.sensor.2.data": ("23593", "CLAMP", True),
    }
    batch = [
        _measurement(
            capacity_id="co.1.inst.0.sensor.0.data",
            measure_kind="FLOW",
            value_bool=None,
            value="629.0",
            value_float=629.0,
        ),
        _measurement(
            capacity_id="co.1.inst.1.sensor.1.data",
            measure_kind="FLOW",
            value_bool=None,
            value="622.0",
            value_float=622.0,
        ),
        _measurement(
            capacity_id="co.1.inst.2.sensor.2.data",
            measure_kind="FLOW",
            value_bool=None,
            value="604.0",
            value_float=604.0,
        ),
    ]
    assert _compute_device_powers(batch, capacity_map) == {"23593": 1855.0}


def test_integrate_live_energy_accumulates_trapezoidal(
    mock_comwatt_client: MagicMock,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    coord = ComwattCoordinator(MagicMock(), entry)
    coord.data = {"devices": {"23593": {"power": None, "energy": None}}}
    coord._energy_state = {}

    scripted = iter([1000.0, 1036.0])
    with patch(
        "custom_components.comwatt.coordinator.monotonic",
        side_effect=lambda: next(scripted, 1036.0),
    ):
        coord.integrate_live_energy({"23593": 100.0})
        state = coord._energy_state["23593"]
        assert state.live_total_wh == 0.0
        assert state.last_power_w == 100.0
        assert coord.data["devices"]["23593"]["energy"] == 0.0

        coord.integrate_live_energy({"23593": 200.0})

    state = coord._energy_state["23593"]
    assert state.live_total_wh == 1.5
    assert state.last_power_w == 200.0
    assert coord.data["devices"]["23593"]["energy"] == 1.5
    assert len(state.live_by_hour) == 1
    hour = next(iter(state.live_by_hour))
    assert hour.minute == 0
    assert hour.second == 0
    assert hour.microsecond == 0
    assert state.live_by_hour[hour] == 1.5


def test_integrate_live_energy_seeds_from_poll_total(
    mock_comwatt_client: MagicMock,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    coord = ComwattCoordinator(MagicMock(), entry)
    coord.data = {"devices": {"23593": {"power": None, "energy": None}}}
    coord._energy_state = {"23593": _EnergyState(total_wh=500.0)}

    scripted = iter([1000.0, 1036.0])
    with patch(
        "custom_components.comwatt.coordinator.monotonic",
        side_effect=lambda: next(scripted, 1036.0),
    ):
        coord.integrate_live_energy({"23593": 100.0})
        state = coord._energy_state["23593"]
        assert state.live_total_wh == 500.0
        assert state.last_power_w == 100.0

        coord.integrate_live_energy({"23593": 200.0})

    state = coord._energy_state["23593"]
    assert state.live_total_wh == 501.5
    assert coord.data["devices"]["23593"]["energy"] == 501.5


def test_process_batch_integrates_and_notifies_only_on_change(
    mock_comwatt_client: MagicMock,
) -> None:
    coordinator = MagicMock()
    coordinator.capacity_map = {"co.2.inst.3.sensor.3.data": ("23600", "CLAMP", False)}
    coordinator.data = {
        "devices": {"23600": {"power": None, "energy": None}},
        "switches": {},
    }
    coordinator.integrate_live_energy = MagicMock()
    coordinator.async_update_listeners = MagicMock()

    manager = ComwattStreamManager(MagicMock(), coordinator, "user", "pass")

    manager._process_batch(
        [
            _measurement(
                capacity_id="co.2.inst.3.sensor.3.data",
                measure_kind="FLOW",
                value_bool=None,
                value="161.0",
                value_float=161.0,
            )
        ]
    )
    coordinator.integrate_live_energy.assert_called_once_with({"23600": 161.0})
    coordinator.async_update_listeners.assert_called_once()

    coordinator.integrate_live_energy.reset_mock()
    coordinator.async_update_listeners.reset_mock()

    manager._process_batch(
        [
            _measurement(
                capacity_id="co.99.inst.0.sensor.0.data",
                measure_kind="FLOW",
                value_bool=None,
                value="42.0",
                value_float=42.0,
            )
        ]
    )
    coordinator.integrate_live_energy.assert_called_once_with({})
    coordinator.async_update_listeners.assert_not_called()


async def test_consumer_accumulates_live_energy_via_trapezoidal(
    hass: HomeAssistant, mock_comwatt_client: MagicMock
) -> None:
    stream_site = {
        "id": "site-1",
        "name": "Home",
        "siteKind": "RESIDENTIAL",
        "siteUid": "site-uid-1",
    }
    solar_device = {
        "id": "23593",
        "name": "Solar Inverter",
        "deviceKind": {"code": "SOLAR"},
    }
    mock_comwatt_client.get_sites.return_value = [stream_site]
    mock_comwatt_client.get_devices.return_value = [solar_device]
    mock_comwatt_client.get_device.return_value = solar_device
    mock_comwatt_client.get_site_time_series.return_value = {"autoproductionRates": []}
    mock_comwatt_client.get_device_ts_time_ago.return_value = {
        "values": [],
        "timestamps": [],
    }
    mock_comwatt_client.get_connected_objects.return_value = [
        {
            "capacities": [
                {
                    "capacityId": "co.1.inst.0.sensor.0.data",
                    "deviceId": 23593,
                    "nature": "CLAMP",
                    "production": True,
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

    scripted = iter([1000.0, 1036.0])
    with patch(
        "custom_components.comwatt.coordinator.monotonic",
        side_effect=lambda: next(scripted, 1036.0),
    ):
        manager._queue.put_nowait(
            _measurement(
                capacity_id="co.1.inst.0.sensor.0.data",
                measure_kind="FLOW",
                value_bool=None,
                value="100.0",
                value_float=100.0,
            )
        )
        for _ in range(20):
            await hass.async_block_till_done()
            if coordinator.data["devices"]["23593"]["power"] == 100.0:
                break
            await asyncio.sleep(0.05)

        manager._queue.put_nowait(
            _measurement(
                capacity_id="co.1.inst.0.sensor.0.data",
                measure_kind="FLOW",
                value_bool=None,
                value="200.0",
                value_float=200.0,
            )
        )
        for _ in range(20):
            await hass.async_block_till_done()
            if coordinator.data["devices"]["23593"]["power"] == 200.0:
                break
            await asyncio.sleep(0.05)

    assert coordinator.data["devices"]["23593"]["energy"] == 1.5

    await manager.async_stop()
    assert all(task.done() for task in manager._stream_tasks)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_integrate_live_energy_skips_delta_on_non_positive_dt(
    mock_comwatt_client: MagicMock,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, title=ENTRY_DATA["username"]
    )
    coord = ComwattCoordinator(MagicMock(), entry)
    coord.data = {"devices": {"23600": {"power": None, "energy": None}}}
    coord._energy_state = {}

    scripted = iter([1000.0, 1000.0])
    with patch(
        "custom_components.comwatt.coordinator.monotonic",
        side_effect=lambda: next(scripted, 1000.0),
    ):
        coord.integrate_live_energy({"23600": 100.0})
        state = coord._energy_state["23600"]
        assert state.live_total_wh == 0.0
        assert state.last_power_w == 100.0

        coord.integrate_live_energy({"23600": 200.0})

    assert state.live_total_wh == 0.0
    assert state.last_power_w == 200.0
    assert state.last_power_t == 1000.0
    assert state.live_by_hour == {}
