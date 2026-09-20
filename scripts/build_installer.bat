@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

REM ===========================================================================
REM  Agence Numerique Financiere - chaine complete : application + installeur
REM
REM      scripts\build_installer.bat            build complet (moteurs embarques)
REM      scripts\build_installer.bat --leger    sans les moteurs IA (~300 Mo)
REM      scripts\build_installer.bat --sans-hermes --modele-ollama llama3.2
REM
REM  Toute option non reconnue est transmise a tools\preparer_runtimes.py.
REM  AGENCE_BUILD_SILENCIEUX=1 : ne pas ouvrir l'explorateur a la fin.
REM  Produit :  dist\AgenceNumerique\        l'application compilee
REM             dist\installateur\AgenceNumerique-Setup-<version>.exe
REM ===========================================================================

set "ARGS_RUNTIME="
set "SAUTER_RUNTIMES="
for %%A in (%*) do (
    if /I "%%~A"=="--leger" (
        set "SAUTER_RUNTIMES=1"
    ) else (
        set "ARGS_RUNTIME=!ARGS_RUNTIME! %%~A"
    )
)

for /f "usebackq delims=" %%V in ("VERSION") do set "VERSION=%%V"
echo.
echo ===========================================================
echo   Agence Numerique Financiere %VERSION% - build Windows
echo ===========================================================
echo.

REM -- 1. Application compilee (PyInstaller) ---------------------------------
REM AGENCE_BUILD_AUTO : build_exe.bat n'ouvre alors ni l'explorateur ni de
REM "pause" - cette chaine doit pouvoir tourner sans personne devant l'ecran.
set "AGENCE_BUILD_AUTO=1"
if defined SAUTER_RUNTIMES (
    echo [1/4] Compilation de l'application ^(build leger, sans moteurs embarques^)...
    set "AGENCE_SANS_RUNTIMES=1"
) else (
    echo [1/4] Compilation de l'application ^(moteurs IA + MetaTrader embarques^)...
)
call build_exe.bat %ARGS_RUNTIME%
if errorlevel 1 (
    echo [ERREUR] La compilation de l'application a echoue. Installeur non genere.
    goto :fin_erreur
)
if not exist "dist\AgenceNumerique\AgenceNumerique.exe" (
    echo [ERREUR] dist\AgenceNumerique\AgenceNumerique.exe introuvable.
    goto :fin_erreur
)

REM -- 2. Ressources de l'installeur (icone, amorceur WebView2) --------------
echo.
echo [2/4] Preparation des ressources de l'installeur...
python tools\preparer_installeur.py
if errorlevel 1 echo [!] Preparation incomplete - l'installeur telechargera WebView2 si besoin.

REM -- 3. Localisation du compilateur Inno Setup -----------------------------
echo.
echo [3/4] Recherche du compilateur Inno Setup...
REM  Inno Setup 7 d'abord (ce que winget installe aujourd'hui), puis 6 :
REM  les deux compilent ce script. Inno Setup 7 exige Windows 11 ; sur
REM  Windows 10, c'est la 6 qu'il faut installer.
REM  %ProgramFiles(x86)% recopie dans une variable dont le nom ne contient
REM  PAS de parenthese : ecrit tel quel dans la liste entre parentheses
REM  ci-dessous, le ")" de son nom refermerait le bloc des l'analyse de la
REM  ligne par cmd.exe, et la recherche du compilateur partirait en erreur.
set "PF=%ProgramFiles%"
set "PF86=%ProgramFiles(x86)%"
set "ISCC="
for %%P in (
    "%PF%\Inno Setup 7\ISCC.exe"
    "%PF86%\Inno Setup 7\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
    "%PF86%\Inno Setup 6\ISCC.exe"
    "%PF%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do if not defined ISCC if exist %%P set "ISCC=%%~P"
if not defined ISCC for /f "delims=" %%P in ('where iscc 2^>nul') do if not defined ISCC set "ISCC=%%P"

if not defined ISCC (
    echo.
    echo [ERREUR] Inno Setup est introuvable (versions 6 et 7 cherchees).
    echo.
    echo   L'application est compilee ^(dist\AgenceNumerique^), mais l'installeur
    echo   ne peut pas etre genere sans son compilateur.
    echo.
    echo   Installez-le une fois pour toutes :
    echo       winget install -e --id JRSoftware.InnoSetup
    echo   ou telechargez-le sur https://jrsoftware.org/isdl.php
    echo.
    echo   Puis relancez ce script.
    goto :fin_erreur
)
echo       %ISCC%

REM -- 4. Compression adaptee au volume, puis compilation de l'installeur ----
REM  Un build "tout embarque" pese plusieurs Go DEJA compresses (modeles GGUF,
REM  blobs Ollama) : lzma2/max en mode solide y coute des heures pour quelques
REM  pour-cent. Et au-dela d'environ 2 Go, un .exe unique n'est plus possible :
REM  Inno Setup doit repartir la charge en tranches .bin, a distribuer AVEC le
REM  .exe. On mesure donc, et on choisit.
for /f %%S in ('powershell -NoProfile -Command "[math]::Round((Get-ChildItem -Recurse -Force -File 'dist\AgenceNumerique' ^| Measure-Object -Sum Length).Sum / 1MB)" 2^>nul') do set "TAILLE_MO=%%S"
if not defined TAILLE_MO set "TAILLE_MO=0"

set "DEFINES=/DCompression=lzma2/max /DSolid=yes /DSpanning=no"
if %TAILLE_MO% GTR 1800 set "DEFINES=/DCompression=lzma2/fast /DSolid=no /DSpanning=yes"

echo.
echo [4/4] Generation de l'installeur ^(charge utile : %TAILLE_MO% Mo^)...
echo       %DEFINES%
echo.
"%ISCC%" %DEFINES% "installer\agence.iss"
if errorlevel 1 (
    echo.
    echo [ERREUR] La generation de l'installeur a echoue ^(voir les messages ci-dessus^).
    goto :fin_erreur
)

echo.
echo ===========================================================
echo   [OK] Installeur genere
echo ===========================================================
dir /b "dist\installateur"
echo.
echo   Dossier : %CD%\dist\installateur
if %TAILLE_MO% GTR 1800 (
    echo.
    echo   [!] Charge utile ^> 2 Go : l'installeur est decoupe en tranches.
    echo       Distribuez le .exe AVEC tous les fichiers .bin du meme dossier.
)
echo.
if not defined AGENCE_BUILD_SILENCIEUX explorer "dist\installateur"
endlocal
exit /b 0

:fin_erreur
endlocal
exit /b 1
