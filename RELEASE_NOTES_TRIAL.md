# Version d'essai — Fonctionnalités limitées

Cette version d'essai a été volontairement limitée afin de permettre la
découverte de l'application et de ses principales fonctionnalités.

## Sécurité et informations privées

Toutes les clés API, identifiants, tokens, secrets et autres informations
sensibles ont été retirés de la version distribuée.

## 36 agents IA

L'exploitation complète des 36 agents IA est volontairement bridée dans cette
version d'essai.

> **Précision sur le nombre d'agents.** L'application en compte en réalité
> **45 agents spécialisés** (plus le Chef d'Orchestre, soit 46 au total). La
> version d'essai en déverrouille **6**, la version Pro les déverrouille
> **tous** — le plafond Pro n'est pas figé à 36, ce qui en verrouillerait 9 aux
> abonnés. Le détail est dans `python/licence/config.py`.

## Version Pro

La version Pro complète permet de débloquer l'ensemble des fonctionnalités et
capacités prévues pour l'application, notamment l'exploitation complète des
agents IA et les fonctionnalités avancées.

La version Pro est accessible directement depuis l'application via un
abonnement.

## Microsoft Store

L'application est également disponible sur le Microsoft Store :

https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR

---

Version d'essai : fonctionnalités volontairement limitées.
Version Pro : accès complet après abonnement.

---

## Détail des limites appliquées

Toutes ces valeurs sont centralisées dans **`python/licence/config.py`** et
nulle part ailleurs. Les modifier là suffit à changer le comportement de toute
l'application.

| Fonctionnalité                           | Essai       | Pro         |
|------------------------------------------|-------------|-------------|
| Agents IA utilisables                    | 6 / 45      | 45 / 45     |
| Requêtes IA par jour                     | 20          | Illimité \* |
| Requêtes IA par heure                    | 5           | Illimité \* |
| Symboles par analyse (multi-agents)      | 1           | Illimité \* |
| Workflows avancés (backtest, pré-vol)    | ❌          | ✅          |
| Automatisations (trading automatique)    | ❌          | ✅          |
| Tâches longues / en arrière-plan         | ❌          | ✅          |
| Paramètres avancés des agents            | ❌          | ✅          |
| Administration avancée (multi-comptes)   | ❌          | ✅          |

\* Sous réserve des éventuelles limites techniques ou de fournisseur API.

Les **30+ agents verrouillés restent visibles** dans l'interface : les
sélectionner affiche le message « Cet agent est disponible dans la version
Pro », afin de laisser découvrir ce que l'abonnement apporte.

Ce qui **reste ouvert** en version d'essai : l'analyse à la demande, la
consultation du portefeuille, des signaux et des rapports, la connexion à un
compte MetaTrader 5, et l'arrêt de toute automatisation en cours.

## Comment les limites sont tenues

```
Abonnement / Licence   →   Feature Gate   →   Agents IA / Fonctionnalités
  (licence/abonnement)      (licence/gate)     (agents/, backend/)
```

Les restrictions sont appliquées **par le serveur de l'application**, jamais
par l'interface :

- le statut Pro n'existe qu'avec une **licence signée** (Ed25519) par
  l'émetteur ; aucune valeur locale — configuration, variable d'environnement
  ou stockage du navigateur — ne suffit à l'obtenir ;
- l'application n'embarque que la **clé publique**, qui permet seulement de
  vérifier une signature, jamais d'en fabriquer une ;
- le compteur de requêtes est rattaché au **compte** et tenu dans la base du
  serveur, pas dans le navigateur ;
- le plafond d'agents est appliqué **dans le Chef d'Orchestre** : une requête
  fabriquée à la main ne réveille pas les 45 agents.

Une licence valide reste valable **hors ligne** jusqu'à son expiration : couper
le réseau ne transforme pas un abonné en version d'essai.

## États d'abonnement

| État               | Effet                                                    |
|--------------------|----------------------------------------------------------|
| `TRIAL`            | Aucune licence — limites de la version d'essai            |
| `PRO_ACTIVE`       | Licence vérifiée et valable — **tout est débloqué**       |
| `PRO_EXPIRED`      | Licence périmée — retour aux limites d'essai              |
| `PAYMENT_REQUIRED` | Abonnement à régler ou licence refusée — limites d'essai  |

Un seul de ces quatre états débloque quoi que ce soit.

## Souscription

L'architecture d'abonnement est en place (`python/licence/abonnement.py`) mais
**aucun prestataire de paiement n'est raccordé à ce jour** : aucun paiement
n'est simulé, et l'activation indique clairement ce qui manque. Deux chemins
sont prévus :

- **`serveur`** — un émetteur de licences HTTPS (variable `LICENCE_API_URL`) ;
- **`microsoft_store`** — abonnement vendu comme extension de la fiche Store,
  qui demande le SDK Windows Store.

Si vous disposez déjà d'une clé d'abonnement ou d'une licence signée, elle
s'active depuis **Réglages ⚙️ → Offre & abonnement → Passer à Pro**.
