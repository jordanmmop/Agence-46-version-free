# Intégration des licences Agence Novia — spécification serveur

Ce document s'adresse au **développeur du site Agence Novia**. Il décrit
exactement ce que l'application **Agence 46** envoie, ce qu'elle attend en
retour, et comment signer les licences. Tout ce qui est écrit ici est vérifié
par la suite de tests de l'application (`python/tests/test_novia_licence.py`),
et un serveur de référence exécutable est fourni :
[`python/tests/_novia_factice.py`](../python/tests/_novia_factice.py).

---

## 1. Principe

```
Site Novia                                   Agence 46 (poste du client)
──────────                                   ───────────────────────────
compte client ─ Stripe ─ licence ─ clé  ───►  saisie de la clé
                                               │
clé PRIVÉE Ed25519 ◄── POST /activate ─────────┘  (5 champs, rien d'autre)
      │
      └─ signe un jeton ───────────────────►  vérifié avec la clé PUBLIQUE
                                               embarquée dans l'application
                                               │
                                               └─ fonctionnement 100 % local,
                                                  hors ligne jusqu'à
                                                  `offline_until`
```

- Agence 46 est une application **locale** : données, projets, modèles, Ollama
  ne quittent jamais la machine. Le serveur Novia ne reçoit **que** ce qui est
  listé au § 3.
- Le serveur signe ; l'application vérifie. **La clé privée n'existe que sur
  le serveur Novia.** L'application n'embarque que la clé publique.
- La réponse JSON est indicative ; **c'est le jeton signé qui fait foi.** Une
  réponse `{"valid": true}` sans jeton authentique est refusée.

---

## 2. Généralités

| | |
|---|---|
| Transport | **HTTPS obligatoire**, certificat valide émis par une autorité publique. L'application refuse toute URL `http://` et ne contourne jamais une erreur de certificat. |
| Format | `Content-Type: application/json`, UTF-8, dans les deux sens |
| Méthode | `POST` pour les trois routes |
| Dates | **secondes UNIX** (entiers), UTC |
| Délai | l'application abandonne au-delà de **15 s** |
| Réseau | Stripe mis à part, le serveur doit être joignable en **IPv4** |
| En-têtes envoyés | `Accept: application/json`, `User-Agent: Agence46-Licence/1` |

L'URL de base est configurée côté application (`LICENSE_API_URL`), par
exemple `https://licences.agence-novia.fr`. Les chemins ci-dessous s'y
ajoutent.

---

## 3. Ce que l'application envoie — et rien d'autre

| Champ | Type | Exemple | Remarque |
|---|---|---|---|
| `license_key` | chaîne | `NOVIA-7K3Q-M9XZ-2PR4-TB8L` | majuscules, sans espaces |
| `installation_id` | chaîne (UUID v4) | `0f8fad5b-d9cb-469f-a165-70867728950e` | tiré au hasard à l'installation ; **aucune donnée matérielle ni personnelle** ; stable tant que le dossier utilisateur est conservé |
| `product` | chaîne | `agence46` | permet de servir plusieurs applications Novia |
| `app_version` | chaîne | `4.3.0` | absent de `deactivate` |
| `platform` | chaîne | `windows` \| `macos` \| `linux` | absent de `deactivate` |

L'application n'envoie **jamais** : fichiers, projets, prompts, conversations,
modèles, données Ollama, nom d'utilisateur, nom de machine, adresse MAC.

### Format de la clé de licence

```
NOVIA-XXXX-XXXX-XXXX-XXXX
```

Préfixe `NOVIA`, puis **3 à 6 groupes de 4** caractères `[A-Z0-9]`. Forme
canonique recommandée : **4 groupes**. Évitez `0/O` et `1/I/L` dans
l'alphabet de génération (les clés se recopient à la main).

L'application vérifie le **format** avant tout envoi : une clé mal formée ne
part jamais sur le réseau.

---

