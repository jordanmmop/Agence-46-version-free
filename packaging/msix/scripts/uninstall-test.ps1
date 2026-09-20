<#
.SYNOPSIS
    Retire proprement le paquet MSIX installe pour les essais.

.DESCRIPTION
    Complement de install-test.ps1, pour les cas ou l'application a ete
    laissee installee (inspection manuelle, essai interrompu).

    LES DONNEES PERSONNELLES NE SONT PAS TOUCHEES par defaut. Elles vivent
    dans %USERPROFILE%\.agence_financiere - historique de trading, reglages,
    identifiants du courtier, modeles d'IA telecharges - c'est-a-dire HORS du
    paquet. C'est voulu, et c'est aussi ce que fait l'installeur Inno Setup :
    reinstaller ne doit pas effacer des mois d'historique.

    Ce n'est pas seulement une politesse : le dossier d'un paquet MSIX est
    supprime integralement a la desinstallation. Des donnees rangees dedans
    disparaitraient a chaque mise a jour du Store, sans avertissement.

.PARAMETER Tout
    Retire aussi les autres paquets dont l'identite commence par le meme nom
    (utile apres des essais avec plusieurs identites de test).

.PARAMETER SupprimerDonnees
    Supprime EN PLUS %USERPROFILE%\.agence_financiere. Irreversible :
    confirmation demandee, sauf avec -Confirmer.

.EXAMPLE
    .\packaging\msix\scripts\uninstall-test.ps1

.EXAMPLE
    .\packaging\msix\scripts\uninstall-test.ps1 -SupprimerDonnees
#>
[CmdletBinding()]
param(
    [string]$NomPaquet = "",
    [switch]$Tout,
    [switch]$SupprimerDonnees,
    [switch]$Confirmer
)

. "$PSScriptRoot\common.ps1"

if (-not (Test-Windows)) {
    Write-Echec "Ce script ne fonctionne que sous Windows (Remove-AppxPackage)."
    exit 1
}

Write-Titre "Desinstallation du paquet de test"

# Nom d'identite : celui de la configuration, sauf indication contraire. On
# ne devine pas a partir des paquets installes - retirer un paquet homonyme
# qui ne serait pas le notre serait pire que de ne rien faire.
if (-not $NomPaquet) {
    $config = Get-ConfigMsix
    $NomPaquet = $config['IdentityName']
}
Write-Detail "identite recherchee : $NomPaquet"

$paquets = @()
if ($Tout) {
    # Le point final evite d'emporter un paquet dont le nom commence par la
    # meme chaine sans etre une variante du notre.
    $paquets = @(Get-AppxPackage | Where-Object {
        $_.Name -eq $NomPaquet -or $_.Name.StartsWith("$NomPaquet.")
    })
}
else {
    $paquets = @(Get-AppxPackage -Name $NomPaquet -ErrorAction SilentlyContinue)
}

if ($paquets.Count -eq 0) {
    Write-Ok "aucun paquet installe sous ce nom - rien a faire"
}
else {
    Write-Etape "[1/2] Arret de l'application..."
    $procs = @(Get-Process -Name "AgenceNumerique" -ErrorAction SilentlyContinue)
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction Stop }
        catch { Write-Detail "processus $($p.Id) : $($_.Exception.Message)" }
    }
    if ($procs.Count -gt 0) {
        Write-Ok "$($procs.Count) processus arrete(s)"
        Start-Sleep -Seconds 3
    }
    else {
        Write-Detail "aucun processus en cours"
    }

    Write-Etape "[2/2] Suppression du ou des paquets..."
    $echecs = 0
    foreach ($p in $paquets) {
        try {
            Remove-AppxPackage -Package $p.PackageFullName -ErrorAction Stop
            Write-Ok "$($p.Name) $($p.Version) retire"
        }
        catch {
            Write-Echec "$($p.Name) : $($_.Exception.Message)"
            $echecs++
        }
    }
    if ($echecs -gt 0) { exit 1 }
}

# ---------------------------------------------------------------------------
#  Donnees personnelles
# ---------------------------------------------------------------------------
$donnees = Join-Path $env:USERPROFILE ".agence_financiere"
Write-Host ""
if (-not (Test-Path $donnees)) {
    Write-Detail "aucune donnee personnelle a $donnees"
}
elseif ($SupprimerDonnees) {
    $taille = 0
    try {
        $taille = (Get-ChildItem $donnees -Recurse -File -ErrorAction SilentlyContinue |
                   Measure-Object -Sum Length).Sum / 1MB
    }
    catch { }
    Write-Host "  SUPPRESSION DEFINITIVE de :" -ForegroundColor Red
    # Parentheses OBLIGATOIRES autour de l'operateur -f : sans elles,
    # PowerShell lit "-f" comme un nom de parametre de Write-Host, ambigu
    # avec -ForegroundColor, et la commande echoue au lieu d'afficher.
    Write-Host ("    $donnees  ({0:N0} Mo)" -f $taille) -ForegroundColor Red
    Write-Host "    historique de trading, reglages, identifiants MetaTrader, modeles IA" -ForegroundColor DarkGray
    $reponse = "o"
    if (-not $Confirmer) {
        $reponse = Read-Host "  Confirmer ? (o/N)"
    }
    if ($reponse -eq "o" -or $reponse -eq "O") {
        Remove-Item $donnees -Recurse -Force
        Write-Ok "donnees supprimees"
    }
    else {
        Write-Detail "conservees"
    }
}
else {
    Write-Host "  Donnees personnelles CONSERVEES :" -ForegroundColor Cyan
    Write-Host "    $donnees"
    Write-Host "    (-SupprimerDonnees pour les effacer aussi)" -ForegroundColor DarkGray
}

Write-Host ""
exit 0
