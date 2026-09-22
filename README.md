# Agence Numérique Financière

Plateforme de trading autonome : **46 agents IA** (45 spécialisés + 1 chef
d'orchestre) et **46 assistants IA locaux** qui analysent les marchés,
produisent des signaux et exécutent des ordres sur **AvaTrade / MetaTrader 5**
— avec de vrais garde-fous d'exécution professionnels.

**100 % local.** L'IA tourne sur votre machine : aucune clé API, aucun
abonnement, aucun compte à créer, et rien de ce que produisent les agents ne
part vers un service tiers. Les seules connexions sortantes sont les cours de
marché et votre courtier.

> ⚠️ **Avertissement** : le trading comporte un risque réel de perte en
> capital. Aucun système, IA comprise, ne garantit des gains. Commencez
> toujours en compte **démo**, avec les protections par défaut.

---

## Installer l'application (utilisateur final)

1. Télécharger `AgenceNumerique-Setup-<version>.exe`
2. Double-cliquer, suivre l'assistant (le dossier d'installation est
   modifiable), puis lancer depuis le **Bureau** ou le **menu Démarrer**
3. **Aucun code d'accès** : l'application s'ouvre directement
4. Choisir le moteur IA : **Ollama** ou **Hermès** — tous deux locaux et
   gratuits

**Aucun outil de développement n'est requis** : Python, les bibliothèques et
l'interface sont dans l'installeur. Aucun droit administrateur non plus —
l'installation se fait dans votre profil utilisateur. La désinstallation se
fait normalement depuis « Applications installées » de Windows, et **conserve
vos données** sauf demande explicite.

## Compiler soi-même (Windows)

1. **Télécharger** : bouton `Code` → `Download ZIP` → extraire
2. **Tout construire** : double-cliquer `build_installer.bat`
   (dépendances, moteurs embarqués, compilation, puis l'installeur —
   comptez 30 à 60 min et 5 à 10 Go la première fois)
3. Résultat : `dist\installateur\AgenceNumerique-Setup-<version>.exe`

```bat
build_installer.bat              :: application + installeur, tout embarque
build_installer.bat --leger      :: sans les moteurs IA (~300 Mo au lieu de 5-10 Go)
build_exe.bat                    :: l'application seule, sans installeur
```

L'installeur exige **Inno Setup 6.1+** (`winget install -e --id
JRSoftware.InnoSetup` ; la version 7 convient aussi, mais elle réclame
Windows 11 — sur Windows 10, prenez la 6.x). Sans lui, la compilation de
l'application se fait quand même et le script indique quoi installer. Détails, options et
déploiement silencieux : [`installer/README.md`](installer/README.md).

### Paquet MSIX (Microsoft Store et installation moderne)

En plus de l'installeur classique, le dépôt produit un paquet **MSIX** — le
format d'installation moderne de Windows, celui qu'utilise le Microsoft Store.
Installation et désinstallation propres, mise à jour par-dessus, aucun
résidu.

```bat
build_msix.bat                   :: application + paquet MSIX
build_msix.bat -Sign             :: ... et signature avec le certificat de test
build_msix.bat -DryRun           :: verifie la configuration sans rien produire
```

```powershell
:: une seule fois par machine, en PowerShell ADMINISTRATEUR
.\packaging\msix\scripts\create-test-certificate.ps1

