<#
    Fonctions partagees par tous les scripts d'empaquetage MSIX.

    Ce fichier ne fait RIEN tout seul : chaque script le charge par
    . "$PSScriptRoot\common.ps1"

    DEUX REGLES D'ECRITURE, volontaires, valables pour tous les .ps1 de ce
    dossier :

    1. ASCII PUR, sans accent. Windows PowerShell 5.1 - celui qui est installe
       d'origine sur Windows et que lance un double-clic - lit un .ps1 sans
       BOM comme du texte ANSI : les accents y deviennent des caracteres
       parasites, y compris dans les chaines comparees par le code. Meme
       raison que les .bat du depot (voir .gitattributes).

    2. Fins de ligne CRLF a l'extraction (deja impose par .gitattributes) et
       verbes PowerShell normalises (Get-, Find-, Test-, Write-) : les
       messages sont en francais, les noms de fonctions restent dans la
       convention de l'ecosysteme.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
#  Affichage
# ---------------------------------------------------------------------------
# Une chaine de build qui echoue doit dire OU et POURQUOI. Chaque etape est
# donc annoncee, et chaque verdict porte un prefixe stable ([OK], [!], [ECHEC])
# qu'on peut retrouver dans un journal de CI de plusieurs milliers de lignes.

function Write-Etape {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ""
    Write-Host "  $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Alerte {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "    [!] $Message" -ForegroundColor Yellow
}

function Write-Echec {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "    [ECHEC] $Message" -ForegroundColor Red
}

function Write-Detail {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "         $Message" -ForegroundColor DarkGray
}

function Write-Titre {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ""
    Write-Host "  ===============================================================" -ForegroundColor White
    Write-Host "    $Message" -ForegroundColor White
    Write-Host "  ===============================================================" -ForegroundColor White
}

# ---------------------------------------------------------------------------
#  Plateforme
# ---------------------------------------------------------------------------

function Test-Windows {
    <#  Sommes-nous sous Windows ?

        A ne PAS remplacer par un simple "$IsWindows" : cette variable n'existe
        pas dans Windows PowerShell 5.1 - celui qui est installe d'origine sur
        Windows, et celui que lance un double-clic. Or Set-StrictMode fait de
        la lecture d'une variable inexistante une ERREUR TERMINANTE. Un test
        ecrit "if (-not $IsWindows)" echoue donc exactement sur la version de
        PowerShell la plus repandue, et seulement sur celle-la : le genre de
        panne qu'on ne reproduit jamais sur la machine du developpeur.

        L'ordre des deux tests compte aussi : PowerShell 5.1 sort au premier,
        avant toute lecture de $IsWindows. #>
    if ($PSVersionTable.PSVersion.Major -lt 6) { return $true }
    return [bool]$IsWindows
}

# ---------------------------------------------------------------------------
#  Emplacements
# ---------------------------------------------------------------------------

