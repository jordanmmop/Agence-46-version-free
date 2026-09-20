# Empaquetage Windows MSIX — procédure complète

Ce document décrit toute la chaîne, de la ligne de code au paquet prêt pour le
Microsoft Store. Il est écrit pour être relu dans six mois sans rien connaître
du contexte : chaque commande est donnée en entier, et chaque décision est
justifiée à l'endroit où elle se prend.

| | |
|---|---|
| Paquet produit | `dist\msix\AgenceNumerique-<version>-x64.msix` |
| Commande unique | `build_msix.bat -Sign` |
| Pipeline | `.github/workflows/windows-msix.yml`, déclenché par un tag `v*` |
| Prêt pour le Store | **partiellement** — voir la [section N](#n--publication-microsoft-store) |

---

## Sommaire

- [A. Architecture](#a--architecture)
- [B. Prérequis](#b--prérequis)
- [C. Build local](#c--build-local)
- [D. Création du MSIX](#d--création-du-msix)
- [E. Certificat de test](#e--certificat-de-test)
- [F. Installation locale](#f--installation-locale)
- [G. Désinstallation](#g--désinstallation)
- [H. Validation](#h--validation)
- [I. GitHub Actions](#i--github-actions)
- [J. GitHub Secrets](#j--github-secrets)
- [K. Versioning](#k--versioning)
- [L. GitHub Releases](#l--github-releases)
- [M. Microsoft Partner Center](#m--microsoft-partner-center)
- [N. Publication Microsoft Store](#n--publication-microsoft-store)
- [O. Mise à jour de l'application](#o--mise-à-jour-de-lapplication)
- [P. Dépannage](#p--dépannage)

---

## A. Architecture

### Ce qu'est réellement cette application

Une application **Python** de bureau, pas une application web ni une
application Electron :

| Couche | Technologie | Rôle |
|---|---|---|
| Fenêtre | pywebview + WebView2 | fenêtre native Windows |
| Serveur | FastAPI + uvicorn | API locale, **processus séparé** |
| Interface | HTML / CSS / JavaScript **sans framework** | tableau de bord |
| Métier | Python (46 agents, pandas, numpy, scipy) | analyse et décision |
| Trading | MetaTrader 5 (extension C) | exécution réelle des ordres |
| IA | Ollama ou Hermès (llama.cpp) | moteurs locaux, hors du paquet |

Il n'y a **ni `package.json`, ni Node.js, ni npm** dans ce dépôt : le
JavaScript du frontend est servi tel quel, sans étape de compilation. Les
commandes d'empaquetage sont donc des scripts PowerShell et des `.bat`, à
l'image de `build_exe.bat` et `build_installer.bat` qui existaient déjà.

### La chaîne de compilation

```
  code source
      |
      |  build_exe.bat  ->  PyInstaller (agence.spec)
      v
  dist\AgenceNumerique\            application Windows autonome
      |  AgenceNumerique.exe         (runtime Python + DLL + .pyd + frontend)
      |  _internal\...
      |
      +---> installer\agence.iss  ->  AgenceNumerique-Setup-<v>.exe   (Inno Setup)
      |
      +---> packaging\msix\       ->  AgenceNumerique-<v>-x64.msix    (MSIX)
```

**Le MSIX ne recompile rien.** Il emballe le dossier que PyInstaller vient de
produire, avec un manifeste et des icônes. C'est délibéré : deux chaînes de
compilation parallèles divergent en quelques mois, et le paquet publié
finirait par ne plus correspondre à l'exécutable testé.

### Pourquoi `makeappx` et pas autre chose

| Outil | Pourquoi il n'a pas été retenu |
|---|---|
| Electron Builder / Forge | il n'y a pas d'Electron ici, ni même de Node |
| MSIX Packaging Tool (interface) | interactif : incompatible avec une CI |
| Windows Application Packaging Project | exige un `.sln` Visual Studio et MSBuild |
| PyInstaller seul | produit un dossier, pas un paquet installable |
| **`makeappx.exe` (SDK Windows)** | **retenu** |

`makeappx` est l'outil officiel, scriptable, présent sur les runners GitHub
`windows-latest`, et il prend en entrée exactement ce que nous avons déjà : un
dossier de fichiers. Il est piloté par `packaging/msix/scripts/build-msix.ps1`.

### Multi-architecture : x64 uniquement, et c'est un choix

Un seul paquet **x64**, ni `x86`, ni `arm64`, ni `.msixbundle`.

- **x86 (32 bits)** : la bibliothèque `MetaTrader5` n'existe qu'en 64 bits.
  Sans elle, aucun trading réel — c'est-à-dire l'essentiel de l'application.
- **ARM64** : il faudrait un Python ARM64, un PyInstaller ARM64, et surtout des
  roues ARM64 pour `MetaTrader5`, `scipy` et `scikit-learn`. `MetaTrader5`
  n'en publie pas. Construire cette cible produirait un paquet qui s'installe
  et ne fonctionne pas.
- **`.msixbundle`** : un bundle sert à regrouper *plusieurs* architectures ou
  plusieurs jeux de ressources. Avec une seule architecture et des libellés
  écrits en clair dans le manifeste, il n'apporterait qu'un niveau
  d'emballage supplémentaire.

Un paquet x64 s'installe et s'exécute sur **Windows 11 ARM64** par émulation :
les machines ARM ne sont donc pas exclues, elles utilisent la même version.

### Dépendances natives embarquées

PyInstaller place tout dans `_internal\` ; le paquet MSIX les contient donc :

- `python311.dll` — le runtime Python : **Python n'a pas à être installé** ;
- `vcruntime140.dll`, `vcruntime140_1.dll` — runtime C de Microsoft ;
- une centaine de `.pyd` — extensions compilées de numpy, pandas, scipy,
  scikit-learn, `MetaTrader5` ;
- `certifi` — magasin de certificats HTTPS, sans lequel tous les
  téléchargements échouent sur `CERTIFICATE_VERIFY_FAILED` ;
- le frontend HTML/CSS/JS et tout l'arbre `python/`.

`validate-msix.ps1` vérifie la présence de chacun dans le paquet final.

**Une seule dépendance reste externe : Microsoft Edge WebView2.** C'est le
moteur de la fenêtre native. Il est présent d'origine sur Windows 11 et sur un
Windows 10 à jour. S'il manque, l'application ne plante pas : elle s'ouvre dans
le navigateur par défaut (repli déjà implémenté dans `app_window.py`).
Contrairement à l'installeur Inno Setup, un paquet MSIX **ne peut pas** installer
WebView2 lui-même — un paquet n'a pas le droit d'installer un autre logiciel.

### Le dossier d'installation est en lecture seule

C'est **la** différence entre un MSIX et l'installeur Inno Setup, et elle
gouverne tout le reste. Le dossier d'un paquet MSIX n'est pas seulement
protégé par les droits d'accès : il est monté en lecture seule pour tout le
monde, y compris un processus administrateur.

L'application y survit sans modification, parce qu'elle écrivait déjà toutes
ses données ailleurs :

| Donnée | Emplacement |
|---|---|
| Configuration (code d'accès s'il est activé) | `%USERPROFILE%\.agence_financiere\config.json` |
| Base de trading (SQLite) | `%USERPROFILE%\.agence_financiere\data\agence.db` |
| Modèles d'IA téléchargés | `%USERPROFILE%\.agence_financiere\ollama`, `...\hermes` |
| Identifiants MetaTrader | `%USERPROFILE%\.agence_financiere` |
| Journaux | `%USERPROFILE%\.agence_financiere\serveur.log`, `app_error.log` |

Ces chemins ne sont **pas** sous `AppData` : Windows ne les redirige donc pas
vers `...\Packages\<famille>\LocalCache`. Une installation MSIX et une
installation Inno Setup partagent exactement les mêmes données — passer de
l'une à l'autre conserve l'historique de trading.

Un seul composant ne survit pas au conteneur : le **terminal MetaTrader 5
portable**. Lancé avec `/portable`, il écrit ses profils et son
`config\common.ini` juste à côté de son exécutable. `python/utils/msix.py`
détecte le cas et l'écarte, ce qui fait retomber l'application sur la
détection d'un MetaTrader installé normalement.

---

## B. Prérequis

Sur la machine de compilation (Windows 10 1809 ou plus récent) :

```powershell
winget install -e --id Python.Python.3.11
winget install -e --id Microsoft.WindowsSDK.10.0.22621
```

Le **SDK Windows** fournit `makeappx.exe` et `signtool.exe`. Les scripts les
retrouvent tout seuls dans `C:\Program Files (x86)\Windows Kits\10\bin\...` :
il n'y a pas de `PATH` à modifier.

Pour **installer** un paquet signé par un certificat de test, Windows doit
autoriser les applications hors Store :

> Paramètres → Système → Pour les développeurs → **Mode développeur**

Rien d'autre n'est requis : `build-msix.ps1` installe lui-même les dépendances
Python (via `build_exe.bat`).

Vérifier que tout est en place, sans rien construire :

```powershell
.\packaging\msix\scripts\build-msix.ps1 -DryRun
```

---

## C. Build local

```bat
build_msix.bat
```

Ce double-clic enchaîne : dépendances Python → PyInstaller → contenu du
paquet → manifeste → `makeappx`. Comptez 10 à 15 minutes la première fois.

Les options se transmettent telles quelles au script PowerShell :

```powershell
.\packaging\msix\scripts\build-msix.ps1                    # paquet non signé
.\packaging\msix\scripts\build-msix.ps1 -Sign              # + signature de test
.\packaging\msix\scripts\build-msix.ps1 -SkipAppBuild      # réutilise dist\AgenceNumerique
.\packaging\msix\scripts\build-msix.ps1 -DryRun            # vérifie seulement
.\packaging\msix\scripts\build-msix.ps1 -Configuration Debug
.\packaging\msix\scripts\build-msix.ps1 -Revision 3        # essais locaux répétés
```

### Debug et Release

| | `Release` (défaut) | `Debug` |
|---|---|---|
| Nom du fichier | `...-x64.msix` | `...-x64-Debug.msix` |
| Dossier de préparation | effacé | **conservé** (`dist\msix\layout\`) |
| Contrôles Store | stricts | signalés sans bloquer |

**`Release` est ce qui part au Microsoft Store.** `Debug` sert à inspecter le
contenu exact du paquet quand quelque chose manque.

### `--dry-run`

`-DryRun` vérifie la configuration complète — outils du SDK, identité, version,
génération et validité du manifeste, présence des icônes — **sans produire ni
signer de paquet**. Il rend 0 si un vrai build aboutirait. C'est ce que la CI
lance en premier, pour ne pas découvrir un manifeste invalide après quinze
minutes de compilation.

---

## D. Création du MSIX

### Contenu du paquet

```
AgenceNumerique-4.0.0.0-x64.msix
├── AppxManifest.xml            généré depuis le gabarit
├── Assets\                     17 icônes
└── app\                        sortie PyInstaller, telle quelle
    ├── AgenceNumerique.exe
    └── _internal\...
```

L'application est dans un sous-dossier `app\` plutôt qu'à la racine : la sortie
PyInstaller contient des centaines de fichiers, et un `Assets` ou un
`AppxManifest.xml` qui s'y trouverait un jour écraserait silencieusement celui
du paquet.

### Le manifeste

`packaging/msix/AppxManifest.xml` est un **gabarit** : `build-msix.ps1` y
remplace les jetons `{{...}}` par les valeurs de `msix.config.json`.

| Champ | Valeur actuelle | Définitive ? |
|---|---|---|
| `Identity/Name` | `AgenceNumeriqueFinanciere.Agence46.TEST` | **non — Partner Center** |
| `Identity/Publisher` | `CN=Agence Numerique Financiere (TEST), ...` | **non — Partner Center** |
| `Identity/Version` | lu dans `VERSION` → `4.0.0.0` | oui, automatique |
| `ProcessorArchitecture` | `x64` | oui |
| `PublisherDisplayName` | `Agence Numérique Financière` | **à confirmer** |
| `Application/Id` | `AgenceNumerique` | oui |
| `Executable` | `app\AgenceNumerique.exe` | oui |
| `EntryPoint` | `Windows.FullTrustApplication` | oui, obligatoire |

Les deux premières portent la mention **TEST** exprès : un paquet marqué ainsi
s'installe localement et ne sera jamais accepté par le Store. Rien ne
distinguerait sinon un paquet d'essai d'un paquet publiable — et l'erreur se
découvrirait après le téléversement.

`ResourceId` n'apparaît pas : cet attribut n'existe que dans les paquets de
*ressources* d'un `.msixbundle`. Nous livrons un paquet unique.

### Capacités déclarées

| Capacité | Pourquoi |
|---|---|
| `runFullTrust` | **obligatoire** pour tout exécutable Win32 empaqueté |
| `internetClient` | cours de marché (yfinance), manifeste de mise à jour |
| `internetClientServer` | le serveur local accepte les connexions |
| `privateNetworkClientServer` | consultation depuis un téléphone du même Wi-Fi |

`runFullTrust` est une capacité **restreinte** : elle déclenche une revue
manuelle à la soumission. C'est le cas normal d'une application de bureau, et
il faut en justifier l'usage (voir [section M](#m--microsoft-partner-center)).

`python/tests/test_msix.py` refuse toute capacité hors de cette liste : chaque
capacité superflue allonge la revue et s'affiche à l'utilisateur.

### Icônes

Les 17 PNG de `packaging/msix/Assets/` sont **versionnés**, comme
`installer/agence.ico` — la compilation ne doit pas dépendre de Pillow. Pour
les régénérer après un changement de logo :

```bash
python tools/generer_assets_msix.py
python tools/generer_assets_msix.py --verifier    # contrôle sans réécrire
```

Windows ne redimensionne pas : il lit des fichiers aux dimensions exactes.
Une image absente fait échouer `makeappx` ; une image à la mauvaise taille
passe la compilation et donne une vignette vide dans le menu Démarrer. Les
tests vérifient donc chaque dimension.

---

## E. Certificat de test

```powershell
# PowerShell ADMINISTRATEUR
.\packaging\msix\scripts\create-test-certificate.ps1
```

Le script :

1. lit le champ `Publisher` du manifeste et en fait le **sujet** du certificat ;
2. génère un certificat auto-signé de signature de code (RSA 2048, SHA-256,
   valable 3 ans) ;
3. exporte la clé privée (`.pfx`) et la partie publique (`.cer`) dans
   `dist\msix\certificat\` ;
4. installe la partie publique dans `Cert:\LocalMachine\TrustedPeople` — le
   magasin que Windows consulte pour autoriser un MSIX hors Store.

Sans droits administrateur, les étapes 1 à 3 se font quand même et le script
affiche la commande exacte à lancer en administrateur.

> **Le sujet du certificat doit être identique au champ `Publisher` du
> manifeste, au caractère près.** C'est pourquoi le script le lit dans la
> configuration au lieu de le demander : une différence d'un espace produit un
> paquet signé que Windows refuse ensuite d'installer, avec un message qui ne
> cite ni l'un ni l'autre.

Pour tout retirer (magasins et fichiers) :

```powershell
.\packaging\msix\scripts\create-test-certificate.ps1 -Supprimer
```

### Ce certificat n'est pas un certificat de production

Il ne vaut que sur les machines où il a été explicitement approuvé. Il ne
permet pas de distribuer l'application, et **le Microsoft Store n'en a pas
besoin** : le Store re-signe lui-même les paquets publiés.

### Signature

```powershell
.\packaging\msix\scripts\sign-msix.ps1
```

Le script choisit son certificat dans cet ordre : `-Certificat` → secret
`MSIX_CERTIFICATE_BASE64` → certificat de test local. Avant de signer, il
compare le sujet du certificat au `Publisher` du manifeste et **refuse** en
cas de différence, en affichant les deux valeurs.

La signature est horodatée (`timestamp.digicert.com`) : sans horodatage, elle
deviendrait invalide le jour où le certificat expire, même chez un utilisateur
qui a déjà installé l'application. Si le serveur d'horodatage est injoignable,
le script signe sans, **en le disant**.

---

## F. Installation locale

```powershell
.\packaging\msix\scripts\install-test.ps1
```

Le script reproduit le parcours d'un utilisateur :

```
MSIX → installation → activation par le menu Démarrer → le serveur répond
     → la version servie est vérifiée → fermeture → (désinstallation)
```

L'activation passe par `shell:AppsFolder\<famille>!<application>`, c'est-à-dire
le même chemin que le menu Démarrer. Lancer directement l'exécutable depuis le
dossier d'installation testerait les *fichiers*, pas le *paquet* : un manifeste
mal formé ou un identifiant d'application erroné passeraient inaperçus.

Le serveur choisit son port tout seul à partir de 8765 ; le script balaie donc
la plage 8765-8784 sur `/api/health` et compare la version servie au fichier
`VERSION`.

```powershell
.\packaging\msix\scripts\install-test.ps1 -Desinstaller       # test complet
.\packaging\msix\scripts\install-test.ps1 -SansLancement      # sans session graphique
.\packaging\msix\scripts\install-test.ps1 -DelaiDemarrage 240
.\packaging\msix\scripts\install-test.ps1 -ModeLancement MenuDemarrer   # exige l'activation
```

**Machines sans session interactive.** Une activation par le shell demande un
bureau ouvert, ce qu'un runner d'intégration continue n'a pas toujours. En
mode `Auto` (défaut), le script essaie d'abord l'activation, puis bascule sur
l'exécutable **installé** lancé avec `--serveur-interne` — ce qui vérifie tout
de même que les fichiers du paquet installé fonctionnent. Le bilan indique
lequel des deux chemins a servi :

```
    Lancement       : OK  (menu Demarrer (activation du paquet))
    Lancement       : OK  (executable installe (--serveur-interne))
```

`-ModeLancement MenuDemarrer` force l'activation et échoue si elle n'aboutit
pas : à utiliser sur une machine de bureau, où l'absence d'activation est un
vrai défaut et non une contrainte d'environnement.

Installation manuelle, sans script :

```powershell
Add-AppxPackage -Path .\dist\msix\AgenceNumerique-4.0.0.0-x64.msix
```

---

## G. Désinstallation

```powershell
.\packaging\msix\scripts\uninstall-test.ps1
```

ou par l'interface Windows : Paramètres → Applications → Applications
installées → Désinstaller.

**Les données personnelles ne sont pas touchées.** Elles vivent dans
`%USERPROFILE%\.agence_financiere`, hors du paquet — historique de trading,
réglages, identifiants du courtier, modèles d'IA (plusieurs gigaoctets, longs
à retélécharger). Pour les supprimer aussi :

```powershell
.\packaging\msix\scripts\uninstall-test.ps1 -SupprimerDonnees
```

Une confirmation est demandée, sauf avec `-Confirmer`.

---

## H. Validation

```powershell
.\packaging\msix\scripts\validate-msix.ps1
```

Neuf familles de contrôles, sans rien installer (le script ouvre le `.msix`
comme l'archive ZIP qu'il est) :

| # | Contrôle |
|---|---|
| 1 | paquet présent, lisible, d'une taille plausible |
| 2 | manifeste présent, bien formé, identité/version/architecture/capacités conformes |
| 3 | version cohérente avec le fichier `VERSION` |
| 4 | architecture supportée |
| 5 | exécutable déclaré **réellement présent**, `python3xx.dll`, `vcruntime140`, `.pyd`, frontend, backend, `python/`, `VERSION` |
| 6 | les icônes référencées par le manifeste existent, variantes `altform-unplated` comprises |
| 7 | signature valide et signataire identique au `Publisher` |
| 8 | pré-contrôles Microsoft Store (identité de test, révision, capacités restreintes) |
| 9 | WACK et installation réelle, sur demande |

Trois verdicts, jamais mélangés : `[OK]`, `[!]` (réserve — le paquet
fonctionne), `[ECHEC]` (code de sortie non nul).

Contrôles approfondis :

```powershell
.\packaging\msix\scripts\validate-msix.ps1 -AvecInstallation -AvecWack
```

`-AvecWack` lance le **Windows App Certification Kit**, qui reproduit les
contrôles du Store. Il s'agit d'un composant optionnel du SDK Windows ; s'il
n'est pas installé, le script le signale et continue — Partner Center fera ces
contrôles à la soumission.

### Ce qui ne peut être vérifié que sous Windows

| Contrôle | Pourquoi |
|---|---|
| Construction (`makeappx`) | outil Windows uniquement |
| Signature (`signtool`, `Get-AuthenticodeSignature`) | API de certificats Windows |
| Installation, lancement, désinstallation | `Add-AppxPackage` / `Remove-AppxPackage` |
| WACK | composant Windows |

Tout le reste — manifeste, identité, version, icônes, absence de secret,
cohérence du pipeline — est vérifié à chaque commit par
`python/tests/test_msix.py`, y compris sur un runner Linux.

---

## I. GitHub Actions

`.github/workflows/windows-msix.yml`, sur `windows-latest`.

**Déclenchement**

| Événement | Effet |
|---|---|
| tag `v1.2.3` | paquet **et** GitHub Release |
| lancement manuel (`workflow_dispatch`) | paquet seul, choix Release/Debug |

**Étapes** — chacune arrête le pipeline en cas d'échec :

1. checkout ;
2. Python 3.11 avec cache pip ;
3. dépendances (`requirements.txt`, PyInstaller, pywebview) ;
4. **suite de tests complète** — inutile d'empaqueter un code qui échoue ;
5. **`build-msix.ps1 -DryRun`** — configuration vérifiée en quelques secondes ;
6. build application + MSIX ;
7. certificat : celui des secrets, sinon un certificat de test créé à la volée ;
8. signature ;
9. **validation** du paquet ;
10. **installation, lancement, vérification de `/api/health`, désinstallation** ;
11. artefact `AgenceNumerique-MSIX` (`.msix` + `SHA256SUMS.txt`) ;
12. artefact du certificat **public** (`.cer`) si c'est un certificat de test ;
13. GitHub Release, **uniquement sur un tag**.

La release est créée après la validation et le test d'installation : un paquet
qui ne s'installe pas ne peut pas atteindre les utilisateurs. `test_msix.py`
vérifie cet ordre.

**Cache** : seul le cache pip d'`actions/setup-python` est utilisé. Il ne
change pas les versions installées (elles restent celles des `requirements`) :
la reproductibilité du build est intacte.

---

## J. GitHub Secrets

**Aucun secret n'est nécessaire.** Sans eux, le pipeline fabrique un certificat
de test à la volée et signe avec — le paquet reste valide pour le Store, qui
re-signe lui-même.

Pour signer avec un vrai certificat d'éditeur (distribution **hors** Store) :

| Secret | Contenu |
|---|---|
| `MSIX_CERTIFICATE_BASE64` | le `.pfx` encodé en base64 |
| `MSIX_CERTIFICATE_PASSWORD` | son mot de passe |

Créer `MSIX_CERTIFICATE_BASE64` :

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\chemin\editeur.pfx")) | Set-Clipboard
```

Puis, sur GitHub : **Settings → Secrets and variables → Actions → New
repository secret**. Coller la valeur, nommer le secret exactement comme
ci-dessus.

> Ces secrets n'existent pas tant que vous ne les créez pas. Le workflow est
> écrit pour fonctionner sans eux : il teste leur présence (`if
> ($env:MSIX_CERTIFICATE_BASE64)`) au lieu de la supposer.

### Variables d'identité (non secrètes)

Pour produire un paquet publiable sans modifier le dépôt : **Settings →
Secrets and variables → Actions → Variables**.

| Variable | Valeur |
|---|---|
| `MSIX_IDENTITY_NAME` | `Package/Identity/Name` donné par Partner Center |
| `MSIX_PUBLISHER` | `Package/Identity/Publisher`, un `CN=<GUID>` |
| `MSIX_PUBLISHER_DISPLAY_NAME` | nom d'éditeur affiché dans le Store |

Renseignées, elles priment sur `msix.config.json`. Elles fonctionnent aussi en
local :

```powershell
$env:MSIX_IDENTITY_NAME = "12345Editeur.AgenceNumeriqueFinanciere"
$env:MSIX_PUBLISHER     = "CN=A1B2C3D4-0000-0000-0000-9876543210FF"
.\packaging\msix\scripts\build-msix.ps1
```

### Ce qui ne doit JAMAIS entrer dans le dépôt

Clé privée (`.pfx`, `.p12`, `.key`, `.pem`, `.snk`), mot de passe, jeton,
identifiant Microsoft, certificat de signature réel.

Deux barrières indépendantes : le certificat de test est écrit sous `dist/`
(déjà ignoré), **et** `.gitignore` refuse ces extensions. Une clé privée
publiée par mégarde ne se retire pas de l'historique d'un dépôt public — il
faut révoquer le certificat.

Les certificats **publics** (`.cer`, `.crt`) ne sont pas ignorés : ils ne
contiennent aucun secret et doivent pouvoir accompagner une release.

---

## K. Versioning

### Une seule source : le fichier `VERSION`

```
VERSION  (« 4.0.0 »)
   |
   +--> python/utils/version.py     l'application (/api/health, /api/diagnostic)
   +--> agence.spec                 ressource « version » de l'exe Windows
   +--> installer/agence.iss        assistant Inno Setup, « Applications installées »
   +--> packaging/msix/             Identity/Version du manifeste  <-- ajouté
```

Aucun de ces fichiers ne recopie le numéro : ils le lisent. Un numéro recopié
finit toujours par diverger, et c'est celui qu'affiche Windows après une mise à
jour qui devient faux.

### Format MSIX : `MAJEUR.MINEUR.BUILD.RÉVISION`

`4.0.0` devient `4.0.0.0`. La conversion est faite par `ConvertTo-VersionMsix`
(`common.ps1`), qui applique exactement la même règle que
`utils.version.version_windows()` — y compris pour les suffixes non numériques
(`4.1.0-rc1` → `4.1.0.0`).

> **La révision doit rester `0`.** Le Microsoft Store se la réserve et
> l'attribue lui-même : un paquet soumis avec une révision non nulle est
> refusé. `-Revision N` n'existe que pour réinstaller localement par-dessus un
> paquet de même version ; `validate-msix.ps1` le signale.

### Publier une nouvelle version

```bash
# 1. le numéro, à un seul endroit
echo "4.1.0" > VERSION

# 2. commit
git add VERSION
git commit -m "Version 4.1.0"
git push

# 3. tag — c'est lui qui déclenche tout
git tag v4.1.0
git push origin v4.1.0
```

GitHub Actions produit alors `AgenceNumerique-4.1.0.0-x64.msix`, le valide,
l'installe, le désinstalle, et crée la release.

> Le tag (`v4.1.0`) et le fichier `VERSION` (`4.1.0`) doivent concorder. Le
> paquet prend sa version du **fichier**, jamais du tag : un tag erroné
> produirait une release dont le nom ne correspond pas au contenu.

---

## L. GitHub Releases

Chaque tag `v*` produit :

```
AgenceNumerique-4.1.0.0-x64.msix
SHA256SUMS.txt
```

`SHA256SUMS.txt` est au format `sha256sum` :

```
6e3f...b91c  AgenceNumerique-4.1.0.0-x64.msix
```

Vérification par l'utilisateur :

```powershell
Get-FileHash .\AgenceNumerique-4.1.0.0-x64.msix -Algorithm SHA256
```

```bash
sha256sum -c SHA256SUMS.txt      # Linux, macOS, WSL
```

Le fichier est aussi produit en local par `build-msix.ps1`, dans `dist\msix\`.

---

## M. Microsoft Partner Center

Ce qui **doit** être fait à la main — rien de tout cela n'est automatisable, et
aucune de ces étapes ne peut être devinée depuis le dépôt.

### 1. Compte développeur

<https://partner.microsoft.com/dashboard> — frais d'inscription uniques
(environ 19 € pour un particulier, 99 € pour une société). Un compte société
demande une vérification d'identité de plusieurs jours.

### 2. Réserver le nom de l'application

Tableau de bord → **Créer une application** → saisir le nom. Le nom réservé
détermine `Identity/Name`.

### 3. Relever les trois valeurs d'identité

Application → **Gestion des produits → Identité du produit**. Reporter dans
`packaging/msix/msix.config.json` (ou dans les variables correspondantes) :

| Partner Center | Clé de configuration | Variable |
|---|---|---|
| `Package/Identity/Name` | `IdentityName` | `MSIX_IDENTITY_NAME` |
| `Package/Identity/Publisher` | `Publisher` | `MSIX_PUBLISHER` |
| `Package/Properties/PublisherDisplayName` | `PublisherDisplayName` | `MSIX_PUBLISHER_DISPLAY_NAME` |

> **`PublisherDisplayName` est le nom de votre COMPTE d'éditeur, pas celui de
> l'application.** C'est le piège de cette page : les deux champs se
> ressemblent, et Partner Center ne refuse le paquet qu'**après** le
> téléversement complet. Voir la [section P](#p--dépannage).

**Préférez écrire les valeurs dans le fichier** plutôt que de les passer en
variables : une variable oubliée produit un paquet refusé, et le message
d'erreur ne dit pas laquelle manquait. Si vous aviez défini `MSIX_IDENTITY_NAME`
ou `MSIX_PUBLISHER` dans votre session, **effacez-les** — elles priment sur le
fichier et masqueraient la configuration réelle.

Valeurs en place dans `msix.config.json`, relevées le 12 septembre 2026 :

| Champ | Valeur |
|---|---|
| `Identity/Name` | `Muller.J.Agence46` |
| `Identity/Publisher` | `CN=82F81A79-20FC-465D-ACDA-2ABA88417132` |
| `PublisherDisplayName` | `Muller.J` |
| ID Store | `9NLTGFR2BTSP` |

> Le `Publisher` doit **aussi** être le sujet du certificat de signature.
> `create-test-certificate.ps1` le lit dans ce fichier : régénérez le
> certificat après avoir changé cette valeur, sinon la signature ne
> correspondra plus au manifeste.

### 3 bis. Réserver le nom affiché de l'application

`DisplayName` doit correspondre à un nom **réservé** dans Partner Center —
Application → **Gestion des produits → Gérer les noms d'application**.

Le nom du produit et le nom affiché par l'application n'ont aucune raison
d'être identiques : réserver un nom supplémentaire prend quelques secondes et
ne coûte rien. C'est plus simple que de renommer l'application partout.

Puis reconstruire. Vérification :

```powershell
.\packaging\msix\scripts\validate-msix.ps1
```

Le bloc « Pré-contrôles Microsoft Store » affiche désormais les quatre valeurs
côte à côte, ce qui permet de les relire d'un coup avant de téléverser :

```
    [OK] identite de publication (aucune mention TEST)
         Identity/Name ................. 12345Editeur.AgenceNumeriqueFinanciere
         Identity/Publisher ............ CN=A1B2C3D4-0000-0000-0000-9876543210FF
         PublisherDisplayName .......... Muller.J.
         DisplayName (nom a reserver) .. Agence Numérique Financière
    [OK] PublisherDisplayName distinct du nom de l'application
    [OK] Publisher au format Partner Center (CN=<GUID>)
```

### 4. Métadonnées de la fiche Store

À fournir dans Partner Center, aucune ne vit dans le dépôt :

- description courte et longue, nom affiché ;
- **catégorie** — « Finance » ou « Entreprise » ;
- **classification par âge** — questionnaire IARC. Une application de trading
  déclenche des questions sur les contenus financiers et les jeux d'argent ;
  répondre honnêtement (il ne s'agit pas de jeux d'argent, mais de trading réel
  sur compte de courtage) ;
- **captures d'écran** — 1366×768 minimum, au moins une ;
- icône du Store — `packaging/msix/Assets/StoreLogo.png` sert de base ;
- **politique de confidentialité** — URL **obligatoire** : l'application traite
  des identifiants de courtier et des données financières ;
- coordonnées de support ;
- marchés, prix, planification de sortie ;
- justification de la capacité restreinte **`runFullTrust`** : « application de
  bureau Win32 existante, empaquetée ; exécute un interpréteur Python embarqué,
  un serveur HTTP local et pilote le terminal MetaTrader 5 de l'utilisateur ».

### 5. Mentions légales

Une application susceptible de passer des ordres réels sur un compte de
courtage relève d'exigences propres à chaque marché. Les textes existants
(`installer/infos_avant.txt`, affiché avant l'installation) sont un point de
départ, pas un avis juridique.

---

## N. Publication Microsoft Store

### État réel : partiellement prêt

Ce que ce dépôt produit maintenant : un paquet MSIX **techniquement conforme**,
validé, installable, testé automatiquement à chaque tag.

Ce qui **empêche encore une soumission**, par ordre d'importance :

#### 1. L'application télécharge et exécute des binaires tiers — bloquant

Au premier lancement, sans moteur embarqué, l'application télécharge et lance :

- **Ollama** (`github.com/ollama/ollama`) puis ses modèles ;
- **Hermès** (`llama-server` + un modèle GGUF) ;
- l'**installeur MetaTrader 5** (`mt5setup.exe`), qui installe un logiciel tiers.

La politique du Store (10.2.x) interdit à une application de télécharger ou
d'installer du code exécutable en dehors du mécanisme de mise à jour du Store.
Les trois cas y tombent, et le dernier — lancer l'installeur d'un autre
logiciel — est le plus net.

Trois issues possibles, à arbitrer :

| Option | Conséquence |
|---|---|
| Embarquer les moteurs dans le paquet | paquet de 5 à 10 Go ; et le terminal MetaTrader portable **ne peut pas** fonctionner en lecture seule |
| Publier une édition Store sans IA locale ni installation de MT5 | conforme ; l'utilisateur installe MetaTrader lui-même, l'application le détecte (code déjà présent) |
| Ne pas publier au Store | le MSIX reste utile en distribution directe : installation et mise à jour propres, sans installeur |

L'option intermédiaire est la seule qui mène réellement au Store sans réécrire
l'application. Elle demande un drapeau d'édition qui désactive les
téléchargements — travail non fait ici, car il change le comportement du
produit et relève d'une décision de votre part, pas d'un choix d'empaquetage.

#### 2. Identité et éditeur — 10 minutes, après création du compte

Valeurs `TEST` à remplacer (voir [section M](#m--microsoft-partner-center)).

#### 3. Métadonnées de la fiche — manuel par nature

Captures d'écran, classification d'âge, politique de confidentialité.

#### 4. WebView2 — à signaler

Un paquet MSIX ne peut pas installer WebView2. Sur un Windows 10 sans mise à
jour, l'application s'ouvrira dans le navigateur par défaut au lieu de sa
fenêtre native. À mentionner dans la description de la fiche.

### Soumission automatisée

Partner Center expose une API de soumission (`Microsoft Store submission
API`), utilisable depuis GitHub Actions. Elle exige un enregistrement Azure AD
(client ID, client secret, tenant ID) **et** un produit déjà créé dans Partner
Center.

L'architecture est prête à l'accueillir : le workflow produit déjà un `.msix`
validé et versionné. L'étape à ajouter, le jour venu, serait un dernier job
conditionné aux secrets `PARTNER_CENTER_*` — sur le modèle exact de l'étape de
signature, qui se désactive proprement quand ses secrets sont absents.

Rien de tel n'est configuré aujourd'hui : configurer de faux secrets donnerait
un pipeline qui échoue à chaque tag, ou pire, qui semble publier sans le faire.

### Ce qui reste à faire, dans l'ordre

État au 12 septembre 2026 — le paquet a été construit et téléversé une
première fois, ce qui a permis de lever les points 1 à 4 :

- [x] compte Partner Center créé, produit **Agence 46** (`9NLTGFR2BTSP`) ;
- [x] `IdentityName` et `Publisher` renseignés depuis Partner Center ;
- [x] paquet MSIX construit localement (`build_msix.bat`) — 87,7 Mo ;
- [x] téléversement accepté, validation d'acceptation exécutée ;
- [x] `PublisherDisplayName` corrigé (`Muller.J`, sans point final) ;
- [x] `DisplayName` aligné sur le nom réservé (`Agence 46`) ;
- [ ] paquet reconstruit et re-téléversé ;
- [ ] familles d'appareils : ne laisser que **Windows 10/11 Desktop** ;
- [ ] fiche Store : captures d'écran, classification d'âge, politique de
      confidentialité, coordonnées de support ;
- [ ] justification de `runFullTrust` dans les notes aux certificateurs ;
- [ ] **arbitrer le téléchargement de binaires tiers** — le seul point
      réellement bloquant, et une décision produit (voir ci-dessus) ;
- [ ] soumettre : de quelques heures à quelques jours de revue, `runFullTrust`
      impliquant une vérification manuelle.

---

## O. Mise à jour de l'application

### Depuis le Microsoft Store

Automatique. Le Store détecte une version supérieure et l'installe ; les
données de `%USERPROFILE%\.agence_financiere` sont conservées, puisqu'elles
sont hors du paquet.

### Hors Store

```powershell
Add-AppxPackage -Path .\AgenceNumerique-4.1.0.0-x64.msix
```

La mise à jour se fait **par-dessus** : même `Identity/Name`, version
supérieure. Inutile de désinstaller.

`Identity/Name` joue ici le rôle que tient l'`AppId` d'Inno Setup : le changer
ferait apparaître une seconde application au lieu d'une mise à jour.

### Coexistence avec l'installeur Inno Setup

Les deux installations peuvent cohabiter (elles ont des identités différentes)
mais **partagent les mêmes données** — donc la même base SQLite. Faire tourner
les deux en même temps est déconseillé : deux instances écrivant la même base
et se disputant le port 8765.

Pour migrer : désinstaller l'ancienne version (les données sont conservées si
vous répondez « Non » à la question de suppression), puis installer le MSIX.
L'historique de trading est repris tel quel.

---

## P. Dépannage

### `makeappx.exe introuvable`

Le SDK Windows n'est pas installé :

```powershell
winget install -e --id Microsoft.WindowsSDK.10.0.22621
```

Les scripts cherchent dans `Windows Kits\10\bin\<version>\<arch>` et prennent
la version la plus récente. Aucun `PATH` à modifier.

### `Add-AppxPackage` : certificat non approuvé

Le certificat de test n'est pas dans le magasin de confiance de la machine :

```powershell
# PowerShell ADMINISTRATEUR
Import-Certificate -FilePath .\dist\msix\certificat\AgenceNumerique-Test.cer `
                   -CertStoreLocation Cert:\LocalMachine\TrustedPeople
```

`Cert:\CurrentUser\...` **ne suffit pas** : Windows consulte le magasin de la
machine.

### `Add-AppxPackage` : « application hors Store désactivée »

Paramètres → Système → Pour les développeurs → **Mode développeur**.

### Le sujet du certificat ne correspond pas au `Publisher`

`sign-msix.ps1` affiche les deux valeurs et refuse de signer. Alignez-les :

```powershell
# soit le manifeste sur le certificat
$env:MSIX_PUBLISHER = "CN=... (valeur exacte du certificat)"
.\packaging\msix\scripts\build-msix.ps1

# soit le certificat sur le manifeste
.\packaging\msix\scripts\create-test-certificate.ps1   # il lit le manifeste
```

### Le paquet s'installe, l'application ne démarre pas

```powershell
Get-Content "$env:USERPROFILE\.agence_financiere\serveur.log" -Tail 40
Get-Content "$env:USERPROFILE\.agence_financiere\app_error.log" -Tail 40
```

Ces journaux sont écrits **hors** du paquet et survivent à la désinstallation.
Cause la plus fréquente : un module absent du bundle PyInstaller — il faut
l'ajouter aux `hiddenimports` d'`agence.spec`.

Pour inspecter le contenu réel du paquet :

```powershell
.\packaging\msix\scripts\build-msix.ps1 -Configuration Debug -SkipAppBuild
# le dossier dist\msix\layout\ est conservé
```

### La fenêtre ne s'ouvre pas, l'application s'ouvre dans le navigateur

WebView2 manque. Un paquet MSIX ne peut pas l'installer :

```powershell
winget install -e --id Microsoft.EdgeWebView2Runtime
```

### Le trading réel ne trouve pas MetaTrader

Depuis un paquet MSIX, le terminal MetaTrader **portable embarqué** est
délibérément ignoré : il écrit à côté de son exécutable, or le dossier
d'installation est en lecture seule. Installez MetaTrader 5 normalement —
l'application le détecte (code déjà en place).

Vérification : Réglages ⚙️ → Diagnostic, bloc `empaquetage`.

### Erreur `0x80073CF3` à l'installation

Dépendance ou architecture incompatible. Vérifiez que la machine est bien x64
ou ARM64 (Windows 11), et que la version de Windows atteint 10.0.17763.

### Partner Center refuse le paquet au téléversement

Les erreurs suivantes s'affichent sur la page **Colis** (« Packages ») après
l'envoi du `.msix`. Aucune n'est visible avant : Partner Center confronte le
manifeste à votre compte, ce qu'aucun outil local ne peut faire à sa place.
`validate-msix.ps1` affiche désormais les quatre valeurs concernées côte à
côte, pour qu'une relecture remplace un aller-retour.

#### « L'élément PublisherDisplayName … ne correspond pas à votre nom complet d'éditeur : X »

`PublisherDisplayName` est le nom de votre **compte** d'éditeur, pas celui de
l'application. Les deux champs ne doivent jamais porter la même valeur.

> ### ⚠️ Ne recopiez pas la valeur depuis le message d'erreur
>
> Le message français se termine par un point. Ce point est la **ponctuation
> de la phrase**, pas une partie du nom :
>
> ```
> ...qui ne correspond pas à votre nom complet d'éditeur : Muller.J.
>                                                          ^^^^^^^^ ^
>                                                          le nom   la phrase
> ```
>
> Recopier `Muller.J.` au lieu de `Muller.J` produit un second refus dont le
> message est **illisible** — il affiche deux valeurs d'apparence identique :
>
> ```
> L'élément PublisherDisplayName ... est Muller.J., qui ne correspond
> pas à votre nom complet d'éditeur : Muller.J.
> ```
>
> **Relevez toujours la valeur avec le bouton Copier** de la page
> *Gestion des produits → Identité du produit*, jamais à la main.

Corriger, puis reconstruire (`build_msix.bat`) et re-téléverser :

```jsonc
// packaging/msix/msix.config.json
"PublisherDisplayName": "Muller.J",   // le COMPTE — sans point final
"DisplayName": "Agence 46"            // l'APPLICATION — un nom réservé
```

`validate-msix.ps1` encadre désormais ces valeurs de crochets et refuse toute
espace parasite, précisément parce que l'écart tient à un caractère invisible :

```
         PublisherDisplayName .......... [Muller.J]
    [OK] aucune espace parasite dans les champs d'identite
```

`python/tests/test_msix.py` empêche le dépôt d'y retomber : il vérifie que
`IdentityName` commence bien par `<PublisherDisplayName>.` — le point de
séparation suffit à démasquer un point en trop.

#### « Vous devez fournir un package compatible avec chacune des familles d'appareils sélectionnées »

Ce n'est pas le manifeste : il ne déclare que `Windows.Desktop`, ce qui est
correct. C'est la case cochée dans Partner Center.

Sur la même page, section **Disponibilité de la famille d'appareils** : ne
laisser cochée que **Windows 10/11 Desktop**, et décocher tout le reste
(Xbox, HoloLens, IoT…). Une application Win32 plein-trust ne peut cibler
qu'un bureau.

#### « Vous devez charger au moins un package »

Conséquence des erreurs ci-dessus, pas un problème distinct : un paquet dont
la validation échoue ne compte pas comme chargé. L'erreur disparaît d'elle-même
une fois les autres corrigées.

#### « Les fonctionnalités restreintes suivantes nécessitent une approbation : runFullTrust »

**Avertissement, pas erreur** — il n'empêche pas la soumission. `runFullTrust`
est obligatoire pour toute application Win32 empaquetée (voir
[section D](#d--création-du-msix)) et déclenche une revue manuelle.

Justification à donner dans les **Notes aux certificateurs** (Options de
soumission) :

> Application de bureau Win32 existante, empaquetée telle quelle. Elle exécute
> un interpréteur Python embarqué, un serveur HTTP local sur la boucle locale,
> et pilote le terminal MetaTrader 5 installé par l'utilisateur. Aucune de ces
> opérations n'est réalisable dans le bac à sable UWP.

#### « Le nom du package ne correspond à aucun de vos noms réservés »

`DisplayName` doit être un nom réservé : Partner Center → **Gestion des
produits → Gérer les noms d'application** → réserver le nom, ou aligner
`DisplayName` sur un nom déjà réservé. Voir la
[section M](#m--microsoft-partner-center), étape 3 bis.

### La CI échoue sur `configuration d'empaquetage invalide`

C'est l'étape `-DryRun`. Rejouez-la en local, le message est le même :

```powershell
.\packaging\msix\scripts\build-msix.ps1 -DryRun
```

### Le pipeline publie une version inattendue

Le paquet prend sa version du fichier `VERSION`, jamais du tag. Vérifiez que
les deux concordent avant de taguer.
