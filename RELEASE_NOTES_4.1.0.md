# Agence Numérique Financière — version 4.1.0

Cette version ajoute les **comptes utilisateurs**, un **essai gratuit de
3 jours** et l'**abonnement Pro par Stripe**, au-dessus de la séparation
Essai / Pro introduite en interne depuis la 4.0.0.

> **Résumé en une phrase.** L'application exige désormais un compte ;
> l'inscription ouvre 3 jours d'essai bridé ; passé ce délai, seul un
> abonnement Pro — 78,79 €/mois ou 849,99 €/an — rouvre l'application.

---

## Compte utilisateur obligatoire

Sans inscription puis connexion, l'application est fermée : toutes les routes
répondent `401` et l'interface présente l'écran de création de compte.

L'inscription demande **e-mail, mot de passe, téléphone et adresse postale**.
Elle ouvre immédiatement l'essai.

**Aucune donnée de carte bancaire n'est demandée ni stockée**, sous aucune
forme — ni numéro, ni date d'expiration, ni cryptogramme. Un champ de carte
envoyé malgré tout fait échouer la requête, et la base n'a aucune colonne pour
en recevoir. La carte est saisie sur les pages de Stripe, jamais ici :
détenir ces données imposerait la conformité PCI-DSS et ferait porter à
l'éditeur le risque d'une fuite, pour un service que Stripe rend déjà.

### Un contact ne peut pas resservir

Un e-mail ou un téléphone déjà enregistré est refusé, y compris déguisé :

| Tentative | Résultat |
|---|---|
| `JEAN.DUPONT@…` après `jean.dupont@…` | refusée (casse) |
| `jean+essai2@…` après `jean@…` | refusée (alias « + ») |
| `j.dupont@gmail.com` après `jdupont@gmail.com` | refusée (point non significatif) |
| `+33 6 12 34 56 78` après `06 12 34 56 78` | refusée (format international) |
| `0033612345678`, `06.12.34.56.78` | refusées |

L'unicité est portée par des **index UNIQUE en base** : deux inscriptions
simultanées ne peuvent pas passer toutes les deux.

### Sécurité des comptes

- Mots de passe en **PBKDF2-HMAC-SHA256**, 200 000 itérations, sel par compte.
- Sessions **révocables** : suspendre un compte ferme ses onglets ouverts.
- Anti-force-brute par IP, à durée de blocage croissante.
- Message de connexion **identique** que l'e-mail soit inconnu ou le mot de
  passe faux — distinguer les deux révélerait qui est inscrit.
- Cookie `HttpOnly`, `SameSite=lax`, et `Secure` automatique en HTTPS.

---

## Essai gratuit de 3 jours, puis abonnement

| | Essai | Pro |
|---|---|---|
| Durée | 3 jours | tant que l'abonnement court |
| Agents IA utilisables | 6 / 45 | 45 / 45 |
| Requêtes IA par jour / par heure | 20 / 5 | illimité \* |
| Symboles par analyse | 1 | illimité \* |
| Backtest, pré-vol, rapports | ❌ | ✅ |
| Trading automatique, automatisations | ❌ | ✅ |
| Tâches en arrière-plan | ❌ | ✅ |
| Paramètres avancés, multi-comptes | ❌ | ✅ |

\* Sous réserve des limites techniques (vitesse du moteur IA local,
disponibilité des cours).

**Aucun agent n'a été supprimé.** Les 45 restent dans le projet, instanciés et
affichés ; en essai, 39 portent un cadenas et ne participent pas aux cycles —
l'essai doit laisser découvrir ce que l'abonnement apporte.

Passé les 3 jours sans abonnement, le compte est **suspendu** : `402` sur
toutes les routes, seul l'écran d'abonnement reste joignable.

### Deux formules

| Formule | Prix | Essai Stripe |
|---|---|---|
| Pro mensuel | **78,79 €** / mois | 3 jours |
| Pro annuel | **849,99 €** / an | 3 jours |

Les liens de paiement livrés sont ceux de **production** : ils encaissent des
règlements réels. Ils se règlent dans `python/licence/config.py`, ou sans
recompiler par `STRIPE_LIEN_MENSUEL` / `STRIPE_LIEN_ANNUEL`. Tout lien qui ne
commence pas par `https://buy.stripe.com/` est refusé.

Au-delà des 3 jours d'essai de l'application, les liens accordent **3 jours
supplémentaires côté Stripe** avant le premier prélèvement. L'abonnement est
actif dès la souscription : l'abonné n'attend pas d'avoir été débité.

### Comment un paiement est confirmé

Rien de ce qui vient du navigateur ne vaut preuve de paiement. Deux sources,
toutes deux côté serveur :

1. **Webhook Stripe signé** (`STRIPE_WEBHOOK_SECRET`) — HMAC-SHA256, tolérance
   d'horloge, protection contre le rejeu. Chemin de référence, qui porte aussi
   les renouvellements.
2. **Relecture de session** via l'API Stripe (`STRIPE_SECRET_KEY`).

La **formule est déduite du montant réellement encaissé**, jamais d'un
paramètre d'URL : on ne choisit pas l'annuel en réglant le tarif mensuel.

