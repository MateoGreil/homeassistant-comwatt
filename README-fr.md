[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacs_badge]][hacs]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

# Intégration Comwatt pour Home Assistant

Contrôlez votre système de surveillance énergétique Comwatt via l'API Comwatt avec Home Assistant.
**Actuellement, cette intégration fonctionne uniquement pour Comwatt gen 4 (et probablement plus, mais non testé encore). Si vous utilisez energy.comwatt.com, cela fonctionnera probablement. Si vous utilisez go.comwatt.com ou tout autre, cela ne fonctionnera pas encore (https://github.com/MateoGreil/homeassistant-comwatt/issues/7).**

*[Read in English](README.md)*

## Installation
1. Installez cette intégration en utilisant [HACS](https://hacs.xyz/) en [ajoutant ce dépôt](https://hacs.xyz/docs/faq/custom_repositories) ou en copiant manuellement les fichiers dans votre installation Home Assistant.
2. Redémarrez Home Assistant pour charger l'intégration.

## Configuration
Après l'installation, recherchez et ajoutez le composant Comwatt sur la page des intégrations de Home Assistant.

Ou cliquez sur [![Configuration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=comwatt)

## Utilisation
Une fois l'intégration configurée, vous aurez accès à des capteurs représentant la consommation d'énergie et la consommation électrique de vos appareils Comwatt. Ces entités peuvent être ajoutées à votre tableau de bord Home Assistant pour la surveillance et l'automatisation.

## Fonctionnalités

Cette intégration peut gérer :

- Consommation électrique de l'appareil
- Consommation énergétique de l'appareil
- Consommation électrique du réseau entrée/sortie
- Consommation énergétique du réseau entrée/sortie
- Capacité de commutation de l'appareil

## Contributions
Les contributions et les retours sont les bienvenus ! Si vous rencontrez des problèmes, avez des suggestions d'amélioration ou souhaitez contribuer avec de nouvelles fonctionnalités, veuillez ouvrir une issue ou soumettre une pull request sur le dépôt GitHub.

Si vous trouvez cette intégration utile, envisagez de soutenir le développement en ajoutant une étoile !

[homeassistant-comwatt]: https://github.com/mateogreil/homeassistant-comwatt
[buymecoffee]: https://dons.restosducoeur.org/particulier/~mon-don?don=5
[buymecoffeebadge]: https://img.shields.io/badge/Buy%20me%20a%20beer-%245-orange?style=for-the-badge&logo=buy-me-a-beer
[commits-shield]: https://img.shields.io/github/commit-activity/y/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[commits]: https://github.com/mateogreil/homeassistant-comwatt/commits/master
[hacs]: https://github.com/custom-components/hacs
[hacs_badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://forum.hacf.fr/
[license-shield]: https://img.shields.io/github/license/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-Mateo%20Greil%20%40mateogreil-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[releases]: https://github.com/mateogreil/homeassistant-comwatt/releases
