# Installeur Windows

Tout ce qui transforme le dépôt en un `AgenceNumerique-Setup-<version>.exe`
qu'un utilisateur double-clique.

```
installer/
  agence.iss                  script Inno Setup (l'assistant d'installation)
  agence.ico                  icône multi-résolutions (exe + installeur)
  infos_avant.txt             page affichée AVANT l'installation (risque financier)
  infos_apres.txt             page affichée APRÈS (démarrage, données, journaux)
  redist/                     amorceur WebView2, récupéré au build (non versionné)
  updates/manifest-exemple.json  format du manifeste de mise à jour
```

## Générer l'installeur

```bat
build_installer.bat              :: application + installeur, tout embarqué
build_installer.bat --leger      :: sans les moteurs IA (~300 Mo au lieu de 5-10 Go)
```

Résultat : `dist\installateur\AgenceNumerique-Setup-<version>.exe`.

Prérequis, une seule fois :

```bat
winget install -e --id JRSoftware.InnoSetup
```

Inno Setup **6.1 minimum** ; la 6 comme la 7 compilent ce script, et
`build_installer.bat` cherche les deux. Attention : Inno Setup 7 exige
Windows 11 — sur Windows 10, installez la
[6.x](https://jrsoftware.org/isdl.php). Une version antérieure à 6.1 est
refusée avec un message clair plutôt qu'une erreur incompréhensible.

Pour ne régénérer que l'installeur, l'application étant déjà compilée :

```bat
iscc installer\agence.iss
```

## Ce que fait l'assistant

| Étape | Détail |
|---|---|
| Avertissement | Risque financier, affiché avant toute copie de fichier |
| Dossier | Modifiable, `%LOCALAPPDATA%\Programs\AgenceNumerique` par défaut |
| Raccourcis | Menu Démarrer (toujours), Bureau (case à cocher) |
| Dépendances | Composant WebView2 installé s'il manque |
| Enregistrement | Entrée « Applications installées » avec nom, version, icône, éditeur |
| Désinstallation | Retire l'application ; les données personnelles ne partent que sur demande explicite |

## Pourquoi une installation « par utilisateur » et non dans Program Files

Le terminal MetaTrader 5 embarqué tourne en mode `/portable` : il **écrit**
ses profils, ses journaux et son `config\common.ini` à côté de son
exécutable, donc dans le dossier d'installation. Dans `Program Files`,
Windows refuse ces écritures ou les redirige silencieusement — le terminal
démarre, refuse la connexion au compte, et rien n'explique pourquoi.
Installer dans le profil de l'utilisateur supprime le problème à la racine, et
avec lui l'invite UAC.

## Où vont les données de l'utilisateur

`%USERPROFILE%\.agence_financiere\` — et **jamais** dans le dossier
d'installation :

| Fichier | Contenu |
|---|---|
| `config.json` | réglages, moteur IA choisi, code d'accès s'il est activé |
| `data/agence.db` | historique de trading, signaux, performances |
| `ollama/`, `hermes/` | modèles d'IA (plusieurs Go) |
| `mt5/` | identifiants MetaTrader mémorisés |
| `serveur.log`, `app_error.log` | journaux de diagnostic |

Une mise à jour remplace le dossier d'installation et laisse tout cela
intact. La désinstallation propose de le supprimer, avec « Non » comme
réponse par défaut.

## Mises à jour

L'installeur réutilise un `AppId` **stable** : une nouvelle version s'installe
par-dessus l'ancienne, sans doublon dans « Applications installées », et le
gestionnaire de redémarrage de Windows ferme l'application si elle tourne.

Il écrit aussi `HKCU\Software\AgenceNumerique` (`InstallPath`, `Version`) : de
quoi permettre à un futur programme de mise à jour de retrouver l'installation
sans rien demander.

Côté application, `python/utils/mise_a_jour.py` sait déjà comparer la version
installée à celle d'un manifeste JSON publié (voir
`updates/manifest-exemple.json`), et `/api/mise-a-jour` l'expose. La
vérification est **désactivée** tant que `AGENCE_UPDATE_URL` n'est pas
renseignée : rien ne sort sur le réseau sans décision explicite.

Pour livrer une nouvelle version :

1. mettre à jour le fichier `VERSION` à la racine ;
2. `build_installer.bat` ;
3. publier `dist\installateur\AgenceNumerique-Setup-<version>.exe`.

Le numéro se propage tout seul à l'application, à la fiche de propriétés de
l'exe et à l'assistant d'installation.

## Installation silencieuse (déploiement)

```bat
AgenceNumerique-Setup-4.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
AgenceNumerique-Setup-4.0.0.exe /VERYSILENT /DIR="D:\Agence"
"%LOCALAPPDATA%\Programs\AgenceNumerique\unins000.exe" /VERYSILENT
```

Une désinstallation silencieuse **conserve** toujours les données
personnelles : la question ne peut pas être posée, donc rien n'est supprimé.

## Charge utile supérieure à 2 Go

Un exécutable d'installation unique ne peut pas dépasser environ 2 Go. Avec
les moteurs IA embarqués (5 à 10 Go), `scripts\build_installer.bat` bascule
automatiquement Inno Setup en mode tranches : le `.exe` est alors accompagné
de fichiers `.bin` qu'il faut **distribuer avec lui**, dans le même dossier.
Pour un téléchargement en un seul fichier, utilisez `--leger` : l'application
récupère alors ses moteurs au premier usage.
