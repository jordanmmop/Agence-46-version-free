; ============================================================================
;  Agence Numérique Financière — programme d'installation Windows
;  (Inno Setup 6.1+ ; la version 7 convient aussi)
;
;  Compilation :  scripts\build_installer.bat   (recommandé)
;         ou      iscc installer\agence.iss
;
;  Ce script empaquette le dossier produit par PyInstaller
;  (dist\AgenceNumerique) dans un assistant d'installation classique :
;  choix du dossier, raccourcis, enregistrement dans « Applications
;  installées », désinstallation propre.
;
;  Deux décisions structurantes sont expliquées ici plutôt qu'ailleurs, parce
;  que c'est ici qu'elles se prennent :
;
;  1. INSTALLATION PAR UTILISATEUR (PrivilegesRequired=lowest), et non dans
;     « Program Files ». L'application ne se contente pas de lire son dossier :
;     le terminal MetaTrader 5 embarqué tourne en mode /portable et ÉCRIT ses
;     profils, ses journaux et son fichier config\common.ini juste à côté de
;     son exécutable. Dans « Program Files », Windows refuse ces écritures (ou
;     les redirige silencieusement vers le magasin de virtualisation) : le
;     terminal démarre, refuse la connexion, et rien n'explique pourquoi.
;     Installer dans le profil de l'utilisateur évite le problème à la racine
;     — et supprime au passage l'invite UAC.
;
;  2. LES DONNÉES PERSONNELLES NE SONT PAS DANS {app}. Configuration, base de
;     données de trading, identifiants MetaTrader et modèles IA vivent dans
;     %USERPROFILE%\.agence_financiere. La désinstallation efface donc
;     l'application sans toucher à l'historique — et ne propose de le
;     supprimer qu'explicitement, à la demande.
; ============================================================================

#define AppName "Agence Numérique Financière"
#define AppShortName "AgenceNumerique"
#define AppExeName "AgenceNumerique.exe"
#define AppPublisher "Agence Numérique Financière"
#define AppURL "https://github.com/jordanmmop/Agence-46"

; AppId STABLE : c'est lui, et lui seul, qui fait qu'une nouvelle version
; s'installe PAR-DESSUS l'ancienne au lieu d'apparaître une seconde fois dans
; « Applications installées ». Ne jamais le changer.
#define AppId "{A7F3C2E1-9B4D-4E8A-B6C5-1D0F2A3B4C5D}"

; ── Version : lue dans le fichier VERSION de la racine ─────────────────────
; Source unique, partagée avec l'application (python/utils/version.py) et avec
; la ressource « version » de l'exe compilé.
; SourcePath (dossier de ce script, avec sa barre finale) et non un chemin
; relatif nu : #ifexist resout les chemins relatifs par rapport au dossier
; COURANT de ISCC. Compilé depuis la racine du dépôt — « iscc
; installer\agence.iss », ce que fait la CI — le fichier VERSION passait pour
; absent et l'installeur annonçait la version 0.0.0.
#define VersionFile SourcePath + "..\VERSION"
#ifexist VersionFile
  #define FH FileOpen(VersionFile)
  #define AppVersion Trim(FileRead(FH))
  #expr FileClose(FH)
#else
  #pragma message "Fichier VERSION introuvable — repli sur 0.0.0"
  #define AppVersion "0.0.0"
#endif

; Inno Setup 6.1 minimum : la page de téléchargement (CreateDownloadPage), qui
; récupère le composant WebView2 quand il manque, n'existe pas avant.
#if Ver < EncodeVer(6,1,0)
  #error Inno Setup 6.1 ou plus recent est requis (https://jrsoftware.org/isdl.php)
#endif

; « x64compatible » n'existe qu'à partir d'Inno Setup 6.3 ; avant, la valeur
; équivalente s'écrit « x64 ». Sans ce test, le script ne compile que sur une
; moitié des versions installées chez les gens.
#if Ver >= EncodeVer(6,3,0)
  #define Arch "x64compatible"
