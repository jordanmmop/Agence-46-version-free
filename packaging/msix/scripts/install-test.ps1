<#
.SYNOPSIS
    Installe le paquet MSIX, le lance, verifie qu'il repond, puis le desinstalle.

.DESCRIPTION
    C'est le seul controle qui prouve quelque chose : un paquet peut passer
    toutes les validations statiques et ne pas demarrer chez l'utilisateur.

    Le parcours reproduit exactement celui d'un utilisateur :

        MSIX -> installation -> ACTIVATION par le menu Demarrer
             -> le serveur repond -> la version servie est la bonne
             -> fermeture -> desinstallation

    L'activation passe par "shell:AppsFolder\<famille>!<application>",
    c'est-a-dire le meme chemin que le menu Demarrer. Lancer directement
    l'executable depuis le dossier d'installation testerait les fichiers,
    PAS le paquet : un manifeste mal forme, une capacite manquante ou un
    identifiant d'application errone passeraient inapercus.

.PARAMETER Desinstaller
    Desinstalle a la fin. Sans ce drapeau, l'application reste installee pour
    inspection manuelle.

.PARAMETER SansLancement
    Installe seulement (utile si aucune session graphique n'est disponible).

.PARAMETER ModeLancement
    MenuDemarrer  activation par "shell:AppsFolder" - le vrai parcours.
    Executable    lance l'executable installe avec --serveur-interne.
    Auto          (defaut) essaie le menu Demarrer, et bascule sur
                  l'executable si aucun processus n'apparait. Une machine
                  sans session interactive - un runner d'integration
                  continue, typiquement - ne peut pas activer une application
                  par le shell ; le test doit alors verifier ce qu'il PEUT
                  verifier, et DIRE lequel des deux chemins a servi, plutot
                  que d'echouer pour une raison d'environnement.

.PARAMETER DelaiDemarrage
    Secondes d'attente avant de declarer que le serveur n'a pas repondu. Le
    premier demarrage charge 46 agents : 180 s par defaut, comme app_window.py.

.EXAMPLE
    .\packaging\msix\scripts\install-test.ps1

.EXAMPLE
    .\packaging\msix\scripts\install-test.ps1 -Desinstaller
#>
[CmdletBinding()]
param(
    [string]$Paquet = "",
    [switch]$Desinstaller,
    [switch]$SansLancement,
    [ValidateSet('Auto', 'MenuDemarrer', 'Executable')][string]$ModeLancement = 'Auto',
    [int]$DelaiDemarrage = 180,
    [string]$DossierSortie = ""
)

. "$PSScriptRoot\common.ps1"

if (-not (Test-Windows)) {
    Write-Echec "Ce script ne fonctionne que sous Windows (Add-AppxPackage)."
    exit 1
}

Write-Titre "Test d'installation du paquet MSIX"

$cheminPaquet = Resolve-Paquet -Paquet $Paquet -DossierSortie $DossierSortie
$manifeste    = Get-ManifesteDuPaquet -Paquet $cheminPaquet
$nomPaquet    = $manifeste.Package.Identity.Name
$idApp        = $manifeste.Package.Applications.Application.Id
$executable   = $manifeste.Package.Applications.Application.Executable
$nomProcessus = [System.IO.Path]::GetFileNameWithoutExtension($executable)

Write-Ok "paquet : $(Split-Path -Leaf $cheminPaquet)"
Write-Detail "identite    : $nomPaquet"
Write-Detail "application : $idApp"

$etat = @{ Installe = $false; Lance = $false; Desinstalle = $false }

function Stop-Application {
    <#  Arrete l'application ET son processus serveur.

        app_window.py demarre le serveur dans un processus SEPARE, portant le
        meme nom d'executable. Ne tuer que la fenetre laisserait le serveur
        vivant, le port occupe, et la desinstallation echouerait sur des
        fichiers verrouilles. #>
    $procs = Get-Process -Name $nomProcessus -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction Stop }
        catch { Write-Detail "arret du processus $($p.Id) : $($_.Exception.Message)" }
    }
    if ($procs) { Start-Sleep -Seconds 3 }
    return @($procs).Count
}

# ===========================================================================
#  1. Etat de depart propre
# ===========================================================================
Write-Etape "[1/6] Etat de depart..."