:: verifier le paquet produit, puis l'installer et le desinstaller pour de vrai
.\packaging\msix\scripts\validate-msix.ps1
.\packaging\msix\scripts\install-test.ps1 -Desinstaller
```

Résultat : `dist\msix\AgenceNumerique-<version>-x64.msix` et
`SHA256SUMS.txt`. Un tag `v1.2.3` poussé sur GitHub reconstruit, valide,
installe, désinstalle et publie le tout automatiquement.

Le paquet MSIX est **allégé** : les moteurs IA et MetaTrader ne sont pas
embarqués, parce que le dossier d'installation d'un MSIX est en lecture seule.
L'application les installe au premier lancement dans
`%USERPROFILE%\.agence_financiere`, comme dans le build `--leger`.

Procédure complète — prérequis, certificats, versioning, publication au
Microsoft Store, dépannage : [`WINDOWS-MSIX.md`](WINDOWS-MSIX.md).

### La fenêtre s'ouvre puis se referme aussitôt

`build_exe.bat` et `build_installer.bat` ne se ferment plus sans expliquer
pourquoi : chaque échec affiche son motif et attend une touche. Si la fenêtre
disparaît malgré tout, c'est que `cmd.exe` a refusé le fichier **avant** de
l'exécuter. Deux causes, toutes deux traitées :

- **fins de ligne Unix (LF)** dans le `.bat`. `cmd.exe` exige CRLF : sur un
  bloc `if (...)` multi-ligne il part en erreur de syntaxe et rend la main
  instantanément. Tous les `.bat` de ce dépôt étaient dans ce cas ;
  `.gitattributes` force désormais la conversion à chaque extraction ;
- **le `.bat` lancé depuis l'archive ZIP** sans l'extraire. Windows le recopie
  seul dans un dossier temporaire, sans le reste du projet. Extrayez le ZIP
  (clic droit → « Extraire tout ») puis relancez depuis le dossier obtenu.

Pour voir le message quoi qu'il arrive, ouvrez d'abord un terminal
(touche Windows → `cmd`), puis :

```bat
cd C:\chemin\vers\Agence-46
build_exe.bat
```

La fenêtre reste ouverte, et le message d'erreur reste lisible. Les détails de
l'installation des dépendances sont dans `build.log`, à la racine du projet.

**Rien à installer après la compilation.** Ollama, Hermès et un terminal
MetaTrader 5 **portable déjà prêt** sont embarqués dans l'exe (voir « Tout est
embarqué » ci-dessous) : au premier lancement il n'y a plus aucun
téléchargement ni aucune installation, l'IA locale fonctionne hors connexion,
et MetaTrader démarre tout seul — il ne reste qu'à saisir vos identifiants
AvaTrade.

Alternative sans compilation : `python start.py` (ou `run.bat` / `run.sh`) —
dans ce mode, les moteurs sont téléchargés à la demande au premier usage.

### Où vont vos données

`%USERPROFILE%\.agence_financiere\` — jamais dans le dossier d'installation :
réglages (`config.json`), historique de trading (`data/agence.db`), modèles
d'IA, identifiants MetaTrader, journaux. Une mise à jour remplace
l'application et laisse tout cela intact ; la désinstallation ne le supprime
que si vous répondez « Oui » à la question posée.

Le numéro de version vient du fichier `VERSION` de la racine : il alimente à
la fois l'application (`/api/health`), la fiche de propriétés de l'exe et
l'assistant d'installation. Pour publier une nouvelle version, modifiez ce
fichier puis relancez `build_installer.bat`.

## Compte, essai de 3 jours et abonnement

**L'application exige un compte.** Sans inscription puis connexion, rien n'est
utilisable. L'inscription demande e-mail, mot de passe, téléphone et adresse
postale, et ouvre un **essai gratuit de 3 jours**. Passé ce délai sans
abonnement, le compte est **suspendu** et l'application fermée — seul l'écran
de paiement reste joignable.

> **Aucune carte bancaire n'est demandée ni stockée par l'application.** La
> carte se saisit sur les pages de Stripe. Détenir ces données imposerait la
> conformité PCI-DSS et ferait porter le risque d'une fuite à l'éditeur, pour
> un service que Stripe rend déjà.

Un e-mail ou un téléphone déjà enregistré ne peut pas resservir — y compris
déguisé (casse différente, alias `+tag`, téléphone au format international) :
c'est ce qui empêche de rouvrir un essai indéfiniment.

| Formule | Prix |
|---|---|
| Pro mensuel | **78,79 €** / mois |
| Pro annuel | **849,99 €** / an |

Le paiement passe par Stripe. **Rien de ce qui vient du navigateur ne vaut
preuve de paiement** : seul un webhook Stripe *signé* (`STRIPE_WEBHOOK_SECRET`)
ou une relecture de session via l'API (`STRIPE_SECRET_KEY`) ouvre les droits,
et la formule est déduite du **montant réellement encaissé**. Sans ces
secrets, aucun paiement ne peut être confirmé et l'application le dit.

⚠️ Les liens de paiement livrés pointent vers l'environnement **de test** de
Stripe. Les remplacer par les liens de production dans
`python/licence/config.py` avant toute mise en vente.

**Mise en ligne sur votre serveur** (Stripe, webhook, HTTPS, systemd, nginx,
sauvegardes) : [`DEPLOIEMENT.md`](DEPLOIEMENT.md).

> ⚠️ **Un serveur = un utilisateur.** Les comptes gèrent l'accès et la
> facturation, mais le portefeuille, le Chef d'Orchestre et la connexion
> MetaTrader 5 sont des singletons **partagés par tout le processus** : sur une
> instance unique, tous les comptes verraient le même portefeuille et le même
> compte courtier. Pour plusieurs clients, prévoir une instance par client, ou
> le montage « licence signée » décrit dans `DEPLOIEMENT.md`.

## Version d'essai et version Pro

Pendant l'essai, l'application est utilisable mais bridée. Le détail complet —
limites, comparatif, états du compte et mode d'abonnement — est dans
[`RELEASE_NOTES_TRIAL.md`](RELEASE_NOTES_TRIAL.md).

| | Essai | Pro |
|---|---|---|
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
affichés ; en version d'essai, 39 d'entre eux portent un cadenas et ne
participent pas aux cycles. C'est volontaire : l'essai doit laisser découvrir
ce que l'abonnement apporte.

### Comment les limites tiennent

```
Abonnement / Licence   →   Feature Gate   →   Agents IA / Fonctionnalités
  (licence/abonnement)      (licence/gate)     (agents/, backend/)