function Get-RacineDepot {
    <#  packaging/msix/scripts/common.ps1 -> racine du depot.
        Calculee depuis l'emplacement du script et JAMAIS depuis le dossier
        courant : ces scripts doivent pouvoir etre lances depuis n'importe ou
        (double-clic, tache planifiee, CI), sans quoi tous les chemins
        relatifs qui suivent designeraient autre chose. #>
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Get-DossierMsix {
    return (Join-Path (Get-RacineDepot) "packaging\msix")
}

function Get-DossierSortie {
    param([string]$Personnalise = "")
    if ($Personnalise) { return $Personnalise }
    return (Join-Path (Get-RacineDepot) "dist\msix")
}

# ---------------------------------------------------------------------------
#  Version
# ---------------------------------------------------------------------------

function Get-VersionDepot {
    <#  Le fichier VERSION de la racine, source UNIQUE du numero de version -
        la meme que lisent python/utils/version.py, agence.spec et
        installer/agence.iss. Recopier ce numero dans le manifeste MSIX
        garantirait qu'il finisse par differer de celui qu'affiche
        l'application. #>
    $fichier = Join-Path (Get-RacineDepot) "VERSION"
    if (-not (Test-Path $fichier)) {
        throw "Fichier VERSION introuvable a la racine du depot ($fichier)."
    }
    $brut = (Get-Content $fichier -Raw).Trim()
    if (-not $brut) { throw "Le fichier VERSION est vide." }
    return $brut
}

function ConvertTo-VersionMsix {
    <#  "4.0.0" -> "4.0.0.0" : un manifeste MSIX exige QUATRE nombres.

        La quatrieme (la revision) est RESERVEE au Microsoft Store, qui la
        reecrit lui-meme lors de la publication : elle doit valoir 0 dans un
        paquet soumis. -Revision ne sert donc qu'aux essais locaux, ou il faut
        parfois incrementer pour reinstaller par-dessus un paquet existant.

        Les suffixes non numeriques ("4.1.0-rc1") sont tronques a leur
        partie numerique, exactement comme le fait utils/version.py : les deux
        doivent produire le meme numero, sinon l'application et le paquet qui
        la contient annonceraient des versions differentes. #>
    param(
        [Parameter(Mandatory)][string]$Version,
        [int]$Revision = 0
    )
    $morceaux = @()
    foreach ($partie in ($Version -split '\.')) {
        $chiffres = ''
        foreach ($c in $partie.Trim().ToCharArray()) {
            if ($c -match '\d') { $chiffres += $c } else { break }
        }
        if ($chiffres -eq '') { $chiffres = '0' }
        $morceaux += [int]$chiffres
    }
    while ($morceaux.Count -lt 3) { $morceaux += 0 }
    return "{0}.{1}.{2}.{3}" -f $morceaux[0], $morceaux[1], $morceaux[2], $Revision
}

# ---------------------------------------------------------------------------
#  Configuration d'identite
# ---------------------------------------------------------------------------

function Get-ConfigMsix {
    <#  Lit packaging/msix/msix.config.json, puis applique les surcharges par
        variables d'environnement (MSIX_IDENTITY_NAME, MSIX_PUBLISHER...).

        Cette indirection est ce qui permet a la CI de produire un paquet de
        TEST et a une publication de produire un paquet Store a partir du
        MEME depot, sans qu'aucune valeur Partner Center - ni aucun secret -
        n'ait a etre ecrite dans un fichier versionne. #>
    param([string]$Chemin = "")

    if (-not $Chemin) { $Chemin = Join-Path (Get-DossierMsix) "msix.config.json" }
    if (-not (Test-Path $Chemin)) {
        throw "Configuration MSIX introuvable : $Chemin"
    }

    $json = Get-Content $Chemin -Raw -Encoding UTF8 | ConvertFrom-Json
    $config = @{}
    foreach ($p in $json.PSObject.Properties) {
        if ($p.Name.StartsWith('_')) { continue }   # "_lisez-moi" : documentation
        $config[$p.Name] = [string]$p.Value
    }

    $surcharges = @{
        'IdentityName'         = 'MSIX_IDENTITY_NAME'
        'Publisher'            = 'MSIX_PUBLISHER'
        'PublisherDisplayName' = 'MSIX_PUBLISHER_DISPLAY_NAME'
        'DisplayName'          = 'MSIX_DISPLAY_NAME'
        'Description'          = 'MSIX_DESCRIPTION'
        'ApplicationId'        = 'MSIX_APPLICATION_ID'
        'ShortName'            = 'MSIX_SHORT_NAME'
        'BackgroundColor'      = 'MSIX_BACKGROUND_COLOR'
        'Architecture'         = 'MSIX_ARCHITECTURE'
    }
    foreach ($cle in $surcharges.Keys) {
        $valeur = [Environment]::GetEnvironmentVariable($surcharges[$cle])
        if ($valeur) { $config[$cle] = $valeur }
    }

    foreach ($obligatoire in @('IdentityName', 'Publisher', 'PublisherDisplayName',
                               'DisplayName', 'Description', 'ApplicationId',
                               'Executable', 'Architecture')) {
        if (-not $config.ContainsKey($obligatoire) -or -not $config[$obligatoire]) {
            throw "Cle '$obligatoire' absente ou vide dans $Chemin."
        }
    }
    return $config
}

function Test-IdentiteDeTest {
    <#  Cette identite est-elle encore celle, PROVISOIRE, du depot ?

        Un paquet portant ".TEST" s'installe parfaitement en local et ne
        sera JAMAIS accepte par le Store. Le dire au moment du build evite de
        decouvrir le probleme apres avoir televerse plusieurs gigaoctets. #>
    param([Parameter(Mandatory)][hashtable]$Config)
    return ($Config['IdentityName'] -match 'TEST') -or ($Config['Publisher'] -match 'TEST')
}

# ---------------------------------------------------------------------------
#  Outils du SDK Windows
# ---------------------------------------------------------------------------

function Find-OutilSdk {
    <#  Chemin complet de makeappx.exe / signtool.exe / appcert.exe, ou "".

        Ces outils ne sont PAS dans le PATH d'une session Windows ordinaire :
        ils vivent dans "Windows Kits\10\bin\<version>\<arch>". On prend la
        version la plus recente installee. Retourne "" au lieu de lever :
        l'appelant sait, lui, si l'outil est indispensable (makeappx) ou
        facultatif (appcert). #>
    param(
        [Parameter(Mandatory)][string]$Nom,
        [string]$Architecture = "x64"
    )

    $danslePath = Get-Command $Nom -CommandType Application -ErrorAction SilentlyContinue
    if ($danslePath) { return $danslePath.Source }

    $racines = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }

    $candidats = New-Object System.Collections.Generic.List[string]
    foreach ($racine in $racines) {
        $versions = Get-ChildItem -Path $racine -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^10\.\d+\.\d+\.\d+$' } |
            Sort-Object { [version]$_.Name } -Descending
        foreach ($v in $versions) {
            foreach ($arch in @($Architecture, 'x64', 'x86')) {
                $candidats.Add((Join-Path $v.FullName "$arch\$Nom"))
            }
        }
        # Certaines installations anciennes posent les outils a plat.
        $candidats.Add((Join-Path $racine $Nom))
    }

    foreach ($c in $candidats) {
        if (Test-Path $c) { return $c }
    }
    return ""
}

