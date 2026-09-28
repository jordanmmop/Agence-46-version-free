# Agence Numérique Financière — version 4.3.0

Cette version prépare **Agence 46** à recevoir les licences vendues sur le
site **Agence Novia**, sans rien changer à sa nature : l'application reste
**100 % locale**. Seule la licence est vérifiée en ligne, périodiquement.

> **Résumé en une phrase.** Quand l'URL du serveur Novia et sa clé publique
> sont configurées, Agence 46 s'active avec une clé `NOVIA-…`, fonctionne
> ensuite hors ligne sur la foi d'une licence signée, et se revérifie au plus
> tous les 7 jours — sans jamais envoyer autre chose que la licence.

---

## Ce qui ne change pas, et ne changera pas

- Données, projets, fichiers, SQLite, mémoire, modèles : **sur la machine**.
- Ollama reste sur `http://127.0.0.1:11434`.
- Aucune fonctionnalité principale ne dépend d'Internet.
- **Sans configuration Novia, l'application se comporte exactement comme en
  4.2.0** (compte local, essai de 3 jours, abonnement Stripe) et ne fait
  aucune requête vers Novia. C'est ce qui permet de livrer cette version
  avant que le serveur Novia n'existe.

## Ce qui part sur le réseau — et rien d'autre

`license_key`, `installation_id`, `product`, `app_version`, `platform`.

L'`installation_id` est un UUID tiré au hasard à l'installation : aucune
donnée matérielle, aucune donnée personnelle. Ni fichier, ni projet, ni
prompt, ni conversation, ni modèle, ni donnée Ollama ne partent jamais. Un
test vérifie les corps de requête champ par champ.

---

## Activation

Au premier lancement en mode Novia, l'écran **« Activation de votre
licence »** remplace l'inscription locale :

- champ **Clé de licence** et bouton **Activer Agence 46** ;
- **Acheter Agence 46** et **Restaurer une licence** (retrouver sa clé dans
  l'espace Novia ; réactiver le même poste ne consomme pas d'emplacement) ;
- **Utiliser l'application hors ligne**, proposé seulement quand une licence
  locale valide existe réellement.

La clé saisie survit aux rafraîchissements de l'écran — le défaut qu'avait eu
le formulaire d'inscription en 4.1.0 ne peut pas se reproduire ici.

## Réglages → Licence

**Agence 46 PRO** · Statut · Licence (masquée : `NOVIA-****-****-****-1234`)
· Appareil · Dernière vérification · Licence hors ligne valide jusqu'au ·
Échéance. Boutons **Actualiser la licence**, **Désactiver cet appareil**,
**Changer de licence**, **Gérer mon abonnement**.

---

## Sécurité

- **Licence signée Ed25519 (JWT EdDSA)**, vérifiée localement avec la clé
  **publique** embarquée. La clé privée n'existe que sur le serveur Novia ;
  un test vérifie qu'aucun module de l'application ne sait signer.
- **Algorithme imposé** : `alg: none`, `HS256` et tout autre algorithme sont
  refusés sans négociation — la faille JWT la plus répandue est fermée.
- Le jeton est **lié à l'appareil** (`inst`) et **au produit** (`aud`) : copié
  sur un autre poste ou présenté à une autre application, il ne vaut rien.
- Une réponse `{"valid": true}` **sans jeton authentique** n'ouvre rien.
- **HTTPS obligatoire**, certificats vérifiés. Une erreur de certificat est
  signalée comme telle, jamais confondue avec une absence de réseau, jamais
  contournée. Testé contre un vrai serveur HTTPS local : autorité inconnue
  refusée.
- Les routes qui modifient la licence **exigent un corps JSON** : une page web
  malveillante ne peut pas désactiver la licence de son visiteur.
- **Journal local** `~/.agence_financiere/logs/licence.log` : activations,
  validations, expirations, erreurs réseau, signatures invalides — clé
  toujours masquée, aucun jeton.

## Hors ligne et horloge

- Hors ligne jusqu'à **30 jours** après la dernière validation (réglable,
  90 maximum ; la valeur locale ne peut que **raccourcir** celle du serveur).
- Revalidation **au plus tous les 7 jours**, jamais à chaque démarrage ;
  hors ligne, un essai toutes les 6 heures au plus.
- **Reculer l'horloge de l'ordinateur ne rend aucun jour** : l'application
  retient la plus grande heure observée, et la date signée du jeton sert de
  plancher. Une horloge en avance se rétablit à la première validation en
  ligne après correction.

## États

`NOT_ACTIVATED` · `ACTIVE` · `OFFLINE_VALID` · `EXPIRED` · `SUSPENDED` ·
`REVOKED` · `INVALID`, et les niveaux `FREE` · `PRO` · `BUSINESS` ·
`ENTERPRISE` — ajouter un niveau se fait en une ligne de configuration.

| Situation | Effet |
|---|---|
| Licence PRO / BUSINESS / ENTERPRISE valide | accès complet, 45 agents |
| Licence FREE valide | limites de la version d'essai (6 agents, quotas) |
| Suspendue, révoquée | premium coupé, message au support, **données intactes** |
| Expirée | mode limité (défaut) ou application fermée, selon `LICENSE_EXPIRED_POLICY` |
| Aucune licence, jeton invalide | écran d'activation |

Une révocation efface le jeton local : elle ne survit pas jusqu'à la fin de
la période hors ligne.

## Désactivation

« Désactiver cet appareil » libère l'emplacement chez Novia **puis** efface
l'activation locale — et **uniquement** elle. Sans réseau, rien n'est
effacé (l'emplacement resterait occupé) ; l'utilisateur peut forcer, en
étant prévenu.

---

## Pour le développeur du site Novia

[`docs/novia-licensing-integration.md`](docs/novia-licensing-integration.md) :
routes, corps, réponses, codes d'erreur, format du jeton, génération des clés,
exemples de signature en **Node.js, PHP et Python**. Ces trois exemples sont
**exécutés par la suite de tests** : la documentation ne peut pas diverger de
ce que l'application accepte. Un serveur de référence exécutable accompagne
la spécification : `python/tests/_novia_factice.py`.

## Ce qu'il reste à faire pour passer en production

1. Novia génère sa paire de clés et remet la **clé publique** + son `kid`.
2. Renseigner dans `python/licence/config.py` : `NOVIA_API_URL_DEFAUT`,
   `NOVIA_CLES_PUBLIQUES`, `NOVIA_URL_ACHAT_DEFAUT`, `NOVIA_URL_COMPTE_DEFAUT`.
3. Recompiler et diffuser.

Tant que ces valeurs sont vides, cette version fonctionne comme la 4.2.0.

## Rappels

Tarifs Stripe inchangés en mode compte local : **78,79 €** par mois,
**849,99 €** par an, après un essai de **3 jours**. Aucune donnée de
**carte bancaire** n'est stockée. Mots de passe locaux en **PBKDF2**. Le
serveur d'abonnement Stripe doit être joignable en **IPv4**.
