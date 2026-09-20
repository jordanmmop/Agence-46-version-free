<#
.SYNOPSIS
    Verifie un paquet MSIX avant de le distribuer.

.DESCRIPTION
    Un MSIX peut etre parfaitement bien forme et parfaitement inutilisable :
    manifeste correct mais executable absent, DLL Python manquante, icone a la
    mauvaise taille, signature d'un autre editeur. Rien de tout cela ne se
    voit a la construction - seulement chez l'utilisateur.

    Ce script ouvre donc le paquet (un MSIX est une archive ZIP) et controle
    ce qu'il contient REELLEMENT, sans rien installer.

    Trois verdicts, jamais melanges :
        [OK]      controle passe
        [!]       reserve - le paquet fonctionne, mais quelque chose merite
                  d'etre su (signature non approuvee, identite de test...)
        [ECHEC]   le paquet ne marchera pas : code de sortie non nul

.PARAMETER Paquet
    Le .msix a verifier. Par defaut : le plus recent de dist/msix.

.PARAMETER AvecInstallation
    Installe puis desinstalle reellement le paquet (install-test.ps1).
    Windows uniquement, et le certificat doit etre approuve.

.PARAMETER AvecWack
    Lance le Windows App Certification Kit s'il est installe. Long (plusieurs
    minutes) mais c'est le controle que fait le Store lui-meme.

.EXAMPLE
    .\packaging\msix\scripts\validate-msix.ps1

.EXAMPLE
    .\packaging\msix\scripts\validate-msix.ps1 -AvecInstallation -AvecWack
#>
[CmdletBinding()]
param(
    [string]$Paquet = "",
    [switch]$AvecInstallation,
    [switch]$AvecWack,
    [string]$DossierSortie = ""
)

. "$PSScriptRoot\common.ps1"

$script:nbOk      = 0
$script:nbAlertes = 0
$script:nbEchecs  = 0

function Write-Succes      { param([string]$m) Write-Ok $m;      $script:nbOk++ }
function Write-Reserve  { param([string]$m) Write-Alerte $m;  $script:nbAlertes++ }
function Write-Bloquant   { param([string]$m) Write-Echec $m;   $script:nbEchecs++ }

Write-Titre "Validation du paquet MSIX"

# ===========================================================================
#  1. Le paquet existe et est lisible
# ===========================================================================
Write-Etape "[1/9] Presence et lisibilite du paquet..."

try { $cheminPaquet = Resolve-Paquet -Paquet $Paquet -DossierSortie $DossierSortie }
catch {
    Write-Echec $_.Exception.Message
    exit 1
}

$infoPaquet = Get-Item $cheminPaquet
Write-Succes ("$($infoPaquet.Name) ({0:N1} Mo)" -f ($infoPaquet.Length / 1MB))

if ($infoPaquet.Length -lt 1MB) {
    Write-Bloquant "paquet suspicieusement petit - la charge utile est-elle bien dedans ?"
}

try {
    $fichiers = Get-FichiersDuPaquet -Paquet $cheminPaquet
    Write-Succes "archive lisible : $($fichiers.Count) fichiers"
}
catch {
    Write-Bloquant "archive illisible : $($_.Exception.Message)"
    Write-Titre "Validation interrompue"
    exit 1
}