## 4. `POST /api/license/activate`

Active la licence **sur cet appareil**.

### Requête

```json
{
  "license_key": "NOVIA-7K3Q-M9XZ-2PR4-TB8L",
  "installation_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
  "product": "agence46",
  "app_version": "4.3.0",
  "platform": "windows"
}
```

### Logique attendue côté serveur

1. `product` inconnu ou différent de celui de la licence → `PRODUCT_MISMATCH`.
2. Clé inconnue → `INVALID_KEY`.
3. Licence expirée / suspendue / révoquée → le code correspondant (§ 7).
4. **`installation_id` déjà enregistré pour cette licence → succès, SANS
   consommer de nouvel emplacement.** C'est ce qui permet de réinstaller
   l'application ou de « Restaurer une licence » sans épuiser les appareils.
5. Sinon, si le nombre d'appareils actifs atteint le maximum →
   `DEVICE_LIMIT_REACHED`.
6. Sinon, enregistrer l'`installation_id`, signer un jeton (§ 8), répondre.

### Réponse — succès (HTTP 200)

```json
{
  "valid": true,
  "license_status": "ACTIVE",
  "product": "agence46",
  "license_type": "PRO",
  "expires_at": 1822399318,
  "offline_until": 1792857600,
  "signed_token": "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCIsImtpZCI6Im5vdmlhLTIwMjYtMDEifQ.eyJ…",
  "server_time": 1790265600
}
```

| Champ | Obligatoire | Rôle |
|---|---|---|
| `valid` | **oui**, booléen | `true` uniquement si la licence est active |
| `license_status` | **oui** | `ACTIVE` \| `EXPIRED` \| `SUSPENDED` \| `REVOKED` \| `INVALID` |
| `product` | recommandé | si présent, doit valoir le produit demandé |
| `signed_token` | **oui si `valid`** | le jeton du § 8 — seule preuve prise en compte |
| `server_time` | recommandé | heure du serveur ; aide l'application à détecter une horloge locale fausse |
| `license_type`, `expires_at`, `offline_until` | informatifs | l'application lit ces valeurs **dans le jeton**, pas ici |

Règles de cohérence imposées par l'application (réponse rejetée sinon) :
`valid: true` exige `license_status: "ACTIVE"` **et** un `signed_token` non
vide.

### Réponse — refus

HTTP **4xx** avec un corps JSON :

```json
{
  "valid": false,
  "license_status": "INVALID",
  "product": "agence46",
  "error_code": "DEVICE_LIMIT_REACHED",
  "message": "3 appareils déjà activés sur cette licence.",
  "server_time": 1790265600
}
```

---

## 5. `POST /api/license/validate`

Revalidation périodique d'une installation **déjà activée**. Même requête que
`activate`, même format de réponse.

Différence de logique : **ne jamais enregistrer de nouvel appareil ici.** Si
l'`installation_id` n'est pas (ou plus) rattaché à la licence — par exemple
parce que le client l'a retiré depuis son espace Novia — répondre
`INSTALLATION_NOT_FOUND`.

Fréquence : l'application n'appelle **pas** cette route à chaque démarrage,
mais au plus tous les `LICENSE_CHECK_INTERVAL` jours (7 par défaut), ou quand
la fin de `offline_until` approche (moins de 2 jours). Hors ligne, elle
réessaie au plus toutes les 6 heures.

Chaque validation réussie doit renvoyer un **nouveau** jeton, avec un `iat`
et un `offline_until` recalculés : c'est ce qui prolonge le fonctionnement
hors ligne, et c'est ainsi qu'un renouvellement d'abonnement parvient à
l'application.

Quand la réponse est un refus explicite (`EXPIRED`, `SUSPENDED`, `REVOKED`,
`INVALID_KEY`, `INSTALLATION_NOT_FOUND`), l'application **efface son jeton
local** : une licence révoquée ne survit pas jusqu'à son `offline_until`.

---

