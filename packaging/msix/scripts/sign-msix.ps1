<#
.SYNOPSIS
    Signe un paquet MSIX.

.DESCRIPTION
    Windows n'installe aucun MSIX non signe. Ce script couvre les deux cas
    qui existent reellement - et refuse de les confondre :

      SIGNATURE DE TEST        certificat auto-signe produit par
                               create-test-certificate.ps1, valable sur les
                               seules machines ou il a ete approuve.

      SIGNATURE DE PRODUCTION  certificat fourni par GitHub Secrets
                               (MSIX_CERTIFICATE_BASE64 +
                               MSIX_CERTIFICATE_PASSWORD), pour une
                               distribution hors Store.

    Le Microsoft Store, lui, n'a besoin d'AUCUNE de ces deux signatures : il
    re-signe lui-meme les paquets publies avec le certificat de l'editeur. Un
    .msix non signe est donc parfaitement soumissible - c'est meme le cas
    normal (voir WINDOWS-MSIX.md, section N).

    CONTROLE FAIT AVANT DE SIGNER : le sujet du certificat doit correspondre
    AU CARACTERE PRES au champ Publisher du manifeste. Quand ils different,
    signtool accepte parfois de signer, et c'est Windows qui refuse ensuite
    l'installation - avec un message qui ne mentionne ni l'un ni l'autre. On
    prefere echouer ici, en nommant les deux valeurs.

.PARAMETER Paquet
    Le .msix a signer. Par defaut : le plus recent de dist/msix.

.PARAMETER Certificat
    Un .pfx. Par defaut : MSIX_CERTIFICATE_BASE64 s'il existe, sinon le
    certificat de test de dist/msix/certificat/.

.PARAMETER MotDePasse
    Mot de passe du .pfx. Par defaut : MSIX_CERTIFICATE_PASSWORD, sinon le
    fichier motdepasse.txt ecrit a cote du certificat de test.

.PARAMETER Force
    Signe meme si le sujet du certificat differe du Publisher du manifeste.
    A n'utiliser qu'en connaissance de cause.

.EXAMPLE
    .\packaging\msix\scripts\sign-msix.ps1

.EXAMPLE
    .\packaging\msix\scripts\sign-msix.ps1 -Certificat C:\certs\editeur.pfx -MotDePasse '...'
#>
[CmdletBinding()]
param(
    [string]$Paquet = "",
    [string]$Certificat = "",
    [string]$MotDePasse = "",
    [string]$HorodatageUrl = "http://timestamp.digicert.com",
    [switch]$SansHorodatage,
    [switch]$Force,
    [string]$DossierSortie = ""
)

. "$PSScriptRoot\common.ps1"

Write-Titre "Signature du paquet MSIX"

$cheminPaquet = Resolve-Paquet -Paquet $Paquet -DossierSortie $DossierSortie
Write-Ok "paquet : $(Split-Path -Leaf $cheminPaquet)"

$signtool = Get-OutilSdkObligatoire -Nom "signtool.exe"

# ---------------------------------------------------------------------------
#  Certificat
# ---------------------------------------------------------------------------
Write-Etape "[1/4] Certificat..."

$pfxTemporaire = ""   # a effacer en sortie s'il a ete fabrique ici
$dossierCert = Get-DossierCertificat -DossierSortie $DossierSortie