#else
  #define Arch "x64"
#endif

; ── Charge utile : le dossier produit par PyInstaller ──────────────────────
#ifndef SourceApp
  #define SourceApp "..\dist\" + AppShortName
#endif
#ifnexist SourcePath + SourceApp + "\" + AppExeName
  #error dist\AgenceNumerique\AgenceNumerique.exe introuvable — lancez d'abord build_exe.bat
#endif

; ── Réglages de compression, pilotés par scripts\build_installer.bat ───────
; Un build « tout embarqué » pèse plusieurs gigaoctets, déjà compressés
; (modèles GGUF, blobs Ollama) : y appliquer lzma2/max en mode solide coûte
; des heures pour quelques pour-cent. Le script de build mesure la charge
; utile et choisit les valeurs adaptées.
#ifndef Compression
  #define Compression "lzma2/max"
#endif
#ifndef Solid
  #define Solid "yes"
#endif
; Au-delà d'environ 2 Go, un exécutable d'installation unique n'est plus
; possible : Inno Setup répartit alors la charge en tranches .bin qui doivent
; accompagner le .exe. Là encore, c'est le script de build qui décide.
#ifndef Spanning
  #define Spanning "no"
#endif

; Amorceur WebView2 livré dans l'installeur ? Décidé À LA COMPILATION (le
; fichier est récupéré par tools/preparer_installeur.py). Le code de
; l'assistant a besoin de le savoir AVANT que les fichiers ne soient extraits :
; à la page « Prêt à installer », le fichier n'existe pas encore dans {tmp},
; et se fier à sa présence sur le disque ferait retélécharger un amorceur
; pourtant déjà embarqué — y compris sur une machine hors ligne.
#ifexist SourcePath + "redist\MicrosoftEdgeWebview2Setup.exe"
  #define WebView2Embarque
#endif

