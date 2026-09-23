# Déploiement sur votre serveur

Marche à suivre pour mettre l'application en ligne et **encaisser réellement**
les abonnements Stripe.

---

## 0. D'abord : quel montage voulez-vous ?

Ce choix commande tout le reste. Lisez-le avant de taper la première commande.

### ⚠️ L'état de trading est PARTAGÉ, pas cloisonné par compte

Les comptes utilisateurs que vous venez d'obtenir gèrent **l'accès et la
facturation** : qui entre, qui paie, qui est suspendu. Ils ne cloisonnent
**pas** le trading.

Dans le processus serveur, le Chef d'Orchestre (`get_orchestrateur`), le
portefeuille et la connexion MetaTrader 5 (`get_mt5_manager`) sont des
**singletons uniques**. Concrètement, si dix personnes créent un compte sur un
même serveur :

- elles voient **le même portefeuille** et les **mêmes positions** ;
- elles partagent **la même connexion courtier** ;
- l'une peut passer des ordres **sur le compte AvaTrade** d'une autre.

Ce n'est pas un défaut de la couche comptes : l'application a été conçue pour
tourner sur **le poste de son utilisateur**. Cloisonner le trading par compte
est un chantier à part (un orchestrateur, un portefeuille et une connexion
courtier par compte), qui n'est pas fait.

### Montage A — un serveur, un utilisateur (vous)

Vous hébergez l'application pour **votre seul usage**, avec votre compte et
votre abonnement. Tout fonctionne tel quel, Stripe compris.

→ Suivez les sections 1 à 7.

### Montage B — vous vendez à plusieurs clients

**N'hébergez pas une instance unique partagée.** Deux voies :

- **B1 — une instance par client.** Un conteneur / une machine virtuelle par
  abonné, chacun avec sa base. Les comptes et Stripe fonctionnent tels quels,
  l'isolation est apportée par l'hébergement. C'est la voie la plus simple
  aujourd'hui.
- **B2 — l'application reste chez le client** (installeur Windows / MSIX), et
  votre serveur ne sert que de **délivreur de licences**. Le client paie sur
  Stripe, votre serveur lui remet une **licence signée Ed25519** qu'il colle
  dans l'application (Réglages ⚙️ → Activer une licence). Ce chemin existe
  déjà dans le code (`python/licence/verification.py`) : il fonctionne hors
  ligne et ne demande aucun accès réseau vers le poste du client.
  Voir la section 8.

> Le webhook Stripe doit pouvoir **joindre** la machine qui détient la base des
> comptes. C'est pourquoi le montage B2 passe par une licence signée : Stripe
> ne peut pas appeler le PC d'un client.

---

## 0 bis. Comptes centralisés sur votre serveur (et le piège IPv6)

Par défaut, les comptes vivent dans le fichier SQLite de la machine qui fait
tourner l'application. Pour qu'ils vivent sur **votre serveur de base de
données** — et suivent donc leur propriétaire d'un appareil à l'autre — une
seule variable suffit :

```ini
AGENCE_COMPTES_DSN=postgresql://agence:MOTDEPASSE@[2001:41d0:301::21]:5432/agence
```

Rien d'autre ne change dans l'application : `licence/comptes.py` passe par un
dépôt, et `licence/depot_postgres.py` en fournit une version PostgreSQL au
contrat identique (mêmes méthodes, même unicité tenue par des index UNIQUE,
mêmes sessions révocables).

### ⚠️ Les crochets autour de l'adresse IPv6 ne sont pas optionnels

```
  ✗  postgresql://agence:mdp@2001:41d0:301::21:5432/agence
  ✓  postgresql://agence:mdp@[2001:41d0:301::21]:5432/agence
```

Sans crochets, les deux-points de l'adresse sont lus comme le séparateur du
port : « 2001 » devient le nom d'hôte. L'application **refuse** désormais ce
DSN au démarrage en indiquant la correction, plutôt que d'échouer plus tard
sur un « could not translate host name "2001" » sans rapport apparent.

### TLS imposé vers un hôte distant

La connexion transporte des mots de passe hachés, des jetons de session et des
identités. Si le DSN ne précise pas `sslmode`, `sslmode=require` est ajouté ;
un `sslmode=disable` explicite vers une adresse non locale est **refusé**.
Pour aller plus loin (recommandé), utilisez `sslmode=verify-full` avec le
certificat de votre serveur PostgreSQL.