Pendant l'essai Stripe, ce montant vaut **zéro** — rien n'est encore prélevé.
La formule est alors déduite de la **périodicité de l'abonnement** (`month` /
`year`), relue au besoin via l'API. Sans cela, un abonné annuel n'aurait reçu
que les droits d'un mois : le défaut aurait été silencieux jusqu'à sa
suspension, un mois après avoir payé un an.

Sans ces secrets, aucun paiement ne peut être confirmé — l'application le dit
et le compte reste fermé.

**Nouveau en 4.1.0 :** l'application détecte une clé et des liens qui ne
parlent pas du même monde. Le cas grave est silencieux : une clé de *test*
avec des liens de *production* ferait payer réellement vos clients sans jamais
débloquer leur compte. L'alerte apparaît dans l'écran d'abonnement et dans
`scripts/verifier-serveur.sh`.

---

## Où vivent les comptes

Par défaut dans la base SQLite de la machine. Une variable suffit à les
centraliser sur un serveur PostgreSQL, sans rien changer d'autre :

```ini
AGENCE_COMPTES_DSN=postgresql://agence:MOTDEPASSE@[2001:db8::1]:5432/agence
```

Les crochets autour d'une adresse IPv6 sont obligatoires — l'application
refuse le DSN au démarrage en indiquant la correction si on les oublie. TLS
est imposé vers un hôte distant (`sslmode=require` d'office, `disable`
refusé). Une base centrale injoignable provoque une **erreur franche**, jamais
un repli silencieux sur SQLite — qui créerait un second jeu de comptes avec
des essais neufs et des abonnements introuvables.

> ⚠️ **Stripe ne gère que l'IPv4**
> ([docs.stripe.com/ips](https://docs.stripe.com/ips)). Stocker les comptes sur
> une adresse IPv6 ne pose aucun problème — c'est du serveur à serveur. Mais le
> serveur qui **expose l'application** a besoin d'une adresse IPv4 publique,
> d'un nom de domaine et d'un certificat valide, sans quoi aucun webhook
> n'arrive et aucun paiement n'est confirmé.

---

## Déploiement

`DEPLOIEMENT.md` couvre la mise en ligne complète, et deux scripts
l'accompagnent :

- `scripts/configurer-serveur.sh` écrit `python/.env` en mode 600 après avoir
  contrôlé les valeurs saisies (une clé `pk_` au lieu de `sk_`, un domaine en
  `http://` sont refusés). Les secrets ne sont jamais affichés.
- `scripts/verifier-serveur.sh` contrôle un serveur en place : il répond, il
  est bien fermé sans compte, les secrets Stripe sont lus, les liens ne sont
  plus en mode test, aucun secret ne sort, le cookie porte `Secure`, Stripe est
  joignable en IPv4. Sort en code 1 s'il reste un blocage.

> ⚠️ **Un serveur = un utilisateur.** Les comptes gèrent l'accès et la
> facturation, pas le cloisonnement du trading : le Chef d'Orchestre, le
> portefeuille et la connexion MetaTrader 5 restent des singletons de
> processus. Pour plusieurs clients, prévoir une instance par client.

---

## Corrections de cette version

Quatre défauts d'enchaînement, tous introduits par la fermeture de
l'application et tous reproduits dans un vrai navigateur avant correction :

- **La fenêtre du bureau n'affichait que du JSON au lancement.** Elle démarre
  sur `/setup`, que la garde de compte avait fermé. Plus largement, une *page*
  demandée par le navigateur recevait du JSON : toute page refusée redirige
  désormais vers la racine, qui sait afficher l'écran d'inscription.
- **« Ollama non détecté » et « undefined »** sur l'écran de configuration :
  ses neuf routes `/api/ollama/*` et `/api/hermes/*` étaient fermées. Le refus
  ne portait pas les champs que la page lit, d'où le « undefined » et une
  détection en échec sur une machine où Ollama était installé.
- **« Backend hors ligne » alors que le serveur répondait.** Les sondages
  périodiques recevaient un `401` et redirigeaient vers `/login`, qui renvoie
  vers `/` : la page se rechargeait sans fin. Un `401` ouvre maintenant
  l'écran de compte, sans navigation.
- **Le formulaire d'inscription se vidait pendant la frappe**, reconstruit
  toutes les trois secondes par ces mêmes `401`. L'ouverture est devenue
  idempotente et la saisie vit hors du DOM.

Également corrigé : une fuite de descripteurs dans le dépôt des comptes, et un
défaut d'isolation préexistant de la suite de tests.

---

## Compatibilité

- **Les données sont conservées.** La base vit dans `~/.agence_financiere` et
  n'est pas touchée par une mise à jour.
- **Un compte est désormais nécessaire**, y compris sur une installation
  existante : au premier lancement après mise à jour, l'écran d'inscription
  s'affiche.
- Le trading réel reste réservé à Windows avec MetaTrader 5.
- Nouvelle dépendance : `cryptography` (vérification des licences signées).
  `psycopg` n'est requis que pour les comptes centralisés
  (`python/requirements-serveur.txt`).

---

## Microsoft Store

L'application est également disponible sur le Microsoft Store :
https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR
