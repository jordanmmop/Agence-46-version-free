<#
.SYNOPSIS
    Cree le certificat de TEST qui permet d'installer le MSIX sur cette machine.

.DESCRIPTION
    Windows refuse d'installer un MSIX qui n'est pas signe par un certificat
    auquel la machine fait confiance. Pour les essais locaux, on en fabrique
    donc un soi-meme.

    CE CERTIFICAT N'EST PAS UN CERTIFICAT DE PRODUCTION.
    Il ne vaut que sur les machines ou vous l'aurez explicitement installe. Il
    ne permet pas de distribuer l'application, et le Microsoft Store n'en a
    pas besoin : c'est le Store qui RE-SIGNE lui-meme les paquets publies.

    Le sujet du certificat est lu dans msix.config.json (cle Publisher). C'est
    la seule facon d'etre certain qu'il corresponde AU CARACTERE PRES au champ
    Publisher du manifeste : toute difference, meme un espace, produit un
    paquet signe que Windows refuse ensuite d'installer avec un message qui ne
    designe pas la cause.

    La cle privee est ecrite dans dist/msix/certificat/, c'est-a-dire sous
    dist/ - deja ignore par git. Elle n'a aucune valeur : ce script la
    regenere a l'identique en quelques secondes.

.PARAMETER MotDePasse
    Mot de passe du .pfx. A defaut : MSIX_CERTIFICATE_PASSWORD, sinon un mot
    de passe aleatoire ecrit a cote du .pfx (dossier local, ignore par git).

.PARAMETER AnneesValidite
    Duree de validite. 3 ans par defaut - au-dela, un certificat de test
    oublie sur une machine devient une nuisance.

.PARAMETER Supprimer
    Retire le certificat des magasins et efface les fichiers produits.

.EXAMPLE
    .\packaging\msix\scripts\create-test-certificate.ps1

.EXAMPLE
    # En administrateur : installe aussi le certificat dans le magasin de
    # confiance, ce qui est indispensable pour installer le paquet.
    .\packaging\msix\scripts\create-test-certificate.ps1
#>
[CmdletBinding()]
param(
    [string]$MotDePasse = "",
    [int]$AnneesValidite = 3,
    [string]$DossierSortie = "",
    [switch]$Supprimer
)

. "$PSScriptRoot\common.ps1"

if (-not (Test-Windows)) {
    Write-Echec "Ce script ne fonctionne que sous Windows (magasins de certificats Windows)."
    exit 1
}

$config      = Get-ConfigMsix
$sujet       = $config['Publisher']
$dossierCert = Get-DossierCertificat -DossierSortie $DossierSortie
$cheminPfx   = Join-Path $dossierCert "AgenceNumerique-Test.pfx"
$cheminCer   = Join-Path $dossierCert "AgenceNumerique-Test.cer"
$cheminMdp   = Join-Path $dossierCert "motdepasse.txt"
$nomAmical   = "Agence Numerique Financiere - certificat de TEST MSIX"

# ---------------------------------------------------------------------------
#  Suppression
# ---------------------------------------------------------------------------
if ($Supprimer) {
    Write-Titre "Suppression du certificat de test"
    $retires = 0
    foreach ($magasin in @("Cert:\CurrentUser\My",
                           "Cert:\LocalMachine\TrustedPeople",
                           "Cert:\CurrentUser\TrustedPeople")) {
        try {
            $certs = Get-ChildItem $magasin -ErrorAction SilentlyContinue |
                Where-Object { $_.Subject -eq $sujet -or $_.FriendlyName -eq $nomAmical }
            foreach ($c in $certs) {
                Remove-Item $c.PSPath -Force -ErrorAction Stop
                Write-Ok "retire de $magasin ($($c.Thumbprint))"
                $retires++
            }
        }
        catch {
            Write-Alerte "$magasin : $($_.Exception.Message) (droits administrateur requis ?)"
        }
    }
    foreach ($f in @($cheminPfx, $cheminCer, $cheminMdp)) {
        if (Test-Path $f) { Remove-Item $f -Force; Write-Ok "efface : $f" }
    }
    if ($retires -eq 0) { Write-Alerte "aucun certificat correspondant trouve dans les magasins" }
    exit 0
}

# ---------------------------------------------------------------------------
#  Creation
# ---------------------------------------------------------------------------
Write-Titre "Certificat de TEST pour la signature MSIX"
Write-Host ""
Write-Host "  Sujet (= champ Publisher du manifeste) :" -ForegroundColor White
Write-Host "    $sujet" -ForegroundColor Yellow
Write-Host ""

New-Item -ItemType Directory -Force -Path $dossierCert | Out-Null

# -- Mot de passe ----------------------------------------------------------
if (-not $MotDePasse) { $MotDePasse = $env:MSIX_CERTIFICATE_PASSWORD }
$motDePasseGenere = $false
if (-not $MotDePasse) {
    # Aleatoire, et non une valeur en dur : un mot de passe par defaut recopie
    # dans un depot public finit toujours par etre reutilise sur autre chose.
    $octets = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($octets)
    $MotDePasse = [Convert]::ToBase64String($octets)
    $motDePasseGenere = $true
}