function Get-OutilSdkObligatoire {
    param(
        [Parameter(Mandatory)][string]$Nom,
        [string]$Architecture = "x64"
    )
    $chemin = Find-OutilSdk -Nom $Nom -Architecture $Architecture
    if (-not $chemin) {
        throw @"
$Nom introuvable.

Cet outil fait partie du SDK Windows 10/11. Installez-le une fois :

    winget install -e --id Microsoft.WindowsSDK.10.0.22621

ou cochez "Windows SDK" dans l'installeur de Visual Studio.
Les runners GitHub "windows-latest" l'ont deja.
"@
    }
    return $chemin
}

# ---------------------------------------------------------------------------
#  Lecture d'un paquet deja construit
# ---------------------------------------------------------------------------

function Get-ManifesteDuPaquet {
    <#  Extrait AppxManifest.xml d'un .msix SANS le desempaqueter sur le
        disque. Un MSIX est une archive ZIP : on peut donc lire son manifeste
        pour verifier identite, version et architecture avant meme de signer
        ou d'installer quoi que ce soit. #>
    param([Parameter(Mandatory)][string]$Paquet)

    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $Paquet).Path)
        $entree = $archive.Entries | Where-Object { $_.FullName -eq 'AppxManifest.xml' }
        if (-not $entree) {
            throw "Le paquet ne contient pas AppxManifest.xml : $Paquet"
        }
        $flux = $entree.Open()
        try {
            $lecteur = New-Object System.IO.StreamReader($flux)
            try   { $texte = $lecteur.ReadToEnd() }
            finally { $lecteur.Dispose() }
        }
        finally { $flux.Dispose() }
    }
    finally {
        if ($archive) { $archive.Dispose() }
    }
    return [xml]$texte
}

function Get-FichiersDuPaquet {
    <#  Liste des chemins contenus dans le .msix (pour verifier qu'un
        executable, une DLL ou un asset est REELLEMENT dans le paquet, et pas
        seulement dans le dossier de preparation). #>
    param([Parameter(Mandatory)][string]$Paquet)

    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $Paquet).Path)
        return ,@($archive.Entries | ForEach-Object { $_.FullName })
    }
    finally {
        if ($archive) { $archive.Dispose() }
    }
}

function Get-DernierPaquet {
    <#  Le .msix le plus recent du dossier de sortie. Sert de defaut a
        sign-msix / validate-msix / install-test : enchainer les scripts ne
        doit pas obliger a recopier un nom de fichier qui contient la version. #>
    param([string]$DossierSortie = "")

    $dossier = Get-DossierSortie -Personnalise $DossierSortie
    if (-not (Test-Path $dossier)) { return "" }
    $paquet = Get-ChildItem -Path $dossier -Filter *.msix -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($paquet) { return $paquet.FullName }
    return ""
}