try {
    if (-not $Certificat -and $env:MSIX_CERTIFICATE_BASE64) {
        # Chemin CI : le certificat arrive en base64 dans un secret GitHub.
        # Il est ecrit dans un fichier TEMPORAIRE, jamais dans le depot, et
        # efface dans le finally - y compris si la signature echoue.
        Write-Detail "certificat fourni par MSIX_CERTIFICATE_BASE64"
        $pfxTemporaire = Join-Path ([System.IO.Path]::GetTempPath()) `
                                   ("msix-" + [guid]::NewGuid().ToString() + ".pfx")
        try {
            $octets = [Convert]::FromBase64String($env:MSIX_CERTIFICATE_BASE64.Trim())
        }
        catch {
            Write-Echec "MSIX_CERTIFICATE_BASE64 n'est pas du base64 valide."
            Write-Detail "Recreez le secret : [Convert]::ToBase64String([IO.File]::ReadAllBytes('cert.pfx'))"
            exit 1
        }
        [System.IO.File]::WriteAllBytes($pfxTemporaire, $octets)
        $Certificat = $pfxTemporaire
    }

    if (-not $Certificat) {
        $Certificat = Join-Path $dossierCert "AgenceNumerique-Test.pfx"
        Write-Detail "certificat de test local"
    }

    if (-not (Test-Path $Certificat)) {
        Write-Echec "Certificat introuvable : $Certificat"
        Write-Host ""
        Write-Host "  Creez le certificat de test :" -ForegroundColor Yellow
        Write-Host "    .\packaging\msix\scripts\create-test-certificate.ps1"
        Write-Host ""
        Write-Host "  ...ou fournissez un certificat existant :" -ForegroundColor Yellow
        Write-Host "    .\packaging\msix\scripts\sign-msix.ps1 -Certificat <chemin.pfx> -MotDePasse <...>"
        exit 1
    }
    Write-Ok "certificat : $Certificat"

    # -- Mot de passe -------------------------------------------------------
    if (-not $MotDePasse) { $MotDePasse = $env:MSIX_CERTIFICATE_PASSWORD }
    if (-not $MotDePasse) {
        $fichierMdp = Join-Path $dossierCert "motdepasse.txt"
        if (Test-Path $fichierMdp) {
            $MotDePasse = (Get-Content $fichierMdp -Raw).Trim()
            Write-Detail "mot de passe lu dans motdepasse.txt (certificat de test)"
        }
    }

    # -----------------------------------------------------------------------
    #  Concordance certificat / manifeste
    # -----------------------------------------------------------------------
    Write-Etape "[2/4] Concordance avec le manifeste..."

    $manifeste = Get-ManifesteDuPaquet -Paquet $cheminPaquet
    $publisher = $manifeste.Package.Identity.Publisher

    $sujetCert = ""
    try {
        if ($MotDePasse) {
            $mdpSecurise = ConvertTo-SecureString -String $MotDePasse -Force -AsPlainText
            $objetCert = Get-PfxCertificate -FilePath $Certificat -Password $mdpSecurise
        }
        else {
            $objetCert = Get-PfxCertificate -FilePath $Certificat
        }
        $sujetCert = $objetCert.Subject
    }
    catch {
        Write-Alerte "sujet du certificat illisible ($($_.Exception.Message))"
        Write-Detail "la concordance ne peut pas etre verifiee avant la signature"
    }

    if ($sujetCert) {
        $a = ConvertTo-SujetNormalise -Sujet $sujetCert
        $b = ConvertTo-SujetNormalise -Sujet $publisher
        if ($a -ne $b) {
            Write-Echec "le sujet du certificat ne correspond pas au Publisher du manifeste"
            Write-Host ""
            Write-Host "    certificat : $a" -ForegroundColor Yellow
            Write-Host "    manifeste  : $b" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "  Le paquet serait signe, puis REFUSE a l'installation." -ForegroundColor DarkGray
            Write-Host "  Alignez l'un sur l'autre :" -ForegroundColor DarkGray
            Write-Host "    - soit MSIX_PUBLISHER=<sujet du certificat> avant build-msix.ps1," -ForegroundColor DarkGray
            Write-Host "    - soit un certificat dont le sujet vaut le Publisher du manifeste." -ForegroundColor DarkGray
            if (-not $Force) { exit 1 }
            Write-Alerte "-Force : signature malgre la difference"
        }
        else {
            Write-Ok "sujet identique au Publisher : $b"
        }
    }

    # -----------------------------------------------------------------------
    #  Signature
    # -----------------------------------------------------------------------
    Write-Etape "[3/4] Signature..."

    $argumentsBase = @("sign", "/fd", "SHA256", "/f", $Certificat)
    if ($MotDePasse) { $argumentsBase += @("/p", $MotDePasse) }

    $argumentsHorodates = $argumentsBase
    if (-not $SansHorodatage) {
        # L'horodatage fait survivre la signature a l'expiration du
        # certificat. Sans lui, le paquet devient "non signe" le jour ou le
        # certificat expire, meme deja installe chez l'utilisateur.
        $argumentsHorodates = $argumentsBase + @("/tr", $HorodatageUrl, "/td", "SHA256")
    }

    $signe = $false
    & $signtool @argumentsHorodates $cheminPaquet
    if ($LASTEXITCODE -eq 0) {
        $signe = $true
    }
    elseif (-not $SansHorodatage) {
        # Un serveur d'horodatage injoignable (reseau d'entreprise, coupure)
        # ne doit pas faire echouer un build de test. On reessaie sans, en le
        # DISANT - une signature non horodatee n'est pas equivalente.
        Write-Alerte "echec avec horodatage (code $LASTEXITCODE) - nouvel essai sans"
        & $signtool @argumentsBase $cheminPaquet
        if ($LASTEXITCODE -eq 0) {
            $signe = $true
            Write-Alerte "paquet signe SANS horodatage : la signature expirera avec le certificat"
        }
    }

    if (-not $signe) {
        Write-Echec "signtool a echoue (code $LASTEXITCODE)"
        Write-Detail "mot de passe du .pfx errone, ou certificat sans usage 'signature de code'"
        exit 1
    }
    Write-Ok "paquet signe"

    # -----------------------------------------------------------------------
    #  Verification
    # -----------------------------------------------------------------------
    Write-Etape "[4/4] Verification de la signature..."

    $signature = Get-AuthenticodeSignature -FilePath $cheminPaquet
    Write-Detail "etat    : $($signature.Status)"
    if ($signature.SignerCertificate) {
        Write-Detail "signe par : $($signature.SignerCertificate.Subject)"
        Write-Detail "empreinte : $($signature.SignerCertificate.Thumbprint)"
    }

    switch ($signature.Status) {
        'Valid' {
            Write-Ok "signature valide et certificat approuve par cette machine"
        }
        'UnknownError' {
            # Cas NORMAL d'un certificat auto-signe pas encore approuve : la
            # signature est correcte, c'est la chaine de confiance qui manque.
            Write-Alerte "signature correcte, mais certificat non approuve par cette machine"
            Write-Detail "installez la partie publique (voir create-test-certificate.ps1)"
        }
        default {
            Write-Echec "signature invalide : $($signature.Status) - $($signature.StatusMessage)"
            exit 1
        }
    }
}
finally {
    if ($pfxTemporaire -and (Test-Path $pfxTemporaire)) {
        Remove-Item $pfxTemporaire -Force -ErrorAction SilentlyContinue
        Write-Detail "certificat temporaire efface"
    }
}

Write-Host ""
exit 0
