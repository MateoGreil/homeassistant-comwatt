# Sensor reference

This page documents what the Comwatt integration exposes, in which unit, and how fresh each value is. It mirrors the actual behavior of the Comwatt API — if the code and this page ever disagree, that's a bug.

The two key distinctions to keep in mind:

- **Power** sensors (site `Production`, device `Power`, …) are **instantaneous power in watts (W)**. They tell you how fast energy flows *right now*.
- **`*_total_energy`** sensors are **cumulative energy in watt-hours (Wh)**. They tell you how much energy has flowed in total. These are the ones to use in the **Energy dashboard**.

_[Lire en Français](sensors-fr.md)_

## Site sensors

Each site gets one device with the following sensors.

| Sensor | Unit | What it measures | Update cadence |
|---|---|---|---|
| Production | W | Instantaneous site production | ~every 2 min |
| Consumption | W | Instantaneous site consumption | ~every 2 min |
| Injection | W | Instantaneous power injected to the grid | ~every 2 min |
| Withdrawal | W | Instantaneous power withdrawn from the grid | ~every 2 min |
| Charge | W | Instantaneous battery charge power | ~every 2 min |
| Discharge | W | Instantaneous battery discharge power | ~every 2 min |
| Auto Production Rate | % | Self-sufficiency ratio reported by the Comwatt API | ~every 2 min |
| Auto Consumption Rate | % | Self-consumption ratio reported by the Comwatt API | ~every 2 min |
| Injection Rate | % | Grid-injection ratio reported by the Comwatt API | ~every 2 min |
| Withdrawal Rate | % | Grid-withdrawal ratio reported by the Comwatt API | ~every 2 min |
| Production Total Energy | Wh | Cumulative production | hourly steps |
| Consumption Total Energy | Wh | Cumulative consumption | hourly steps |
| Injection Total Energy | Wh | Cumulative grid injection | hourly steps |
| Withdrawal Total Energy | Wh | Cumulative grid withdrawal | hourly steps |
| Charge Total Energy | Wh | Cumulative battery charge | hourly steps |
| Discharge Total Energy | Wh | Cumulative battery discharge | hourly steps |

### Site power (W): a ~2-minute FLOW series

The site power sensors are **not** energy deltas. They come from the REST `FLOW` time series (`get_site_time_series(..., "FLOW", ...)`) sampled by the Comwatt backend roughly every two minutes; the integration polls every ~2 minutes and publishes the latest sample. Expect small stair-steps, not real-time curves.

### Site total energy (Wh): hourly steps, by design

The site `*_total_energy` sensors are cumulative counters driven **exclusively** by the official REST `QUANTITY/HOUR` buckets (energy per completed hour). The Comwatt backend only publishes a bucket once an hour has completed, so these totals **advance in hourly steps** — that is the API's cadence, not a bug. On the first run the counters are seeded with about 8 days of official history so the Energy dashboard immediately shows data, and the totals are persisted so they survive restarts.

## Device sensors

Each meter / appliance reported by the Comwatt box gets a device with:

| Sensor | Unit | What it measures | Update cadence |
|---|---|---|---|
| Power | W | Instantaneous device power | real time (WebSocket) |
| Total Energy | Wh | Cumulative device energy | real time, reconciled hourly |

Devices that expose a `POWER_SWITCH` / `RELAY` capacity also get a **Switch** entity (remotely controllable plugs, relays…), updated in real time through the WebSocket stream.

### Device power (W): real time via WebSocket

Per-device `Power` sensors are updated in real time from the WebSocket measurement stream (`FLOW` messages). Multi-phase devices push one measurement per phase; the integration sums them into a single instantaneous power value per device.

### Device total energy (Wh): live accumulation + hourly reconciliation

The device `Total Energy` sensors combine two sources:

1. **Live accumulation** — every power burst from the WebSocket stream is integrated (∫W·dt) into the running total, so the sensor advances in real time between polls.
2. **Hourly reconciliation** — roughly once per hour, the integration fetches the official `QUANTITY/HOUR` buckets from the REST API and corrects the live total for each completed hour, so drift from missed stream samples stays bounded. (The Comwatt API returns these buckets in mixed units — Wh for some devices, kWh for others — so the integration infers the unit by comparing each bucket against the live measurement.)

Two consequences worth knowing:

- The device total **starts from zero when the integration is installed** — unlike the site totals, it is not backfilled with history; it counts from the moment the stream is live.
- The running total is persisted in Home Assistant storage, so it **survives restarts**.

## WebSocket stream: capabilities and limits

The integration keeps one WebSocket stream per site to get real-time measurements. The stream sends:

- `FLOW` messages — instantaneous power, routed to the device `Power` sensors and the live energy accumulator;
- `STATE` messages — on/off state, routed to the `Switch` entities.

It does **not** send `QUANTITY` (energy) messages — the Comwatt WebSocket simply doesn't provide them. All energy data therefore comes from the REST API (hourly buckets), either directly (site totals) or through reconciliation (device totals). The stream reconnects automatically with a backoff if the connection drops.

## Energy dashboard

Use the **`*_total_energy`** entities (site and device) in the **Energy dashboard** (Settings → Dashboards → Energy). They carry the right metadata (`device_class: energy`, `state_class: total_increasing`) for Home Assistant to build long-term statistics.

Do **not** use the power entities (W) in the Energy dashboard: they are instantaneous measurements, not energy. If you need site-level energy that advances more often than hourly, use the per-device `Total Energy` sensors (real time) — the site `*_total_energy` sensors will always step hourly because that is how the Comwatt backend publishes energy.
