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
**Currently, this integration work only for comwatt gen 4 (and probably more, but not tested yet). If you use energy.comwatt.com, it will probably work. If you use go.comwatt.com or any other, it will not work yet (<https://github.com/MateoGreil/homeassistant-comwatt/issues/7>).**

_[Lire en Français](README-fr.md)_

## Disclaimer

This Comwatt Integration for Home Assistant is not affiliated with, endorsed by, or in any way officially connected to Comwatt or its parent company. The integration is provided as-is and is not guaranteed to be suitable for any particular purpose. The use of this integration is at your own risk, and the author(s) of this integration will not be liable for any damages arising from the use or misuse of this integration.

## Installation

1. Install this integration using [HACS](https://hacs.xyz/) by [adding this repository](https://hacs.xyz/docs/faq/custom_repositories) or manually copy the files to your Home Assistant installation.
2. Restart Home Assistant to load the integration.

## Configuration

After installation, search and add component Comwatt in Home Assistant integrations page.

Or click [![Configuration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=comwatt)

## Usage

Once the integration is set up and configured, you will have access to sensors representing energy consumption and power consumption from your Comwatt devices. These entities can be added to your Home Assistant dashboard for monitoring and automation purposes.

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