```

Toutes les valeurs sont dans **`python/licence/config.py`**, et nulle part
ailleurs — les changer là suffit. Elles ne sont volontairement PAS réglables
par variable d'environnement : une limite qu'un fichier texte relève n'est pas
une limite.

Les restrictions sont appliquées **par le serveur**, jamais par l'interface :

- le statut Pro exige une **licence signée** (Ed25519) par l'émetteur ; aucune
  valeur locale — config, variable d'environnement, stockage du navigateur —
  ne l'accorde ;
- l'application n'embarque que la **clé publique**, qui permet seulement de
  vérifier une signature, jamais d'en fabriquer une ;
- le plafond d'agents est appliqué **dans le Chef d'Orchestre**, le quota
  **en base et par compte** ;
- si l'abonnement expire pendant que le trading automatique tourne, la boucle
  **s'arrête d'elle-même**.

Une licence valide reste valable **hors ligne** jusqu'à son expiration.

### Abonnement

L'architecture est en place, mais **aucun prestataire de paiement n'est
raccordé à ce jour** : rien n'est simulé, et l'activation dit ce qui manque.
Deux chemins sont prévus — un émetteur HTTPS (`LICENCE_API_URL`) ou
l'extension Microsoft Store. Une clé déjà en votre possession s'active depuis
**Réglages ⚙️ → Offre & abonnement**.

L'application est également disponible sur le
[Microsoft Store](https://apps.microsoft.com/detail/9nltgfr2btsp?hl=fr-FR&gl=FR).

## Code d'accès : aucun par défaut

L'application ne demande **aucun code** au lancement. Elle s'ouvre
directement, sur l'ordinateur comme depuis un téléphone du même Wi-Fi.

C'est un choix assumé, et il a une conséquence qu'il vaut mieux connaître :
le serveur écoute sur le réseau local — c'est ce qui rend l'accès mobile
possible (voir ci-dessous). Sans code, **tout appareil de ce réseau** peut
donc ouvrir l'interface, consulter les identifiants enregistrés du courtier
et déclencher des ordres réels.

Sur un réseau personnel, cela ne porte pas à conséquence. Sur un réseau
partagé — café, hôtel, espace de travail, entreprise — deux parades, au
choix :

```bat
:: 1. rétablir le code d'accès (497040, modifiable dans Réglages)
set APP_AUTH=on

:: 2. ou n'écouter que sur cet ordinateur (l'accès téléphone cesse alors)
set BACKEND_HOST=127.0.0.1
```

`APP_PASSWORD=<code>` impose un code précis et active l'authentification du
même coup. Tout le mécanisme est resté en place — KDF lent et salé,
anti-force-brute par IP, sessions signées : il ne demande qu'un mot pour
revenir.

Réglages ⚙️ → **Sécurité** indique toujours l'état réel et rappelle ces deux
commandes.

### Sur un serveur, c'est pareil — et cela compte davantage

`server.py`, le point d'entrée Railway / Render, n'impose aucun code non plus :
le choix vaut partout. Mais l'URL qu'il sert est **publique**, pas limitée à
un Wi-Fi : toute personne la connaissant peut ouvrir l'interface, lire les
identifiants enregistrés du courtier et passer des ordres réels.

Le démarrage l'inscrit dans le journal de l'hébergeur, pour que l'état se
constate au lieu de se deviner :

```
Aucun code d'acces : l'URL de ce serveur est ouverte a qui la connait, [...]
APP_AUTH=on pour en demander un.
```

Pour protéger un déploiement, il suffit d'ajouter `APP_AUTH=on` aux variables
d'environnement de l'hébergeur — sans toucher une ligne de code.

## Sur téléphone

- **iPhone** : l'écran de démarrage affiche l'adresse Wi-Fi locale
  (ex. `http://192.168.1.20:8765`) → ouvrir dans Safari → Partager →
  « Sur l'écran d'accueil ». L'app s'installe comme une vraie application.
- **Android** : même adresse dans Chrome → menu ⋮ → « Installer
  l'application ». Ou compiler l'APK natif : `build_apk.bat`
  (voir `android/README.md`).

---

## Architecture