$dejaInstalle = Get-AppxPackage -Name $nomPaquet -ErrorAction SilentlyContinue
if ($dejaInstalle) {
    Write-Detail "version deja installee : $($dejaInstalle.Version) - retiree pour repartir propre"
    Stop-Application | Out-Null
    Remove-AppxPackage -Package $dejaInstalle.PackageFullName -ErrorAction SilentlyContinue
}
Write-Ok "aucune installation residuelle"

# ===========================================================================
#  2. Installation
# ===========================================================================
Write-Etape "[2/6] Installation..."

try {
    Add-AppxPackage -Path $cheminPaquet -ErrorAction Stop
    $etat.Installe = $true
    Write-Ok "installe"
}
catch {
    Write-Echec "installation refusee : $($_.Exception.Message)"
    Write-Host ""
    # Les deux causes qui representent la quasi-totalite des refus.
    Write-Host "  Causes habituelles :" -ForegroundColor Yellow
    Write-Host "    - paquet non signe, ou certificat non approuve par cette machine :" -ForegroundColor DarkGray
    Write-Host "        .\packaging\msix\scripts\create-test-certificate.ps1   (en administrateur)" -ForegroundColor DarkGray
    Write-Host "        .\packaging\msix\scripts\sign-msix.ps1" -ForegroundColor DarkGray
    Write-Host "    - installation d'applications hors Store desactivee :" -ForegroundColor DarkGray
    Write-Host "        Parametres > Systeme > Pour les developpeurs > Mode developpeur" -ForegroundColor DarkGray
    exit 1
}

$installe = Get-AppxPackage -Name $nomPaquet
if (-not $installe) {
    Write-Echec "le paquet n'apparait pas dans Get-AppxPackage apres installation"
    exit 1
}
Write-Detail "version   : $($installe.Version)"
Write-Detail "famille   : $($installe.PackageFamilyName)"
Write-Detail "dossier   : $($installe.InstallLocation)"

# ===========================================================================
#  3. Contenu installe
# ===========================================================================
Write-Etape "[3/6] Contenu installe..."

$exeInstalle = Join-Path $installe.InstallLocation $executable
if (Test-Path $exeInstalle) {
    Write-Ok "executable en place : $executable"
}
else {
    Write-Echec "executable absent du dossier installe : $exeInstalle"
    exit 1
}

# ===========================================================================
#  4. Lancement
# ===========================================================================
Write-Etape "[4/6] Lancement..."

$versionAttendue = Get-VersionDepot
$versionServie   = ""
$cheminUtilise   = ""

function Wait-Reponse {
    <#  Interroge /api/health et rend la version servie, ou "".

        L'application choisit son port toute seule a partir de 8765 (une
        instance precedente peut occuper le sien) : on balaie la plage
        qu'elle parcourt, au lieu de supposer un port fixe qui serait faux
        une fois sur deux. #>
    param([int]$Secondes)
    $limite = (Get-Date).AddSeconds($Secondes)
    while ((Get-Date) -lt $limite) {
        Start-Sleep -Seconds 3
        foreach ($port in 8765..8784) {
            try {
                $reponse = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" `
                                             -TimeoutSec 2 -ErrorAction Stop
                if ($reponse.statut -eq "ok") {
                    Write-Ok "le serveur repond sur le port $port"
                    return [string]$reponse.version
                }
            }
            catch { }   # port ferme, ou occupe par autre chose : on continue
        }
    }
    return ""
}