function Resolve-Paquet {
    <#  Paquet demande, ou le dernier construit. Leve un message utile si
        aucun des deux n'existe - "fichier introuvable" sans indiquer ou on
        a cherche ne sert personne. #>
    param(
        [string]$Paquet = "",
        [string]$DossierSortie = ""
    )
    if ($Paquet) {
        if (-not (Test-Path $Paquet)) { throw "Paquet introuvable : $Paquet" }
        return (Resolve-Path $Paquet).Path
    }
    $dernier = Get-DernierPaquet -DossierSortie $DossierSortie
    if (-not $dernier) {
        $dossier = Get-DossierSortie -Personnalise $DossierSortie
        throw @"
Aucun paquet .msix dans $dossier.

Construisez-le d'abord :

    powershell -ExecutionPolicy Bypass -File packaging\msix\scripts\build-msix.ps1
"@
    }
    return $dernier
}

# ---------------------------------------------------------------------------
#  Certificat de test
# ---------------------------------------------------------------------------

function Get-DossierCertificat {
    <#  dist/msix/certificat/ : SOUS dist/, donc ignore par git.

        C'est un choix de securite, pas de commodite : une cle privee ne doit
        jamais pouvoir etre ajoutee au depot par un "git add -A" distrait.
        Le certificat de test est REGENERABLE a tout moment par
        create-test-certificate.ps1 - il n'y a rien a conserver. #>
    param([string]$DossierSortie = "")
    return (Join-Path (Get-DossierSortie -Personnalise $DossierSortie) "certificat")
}

function Test-Administrateur {
    $identite = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identite)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function ConvertTo-SujetNormalise {
    <#  Compare un sujet de certificat et un Publisher de manifeste.

        .NET et le manifeste ecrivent le meme sujet avec des espaces
        differents autour des virgules. Une comparaison brute declare alors
        "publisher different" sur deux chaines identiques - et c'est la
        cause no1 d'un MSIX signe que Windows refuse ensuite d'installer. #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Sujet)
    $morceaux = $Sujet -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    return (($morceaux -join ', '))
}

# ---------------------------------------------------------------------------
#  Generation du manifeste
# ---------------------------------------------------------------------------

function New-ManifesteMsix {
    <#  Remplit le gabarit AppxManifest.xml et rend le texte du manifeste.

        Isole ici, et non dans build-msix.ps1, pour une raison precise : c'est
        la seule etape de la chaine qui ne demande ni Windows, ni le SDK. Elle
        peut donc etre exercee par la suite de tests sur n'importe quelle
        machine, la ou le reste exige un runner Windows. Une erreur d'identite
        ou de version se voit alors en CI, sur chaque commit, et pas seulement
        le jour d'une publication. #>
    param(
        [Parameter(Mandatory)][string]$Gabarit,
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)][string]$Version,
        [string]$Destination = ""
    )

    if (-not (Test-Path $Gabarit)) { throw "Gabarit introuvable : $Gabarit" }
    $texte = Get-Content $Gabarit -Raw -Encoding UTF8

    # Les valeurs viennent d'un fichier de configuration : elles DOIVENT etre
    # echappees avant d'entrer dans du XML. Une esperluette dans un nom
    # d'editeur ("Dupont & Fils") produirait sinon un manifeste invalide, et
    # makeappx signalerait une erreur de syntaxe sans dire d'ou elle vient.
    $valeurParDefaut = {
        param($cle, $defaut)
        if ($Config.ContainsKey($cle) -and $Config[$cle]) { return $Config[$cle] }
        return $defaut
    }

    $remplacements = @{
        'IDENTITY_NAME'          = $Config['IdentityName']
        'PUBLISHER'              = $Config['Publisher']
        'VERSION'                = $Version
        'ARCHITECTURE'           = $Config['Architecture']
        'DISPLAY_NAME'           = $Config['DisplayName']
        'PUBLISHER_DISPLAY_NAME' = $Config['PublisherDisplayName']
        'DESCRIPTION'            = $Config['Description']
        'APPLICATION_ID'         = $Config['ApplicationId']
        'EXECUTABLE'             = $Config['Executable']
        'BACKGROUND_COLOR'       = (& $valeurParDefaut 'BackgroundColor' '#0a0e1a')
        'SHORT_NAME'             = (& $valeurParDefaut 'ShortName' $Config['DisplayName'])
        'MIN_WINDOWS_VERSION'    = (& $valeurParDefaut 'MinWindowsVersion' '10.0.17763.0')
        'MAX_VERSION_TESTED'     = (& $valeurParDefaut 'MaxVersionTested' '10.0.22621.0')
    }
    foreach ($cle in $remplacements.Keys) {
        $valeur = [System.Security.SecurityElement]::Escape([string]$remplacements[$cle])
        $texte = $texte.Replace("{{$cle}}", $valeur)
    }

    # Un jeton oublie passerait la compilation et donnerait une application
    # nommee "{{DISPLAY_NAME}}" dans le menu Demarrer.
    $restants = @([regex]::Matches($texte, '\{\{(\w+)\}\}') |
                  ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    if ($restants.Count -gt 0) {
        throw "Jetons non remplaces dans le manifeste : $($restants -join ', ')"
    }

    if ($Destination) {
        $dossier = Split-Path -Parent $Destination
        if ($dossier -and -not (Test-Path $dossier)) {
            New-Item -ItemType Directory -Force -Path $dossier | Out-Null
        }
        # Sans BOM : makeappx lit l'UTF-8 nu, et un BOM inattendu dans un
        # AppxManifest.xml est refuse par certaines versions de l'outil.
        $encodage = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($Destination, $texte, $encodage)
    }
    return $texte
}

function Test-ManifesteMsix {
    <#  Controles que makeappx ne fait pas tous - ou pas avec un message
        exploitable. Rend la liste des problemes ("" = manifeste correct).

        Chacune de ces regles correspond a un echec reellement constate au
        moment de l'installation ou de la soumission, c'est-a-dire beaucoup
        trop tard : version a trois nombres, Identity Name contenant un
        caractere interdit, Publisher qui n'est pas un nom distinctif. #>
    param([Parameter(Mandatory)][string]$TexteManifeste)

    # ATTENTION : le "," devant chaque valeur rendue n'est pas cosmetique.
    # PowerShell "deroule" une collection rendue par une fonction : une
    # liste VIDE devient $null, et l'appelant qui teste $resultat.Count part
    # en erreur sous Set-StrictMode - c'est-a-dire precisement dans le cas
    # nominal, quand le manifeste est correct.
    $problemes = New-Object System.Collections.Generic.List[string]
    try { $xml = [xml]$TexteManifeste }
    catch {
        $problemes.Add("XML invalide : $($_.Exception.Message)")
        return ,$problemes.ToArray()
    }

    $identite = $xml.Package.Identity
    if ($identite.Version -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        $problemes.Add("Version '$($identite.Version)' : quatre nombres attendus (a.b.c.d).")
    }
    else {
        foreach ($n in ($identite.Version -split '\.')) {
            if ([int]$n -gt 65535) {
                $problemes.Add("Version '$($identite.Version)' : chaque nombre doit rester sous 65536.")
                break
            }
        }
    }
    if ($identite.Name -notmatch '^[A-Za-z0-9][A-Za-z0-9\.\-]{2,49}$') {
        $problemes.Add("Identity Name '$($identite.Name)' : 3 a 50 caracteres, lettres/chiffres/point/tiret.")
    }
    if ($identite.Publisher -notmatch '^CN=') {
        $problemes.Add("Publisher '$($identite.Publisher)' : doit etre un nom distinctif commencant par CN=.")
    }
    if ($identite.ProcessorArchitecture -notin @('x64', 'x86', 'arm64', 'neutral')) {
        $problemes.Add("ProcessorArchitecture '$($identite.ProcessorArchitecture)' inattendue.")
    }

    $app = $xml.Package.Applications.Application
    if (-not $app) { $problemes.Add("Aucune <Application> declaree.") }
    else {
        if ($app.Id -notmatch '^[A-Za-z][A-Za-z0-9\.\-]{0,63}$') {
            $problemes.Add("Application Id '$($app.Id)' : doit commencer par une lettre (64 caracteres max).")
        }
        if ($app.EntryPoint -ne 'Windows.FullTrustApplication') {
            $problemes.Add("EntryPoint '$($app.EntryPoint)' : une application Win32 empaquetee exige Windows.FullTrustApplication.")
        }
        if (-not $app.Executable) { $problemes.Add("Aucun Executable declare.") }
    }

    # runFullTrust est OBLIGATOIRE pour un executable Win32 empaquete. Sans
    # elle, le paquet se construit, s'installe... et l'application est tuee au
    # demarrage sans message.
    # Filtre sur les ELEMENTS : ChildNodes renvoie aussi les commentaires du
    # manifeste, qui apparaitraient sinon dans la liste des capacites.
    $capacites = @($xml.Package.Capabilities.ChildNodes |
                   Where-Object { $_.NodeType -eq 'Element' } |
                   ForEach-Object { $_.Name })
    if ($capacites -notcontains 'runFullTrust') {
        $problemes.Add("Capacite runFullTrust absente : indispensable a une application Win32 empaquetee.")
    }
    return ,$problemes.ToArray()
}