```
├── python/
│   ├── agents/            46 agents IA + 46 assistants (préparation/vérification)
│   ├── licence/           comptes, offre Essai / Pro, abonnement
│   │   ├── config.py          SOURCE UNIQUE des limites et des tarifs
│   │   ├── comptes.py         comptes utilisateurs, essai 3 j, sessions
│   │   ├── stripe_paiement.py vérification des paiements (webhook signé)
│   │   ├── gate.py            feature gate : agents, quotas, fonctionnalités
│   │   ├── abonnement.py      état de licence, activation, identité du compte
│   │   ├── verification.py    licence signée Ed25519 (clé PUBLIQUE embarquée)
│   │   └── quota.py           compteur de requêtes, par compte, en base
│   ├── utils/
│   │   ├── auto_trader.py     boucle autonome (analyse → protections → ordre)
│   │   ├── mt5_manager.py     MetaTrader 5 : démarrage portable, connexion,
│   │   │                      exécution (réel + simulation)
│   │   ├── trading_config.py  moteur de performance (levier, plafonds réglables)
│   │   ├── risk_guard.py      plafonds de coupure (kill-switch, exposition)
│   │   ├── market_hours.py    heures de marché (crypto/forex/matières/actions)
│   │   ├── backtest.py        rejoue une stratégie sur l'historique
│   │   ├── moteur_ia.py       routeur IA — Ollama ou Hermès (local)
│   │   ├── ollama_embedded.py moteur IA local n°1 (Ollama)
│   │   ├── hermes_embedded.py moteur IA local n°2 (Hermès + llama.cpp)
│   │   ├── runtime_embarque.py localise les moteurs livrés dans l'exe
│   │   ├── auth.py            code d'accès — désactivé par défaut (APP_AUTH=on)
│   │   ├── notifier.py        alertes webhook (Discord/Slack)
│   │   ├── version.py         version unique (lue du fichier VERSION)
│   │   ├── mise_a_jour.py     socle de mise à jour (désactivé par défaut)
│   │   └── database.py        SQLite (signaux, ordres, portefeuille)
│   └── tests/             suite de tests (14 modules) — python python/tests/run_tests.py
├── backend/main.py        API FastAPI + pages login/setup
├── backend/routes/           licence, comptes, abonnement (routes dédiées)
├── frontend/              tableau de bord (PWA installable)
├── android/               application Android native (WebView)
├── tools/
│   ├── preparer_runtimes.py   télécharge les moteurs À EMBARQUER (avant build)
│   ├── preparer_installeur.py ressources de l'installeur (icône, WebView2)
│   └── generer_icone.py       icône .ico multi-résolutions
├── installer/             installeur Windows — voir installer/README.md
│   ├── agence.iss             script Inno Setup (assistant d'installation)
│   ├── agence.ico             icône de l'application
│   ├── infos_avant.txt        page affichée avant l'installation
│   ├── infos_apres.txt        page affichée après l'installation
│   └── updates/               format du manifeste de mise à jour
├── scripts/
│   └── build_installer.bat    chaîne complète : application + installeur
├── build_installer.bat    raccourci à double-cliquer (appelle scripts/)
├── build_exe.bat          compile l'application seule
├── runtime/               moteurs embarqués (généré, non versionné)
├── dist/                  sortie du build (application + installateur/)
├── RELEASE_NOTES_TRIAL.md note de version de l'essai (limites appliquées)
├── VERSION                numéro de version — source unique
├── agence.spec            build PyInstaller (exe Windows)
└── server.py              point d'entrée serveur (Railway/Render/local)
```

## Qui vote, et qui conseille

Les 45 agents ne votent pas tous sur la direction. L'orchestrateur ne compte
que les votes `BUY`/`SELL` ; les agents de **risque, exécution et reporting**
émettent volontairement des avis `HOLD`/`WATCH`/`ALERT`, qui informent sans
peser sur le sens de l'ordre.

Mesuré sur 300 marchés simulés : **19 agents sur 45** émettent au moins un vote
directionnel, et le quorum de l'orchestrateur (≥ 3 votes du même sens) est
atteint dans ~85 % des cas.

Quatre agents s'abstiennent tant que leur source de données n'est pas
branchée — c'est **délibéré**, et c'est le côté sûr :

| Agent | Donnée manquante | Comportement |
|---|---|---|
| Arbitragiste | prix d'un second instrument | abstention totale |
| Corrélations | séries des symboles corrélés | abstention totale |
| Trader Actualités | vraies dépêches | `WATCH`, confiance plafonnée à 45 |
| Macro-Économique | indicateurs macro courants | `WATCH`, repères figés signalés |

Sans cette abstention, ces agents concluraient à partir de valeurs inventées —
et le volume d'un ordre réel se retrouverait dicté par un tirage aléatoire.

## Moteur de performance (Réglages ⚙️ → Moteur de performance)

Tout ce qui décide de l'agressivité du robot est réglable **dans
l'interface**, prend effet **au cycle suivant** (sans redémarrage) et est
**enregistré** entre deux sessions. Le réglage de l'interface est prioritaire
sur la variable d'environnement correspondante.

| Réglage | Défaut livré | Effet |
|---|---|---|
| **Levier maximum** | 10 | Plafond que les agents ne peuvent jamais dépasser (1 → 10) |
| **Levier courant** | 10 | Notionnel engagé par ordre = levier × équité |
| **Levier piloté par les agents** | activé | Le Chef d'Orchestre l'ajuste à chaque décision : conviction élevée + marché calme → plafond ; volatilité > 5 % → divisé par deux |
| **Objectif de gain / jour** | 80 | Suivi sur l'accueil (réalisé + latent) |
| **Capital de référence** | 100 | Sert à exprimer l'objectif en % par jour |
| **Risque par ordre** | 25 % de l'équité | Perte maximale si le stop est touché |
| **Volume max** | 5 lots | Plafond absolu du volume par ordre |
| **Confiance minimale** | 0 (aucun filtre) | Ignore les décisions sous ce seuil |
| **Délai anti ré-entrée** | 60 s | `0` = ré-entrée immédiate autorisée |
| **Stop-loss / take-profit par défaut** | 2 % / 3 % | Appliqués quand les agents n'en fournissent pas |
| **Spread max** | 2 % | `0` = aucun refus sur le spread |
| **Positions max par symbole** | 3 | Empilement autorisé jusqu'à ce nombre |

