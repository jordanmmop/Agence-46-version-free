# Agence Numérique Financière — version 4.2.0

Cette version répond à un manque qui rendait l'abonnement inutilisable en
pratique : **on pouvait payer sans jamais recevoir de clé d'abonnement.**

> **Résumé en une phrase.** Dès que Stripe confirme un règlement,
> l'application **émet une clé d'abonnement** et l'**envoie par e-mail et par
> SMS** à son titulaire ; coller cette clé dans « Passer à la version Pro »
> débloque l'application, y compris hors ligne.

---

## Ce qui manquait

Jusqu'en 4.1.0, un paiement confirmé ouvrait bien les droits Pro du compte,
mais **rien n'était remis à l'abonné**. L'écran « Passer à la version Pro »
réclamait une clé d'abonnement que personne n'avait jamais envoyée, et le seul
chemin prévu pour en obtenir une passait par un émetteur de licences externe
(`LICENCE_API_URL`) qui n'existe pas.

Un client qui payait se retrouvait donc devant un champ vide, sans savoir ce
qu'on attendait de lui. C'est corrigé.

---

## La clé d'abonnement

### Format

```
AGF-7K3QM-9XZ2P-R4TB8
```

Trois groupes de cinq caractères, 75 bits d'aléa, tirés d'un alphabet **sans
caractère ambigu** : ni `I`, ni `L`, ni `O`, ni `U`. Une clé se dicte au
téléphone et se recopie depuis un SMS sans se tromper entre `0` et `O`.

À la saisie, tout est accepté : minuscules, espaces, tirets oubliés, préfixe
absent, `O` tapé pour `0`, `I` ou `L` pour `1`. L'utilisateur qui tape ce
qu'il croit voir tombe juste.

### Ce qui se passe après un paiement

1. Stripe confirme le règlement — webhook signé, ou relecture de session.
2. Les droits Pro du compte s'ouvrent (inchangé).
3. Une clé d'abonnement est **émise** et rattachée au compte.
4. Elle part **par e-mail et par SMS**, sur les coordonnées du compte.
5. L'abonné la colle dans l'application : son installation enregistre une
   **licence signée Ed25519** et fonctionne ensuite hors ligne jusqu'au terme.

**Une seule clé par règlement.** Le webhook et le retour du navigateur
annoncent le même paiement : sans cette garde, l'abonné recevrait deux clés et
deux SMS, et ne saurait plus laquelle conserver.

### Ce qui est stocké

L'**empreinte SHA-256** de la clé, jamais la clé. Une clé ouvre des droits, au
même titre qu'un mot de passe : une fuite de la base ne doit pas livrer des
abonnements utilisables.

Conséquence assumée : **même l'éditeur ne peut pas réafficher une clé
perdue.** Il ne peut qu'en émettre une nouvelle, l'ancienne étant alors
révoquée. Seuls les cinq derniers caractères sont conservés en clair, pour que
l'abonné reconnaisse laquelle est laquelle.

### Ce qu'une clé ne fait pas

Elle n'ouvre aucun droit par elle-même. À la validation, c'est l'état **réel**
du compte en base qui décide : une clé dont l'abonnement est terminé est
refusée sans qu'il faille la révoquer. À l'inverse, un renouvellement prolonge
le compte, donc la clé déjà remise reste valable — l'abonné n'en reçoit pas
une nouvelle à chaque prélèvement mensuel.

---

## Envoi par e-mail et par SMS

