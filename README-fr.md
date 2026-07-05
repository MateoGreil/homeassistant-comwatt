[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
[![hacs][hacs_badge]][hacs]
[![BuyMeCoffee][buymecoffeebadge]][buymecoffee]

<div>
  <img src="https://github.com/user-attachments/assets/8fabed32-502a-4868-9b34-be133bf0748e" alt="Comwatt Logo" height="100" style="display: inline-block;">
  <img src="https://github.com/user-attachments/assets/30fc117e-b64d-47f5-9da9-1d92f868c352" alt="HomeAssistant Logo" height="100" style="display: inline-block;">
</div>

# Intégration Comwatt pour Home Assistant

Contrôlez votre système de surveillance énergétique Comwatt via l'API Comwatt avec Home Assistant.
Cette intégration ne fonctionne qu'avec Comwatt Gen 4 (energy.comwatt.com). Si vous utilisez go.comwatt.com, vous devez utiliser l'intégration [ComwattIndepbox](https://github.com/ZoomeoTooknor/comwatt_indepbox).

_[Read in English](README.md)_

## Avertissement

Cette intégration Comwatt pour Home Assistant n'est pas affiliée, approuvée ou officiellement connectée de quelque manière que ce soit à Comwatt ou à sa société mère. L'intégration est fournie telle quelle et n'est pas garantie d'être adaptée à un usage particulier. L'utilisation de cette intégration se fait à vos propres risques, et le(s) auteur(s) de cette intégration ne seront pas responsables des dommages résultant de l'utilisation ou de la mauvaise utilisation de cette intégration.

## Pré-requis

- Un compte Comwatt fonctionnel sur [energy.comwatt.com](https://energy.comwatt.com) (Comwatt Gen 4). Connectez-vous d'abord sur ce site pour vérifier que vos identifiants marchent et que vos appareils apparaissent.
- Home Assistant 2024.10 ou plus récent.
- [HACS](https://hacs.xyz/) installé dans Home Assistant (recommandé pour les mises à jour).

## Installation

### Via HACS (recommandé)

1. Ouvrez HACS dans Home Assistant (barre latérale gauche).
2. Allez dans **Intégrations**, cliquez sur le menu à trois points en haut à droite, choisissez **Dépôts personnalisés**.
3. Collez `https://github.com/MateoGreil/homeassistant-comwatt` dans le champ **Dépôt**, choisissez **Intégration** comme catégorie, puis **Ajouter**.
4. De retour dans la liste des intégrations, cherchez **Comwatt** et cliquez sur **Télécharger**.
5. Redémarrez Home Assistant (Paramètres → Système → Redémarrer).

### Installation manuelle

1. Téléchargez la dernière archive depuis [la page des Releases](https://github.com/MateoGreil/homeassistant-comwatt/releases).
2. Extrayez-la et copiez le dossier `custom_components/comwatt/` dans le répertoire de configuration de Home Assistant (par exemple `/config/custom_components/comwatt/`).
3. Redémarrez Home Assistant.

## Configuration

1. Dans Home Assistant, allez dans **Paramètres → Appareils & services → + Ajouter une intégration**, cherchez **Comwatt** et cliquez dessus.
   *Ou* cliquez sur ce raccourci : [![Ajouter l'intégration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=comwatt)
2. Saisissez vos identifiants Comwatt :
   - **Nom d'utilisateur** : l'adresse e-mail que vous utilisez pour vous connecter à [energy.comwatt.com](https://energy.comwatt.com).
   - **Mot de passe** : le mot de passe de ce compte Comwatt (**pas** un mot de passe Home Assistant).
3. Cliquez sur **Valider**. Quelques secondes plus tard, vous devriez voir un message de succès et une nouvelle carte **Comwatt**.

### Vérification

Ouvrez **Paramètres → Appareils & services → Comwatt → *votre site*** — vous devriez voir :

- Un appareil par site, avec des capteurs pour le taux d'autoproduction, la production, la consommation, l'injection, le soutirage…
- Un appareil par compteur / équipement remonté par la box Comwatt, avec les capteurs **Power** et **Total Energy**.
- Un interrupteur (**Switch**) pour tout appareil exposant une capacité `POWER_SWITCH` / `RELAY` (prises commandables, relais…).

Si des capteurs restent `unavailable` plus de quelques minutes, consultez le journal Home Assistant (Paramètres → Système → Journaux, filtrez sur *comwatt*). Causes classiques : mauvais identifiants (l'intégration proposera de les ressaisir), panne chez Comwatt, ou le compte dépend de l'ancienne plateforme `go.comwatt.com` (Gen 3) — dans ce cas il faut l'intégration [ComwattIndepbox](https://github.com/ZoomeoTooknor/comwatt_indepbox).

## Utilisation

Une fois configurée, vous disposez de capteurs d'énergie et de puissance par appareil, utilisables dans votre tableau de bord ou dans des automatisations. Les capteurs `*_total_energy` s'ajoutent au **tableau de bord Énergie** (Paramètres → Tableaux de bord → Énergie).

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

[buymecoffee]: https://dons.restosducoeur.org/particulier/~mon-don?don=5
[buymecoffeebadge]: https://img.shields.io/badge/Buy%20him%20a%20coffee-%245-orange?style=for-the-badge&logo=buy-him-a-coffee
[commits-shield]: https://img.shields.io/github/commit-activity/y/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[commits]: https://github.com/mateogreil/homeassistant-comwatt/commits/master
[hacs]: https://github.com/custom-components/hacs
[hacs_badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/mateogreil/homeassistant-comwatt.svg?style=for-the-badge
[releases]: https://github.com/mateogreil/homeassistant-comwatt/releases