Interrupteurs (même écran) : dimensionnement au levier, stop-loss
obligatoire, envoi au lot minimum quand le risque le dépasse, refus si la
perte au stop est incalculable, anti-empilement, respect des heures de
marché, blocage des ordres réels sur prix simulés, arrêt des ouvertures une
fois l'objectif atteint.

> Ces défauts sont **offensifs**, à la demande explicite du propriétaire du
> projet. Un levier de 10 multiplie les gains **et les pertes** dans la même
> proportion : un mouvement de 10 % contre la position efface l'équivalent du
> capital engagé. Aucun réglage ne rend un objectif de gain quotidien
> atteignable — 80 €/jour sur 100 € de capital, c'est **80 % par jour**, un
> rendement qu'aucune stratégie ne soutient dans la durée. Réduisez le levier
> et le risque par ordre pour revenir à un profil prudent.

## Plafonds de coupure (Réglages ⚙️ → Sécurité)

| Protection | Défaut | Variable d'environnement |
|---|---|---|
| Kill-switch perte journalière | −50 % → trading coupé | `PERTE_MAX_JOURNALIERE_PCT` |
| Positions simultanées max | 50 | `MAX_POSITIONS` |
| Exposition max (marge/équité) | 1000 % | `EXPOSITION_MAX_PCT` |
| Garde-fous actifs | oui | `RISK_GUARD_ACTIF` |
| Trailing stop (breakeven à +1R puis suivi) | actif | `TRAILING_ACTIF` |
| Rapport quotidien (webhook) | 22 h | `RAPPORT_HEURE`, `RAPPORT_QUOTIDIEN` |

Décocher « Garde-fous actifs » lève **toutes** les coupures automatiques :
plus rien n'arrête le robot en cas de série perdante.

> **Un réglage fait dans l'interface est toujours prioritaire**, ici comme dans
> le moteur de performance. Les variables d'environnement ne servent que de
> valeur *initiale*, tant que vous n'avez rien réglé vous-même. L'inverse
> s'appliquait avant : un `MAX_POSITIONS=10` oublié dans un vieux `.env`
> reprenait la main au redémarrage suivant, sans rien dire — le plafond bas
> était atteint dès les premières positions et plus aucun ordre ne partait.

Le bouton **🛑 Fermer toutes les positions** de l'accueil clôture tout au prix
du marché en un clic, et propose d'arrêter aussi la boucle de trading (sans
quoi les agents rouvrent au cycle suivant).