if ($SansLancement) {
    Write-Detail "(-SansLancement : lancement ignore)"
}
else {
    # --- Chemin normal : activation comme le ferait le menu Demarrer -------
    # Lancer directement l'executable testerait les FICHIERS, pas le PAQUET :
    # un manifeste mal forme ou un Application Id errone passeraient
    # inapercus. On essaie donc toujours celui-ci en premier.
    if ($ModeLancement -ne 'Executable') {
        $cible = "shell:AppsFolder\$($installe.PackageFamilyName)!$idApp"
        Write-Detail $cible
        try {
            Start-Process -FilePath "explorer.exe" -ArgumentList $cible -ErrorAction Stop
            $attente = if ($ModeLancement -eq 'MenuDemarrer') { $DelaiDemarrage } else { 45 }
            $versionServie = Wait-Reponse -Secondes $attente
            if ($versionServie) { $cheminUtilise = "menu Demarrer (activation du paquet)" }
        }
        catch {
            Write-Alerte "activation impossible : $($_.Exception.Message)"
        }
    }

    # --- Repli : l'executable installe, en mode serveur --------------------
    if (-not $versionServie -and $ModeLancement -ne 'MenuDemarrer') {
        if ($ModeLancement -eq 'Auto') {
            Write-Alerte "aucune reponse apres activation - repli sur l'executable installe"
            Write-Detail "cause probable : session non interactive (runner d'integration continue)"
            Stop-Application | Out-Null
        }
        # --serveur-interne : le serveur seul, sans fenetre. C'est ce que la
        # machine peut faire sans bureau, et cela verifie tout de meme que les
        # fichiers INSTALLES (et non ceux du dossier de build) fonctionnent.
        $env:AGENCE_PORT = "8765"
        Start-Process -FilePath $exeInstalle -ArgumentList "--serveur-interne"
        $versionServie = Wait-Reponse -Secondes $DelaiDemarrage
        if ($versionServie) { $cheminUtilise = "executable installe (--serveur-interne)" }
    }

    if ($versionServie) {
        $etat.Lance = $true
        Write-Ok "demarre via : $cheminUtilise"
        if ($versionServie -eq $versionAttendue) {
            Write-Ok "version servie : $versionServie (conforme au fichier VERSION)"
        }
        else {
            # Le paquet contient alors un autre etat du depot que celui qu'on
            # croit tester : c'est exactement l'ecart qui fait publier une
            # version pour une autre.
            Write-Echec "version servie $versionServie, attendue $versionAttendue"
            $etat.Lance = $false
        }
    }
    else {
        Write-Echec "aucune reponse sur /api/health apres $DelaiDemarrage s"
        $journal = Join-Path $env:USERPROFILE ".agence_financiere\serveur.log"
        if (Test-Path $journal) {
            Write-Host ""
            Write-Host "  Dernieres lignes de $journal :" -ForegroundColor Yellow
            Get-Content $journal -Tail 30 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        else {
            Write-Detail "aucun journal serveur : l'application n'a pas demarre du tout"
        }
    }
}

# ===========================================================================
#  5. Fermeture
# ===========================================================================
Write-Etape "[5/6] Fermeture..."

$arretes = Stop-Application
if ($arretes -gt 0) { Write-Ok "$arretes processus arrete(s)" }
else { Write-Detail "aucun processus a arreter" }

# ===========================================================================
#  6. Desinstallation
# ===========================================================================
Write-Etape "[6/6] Desinstallation..."

if ($Desinstaller) {
    try {
        Remove-AppxPackage -Package $installe.PackageFullName -ErrorAction Stop
        Start-Sleep -Seconds 2
        if (Get-AppxPackage -Name $nomPaquet -ErrorAction SilentlyContinue) {
            Write-Echec "le paquet est toujours installe apres Remove-AppxPackage"
        }
        else {
            $etat.Desinstalle = $true
            Write-Ok "desinstalle"
            # Les donnees personnelles vivent dans %USERPROFILE%\.agence_financiere,
            # HORS du paquet : elles survivent volontairement a la
            # desinstallation, comme avec l'installeur Inno Setup.
            $donnees = Join-Path $env:USERPROFILE ".agence_financiere"
            if (Test-Path $donnees) {
                Write-Detail "donnees personnelles conservees : $donnees"
            }
        }
    }
    catch {
        Write-Echec "desinstallation impossible : $($_.Exception.Message)"
    }
}
else {
    Write-Detail "application laissee installee (-Desinstaller pour la retirer)"
    Write-Detail "ou : .\packaging\msix\scripts\uninstall-test.ps1"
}

# ===========================================================================
#  Bilan
# ===========================================================================
Write-Titre "Bilan du test d'installation"
Write-Host ""
Write-Host "    Installation    : $(if ($etat.Installe) { 'OK' } else { 'ECHEC' })"
if ($SansLancement) {
    Write-Host "    Lancement       : non teste (-SansLancement)"
}
else {
    Write-Host "    Lancement       : $(if ($etat.Lance) { 'OK' } else { 'ECHEC' })$(if ($cheminUtilise) { "  ($cheminUtilise)" })"
}
if ($Desinstaller) {
    Write-Host "    Desinstallation : $(if ($etat.Desinstalle) { 'OK' } else { 'ECHEC' })"
}
else {
    Write-Host "    Desinstallation : non demandee"
}
Write-Host ""

$echec = (-not $etat.Installe) `
    -or ((-not $SansLancement) -and (-not $etat.Lance)) `
    -or ($Desinstaller -and (-not $etat.Desinstalle))
if ($echec) { exit 1 }
exit 0
