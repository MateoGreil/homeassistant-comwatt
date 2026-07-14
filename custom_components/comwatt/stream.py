"""WebSocket stream manager for real-time Comwatt measurements."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from comwatt_client import ComwattAuthError, ComwattClient, Measurement

from .coordinator import SWITCH_NATURE, ComwattCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _apply_switch_updates(
    batch: list[Any],
    capacity_map: dict[str, tuple[str, str, bool]],
    switches_data: dict[str, dict[str, Any]],
) -> bool:
    """Mutate ``switches_data`` with STATE switch updates from ``batch``.

    Returns whether any switch state changed. Non-STATE measurements, unmapped
    capacityIds, non-switch natures and devices without a switch entity are
    ignored. FLOW/QUANTITY and ``CapacityChanged`` messages are not handled
    here (later slices).
    """
    changed = False
    for msg in batch:
        if not isinstance(msg, Measurement):
            continue
        if msg.measure_kind != "STATE":
            continue
        route = capacity_map.get(msg.capacity_id)
        if route is None:
            continue
        device_id, nature, _production = route
        if nature not in SWITCH_NATURE:
            continue
        sw = switches_data.get(device_id)
        if sw is None:
            continue
        sw["is_on"] = msg.value_bool
        changed = True
    return changed


class ComwattStreamManager:
    """Owns the per-site WebSocket lifecycle and routes live measurements to the coordinator.

    The blocking ``stream_measurements`` generator runs in an executor thread
    per site; messages are pushed onto an ``asyncio.Queue`` thread-safely and
    drained on the event loop by a single consumer task that batches bursts and
    applies switch-state updates to the coordinator. The stream uses a dedicated
    ``ComwattClient`` so it never shares the coordinator's ``requests.Session``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ComwattCoordinator,
        username: str,
        password: str,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._username = username
        self._password = password
        self._client = ComwattClient()
        self._queue: asyncio.Queue | None = None
        self._consumer_task: asyncio.Task | None = None
        self._stream_tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def async_start(self) -> None:
        """Authenticate the dedicated client and start the consumer + per-site streams."""
        await self._hass.async_add_executor_job(
            self._client.authenticate, self._username, self._password
        )
        self._queue = asyncio.Queue()
        self._consumer_task = self._hass.async_create_background_task(
            self._consume(), "comwatt:stream:consumer"
        )
        for site in self._coordinator.sites:
            if "siteUid" in site and "id" in site:
                self._stream_tasks.append(
                    self._hass.async_create_background_task(
                        self._stream_site(site),
                        f"comwatt:stream:site:{site.get('id')}",
                    )
                )

    async def _stream_site(self, site: dict[str, Any]) -> None:
        """Run the blocking stream generator in an executor with async reconnect/backoff."""
        loop = asyncio.get_running_loop()
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._hass.async_add_executor_job(self._run_stream_once, site, loop)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except ComwattAuthError:
                _LOGGER.warning(
                    "stream auth error for site %s; stopping stream (poll will trigger reauth)",
                    site.get("id"),
                )
                break
            except Exception:
                _LOGGER.exception("stream error for site %s", site.get("id"))
            if self._stop.is_set():
                break
            await asyncio.sleep(min(backoff, 60.0))
            backoff = min(backoff * 2, 60.0)

    def _run_stream_once(self, site: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        """Drive one pass of the blocking generator in the executor thread."""
        queue = self._queue
        if queue is None:
            return
        for msg in self._client.stream_measurements(site, reconnect=False):
            loop.call_soon_threadsafe(queue.put_nowait, msg)

    async def _consume(self) -> None:
        """Drain burst batches from the queue and apply them to the coordinator."""
        while True:
            first = await self._queue.get()
            batch = [first]
            try:
                while True:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                self._process_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("failed to process stream batch")

    def _process_batch(self, batch: list[Any]) -> None:
        """Apply a drained batch to the coordinator, notifying on switch changes."""
        if _apply_switch_updates(
            batch,
            self._coordinator.capacity_map,
            self._coordinator.data["switches"],
        ):
            self._coordinator.async_update_listeners()

    async def async_stop(self) -> None:
        """Cancel all stream/consumer tasks and close the dedicated client.

        Idempotent and safe to call before ``async_start``.
        """
        self._stop.set()
        tasks: list[asyncio.Task] = []
        for task in self._stream_tasks:
            task.cancel()
            tasks.append(task)
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            tasks.append(self._consumer_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._hass.async_add_executor_job(self._client.close)