Autres réglages : `APP_AUTH=on` (rétablit le code d'accès, voir ci-dessous),
`APP_PASSWORD` (impose un code et l'active), `WEBHOOK_URL` (**https
obligatoire** : le message contient vos ordres et l'état du compte),
`MOTEUR_IA` (`ollama` ou `hermes`), `OLLAMA_MODEL`, `HERMES_MODEL`,
`MT5_AUTO_INSTALL`, `CAPITAL_INITIAL`,
`RETENTION_JOURS` (purge des signaux, 30 j par défaut ; `0` la désactive).

> Un cycle enregistre **46 signaux par symbole** (45 agents + décision
> finale). La base se purge donc toute seule une fois par jour et au
> démarrage ; sans cela, une instance laissée tourner accumulait environ
> 200 000 lignes par jour.

## Moteur IA : Ollama ou Hermès — jamais ailleurs que chez vous

Deux moteurs, **au choix**, changeables à tout moment dans
Réglages ⚙️ → « Moteur IA » (ou à la première configuration, page `/setup`).
Le choix prend effet **au cycle suivant**, sans redémarrage, et est mémorisé
entre deux sessions.

| Moteur | Où tourne l'IA | Coût | Livré avec l'app |
|---|---|---|---|
| **Ollama** | sur votre PC | gratuit | ✅ binaire + modèle |
| **Hermès** | sur votre PC | gratuit | ✅ serveur llama.cpp + modèle GGUF |

Il n'existe **aucun mode distant** : pas de clé API à saisir, rien à
souscrire, et l'application fonctionne hors connexion. Une clé enregistrée par
une version précédente est **effacée du fichier de configuration** au premier
démarrage — l'application ne s'en sert plus, elle n'a aucune raison de
continuer à la stocker. Un test le vérifie, un autre coupe le réseau non local
pendant que les 45 agents analysent : aucune connexion distante ne doit
partir.

**Hermès** est un modèle NousResearch (famille Llama) ré-entraîné pour suivre
les consignes de format à la lettre — utile ici, où l'application demande à
ses 46 assistants des verdicts courts et normalisés (`CONFIRME` / `DOUTE`). Il
tourne sur `llama-server`, le serveur de llama.cpp, qui expose une API
compatible OpenAI sur `127.0.0.1:11435`. Ollama garde son port habituel
(`11434`) : les deux peuvent cohabiter, mais **seul le moteur sélectionné est
démarré** — en lancer deux chargerait deux modèles en mémoire pour rien.

Modèles proposés : `hermes-3-llama-3.2-3b` (~2 Go, rapide, celui embarqué par
défaut) et `hermes-3-llama-3.1-8b` (~4,9 Go, meilleure qualité).

Tout ce qui consulte l'IA passe par `utils/moteur_ia.chat()` — les 46
assistants comme le Chef d'Orchestre. Une IA injoignable ne lève jamais
d'exception : elle rend `None`, et l'appelant retombe sur son texte de
secours, sans interrompre le cycle d'analyse.

## Tout est embarqué (aucune installation)

`build_exe.bat` lance `tools/preparer_runtimes.py` **avant** PyInstaller. Ce
script télécharge une fois pour toutes les moteurs et les modèles dans
`runtime/`, que PyInstaller empaquette ensuite dans l'application :

```
runtime/
  manifest.json                        ce qui est réellement dans cet exe
  ollama/  ollama.exe + models/        moteur IA local n°1, modèle déjà tiré
  hermes/  bin/llama-server + models/*.gguf   moteur IA local n°2
  mt5/     terminal/terminal64.exe     MetaTrader 5 PORTABLE, prêt à l'emploi
           mt5setup.exe                installeur (repli)
```

Au premier lancement, l'application **démarre** tout cela au lieu de
l'installer — MetaTrader 5 compris.

Le terminal embarqué est celui d'**AvaTrade**, pas un MetaTrader générique.
C'est essentiel : un terminal MetaQuotes démarre parfaitement mais ne connaît
**aucun serveur AvaTrade**, donc refuse toute connexion au compte — et
l'utilisateur cherche alors du côté de son mot de passe. Le script le vérifie
deux fois (marque de l'installeur, puis serveurs `.srv` du terminal copié) et
**retire le dossier** plutôt que de livrer un terminal inutilisable.
`--terminal-mt5-generique` permet de passer outre en connaissance de cause.

Si le CDN d'AvaTrade est inaccessible, téléchargez l'installeur depuis
avatrade.fr et pointez-le :
`MT5_SETUP_URL=file:///C:/chemin/avatrade5setup.exe`.

MetaQuotes ne publiant pas d'archive portable, `preparer_runtimes.py` exécute
l'installeur officiel **sur la machine de compilation**, puis recopie le
terminal obtenu dans `runtime/mt5/terminal/`. Ce dossier fonctionne ensuite en
mode `/portable` depuis n'importe où : MetaTrader est donc **réellement déjà
installé** pour l'utilisateur final, qui n'a plus qu'à saisir ses identifiants
AvaTrade. Cela impose de compiler **sous Windows** ; ailleurs, seul
l'installeur est embarqué et l'application le lance une fois au premier
démarrage (`MT5_AUTO_INSTALL=false` pour s'en passer). `--sans-terminal-mt5`
force ce mode allégé.

```bash
python tools/preparer_runtimes.py                 # tout (5 à 10 Go)
python tools/preparer_runtimes.py --sans-hermes   # build allégé
python tools/preparer_runtimes.py --modele-hermes hermes-3-llama-3.1-8b
python tools/preparer_runtimes.py --verifier      # état, sans rien télécharger
```

Le script est **idempotent** (rien n'est retéléchargé) et un composant en
échec n'arrête pas les autres : il est signalé en fin d'exécution, et
l'application le téléchargera à la demande au premier usage — dégradé, mais
fonctionnel. `runtime/` n'est **pas versionné** (plusieurs gigaoctets de
binaires) : il se reconstruit avec la commande ci-dessus.

Variables utiles pour figer une version ou passer par un miroir interne :
`LLAMA_SERVER_URL`, `HERMES_MODEL_URL`, `LLAMA_SERVER_BIN`,
`HERMES_MODEL_PATH`, `MT5_SETUP_URL`, `AGENCE_RUNTIME_DIR`.

## Trading réel (AvaTrade / MetaTrader 5)

L'exécution réelle nécessite **Windows**. Le terminal étant livré dans l'exe,
l'application le **démarre elle-même** au lancement : il ne reste qu'à saisir
vos identifiants AvaTrade (Réglages ⚙️ → « Connexion AvaTrade »). Sur
Linux/serveur, l'app fonctionne en **simulation** (aucun ordre réel).

### Ce qui a été corrigé côté connexion

| Symptôme | Cause | Correction |
|---|---|---|
| « IPC timeout », terminal pourtant ouvert | `mt5.initialize(path=…)` lance le terminal **sans** `/portable` : un SECOND terminal démarrait à côté du portable déjà actif, et MetaTrader refuse la double instance | l'application lance elle-même le terminal embarqué avec `/portable`, puis s'y branche par un `initialize()` nu — le chemin du terminal embarqué n'est plus jamais passé à la librairie |
| Connexion acceptée mais **tout ordre refusé** (code 10027) | « Autoriser le trading automatique » désactivé dans un terminal neuf | l'option est écrite dans `config/common.ini` du terminal embarqué **avant** son premier démarrage (les DLL externes restent, elles, interdites) |
| Reconnexion au démarrage jamais faite | le mode « reprendre le compte déjà ouvert » n'enregistre aucun mot de passe, et le démarrage ne testait que la présence d'identifiants | `mt5_attacher` déclenche la reconnexion au même titre que des identifiants mémorisés |
| « CERTIFICATE_VERIFY_FAILED » au téléchargement de MetaTrader | `urlopen` nu, sans magasin de certificats embarqué ni repli : un antivirus qui inspecte le HTTPS, ou un magasin Windows incomplet, faisait échouer tout téléchargement | `utils/reseau` essaie les magasins l'un après l'autre — celui du système (seul à connaître l'autorité d'un antivirus), puis `certifi` embarqué, puis le défaut. La vérification n'est **jamais** désactivée : ces fichiers sont des programmes que vous allez exécuter |
| Diagnostic MetaTrader vide | le bloc filtrait `get_status()` sur des noms de champs inexistants | le diagnostic est composé des vraies sources : version de la librairie, terminaux détectés, terminal qui répond ou non, AlgoTrading, serveurs connus |

Le bouton **« Démarrer / réparer la connexion »** (Réglages ⚙️ → Trading réel)
relance le terminal embarqué à tout moment, et Réglages ⚙️ → **Diagnostic**
montre l'état complet de MetaTrader.

**Symboles** — l'interface affiche les noms du courtier tels qu'ils
apparaissent dans le Market Watch MetaTrader (`EURUSD`, `USDSEK`, `BTCUSD`).
En interne, l'app utilise le format Yahoo (`EURUSD=X`, `BTC-USD`), qui est la
source des cours, et `resoudre_symbole()` traduit vers le nom du courtier au
moment d'envoyer l'ordre. Paires livrées par défaut : EURUSD, GBPUSD, USDCHF,
USDJPY, AUDUSD, NZDUSD, USDCAD, USDSEK, EURGBP, les cryptos majeures, 8
actions US, 4 indices et 2 matières premières (**GOLD.TR**, **CrudeOIL**).

Pour en ajouter d'autres, complétez `SYMBOLES_FOREX` / `SYMBOLES_CRYPTO` /
`SYMBOLES_ACTIONS` / `SYMBOLES_MATIERES` dans `python/config.py` — **au format
Yahoo**, pas au format courtier : sinon le téléchargement des cours échoue, les
agents travaillent sur des prix simulés et l'auto-trader refuse tout ordre réel.

**Instruments dont le nom courtier ne ressemble pas au ticker** — l'or et le
pétrole en sont le cas type : le Market Watch les nomme `GOLD.TR` et
`CrudeOIL`, alors que les cours se téléchargent sous `GC=F` (COMEX Gold) et
`CL=F` (WTI). Aucune règle de réécriture ne relie les deux, et deviner par la
racine du ticker serait dangereux — `CL` est le symbole de Colgate-Palmolive
chez beaucoup de courtiers. Ces instruments passent donc par deux tables
explicites de `python/config.py` :

| Table | Rôle |
|---|---|
| `NOMS_AFFICHES` | nom montré dans l'interface (`GC=F` → « GOLD.TR ») |
| `ALIAS_COURTIER` | noms cherchés chez le courtier, et **eux seuls** |

Les saisies libres suivent : `GOLD.TR`, `GOLD`, `XAUUSD`, `CrudeOIL`, `WTI` ou
`USOIL` retombent toutes sur le bon ticker de cours. Côté horaires, les
matières premières ont leur propre fenêtre (dimanche 23h → vendredi 21h UTC,
coupure quotidienne 21h-22h UTC) : les traiter comme des actions US les aurait
rendues intradables pendant les sessions asiatique et européenne.

Checklist avant le réel :
1. Plusieurs jours en **compte démo** — observer journal et rapports
2. Vérifier l'indicateur 🛡️ sécurité dans le panneau Trading automatique
3. Passer en réel avec les protections par défaut, volumes minimaux

## En cas d'erreur : Réglages ⚙️ → Diagnostic

Un bandeau rouge « Erreur serveur sur /api/… » nomme désormais sa cause et
porte une **référence** (ex. `réf. 5F38B0`). Réglages ⚙️ → **Diagnostic**
affiche la trace complète correspondante, plus la version, le moteur IA actif,
l'état de la base et de MetaTrader — le tout copiable en un clic.

C'est indispensable sur l'exe Windows : il est compilé **sans console**, donc
tout ce que le serveur écrivait sur la sortie standard disparaissait. Une
panne ne laissait que « Erreur interne du serveur », sans cause ni endroit.
Le détail n'est renvoyé qu'à une **session authentifiée** — un visiteur du
réseau local reçoit un 401 avant même d'atteindre le gestionnaire d'erreurs.

`/api/status`, le battement de cœur interrogé en continu, ne répond plus
jamais 500 : si le Chef d'Orchestre est indisponible, la route rend l'état
partiel avec `statut: "degrade"` et la cause, au lieu de faire virer au rouge
un tableau de bord par ailleurs fonctionnel. Même principe pour
`/api/assistants` : un assistant en erreur ne masque plus les 45 autres.

## API (authentifiée par cookie)

`/api/status` `/api/agents` `/api/assistants` `/api/analyser`
`/api/auto-trader/{status,start,stop}` `/api/mt5/*` `/api/risk/*`
`/api/ia/{statut,moteur}` `/api/ollama/*` `/api/hermes/*` `/api/diagnostic`
`/api/backtest` `/api/performance[/reel]` `/api/mt5/trades`
`/api/notifications/*` `/api/rapport/test` `/api/mise-a-jour`
`/api/licence[/offre,/activer,/rafraichir,/desactiver]`
`/api/compte[/inscription,/connexion,/deconnexion,/disponible]`
`/api/abonnement[/formules,/verifier,/retour,/webhook]` —
documentation interactive sur `/docs` (après connexion).

Trois refus normalisés, que l'interface traduit en trois écrans :

| Code | Corps | Écran |
|---|---|---|
| **401** | `{"compte_requis": true}` | inscription / connexion |
| **402** | `{"abonnement_requis": true}` | mur de paiement (essai écoulé) |
| **402** | `{"pro_requis": true, "feature": …}` | « fonctionnalité Pro » |
| **429** | `{"quota_depasse": true}` | quota d'essai atteint |

Seules `/api/health`, `/api/licence`, `/api/compte/*` et `/api/abonnement/*`
restent ouvertes sans session — un compte suspendu doit pouvoir régulariser.

## Tests

```bash
python python/tests/run_tests.py
```

12 modules : garde-fous, exécution MT5 (faux courtier), protections de
trading réel, agents/assistants, backtest, heures de marché, indicateurs
techniques, performance, fonctionnalités applicatives (notifications,
multi-comptes, export CSV, pré-vol, purge de la base, réactivité du serveur,
persistance des plafonds de sécurité, refus d'ordre réel sans données de
marché), et **moteur IA** (sélection des trois moteurs, routage des
consultations, détection des runtimes embarqués). Ce dernier ne télécharge
rien et ne démarre aucun serveur : il vérifie le routage et la détection,
c'est-à-dire les deux endroits où un second moteur pouvait être ignoré en
silence. Quatre autres couvrent le diagnostic : `/api/status` qui ne tombe
jamais en 500, un assistant cassé qui n'en masque pas 45, une erreur 500 qui
reste consultable, et une page de diagnostic qui survit à un sous-système en
panne.

Le onzième couvre l'**empaquetage Windows** : source unique de version,
base de données écrite hors du dossier d'installation (sinon la première mise
à jour l'efface), raccourcis Bureau et menu Démarrer réellement déclarés,
désinstallation qui ne supprime aucune donnée personnelle sans demander,
dépendance WebView2 traitée, et données locales du développeur jamais
empaquetées dans l'installeur.

Le douzième couvre l'**empaquetage MSIX** (Microsoft Store). Il défend une
chaîne que personne ne peut vérifier ailleurs que sous Windows : le manifeste
se remplit depuis la même source de version que le reste, les icônes citées
existent aux dimensions exactes qu'exige Windows, les scripts PowerShell
restent lisibles par Windows PowerShell 5.1, aucune clé privée ne peut entrer
dans le dépôt, le pipeline ne publie rien avant d'avoir validé, et le
conteneur MSIX — dossier d'installation en **lecture seule** — est pris en
compte par l'application.

Lancés automatiquement en CI à chaque push (`.github/workflows/tests.yml`).
`.github/workflows/build-exe.yml` va plus loin : il compile l'exe, **vérifie
qu'il démarre et répond** sur `/api/health`, puis génère l'installeur avec
Inno Setup et publie les deux en artefacts.
`.github/workflows/windows-msix.yml` fait de même pour le paquet MSIX, et y
ajoute l'épreuve décisive : il **installe le paquet, le lance par le menu
Démarrer, vérifie la version servie, puis le désinstalle** avant toute
publication.

Deux tests sont des **contrôles statiques** du code plutôt que des tests
d'exécution, parce qu'ils défendent des invariants qu'un test fonctionnel ne
peut pas atteindre de façon fiable :

- tout appel à la librairie MetaTrader 5 est sérialisé sous `_mt5_lock`
  (`with self._mt5_lock` ou méthode décorée `@_sous_verrou`) — la connexion
  terminal est unique et non thread-safe, et trois threads l'utilisent en
  parallèle ;
- aucun handler `onclick="..."` du tableau de bord n'est construit par
  interpolation de chaîne — le navigateur y décode les entités HTML *avant*
  d'analyser le JavaScript, donc `esc()` n'y protège de rien (utiliser
  `data-*` + `addEventListener`).

## Déploiement sur un serveur (optionnel)

`railway.toml` / `render.yaml` / `Procfile` fournis. Sur un serveur distant :
simulation uniquement (pas de MetaTrader). L'IA y reste locale — il faut donc
qu'Ollama ou Hermès tourne **sur ce serveur** ; sinon les agents fonctionnent
en mode règles, avec la synthèse textuelle de secours. Le cas normal de cette
application reste le poste de l'utilisateur.