| Canal | Mise en œuvre | Variables |
|---|---|---|
| E-mail | SMTP, bibliothèque standard | `SMTP_HOTE`, `SMTP_PORT`, `SMTP_SECURITE`, `SMTP_UTILISATEUR`, `SMTP_MOTDEPASSE`, `SMTP_EXPEDITEUR` |
| SMS | Twilio | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_EXPEDITEUR` |
| SMS | OVHcloud | `OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET`, `OVH_CONSUMER_KEY`, `OVH_SERVICE_SMS` |
| SMS | Passerelle maison | `SMS_URL`, `SMS_AUTORISATION` |

Les destinataires viennent **du compte en base**, jamais d'un paramètre
d'appel : sinon il suffirait de demander l'envoi vers une autre boîte pour
détourner un abonnement.

**Aucun secret n'est dans le dépôt.** Tout vient de l'environnement du serveur
(`python/.env`, mode 600, ignoré par git), et rien ne sort par une réponse
HTTP — un test le vérifie.

### Sans configuration d'envoi

Rien n'est cassé et rien n'est promis à tort : la clé est **affichée sur la
page de retour** du paiement, reste réémettable depuis l'application, et
l'interface annonce qu'aucun envoi automatique n'est configuré au lieu de
laisser l'abonné attendre un e-mail qui n'arrivera pas.

### Un envoi qui échoue n'annule jamais l'abonnement

Le paiement est encaissé, les droits sont ouverts, la clé est émise. Une panne
de SMTP ou de passerelle est journalisée — sans la clé — et remontée à
l'écran, jamais transformée en échec de paiement. Chaque canal est isolé :
même une variable d'environnement aberrante (`SMTP_PORT=abc`) ne peut plus
faire échouer une confirmation.

---

## Rattrapage

### Pour l'abonné

Bouton **« Je n'ai pas reçu ma clé »** dans l'écran d'abonnement, et le même
lien dans « Passer à la version Pro ». Une nouvelle clé est émise, envoyée, et
affichée à l'écran. L'ancienne est révoquée — c'est précisément celle qui
traîne dans une boîte mail oubliée dont on ne veut plus.

Un délai d'une minute sépare deux demandes : chaque envoi est un SMS
réellement facturé, et une session volée ne doit pas pouvoir les enchaîner.
Un compte en essai ne peut pas en demander : il n'a pas d'abonnement.

### Pour l'administrateur

Nouvel outil `scripts/abonnement.py`, à lancer **sur le serveur** :

```bash
python scripts/abonnement.py etat    alice@exemple.fr   # état + clés émises
python scripts/abonnement.py cle     alice@exemple.fr   # réémettre et envoyer
python scripts/abonnement.py canaux                     # ce qui est configuré
python scripts/abonnement.py activer alice@exemple.fr \
       --formule annuel --reference cs_live_xxxx        # règlement constaté
```

`activer` **ne constate aucun paiement et n'en simule aucun** : il ouvre des
droits parce que l'administrateur affirme avoir vu le règlement dans son
tableau de bord Stripe. La référence demandée est ce qui permettra de le
retrouver. La vérification automatique reste le webhook signé et la relecture
de session.

---

## Émetteur de licences

Quand l'abonné active sa clé, le serveur signe un jeton `AGENCE1.…` (Ed25519)
que l'installation revérifie ensuite seule, hors ligne, jusqu'à l'échéance.

La clé privée n'est **ni dans le dépôt, ni dans le paquet distribué, ni dans
une réponse HTTP**. Deux origines : `AGENCE_LICENCE_PRIVKEY` sur le serveur,
ou un fichier `~/.agence_financiere/licence_emetteur.json` créé au premier
besoin, écrit en **mode 600 dès sa création** — et non ouvert puis corrigé,
délai pendant lequel il serait lisible par les autres comptes de la machine.

> **Limite, écrite plutôt que passée sous silence.** Quand l'application
> tourne entièrement sur la machine de l'utilisateur (paquet MSIX, backend
> local), l'émetteur est cette même machine. La signature protège alors d'une
> licence bricolée à la main ou recopiée d'un autre poste, **mais pas** de
> quelqu'un qui contrôle la machine. Un parc où cela compte fait tourner
> l'émetteur sur un serveur, avec `AGENCE_LICENCE_PRIVKEY` là-bas et la clé
> **publique** (`AGENCE_LICENCE_PUBKEY`) sur les postes.

---

## Ce qui ne change pas

- Les tarifs : **78,79 €** par mois, **849,99 €** par an.
- L'essai de **3 jours** après création de compte, et ses limites (6 agents,
  20 requêtes/jour, 5/heure).
- **Aucune donnée de carte bancaire** n'est demandée ni stockée, sous aucune
  forme. La carte est saisie sur les pages de Stripe.
- Les mots de passe : **PBKDF2**-HMAC-SHA256, 200 000 itérations, sel par
  compte.
- Le serveur d'application doit être joignable en **IPv4** : Stripe n'émet pas
  ses webhooks en IPv6.
- Les 45 agents et les fonctionnalités Pro : rien n'a été retiré.

---

## Sécurité — ce que cette version vérifie

- Une clé inventée, révoquée, ou dont l'abonnement est terminé est **refusée**.
- La clé en clair n'est **jamais** stockée, journalisée, ni renvoyée à qui
  n'est pas le compte payeur : la référence de session Stripe, qui circule
  dans une URL, ne suffit pas à l'obtenir.
- Le webhook ne renvoie **jamais** la clé : sa réponse part chez Stripe.
- Aucun identifiant SMTP ni jeton de passerelle n'est exposé par l'API.
- Un seul fichier du paquet `licence/` a le droit de fabriquer une clé
  privée — l'émetteur — et le test le nomme, pour qu'un autre module ne puisse
  pas s'ajouter discrètement à la liste.

## Compatibilité

Aucune action requise sur une installation existante : la table des clés est
créée au premier démarrage, en SQLite comme en PostgreSQL, et les abonnements
en cours ne sont pas touchés. Les clés commencent à être émises au prochain
règlement confirmé ; pour les abonnés déjà en place, utilisez
`scripts/abonnement.py cle`.