### ⚠️⚠️ Stripe ne parle QUE l'IPv4 — votre serveur applicatif a besoin d'IPv4

C'est le point à vérifier avant tout le reste. La documentation Stripe est
explicite : « *Stripe only supports IPv4 on api.stripe.com. IPv6 isn't
supported.* » Les quinze adresses d'où partent les webhooks sont toutes des
adresses IPv4 ([docs.stripe.com/ips](https://docs.stripe.com/ips)).

Conséquence, et elle est nette :

| Ce qui passe par l'IPv6 | Verdict |
|---|---|
| Application → base PostgreSQL des comptes | ✅ **sans problème** — c'est votre réseau, de serveur à serveur |
| Stripe → votre webhook `/api/abonnement/webhook` | ❌ **impossible** en IPv6 seul |
| Application → `api.stripe.com` (relecture de session) | ❌ **impossible** en IPv6 seul |

Autrement dit : **stocker les comptes sur `2001:41d0:301::21` est parfaitement
viable**, mais le serveur qui expose l'application doit avoir une **adresse
IPv4 publique**, un **nom de domaine** et un **certificat TLS valide** — sans
quoi aucun paiement ne sera jamais confirmé, et les comptes resteront
suspendus après leurs trois jours d'essai.

Un serveur OVH dispose normalement des deux adresses ; vérifiez la vôtre :

```bash
curl -4 -s https://api.ipify.org ; echo     # doit répondre une adresse IPv4
curl -4 -sS -o /dev/null -w '%{http_code}\n' https://api.stripe.com/v1   # 401 = joignable
```

Un `401` est le bon résultat : l'API répond, elle refuse simplement une
requête sans clé.

### Installer la base sur votre serveur

```bash
sudo apt install postgresql
sudo -u postgres psql -c "CREATE USER agence WITH PASSWORD 'un-mot-de-passe-solide';"
sudo -u postgres psql -c "CREATE DATABASE agence OWNER agence;"
```

Pour que PostgreSQL écoute en IPv6 et accepte TLS, dans `postgresql.conf` :

```
listen_addresses = '2001:41d0:301::21,127.0.0.1'
ssl = on
```

et dans `pg_hba.conf`, n'ouvrez **que** ce dont vous avez besoin (`hostssl`,
jamais `host` nu, qui accepterait une connexion en clair) :

```
hostssl  agence  agence  2001:41d0:301::21/128  scram-sha-256
```

Le pilote n'est pas installé par défaut — il ne sert qu'à ce montage :

```bash
pip install -r python/requirements-serveur.txt
```

Les tables sont créées automatiquement au premier démarrage.

> **Sauvegardez cette base.** Elle contient désormais **tous les comptes et
> tous les abonnements** — voir la section 9.

---

## 1. Prérequis

- Un serveur Linux (Debian/Ubuntu récent), Python **3.11**.
- Un **nom de domaine** pointant dessus, et **HTTPS**. Ce n'est pas optionnel :
  Stripe n'envoie ses webhooks que vers des URL publiques en HTTPS, et le
  cookie de session ne doit jamais circuler en clair.
- Un compte Stripe.

> **Le trading réel exige Windows** (MetaTrader 5). Sur un serveur Linux,
> l'application tourne en **simulation** : les agents analysent et journalisent
> les ordres, mais aucun ordre réel ne part. C'est sans effet sur les comptes
> et sur l'abonnement, qui fonctionnent partout.

---

## 2. Installer l'application

```bash
sudo adduser --system --group --home /opt/agence agence
sudo -u agence git clone https://github.com/jordanmmop/Agence-46-version-free.git /opt/agence/app
cd /opt/agence/app

sudo -u agence python3 -m venv /opt/agence/venv
sudo -u agence /opt/agence/venv/bin/pip install --upgrade pip
sudo -u agence /opt/agence/venv/bin/pip install -r python/requirements.txt
```

Vérifiez que la suite passe avant d'aller plus loin :

```bash
sudo -u agence /opt/agence/venv/bin/python python/tests/run_tests.py
```

---

## 3. Côté Stripe

### 3.1 Passer les liens en production

Les liens livrés sont des liens **de test** (`/test_`) : ils n'encaissent rien.
Créez vos deux liens de paiement en mode **Live** (Stripe → Paiements → Liens
de paiement), puis remplacez-les dans **`python/licence/config.py`** :

```python
FORMULES = {
    "mensuel": { ... "lien_paiement": "https://buy.stripe.com/VOTRE_LIEN_MENSUEL" },
    "annuel":  { ... "lien_paiement": "https://buy.stripe.com/VOTRE_LIEN_ANNUEL" },
}
```

C'est le **seul** endroit à modifier : les tarifs et les liens affichés par
l'interface en découlent.

> Les prix (78,79 € et 849,99 €) doivent correspondre **exactement** à ceux
> configurés chez Stripe : la formule est déduite du **montant encaissé**. Un
> écart de plus d'un centime et le paiement n'est rattaché à aucune formule.

### 3.2 URL de succès

Sur **chacun** des deux liens de paiement (Stripe → le lien → « Après le
paiement » → « Rediriger vers votre page ») :

```
https://agence.mondomaine.fr/api/abonnement/retour?session_id={CHECKOUT_SESSION_ID}
```

Gardez `{CHECKOUT_SESSION_ID}` **littéralement** : Stripe le remplace lui-même.

### 3.3 Webhook

Stripe → Développeurs → Webhooks → « Ajouter un point de terminaison » :

- **URL** : `https://agence.mondomaine.fr/api/abonnement/webhook`
- **Événements** :
  - `checkout.session.completed`
  - `invoice.paid`
  - `invoice.payment_succeeded`
  - `customer.subscription.deleted`

Stripe affiche ensuite un **secret de signature** (`whsec_…`) : notez-le.

### 3.4 Clé secrète

Stripe → Développeurs → Clés API → **clé secrète** (`sk_live_…`).

---

## 4. Configurer le serveur

### Le plus simple : le script

```bash
cd /opt/agence/app
sudo -u agence bash scripts/configurer-serveur.sh
```

Il demande la clé secrète Stripe, le secret du webhook et votre domaine, les
contrôle (une clé `pk_` au lieu de `sk_`, un domaine en `http://` sont
refusés), puis écrit `python/.env` en **mode 600**. Les secrets saisis ne sont
jamais affichés, et une configuration existante est sauvegardée avant d'être
remplacée.

### Ou à la main

Créez `/opt/agence/app/python/.env` (ignoré par git) :

```ini
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000

# Secrets Stripe — sans eux, AUCUN paiement ne peut être confirmé
STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxxxxx

# Serveur public en HTTPS
AGENCE_COOKIE_SECURE=1
AGENCE_CORS_ORIGINS=https://agence.mondomaine.fr
```

```bash
sudo chown agence:agence /opt/agence/app/python/.env
sudo chmod 600 /opt/agence/app/python/.env
```

`BACKEND_HOST=127.0.0.1` : l'application n'écoute **que** en local, nginx
étant seul exposé. Ne laissez pas `0.0.0.0` sur un serveur public.

---

## 5. Lancer en service

`/etc/systemd/system/agence.service` :

```ini
[Unit]
Description=Agence Numerique Financiere
After=network.target

[Service]
User=agence
Group=agence
WorkingDirectory=/opt/agence/app
Environment=PYTHONPATH=/opt/agence/app/python:/opt/agence/app
ExecStart=/opt/agence/venv/bin/python -m uvicorn backend.main:app \
    --host 127.0.0.1 --port 8000 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`--proxy-headers --forwarded-allow-ips=127.0.0.1` : sans cela, l'application
croit recevoir du HTTP simple et voit toutes les requêtes venir de `127.0.0.1`
— ce qui fausse l'anti-force-brute (un seul compteur pour tout le monde) et la
détection du schéma. `--forwarded-allow-ips` est restreint au proxy : sinon
n'importe qui pourrait se déclarer une autre adresse IP.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agence
sudo systemctl status agence
```

---

## 6. nginx + HTTPS

```nginx
server {
    server_name agence.mondomaine.fr;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;   # un cycle d'analyse dure 1 à 2 minutes
    }
}
```

`X-Forwarded-Proto` et `X-Real-IP` sont **indispensables** (voir ci-dessus).
`proxy_read_timeout` aussi : le défaut de 60 s coupe une analyse en cours.

Puis le certificat :

```bash
sudo certbot --nginx -d agence.mondomaine.fr
```

---

## 7. Vérifier — dans cet ordre

### Le plus simple : le script

```bash
bash scripts/verifier-serveur.sh https://agence.mondomaine.fr
```

Il contrôle que le serveur répond, que l'application est bien **fermée sans
compte**, que les deux secrets Stripe sont lus, que les liens ne sont plus en
mode test, qu'aucun secret ne sort par une route publique, et que le cookie de
session porte `Secure`. Il sort en code 1 s'il reste un problème bloquant —
utilisable tel quel dans un script de déploiement.

### Ou à la main

```bash
# 1. Le serveur répond
curl -s https://agence.mondomaine.fr/api/health

# 2. L'application est bien FERMÉE sans compte
curl -s -o /dev/null -w "%{http_code}\n" https://agence.mondomaine.fr/api/status
# → doit afficher 401

# 3. Les secrets Stripe sont vus par l'application
curl -s https://agence.mondomaine.fr/api/abonnement | python3 -m json.tool
# → "encaissement_configure": true, "webhook_configure": true,
#   "verification_auto": true, "mode_test": false
```

Si `mode_test` vaut encore `true`, vos liens de production ne sont pas en
place (section 3.1).

**4. Le webhook** : Stripe → votre endpoint → « Envoyer un événement de test ».
Stripe doit afficher `200`. Un `400` signifie que `STRIPE_WEBHOOK_SECRET` ne
correspond pas.

**5. Un vrai paiement**, de bout en bout : créez un compte, laissez l'essai,
payez, et vérifiez que le compte bascule en abonné.

```bash
sudo journalctl -u agence -f | grep -i stripe
```

---

## 8. Montage B2 — serveur délivreur de licences

Si l'application reste installée chez vos clients, votre serveur n'a pas à
l'héberger : il encaisse et **délivre une licence signée**.

1. Générez **une fois** une paire de clés Ed25519. La clé privée ne quitte
   jamais votre serveur ; la publique part dans l'application.

   ```bash
   python3 - <<'PY'
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   from cryptography.hazmat.primitives import serialization
   p = Ed25519PrivateKey.generate()
   print("PRIVEE  (serveur, à garder secrète) :", p.private_bytes(
       serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
       serialization.NoEncryption()).hex())
   print("PUBLIQUE (à publier dans l'app)     :", p.public_key().public_bytes(
       serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
   PY
   ```

2. Renseignez la **publique** dans `python/licence/verification.py` :
   `CLE_PUBLIQUE_EMETTEUR = "…"`, puis recompilez l'application distribuée.

3. Sur votre serveur, à réception du webhook Stripe, émettez un jeton :
   `AGENCE1.<charge base64url>.<signature base64url>`, la charge étant
   `{"sub": "<client>", "plan": "PRO", "iss": "agence46", "iat": …, "exp": …}`
   signée avec la clé privée. Le format complet est documenté en tête de
   `python/licence/verification.py`.

4. Le client colle le jeton dans **Réglages ⚙️ → Activer une licence**.
   L'application le vérifie hors ligne et passe en Pro jusqu'à `exp`.

Ce service d'émission **n'existe pas encore** : c'est le composant à écrire
pour ce montage.

---

## 9. Sauvegardes

Tout vit dans `~/.agence_financiere` de l'utilisateur `agence` :
`config.json` (configuration, secret de session) et `data/agence.db`
(**comptes, abonnements, historique de trading**).

```bash
sudo -u agence sqlite3 /opt/agence/.agence_financiere/data/agence.db \
    ".backup '/opt/agence/sauvegardes/agence-$(date +%F).db'"
```

Perdre ce fichier, c'est perdre **tous les comptes et tous les abonnements**.
Sauvegardez-le avant chaque mise à jour, et automatisez-le.

---

## 10. Mettre à jour

```bash
cd /opt/agence/app
sudo -u agence git pull
sudo -u agence /opt/agence/venv/bin/pip install -r python/requirements.txt
sudo -u agence /opt/agence/venv/bin/python python/tests/run_tests.py
sudo systemctl restart agence
```

La base n'est jamais touchée par une mise à jour : elle vit hors du dossier
d'installation.

---

## Rappels de sécurité

- **Aucune donnée de carte** ne transite par l'application ni par votre
  serveur : Stripe s'en charge sur ses propres pages. Ne l'ajoutez pas.
- `STRIPE_SECRET_KEY` et `STRIPE_WEBHOOK_SECRET` sont des **secrets** : dans
  `.env` (mode 600, ignoré par git), jamais dans le dépôt, jamais dans une
  page.
- Le `.env` n'est **jamais** embarqué dans le paquet distribué (`agence.spec`
  l'exclut). Ne le copiez pas sur les postes clients.
- Si une clé fuite : révoquez-la dans Stripe **avant** d'en poser une nouvelle.