## 6. `POST /api/license/deactivate`

Libère l'emplacement de cet appareil (bouton « Désactiver cet appareil »).

### Requête

```json
{
  "license_key": "NOVIA-7K3Q-M9XZ-2PR4-TB8L",
  "installation_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
  "product": "agence46"
}
```

### Réponse

- **Succès (HTTP 200)** : `{"valid": true, "license_status": "ACTIVE", "product": "agence46", "server_time": …}` — aucun jeton requis.
- Appareil déjà absent : `INSTALLATION_NOT_FOUND` — l'application le traite
  comme un succès (l'emplacement est libre).

L'application n'efface son activation locale **qu'après** cette
confirmation — jamais les projets ni les données de l'utilisateur. Sans
réseau, elle ne désactive rien (sauf si l'utilisateur force, auquel cas elle
l'avertit que l'emplacement reste occupé chez Novia jusqu'à ce qu'il le
libère depuis son espace client : **prévoyez cette action sur le site**).

---

## 7. Codes d'erreur

| `error_code` | HTTP | `license_status` | Message affiché par l'application |
|---|---|---|---|
| `INVALID_KEY` | 404 | `INVALID` | « Cette clé de licence est invalide. » |
| `LICENSE_EXPIRED` | 403 | `EXPIRED` | « Votre licence a expiré. » |
| `LICENSE_SUSPENDED` | 403 | `SUSPENDED` | « Votre licence Agence 46 est suspendue. Veuillez contacter le support. » |
| `LICENSE_REVOKED` | 403 | `REVOKED` | « Votre licence Agence 46 a été révoquée. Veuillez contacter le support. » |
| `DEVICE_LIMIT_REACHED` | 403 | `INVALID` | « Le nombre maximal d'appareils autorisés est atteint. » |
| `PRODUCT_MISMATCH` | 400 | `INVALID` | « Cette licence n'est pas une licence Agence 46. » |
| `INSTALLATION_NOT_FOUND` | 404 | `INVALID` | (validation) réactivation demandée ; (désactivation) succès |
| `INVALID_REQUEST` | 400 | `INVALID` | corps mal formé |
| `RATE_LIMITED` | **429** | — | « Le serveur de licences est temporairement indisponible… » |
| `SERVER_ERROR` | **5xx** | — | « Le serveur de licences est temporairement indisponible. » |

- Un code inconnu est traité comme `SERVER_ERROR`.
- **429 et 5xx ne coupent jamais l'accès** : l'application continue sur sa
  licence locale jusqu'à `offline_until`.
- Le champ `message` est facultatif ; l'application affiche ses propres
  textes pour les codes connus.

---

## 8. Le jeton signé

### Format

**JWT compact signé en EdDSA (Ed25519)** — RFC 7519 et RFC 8037 :

```
base64url(en-tête) "." base64url(charge utile) "." base64url(signature)
```

- `base64url` **sans remplissage** (`=` retirés).
- La signature porte sur les octets ASCII de `base64url(en-tête) + "." +
  base64url(charge utile)`, exactement comme émis.

### En-tête

```json
{ "alg": "EdDSA", "typ": "JWT", "kid": "novia-2026-01" }
```

- `alg` **doit** valoir `EdDSA`. L'application refuse tout autre algorithme,
  `none` et `HS256` compris — sans négociation.
- `kid` : identifiant de la clé de signature. Permet la **rotation** : les
  deux clés coexistent dans l'application le temps que les anciens jetons
  expirent.

### Charge utile

```json
{
  "iss": "novia",
  "aud": "agence46",
  "sub": "lic_8f2a1c",
  "inst": "0f8fad5b-d9cb-469f-a165-70867728950e",
  "tier": "PRO",
  "status": "ACTIVE",
  "iat": 1790265600,
  "exp": 1822399318,
  "offline_until": 1792857600,
  "min_version": "4.0.0",
  "max_version": null,
  "key_hint": "TB8L",
  "max_devices": 3
}
```

| Revendication | Obligatoire | Contrôle côté application |
|---|---|---|
| `iss` | **oui** | doit valoir `novia` |
| `aud` | **oui** | doit valoir le produit (`agence46`) |
| `inst` | **oui** | doit valoir l'`installation_id` de CET appareil — un jeton recopié sur un autre poste est refusé |
| `iat` | **oui**, entier | date d'émission ; un `iat` à plus de 24 h dans le futur est refusé ; sert aussi de plancher d'horloge |
| `offline_until` | **oui**, entier | au-delà, l'application exige une revalidation en ligne |
| `exp` | recommandé | fin de la licence ; `null` pour une licence perpétuelle |
| `tier` | recommandé | `FREE` \| `PRO` \| `BUSINESS` \| `ENTERPRISE` ; inconnu → traité comme `FREE` |
| `status` | recommandé | `ACTIVE` ; tout autre valeur coupe l'accès premium |
| `sub` | recommandé | identifiant de la licence (affiché pour le support) |
| `min_version` / `max_version` | facultatifs | bornes de version couvertes par la licence |
| `key_hint`, `max_devices` | facultatifs | informatifs |

### Calcul de `offline_until`

```
offline_until = min(iat + 30 jours, exp)       # 30 : valeur recommandée
```

L'application applique en plus sa propre limite (`OFFLINE_GRACE_PERIOD`,
30 jours par défaut, 90 au maximum) : elle ne peut que **raccourcir** la
période accordée par le serveur, jamais l'allonger.

