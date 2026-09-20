@echo off
title Installation rapide - Agence Numerique Financiere

rem  ATTENTION : ce script est le mode SECOURS, conserve pour les postes ou
rem  Inno Setup n'est pas disponible. Il copie des fichiers et pose deux
rem  raccourcis, rien de plus : ni assistant, ni choix du dossier, ni entree
rem  dans "Applications installees", ni desinstalleur.
rem
rem  Le vrai programme d'installation est genere par build_installer.bat :
rem      dist\installateur\AgenceNumerique-Setup-<version>.exe
rem  C'est celui-la qu'il faut distribuer (voir installer\README.md).

echo.
echo  Installation de Agence Numerique Financiere (mode rapide)...
echo.
echo  [i] Pour une vraie installation Windows (assistant, choix du dossier,
echo      desinstallation propre), lancez build_installer.bat puis
echo      dist\installateur\AgenceNumerique-Setup-<version>.exe
echo.

rem Installation dans le profil utilisateur (%LOCALAPPDATA%) : AUCUN droit
rem administrateur requis. Écrire dans %ProgramFiles% exigerait une élévation
rem UAC ; sans elle, xcopy échouait en silence et créait des raccourcis morts.
set DEST=%LOCALAPPDATA%\AgenceNumerique
set SRC=%~dp0dist\AgenceNumerique

if not exist "%SRC%" (
    echo [ERREUR] Le dossier dist\AgenceNumerique est introuvable.
    echo Lancez d'abord build_exe.bat pour compiler l'application.
    pause
    exit /b 1
)

echo  Copie des fichiers vers "%DEST%"...
xcopy /E /I /Y "%SRC%" "%DEST%" >nul
if errorlevel 1 (
    echo [ERREUR] La copie a echoue. Verifiez l'espace disque et les droits.
    pause
    exit /b 1
)
if not exist "%DEST%\AgenceNumerique.exe" (
    echo [ERREUR] L'executable n'a pas ete copie. Installation interrompue.
    pause
    exit /b 1
)

echo  Creation du raccourci Bureau...
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Agence Numerique.lnk');$s.TargetPath='%DEST%\AgenceNumerique.exe';$s.WorkingDirectory='%DEST%';$s.Description='Agence Numerique Financiere - Trading IA';$s.Save()"

echo  Creation du raccourci Menu Demarrer...
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('StartMenu')+'\Agence Numerique.lnk');$s.TargetPath='%DEST%\AgenceNumerique.exe';$s.WorkingDirectory='%DEST%';$s.Save()"

echo.
echo  Installation terminee !
echo  Un raccourci a ete cree sur votre Bureau.
echo  Double-cliquez sur "Agence Numerique" pour lancer l'application.
echo.
pause
