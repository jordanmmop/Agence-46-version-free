<#
.SYNOPSIS
    Chaine complete : application Windows -> paquet MSIX (-> signature de test).

.DESCRIPTION
    Produit dist/msix/AgenceNumerique-<version>-<arch>.msix a partir du depot.

    STRATEGIE D'EMPAQUETAGE, et pourquoi celle-la
    ---------------------------------------------
    L'application est un programme Python (FastAPI + pywebview) deja compile
    en executable Windows autonome par PyInstaller - c'est ce que fait
    build_exe.bat, et c'est ce que distribue deja l'installeur Inno Setup.
    Le MSIX ne refait donc PAS ce travail : il emballe le dossier produit par
    PyInstaller (dist/AgenceNumerique) avec un manifeste et des icones.

    Consequence directe : l'utilisateur final n'a besoin ni de Python, ni de
    Node, ni de pip. Le runtime Python, les DLL, les extensions compilees
    (.pyd de numpy, scipy, pandas) et le frontend HTML/CSS/JS voyagent dans le
    paquet.

    BUILD ALLEGE PAR DEFAUT
    -----------------------
    Les moteurs embarques (Ollama, Hermes, terminal MetaTrader 5) ne sont PAS
    inclus dans le MSIX, pour trois raisons techniques, pas par commodite :

      1. le dossier d'installation d'un MSIX est en LECTURE SEULE. Or le
         terminal MetaTrader 5 en mode /portable ECRIT ses profils et ses
         journaux a cote de son executable : embarque dans le paquet, il
         demarrerait puis refuserait de se connecter ;
      2. livrer un installeur tiers (mt5setup.exe) dans un paquet Store est
         contraire aux regles de publication Microsoft ;
      3. 5 a 10 Go de modeles depassent ce qu'un artefact GitHub ou une
         release peuvent transporter.

    L'application sait deja fonctionner ainsi : elle telecharge ses moteurs au
    premier lancement, dans ~/.agence_financiere (hors du paquet, donc
    inscriptible). -AvecRuntimes force l'inclusion pour un essai local.

.PARAMETER Configuration
    Release (defaut) ou Debug. Release est ce qui part au Microsoft Store :
    controles stricts, dossier de preparation nettoye. Debug conserve le
    dossier de preparation et n'impose pas les regles propres au Store.

.PARAMETER SkipAppBuild
    Ne pas relancer PyInstaller : reutilise dist/AgenceNumerique tel quel.
    Utile pour iterer sur le manifeste ou les icones sans 10 minutes de
    compilation a chaque essai.

.PARAMETER AvecRuntimes
    Embarque Ollama / Hermes / MetaTrader 5 (voir ci-dessus : deconseille).

.PARAMETER Sign
    Signe le paquet avec le certificat de test (create-test-certificate.ps1).

.PARAMETER DryRun
    Verifie TOUTE la configuration - outils, identite, version, manifeste,
    charge utile - sans produire ni signer de paquet. Rend 0 si un vrai build
    aboutirait.