⚠️ Un jeton signé **n'est pas révocable** hors ligne : il vaut jusqu'à son
`offline_until`. Gardez donc une période raisonnable — c'est la durée
maximale pendant laquelle une licence révoquée peut encore fonctionner sur un
poste qui ne se reconnecte jamais.

---

## 9. Clés de signature

### Générer la paire (une seule fois, sur le serveur)

Node.js :

```js
const crypto = require('crypto');
const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
// PRIVÉE — à stocker comme un secret (coffre, variable d'environnement)
console.log(privateKey.export({ format: 'pem', type: 'pkcs8' }));
// PUBLIQUE — 64 caractères hexadécimaux, à remettre à l'éditeur d'Agence 46
console.log(publicKey.export({ format: 'der', type: 'spki' }).subarray(-32).toString('hex'));
```

PHP (libsodium) :

```php
$paire    = sodium_crypto_sign_keypair();
$privee   = sodium_crypto_sign_secretkey($paire);   // 64 octets — SECRET
$publique = sodium_crypto_sign_publickey($paire);   // 32 octets
echo bin2hex($publique);                            // à remettre à l'éditeur
```

### Ce qui est remis à l'éditeur d'Agence 46

**Uniquement la clé publique**, en hexadécimal (64 caractères), avec son `kid`.
Elle est intégrée dans `python/licence/config.py` :

```python
NOVIA_CLES_PUBLIQUES = {"novia-2026-01": "3b6a27bcceb6a42d62a3a8d02a6f0d73…"}
```

**Ne transmettez jamais la clé privée**, ni par e-mail, ni dans un dépôt. Qui
la détient peut fabriquer des licences pour toutes les installations.

### Rotation

