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

## Compte utilisateur et essai de 3 jours

**L'application exige un compte.** Sans inscription puis connexion, rien n'est
utilisable : toutes les routes répondent `401` et l'interface présente l'écran
de création de compte.

L'inscription demande : **e-mail, mot de passe, téléphone, adresse postale**
(adresse, code postal, ville, pays). Elle ouvre immédiatement un **essai
gratuit de 3 jours**, avec les limites d'essai détaillées plus bas.

> **Aucune carte bancaire n'est demandée à l'inscription**, et l'application
> n'en stocke aucune, sous aucune forme — ni numéro, ni date d'expiration, ni
> cryptogramme. La carte est saisie **sur les pages de Stripe**, jamais ici.
> C'est un choix de sécurité : détenir ces données imposerait la conformité
> PCI-DSS et ferait porter à l'éditeur le risque d'une fuite, pour un service
> que Stripe rend déjà. L'application n'apprend du paiement que son résultat.

### Passé les 3 jours

Sans abonnement, le compte est **suspendu** : l'application devient
inutilisable (`402` sur toutes les routes) et seul l'écran d'abonnement reste
joignable — c'est le seul moyen de régulariser.

### Un contact ne peut pas resservir

Un e-mail ou un téléphone déjà enregistré est refusé. La comparaison se fait
sur des **clés canoniques**, ce qui bloque les réinscriptions déguisées :

| Tentative | Résultat |
|---|---|
| `JEAN.DUPONT@…` après `jean.dupont@…` | refusée (casse) |
| `jean+essai2@…` après `jean@…` | refusée (alias « + ») |
| `+33 6 12 34 56 78` après `06 12 34 56 78` | refusée (format international) |
| `0033612345678`, `06.12.34.56.78` | refusées |

L'unicité est portée par des **index UNIQUE en base**, donc tenue par le
moteur lui-même : deux inscriptions simultanées ne peuvent pas passer toutes
les deux.

## Abonnement Pro — deux formules

| Formule | Prix | Lien de paiement |
|---|---|---|
| Mensuel | **78,79 €** / mois | `buy.stripe.com/test_aFaaEX7ol6Nc9ZsewvdZ601` |
| Annuel | **849,99 €** / an | `buy.stripe.com/test_00w8wP3851sS0oS1JJdZ602` |

> ⚠️ Ces liens sont des liens Stripe **de test** : ils n'encaissent aucun
> paiement réel. Les remplacer par les liens de production dans
> `python/licence/config.py` avant toute mise en vente — c'est le seul
> changement à faire.

Une fois le règlement **confirmé par Stripe**, le compte passe en `abonné` et
l'intégralité des agents IA et des fonctionnalités avancées est débloquée.

### Comment un paiement est confirmé

Rien de ce qui vient du navigateur ne vaut preuve de paiement — ni « j'ai
payé » cliqué dans l'interface, ni un retour sur l'URL de succès, ni un
identifiant collé à la main. Deux sources, toutes deux côté serveur :

1. **Webhook Stripe** (`POST /api/abonnement/webhook`) — chemin de référence.
   La requête est **signée** ; la signature est vérifiée avec
   `STRIPE_WEBHOOK_SECRET`, avec protection contre le rejeu. C'est lui qui
   porte aussi les renouvellements.
2. **Relecture de session** (`STRIPE_SECRET_KEY`) — au retour de Stripe,
   l'application demande à l'API l'état réel de la session de paiement.

La **formule est déduite du montant réellement encaissé**, jamais d'un
paramètre d'URL : on ne choisit pas l'annuel en réglant le tarif mensuel.

Sans ces deux secrets, **aucun paiement ne peut être confirmé** : l'application
le dit clairement et le compte reste fermé.

### États du compte

| État | Effet |
|---|---|
| `COMPTE_REQUIS` | personne n'est connecté — application fermée |
| `TRIAL` | essai en cours — limites d'essai |
| `TRIAL_EXPIRED` | 3 jours écoulés sans paiement — **compte suspendu** |
| `PRO_ACTIVE` | abonnement payé et vérifié — **tout est débloqué** |
| `PRO_EXPIRED` | abonnement échu — compte suspendu |
| `PAYMENT_REQUIRED` | paiement attendu ou refusé — compte suspendu |
| `SUSPENDU` | compte suspendu par l'éditeur |

Un seul état débloque les fonctionnalités Pro (`PRO_ACTIVE`) ; deux seulement
rendent l'application utilisable (`PRO_ACTIVE` et `TRIAL`).

## Stockage des comptes

Les comptes vivent dans la base de l'application, derrière un **dépôt**
(`DepotComptes`) qui isole complètement le reste du code du support de
stockage. Basculer vers une base distante ne demande de réécrire que cette
classe — aucun appelant ne connaît SQLite.

Les mots de passe sont hachés en **PBKDF2-HMAC-SHA256**, 200 000 itérations,
sel aléatoire par compte. Jamais en clair, jamais exposés par une route.

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
