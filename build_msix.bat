@echo off
setlocal
title Agence Numerique - Paquet MSIX

rem ==========================================================================
rem  Agence Numerique Financiere - paquet MSIX (Microsoft Store / sideload)
rem
rem      build_msix.bat                application + paquet MSIX
rem      build_msix.bat -Sign          ... et signature avec le certificat de test
rem      build_msix.bat -DryRun        verifie la configuration sans rien produire
rem      build_msix.bat -SkipAppBuild  reutilise dist\AgenceNumerique tel quel
rem
rem  Toute la logique vit dans packaging\msix\scripts\build-msix.ps1 ; ce
rem  fichier n'existe que pour pouvoir double-cliquer, comme build_exe.bat et
rem  build_installer.bat.
rem
rem  Les arguments sont transmis tels quels au script PowerShell.
rem
rem  Ce fichier doit rester en ASCII pur avec des fins de ligne CRLF
rem  (voir .gitattributes) : cmd.exe ne lit correctement ni l'UTF-8 sans BOM,
rem  ni les fins de ligne Unix.
rem ==========================================================================

cd /d "%~dp0"

rem  -ExecutionPolicy Bypass : la strategie par defaut de Windows refuse
rem  d'executer un .ps1 telecharge. Sans ce drapeau, le script serait bloque
rem  sans autre explication qu'une erreur de securite.
powershell -NoProfile -ExecutionPolicy Bypass -File "packaging\msix\scripts\build-msix.ps1" %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" goto :ok
echo  [ECHEC] La construction du paquet MSIX a echoue (code %CODE%).
goto :fin

:ok
echo  [OK] Termine. Le paquet est dans dist\msix\.

:fin
echo.
if not defined AGENCE_BUILD_AUTO pause
endlocal & exit /b %CODE%
