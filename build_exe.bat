@echo off
setlocal enabledelayedexpansion
title Agence Numerique - Compilation de l'application

rem ==========================================================================
rem  Agence Numerique Financiere - compilation de l'application Windows
rem
rem      build_exe.bat            l'application seule
rem      build_installer.bat      l'application + son programme d'installation
rem
rem  REGLE DE CE SCRIPT : il ne se ferme JAMAIS sans dire pourquoi. Toute
rem  sortie passe par :fin, qui affiche le motif et attend une touche. Une
rem  fenetre qui se referme d'elle-meme ne laisse rien a comprendre, et c'est
rem  exactement ce qui rendait un echec impossible a diagnostiquer.
rem
rem  Ecrit SANS bloc "if (...)" multi-ligne, volontairement : l'analyseur de
rem  cmd.exe y est fragile, et une erreur de syntaxe ferme la fenetre avant
rem  meme la premiere ligne affichee. Tout passe par des etiquettes "goto".
rem
rem  Ce fichier doit rester en ASCII pur avec des fins de ligne CRLF
rem  (voir .gitattributes) : cmd.exe ne lit correctement ni l'UTF-8 sans BOM,
rem  ni les fins de ligne Unix.
rem ==========================================================================

set "MOTIF="
set "CODE=0"

rem -- Le script doit tourner depuis son propre dossier ----------------------
rem  cmd.exe ne sait pas se placer sur un chemin reseau (\\serveur\partage) :
rem  il resterait dans C:\Windows\System32 et tout echouerait ensuite.
set "LECTEUR=%~d0"
if "!LECTEUR:~0,2!"=="\\" goto :chemin_reseau
cd /d "%~dp0"

echo.
echo  ===============================================================
echo    Agence Numerique Financiere - Compilation
echo  ===============================================================
echo.

rem -- Le dossier du projet est-il complet ? ---------------------------------
rem  Lancer le .bat directement depuis la fenetre d'apercu du ZIP est l'erreur
rem  la plus frequente : Windows recopie le seul fichier dans un dossier
rem  temporaire, sans le reste du projet. Rien de ce qui suit ne peut marcher.
if not exist "agence.spec" goto :projet_incomplet
if not exist "python\requirements.txt" goto :projet_incomplet
if not exist "app_window.py" goto :projet_incomplet

rem -- Python ----------------------------------------------------------------
rem  Test en deux temps, sans operateur "&&" : dans "if cond a && b", cmd.exe
rem  rattache le "&&" a l'instruction IF, laquelle reussit meme quand sa
rem  condition est fausse. "py -3" ecraserait alors le "python" deja trouve.
set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :python_trouve
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_trouve
goto :pas_de_python

:python_trouve
for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set "VERSION_PY=%%V"
echo  Python detecte : !VERSION_PY!   ^(commande : %PY%^)
echo.
echo  L'application compilee embarque TOUT : Ollama, Hermes et un terminal
echo  MetaTrader 5 PORTABLE deja pret. L'utilisateur final n'installera rien.
echo  Prevoyez 5 a 10 Go d'espace disque et 30 a 60 min la premiere fois.
echo.

rem -- 1/4  Dependances Python -----------------------------------------------
set "JOURNAL=%CD%\build.log"
echo  [1/4] Installation des dependances Python...
echo        Sortie detaillee dans build.log
rem  Redirection ECRITE EN TETE de ligne : "echo texte > fichier" avec le ">"
rem  colle au texte est fragile (un chiffre juste avant serait pris pour un
rem  descripteur de fichier).
> "%JOURNAL%" echo === %DATE% %TIME% - installation des dependances ===
%PY% -m pip install --upgrade pip >> "%JOURNAL%" 2>&1
%PY% -m pip install -r python\requirements.txt -r backend\requirements.txt >> "%JOURNAL%" 2>&1
if errorlevel 1 goto :echec_dependances
rem  pywebview : indispensable a la FENETRE native (app_window.py). Sans lui,
rem  l'exe est construit sans interface (console=False) : double-clic =
rem  processus fantome invisible.
%PY% -m pip install pyinstaller pywebview >> "%JOURNAL%" 2>&1
if errorlevel 1 goto :echec_dependances
echo        OK
echo.

