# Référence des capteurs

Cette page documente ce que l'intégration Comwatt expose, dans quelle unité, et la fraîcheur de chaque valeur. Elle reflète le comportement réel de l'API Comwatt — si le code et cette page divergent, c'est un bug.

Les deux distinctions clés à garder en tête :

- Les capteurs de **puissance** (`Production` du site, `Power` d'un appareil, …) sont des **puissances instantanées en watts (W)**. Ils indiquent la vitesse à laquelle l'énergie circule *maintenant*.
- Les capteurs **`*_total_energy`** sont des **énergies cumulées en watt-heures (Wh)**. Ils indiquent la quantité totale d'énergie qui a circulé. Ce sont eux qu'il faut utiliser dans le **tableau de bord Énergie**.

_[Read in English](sensors.md)_

## Capteurs du site

Chaque site donne lieu à un appareil doté des capteurs suivants.

| Capteur | Unité | Ce qu'il mesure | Cadence |
|---|---|---|---|
| Production | W | Production instantanée du site | ~toutes les 2 min |
| Consumption | W | Consommation instantanée du site | ~toutes les 2 min |
| Injection | W | Puissance instantanée injectée sur le réseau | ~toutes les 2 min |
| Withdrawal | W | Puissance instantanée soutirée du réseau | ~toutes les 2 min |
| Charge | W | Puissance instantanée de charge batterie | ~toutes les 2 min |
| Discharge | W | Puissance instantanée de décharge batterie | ~toutes les 2 min |
| Auto Production Rate | % | Taux d'autoproduction rapporté par l'API Comwatt | ~toutes les 2 min |
| Auto Consumption Rate | % | Taux d'autoconsommation rapporté par l'API Comwatt | ~toutes les 2 min |
| Injection Rate | % | Taux d'injection rapporté par l'API Comwatt | ~toutes les 2 min |
| Withdrawal Rate | % | Taux de soutirage rapporté par l'API Comwatt | ~toutes les 2 min |
| Production Total Energy | Wh | Production cumulée | pas horaires |
| Consumption Total Energy | Wh | Consommation cumulée | pas horaires |
| Injection Total Energy | Wh | Injection réseau cumulée | pas horaires |
| Withdrawal Total Energy | Wh | Soutirage réseau cumulé | pas horaires |
| Charge Total Energy | Wh | Charge batterie cumulée | pas horaires |
| Discharge Total Energy | Wh | Décharge batterie cumulée | pas horaires |

### Puissance du site (W) : une série FLOW ~2 minutes

Les capteurs de puissance du site ne sont **pas** des deltas d'énergie. Ils proviennent de la série temporelle REST `FLOW` (`get_site_time_series(..., "FLOW", ...)`) échantillonnée par le backend Comwatt environ toutes les deux minutes ; l'intégration interroge l'API toutes les ~2 minutes et publie le dernier échantillon. Attendez-vous à de petits paliers, pas à des courbes temps réel.

### Énergie totale du site (Wh) : des pas horaires, par conception

Les capteurs `*_total_energy` du site sont des compteurs cumulés alimentés **exclusivement** par les buckets officiels REST `QUANTITY/HOUR` (énergie par heure complète). Le backend Comwatt ne publie un bucket qu'une fois l'heure terminée, ces totaux **avancent donc par pas horaires** — c'est la cadence de l'API, pas un bug. Au premier lancement, les compteurs sont amorcés avec environ 8 jours d'historique officiel afin que le tableau de bord Énergie affiche immédiatement des données, et les totaux sont persistés pour survivre aux redémarrages.

## Capteurs des appareils

Chaque compteur / équipement remonté par la box Comwatt donne lieu à un appareil doté de :

| Capteur | Unité | Ce qu'il mesure | Cadence |
|---|---|---|---|
| Power | W | Puissance instantanée de l'appareil | temps réel (WebSocket) |
| Total Energy | Wh | Énergie cumulée de l'appareil | temps réel, réconciliée toutes les heures |

Les appareils exposant une capacité `POWER_SWITCH` / `RELAY` reçoivent aussi une entité **Switch** (prises commandables, relais…), mise à jour en temps réel via le flux WebSocket.

### Puissance de l'appareil (W) : temps réel via WebSocket

Les capteurs `Power` par appareil sont mis à jour en temps réel depuis le flux de mesures WebSocket (messages `FLOW`). Les appareils triphasés émettent une mesure par phase ; l'intégration les somme en une seule puissance instantanée par appareil.

### Énergie totale de l'appareil (Wh) : accumulation temps réel + réconciliation horaire

Les capteurs `Total Energy` des appareils combinent deux sources :

1. **Accumulation temps réel** — chaque salve de puissance du flux WebSocket est intégrée (∫W·dt) dans le total courant, de sorte que le capteur avance en temps réel entre deux interrogations.
2. **Réconciliation horaire** — environ une fois par heure, l'intégration récupère les buckets officiels `QUANTITY/HOUR` depuis l'API REST et corrige le total temps réel pour chaque heure terminée, afin que la dérive due aux échantillons manquants du flux reste bornée. (L'API Comwatt renvoie ces buckets dans des unités mixtes — Wh pour certains appareils, kWh pour d'autres — l'intégration déduit donc l'unité en comparant chaque bucket à la mesure temps réel.)

Deux conséquences à connaître :

- Le total d'un appareil **démarre à zéro à l'installation de l'intégration** — contrairement aux totaux du site, il n'est pas retro-amorcé avec l'historique ; il compte à partir du moment où le flux est actif.
- Le total courant est persisté dans le stockage Home Assistant, il **survit donc aux redémarrages**.

## Flux WebSocket : capacités et limites

L'intégration maintient un flux WebSocket par site pour obtenir les mesures en temps réel. Le flux envoie :

- des messages `FLOW` — puissance instantanée, routée vers les capteurs `Power` des appareils et l'accumulateur d'énergie temps réel ;
- des messages `STATE` — état marche/arrêt, routé vers les entités `Switch`.

Il n'envoie **pas** de messages `QUANTITY` (énergie) — le WebSocket Comwatt ne les fournit tout simplement pas. Toutes les données d'énergie proviennent donc de l'API REST (buckets horaires), soit directement (totaux du site), soit par réconciliation (totaux des appareils). Le flux se reconnecte automatiquement avec un backoff en cas de coupure.

## Tableau de bord Énergie

Utilisez les entités **`*_total_energy`** (site et appareils) dans le **tableau de bord Énergie** (Paramètres → Tableaux de bord → Énergie). Elles portent les bonnes métadonnées (`device_class: energy`, `state_class: total_increasing`) pour que Home Assistant construise des statistiques long terme.

N'utilisez **pas** les entités de puissance (W) dans le tableau de bord Énergie : ce sont des mesures instantanées, pas de l'énergie. Si vous avez besoin d'une énergie au niveau du site qui avance plus souvent qu'une fois par heure, utilisez les capteurs `Total Energy` des appareils (temps réel) — les capteurs `*_total_energy` du site avanceront toujours par pas horaires, car c'est ainsi que le backend Comwatt publie l'énergie.