[Setup]
AppId={{#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases

; Installation dans le profil utilisateur : aucune élévation UAC, et un
; dossier réellement inscriptible (voir l'en-tête). L'utilisateur reste libre
; de choisir un autre emplacement dans l'assistant.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no

; L'application est 64 bits (Ollama, MetaTrader 5, la librairie MetaTrader5).
ArchitecturesAllowed={#Arch}
ArchitecturesInstallIn64BitMode={#Arch}

OutputDir=..\dist\installateur
OutputBaseFilename={#AppShortName}-Setup-{#AppVersion}
SetupIconFile=agence.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
Compression={#Compression}
SolidCompression={#Solid}
DiskSpanning={#Spanning}
DiskSliceSize=2100000000

; Avertissement sur le risque financier AVANT l'installation, et mode d'emploi
; APRÈS : un logiciel qui peut passer des ordres réels doit le dire pendant
; l'installation, pas seulement dans un fichier que personne n'ouvrira.
InfoBeforeFile=infos_avant.txt
InfoAfterFile=infos_apres.txt

; Mise à jour par-dessus une version en cours d'exécution : Windows refuse de
; remplacer un fichier verrouillé. Le gestionnaire de redémarrage repère les
; processus concernés (l'application ET le terminal MetaTrader embarqué) et
; propose de les fermer — au lieu d'échouer sur « fichier en cours
; d'utilisation » à mi-installation.
CloseApplications=yes
RestartApplications=no
SetupMutex={#AppShortName}_setup_mutex

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.CreerRaccourciBureau=Créer un raccourci sur le &Bureau
en.CreerRaccourciBureau=Create a &desktop shortcut
fr.LancerApp=Lancer {#AppName}
en.LancerApp=Launch {#AppName}
fr.GroupeRaccourcis=Raccourcis supplémentaires :
en.GroupeRaccourcis=Additional shortcuts:
fr.WebView2Titre=Composant Microsoft Edge WebView2
en.WebView2Titre=Microsoft Edge WebView2 component
fr.WebView2Installation=Installation du composant Microsoft Edge WebView2 (fenêtre de l'application)...
en.WebView2Installation=Installing the Microsoft Edge WebView2 component (application window)...
fr.WebView2Echec=Le composant Microsoft Edge WebView2 n'a pas pu être installé.%n%nL'application fonctionnera quand même : elle s'ouvrira dans votre navigateur au lieu de sa propre fenêtre. Pour retrouver la fenêtre native, installez « Microsoft Edge WebView2 Runtime » depuis le site de Microsoft.
en.WebView2Echec=The Microsoft Edge WebView2 component could not be installed.%n%nThe application will still work: it will open in your browser instead of its own window. To get the native window, install the "Microsoft Edge WebView2 Runtime" from Microsoft.
fr.SupprimerDonnees=Supprimer également vos données personnelles ?%n%nCela effacera DÉFINITIVEMENT :%n  •  l'historique de trading (base de données)%n  •  vos réglages et votre code d'accès%n  •  vos identifiants MetaTrader enregistrés%n  •  les modèles d'IA téléchargés%n%nDossier : %1%n%nRépondez Non pour les conserver (recommandé, notamment avant une réinstallation).
en.SupprimerDonnees=Also delete your personal data?%n%nThis will PERMANENTLY erase:%n  •  the trading history (database)%n  •  your settings and access code%n  •  your saved MetaTrader credentials%n  •  the downloaded AI models%n%nFolder: %1%n%nAnswer No to keep them (recommended, especially before reinstalling).

[Tasks]
Name: "desktopicon"; Description: "{cm:CreerRaccourciBureau}"; GroupDescription: "{cm:GroupeRaccourcis}"

[Files]
; Tout le dossier PyInstaller : exe, runtime Python, dépendances, frontend, et
; — si le build les a préparés — les moteurs IA et MetaTrader embarqués.
Source: "{#SourceApp}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Amorceur WebView2, s'il a été récupéré au build (installation hors ligne).
; Extrait UNIQUEMENT si le composant manque réellement sur la machine.
#ifdef WebView2Embarque
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: WebView2Manquant
#endif

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; \
    Comment: "46 agents IA de trading, entièrement en local"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    WorkingDir: "{app}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Repères pour une future mise à jour automatique : un installeur téléchargé
; par l'application doit pouvoir retrouver OÙ elle est installée et QUELLE
; version tourne, sans le demander à l'utilisateur.
Root: HKCU; Subkey: "Software\{#AppShortName}"; ValueType: string; \
    ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\{#AppShortName}"; ValueType: string; \
    ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey

[Run]
#ifdef WebView2Embarque
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "{cm:WebView2Installation}"; Check: WebView2Manquant; \
    Flags: waituntilterminated runhidden
#endif
Filename: "{app}\{#AppExeName}"; Description: "{cm:LancerApp}"; \
    WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller installe des fichiers ; l'application, elle, en CRÉE d'autres à
; l'exécution dans son propre dossier — profils et journaux du terminal
; MetaTrader portable, caches Python. Sans cette ligne, la désinstallation
; laisserait un dossier orphelin de plusieurs gigaoctets.
; Ne concerne QUE {app} : les données personnelles sont ailleurs (voir
; l'en-tête et CurUninstallStepChanged).
Type: filesandordirs; Name: "{app}"

[Code]
const
  { Identifiant public du « Microsoft Edge WebView2 Runtime » — la fenêtre
    native de l'application (pywebview) s'appuie dessus. Présent d'origine sur
    Windows 11 et sur un Windows 10 à jour, il peut manquer sur une machine
    fraîchement installée. }
  CLE_WEBVIEW2 = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  URL_WEBVIEW2 = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

var
  PageTelechargement: TDownloadWizardPage;
  WebView2Telecharge: Boolean;

function VersionWebView2(Ruche: Integer; Chemin: String): String;
begin
  Result := '';
  if not RegQueryStringValue(Ruche, Chemin, 'pv', Result) then
    Result := '';
  if Result = '0.0.0.0' then
    Result := '';
end;

function WebView2Present(): Boolean;
begin
  Result := (VersionWebView2(HKLM, 'SOFTWARE\WOW6432Node\' + CLE_WEBVIEW2) <> '')
         or (VersionWebView2(HKLM, 'SOFTWARE\' + CLE_WEBVIEW2) <> '')
         or (VersionWebView2(HKCU, 'SOFTWARE\' + CLE_WEBVIEW2) <> '');
end;

{ Utilisé par [Files] et [Run] : n'extraire et n'exécuter l'amorceur que si le
  composant manque vraiment. }
function WebView2Manquant(): Boolean;
begin
  Result := not WebView2Present();
end;

{ L'amorceur voyage-t-il DANS cet installeur ? Répondu à la compilation :
  quand c'est le cas, l'entrée [Run] s'en charge — y compris en mode
  silencieux, où l'assistant ne s'affiche pas et ne peut rien télécharger. }
function AmorceurEmbarque(): Boolean;
begin
#ifdef WebView2Embarque
  Result := True;
#else
  Result := False;
#endif
end;

function DossierDonnees(): String;
begin
  Result := ExpandConstant('{%USERPROFILE}') + '\.agence_financiere';
end;

function OnDownloadProgress(const Url, NomFichier: String;
  const Recu, Total: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
begin
  PageTelechargement := CreateDownloadPage(
    ExpandConstant('{cm:WebView2Titre}'),
    ExpandConstant('{cm:WebView2Installation}'), @OnDownloadProgress);
end;

function NextButtonClick(IdPage: Integer): Boolean;
begin
  Result := True;
  if IdPage <> wpReady then
    Exit;

  { L'amorceur n'a pas été livré avec l'installeur et le composant manque :
    on le récupère maintenant. Un échec n'interrompt PAS l'installation —
    l'application sait s'ouvrir dans le navigateur par défaut, ce qui vaut
    infiniment mieux qu'une installation refusée. }
  WebView2Telecharge := False;
  if WebView2Present() or AmorceurEmbarque() then
    Exit;

  PageTelechargement.Clear;
  PageTelechargement.Add(URL_WEBVIEW2, 'MicrosoftEdgeWebview2Setup.exe', '');
  PageTelechargement.Show;
  try
    try
      PageTelechargement.Download;
      WebView2Telecharge := True;
    except
      MsgBox(ExpandConstant('{cm:WebView2Echec}'), mbInformation, MB_OK);
    end;
  finally
    PageTelechargement.Hide;
  end;
end;

procedure CurStepChanged(Etape: TSetupStep);
var
  Code: Integer;
  Amorceur: String;
begin
  if Etape <> ssPostInstall then
    Exit;
  { Amorceur téléchargé pendant l'assistant (le cas « installeur léger ») :
    l'entrée [Run] ne le connaît pas, on le lance ici. }
  if not WebView2Telecharge then
    Exit;
  Amorceur := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  if not FileExists(Amorceur) then
    Exit;
  if not Exec(Amorceur, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    MsgBox(ExpandConstant('{cm:WebView2Echec}'), mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(Etape: TUninstallStep);
var
  Donnees: String;
begin
  if Etape <> usPostUninstall then
    Exit;

  { Les données personnelles ne sont JAMAIS supprimées sans une réponse
    explicite : historique de trading, réglages, identifiants du courtier et
    modèles d'IA (plusieurs gigaoctets, longs à retélécharger). La question
    n'est même pas posée si le dossier n'existe pas, et une désinstallation
    silencieuse (/VERYSILENT) conserve tout. }
  Donnees := DossierDonnees();
  if not DirExists(Donnees) then
    Exit;
  if UninstallSilent() then
    Exit;
  if MsgBox(FmtMessage(ExpandConstant('{cm:SupprimerDonnees}'), [Donnees]),
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(Donnees, True, True, True);
end;