# Les entrees d'un paquet peuvent utiliser l'un ou l'autre separateur selon
# l'outil qui l'a produit : on normalise avant toute comparaison, sinon un
# fichier bien present serait declare manquant.
$normalises = @($fichiers | ForEach-Object { $_.Replace('\', '/') })
function Test-DansPaquet {
    param([string]$Chemin)
    $cible = $Chemin.Replace('\', '/')
    return ($normalises -contains $cible)
}
function Get-DansPaquet {
    # Le "," devant @(...) est indispensable, et ce n'est pas du style :
    # PowerShell DEROULE une collection rendue par une fonction. Sans lui,
    # zero resultat devient $null et UN resultat devient une simple chaine -
    # dans les deux cas, le ".Count" de l'appelant leve une exception sous
    # Set-StrictMode. Le controle cense reperer une DLL manquante plantait
    # donc aussi bien quand la DLL etait la que quand elle ne l'etait pas.
    param([string]$Motif)
    return ,@($normalises | Where-Object { $_ -like $Motif })
}

# ===========================================================================
#  2. Manifeste
# ===========================================================================
Write-Etape "[2/9] Manifeste..."

try {
    $manifeste = Get-ManifesteDuPaquet -Paquet $cheminPaquet
    Write-Succes "AppxManifest.xml present et bien forme"
}
catch {
    Write-Bloquant "manifeste illisible : $($_.Exception.Message)"
    Write-Titre "Validation interrompue"
    exit 1
}

$problemes = Test-ManifesteMsix -TexteManifeste $manifeste.OuterXml
if ($problemes.Count -gt 0) {
    foreach ($p in $problemes) { Write-Bloquant $p }
}
else {
    Write-Succes "manifeste conforme (identite, version, architecture, capacites)"
}

$identite = $manifeste.Package.Identity
$app      = $manifeste.Package.Applications.Application

Write-Detail "Identity     : $($identite.Name)"
Write-Detail "Publisher    : $($identite.Publisher)"
Write-Detail "Version      : $($identite.Version)"
Write-Detail "Architecture : $($identite.ProcessorArchitecture)"
Write-Detail "Executable   : $($app.Executable)"

# ===========================================================================
#  3. Version
# ===========================================================================
Write-Etape "[3/9] Version..."

$versionDepot = Get-VersionDepot
$attendue = ConvertTo-VersionMsix -Version $versionDepot
if ($identite.Version -eq $attendue) {
    Write-Succes "version $($identite.Version) : conforme au fichier VERSION ($versionDepot)"
}
else {
    # Pas un echec : un paquet de test peut porter une revision non nulle.
    # Mais une DIVERGENCE de majeur/mineur/build signale un paquet construit
    # a partir d'un autre etat du depot, ce qui merite d'etre dit.
    $troisPremiers  = ($identite.Version -split '\.')[0..2] -join '.'
    $attenduTrois   = ($attendue -split '\.')[0..2] -join '.'
    if ($troisPremiers -eq $attenduTrois) {
        Write-Reserve "revision $(($identite.Version -split '\.')[3]) : paquet local, non soumissible au Store"
    }
    else {
        Write-Reserve "version $($identite.Version) alors que VERSION dit $versionDepot"
    }
}

# ===========================================================================
#  4. Architecture
# ===========================================================================
Write-Etape "[4/9] Architecture..."

if ($identite.ProcessorArchitecture -eq 'x64') {
    Write-Succes "x64 : la seule architecture supportee par cette application"
    Write-Detail "(MetaTrader5, Ollama et les roues scientifiques n'existent qu'en 64 bits)"
}
elseif ($identite.ProcessorArchitecture -in @('x86', 'arm64', 'neutral')) {
    Write-Reserve "$($identite.ProcessorArchitecture) : verifiez que toutes les dependances natives existent pour cette cible"
}
else {
    Write-Bloquant "architecture inconnue : $($identite.ProcessorArchitecture)"
}

# ===========================================================================
#  5. Executable et dependances natives
# ===========================================================================
Write-Etape "[5/9] Executable, DLL et dependances..."

if (Test-DansPaquet $app.Executable) {
    Write-Succes "executable present : $($app.Executable)"
}
else {
    Write-Bloquant "executable DECLARE mais ABSENT du paquet : $($app.Executable)"
}

# Le runtime Python : c'est lui qui rend l'application autonome. Absent, le
# paquet s'installe et l'application ne demarre pas - sur une machine sans
# Python, ce qui est precisement le cas vise.
$dllPython = Get-DansPaquet "*python3*.dll"
if ($dllPython.Count -gt 0) {
    Write-Succes "runtime Python embarque : $($dllPython[0] | Split-Path -Leaf)"
}
else {
    Write-Bloquant "aucune DLL python3xx.dll : l'application exigerait Python sur la machine"
}

# Runtime C de Microsoft : PyInstaller le copie. Sans lui, l'application
# depend du "Visual C++ Redistributable" installe sur la machine cible.
$vcruntime = Get-DansPaquet "*vcruntime140*.dll"
if ($vcruntime.Count -gt 0) {
    Write-Succes "runtime Visual C++ embarque ($($vcruntime.Count) DLL)"
}
else {
    Write-Reserve "vcruntime140.dll absente : l'application dependra du redistribuable Visual C++ de la machine"
}

# Extensions compilees : numpy/pandas/scipy sont des .pyd. Leur absence
# signale un bundle ampute, qui echouera au premier calcul d'indicateur.
$pyd = Get-DansPaquet "*.pyd"
if ($pyd.Count -ge 10) {
    Write-Succes "extensions natives Python : $($pyd.Count) fichiers .pyd"
}
else {
    Write-Reserve "seulement $($pyd.Count) fichiers .pyd - bundle incomplet ?"
}

# Le code applicatif et le frontend doivent etre la, sinon l'interface est
# vide alors que le serveur repond.
foreach ($attendu in @(@{ motif = "*/frontend/index.html"; nom = "interface web (frontend)" },
                       @{ motif = "*/backend/main.py";     nom = "serveur FastAPI (backend)" },
                       @{ motif = "*/python/config.py";    nom = "code applicatif (python/)" },
                       @{ motif = "*/VERSION";             nom = "fichier VERSION" })) {
    if ((Get-DansPaquet $attendu.motif).Count -gt 0) {
        Write-Succes "$($attendu.nom) present"
    }
    else {
        Write-Bloquant "$($attendu.nom) absent du paquet"
    }
}

# Moteurs embarques : leur PRESENCE est un probleme dans un MSIX, pas leur
# absence (dossier d'installation en lecture seule + regles du Store).
$runtimes = Get-DansPaquet "*/runtime/*"
if ($runtimes.Count -gt 0) {
    Write-Reserve "moteurs embarques detectes ($($runtimes.Count) fichiers) : le terminal MetaTrader portable ne pourra pas ecrire dans un paquet en lecture seule"
}
else {
    Write-Succes "aucun moteur embarque : les moteurs iront dans ~/.agence_financiere (inscriptible)"
}

# ===========================================================================
#  6. Icones
# ===========================================================================
Write-Etape "[6/9] Icones..."

$assetsAttendus = @(
    "Assets/StoreLogo.png",
    "Assets/Square44x44Logo.png",
    "Assets/Square150x150Logo.png",
    "Assets/Square71x71Logo.png",
    "Assets/Square310x310Logo.png",
    "Assets/Wide310x150Logo.png",
    "Assets/SplashScreen.png"
)
$manquants = @($assetsAttendus | Where-Object { -not (Test-DansPaquet $_) })
if ($manquants.Count -eq 0) {
    Write-Succes "les $($assetsAttendus.Count) icones referencees par le manifeste sont presentes"
}
else {
    foreach ($m in $manquants) { Write-Bloquant "icone absente : $m" }
    Write-Detail "regenerez-les : python tools/generer_assets_msix.py"
}

# Les variantes non "platees" (barre des taches, Alt-Tab) ne sont pas
# obligatoires, mais leur absence donne une icone minuscule dans la barre.
$unplated = Get-DansPaquet "Assets/Square44x44Logo.altform-unplated*"
if ($unplated.Count -gt 0) {
    Write-Succes "variantes altform-unplated presentes ($($unplated.Count))"
}
else {
    Write-Reserve "pas de variante altform-unplated : icone degradee dans la barre des taches"
}

# ===========================================================================
#  7. Signature
# ===========================================================================
Write-Etape "[7/9] Signature..."

$estWindows = Test-Windows
if (-not $estWindows) {
    Write-Reserve "controle de signature ignore : possible uniquement sous Windows"
}
else {
    $signature = Get-AuthenticodeSignature -FilePath $cheminPaquet
    switch ($signature.Status) {
        'Valid' {
            Write-Succes "signature valide, certificat approuve par cette machine"
            Write-Detail "signataire : $($signature.SignerCertificate.Subject)"
            $sujet = ConvertTo-SujetNormalise -Sujet $signature.SignerCertificate.Subject
            $pub   = ConvertTo-SujetNormalise -Sujet $identite.Publisher
            if ($sujet -ne $pub) {
                Write-Bloquant "signataire different du Publisher du manifeste - l'installation sera refusee"
                Write-Detail "signataire : $sujet"
                Write-Detail "manifeste  : $pub"
            }
        }
        'NotSigned' {
            # Pas un echec en soi : un paquet destine au Store n'a pas a etre
            # signe (le Store re-signe). Mais il ne s'installera pas ici.
            Write-Reserve "paquet NON signe : installable nulle part en l'etat"
            Write-Detail "pour un essai local : sign-msix.ps1"
            Write-Detail "pour le Microsoft Store : c'est normal, le Store re-signe le paquet"
        }
        'UnknownError' {
            Write-Reserve "signature presente mais certificat non approuve par cette machine"
            Write-Detail "installez la partie publique : create-test-certificate.ps1"
        }
        default {
            Write-Bloquant "signature invalide : $($signature.Status) - $($signature.StatusMessage)"
        }
    }
}

# ===========================================================================
#  8. Regles de publication Microsoft Store
# ===========================================================================
Write-Etape "[8/9] Pre-controles Microsoft Store..."

# Ces points ne cassent PAS le paquet : ils determinent s'il peut etre
# PUBLIE. Les signaler ici evite de decouvrir un refus apres televersement.
$config = Get-ConfigMsix
if (Test-IdentiteDeTest -Config $config) {
    Write-Reserve "identite de TEST : remplacez IdentityName/Publisher par les valeurs Partner Center"
}
else {
    Write-Succes "identite de publication (aucune mention TEST)"
}

# --- Les trois champs d'identite que Partner Center confronte au compte ----
# Ils sont refuses AU TELEVERSEMENT, apres plusieurs dizaines de Mo envoyes,
# et le message ne nomme que celui qui a echoue - pas les deux autres. On les
# affiche donc tous les trois cote a cote, pour qu'une relecture de dix
# secondes remplace un aller-retour.
$nomEditeur = $manifeste.Package.Properties.PublisherDisplayName
$nomAffiche = $manifeste.Package.Properties.DisplayName

# Valeurs ENCADREES DE CROCHETS, et ce n'est pas decoratif : ces champs se
# comparent au caractere pres, et l'ecart tient souvent a un caractere qu'on
# ne voit pas. Cas reellement rencontre : un point final recopie depuis un
# message d'erreur francais ("...votre nom complet d'editeur : Muller.J.")
# ou le point etait la PONCTUATION de la phrase. Partner Center affichait
# alors deux valeurs d'apparence identique - "Muller.J." et "Muller.J" -
# impossibles a distinguer a l'oeil nu. Les crochets rendent aussi visible
# toute espace en debut ou fin.
Write-Detail "Identity/Name ................. [$($identite.Name)]"
Write-Detail "Identity/Publisher ............ [$($identite.Publisher)]"
Write-Detail "PublisherDisplayName .......... [$nomEditeur]"
Write-Detail "DisplayName (nom a reserver) .. [$nomAffiche]"

$avecEspaces = @()
foreach ($champ in @(@{ nom = 'Identity/Name'; valeur = $identite.Name },
                     @{ nom = 'Identity/Publisher'; valeur = $identite.Publisher },
                     @{ nom = 'PublisherDisplayName'; valeur = $nomEditeur },
                     @{ nom = 'DisplayName'; valeur = $nomAffiche })) {
    if ($champ.valeur -ne $champ.valeur.Trim()) { $avecEspaces += $champ.nom }
}
if ($avecEspaces.Count -gt 0) {
    Write-Bloquant "espace en debut ou fin de : $($avecEspaces -join ', ') - Partner Center refusera le paquet"
}
else {
    Write-Succes "aucune espace parasite dans les champs d'identite"
}

if ($nomEditeur -eq $nomAffiche) {
    # Erreur reellement rencontree a la premiere soumission. PublisherDisplayName
    # est le nom du COMPTE d'editeur ; DisplayName celui de l'application. Les
    # deux identiques signalent presque toujours la meme valeur recopiee, et
    # Partner Center repond alors :
    #   "L'element PublisherDisplayName ... ne correspond pas a votre nom
    #    complet d'editeur : <compte>"
    Write-Reserve "PublisherDisplayName identique a DisplayName : le Store attend ici le nom de votre COMPTE d'editeur, pas celui de l'application"
}
else {
    Write-Succes "PublisherDisplayName distinct du nom de l'application"
}

if ($identite.Publisher -notmatch '^CN=[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-') {
    # Partner Center attribue un Publisher de la forme CN=<GUID>. Tout autre
    # sujet (un nom lisible, par exemple) vient forcement d'ailleurs.
    Write-Reserve "Publisher n'est pas un CN=<GUID> Partner Center : paquet non publiable en l'etat"
}
else {
    Write-Succes "Publisher au format Partner Center (CN=<GUID>)"
}

if (($identite.Version -split '\.')[3] -eq '0') {
    Write-Succes "revision a 0 : conforme a ce qu'attend le Store"
}
else {
    Write-Reserve "revision non nulle : le Store refuse ce paquet (la revision lui est reservee)"
}

$capacites = @($manifeste.Package.Capabilities.ChildNodes |
                Where-Object { $_.NodeType -eq 'Element' } |
                ForEach-Object { $_.Name })
if ($capacites -contains 'runFullTrust') {
    Write-Reserve "capacite restreinte runFullTrust : revue manuelle du Store a prevoir (normal pour une application de bureau)"
}
Write-Detail "capacites declarees : $($capacites -join ', ')"

# Le telechargement puis l'execution de binaires tiers a l'execution
# (Ollama, llama-server, installeur MetaTrader) est incompatible avec les
# regles de publication. C'est le point bloquant de cette application.
Write-Reserve "l'application telecharge et execute des binaires tiers au 1er lancement - a traiter avant toute soumission (voir WINDOWS-MSIX.md, section N)"

# ===========================================================================
#  9. Windows App Certification Kit / installation reelle
# ===========================================================================
Write-Etape "[9/9] Controles approfondis..."

if ($AvecWack) {
    if (-not $estWindows) {
        Write-Reserve "WACK ignore : Windows requis"
    }
    else {
        $appcert = Find-OutilSdk -Nom "appcert.exe"
        if (-not $appcert) {
            Write-Reserve "Windows App Certification Kit non installe (composant optionnel du SDK Windows)"
            Write-Detail "il reproduit les controles du Store ; a defaut, Partner Center les fera a la soumission"
        }
        else {
            $rapport = Join-Path (Split-Path -Parent $cheminPaquet) "wack-rapport.xml"
            Write-Detail "appcert : $appcert (plusieurs minutes)"
            & $appcert reset | Out-Null
            & $appcert test -appxpackagepath $cheminPaquet -reportoutputpath $rapport
            if ($LASTEXITCODE -eq 0 -and (Test-Path $rapport)) {
                $resultat = ([xml](Get-Content $rapport -Raw)).REPORT.OVERALL_RESULT
                if ($resultat -eq 'PASS') { Write-Succes "WACK : PASS ($rapport)" }
                else { Write-Bloquant "WACK : $resultat - voir $rapport" }
            }
            else {
                Write-Reserve "WACK n'a pas produit de rapport exploitable (code $LASTEXITCODE)"
            }
        }
    }
}
else {
    Write-Detail "WACK non demande (-AvecWack pour le lancer)"
}

if ($AvecInstallation) {
    if (-not $estWindows) {
        Write-Reserve "installation d'essai ignoree : Windows requis"
    }
    else {
        & (Join-Path $PSScriptRoot "install-test.ps1") -Paquet $cheminPaquet -Desinstaller
        if ($LASTEXITCODE -eq 0) { Write-Succes "installation, lancement et desinstallation reussis" }
        else { Write-Bloquant "le test d'installation a echoue (code $LASTEXITCODE)" }
    }
}
else {
    Write-Detail "installation d'essai non demandee (-AvecInstallation pour la lancer)"
}

# ===========================================================================
#  Bilan
# ===========================================================================
Write-Titre "Bilan de la validation"
Write-Host ""
Write-Host "    Controles reussis : $script:nbOk" -ForegroundColor Green
Write-Host "    Reserves          : $script:nbAlertes" -ForegroundColor Yellow
Write-Host "    Echecs            : $script:nbEchecs" -ForegroundColor $(if ($script:nbEchecs) { 'Red' } else { 'Green' })
Write-Host ""

if ($script:nbEchecs -gt 0) {
    Write-Host "  [ECHEC] Le paquet ne doit pas etre distribue en l'etat." -ForegroundColor Red
    Write-Host ""
    exit 1
}
Write-Host "  [OK] Paquet exploitable." -ForegroundColor Green
Write-Host ""
exit 0
