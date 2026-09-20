# Empaquetage MSIX

Tout ce qui transforme le dépôt en un `AgenceNumerique-<version>-x64.msix`,
installable sur Windows et soumissible au Microsoft Store.

La procédure complète — prérequis, secrets, versioning, publication, dépannage —
est dans [`WINDOWS-MSIX.md`](../../WINDOWS-MSIX.md) à la racine. Ce fichier-ci
ne décrit que le contenu du dossier.

```
packaging/msix/
  AppxManifest.xml            gabarit du manifeste (jetons {{...}})
  msix.config.json            identité du paquet — NON sensible, versionné
  Assets/                     17 icônes aux dimensions exigées par Windows
  scripts/
    common.ps1                fonctions partagées (version, config, SDK, manifeste)
    build-msix.ps1            chaîne complète : exe -> MSIX (+ --dry-run)
    create-test-certificate.ps1  certificat auto-signé pour les essais locaux
    sign-msix.ps1             signature (certificat de test ou GitHub Secrets)
    validate-msix.ps1         29 contrôles sur le paquet, sans l'installer
    install-test.ps1          installe, lance, vérifie, désinstalle
    uninstall-test.ps1        retire un paquet resté installé
  README.md                   ce fichier
```

## Les trois commandes

```powershell
# 1. une fois par machine (PowerShell administrateur)
.\packaging\msix\scripts\create-test-certificate.ps1

# 2. à chaque build
.\packaging\msix\scripts\build-msix.ps1 -Sign

# 3. pour vérifier
.\packaging\msix\scripts\validate-msix.ps1
.\packaging\msix\scripts\install-test.ps1 -Desinstaller
```

Ou, en double-cliquant : `build_msix.bat` à la racine du dépôt.

Résultat dans `dist\msix\` : le `.msix` et `SHA256SUMS.txt`.

## Ce que ces scripts NE font pas

Ils **n'écrivent jamais** de clé privée dans le dépôt. Le certificat de test est
fabriqué à la demande dans `dist/msix/certificat/` — dossier ignoré par git, et
doublé de motifs `*.pfx`, `*.key`, `*.pem` dans `.gitignore`. Il se régénère en
quelques secondes : il n'y a rien à sauvegarder.

Ils **ne soumettent rien** au Microsoft Store. La soumission demande un compte
Partner Center et une identité réservée ; les points restants sont listés dans
`WINDOWS-MSIX.md`, section N.

## Deux choses à savoir avant de modifier quoi que ce soit

**Le dossier d'installation d'un MSIX est en lecture seule.** Pas « protégé par
les droits » : réellement en lecture seule, pour tout le monde, y compris un
processus administrateur. C'est pour cela que le paquet n'embarque pas les
moteurs IA ni le terminal MetaTrader portable — ce dernier écrit ses profils à
côté de son exécutable et ne pourrait pas démarrer. L'application les installe
dans `~/.agence_financiere`, qui reste inscriptible.

**Les scripts sont en ASCII pur.** Windows PowerShell 5.1 — celui qui est
installé d'origine sur Windows — lit un `.ps1` sans BOM comme du texte ANSI :
un accent y devient un caractère parasite, y compris dans une chaîne comparée
par le code. `python/tests/test_msix.py` le vérifie à chaque commit, comme il
le fait déjà pour les `.bat`.
