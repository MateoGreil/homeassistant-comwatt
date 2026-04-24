"""DataUpdateCoordinator for the Comwatt integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from comwatt_client import ComwattClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(minutes=2)
SWITCH_NATURE = ("POWER_SWITCH", "RELAY")


class _AuthError(Exception):
    """Raised internally when credentials are rejected by the API."""


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
        # Per-device accumulated energy totals: device_id -> (last_ts, total_wh)
        self._energy_state: dict[str, tuple[int, float]] = {}
        # Topology discovered on the most recent refresh; used by platform setup.
        self.sites: list[dict[str, Any]] = []
        self.sensor_devices: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.switch_devices: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch everything; re-auth once if the session has expired."""
        try:
            return await self.hass.async_add_executor_job(self._fetch_all)
        except _AuthError as err:
            # Re-auth failed outright — user intervention needed.
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as first_err:  # noqa: BLE001 - upstream client raises bare Exception
            _LOGGER.debug("First fetch failed (%s); re-authenticating and retrying", first_err)
            try:
                await self.hass.async_add_executor_job(self._authenticate)
                return await self.hass.async_add_executor_job(self._fetch_all)
            except _AuthError as auth_err:
                raise ConfigEntryAuthFailed(str(auth_err)) from auth_err
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(str(err)) from err

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

    def _authenticate(self) -> None:
        """Authenticate the shared client; raise `_AuthError` on credential failure."""
        try:
            self.client.authenticate(self._username, self._password)
        except Exception as err:  # noqa: BLE001 - upstream raises bare Exception
            message = str(err)
            # `ComwattClient.authenticate` raises "Authentication failed: <status>".
            # HTTP 401/403 means the credentials are wrong; anything else is
            # transient (network, 5xx) and should trigger a retry next cycle.
            if "401" in message or "403" in message:
                raise _AuthError(message) from err
            raise
        self._authenticated = True

    def _fetch_all(self) -> dict[str, Any]:
        """One full sync of the Comwatt account; called in the executor."""
        if not self._authenticated:
            self._authenticate()

        sites = self.client.get_sites()

        sites_data: dict[str, dict[str, Any]] = {}
        devices_data: dict[str, dict[str, Any]] = {}
        switches_data: dict[str, dict[str, Any]] = {}
        sensor_devices: list[tuple[dict[str, Any], dict[str, Any]]] = []
        switch_devices: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for site in sites:
            site_id = site.get("id")
            if site_id is None:
                continue

            try:
                site_ts = self.client.get_site_networks_ts_time_ago(
                    site_id, "FLOW", "NONE", None, "HOUR", 1
                )
            except Exception:  # noqa: BLE001
                rates: list[float] = []
            else:
                rates = site_ts.get("autoproductionRates") or []
            sites_data[site_id] = {
                "auto_production_rate": rates[-1] * 100 if rates else None,
            }

            for leaf in self._iter_leaf_devices(site):
                device_id = leaf["id"]
                sensor_devices.append((site, leaf))
                devices_data[device_id] = self._fetch_device_metrics(device_id)
                if self._has_switch_capacity(leaf):
                    switch_devices.append((site, leaf))
                    switches_data[device_id] = self._fetch_switch_state(leaf)

        self.sites = sites
        self.sensor_devices = sensor_devices
        self.switch_devices = switch_devices

        return {
            "sites": sites_data,
            "devices": devices_data,
            "switches": switches_data,
        }

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

    def _fetch_device_metrics(self, device_id: str) -> dict[str, float | None]:
        """Fetch latest power reading and update the running energy total."""
        power: float | None = None
        try:
            power_ts = self.client.get_device_ts_time_ago(
                device_id, "FLOW", "NONE", "NONE", "HOUR", 1
            )
        except Exception:  # noqa: BLE001
            pass
        else:
            values = power_ts.get("values") or []
            if values:
                power = values[-1]

        energy: float | None = None
        try:
            energy_ts = self.client.get_device_ts_time_ago(
                device_id, "QUANTITY", "HOUR", "NONE"
            )
        except Exception:  # noqa: BLE001
            pass
        else:
            timestamps = energy_ts.get("timestamps") or []
            values = energy_ts.get("values") or []
            last_ts, total = self._energy_state.get(device_id, (0, 0.0))
            if timestamps and values and timestamps[-1] != last_ts:
                total += values[-1]
                last_ts = timestamps[-1]
                self._energy_state[device_id] = (last_ts, total)
            energy = total if device_id in self._energy_state else None

        return {"power": power, "energy": energy}

    @staticmethod
    def _has_switch_capacity(device: dict[str, Any]) -> bool:
        for feature in device.get("features") or []:
            for capacity in feature.get("capacities") or []:
                if (capacity.get("capacity") or {}).get("nature") in SWITCH_NATURE:
                    return True
        return False

    def _fetch_switch_state(self, device: dict[str, Any]) -> dict[str, Any]:
        """Refresh the device to read current switch on/off state."""
        try:
            refreshed = self.client.get_device(device["id"])
        except Exception:  # noqa: BLE001
            refreshed = device

        is_on: bool | None = None
        capacity_id: str | None = None
        for feature in refreshed.get("features") or []:
            for capacity in feature.get("capacities") or []:
                cap = capacity.get("capacity") or {}
                if cap.get("nature") in SWITCH_NATURE:
                    is_on = cap.get("enable")
                    capacity_id = cap.get("id")
                    break
        return {"is_on": is_on, "capacity_id": capacity_id}