rem -- 2/4  Moteurs embarques ------------------------------------------------
rem  --sans-hermes / --sans-ollama / --sans-mt5 pour un build allege.
rem  --sans-terminal-mt5 : embarquer l'installeur MetaTrader au lieu du
rem  terminal portable (build plus leger, installation au 1er demarrage).
rem  Le terminal embarque est celui d'AVATRADE : un MetaTrader generique ne
rem  connait aucun serveur AvaTrade et ne peut pas se connecter au compte.
rem  Le script refuse d'en embarquer un (--terminal-mt5-generique force).
rem  CDN AvaTrade inaccessible ? Telechargez l'installeur depuis avatrade.fr :
rem     set MT5_SETUP_URL=file:///C:/chemin/avatrade5setup.exe
rem  NOTE : produire le terminal portable INSTALLE MetaTrader 5 sur CETTE
rem  machine, puis en copie le dossier. C'est la seule facon d'obtenir un
rem  MetaTrader portable - MetaQuotes ne distribue qu'un installeur.
rem  --modele-ollama / --modele-hermes pour choisir d'autres modeles.
rem  AGENCE_SANS_RUNTIMES=1 : build leger (~300 Mo au lieu de 5 a 10 Go),
rem  pose par "build_installer.bat --leger" et par la CI.
echo  [2/4] Moteurs embarques ^(Ollama, Hermes, MetaTrader 5^)...
if defined AGENCE_SANS_RUNTIMES goto :sans_runtimes
echo        Ce qui est deja dans runtime\ est conserve.
%PY% tools\preparer_runtimes.py %*
if errorlevel 1 echo        [!] Preparation incomplete - l'application telechargera ce qui manque au 1er lancement.
goto :compilation

:sans_runtimes
echo        Ignore : build leger demande ^(AGENCE_SANS_RUNTIMES=1^).
echo        Les moteurs seront telecharges au premier lancement.

rem -- 3/4  Compilation ------------------------------------------------------
:compilation
echo.
echo  [3/4] Compilation PyInstaller ^(10 a 15 min, plus la copie des moteurs^)...
echo.
%PY% -m PyInstaller agence.spec --noconfirm --clean
echo.

rem -- 4/4  Verification -----------------------------------------------------
echo  [4/4] Verification...
if not exist "dist\AgenceNumerique\AgenceNumerique.exe" goto :echec_compilation
echo        OK - l'application est dans dist\AgenceNumerique\
if not defined AGENCE_SANS_RUNTIMES %PY% tools\preparer_runtimes.py --verifier
set "MOTIF=Compilation terminee : dist\AgenceNumerique\AgenceNumerique.exe"
if not defined AGENCE_BUILD_AUTO start "" explorer "dist\AgenceNumerique"
goto :fin

rem =========================  SORTIES EN ERREUR  ============================

:chemin_reseau
set "CODE=1"
set "MOTIF=Projet sur un lecteur reseau : copiez-le sur un disque local, par exemple C:\Agence-46, puis relancez."
echo  [ERREUR] Ce dossier est sur un chemin reseau ^(\\...^).
echo           cmd.exe ne sait pas s'y placer comme dossier courant.
echo           Copiez le projet sur un disque local, par exemple C:\Agence-46.
goto :fin

:projet_incomplet
set "CODE=1"
set "MOTIF=Projet incomplet : agence.spec, python\requirements.txt ou app_window.py manque."
echo  [ERREUR] Les fichiers du projet sont introuvables dans :
echo           %CD%
echo.
echo  Causes habituelles :
echo    - le .bat a ete lance DEPUIS l'archive ZIP, sans l'extraire.
echo      Clic droit sur le ZIP ^> "Extraire tout", puis relancez
echo      build_exe.bat depuis le dossier extrait ;
echo    - le fichier a ete copie seul, sans le reste du projet.
goto :fin

:pas_de_python
set "CODE=1"
set "MOTIF=Python introuvable : ni la commande python, ni py -3."
echo  [ERREUR] Python est introuvable.
echo.
echo  Installez Python 3.11 ou plus recent :
echo      winget install -e --id Python.Python.3.11
echo  ou telechargez-le sur https://www.python.org/downloads/
echo.
echo  IMPORTANT : cochez "Add python.exe to PATH" pendant l'installation.
echo  Si vous venez de l'installer, FERMEZ cette fenetre et relancez le
echo  script : le PATH n'est lu qu'a l'ouverture d'une fenetre.
goto :fin

:echec_dependances
set "CODE=1"
set "MOTIF=Echec de 'pip install'. Journal complet : %JOURNAL%"
echo.
echo  [ERREUR] L'installation des dependances a echoue. Dernieres lignes :
echo  ---------------------------------------------------------------
powershell -NoProfile -Command "Get-Content '%JOURNAL%' -Tail 25" 2>nul
if errorlevel 1 type "%JOURNAL%"
echo  ---------------------------------------------------------------
goto :fin

:echec_compilation
set "CODE=1"
set "MOTIF=La compilation a echoue - voir les messages de PyInstaller ci-dessus."
echo  [ERREUR] dist\AgenceNumerique\AgenceNumerique.exe n'a pas ete produit.
echo           Le message d'erreur de PyInstaller est affiche juste au-dessus.
goto :fin

rem =============================  SORTIE  ===================================
:fin
echo.
echo  ===============================================================
if "%CODE%"=="0" echo    [OK] %MOTIF%
if not "%CODE%"=="0" echo    [ECHEC] %MOTIF%
echo  ===============================================================
echo.
if not defined AGENCE_BUILD_AUTO pause
endlocal & exit /b %CODE%
