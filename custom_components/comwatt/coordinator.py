"""DataUpdateCoordinator for the Comwatt integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from time import monotonic
from typing import TYPE_CHECKING, Any

from comwatt_client import ComwattAuthError, ComwattClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

if TYPE_CHECKING:
    from .stream import ComwattStreamManager

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=2)
# The Comwatt QUANTITY/HOUR endpoint only publishes a new bucket once per hour,
# so polling every 2 min is 29 wasted calls per hour per device. Skip the call
# when the last successful fetch is younger than this. 55 min (not 60) gives
# one-poll slack so we still see the new bucket shortly after it appears.
ENERGY_MIN_FETCH_INTERVAL_S = 55 * 60
SWITCH_NATURE = ("POWER_SWITCH", "RELAY")

# API key in `get_site_time_series()["<key>"]` → our internal key.
# Rate fields are 0-1 ratios (multiplied by 100 downstream to render as %);
# the rest are Wh deltas for the last hour bucket.
SITE_TIME_SERIES_KEYS: dict[str, str] = {
    "productions": "production",
    "consumptions": "consumption",
    "injections": "injection",
    "withdrawals": "withdrawal",
    "charges": "charge",
    "discharges": "discharge",
    "autoproductionRates": "auto_production_rate",
    "autoconsumptionRates": "auto_consumption_rate",
    "injectionRates": "injection_rate",
    "withdrawalRates": "withdrawal_rate",
}


@dataclass
class _EnergyState:
    """Per-device bookkeeping for the hourly energy accumulator."""

    last_fetched_at: float | None = None  # monotonic clock of the last successful fetch
    last_bucket_ts: datetime | None = None  # highest API timestamp folded in, as UTC datetime
    total_wh: float = 0.0  # cumulative Wh since this coordinator was instantiated
    last_power_w: float | None = None
    last_power_t: float | None = None
    live_total_wh: float | None = None
    live_by_hour: dict[datetime, float] = field(default_factory=dict)


def _parse_bucket_ts(ts: Any) -> datetime | None:
    """Convert a Comwatt API timestamp to a UTC datetime.

    The `aggregations/time-series` endpoint can return timestamps as ISO 8601
    strings (e.g. ``2026-04-29T10:00:00.000+0000``) or as numeric epoch values.
    Returns `None` for unparseable values so the caller can skip the bucket.
    """
    if isinstance(ts, bool):  # bool is a subclass of int — exclude
        return None
    if isinstance(ts, (int, float)):
        # Heuristic: anything past year 3000 in seconds must really be ms.
        if ts > 32503680000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        # Numeric string — fall back to the int/float branch.
        try:
            return _parse_bucket_ts(int(s))
        except ValueError:
            pass
        # Normalise "Z" and "+0000"/"-0000" → "+00:00".
        s = s.replace("Z", "+00:00")
        if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
            s = s[:-2] + ":" + s[-2:]
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


class ComwattCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches all Comwatt data used by the integration in one periodic cycle.

    Holds a single long-lived `ComwattClient`, runs every HTTP call in an
    executor, and returns a dict shaped as::

        {
            "sites": {site_id: {"auto_production_rate": float|None}},
            "devices": {device_id: {"power": float|None, "energy": float|None}},
            "switches": {device_id: {"is_on": bool|None, "capacity_id": str|None}},
        }

    Also exposes `sites`, `sensor_devices` and `switch_devices` for platform
    setup to know which entities to instantiate after the first refresh.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = ComwattClient()
        self._username: str = entry.data["username"]
        self._password: str = entry.data["password"]
        self._authenticated = False
        # Per-device accumulated energy state.
        self._energy_state: dict[str, _EnergyState] = {}
        # Topology discovered on the most recent refresh; used by platform setup.
        self.sites: list[dict[str, Any]] = []
        self.sensor_devices: list[dict[str, Any]] = []
        self.switch_devices: list[dict[str, Any]] = []
        self.capacity_map: dict[str, tuple[Any, str, bool]] = {}
        self.stream_manager: ComwattStreamManager | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch everything; the client re-authenticates expired sessions itself."""
        try:
            data = await self.hass.async_add_executor_job(self._fetch_all)
        except ComwattAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

        return data

    # ------------------------------------------------------------------
    # Actions triggered by entities (executor-bound)
    # ------------------------------------------------------------------

    async def async_set_switch(self, capacity_id: str, on: bool) -> None:
        """Toggle a switch capacity and request a refresh of coordinator data."""
        await self.hass.async_add_executor_job(
            self.client.switch_capacity, capacity_id, on
        )
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Internal executor-side helpers
    # ------------------------------------------------------------------

    def _fetch_all(self) -> dict[str, Any]:
        """One full sync of the Comwatt account; called in the executor."""
        if not self._authenticated:
            self.client.authenticate(self._username, self._password)
            self._authenticated = True

        sites = self.client.get_sites()

        sites_data: dict[str, dict[str, Any]] = {}
        devices_data: dict[str, dict[str, Any]] = {}
        switches_data: dict[str, dict[str, Any]] = {}
        sensor_devices: list[dict[str, Any]] = []
        switch_devices: list[dict[str, Any]] = []
        self.capacity_map = {}

        for site in sites:
            site_id = site.get("id")
            if site_id is None:
                continue

            site_ts = self._try_fetch(
                self.client.get_site_time_series,
                site_id, "FLOW", "NONE", None, "HOUR", 1,
            )
            sites_data[site_id] = self._extract_site_metrics(site_ts or {})

            connected_objects = self._try_fetch(self.client.get_connected_objects, site_id)
            self._fold_capacity_map(connected_objects or [])

            for leaf in self._iter_leaf_devices(site):
                device_id = leaf["id"]
                sensor_devices.append(leaf)
                devices_data[device_id] = self._fetch_device_metrics(leaf)
                if self._find_switch_capacity(leaf) is not None:
                    switch_devices.append(leaf)
                    switches_data[device_id] = self._fetch_switch_state(leaf)

        self.sites = sites
        self.sensor_devices = sensor_devices
        self.switch_devices = switch_devices

        return {
            "sites": sites_data,
            "devices": devices_data,
            "switches": switches_data,
        }

    def _fold_capacity_map(self, connected_objects: list[dict[str, Any]]) -> None:
        """Add every mappable capacity from `connected_objects` to `self.capacity_map`."""
        for connected_object in connected_objects:
            for cap in connected_object.get("capacities") or []:
                capacity_id = cap.get("capacityId")
                if not capacity_id:
                    continue
                device_id = cap.get("deviceId")
                if not device_id:
                    continue
                nature = cap.get("nature")
                if nature is None:
                    continue
                self.capacity_map[capacity_id] = (
                    device_id,
                    nature,
                    bool(cap.get("production", False)),
                )

    @staticmethod
    def _extract_site_metrics(site_ts: dict[str, Any]) -> dict[str, float | None]:
        """Pull the latest bucket for every known site metric."""
        metrics: dict[str, float | None] = {}
        for api_key, internal_key in SITE_TIME_SERIES_KEYS.items():
            series = site_ts.get(api_key) or []
            if not series:
                metrics[internal_key] = None
                continue
            value = series[-1]
            if value is None:
                metrics[internal_key] = None
                continue
            metrics[internal_key] = value
        return metrics

    def _try_fetch(self, fn: Any, *args: Any) -> Any:
        """Call `fn(*args)`; re-raise auth errors, return None on other failure."""
        try:
            return fn(*args)
        except ComwattAuthError:
            raise
        except Exception:  # noqa: BLE001
            return None

    def _iter_leaf_devices(self, site: dict[str, Any]):
        """Yield each leaf device for a site.

        A device with a non-empty `partChilds` list is a container; its children
        are the leaves. Otherwise the device itself is the leaf.
        """
        devices = self.client.get_devices(site["id"])
        for device in devices:
            if "id" not in device:
                continue
            children = device.get("partChilds") or []
            if children:
                for child in children:
                    if "id" in child:
                        yield child
            else:
                yield device

    def integrate_live_energy(self, device_powers: dict[str, float]) -> None:
        """Accumulate trapezoidal ∫W·dt into each device's live energy total.

        Called by the stream manager after it computes per-device power for a
        burst. Seeds `live_total_wh` from the poll's `total_wh` on the first
        burst, then accumulates. Also writes the live total into
        `self.data["devices"][id]["energy"]` and buckets the delta by UTC hour
        for Slice 5 reconciliation.
        """
        for device_id, power_w in device_powers.items():
            state = self._energy_state.setdefault(device_id, _EnergyState())
            now_mono = monotonic()
            now_utc = datetime.now(UTC)
            if state.live_total_wh is None:
                state.live_total_wh = state.total_wh
            if state.last_power_w is not None and state.last_power_t is not None:
                dt_h = (now_mono - state.last_power_t) / 3600.0
                if dt_h > 0:
                    delta_wh = (power_w + state.last_power_w) / 2.0 * dt_h
                    state.live_total_wh += delta_wh
                    hour = now_utc.replace(minute=0, second=0, microsecond=0)
                    state.live_by_hour[hour] = state.live_by_hour.get(hour, 0.0) + delta_wh
            state.last_power_w = power_w
            state.last_power_t = now_mono
            dev = self.data.get("devices", {}).get(device_id)
            if dev is not None:
                dev["energy"] = state.live_total_wh

    def _fetch_device_metrics(self, device: dict[str, Any]) -> dict[str, float | None]:
        """Fetch latest power reading and update the running energy total.

        When the stream owns the live total (`live_total_wh is not None`), each
        new server QUANTITY/HOUR bucket RECONCILES that total instead of being
        accumulated: the server's authoritative Wh for a completed hour
        corrects whatever the live accumulator measured for the same hour, so
        drift from missed stream samples stays bounded. When the stream has not
        taken over (`live_total_wh is None`), buckets accumulate into `total_wh`
        as before.

        Bucket-labeling assumption: the server's `bucket_dt` (from
        `_parse_bucket_ts`) is the START of the hour it represents. The live
        accumulator (`integrate_live_energy`) buckets each sample under the
        sample timestamp's UTC hour, so a sample at 10:30 lands in the 10:00
        hour — the same key as a server bucket labeled 10:00. Reconciliation
        therefore keys the server bucket directly by
        `bucket_dt.replace(minute=0, second=0, microsecond=0)`. If real data
        proves the server labels by the END of the hour, the fix is
        `hour = bucket_dt - timedelta(hours=1)`; the architecture is correct
        either way.

        The QUANTITY/HOUR call is skipped while the last successful fetch is
        younger than `ENERGY_MIN_FETCH_INTERVAL_S`, since the API only publishes
        a new hourly bucket once per hour (issue #3).
        """
        device_id = device["id"]
        power_ts = self._try_fetch(
            self.client.get_device_ts_time_ago,
            device_id, "FLOW", "NONE", "NONE", "HOUR", 1,
        )
        values = (power_ts or {}).get("values") or []
        power = values[-1] if values else None

        state = self._energy_state.setdefault(device_id, _EnergyState())
        live_total = state.live_total_wh
        energy: float | None = (
            live_total
            if live_total is not None
            else (state.total_wh if state.last_fetched_at is not None else None)
        )

        now = monotonic()
        if state.last_fetched_at is None or now - state.last_fetched_at >= ENERGY_MIN_FETCH_INTERVAL_S:
            initial_live = live_total
            energy_ts = self._try_fetch(
                self.client.get_device_ts_time_ago,
                device_id, "QUANTITY", "HOUR", "NONE",
            )
            if energy_ts is not None:
                timestamps = energy_ts.get("timestamps") or []
                values = energy_ts.get("values") or []
                for ts, val in zip(timestamps, values):
                    if val is None:
                        continue
                    bucket_dt = _parse_bucket_ts(ts)
                    if bucket_dt is None:
                        _LOGGER.debug(
                            "Skipping unparseable energy timestamp %r for device %s",
                            ts,
                            device_id,
                        )
                        continue
                    if (
                        state.last_bucket_ts is not None
                        and bucket_dt <= state.last_bucket_ts
                    ):
                        continue
                    if live_total is None:
                        state.total_wh += val
                    else:
                        hour = bucket_dt.replace(minute=0, second=0, microsecond=0)
                        live_total += val - state.live_by_hour.get(hour, 0.0)
                        state.live_by_hour[hour] = val
                    state.last_bucket_ts = bucket_dt
                state.last_fetched_at = now
                if live_total is None:
                    energy = state.total_wh
                else:
                    state.live_total_wh += live_total - initial_live
                    energy = state.live_total_wh
                    if state.last_bucket_ts is not None:
                        hwm_hour = state.last_bucket_ts.replace(minute=0, second=0, microsecond=0)
                        for stale_hour in list(state.live_by_hour):
                            if stale_hour < hwm_hour:
                                state.live_by_hour.pop(stale_hour, None)

        return {"power": power, "energy": energy}

    @staticmethod
    def _find_switch_capacity(device: dict[str, Any]) -> dict[str, Any] | None:
        """Return the first switch-capacity object (nature in SWITCH_NATURE)."""
        for feature in device.get("features") or []:
            for capacity in feature.get("capacities") or []:
                cap = capacity.get("capacity") or {}
                if cap.get("nature") in SWITCH_NATURE:
                    return cap
        return None

    def _fetch_switch_state(self, device: dict[str, Any]) -> dict[str, Any]:
        """Refresh the device to read current switch on/off state."""
        refreshed = self._try_fetch(self.client.get_device, device["id"])
        if refreshed is None:
            refreshed = device

        cap = self._find_switch_capacity(refreshed)
        if cap is None:
            return {"is_on": None, "capacity_id": None}
        return {"is_on": cap.get("enable"), "capacity_id": cap.get("id")}