Write-Etape "[1/4] Generation du certificat auto-signe..."

# Un certificat deja present pour ce sujet est remplace : deux certificats de
# meme sujet mais d'empreintes differentes feraient signer avec l'un et
# installer la confiance de l'autre, pour un echec incomprehensible.
$existants = Get-ChildItem "Cert:\CurrentUser\My" -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $sujet }
foreach ($ancien in $existants) {
    Write-Detail "remplacement du certificat existant $($ancien.Thumbprint)"
    Remove-Item $ancien.PSPath -Force -ErrorAction SilentlyContinue
}

$certificat = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $sujet `
    -FriendlyName $nomAmical `
    -KeyAlgorithm RSA `
    -KeyLength 2048 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy Exportable `
    -KeyUsage DigitalSignature `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears($AnneesValidite)

Write-Ok "empreinte : $($certificat.Thumbprint)"
Write-Detail "valide jusqu'au $($certificat.NotAfter.ToString('yyyy-MM-dd'))"

# -- Export ----------------------------------------------------------------
Write-Etape "[2/4] Export de la cle privee (.pfx) et de la partie publique (.cer)..."

$mdpSecurise = ConvertTo-SecureString -String $MotDePasse -Force -AsPlainText
Export-PfxCertificate -Cert $certificat -FilePath $cheminPfx -Password $mdpSecurise | Out-Null
Export-Certificate   -Cert $certificat -FilePath $cheminCer -Type CERT | Out-Null

Write-Ok "cle privee  : $cheminPfx"
Write-Ok "public      : $cheminCer"

if ($motDePasseGenere) {
    Set-Content -Path $cheminMdp -Value $MotDePasse -Encoding ASCII -NoNewline
    Write-Ok "mot de passe : $cheminMdp"
    Write-Detail "dossier local, ignore par git, regenerable - ne s'applique"
    Write-Detail "qu'a ce certificat de test, sans valeur hors de cette machine."
}

# -- Confiance -------------------------------------------------------------
Write-Etape "[3/4] Installation dans le magasin de confiance..."

# TrustedPeople de la MACHINE : c'est le magasin que Windows consulte pour
# autoriser l'installation d'un MSIX signe hors Store. Le mettre dans le
# magasin de l'utilisateur ne suffit pas - l'installation echouerait avec
# "certificat non approuve" alors que le certificat est bien present.
$confiance = $false
if (Test-Administrateur) {
    try {
        Import-Certificate -FilePath $cheminCer `
                           -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" | Out-Null
        Write-Ok "installe dans Cert:\LocalMachine\TrustedPeople"
        $confiance = $true
    }
    catch {
        Write-Alerte "installation impossible : $($_.Exception.Message)"
    }
}
else {
    Write-Alerte "session NON administrateur - le certificat n'est pas encore approuve"
    Write-Host ""
    Write-Host "  Lancez CETTE commande dans un PowerShell ADMINISTRATEUR :" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Import-Certificate -FilePath '$cheminCer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople" -ForegroundColor White
    Write-Host ""
    Write-Host "  Sans elle, la signature fonctionnera mais l'INSTALLATION du" -ForegroundColor DarkGray
    Write-Host "  paquet sera refusee par Windows." -ForegroundColor DarkGray
}

# -- Verification ----------------------------------------------------------
Write-Etape "[4/4] Verification..."

$ok = $true
if (-not (Test-Path $cheminPfx)) { Write-Echec "le .pfx n'a pas ete produit"; $ok = $false }
if (-not (Test-Path $cheminCer)) { Write-Echec "le .cer n'a pas ete produit"; $ok = $false }

$sujetCert = ConvertTo-SujetNormalise -Sujet $certificat.Subject
$sujetConf = ConvertTo-SujetNormalise -Sujet $sujet
if ($sujetCert -ne $sujetConf) {
    Write-Echec "sujet du certificat different du Publisher du manifeste"
    Write-Detail "certificat : $sujetCert"
    Write-Detail "manifeste  : $sujetConf"
    $ok = $false
}
else {
    Write-Ok "sujet identique au Publisher du manifeste"
}

Write-Titre "Certificat de test pret"
Write-Host ""
Write-Host "  Partie PUBLIQUE (partageable)  : $cheminCer"
Write-Host "  Cle PRIVEE (ne jamais publier) : $cheminPfx"
Write-Host "  Approuve par la machine        : $(if ($confiance) { 'oui' } else { 'PAS ENCORE - voir ci-dessus' })"
Write-Host ""
Write-Host "  Suite :" -ForegroundColor Cyan
Write-Host "    .\packaging\msix\scripts\build-msix.ps1 -Sign"
Write-Host ""

if (-not $ok) { exit 1 }
exit 0
