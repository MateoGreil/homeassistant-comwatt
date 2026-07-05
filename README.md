[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacs_badge]][hacs]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

<div>
  <img src="https://github.com/user-attachments/assets/8fabed32-502a-4868-9b34-be133bf0748e" alt="Comwatt Logo" height="100" style="display: inline-block;">
  <img src="https://github.com/user-attachments/assets/30fc117e-b64d-47f5-9da9-1d92f868c352" alt="HomeAssistant Logo" height="100" style="display: inline-block;">
</div>

# Comwatt Integration for Home Assistant

Control your Comwatt energy monitoring system via the Comwatt API with Home Assistant.
This integration work only with Comwatt Gen 4 (energy.comwatt.com). If you use go.comwatt.com, you need to use the [ComwattIndepbox integration](https://github.com/ZoomeoTooknor/comwatt_indepbox)

_[Lire en Français](README-fr.md)_

## Disclaimer

This Comwatt Integration for Home Assistant is not affiliated with, endorsed by, or in any way officially connected to Comwatt or its parent company. The integration is provided as-is and is not guaranteed to be suitable for any particular purpose. The use of this integration is at your own risk, and the author(s) of this integration will not be liable for any damages arising from the use or misuse of this integration.

## Prerequisites

- A working Comwatt account at [energy.comwatt.com](https://energy.comwatt.com) (Comwatt Gen 4). Log in there first to confirm your credentials work and that your devices appear.
- Home Assistant 2024.10 or newer.
- [HACS](https://hacs.xyz/) installed in Home Assistant (recommended for updates).

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant (left sidebar).
2. Go to **Integrations**, click the three-dot menu in the top-right, choose **Custom repositories**.
3. Paste `https://github.com/MateoGreil/homeassistant-comwatt` in the **Repository** field, pick **Integration** as the category, click **Add**.
4. Back on the Integrations list, search for **Comwatt** and click **Download**.
5. Restart Home Assistant (Settings → System → Restart).

### Manual installation

1. Download the latest release archive from [the Releases page](https://github.com/MateoGreil/homeassistant-comwatt/releases).
2. Extract it and copy the `custom_components/comwatt/` folder into your Home Assistant configuration directory (for example `/config/custom_components/comwatt/`).
3. Restart Home Assistant.

## Configuration

1. In Home Assistant, go to **Settings → Devices & services → + Add integration**, search for **Comwatt** and click it.
   *Or* click this shortcut: [![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=comwatt)
2. Enter your Comwatt credentials:
   - **Username**: the email address you use to log in at [energy.comwatt.com](https://energy.comwatt.com).
   - **Password**: the password of that Comwatt account (**not** a Home Assistant password).
3. Click **Submit**. After a few seconds you should see a success message and a new **Comwatt** device card.

### Verifying it works

Open **Settings → Devices & services → Comwatt → *your site*** — you should see:

- One device per site, with sensors for auto-production rate, production, consumption, injection, withdrawal…
- One device per meter / appliance reported by the Comwatt box, with **Power** and **Total Energy** sensors.
- A **Switch** entity for any device that exposes a `POWER_SWITCH` / `RELAY` capacity (remotely controllable plugs, relays…).

If sensors show `unavailable` for more than a few minutes, check the Home Assistant log (Settings → System → Logs, filter on *comwatt*). Common causes: wrong credentials (the integration will prompt for a re-auth), Comwatt backend outage, or the account belongs to the older `go.comwatt.com` (Gen 3) platform — that one needs the [ComwattIndepbox integration](https://github.com/ZoomeoTooknor/comwatt_indepbox) instead.

## Usage

Once set up, you have sensors for energy and power per device you can add to your dashboard or use in automations. The `*_total_energy` sensors can be used in the **Energy dashboard** (Settings → Dashboards → Energy).

## Features

This integration can handle :

- Power consumption of device
- Energy consumption of device
- Power consumption of network in/out
- Energy consumption of network in/out
- Switch capacity of device

## Contributions

Contributions and feedback are welcome! If you encounter any issues, have suggestions for improvement, or would like to contribute new features, please open an issue or submit a pull request on the GitHub repository.

If you find this integration useful, consider supporting the development by adding a star !

[buymecoffee]: https://dons.restosducoeur.org/particulier/~mon-don?don=5
[buymecoffeebadge]: https://img.shields.io/badge/Buy%20him%20a%20coffee-%245-orange?style=for-the-badge&logo=buy-him-a-coffee
[commits-shield]: https://img.shields.io/github/commit-activity/y/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[commits]: https://github.com/mateogreil/homeassistant-comwatt/commits/master
[hacs]: https://github.com/custom-components/hacs
[hacs_badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[releases]: https://github.com/mateogreil/homeassistant-comwatt/releases