1. Générer une nouvelle paire, `kid` différent.
2. Remettre la nouvelle clé publique ; l'éditeur l'**ajoute** (sans retirer
   l'ancienne) et publie une version d'Agence 46.
3. Signer avec la nouvelle clé une fois cette version diffusée.
4. Retirer l'ancienne clé publique après `offline_until` + marge.

---

## 10. Signer un jeton

<!-- exemple-node -->
Node.js (≥ 12, aucune dépendance) — **cet exemple est exécuté tel quel par la
suite de tests d'Agence 46** :

```js
const crypto = require('crypto');

function b64url(donnees) {
  return Buffer.from(donnees).toString('base64')
    .replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function signerLicence(charge, privateKey, kid) {
  const h = b64url(JSON.stringify({ alg: 'EdDSA', typ: 'JWT', kid }));
  const p = b64url(JSON.stringify(charge));
  const signature = crypto.sign(null, Buffer.from(`${h}.${p}`), privateKey);
  return `${h}.${p}.${b64url(signature)}`;
}
```

PHP (libsodium, inclus dans PHP ≥ 7.2) :

```php
function b64url(string $d): string {
    return rtrim(strtr(base64_encode($d), '+/', '-_'), '=');
}

function signerLicence(array $charge, string $clePrivee64Octets, string $kid): string {
    $h = b64url(json_encode(['alg' => 'EdDSA', 'typ' => 'JWT', 'kid' => $kid]));
    $p = b64url(json_encode($charge));
    $signature = sodium_crypto_sign_detached("$h.$p", $clePrivee64Octets);
    return "$h.$p." . b64url($signature);
}
```

Python (`cryptography`) :

```python
import base64, json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def b64url(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).decode().rstrip("=")

def signer_licence(charge: dict, cle_privee: Ed25519PrivateKey, kid: str) -> str:
    h = b64url(json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": kid}).encode())
    p = b64url(json.dumps(charge).encode())
    return f"{h}.{p}.{b64url(cle_privee.sign(f'{h}.{p}'.encode()))}"
```

---

## 11. Hors ligne et horloge

- Entre deux validations, l'application fonctionne **sans aucune connexion**,
  sur la foi du jeton, jusqu'à `offline_until`.
- Passé ce délai sans revalidation, elle passe en « licence à revalider » :
  premium coupé (ou application fermée, selon la politique d'expiration de
  l'éditeur), **données intactes**. Une validation réussie rétablit tout.
- **Horloge locale** : l'application ne revient jamais en arrière. Elle
  retient la plus grande heure observée ; reculer l'horloge de l'ordinateur ne
  rend aucun jour hors ligne. Le `iat` signé sert de plancher. Une horloge en
  avance fait expirer la licence plus tôt ; elle se rétablit à la première
  validation en ligne réussie avec une horloge corrigée.
- `server_time` permet à l'application de signaler à l'utilisateur une
  horloge décalée de plus d'un jour.

---

## 12. Exigences de sécurité côté serveur

- **HTTPS** avec un certificat d'autorité publique (Let's Encrypt convient).
- **Clé privée** hors du code et du dépôt : coffre à secrets ou variable
  d'environnement, accès restreint.
- **Limitation de débit** par clé et par IP sur les trois routes (répondre
  `429`) : empêche l'énumération de clés.
- **Ne jamais journaliser une clé complète** : `NOVIA-****-****-****-TB8L`.
- Ne pas réutiliser une clé de licence entre produits.
- Stocker, par licence : statut, niveau, échéance, nombre maximal
  d'appareils, liste des `installation_id` actifs (avec date d'activation et
  de dernière validation — utile au support).
- Côté espace client : permettre de **voir et retirer** les appareils
  activés, et d'afficher la clé de licence (« Restaurer une licence »).

---

## 13. Liste de vérification avant mise en service

1. Paire de clés générée ; clé **publique** + `kid` remis à l'éditeur.
2. Les trois routes répondent selon les §§ 4 à 7 (le serveur de référence
   `python/tests/_novia_factice.py` montre le comportement exact).
3. Jeton conforme au § 8 — contrôle rapide : décoder l'en-tête et la charge
   sur jwt.io, vérifier `alg: EdDSA`, `aud`, `inst`, `offline_until`.
4. URL de production communiquée à l'éditeur (`LICENSE_API_URL`), ainsi que
   les pages « Acheter » et « Gérer mon abonnement ».
5. Test de bout en bout : activer une vraie clé depuis Agence 46, couper le
   réseau, vérifier que l'application reste utilisable, désactiver.