.PARAMETER Revision
    Quatrieme nombre de la version MSIX. Doit rester 0 pour le Store (c'est
    lui qui l'attribue). A n'utiliser que pour reinstaller localement
    par-dessus un paquet de meme version.

.EXAMPLE
    .\packaging\msix\scripts\build-msix.ps1 -DryRun

.EXAMPLE
    .\packaging\msix\scripts\build-msix.ps1 -Sign

.EXAMPLE
    .\packaging\msix\scripts\build-msix.ps1 -SkipAppBuild -Sign -Configuration Debug
#>
[CmdletBinding()]
param(
    [ValidateSet('Release', 'Debug')][string]$Configuration = 'Release',
    [switch]$SkipAppBuild,
    [switch]$AvecRuntimes,
    [switch]$Sign,
    [switch]$DryRun,
    [ValidateRange(0, 65535)][int]$Revision = 0,
    [string]$DossierSortie = "",
    [string]$MotDePasseCertificat = ""
)

. "$PSScriptRoot\common.ps1"

$racine       = Get-RacineDepot
$dossierMsix  = Get-DossierMsix
$sortie       = Get-DossierSortie -Personnalise $DossierSortie
$layout       = Join-Path $sortie "layout"
$gabarit      = Join-Path $dossierMsix "AppxManifest.xml"
$assets       = Join-Path $dossierMsix "Assets"
$appPyInstall = Join-Path $racine "dist\AgenceNumerique"
$debut        = Get-Date

$etapes = 8
$echecs = New-Object System.Collections.Generic.List[string]
$alertes = New-Object System.Collections.Generic.List[string]

Write-Titre "Agence Numerique Financiere - paquet MSIX ($Configuration)"

# ===========================================================================
#  [1/8] Configuration, version, outils
# ===========================================================================
Write-Etape "[1/$etapes] Configuration et outils..."

$config = Get-ConfigMsix
$versionDepot = Get-VersionDepot
$versionMsix  = ConvertTo-VersionMsix -Version $versionDepot -Revision $Revision
$architecture = $config['Architecture']

Write-Ok "version du depot : $versionDepot  ->  version MSIX : $versionMsix"
Write-Detail "source unique : le fichier VERSION de la racine"

if ($Revision -ne 0) {
    $alertes.Add("Revision=$Revision : ce paquet ne peut PAS etre soumis au Microsoft Store (revision reservee).")
    Write-Alerte "revision $Revision - paquet local uniquement"
}

if (Test-IdentiteDeTest -Config $config) {
    $alertes.Add("Identite PROVISOIRE (marquee TEST) : a remplacer par les valeurs Partner Center avant publication.")
    Write-Alerte "identite de developpement : $($config['IdentityName'])"
}
else {
    Write-Ok "identite : $($config['IdentityName'])"
}

$makeappx = Get-OutilSdkObligatoire -Nom "makeappx.exe" -Architecture $architecture
Write-Ok "makeappx : $makeappx"

$signtool = ""
if ($Sign) {
    $signtool = Get-OutilSdkObligatoire -Nom "signtool.exe" -Architecture $architecture
    Write-Ok "signtool : $signtool"
}

# ===========================================================================
#  [2/8] Nettoyage
# ===========================================================================
Write-Etape "[2/$etapes] Nettoyage des builds precedents..."

if ($DryRun) {
    Write-Detail "(dry-run : rien n'est efface)"
}
else {
    if (Test-Path $layout) { Remove-Item $layout -Recurse -Force }
    Get-ChildItem -Path $sortie -Filter *.msix -File -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $sortie | Out-Null
    Write-Ok "dist/msix nettoye"
}

# ===========================================================================
#  [3/8] Application Windows (PyInstaller)
# ===========================================================================
Write-Etape "[3/$etapes] Application Windows..."

$exeAttendu = Join-Path $appPyInstall "AgenceNumerique.exe"

if ($SkipAppBuild) {
    if (-not (Test-Path $exeAttendu)) {
        Write-Echec "-SkipAppBuild demande mais $exeAttendu n'existe pas."
        Write-Detail "Lancez d'abord build_exe.bat, ou retirez -SkipAppBuild."
        exit 1
    }
    Write-Ok "reutilisation de dist/AgenceNumerique (compilation ignoree)"
}
elseif ($DryRun) {
    if (Test-Path $exeAttendu) {
        Write-Ok "application deja compilee (dry-run : pas de recompilation)"
    }
    else {
        Write-Alerte "application pas encore compilee - un vrai build la produirait"
        $alertes.Add("dist/AgenceNumerique absent au moment du dry-run.")
    }
}
else {
    # On reutilise build_exe.bat plutot que de reecrire la chaine PyInstaller :
    # c'est le script deja eprouve du depot (dependances, moteurs, controle du
    # resultat). Deux chaines de compilation divergeraient en quelques mois.
    $env:AGENCE_BUILD_AUTO = "1"          # ni pause, ni ouverture de l'explorateur
    if (-not $AvecRuntimes) {
        $env:AGENCE_SANS_RUNTIMES = "1"   # build allege : voir l'en-tete
        Write-Detail "build allege (moteurs IA telecharges au 1er lancement)"
    }
    else {
        Remove-Item Env:\AGENCE_SANS_RUNTIMES -ErrorAction SilentlyContinue
        Write-Alerte "moteurs embarques demandes : paquet de plusieurs Go, non publiable au Store"
        $alertes.Add("-AvecRuntimes : le terminal MetaTrader portable ne peut pas ecrire dans un paquet MSIX (lecture seule).")
    }

    Push-Location $racine
    try {
        Write-Detail "appel de build_exe.bat (10 a 15 min a la premiere compilation)"
        & cmd.exe /c "build_exe.bat"
        if ($LASTEXITCODE -ne 0) {
            Write-Echec "build_exe.bat a echoue (code $LASTEXITCODE)"
            exit 1
        }
    }
    finally { Pop-Location }

    if (-not (Test-Path $exeAttendu)) {
        Write-Echec "$exeAttendu n'a pas ete produit"
        exit 1
    }
    Write-Ok "application compilee : dist/AgenceNumerique"
}

# ===========================================================================
#  [4/8] Preparation du contenu du paquet
# ===========================================================================
Write-Etape "[4/$etapes] Preparation du contenu du paquet..."

# Disposition retenue :
#     layout\AppxManifest.xml
#     layout\Assets\...            icones lues par Windows
#     layout\app\...               la sortie PyInstaller telle quelle
#
# L'application dans un sous-dossier "app" plutot qu'a la racine : la sortie
# PyInstaller contient des centaines de fichiers, et un "Assets" ou un
# "AppxManifest.xml" qui s'y trouverait un jour ecraserait silencieusement
# celui du paquet.

if (-not (Test-Path $assets)) {
    Write-Echec "Dossier des icones introuvable : $assets"
    Write-Detail "Regenerez-le : python tools/generer_assets_msix.py"
    exit 1
}

if ($DryRun) {
    Write-Detail "(dry-run : contenu non recopie)"
    New-Item -ItemType Directory -Force -Path $layout | Out-Null
}
else {
    New-Item -ItemType Directory -Force -Path $layout | Out-Null
    Copy-Item -Path $assets -Destination (Join-Path $layout "Assets") -Recurse -Force
    Write-Ok "icones copiees"

    if (Test-Path $exeAttendu) {
        $cible = Join-Path $layout "app"
        New-Item -ItemType Directory -Force -Path $cible | Out-Null
        Copy-Item -Path (Join-Path $appPyInstall "*") -Destination $cible -Recurse -Force
        $taille = (Get-ChildItem $cible -Recurse -File | Measure-Object -Sum Length).Sum / 1MB
        Write-Ok ("application copiee ({0:N0} Mo)" -f $taille)
    }
    else {
        Write-Echec "application introuvable : $appPyInstall"
        exit 1
    }
}

# ===========================================================================
#  [5/8] Manifeste
# ===========================================================================
Write-Etape "[5/$etapes] Generation du manifeste..."

if (-not (Test-Path $gabarit)) {
    Write-Echec "Gabarit introuvable : $gabarit"
    exit 1
}

$manifesteGenere = Join-Path $layout "AppxManifest.xml"
New-Item -ItemType Directory -Force -Path $layout | Out-Null

try {
    $texte = New-ManifesteMsix -Gabarit $gabarit -Config $config `
                               -Version $versionMsix -Destination $manifesteGenere
}
catch {
    Write-Echec $_.Exception.Message
    exit 1
}

# Controles que makeappx ne fait pas tous, ou pas avec un message exploitable.
# Ils vivent dans common.ps1 pour une raison : la suite de tests Python les
# rejoue a chaque commit, sur n'importe quel systeme (voir test_msix.py).
$problemes = Test-ManifesteMsix -TexteManifeste $texte
if ($problemes.Count -gt 0) {
    foreach ($p in $problemes) { Write-Echec $p }
    exit 1
}

$xml = [xml]$texte
$identite = $xml.Package.Identity
if ($Configuration -eq 'Release' -and $identite.Version -notmatch '\.0$') {
    Write-Alerte "la revision n'est pas 0 : paquet non soumissible au Store"
}

Write-Ok "manifeste valide"
Write-Detail "Identity      : $($identite.Name)"
Write-Detail "Publisher     : $($identite.Publisher)"
Write-Detail "Version       : $($identite.Version)"
Write-Detail "Architecture  : $($identite.ProcessorArchitecture)"

# L'executable declare doit exister REELLEMENT dans le contenu prepare.
# Un chemin errone ici produit un paquet parfaitement valide qui ne demarre
# pas, et Windows n'affiche alors qu'un message generique.
$executableDeclare = $xml.Package.Applications.Application.Executable
if (-not $DryRun) {
    $cheminExe = Join-Path $layout $executableDeclare
    if (-not (Test-Path $cheminExe)) {
        Write-Echec "l'executable declare est absent du contenu : $executableDeclare"
        exit 1
    }
    Write-Ok "executable present : $executableDeclare"
}

# ===========================================================================
#  [6/8] Creation du paquet
# ===========================================================================
Write-Etape "[6/$etapes] Creation du paquet MSIX..."

$suffixe = if ($Configuration -eq 'Debug') { "-Debug" } else { "" }
$nomPaquet = "AgenceNumerique-$versionMsix-$architecture$suffixe.msix"
$cheminPaquet = Join-Path $sortie $nomPaquet

if ($DryRun) {
    Write-Detail "(dry-run : makeappx non appele)"
    Write-Ok "un vrai build produirait : $nomPaquet"
}
else {
    & $makeappx pack /d $layout /p $cheminPaquet /o
    if ($LASTEXITCODE -ne 0) {
        Write-Echec "makeappx a echoue (code $LASTEXITCODE)"
        exit 1
    }
    if (-not (Test-Path $cheminPaquet)) {
        Write-Echec "le paquet n'a pas ete produit : $cheminPaquet"
        exit 1
    }
    $mo = (Get-Item $cheminPaquet).Length / 1MB
    Write-Ok ("{0} ({1:N0} Mo)" -f $nomPaquet, $mo)
    if ($mo -gt 10240) {
        $alertes.Add("Paquet de plus de 10 Go : au-dela de ce qu'une release GitHub accepte.")
    }
}

# ===========================================================================
#  [7/8] Signature
# ===========================================================================
Write-Etape "[7/$etapes] Signature..."

if ($DryRun) {
    Write-Detail "(dry-run : pas de signature)"
}
elseif ($Sign) {
    $argsSignature = @{ Paquet = $cheminPaquet; DossierSortie = $sortie }
    if ($MotDePasseCertificat) { $argsSignature['MotDePasse'] = $MotDePasseCertificat }
    & (Join-Path $PSScriptRoot "sign-msix.ps1") @argsSignature
    if ($LASTEXITCODE -ne 0) {
        Write-Echec "la signature a echoue"
        $echecs.Add("signature")
    }
}
else {
    Write-Alerte "paquet NON signe - il ne pourra pas etre installe en l'etat"
    Write-Detail "signature de test : ajoutez -Sign (apres create-test-certificate.ps1)"
    Write-Detail "publication Store : le Store re-signe lui-meme, c'est normal"
}

# ===========================================================================
#  [8/8] Empreintes et nettoyage
# ===========================================================================
Write-Etape "[8/$etapes] Empreintes SHA-256..."

if ($DryRun) {
    Write-Detail "(dry-run : pas d'empreinte)"
}
else {
    # Format compatible "sha256sum -c" : l'utilisateur peut verifier le
    # telechargement avec l'outil de son choix, sans faire confiance a un
    # script de ce depot.
    $lignes = @()
    foreach ($f in Get-ChildItem -Path $sortie -Filter *.msix -File | Sort-Object Name) {
        $empreinte = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash.ToLower()
        $lignes += "$empreinte  $($f.Name)"
    }
    $fichierSommes = Join-Path $sortie "SHA256SUMS.txt"
    Set-Content -Path $fichierSommes -Value $lignes -Encoding ASCII
    Write-Ok "SHA256SUMS.txt"
    foreach ($l in $lignes) { Write-Detail $l }

    if ($Configuration -eq 'Release' -and (Test-Path $layout)) {
        Remove-Item $layout -Recurse -Force
        Write-Detail "dossier de preparation efface (Release)"
    }
    elseif (Test-Path $layout) {
        Write-Detail "dossier de preparation conserve (Debug) : $layout"
    }
}

# ===========================================================================
#  Bilan
# ===========================================================================
$duree = (Get-Date) - $debut
Write-Titre "Bilan"
Write-Host ""
Write-Host "  Configuration : $Configuration"
Write-Host "  Version       : $versionMsix"
Write-Host "  Architecture  : $architecture"
if ($DryRun) {
    Write-Host "  Mode          : DRY-RUN (aucun paquet produit)" -ForegroundColor Yellow
}
else {
    Write-Host "  Paquet        : $cheminPaquet"
}
Write-Host ("  Duree         : {0:mm\:ss}" -f $duree)

if ($alertes.Count -gt 0) {
    Write-Host ""
    Write-Host "  AVERTISSEMENTS :" -ForegroundColor Yellow
    foreach ($a in $alertes) { Write-Host "    - $a" -ForegroundColor Yellow }
}

if ($echecs.Count -gt 0) {
    Write-Host ""
    Write-Host "  ECHECS : $($echecs -join ', ')" -ForegroundColor Red
    exit 1
}

Write-Host ""
if ($DryRun) {
    Write-Host "  [OK] Configuration verifiee - un build reel aboutirait." -ForegroundColor Green
}
else {
    Write-Host "  [OK] Paquet MSIX produit." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Suite :" -ForegroundColor Cyan
    Write-Host "    .\packaging\msix\scripts\validate-msix.ps1"
    Write-Host "    .\packaging\msix\scripts\install-test.ps1"
}
Write-Host ""
exit 0
