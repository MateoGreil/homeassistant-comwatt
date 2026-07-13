"""DataUpdateCoordinator for the Comwatt integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any

from comwatt_client import ComwattAuthError, ComwattClient

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_add_external_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import slugify
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN

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


@dataclass
class _NewEnergyBucket:
    """One hourly API bucket that hasn't been pushed to statistics yet."""

    device_id: str
    device_name: str
    bucket_ts: datetime
    delta_wh: float  # Wh consumed in this hour — the per-period `state` for statistics
    cumulative_wh: float  # running total — the `sum` for statistics


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
        # New hourly buckets staged by _fetch_all (executor) for the
        # event-loop-side statistics push; replaced on every refresh.
        self._pending_energy_buckets: list[_NewEnergyBucket] = []
        # Topology discovered on the most recent refresh; used by platform setup.
        self.sites: list[dict[str, Any]] = []
        self.sensor_devices: list[dict[str, Any]] = []
        self.switch_devices: list[dict[str, Any]] = []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch everything; the client re-authenticates expired sessions itself."""
        try:
            data = await self.hass.async_add_executor_job(self._fetch_all)
        except ComwattAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

        # Push any new hourly buckets to long-term statistics at their real
        # timestamps, not "now", so the Energy dashboard attributes them to
        # the correct hour (closes issues #5 and #42). _fetch_all stages them
        # on self._pending_energy_buckets; drain them here so a subsequent
        # refresh can't re-push stale buckets.
        buckets = self._pending_energy_buckets
        self._pending_energy_buckets = []
        self._async_push_energy_statistics(buckets)
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
    # Statistics emission (called on the event loop after each refresh)
    # ------------------------------------------------------------------

    def _async_push_energy_statistics(
        self, buckets: list[_NewEnergyBucket]
    ) -> None:
        """Forward new hourly energy buckets to the recorder.

        Uses external statistics (source = `comwatt`) so it coexists with
        whatever the `TOTAL_INCREASING` sensor's own state tracking produces,
        rather than overwriting it. Users who want energy attributed to the
        real hour can add the `comwatt:<device_id>_total_energy` statistic to
        the Energy dashboard; existing users keep their current sensor-based
        dashboard working unchanged.
        """
        if not buckets or "recorder" not in self.hass.config.components:
            # No data to push, or HA is running without the recorder (rare,
            # but we shouldn't crash the poll).
            return
        by_device: dict[str, list[_NewEnergyBucket]] = {}
        for bucket in buckets:
            by_device.setdefault(bucket.device_id, []).append(bucket)
        for device_id, device_buckets in by_device.items():
            name = device_buckets[0].device_name
            statistic_id = f"{DOMAIN}:{slugify(str(device_id))}_total_energy"
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"{name} Total Energy",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement="Wh",
            )
            stats = [
                StatisticData(
                    start=b.bucket_ts.replace(minute=0, second=0, microsecond=0),
                    state=b.delta_wh,
                    sum=b.cumulative_wh,
                )
                for b in device_buckets
            ]
            async_add_external_statistics(self.hass, metadata, stats)

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
        new_energy_buckets: list[_NewEnergyBucket] = []

        for site in sites:
            site_id = site.get("id")
            if site_id is None:
                continue

            site_ts = self._try_fetch(
                self.client.get_site_time_series,
                site_id, "FLOW", "NONE", None, "HOUR", 1,
            )
            sites_data[site_id] = self._extract_site_metrics(site_ts or {})

            for leaf in self._iter_leaf_devices(site):
                device_id = leaf["id"]
                sensor_devices.append(leaf)
                metrics, device_new_buckets = self._fetch_device_metrics(leaf)
                devices_data[device_id] = metrics
                new_energy_buckets.extend(device_new_buckets)
                if self._find_switch_capacity(leaf) is not None:
                    switch_devices.append(leaf)
                    switches_data[device_id] = self._fetch_switch_state(leaf)

        self.sites = sites
        self.sensor_devices = sensor_devices
        self.switch_devices = switch_devices
        self._pending_energy_buckets = new_energy_buckets

        return {
            "sites": sites_data,
            "devices": devices_data,
            "switches": switches_data,
        }

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

    def _fetch_device_metrics(
        self, device: dict[str, Any]
    ) -> tuple[dict[str, float | None], list[_NewEnergyBucket]]:
        """Fetch latest power + (optionally) new hourly energy buckets.

        Returns the `{"power": ..., "energy": ...}` payload for `coordinator.data`
        **and** a list of any new hourly buckets that should be pushed to
        long-term statistics.
        """
        device_id = device["id"]
        power_ts = self._try_fetch(
            self.client.get_device_ts_time_ago,
            device_id, "FLOW", "NONE", "NONE", "HOUR", 1,
        )
        values = (power_ts or {}).get("values") or []
        power = values[-1] if values else None

        state = self._energy_state.setdefault(device_id, _EnergyState())
        energy: float | None = state.total_wh if state.last_fetched_at is not None else None
        new_buckets: list[_NewEnergyBucket] = []

        now = time.monotonic()
        # Skip the QUANTITY call if we already fetched recently — the API
        # only changes hourly and each call is cached in `total_wh`.
        if state.last_fetched_at is None or now - state.last_fetched_at >= ENERGY_MIN_FETCH_INTERVAL_S:
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
                    state.total_wh += val
                    state.last_bucket_ts = bucket_dt
                    new_buckets.append(
                        _NewEnergyBucket(
                            device_id=device_id,
                            device_name=device.get("name", device_id),
                            bucket_ts=bucket_dt,
                            delta_wh=val,
                            cumulative_wh=state.total_wh,
                        )
                    )
                state.last_fetched_at = now
                energy = state.total_wh

        return {"power": power, "energy": energy}, new_buckets

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
